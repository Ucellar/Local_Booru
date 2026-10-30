"""DB-first duplicate candidate scanning.

The VP-tree is only a streaming candidate-pair index.  Candidate pairs are
normalised, staged outside the main database, validated against the source
revision, and then replace the previous automatic visual relation set in one
SQLite write transaction.  Pairwise UI records deliberately avoid transitive
connected-component grouping: A~B and B~C do not imply A~C.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Callable, Iterable

from core.database.connection import connect, write_txn
from core.derived_maintenance import (
    CACHE_VP_TREE,
    get_derived_cache_states,
    rebuild_derived_cache,
)
from core.maintenance_coordinator import MaintenanceCancelled, get_maintenance_coordinator
from core.media_utils import MEDIA_EXTS
from core.path_utils import path_key
from core.storage_roots import check_storage_root, configured_storage_roots
from core.vptree import VPTree
from core.derived_cache_db import open_selected_cache, validate_external_cache

RELATION_VISUAL_PHASH = "visual_phash"
DEFAULT_PAIR_LIMIT = 500_000


class DuplicateScanChanged(RuntimeError):
    """The relevant image/pHash snapshot changed while candidates were staged."""


class DuplicatePairLimitExceeded(RuntimeError):
    """A threshold generated more candidates than the configured safety limit."""


def threshold_to_distance(threshold_percent: int | float) -> int:
    """Convert an inclusive percentage threshold to a 64-bit Hamming radius.

    Similarity is ``100 * (1 - distance / 64)``.  Distances are integers, so
    floor keeps every pair whose displayed similarity is at least the selected
    threshold.  For example 85% maps to distance <= 9.
    """
    threshold = max(50.0, min(100.0, float(threshold_percent or 85)))
    return max(0, min(32, int(math.floor(64.0 * (1.0 - threshold / 100.0)))))


def distance_to_similarity(distance: int) -> float:
    return max(0.0, 100.0 * (1.0 - max(0, min(64, int(distance))) / 64.0))


def normalize_pair(image_a: int, image_b: int) -> tuple[int, int]:
    a, b = int(image_a), int(image_b)
    if a <= 0 or b <= 0 or a == b:
        raise ValueError("duplicate candidate pair requires two different positive image ids")
    return (a, b) if a < b else (b, a)


def _progress(callback: Callable[[str], None] | None, message: str) -> None:
    if callback:
        callback(str(message))


def _cache_state(settings: dict) -> dict | None:
    states = {str(row.get("cache_name") or ""): row for row in get_derived_cache_states(settings)}
    return states.get(CACHE_VP_TREE)


def _state_ready(state: dict | None) -> bool:
    return bool(
        state
        and str(state.get("status") or "") == "ready"
        and int(state.get("stale") or 0) == 0
        and int(state.get("source_revision") or 0) == int(state.get("current_source_revision") or 0)
        and state.get("external_valid") is not False
    )


def _ensure_vp_tree(settings, *, mode, progress, stop_check) -> dict:
    state = _cache_state(settings)
    if _state_ready(state):
        return dict(state or {})
    _progress(progress, "Дубликаты: VP-tree отсутствует или устарел; безопасная пересборка")
    result = rebuild_derived_cache(
        settings,
        CACHE_VP_TREE,
        mode=mode,
        progress=progress,
        stop_check=stop_check,
    )
    if result.get("cancelled"):
        raise MaintenanceCancelled("duplicate scan cancelled while rebuilding VP-tree")
    state = _cache_state(settings)
    if not _state_ready(state):
        raise RuntimeError("VP-tree rebuild completed but the cache is not ready for candidate scanning")
    return dict(state or {})


def _new_stage_db() -> tuple[sqlite3.Connection, Path]:
    fd, raw = tempfile.mkstemp(prefix="local_booru_duplicate_pairs_", suffix=".sqlite3")
    os.close(fd)
    path = Path(raw)
    con = sqlite3.connect(str(path), timeout=60)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA temp_store=MEMORY")
    con.executescript(
        """
        CREATE TABLE pairs (
            image_a INTEGER NOT NULL,
            image_b INTEGER NOT NULL,
            distance INTEGER NOT NULL,
            confidence REAL NOT NULL,
            PRIMARY KEY(image_a,image_b),
            CHECK(image_a < image_b)
        ) WITHOUT ROWID;
        """
    )
    return con, path


def _remove_stage(path: Path | None) -> None:
    if path is None:
        return
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            Path(str(path) + suffix).unlink(missing_ok=True)
        except Exception:
            pass


def _wait_chunk(settings, *, mode, progress, stop_check) -> None:
    get_maintenance_coordinator().wait_for_permission(
        settings,
        task_kind="duplicate_candidate_scan",
        mode=mode,
        stop_check=stop_check,
        progress=progress,
        min_idle_seconds=float((settings or {}).get("maintenance_idle_seconds", 5) or 5),
    )


def _start_run(settings: dict, threshold: int, max_distance: int, state: dict) -> int:
    now = int(time.time())
    with write_txn(settings) as con:
        cur = con.execute(
            """
            INSERT INTO duplicate_scan_runs(
                status,threshold_percent,max_distance,source_revision,vp_generation,started_at
            ) VALUES('running',?,?,?,?,?)
            """,
            (
                int(threshold), int(max_distance), int(state.get("source_revision") or 0),
                int(state.get("generation") or 0), now,
            ),
        )
        return int(cur.lastrowid)


def _finish_run(settings: dict, run_id: int, status: str, values: dict, error: str = "") -> None:
    try:
        with write_txn(settings) as con:
            con.execute(
                """
                UPDATE duplicate_scan_runs SET
                    status=?,total_images=?,processed_images=?,candidate_pairs=?,stored_pairs=?,
                    ignored_pairs=?,stale_removed=?,unindexed_files=?,unindexed_samples_json=?,
                    finished_at=?,last_error=?
                WHERE id=?
                """,
                (
                    str(status), int(values.get("total_images") or 0),
                    int(values.get("processed_images") or 0), int(values.get("candidate_pairs") or 0),
                    int(values.get("stored_pairs") or 0), int(values.get("ignored_pairs") or 0),
                    int(values.get("stale_removed") or 0), int(values.get("unindexed_files") or 0),
                    json.dumps(list(values.get("unindexed_samples") or []), ensure_ascii=False),
                    int(time.time()), str(error or "")[:2000], int(run_id),
                ),
            )
    except Exception:
        pass


def _iter_stage_rows(stage_con: sqlite3.Connection, chunk_size: int = 5000) -> Iterable[list[tuple]]:
    cur = stage_con.execute(
        "SELECT image_a,image_b,distance,confidence FROM pairs ORDER BY image_a,image_b"
    )
    while True:
        rows = cur.fetchmany(max(100, int(chunk_size)))
        if not rows:
            return
        yield rows


def _managed_media_roots(settings: dict) -> list[Path]:
    # Duplicate discovery must never recreate a missing/offline output tree.
    # Resolve the configured managed-output root through the same non-mutating
    # storage classifier used by presence integrity and the DB-missing guard.
    output_root = next(
        (root for kind, root in configured_storage_roots(settings) if kind == "output"),
        "",
    )
    if not output_root:
        return []
    base = Path(output_root)
    roots = [
        base / "found" / "media",
        base / "no_match" / "media",
        base / "downloads" / "found" / "media",
        base / "downloads" / "no_match" / "media",
    ]
    # Preserve order while removing aliases that resolve to the same spelling.
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = path_key(root)
        if key and key not in seen:
            seen.add(key)
            out.append(root)
    return out


def _scan_unindexed_files(
    settings: dict,
    source_con,
    *,
    mode: str,
    progress,
    stop_check,
    sample_limit: int = 50,
) -> dict:
    """Cheap guarded filesystem fallback: detect, but never hash/delete, unknown files."""
    live_keys = {
        path_key(row[0])
        for row in source_con.execute("SELECT path FROM images WHERE deleted=0").fetchall()
        if path_key(row[0])
    }
    count = 0
    visited_paths = 0
    samples: list[str] = []
    root_issues: list[dict] = []
    for root in _managed_media_roots(settings):
        _wait_chunk(settings, mode=mode, progress=progress, stop_check=stop_check)
        try:
            state = check_storage_root(root)
            if state.status in {"offline_root", "path_error"}:
                root_issues.append({"root": str(root), "status": state.status, "detail": state.detail})
                continue
        except Exception:
            state = None
        if not root.exists():
            # Optional buckets are commonly absent; this is not an integrity error.
            continue
        try:
            for path in root.rglob("*"):
                if stop_check and stop_check():
                    raise MaintenanceCancelled("duplicate scan cancelled during unindexed-file fallback")
                visited_paths += 1
                if visited_paths % 500 == 0:
                    _wait_chunk(settings, mode=mode, progress=progress, stop_check=stop_check)
                try:
                    if not path.is_file() or path.suffix.lower() not in MEDIA_EXTS:
                        continue
                except OSError:
                    continue
                if path_key(path) in live_keys:
                    continue
                count += 1
                if len(samples) < max(0, int(sample_limit)):
                    samples.append(str(path))
        except MaintenanceCancelled:
            raise
        except OSError as exc:
            root_issues.append({"root": str(root), "status": "path_error", "detail": str(exc)})
    _progress(progress, f"Дубликаты: filesystem fallback — неиндексированных файлов {count}")
    return {"count": count, "samples": samples, "root_issues": root_issues}


def _stage_candidate_pairs(
    settings: dict,
    source_con,
    tree_con,
    stage_con,
    *,
    max_distance: int,
    pair_limit: int,
    mode: str,
    chunk_size: int,
    progress,
    stop_check,
) -> dict:
    tree = VPTree(tree_con)
    total = int(source_con.execute(
        """
        SELECT COUNT(*) FROM images i
        WHERE i.deleted=0 AND i.hash_phash IS NOT NULL AND i.hash_phash!=''
        """
    ).fetchone()[0] or 0)
    last_id = 0
    processed = 0
    pairs = 0
    while True:
        _wait_chunk(settings, mode=mode, progress=progress, stop_check=stop_check)
        rows = source_con.execute(
            """
            SELECT i.id,i.hash_phash
            FROM images i
            WHERE i.deleted=0 AND i.hash_phash IS NOT NULL AND i.hash_phash!='' AND i.id>?
            ORDER BY i.id LIMIT ?
            """,
            (int(last_id), int(chunk_size)),
        ).fetchall()
        if not rows:
            break
        stage_con.execute("BEGIN")
        try:
            for row in rows:
                image_id = int(row[0])
                phash = str(row[1] or "").strip().lower()
                last_id = image_id
                processed += 1
                for other_id, distance in tree.search(phash, max_distance=max_distance):
                    other_id = int(other_id)
                    if other_id <= image_id:
                        continue
                    a, b = normalize_pair(image_id, other_id)
                    if pairs >= pair_limit:
                        raise DuplicatePairLimitExceeded(
                            f"threshold generated more than {pair_limit} candidate pairs; "
                            "increase the similarity threshold or the explicit "
                            "duplicate_candidate_pair_limit setting."
                        )
                    cur = stage_con.execute(
                        """
                        INSERT OR IGNORE INTO pairs(image_a,image_b,distance,confidence)
                        VALUES(?,?,?,?)
                        """,
                        (a, b, int(distance), float(distance_to_similarity(distance))),
                    )
                    if int(cur.rowcount or 0) > 0:
                        pairs += 1
            stage_con.commit()
        except Exception:
            stage_con.rollback()
            raise
        _progress(progress, f"Дубликаты DB-first: {processed}/{total}; пар-кандидатов {pairs}")
    return {"total_images": total, "processed_images": processed, "candidate_pairs": pairs}


def _atomic_store_pairs(
    settings: dict,
    stage_con,
    *,
    run_id: int,
    source_revision: int,
    vp_generation: int,
    storage_backend: str,
    cache_file_id: str,
    run_values: dict,
) -> dict:
    now = int(time.time())
    stored = ignored = stale_removed = 0
    with write_txn(settings) as con:
        revision_row = con.execute(
            "SELECT revision FROM derived_source_revision WHERE cache_name=?",
            (CACHE_VP_TREE,),
        ).fetchone()
        current_revision = int(revision_row[0] or 0) if revision_row else 0
        state = con.execute(
            "SELECT generation,source_revision,status,storage_backend,cache_file_id FROM derived_cache_state WHERE cache_name=?",
            (CACHE_VP_TREE,),
        ).fetchone()
        if (
            current_revision != int(source_revision)
            or state is None
            or int(state[0] or 0) != int(vp_generation)
            or int(state[1] or 0) != int(source_revision)
            or str(state[2] or "") != "ready"
            or str(state[3] or "main") != str(storage_backend or "main")
            or str(state[4] or "") != str(cache_file_id or "")
        ):
            raise DuplicateScanChanged("images/pHash data or VP-tree generation changed during duplicate scan; old relations kept")
        if str(storage_backend or "main") == "external":
            try:
                validate_external_cache(settings, CACHE_VP_TREE, require_fresh=True)
            except Exception as exc:
                raise DuplicateScanChanged(f"external VP-tree generation is no longer valid: {exc}") from exc

        md5_by_id = {
            int(row[0]): str(row[1] or "").strip().lower()
            for row in con.execute("SELECT id,hash_md5 FROM images WHERE deleted=0").fetchall()
        }
        ignored_md5_pairs = {
            (str(row[0] or "").lower(), str(row[1] or "").lower())
            for row in con.execute(
                "SELECT md5_a,md5_b FROM ignored_duplicate_pairs WHERE relation IN ('not_duplicate','alternative')"
            ).fetchall()
        }

        for rows in _iter_stage_rows(stage_con):
            payload = []
            for image_a, image_b, distance, confidence in rows:
                a, b = normalize_pair(image_a, image_b)
                md5_a = md5_by_id.get(a, "")
                md5_b = md5_by_id.get(b, "")
                if a not in md5_by_id or b not in md5_by_id:
                    raise DuplicateScanChanged("a candidate endpoint disappeared during duplicate scan; old relations kept")
                md5_pair = tuple(sorted((md5_a, md5_b))) if md5_a and md5_b and md5_a != md5_b else None
                if md5_pair and md5_pair in ignored_md5_pairs:
                    ignored += 1
                    continue
                payload.append((
                    a, b, RELATION_VISUAL_PHASH, int(distance), float(confidence), "unreviewed",
                    int(run_id), int(source_revision), md5_a, md5_b, now, now, now,
                ))
            if not payload:
                continue
            con.executemany(
                """
                INSERT INTO duplicate_relations(
                    image_a,image_b,relation,distance,confidence,status,
                    scan_generation,source_revision,md5_a,md5_b,last_seen_at,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(image_a,image_b,relation) DO UPDATE SET
                    distance=excluded.distance,
                    confidence=excluded.confidence,
                    status=CASE
                        WHEN duplicate_relations.md5_a=excluded.md5_a
                         AND duplicate_relations.md5_b=excluded.md5_b
                        THEN duplicate_relations.status
                        ELSE 'unreviewed'
                    END,
                    scan_generation=excluded.scan_generation,
                    source_revision=excluded.source_revision,
                    md5_a=excluded.md5_a,
                    md5_b=excluded.md5_b,
                    last_seen_at=excluded.last_seen_at,
                    updated_at=excluded.updated_at
                """,
                payload,
            )
            stored += len(payload)

        cur = con.execute(
            "DELETE FROM duplicate_relations WHERE relation=? AND scan_generation<>?",
            (RELATION_VISUAL_PHASH, int(run_id)),
        )
        stale_removed = max(0, int(cur.rowcount or 0))
        con.execute(
            """
            UPDATE duplicate_scan_runs SET
                status='completed',total_images=?,processed_images=?,candidate_pairs=?,
                stored_pairs=?,ignored_pairs=?,stale_removed=?,unindexed_files=?,
                unindexed_samples_json=?,finished_at=?,last_error=''
            WHERE id=?
            """,
            (
                int(run_values.get("total_images") or 0),
                int(run_values.get("processed_images") or 0),
                int(run_values.get("candidate_pairs") or 0),
                stored,
                ignored,
                stale_removed,
                int(run_values.get("unindexed_files") or 0),
                json.dumps(list(run_values.get("unindexed_samples") or []), ensure_ascii=False),
                now,
                int(run_id),
            ),
        )
    return {"stored_pairs": stored, "ignored_pairs": ignored, "stale_removed": stale_removed}


def load_duplicate_groups(settings: dict, *, limit: int = 250) -> dict:
    """Load pairwise candidate groups from SQLite without opening media files."""
    limit = max(1, min(5000, int(limit or 250)))
    con = connect(settings, readonly=True)
    try:
        total = int(con.execute(
            """
            SELECT COUNT(*)
            FROM duplicate_relations r
            JOIN images a ON a.id=r.image_a AND a.deleted=0
            JOIN images b ON b.id=r.image_b AND b.deleted=0
            LEFT JOIN image_file_integrity ia ON ia.image_id=a.id AND ia.checked_path=a.path
            LEFT JOIN image_file_integrity ib ON ib.image_id=b.id AND ib.checked_path=b.path
            WHERE r.relation=? AND r.status='unreviewed'
              AND COALESCE(ia.presence_status,'unknown') NOT IN ('missing','path_error')
              AND COALESCE(ib.presence_status,'unknown') NOT IN ('missing','path_error')
              AND NOT EXISTS(
                  SELECT 1 FROM ignored_duplicate_pairs p
                  WHERE p.relation IN ('not_duplicate','alternative')
                    AND p.md5_a=CASE WHEN lower(COALESCE(a.hash_md5,'')) < lower(COALESCE(b.hash_md5,''))
                                     THEN lower(COALESCE(a.hash_md5,'')) ELSE lower(COALESCE(b.hash_md5,'')) END
                    AND p.md5_b=CASE WHEN lower(COALESCE(a.hash_md5,'')) < lower(COALESCE(b.hash_md5,''))
                                     THEN lower(COALESCE(b.hash_md5,'')) ELSE lower(COALESCE(a.hash_md5,'')) END
              )
            """,
            (RELATION_VISUAL_PHASH,),
        ).fetchone()[0] or 0)
        rows = con.execute(
            """
            SELECT r.id AS relation_id,r.distance,r.confidence,r.scan_generation,
                   run.threshold_percent,
                   a.id AS a_id,a.path AS a_path,a.file_name AS a_name,a.size_bytes AS a_bytes,
                   a.width AS a_width,a.height AS a_height,a.hash_md5 AS a_md5,a.hash_phash AS a_phash,
                   b.id AS b_id,b.path AS b_path,b.file_name AS b_name,b.size_bytes AS b_bytes,
                   b.width AS b_width,b.height AS b_height,b.hash_md5 AS b_md5,b.hash_phash AS b_phash
            FROM duplicate_relations r
            JOIN images a ON a.id=r.image_a AND a.deleted=0
            JOIN images b ON b.id=r.image_b AND b.deleted=0
            LEFT JOIN duplicate_scan_runs run ON run.id=r.scan_generation
            LEFT JOIN image_file_integrity ia ON ia.image_id=a.id AND ia.checked_path=a.path
            LEFT JOIN image_file_integrity ib ON ib.image_id=b.id AND ib.checked_path=b.path
            WHERE r.relation=? AND r.status='unreviewed'
              AND COALESCE(ia.presence_status,'unknown') NOT IN ('missing','path_error')
              AND COALESCE(ib.presence_status,'unknown') NOT IN ('missing','path_error')
              AND NOT EXISTS(
                  SELECT 1 FROM ignored_duplicate_pairs p
                  WHERE p.relation IN ('not_duplicate','alternative')
                    AND p.md5_a=CASE WHEN lower(COALESCE(a.hash_md5,'')) < lower(COALESCE(b.hash_md5,''))
                                     THEN lower(COALESCE(a.hash_md5,'')) ELSE lower(COALESCE(b.hash_md5,'')) END
                    AND p.md5_b=CASE WHEN lower(COALESCE(a.hash_md5,'')) < lower(COALESCE(b.hash_md5,''))
                                     THEN lower(COALESCE(b.hash_md5,'')) ELSE lower(COALESCE(a.hash_md5,'')) END
              )
            ORDER BY r.distance ASC,r.confidence DESC,r.image_a,r.image_b
            LIMIT ?
            """,
            (RELATION_VISUAL_PHASH, limit),
        ).fetchall()
    finally:
        con.close()

    def item(row, prefix: str) -> dict:
        return {
            "image_id": int(row[f"{prefix}_id"]),
            "path": str(row[f"{prefix}_path"] or ""),
            "name": str(row[f"{prefix}_name"] or Path(str(row[f"{prefix}_path"] or "")).name),
            "bytes": int(row[f"{prefix}_bytes"] or 0),
            "pixels": [int(row[f"{prefix}_width"] or 0), int(row[f"{prefix}_height"] or 0)],
            "md5": str(row[f"{prefix}_md5"] or ""),
            "phash": str(row[f"{prefix}_phash"] or ""),
        }

    groups = []
    for row in rows:
        distance = int(row["distance"] or 0)
        confidence = float(row["confidence"] or distance_to_similarity(distance))
        threshold = int(row["threshold_percent"] or 0)
        reason = f"Похожая пара: pHash {confidence:.1f}% (distance={distance})"
        if threshold:
            reason += f"; порог скана {threshold}%"
        groups.append({
            "relation_id": int(row["relation_id"]),
            "relation": RELATION_VISUAL_PHASH,
            "distance": distance,
            "similarity": confidence,
            "reason": reason,
            "items": [item(row, "a"), item(row, "b")],
        })
    return {"total_groups": total, "groups": groups}


def latest_duplicate_scan(settings: dict) -> dict:
    con = connect(settings, readonly=True)
    try:
        row = con.execute(
            "SELECT * FROM duplicate_scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else {}
    finally:
        con.close()


def run_duplicate_scan(
    settings: dict,
    threshold_percent: int = 85,
    *,
    mode: str = "forced",
    chunk_size: int = 250,
    progress: Callable[[str], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
    result_limit: int = 250,
    scan_filesystem_fallback: bool = True,
) -> dict:
    """Rebuild visual candidate relations and return pairwise UI groups.

    The old relation set remains live on cancellation, source revision changes,
    pair-limit failure, or any other exception.
    """
    threshold = max(50, min(100, int(threshold_percent or 85)))
    max_distance = threshold_to_distance(threshold)
    chunk_size = max(25, min(2000, int(chunk_size or 250)))
    try:
        pair_limit = int((settings or {}).get("duplicate_candidate_pair_limit", DEFAULT_PAIR_LIMIT) or DEFAULT_PAIR_LIMIT)
    except Exception:
        pair_limit = DEFAULT_PAIR_LIMIT
    pair_limit = max(1000, min(5_000_000, pair_limit))
    started = time.monotonic()

    # Rebuild uses the same lane, so it must happen before acquiring the scan lane.
    # Retry once if a relevant write lands between readiness check and lane entry.
    for attempt in range(2):
        state = _ensure_vp_tree(settings, mode=mode, progress=progress, stop_check=stop_check)
        coordinator = get_maintenance_coordinator()
        retry = False
        with coordinator.maintenance_job(
            "duplicate_candidate_scan",
            stop_check=stop_check,
            progress=progress,
        ):
            current = _cache_state(settings)
            if not _state_ready(current) or int(current.get("generation") or 0) != int(state.get("generation") or 0):
                retry = True
            else:
                return _run_duplicate_scan_locked(
                    settings,
                    threshold=threshold,
                    max_distance=max_distance,
                    pair_limit=pair_limit,
                    mode=mode,
                    chunk_size=chunk_size,
                    progress=progress,
                    stop_check=stop_check,
                    result_limit=result_limit,
                    scan_filesystem_fallback=scan_filesystem_fallback,
                    state=dict(current),
                    started=started,
                )
        if not retry:
            break
        _progress(progress, f"Дубликаты: VP-tree успел устареть перед стартом; повтор {attempt + 1}/2")
    raise DuplicateScanChanged("VP-tree changed repeatedly before duplicate scan could start")


def _run_duplicate_scan_locked(
    settings: dict,
    *,
    threshold: int,
    max_distance: int,
    pair_limit: int,
    mode: str,
    chunk_size: int,
    progress,
    stop_check,
    result_limit: int,
    scan_filesystem_fallback: bool,
    state: dict,
    started: float,
) -> dict:
    run_id = _start_run(settings, threshold, max_distance, state)
    stage_con = None
    stage_path = None
    source_con = None
    values = {
        "total_images": 0,
        "processed_images": 0,
        "candidate_pairs": 0,
        "stored_pairs": 0,
        "ignored_pairs": 0,
        "stale_removed": 0,
        "unindexed_files": 0,
        "unindexed_samples": [],
    }
    try:
        _wait_chunk(settings, mode=mode, progress=progress, stop_check=stop_check)
        source_con = connect(settings, readonly=True)
        stage_con, stage_path = _new_stage_db()
        external_selected = str(state.get("storage_backend") or "main") == "external"
        with open_selected_cache(
            settings, CACHE_VP_TREE, main_connection=source_con, require_fresh=True,
            allow_main_fallback=not external_selected,
        ) as (tree_con, _cache_info):
            values.update(_stage_candidate_pairs(
                settings,
                source_con,
                tree_con,
                stage_con,
                max_distance=max_distance,
                pair_limit=pair_limit,
                mode=mode,
                chunk_size=chunk_size,
                progress=progress,
                stop_check=stop_check,
            ))
        fallback = {"count": 0, "samples": [], "root_issues": []}
        if scan_filesystem_fallback:
            fallback = _scan_unindexed_files(
                settings,
                source_con,
                mode=mode,
                progress=progress,
                stop_check=stop_check,
            )
            values["unindexed_files"] = int(fallback.get("count") or 0)
            values["unindexed_samples"] = list(fallback.get("samples") or [])
        if stop_check and stop_check():
            raise MaintenanceCancelled("duplicate scan cancelled before relation swap")
        values.update(_atomic_store_pairs(
            settings,
            stage_con,
            run_id=run_id,
            source_revision=int(state.get("source_revision") or 0),
            vp_generation=int(state.get("generation") or 0),
            storage_backend=str(state.get("storage_backend") or "main"),
            cache_file_id=str(state.get("cache_file_id") or ""),
            run_values=values,
        ))
        loaded = load_duplicate_groups(settings, limit=result_limit)
        result = {
            "ok": True,
            "run_id": run_id,
            "threshold_percent": threshold,
            "max_distance": max_distance,
            "source_revision": int(state.get("source_revision") or 0),
            "vp_generation": int(state.get("generation") or 0),
            "vp_storage_backend": str(state.get("storage_backend") or "main"),
            "vp_cache_file_id": str(state.get("cache_file_id") or ""),
            **values,
            **loaded,
            "filesystem_root_issues": fallback.get("root_issues", []),
            "duration_seconds": round(max(0.0, time.monotonic() - started), 3),
        }
        _progress(
            progress,
            f"Дубликаты готовы: пар={result['stored_pairs']}; показано={len(result['groups'])}; "
            f"неиндексированных={result['unindexed_files']}",
        )
        return result
    except MaintenanceCancelled as exc:
        _finish_run(settings, run_id, "cancelled", values, str(exc))
        return {
            "ok": False,
            "cancelled": True,
            "run_id": run_id,
            **values,
            "error": str(exc),
            "duration_seconds": round(max(0.0, time.monotonic() - started), 3),
        }
    except Exception as exc:
        _finish_run(settings, run_id, "failed", values, f"{type(exc).__name__}: {exc}")
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
