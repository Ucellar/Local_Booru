"""Disposable generation files for large derived SQLite caches.

``main.db`` remains the sole source of truth.  Each external cache generation is
an immutable, self-describing SQLite file.  Publication deliberately uses a
one-way protocol instead of a cross-database transaction:

1. build and validate a new generation file;
2. rename it to its final immutable name;
3. under the main DB writer lock, re-check the source revision and publish the
   file id in ``derived_cache_state``.

A crash between (2) and (3) leaves an ignored orphan.  A missing/corrupt file
never invalidates primary metadata; readers reject it and fall back/rebuild.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from typing import Iterator

from core.paths import CACHE_DIR

CACHE_SCHEMA_VERSION = 1
SUPPORTED_EXTERNAL_CACHES = {"vp_tree", "image_effective_tag_category"}
_FILE_RE = re.compile(r"^derived_(vp_tree|image_effective_tag_category)_([0-9a-f]{32})\.sqlite3$")
_VALIDATION_LOCK = threading.RLock()
_VALIDATED: dict[str, tuple[tuple[int, int], dict]] = {}


class ExternalCacheInvalid(RuntimeError):
    """Published cache file does not match the authority recorded by main.db."""


def external_cache_enabled(settings: dict | None) -> bool:
    return bool((settings or {}).get("derived_cache_external_enabled", False))


def external_cache_dir(settings: dict | None = None, *, create: bool = False) -> Path:
    # CACHE_DIR already follows the active portable/separate Local Booru archive.
    root = Path(CACHE_DIR) / "derived"
    if create:
        try:
            from core.storage_roots import check_storage_root
            state = check_storage_root(root)
            if state.status in {"offline_root", "path_error"}:
                raise ExternalCacheInvalid(
                    f"derived cache root is unavailable: status={state.status}; root={state.root}; detail={state.detail}"
                )
        except ExternalCacheInvalid:
            raise
        except Exception as exc:
            raise ExternalCacheInvalid(f"cannot classify derived cache root {root}: {type(exc).__name__}: {exc}") from exc
        root.mkdir(parents=True, exist_ok=True)
    return root


def _slug(cache_name: str) -> str:
    name = str(cache_name or "").strip()
    if name not in SUPPORTED_EXTERNAL_CACHES:
        raise ValueError(f"unsupported external derived cache: {name}")
    return name


def generation_path(settings: dict | None, cache_name: str, file_id: str) -> Path:
    name = _slug(cache_name)
    clean_id = str(file_id or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", clean_id):
        raise ExternalCacheInvalid("invalid external cache file id")
    return external_cache_dir(settings) / f"derived_{name}_{clean_id}.sqlite3"


def _main_authority(settings: dict, cache_name: str) -> dict:
    from core.database.connection import connect

    con = connect(settings, readonly=True)
    try:
        state = con.execute(
            """
            SELECT s.cache_name,s.generation,s.source_revision,s.content_hash,s.status,
                   s.source_rows,s.cache_rows,s.storage_backend,s.cache_file_id,
                   s.cache_schema_version,s.published_at,
                   COALESCE(r.revision,0) AS current_source_revision,
                   COALESCE((SELECT value FROM meta WHERE key='derived_cache_owner_id'),'') AS owner_id
            FROM derived_cache_state s
            LEFT JOIN derived_source_revision r ON r.cache_name=s.cache_name
            WHERE s.cache_name=?
            """,
            (str(cache_name),),
        ).fetchone()
        return dict(state) if state else {}
    finally:
        con.close()


def _readonly_uri(path: Path) -> str:
    """Return a correctly escaped immutable SQLite URI for one generation."""
    try:
        base = path.resolve().as_uri()
    except Exception:
        base = Path(path).absolute().as_uri()
    return base + ("&" if "?" in base else "?") + "mode=ro&immutable=1"


def _read_manifest_uncached(path: Path) -> dict:
    try:
        stat = path.stat()
    except OSError as exc:
        raise ExternalCacheInvalid(f"external cache file is unavailable: {path}: {exc}") from exc
    signature = (int(stat.st_size), int(stat.st_mtime_ns))
    key = str(path.resolve())
    with _VALIDATION_LOCK:
        cached = _VALIDATED.get(key)
        if cached and cached[0] == signature:
            return dict(cached[1])

    con = None
    try:
        con = sqlite3.connect(_readonly_uri(path), uri=True, timeout=20)
        con.row_factory = sqlite3.Row
        quick = con.execute("PRAGMA quick_check(1)").fetchone()
        if not quick or str(quick[0]).lower() != "ok":
            raise ExternalCacheInvalid(f"external cache quick_check failed: {quick[0] if quick else 'no result'}")
        row = con.execute("SELECT * FROM cache_manifest WHERE id=1").fetchone()
        if row is None:
            raise ExternalCacheInvalid("external cache manifest is missing")
        manifest = dict(row)
        cache_name = str(manifest.get("cache_name") or "")
        expected_table = {
            "vp_tree": "vp_tree",
            "image_effective_tag_category": "image_effective_tag_category",
        }.get(cache_name)
        if expected_table is None:
            raise ExternalCacheInvalid(f"external cache manifest has unsupported cache_name: {cache_name!r}")
        actual_rows = int(con.execute(f"SELECT COUNT(*) FROM {expected_table}").fetchone()[0] or 0)
        if actual_rows != int(manifest.get("cache_rows") or 0):
            raise ExternalCacheInvalid(
                f"external cache row count mismatch: manifest={manifest.get('cache_rows')} actual={actual_rows}"
            )
    except ExternalCacheInvalid:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
        raise ExternalCacheInvalid(f"external cache validation failed for {path}: {type(exc).__name__}: {exc}") from exc
    finally:
        if con is not None:
            con.close()
    with _VALIDATION_LOCK:
        _VALIDATED[key] = (signature, dict(manifest))
    return manifest


def validate_external_cache(
    settings: dict,
    cache_name: str,
    *,
    authority: dict | None = None,
    require_fresh: bool = True,
) -> dict:
    name = _slug(cache_name)
    state = dict(authority or _main_authority(settings, name))
    if str(state.get("storage_backend") or "main") != "external":
        raise ExternalCacheInvalid(f"{name}: main.db does not select external backend")
    if str(state.get("status") or "") != "ready":
        raise ExternalCacheInvalid(f"{name}: main.db cache state is not ready")
    if require_fresh and int(state.get("source_revision") or 0) != int(state.get("current_source_revision") or 0):
        raise ExternalCacheInvalid(f"{name}: external cache is stale")
    file_id = str(state.get("cache_file_id") or "")
    path = generation_path(settings, name, file_id)
    if not path.is_file():
        raise ExternalCacheInvalid(f"{name}: published generation file is missing: {path}")
    manifest = _read_manifest_uncached(path)
    expected = {
        "cache_name": name,
        "file_id": file_id,
        "owner_id": str(state.get("owner_id") or ""),
        "generation": int(state.get("generation") or 0),
        "source_revision": int(state.get("source_revision") or 0),
        "content_hash": str(state.get("content_hash") or ""),
        "source_rows": int(state.get("source_rows") or 0),
        "cache_rows": int(state.get("cache_rows") or 0),
        "schema_version": int(state.get("cache_schema_version") or 0),
    }
    for key, value in expected.items():
        actual = manifest.get(key)
        if key in {"generation", "source_revision", "source_rows", "cache_rows", "schema_version"}:
            actual = int(actual or 0)
        else:
            actual = str(actual or "")
        if actual != value:
            raise ExternalCacheInvalid(f"{name}: manifest mismatch for {key}: expected={value!r} actual={actual!r}")
    if int(manifest.get("schema_version") or 0) != CACHE_SCHEMA_VERSION:
        raise ExternalCacheInvalid(f"{name}: unsupported external cache schema")
    return {"path": path, "manifest": manifest, "authority": state, "fresh": int(state.get("source_revision") or 0) == int(state.get("current_source_revision") or 0)}


@contextmanager
def open_external_cache(
    settings: dict,
    cache_name: str,
    *,
    require_fresh: bool = True,
) -> Iterator[tuple[sqlite3.Connection, dict]]:
    validated = validate_external_cache(settings, cache_name, require_fresh=require_fresh)
    path = Path(validated["path"])
    con = sqlite3.connect(_readonly_uri(path), uri=True, timeout=20, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        yield con, validated
    finally:
        con.close()


def _create_generation_db(
    stage_con: sqlite3.Connection,
    target: Path,
    *,
    cache_name: str,
    file_id: str,
    owner_id: str,
    generation: int,
    source_revision: int,
    content_hash: str,
    source_rows: int,
    cache_rows: int,
) -> None:
    con = sqlite3.connect(str(target), timeout=60)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA temp_store=FILE")
        con.executescript(
            """
            CREATE TABLE cache_manifest (
                id INTEGER PRIMARY KEY CHECK(id=1),
                cache_name TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                source_revision INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                source_rows INTEGER NOT NULL,
                cache_rows INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            """
        )
        if cache_name == "vp_tree":
            con.executescript(
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
            cur = stage_con.execute(
                "SELECT node_id,image_id,phash,radius,inner_id,outer_id,inner_pop,outer_pop,parent_id FROM vp_tree ORDER BY node_id"
            )
            while True:
                rows = cur.fetchmany(5000)
                if not rows:
                    break
                con.executemany(
                    "INSERT INTO vp_tree(node_id,image_id,phash,radius,inner_id,outer_id,inner_pop,outer_pop,parent_id) VALUES(?,?,?,?,?,?,?,?,?)",
                    rows,
                )
        else:
            con.executescript(
                """
                CREATE TABLE image_effective_tag_category (
                    image_id INTEGER NOT NULL,
                    tag_id INTEGER NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(image_id,tag_id)
                ) WITHOUT ROWID;
                CREATE INDEX idx_external_effective_category
                    ON image_effective_tag_category(category,tag_id,image_id);
                CREATE INDEX idx_external_effective_tag
                    ON image_effective_tag_category(tag_id,image_id);
                """
            )
            cur = stage_con.execute(
                "SELECT image_id,tag_id,category,updated_at FROM effective ORDER BY image_id,tag_id"
            )
            while True:
                rows = cur.fetchmany(5000)
                if not rows:
                    break
                con.executemany(
                    "INSERT INTO image_effective_tag_category(image_id,tag_id,category,updated_at) VALUES(?,?,?,?)",
                    rows,
                )
        actual = int(con.execute(
            "SELECT COUNT(*) FROM " + ("vp_tree" if cache_name == "vp_tree" else "image_effective_tag_category")
        ).fetchone()[0] or 0)
        if actual != int(cache_rows):
            raise ExternalCacheInvalid(f"generation row count mismatch: expected={cache_rows} actual={actual}")
        con.execute(
            "INSERT INTO cache_manifest VALUES(1,?,?,?,?,?,?,?,?,?,?)",
            (
                cache_name, CACHE_SCHEMA_VERSION, file_id, owner_id, int(generation),
                int(source_revision), str(content_hash), int(source_rows), int(cache_rows), int(time.time()),
            ),
        )
        con.commit()
        quick = con.execute("PRAGMA quick_check(1)").fetchone()
        if not quick or str(quick[0]).lower() != "ok":
            raise ExternalCacheInvalid("new external generation failed quick_check")
    finally:
        con.close()
    # fsync the immutable generation before main.db can point at it.
    with target.open("rb") as handle:
        os.fsync(handle.fileno())


def publish_stage_generation(
    settings: dict,
    cache_name: str,
    stage_con: sqlite3.Connection,
    *,
    generation: int,
    source_revision: int,
    content_hash: str,
    source_rows: int,
    cache_rows: int,
    owner_id: str,
) -> dict:
    name = _slug(cache_name)
    root = external_cache_dir(settings, create=True)
    file_id = uuid.uuid4().hex
    final_path = generation_path(settings, name, file_id)
    temp_path = root / f".{final_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        _create_generation_db(
            stage_con,
            temp_path,
            cache_name=name,
            file_id=file_id,
            owner_id=str(owner_id),
            generation=int(generation),
            source_revision=int(source_revision),
            content_hash=str(content_hash),
            source_rows=int(source_rows),
            cache_rows=int(cache_rows),
        )
        os.replace(temp_path, final_path)
        try:
            flags = getattr(os, "O_DIRECTORY", 0)
            if flags:
                fd = os.open(str(root), flags)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except Exception:
            pass
        # Validate by reopening the final immutable name before publication.
        with _VALIDATION_LOCK:
            _VALIDATED.pop(str(final_path.resolve()), None)
        manifest = _read_manifest_uncached(final_path)
        if str(manifest.get("file_id") or "") != file_id:
            raise ExternalCacheInvalid("published generation file id mismatch")
        return {
            "path": final_path,
            "file_id": file_id,
            "schema_version": CACHE_SCHEMA_VERSION,
            "manifest": manifest,
        }
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _active_external_file_ids(settings: dict) -> set[str]:
    """Read authoritative file ids or fail closed before deleting any cache."""
    from core.database.connection import connect

    con = connect(settings, readonly=True)
    try:
        return {
            str(row[0] or "")
            for row in con.execute(
                "SELECT cache_file_id FROM derived_cache_state "
                "WHERE storage_backend='external' AND cache_file_id!=''"
            ).fetchall()
        }
    finally:
        con.close()


def garbage_collect_external_generations(settings: dict, *, keep_per_cache: int = 2) -> dict:
    """Best-effort deletion of orphan/old immutable generations.

    Open readers can keep an old generation locked on Windows; failure is safe
    and merely leaves a disposable file for the next pass.
    """
    keep_per_cache = max(1, min(10, int(keep_per_cache or 2)))
    root = external_cache_dir(settings)
    if not root.exists():
        return {"removed": 0, "kept": 0, "errors": []}
    try:
        active_ids = _active_external_file_ids(settings)
    except Exception as exc:
        # Authority could not be read.  Deleting anything would risk removing
        # the currently selected generation, so fail closed and retry later.
        return {
            "removed": 0,
            "kept": sum(1 for _ in root.glob("derived_*.sqlite3")),
            "errors": [f"authority_unavailable: {type(exc).__name__}: {exc}"],
        }
    grouped: dict[str, list[Path]] = {name: [] for name in SUPPORTED_EXTERNAL_CACHES}
    for path in root.glob("derived_*.sqlite3"):
        match = _FILE_RE.match(path.name)
        if match:
            grouped.setdefault(match.group(1), []).append(path)
    removed = kept = 0
    errors: list[str] = []
    for _name, paths in grouped.items():
        def _mtime(candidate: Path) -> int:
            try:
                return int(candidate.stat().st_mtime_ns)
            except OSError:
                return 0
        paths.sort(key=_mtime, reverse=True)
        protected = {p.name for p in paths[:keep_per_cache]}
        for path in paths:
            match = _FILE_RE.match(path.name)
            file_id = match.group(2) if match else ""
            if file_id in active_ids or path.name in protected:
                kept += 1
                continue
            try:
                path.unlink(missing_ok=True)
                removed += 1
                with _VALIDATION_LOCK:
                    _VALIDATED.pop(str(path.resolve()), None)
            except Exception as exc:
                errors.append(f"{path}: {exc}")
    return {"removed": removed, "kept": kept, "errors": errors}


def clear_external_generations(settings: dict) -> dict:
    """Delete only unpublished generations and always preserve active files.

    The name is retained for compatibility, but this helper deliberately cannot
    make a valid main.db pointer dangle.  Switching back to the main backend must
    happen through a successful rebuild before its former generation is eligible
    for removal.
    """
    root = external_cache_dir(settings)
    removed = 0
    kept_active = 0
    errors: list[str] = []
    try:
        active_ids = _active_external_file_ids(settings)
    except Exception as exc:
        return {
            "removed": 0,
            "kept_active": 0,
            "errors": [f"authority_unavailable: {type(exc).__name__}: {exc}"],
        }
    if root.exists():
        for path in root.glob("derived_*.sqlite3"):
            match = _FILE_RE.match(path.name)
            if match and match.group(2) in active_ids:
                kept_active += 1
                continue
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except Exception as exc:
                errors.append(f"{path}: {exc}")
    with _VALIDATION_LOCK:
        # Clearing validation memoisation is harmless; active immutable files
        # are reopened and revalidated on their next read.
        _VALIDATED.clear()
    return {"removed": removed, "kept_active": kept_active, "errors": errors}


def selected_backend_from_connection(con: sqlite3.Connection, cache_name: str) -> str:
    """Read the backend selected by main.db; old/staging DBs default to main."""
    try:
        row = con.execute(
            "SELECT storage_backend FROM derived_cache_state WHERE cache_name=?",
            (str(cache_name),),
        ).fetchone()
        value = str(row[0] or "main") if row else "main"
        return value if value in {"main", "external"} else "main"
    except Exception:
        return "main"


def main_cache_writes_enabled(con: sqlite3.Connection, cache_name: str) -> bool:
    return selected_backend_from_connection(con, cache_name) != "external"


@contextmanager
def open_selected_cache(
    settings: dict,
    cache_name: str,
    *,
    main_connection: sqlite3.Connection | None = None,
    require_fresh: bool = True,
    allow_main_fallback: bool = True,
) -> Iterator[tuple[sqlite3.Connection, dict]]:
    """Yield the selected cache connection without ever writing cross-database.

    ``main_connection`` is borrowed and therefore not closed.  External readers
    own an immutable generation connection that is closed at context exit.
    """
    authority = _main_authority(settings, cache_name)
    if str(authority.get("storage_backend") or "main") == "external":
        try:
            with open_external_cache(settings, cache_name, require_fresh=require_fresh) as pair:
                yield pair
                return
        except ExternalCacheInvalid:
            if not allow_main_fallback:
                raise
    if main_connection is not None:
        yield main_connection, {"authority": authority, "fresh": True, "path": None, "manifest": {}}
        return
    from core.database.connection import connect
    con = connect(settings, readonly=True)
    try:
        yield con, {"authority": authority, "fresh": True, "path": None, "manifest": {}}
    finally:
        con.close()


@contextmanager
def attach_external_cache(
    con: sqlite3.Connection,
    settings: dict,
    cache_name: str,
    *,
    alias: str = "derived_external",
    require_fresh: bool = True,
) -> Iterator[tuple[str, dict]]:
    """Attach one immutable generation read-only for a cross-DB SELECT.

    This helper never performs cross-database writes.  The main connection may
    be query-only; SQLite permits attaching another read-only database for joins.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(alias or "")):
        raise ValueError("invalid SQLite attach alias")
    validated = validate_external_cache(settings, cache_name, require_fresh=require_fresh)
    path = Path(validated["path"])
    uri = _readonly_uri(path)
    attached = False
    try:
        existing = {str(row[1]) for row in con.execute("PRAGMA database_list").fetchall()}
        if alias in existing:
            con.execute(f"DETACH DATABASE {alias}")
        con.execute(f"ATTACH DATABASE ? AS {alias}", (uri,))
        attached = True
        yield alias, validated
    finally:
        if attached:
            try:
                con.execute(f"DETACH DATABASE {alias}")
            except Exception:
                pass
