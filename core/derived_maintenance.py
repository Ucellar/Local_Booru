"""Safe rebuilds for disposable SQLite-derived state.

Primary metadata is never cleared by this module.  Every cache is first built
and validated in a private staging database.  The legacy ``main`` backend then
replaces its materialised table inside one main-database transaction.  The
optional ``external`` backend publishes a new immutable SQLite generation
first, then compare-and-swaps only its authority pointer in ``main.db``.  No
cross-database write transaction or crash atomicity is assumed.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Callable, Iterable

from core.database.connection import connect, write_txn
from core.maintenance_coordinator import (
    MaintenanceCancelled,
    get_maintenance_coordinator,
)
from core.vptree import hamming
from core.derived_cache_db import (
    external_cache_enabled,
    garbage_collect_external_generations,
    publish_stage_generation,
    validate_external_cache,
)

CACHE_VP_TREE = "vp_tree"
CACHE_EFFECTIVE_TAG_CATEGORIES = "image_effective_tag_category"
SUPPORTED_CACHES = {CACHE_VP_TREE, CACHE_EFFECTIVE_TAG_CATEGORIES}


class DerivedCacheChanged(RuntimeError):
    """Primary DB changed while a cache snapshot was being prepared."""


def _progress(callback: Callable[[str], None] | None, text: str) -> None:
    if callback:
        callback(str(text))


def _source_revision(con, cache_name: str) -> int:
    row = con.execute(
        "SELECT revision FROM derived_source_revision WHERE cache_name=?",
        (str(cache_name),),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _new_stage_db() -> tuple[sqlite3.Connection, Path]:
    fd, raw = tempfile.mkstemp(prefix="local_booru_derived_", suffix=".sqlite3")
    os.close(fd)
    path = Path(raw)
    con = sqlite3.connect(str(path), timeout=60)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA temp_store=MEMORY")
    return con, path


def _remove_stage(path: Path | None) -> None:
    if not path:
        return
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            Path(str(path) + suffix).unlink(missing_ok=True)
        except Exception:
            pass


def _mark_building(settings: dict, cache_name: str) -> None:
    now = int(time.time())
    with write_txn(settings) as con:
        row = con.execute(
            "SELECT storage_backend FROM derived_cache_state WHERE cache_name=?",
            (cache_name,),
        ).fetchone()
        current_backend = str(row[0] or "main") if row else "main"
        if external_cache_enabled(settings) or current_backend == "external":
            # File-first pointer publication keeps the old generation fully
            # authoritative during a rebuild.  Stage E's task registry exposes
            # the transient "building" lifecycle; changing this durable row
            # would make a valid old external generation unreadable after a
            # cancellation or process crash.
            con.execute(
                "UPDATE derived_cache_state SET last_error='',updated_at=? WHERE cache_name=?",
                (now, cache_name),
            )
            return
        con.execute(
            """
            INSERT INTO derived_cache_state(cache_name,status,last_error,updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(cache_name) DO UPDATE SET
                status=excluded.status,
                last_error='',
                updated_at=excluded.updated_at
            """,
            (cache_name, "building", "", now),
        )


def _mark_terminal(settings: dict, cache_name: str, status: str, error: str = "") -> None:
    now = int(time.time())
    try:
        with write_txn(settings) as con:
            row = con.execute(
                "SELECT storage_backend FROM derived_cache_state WHERE cache_name=?",
                (cache_name,),
            ).fetchone()
            current_backend = str(row[0] or "main") if row else "main"
            if external_cache_enabled(settings) or current_backend == "external":
                # A failed attempt is diagnostic only.  Preserve the previously
                # published ready pointer and record the attempt error beside it.
                con.execute(
                    "UPDATE derived_cache_state SET last_error=?,updated_at=? WHERE cache_name=?",
                    (str(error or "")[:1000], now, cache_name),
                )
                return
            con.execute(
                """
                INSERT INTO derived_cache_state(cache_name,status,last_error,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(cache_name) DO UPDATE SET
                    status=excluded.status,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (cache_name, str(status), str(error or "")[:1000], now),
            )
    except Exception:
        pass


def get_derived_cache_states(settings: dict) -> list[dict]:
    con = connect(settings, readonly=True)
    try:
        try:
            rows = con.execute(
                """
                SELECT s.*,COALESCE(r.revision,0) AS current_source_revision,
                       COALESCE((SELECT value FROM meta WHERE key='derived_cache_owner_id'),'') AS owner_id,
                       CASE WHEN s.source_revision=COALESCE(r.revision,0)
                                  AND s.status='ready' THEN 0 ELSE 1 END AS stale
                FROM derived_cache_state s
                LEFT JOIN derived_source_revision r ON r.cache_name=s.cache_name
                ORDER BY s.cache_name
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        con.close()

    out = []
    for row in rows:
        item = dict(row)
        item.setdefault("storage_backend", "main")
        item.setdefault("cache_file_id", "")
        item.setdefault("cache_schema_version", 0)
        item.setdefault("published_at", 0)
        item["external_valid"] = None
        item["external_error"] = ""
        item["external_path"] = ""
        item["external_bytes"] = 0
        if str(item.get("storage_backend") or "main") == "external":
            try:
                validated = validate_external_cache(
                    settings, str(item.get("cache_name") or ""), authority=item, require_fresh=False
                )
                path = Path(validated["path"])
                item["external_valid"] = True
                item["external_path"] = str(path)
                try:
                    item["external_bytes"] = int(path.stat().st_size)
                except OSError:
                    item["external_bytes"] = 0
            except Exception as exc:
                item["external_valid"] = False
                item["external_error"] = f"{type(exc).__name__}: {exc}"
                item["stale"] = 1
        out.append(item)
    return out


def _wait_chunk(settings, cache_name, mode, stop_check, progress) -> None:
    get_maintenance_coordinator().wait_for_permission(
        settings,
        task_kind=f"rebuild:{cache_name}",
        mode=mode,
        stop_check=stop_check,
        progress=progress,
        min_idle_seconds=float((settings or {}).get("maintenance_idle_seconds", 5) or 5),
    )


def _hash_stage_rows(con: sqlite3.Connection, sql: str) -> str:
    digest = hashlib.sha256()
    cur = con.execute(sql)
    while True:
        rows = cur.fetchmany(5000)
        if not rows:
            break
        for row in rows:
            for value in row:
                digest.update(str(value if value is not None else "").encode("utf-8", "surrogatepass"))
                digest.update(b"\0")
            digest.update(b"\n")
    return digest.hexdigest()


def _build_balanced_vp_rows(
    items: list[tuple[int, str]],
    *,
    checkpoint: Callable[[int, int], None] | None = None,
) -> tuple[list[tuple], int]:
    """Build a deterministic count-balanced VP-tree in memory.

    Equal-distance hashes are split across both branches when necessary.  The
    search inequalities are inclusive at the radius boundary, so this keeps
    pruning correct while preventing identical/adversarial hashes from forming
    a linear tree.  Recursive depth is therefore O(log n), not O(n).
    """
    nodes: dict[int, list] = {}
    next_node_id = 1
    built = 0
    max_depth = 0
    total = len(items)

    def build(points: list[tuple[int, str]], parent_id: int | None, depth: int) -> int | None:
        nonlocal next_node_id, built, max_depth
        if not points:
            return None
        node_id = next_node_id
        next_node_id += 1
        image_id, phash = points[0]
        built += 1
        max_depth = max(max_depth, depth)
        if checkpoint and (built == 1 or built % 500 == 0):
            checkpoint(built, total)

        rest = [(other_id, other_hash, hamming(phash, other_hash)) for other_id, other_hash in points[1:]]
        rest.sort(key=lambda item: (int(item[2]), int(item[0])))
        inner_points: list[tuple[int, str]] = []
        outer_points: list[tuple[int, str]] = []
        radius = None
        if rest:
            inner_count = (len(rest) + 1) // 2
            inner = rest[:inner_count]
            outer = rest[inner_count:]
            inner_points = [(int(a), str(b)) for a, b, _distance in inner]
            outer_points = [(int(a), str(b)) for a, b, _distance in outer]
            if outer:
                radius = (float(inner[-1][2]) + float(outer[0][2])) / 2.0
            else:
                radius = float(inner[-1][2])

        # Reserve the node before descending so child rows can refer back to it.
        nodes[node_id] = [
            node_id, int(image_id), str(phash), radius, None, None,
            len(inner_points), len(outer_points), parent_id,
        ]
        inner_id = build(inner_points, node_id, depth + 1)
        outer_id = build(outer_points, node_id, depth + 1)
        nodes[node_id][4] = inner_id
        nodes[node_id][5] = outer_id
        return node_id

    build(list(items), None, 1)
    return [tuple(nodes[node_id]) for node_id in sorted(nodes)], max_depth


def _stage_vp_tree(settings, source_con, stage_con, *, mode, progress, stop_check, chunk_size) -> dict:
    stage_con.executescript(
        """
        CREATE TABLE vp_tree (
            node_id INTEGER PRIMARY KEY,
            image_id INTEGER NOT NULL,
            phash TEXT NOT NULL,
            radius REAL,
            inner_id INTEGER,
            outer_id INTEGER,
            inner_pop INTEGER DEFAULT 0,
            outer_pop INTEGER DEFAULT 0,
            parent_id INTEGER
        );
        CREATE INDEX idx_vp_parent ON vp_tree(parent_id);
        CREATE UNIQUE INDEX idx_vp_image ON vp_tree(image_id);
        """
    )
    total = int(source_con.execute(
        "SELECT COUNT(*) FROM images WHERE deleted=0 AND hash_phash IS NOT NULL AND hash_phash!=''"
    ).fetchone()[0] or 0)
    last_id = 0
    seen = invalid = 0
    valid_items: list[tuple[int, str]] = []
    while True:
        _wait_chunk(settings, CACHE_VP_TREE, mode, stop_check, progress)
        rows = source_con.execute(
            """
            SELECT id,hash_phash FROM images
            WHERE deleted=0 AND hash_phash IS NOT NULL AND hash_phash!='' AND id>?
            ORDER BY id LIMIT ?
            """,
            (last_id, int(chunk_size)),
        ).fetchall()
        if not rows:
            break
        for image_id, phash in rows:
            last_id = int(image_id)
            seen += 1
            value = str(phash or "").strip().lower()
            if len(value) != 16:
                invalid += 1
                continue
            try:
                int(value, 16)
            except ValueError:
                invalid += 1
                continue
            valid_items.append((int(image_id), value))
        _progress(progress, f"VP-tree snapshot: {seen}/{total}; valid={len(valid_items)}; invalid={invalid}")

    def build_checkpoint(done: int, build_total: int) -> None:
        _wait_chunk(settings, CACHE_VP_TREE, mode, stop_check, progress)
        _progress(progress, f"VP-tree balanced build: {done}/{build_total}")

    vp_rows, max_depth = _build_balanced_vp_rows(valid_items, checkpoint=build_checkpoint)
    inserted = 0
    for offset in range(0, len(vp_rows), max(100, int(chunk_size))):
        _wait_chunk(settings, CACHE_VP_TREE, mode, stop_check, progress)
        batch = vp_rows[offset:offset + max(100, int(chunk_size))]
        stage_con.executemany(
            """
            INSERT INTO vp_tree(
                node_id,image_id,phash,radius,inner_id,outer_id,inner_pop,outer_pop,parent_id
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            batch,
        )
        stage_con.commit()
        inserted += len(batch)
        _progress(progress, f"VP-tree staging: {inserted}/{len(vp_rows)}; depth={max_depth}")

    valid = len(valid_items)
    count = int(stage_con.execute("SELECT COUNT(*) FROM vp_tree").fetchone()[0] or 0)
    unique_count = int(stage_con.execute("SELECT COUNT(DISTINCT image_id) FROM vp_tree").fetchone()[0] or 0)
    roots = int(stage_con.execute("SELECT COUNT(*) FROM vp_tree WHERE parent_id IS NULL").fetchone()[0] or 0)
    bad_links = int(stage_con.execute(
        """
        SELECT COUNT(*) FROM vp_tree v
        LEFT JOIN vp_tree i ON i.node_id=v.inner_id
        LEFT JOIN vp_tree o ON o.node_id=v.outer_id
        LEFT JOIN vp_tree p ON p.node_id=v.parent_id
        WHERE (v.inner_id IS NOT NULL AND (i.node_id IS NULL OR i.parent_id<>v.node_id))
           OR (v.outer_id IS NOT NULL AND (o.node_id IS NULL OR o.parent_id<>v.node_id))
           OR (v.parent_id IS NOT NULL AND p.node_id IS NULL)
        """
    ).fetchone()[0] or 0)
    reachable = 0
    if count:
        reachable = int(stage_con.execute(
            """
            WITH RECURSIVE walk(node_id) AS (
                SELECT node_id FROM vp_tree WHERE parent_id IS NULL
                UNION
                SELECT v.inner_id FROM vp_tree v JOIN walk w ON v.node_id=w.node_id
                    WHERE v.inner_id IS NOT NULL
                UNION
                SELECT v.outer_id FROM vp_tree v JOIN walk w ON v.node_id=w.node_id
                    WHERE v.outer_id IS NOT NULL
            ) SELECT COUNT(*) FROM walk
            """
        ).fetchone()[0] or 0)
    if count != valid or unique_count != valid:
        raise RuntimeError(f"VP-tree staging invariant failed: rows={count} unique={unique_count} expected={valid}")
    if (count == 0 and roots != 0) or (count > 0 and roots != 1):
        raise RuntimeError(f"VP-tree staging root invariant failed: rows={count} roots={roots}")
    if bad_links:
        raise RuntimeError(f"VP-tree staging has {bad_links} broken child/parent links")
    if reachable != count:
        raise RuntimeError(f"VP-tree staging reachability failed: reachable={reachable} rows={count}")
    revision = _hash_stage_rows(stage_con, "SELECT image_id,phash FROM vp_tree ORDER BY image_id")
    return {
        "source_rows": total,
        "cache_rows": count,
        "skipped_invalid": invalid,
        "content_hash": revision,
        "max_depth": max_depth,
    }


def _stage_effective_categories(settings, source_con, stage_con, *, mode, progress, stop_check, chunk_size) -> dict:
    stage_con.executescript(
        """
        CREATE TABLE effective (
            image_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            updated_at INTEGER NOT NULL DEFAULT 0,
            category_rank INTEGER NOT NULL DEFAULT 99,
            source_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(image_id,tag_id)
        );
        CREATE INDEX idx_effective_category ON effective(category,tag_id,image_id);
        """
    )
    source_specific = int(source_con.execute("SELECT COUNT(*) FROM image_source_tags").fetchone()[0] or 0)
    legacy = int(source_con.execute("SELECT COUNT(*) FROM image_tags").fetchone()[0] or 0)
    source_rows = source_specific + legacy
    now = int(time.time())

    # Stream the PK order once.  Do not repeat a full ROW_NUMBER window query for
    # every chunk: on a large library that would turn a rebuild into O(chunks*n).
    last_image = -1
    last_source = -1
    last_tag = -1
    source_seen = 0
    upsert_sql = """
        INSERT INTO effective(
            image_id,tag_id,category,updated_at,category_rank,source_id
        ) VALUES(?,?,?,?,?,?)
        ON CONFLICT(image_id,tag_id) DO UPDATE SET
            category=excluded.category,
            updated_at=excluded.updated_at,
            category_rank=excluded.category_rank,
            source_id=excluded.source_id
        WHERE excluded.category_rank < effective.category_rank
           OR (excluded.category_rank = effective.category_rank
               AND excluded.source_id < effective.source_id)
    """
    while True:
        _wait_chunk(settings, CACHE_EFFECTIVE_TAG_CATEGORIES, mode, stop_check, progress)
        rows = source_con.execute(
            """
            SELECT image_id,source_id,tag_id,
                   LOWER(COALESCE(NULLIF(category,''),'general')) AS category,
                   CASE LOWER(COALESCE(NULLIF(category,''),'general'))
                       WHEN 'artist' THEN 0 WHEN 'contributor' THEN 1 WHEN 'character' THEN 2
                       WHEN 'copyright' THEN 3 WHEN 'species' THEN 4 WHEN 'meta' THEN 5
                       WHEN 'lore' THEN 6 WHEN 'invalid' THEN 7 WHEN 'parody' THEN 8
                       WHEN 'language' THEN 9 WHEN 'category' THEN 10 WHEN 'pages' THEN 11
                       WHEN 'general' THEN 99 ELSE 50 END AS category_rank
            FROM image_source_tags
            WHERE image_id>?
               OR (image_id=? AND source_id>?)
               OR (image_id=? AND source_id=? AND tag_id>?)
            ORDER BY image_id,source_id,tag_id LIMIT ?
            """,
            (
                last_image,
                last_image, last_source,
                last_image, last_source, last_tag,
                int(chunk_size),
            ),
        ).fetchall()
        if not rows:
            break
        stage_con.executemany(
            upsert_sql,
            [
                (int(image_id), int(tag_id), str(category or "general").lower(), now, int(rank), int(source_id))
                for image_id, source_id, tag_id, category, rank in rows
            ],
        )
        stage_con.commit()
        last_image = int(rows[-1][0])
        last_source = int(rows[-1][1])
        last_tag = int(rows[-1][2])
        source_seen += len(rows)
        _progress(progress, f"Эффективные категории: source {source_seen}/{source_specific}")

    last_image = -1
    last_tag = -1
    legacy_seen = 0
    while True:
        _wait_chunk(settings, CACHE_EFFECTIVE_TAG_CATEGORIES, mode, stop_check, progress)
        rows = source_con.execute(
            """
            SELECT it.image_id,it.tag_id,LOWER(COALESCE(NULLIF(t.category,''),'general'))
            FROM image_tags it JOIN tags t ON t.id=it.tag_id
            WHERE it.image_id>? OR (it.image_id=? AND it.tag_id>?)
            ORDER BY it.image_id,it.tag_id LIMIT ?
            """,
            (last_image, last_image, last_tag, int(chunk_size)),
        ).fetchall()
        if not rows:
            break
        stage_con.executemany(
            """
            INSERT OR IGNORE INTO effective(
                image_id,tag_id,category,updated_at,category_rank,source_id
            ) VALUES(?,?,?,?,999,0)
            """,
            [(int(a), int(b), str(c or "general").lower(), now) for a, b, c in rows],
        )
        stage_con.commit()
        last_image, last_tag = int(rows[-1][0]), int(rows[-1][1])
        legacy_seen += len(rows)
        _progress(progress, f"Эффективные категории: legacy {legacy_seen}/{legacy}")

    count = int(stage_con.execute("SELECT COUNT(*) FROM effective").fetchone()[0] or 0)
    bad = int(stage_con.execute(
        "SELECT COUNT(*) FROM effective WHERE image_id<=0 OR tag_id<=0 OR category=''"
    ).fetchone()[0] or 0)
    if bad:
        raise RuntimeError(f"effective category staging contains {bad} invalid rows")
    revision = _hash_stage_rows(stage_con, "SELECT image_id,tag_id,category FROM effective ORDER BY image_id,tag_id")
    return {
        "source_rows": source_rows,
        "cache_rows": count,
        "skipped_invalid": 0,
        "content_hash": revision,
    }


def _iter_stage_rows(stage_con: sqlite3.Connection, sql: str, chunk_size: int = 5000) -> Iterable[list[tuple]]:
    cur = stage_con.execute(sql)
    while True:
        rows = cur.fetchmany(int(chunk_size))
        if not rows:
            return
        yield rows


def _atomic_replace(
    settings: dict,
    cache_name: str,
    source_con,
    source_revision: int,
    stage_con,
    info: dict,
    *,
    started: float,
    progress=None,
) -> int:
    use_external = external_cache_enabled(settings)
    _progress(
        progress,
        f"{cache_name}: проверка snapshot и публикация "
        + ("в disposable cache DB" if use_external else "в main DB"),
    )

    # Read the next generation and owner identity from main.db.  This is only a
    # proposal until the final writer transaction rechecks source_revision.
    row = source_con.execute(
        """
        SELECT COALESCE(s.generation,0),
               COALESCE((SELECT value FROM meta WHERE key='derived_cache_owner_id'),''),
               COALESCE(s.storage_backend,'main'),COALESCE(s.cache_file_id,'')
        FROM derived_cache_state s WHERE s.cache_name=?
        """,
        (cache_name,),
    ).fetchone()
    if row:
        base_generation = int(row[0] or 0)
        generation = base_generation + 1
        owner_id = str(row[1] or "")
        base_backend = str(row[2] or "main")
        base_file_id = str(row[3] or "")
    else:
        base_generation = 0
        generation = 1
        base_backend = "main"
        base_file_id = ""
        owner_row = source_con.execute(
            "SELECT value FROM meta WHERE key='derived_cache_owner_id'"
        ).fetchone()
        owner_id = str(owner_row[0] or "") if owner_row else ""
    if use_external and not owner_id:
        raise RuntimeError("main database has no derived_cache_owner_id; migration v30 is incomplete")

    published = None
    if use_external:
        # The immutable generation is made visible before main.db points at it.
        # A crash here only leaves an orphan, never a false authoritative cache.
        published = publish_stage_generation(
            settings,
            cache_name,
            stage_con,
            generation=generation,
            source_revision=int(source_revision),
            content_hash=str(info["content_hash"]),
            source_rows=int(info["source_rows"]),
            cache_rows=int(info["cache_rows"]),
            owner_id=owner_id,
        )

    try:
        with write_txn(settings) as con:
            # This BEGIN IMMEDIATE owns the main writer slot.  Relevant source
            # triggers cannot advance revision between this check and commit.
            if _source_revision(con, cache_name) != int(source_revision):
                raise DerivedCacheChanged("relevant primary data changed during cache rebuild; old cache kept")
            authority_now = con.execute(
                """
                SELECT COALESCE(generation,0),COALESCE(storage_backend,'main'),COALESCE(cache_file_id,'')
                FROM derived_cache_state WHERE cache_name=?
                """,
                (cache_name,),
            ).fetchone()
            owner_now_row = con.execute(
                "SELECT value FROM meta WHERE key='derived_cache_owner_id'"
            ).fetchone()
            owner_now = str(owner_now_row[0] or "") if owner_now_row else ""
            current_authority = (
                int(authority_now[0] or 0),
                str(authority_now[1] or "main"),
                str(authority_now[2] or ""),
                owner_now,
            ) if authority_now else (0, "main", "", owner_now)
            expected_authority = (base_generation, base_backend, base_file_id, owner_id)
            if current_authority != expected_authority:
                raise DerivedCacheChanged(
                    "derived cache authority changed during rebuild; newer cache kept"
                )

            if use_external:
                # Remove the legacy duplicate copy.  Primary rows/revisions stay
                # in main.db; only this disposable materialisation moves out.
                if cache_name == CACHE_VP_TREE:
                    con.execute("DELETE FROM vp_tree")
                elif cache_name == CACHE_EFFECTIVE_TAG_CATEGORIES:
                    con.execute("DELETE FROM image_effective_tag_category")
                    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('effective_tag_category_complete','external')")
                else:
                    raise ValueError(f"unsupported cache: {cache_name}")
            elif cache_name == CACHE_VP_TREE:
                con.execute("DELETE FROM vp_tree")
                for rows in _iter_stage_rows(
                    stage_con,
                    "SELECT node_id,image_id,phash,radius,inner_id,outer_id,inner_pop,outer_pop,parent_id FROM vp_tree ORDER BY node_id",
                ):
                    con.executemany(
                        """
                        INSERT INTO vp_tree(node_id,image_id,phash,radius,inner_id,outer_id,inner_pop,outer_pop,parent_id)
                        VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        rows,
                    )
            elif cache_name == CACHE_EFFECTIVE_TAG_CATEGORIES:
                con.execute("DELETE FROM image_effective_tag_category")
                for rows in _iter_stage_rows(
                    stage_con,
                    "SELECT image_id,tag_id,category,updated_at FROM effective ORDER BY image_id,tag_id",
                ):
                    con.executemany(
                        """
                        INSERT INTO image_effective_tag_category(image_id,tag_id,category,updated_at)
                        VALUES(?,?,?,?)
                        """,
                        rows,
                    )
                con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('effective_tag_category_complete','1')")
            else:
                raise ValueError(f"unsupported cache: {cache_name}")

            now = int(time.time())
            duration_ms = int(max(0.0, (time.monotonic() - started) * 1000.0))
            con.execute(
                """
                INSERT INTO derived_cache_state(
                    cache_name,generation,source_revision,content_hash,status,source_rows,cache_rows,
                    built_at,duration_ms,last_error,updated_at,storage_backend,cache_file_id,
                    cache_schema_version,published_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(cache_name) DO UPDATE SET
                    generation=excluded.generation,
                    source_revision=excluded.source_revision,
                    content_hash=excluded.content_hash,
                    status='ready',
                    source_rows=excluded.source_rows,
                    cache_rows=excluded.cache_rows,
                    built_at=excluded.built_at,
                    duration_ms=excluded.duration_ms,
                    last_error='',
                    updated_at=excluded.updated_at,
                    storage_backend=excluded.storage_backend,
                    cache_file_id=excluded.cache_file_id,
                    cache_schema_version=excluded.cache_schema_version,
                    published_at=excluded.published_at
                """,
                (
                    cache_name, generation, int(source_revision), info["content_hash"], "ready",
                    int(info["source_rows"]), int(info["cache_rows"]), now,
                    duration_ms, "", now,
                    "external" if use_external else "main",
                    str((published or {}).get("file_id") or ""),
                    int((published or {}).get("schema_version") or 0),
                    now,
                ),
            )
    except Exception:
        # The immutable generation was not made authoritative.  Best-effort
        # cleanup is enough; an undeletable orphan is harmless and GC will retry.
        if published:
            try:
                Path(published["path"]).unlink(missing_ok=True)
            except Exception:
                pass
        raise

    if use_external:
        try:
            garbage_collect_external_generations(settings, keep_per_cache=2)
        except Exception:
            pass
    return generation


def rebuild_derived_cache(
    settings: dict,
    cache_name: str,
    *,
    mode: str = "forced",
    chunk_size: int = 500,
    progress: Callable[[str], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> dict:
    coordinator = get_maintenance_coordinator()
    with coordinator.maintenance_job(
        f"rebuild:{cache_name}",
        stop_check=stop_check,
        progress=progress,
    ):
        return _rebuild_derived_cache_locked(
            settings,
            cache_name,
            mode=mode,
            chunk_size=chunk_size,
            progress=progress,
            stop_check=stop_check,
        )


def _rebuild_derived_cache_locked(
    settings: dict,
    cache_name: str,
    *,
    mode: str = "forced",
    chunk_size: int = 500,
    progress: Callable[[str], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> dict:
    """Rebuild one supported cache without destroying the previous good copy."""
    cache_name = str(cache_name or "").strip()
    if cache_name not in SUPPORTED_CACHES:
        raise ValueError(f"Unsupported derived cache: {cache_name}")
    chunk_size = max(50, min(5000, int(chunk_size or 500)))
    started = time.monotonic()
    stage_con = None
    stage_path = None
    source_con = None
    _mark_building(settings, cache_name)
    try:
        _wait_chunk(settings, cache_name, mode, stop_check, progress)
        source_con = connect(settings, readonly=True)
        source_revision = _source_revision(source_con, cache_name)
        stage_con, stage_path = _new_stage_db()
        if cache_name == CACHE_VP_TREE:
            info = _stage_vp_tree(
                settings, source_con, stage_con, mode=mode, progress=progress,
                stop_check=stop_check, chunk_size=chunk_size,
            )
        else:
            info = _stage_effective_categories(
                settings, source_con, stage_con, mode=mode, progress=progress,
                stop_check=stop_check, chunk_size=chunk_size,
            )
        if stop_check and stop_check():
            raise MaintenanceCancelled(f"{cache_name}: cancelled before swap")
        generation = _atomic_replace(
            settings, cache_name, source_con, source_revision, stage_con, info,
            started=started, progress=progress,
        )
        result = {
            "ok": True,
            "cache": cache_name,
            "generation": generation,
            "source_revision": int(source_revision),
            "content_hash": info["content_hash"],
            "source_rows": int(info["source_rows"]),
            "cache_rows": int(info["cache_rows"]),
            "skipped_invalid": int(info.get("skipped_invalid") or 0),
            "max_depth": int(info.get("max_depth") or 0),
            "duration_seconds": round(max(0.0, time.monotonic() - started), 3),
            "storage_backend": "external" if external_cache_enabled(settings) else "main",
        }
        _progress(progress, f"{cache_name}: готово, generation={generation}, rows={result['cache_rows']}")
        return result
    except MaintenanceCancelled as exc:
        _mark_terminal(settings, cache_name, "cancelled", str(exc))
        return {
            "ok": False,
            "cancelled": True,
            "cache": cache_name,
            "error": str(exc),
            "duration_seconds": round(max(0.0, time.monotonic() - started), 3),
        }
    except Exception as exc:
        _mark_terminal(settings, cache_name, "failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if source_con is not None:
            try:
                source_con.close()
            except Exception:
                pass
        if stage_con is not None:
            try:
                stage_con.close()
            except Exception:
                pass
        _remove_stage(stage_path)
