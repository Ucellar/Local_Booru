from __future__ import annotations

from pathlib import Path
import os
import re
import sqlite3
import threading
import time
import weakref
from contextlib import contextmanager
from threading import Lock, RLock, local

_INIT_LOCK = Lock()
_INIT_DONE = set()
_TLS = local()
_POOL_LOCK = Lock()
db_write_lock = RLock()
_ALL_CONNECTIONS = []
_CONNECTION_INFO = {}
_WRITE_BLOCK_REASON = ""


class DatabaseWriteBlockedError(RuntimeError):
    """Raised when startup integrity checks put the working DB in read-only safety mode."""


class DatabaseMissingError(RuntimeError):
    """Raised when a previously initialised Local Booru SQLite DB disappeared.

    SQLite normally creates a new empty file on connect().  That is dangerous for
    this app: a disconnected/misconfigured archive drive would look like "all
    tags vanished".  Intentional full resets remain possible with an explicit
    marker/env override.
    """


def set_writes_blocked(reason: str = "") -> None:
    global _WRITE_BLOCK_REASON
    _WRITE_BLOCK_REASON = str(reason or "").strip()


def writes_blocked_reason() -> str:
    return _WRITE_BLOCK_REASON


def writes_blocked() -> bool:
    return bool(_WRITE_BLOCK_REASON)


def _db_root(settings, *, create: bool = True) -> Path:
    folder = str((settings or {}).get("sqlite_db_folder", "")).strip()
    if folder:
        root = Path(folder).expanduser()
    else:
        try:
            from core.paths import DB_DIR
            root = Path(DB_DIR)
        except Exception:
            root = Path.cwd() / "Local_Booru_Archive" / "settings" / "db"
    if create:
        try:
            from core.storage_roots import check_storage_root, ROOT_OFFLINE, ROOT_ERROR
            state = check_storage_root(root)
            if state.status not in {ROOT_OFFLINE, ROOT_ERROR}:
                root.mkdir(parents=True, exist_ok=True)
        except Exception:
            # Preserve first-run behaviour when the common probe itself is not
            # available yet; the later missing-DB guard still prevents silent
            # recreation of an established database.
            try:
                root.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
    return root


def db_path(settings):
    root = _db_root(settings, create=True)
    return root / "local_booru_index.sqlite3"


def _db_init_marker(path: Path) -> Path:
    return path.with_name(path.name + ".initialized")


def _allow_missing_db_recreate(settings, path: Path) -> bool:
    if str(os.environ.get("LOCAL_BOORU_ALLOW_NEW_DB", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return True
    try:
        if bool((settings or {}).get("sqlite_allow_recreate_missing_db", False)):
            return True
    except Exception:
        pass
    # Manual emergency override: create this file beside the expected DB.
    # It is intentionally explicit so accidental disconnected drives do not
    # silently create a fresh empty SQLite.
    try:
        return (path.parent / "ALLOW_CREATE_EMPTY_DB.txt").exists()
    except Exception:
        return False


def database_was_initialized(settings, path: Path) -> bool:
    """True when a library database already lived at this path (marker file or settings flag).

    A missing file at such a path means the database disappeared (moved, deleted, drive
    unplugged): it must be reported, never silently replaced by a new empty one.
    """
    try:
        if bool((settings or {}).get("db_initialized_once", False)):
            return True
    except AttributeError:
        pass
    try:
        return _db_init_marker(Path(path)).exists()
    except OSError:
        return False


def missing_database_message(settings, path: Path) -> str:
    """'' when a missing DB file may be created (first run or explicit reset), else the reason."""
    path = Path(path)
    if path.exists() or _allow_missing_db_recreate(settings, path) or not database_was_initialized(settings, path):
        return ""
    return ("SQLite database is missing and Local Booru will not create a new empty DB automatically: "
            + str(path) + ". Проверь диск/путь. Если это намеренный полный сброс базы, создай рядом файл "
            + "ALLOW_CREATE_EMPTY_DB.txt или запусти с LOCAL_BOORU_ALLOW_NEW_DB=1.")


def reset_database_files(settings) -> list:
    """Delete the library database on purpose (Settings → «Удалить всё»).

    Removes the database, its WAL/SHM files and the «initialized» marker and clears
    ``db_initialized_once`` in ``settings``, so the next start creates a fresh empty
    database instead of reporting it as missing.  The caller saves the settings.
    """
    close_pooled_connections()
    path = Path(db_path(settings))
    deleted = []
    for target in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"), _db_init_marker(path)):
        if target.exists():
            target.unlink()
            deleted.append(str(target))
    _INIT_DONE.clear()
    settings["db_initialized_once"] = False
    return deleted


def _guard_missing_existing_db(settings, path: Path, *, readonly: bool) -> None:
    """Refuse to open (and so create) a database that existed before and is now missing.

    Applies to readers too: a read-only open of a missing file used to create an empty
    database and the app then started silently with an empty library (docs/map, trap 2).
    """
    if path.exists():
        return
    root_state = None
    try:
        from core.storage_roots import check_storage_root
        root_state = check_storage_root(path.parent)
    except Exception:
        root_state = None
    if _allow_missing_db_recreate(settings, path):
        return
    # An unavailable volume/share is never a valid first-run location.  Raise
    # the same explicit safety error instead of falling through to SQLite's
    # vague "unable to open database file" or attempting an empty database.
    if root_state is not None and root_state.status in {"offline_root", "path_error"}:
        raise DatabaseMissingError(
            "SQLite storage root is unavailable and Local Booru will not create a database: "
            + str(path)
            + f". storage_root_status={root_state.status}; storage_root={root_state.root}; detail={root_state.detail}."
            + " Проверь диск/путь."
        )
    if database_was_initialized(settings, path):
        root_detail = ""
        try:
            state = root_state
            if state is None:
                from core.storage_roots import check_storage_root
                state = check_storage_root(path.parent)
            root_detail = f" storage_root_status={state.status}; storage_root={state.root}; detail={state.detail}."
        except Exception:
            pass
        raise DatabaseMissingError(
            "SQLite database is missing and Local Booru will not create a new empty DB automatically: "
            + str(path)
            + "." + root_detail
            + " Проверь диск/путь. Если это намеренный полный сброс базы, создай рядом файл "
            + "ALLOW_CREATE_EMPTY_DB.txt или запусти с LOCAL_BOORU_ALLOW_NEW_DB=1."
        )


def _mark_db_initialized(settings, path: Path) -> None:
    """Persist only the filesystem marker that this DB path was initialized.

    Do not call save_settings() from connect()/ensure_initialized().  Those
    functions are used by parser lanes and maintenance workers with temporary
    session dictionaries; serialising such dictionaries can either fail on
    callables (for example _cancel_callback) or permanently leak per-run
    overrides into app_settings.json.  The marker file is enough to prevent
    silent recreation of a missing previously-initialised SQLite file.
    """
    try:
        marker = _db_init_marker(path)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            "Local Booru SQLite was initialised here. Delete only when intentionally resetting the DB.\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _sqlite_cache_mb(settings) -> int:
    """Return the configured per-connection SQLite page-cache cap in MiB."""
    try:
        cache_mb = int((settings or {}).get("sqlite_cache_mb", 40) or 40)
    except Exception:
        cache_mb = 40
    return max(8, min(512, cache_mb))


def connect(settings, *, readonly: bool = False):
    path = db_path(settings)
    _guard_missing_existing_db(settings, path, readonly=readonly)
    opened_readonly = bool(readonly and path.exists())
    if opened_readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    else:
        # Keep first-run behaviour: a missing DB is created by the normal schema
        # initialisation path even when the caller is only going to read from it.
        con = sqlite3.connect(str(path), timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    if opened_readonly:
        # Do not issue PRAGMA journal_mode=WAL on a read-only connection.  If an
        # old/pre-WAL database is opened read-only, SQLite treats that PRAGMA as
        # a write attempt and raises "attempt to write a readonly database".
        try:
            con.execute("PRAGMA query_only=ON")
        except Exception:
            pass
    else:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    # Large galleries/parsers can allocate huge temp sort tables.  MEMORY is
    # still the default for speed, but it is now configurable for low-RAM runs.
    temp_store = str((settings or {}).get("sqlite_temp_store", "MEMORY") or "MEMORY").upper()
    if temp_store not in {"MEMORY", "FILE", "DEFAULT"}:
        temp_store = "MEMORY"
    con.execute(f"PRAGMA temp_store={temp_store}")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=60000")
    try:
        wal_limit_mb = int((settings or {}).get("sqlite_wal_limit_mb", 512) or 512)
    except Exception:
        wal_limit_mb = 512
    wal_limit_mb = max(32, min(4096, wal_limit_mb))
    con.execute(f"PRAGMA journal_size_limit={wal_limit_mb * 1024 * 1024}")
    # Cache size is per SQLite connection. Keep it configurable so 5 site lanes
    # plus UI readers do not silently reserve hundreds of MB each.
    cache_mb = _sqlite_cache_mb(settings)
    con.execute(f"PRAGMA cache_size={-cache_mb * 1024}")
    return con


def _connection_is_readonly(con) -> bool:
    """Return True for connections that must never perform schema writes.

    ``connect(..., readonly=True)`` enables PRAGMA query_only, which is a cheap
    and deterministic signal.  Most importantly, a read-only reader must not
    mark a database path as successfully initialised after a rejected write.
    """
    try:
        row = con.execute("PRAGMA query_only").fetchone()
        return bool(row and int(row[0] or 0))
    except Exception:
        return False


def ensure_initialized(con, *, force=False, settings=None) -> bool:
    try:
        path = con.execute("PRAGMA database_list").fetchone()[2]
    except Exception:
        path = "memory"
    if writes_blocked():
        # The DB failed startup validation.  Do not attempt schema changes while
        # pages are opening; read-only inspection and manual recovery remain possible.
        return False
    if _connection_is_readonly(con):
        # A reader may be the first pooled connection opened by the UI.  It is
        # allowed to inspect an older schema, but it can never consume the one
        # process-wide successful-initialisation slot for this database path.
        return False
    if not force and path in _INIT_DONE:
        return True
    with _INIT_LOCK:
        if not force and path in _INIT_DONE:
            return True
        from .schema import init_db
        # Do not swallow readonly/schema errors and then mark the path done.
        # _INIT_DONE is written only after init_db and all pending repairs have
        # completed successfully on this writable connection.
        init_db(con, force=True)
        try:
            _mark_db_initialized(settings, Path(path))
        except Exception:
            pass
        _INIT_DONE.add(path)
        return True


def ensure_database_schema(settings, *, force: bool = False) -> dict:
    """Run the schema/migration pass on an explicitly writable connection.

    Startup calls this after the read-only smoke check and before services that
    can write (notably Browser Companion).  It deliberately bypasses the pooled
    reader cache so a read-only connection opened first cannot suppress pending
    migrations.
    """
    if writes_blocked():
        raise DatabaseWriteBlockedError(
            "SQLite schema migration is blocked: " + writes_blocked_reason()
        )
    con = connect(settings, readonly=False)
    try:
        with db_write_lock:
            ensure_initialized(con, force=force, settings=settings)
            from .migrations import migration_status
            status = migration_status(con)
            con.commit()
            return status
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            con.close()
        except Exception:
            pass



def _pooled_key(settings, readonly: bool) -> str:
    return f"{db_path(settings)}|ro={int(bool(readonly))}"


def _normalize_pool_thread_name(name: str) -> str:
    """Collapse executor instance/worker suffixes into readable forensic buckets."""
    value = str(name or "unknown").strip() or "unknown"
    if value.startswith("rule34_sha1_locator"):
        return "rule34_sha1_locator"
    value = re.sub(r"-\d+_\d+$", "", value)
    return value


def _register_pooled_connection(con, *, key: str, settings, readonly: bool) -> None:
    owner = threading.current_thread()
    try:
        owner_ref = weakref.ref(owner)
    except TypeError:
        owner_ref = None
    info = {
        "key": str(key),
        "readonly": bool(readonly),
        "cache_mb": _sqlite_cache_mb(settings),
        "owner_ref": owner_ref,
        "owner_ident": threading.get_ident(),
        "owner_name": str(getattr(owner, "name", "") or "unknown"),
        "created_at": time.monotonic(),
    }
    with _POOL_LOCK:
        _ALL_CONNECTIONS.append(con)
        _CONNECTION_INFO[id(con)] = info


def _unregister_pooled_connection(con) -> None:
    with _POOL_LOCK:
        while con in _ALL_CONNECTIONS:
            _ALL_CONNECTIONS.remove(con)
        _CONNECTION_INFO.pop(id(con), None)


def _pooled_owner_state(info: dict) -> str:
    """Return ``alive``/``dead``/``unknown`` for a registered owner thread.

    The pool contract is one connection per owner thread. Once that thread is
    gone, no valid caller can reach its thread-local cache again, so the
    connection is safe to close from another thread (all pooled connections are
    opened with ``check_same_thread=False``).
    """
    owner_ref = (info or {}).get("owner_ref")
    if not callable(owner_ref):
        return "unknown"
    owner = owner_ref()
    if owner is None:
        return "dead"
    try:
        return "alive" if bool(owner.is_alive()) else "dead"
    except Exception:
        return "dead"


def reap_dead_pooled_connections() -> int:
    """Close pooled SQLite connections whose owner threads have exited.

    v450 kept every connection in ``_ALL_CONNECTIONS`` as a strong reference
    for diagnostics/shutdown. Thread-local caches therefore could not release a
    connection when a short-lived worker exited. Long parser/gallery sessions
    accumulated thousands of dead-owner readers and their SQLite page caches.

    Selection/removal happens under the registry lock; close() happens outside
    it so a slow SQLite teardown cannot block unrelated pool registration.
    """
    dead = []
    with _POOL_LOCK:
        for con in list(_ALL_CONNECTIONS):
            info = _CONNECTION_INFO.get(id(con)) or {}
            if _pooled_owner_state(info) != "dead":
                continue
            dead.append(con)
            while con in _ALL_CONNECTIONS:
                _ALL_CONNECTIONS.remove(con)
            _CONNECTION_INFO.pop(id(con), None)
    closed = 0
    for con in dead:
        try:
            con.close()
            closed += 1
        except Exception:
            pass
    return closed


def snapshot_pooled_connections() -> dict:
    """Return lock-safe SQLite pool diagnostics without touching live connections.

    ``cache_cap_mb`` is a configured upper bound, not allocated memory.  Owner
    liveness is derived from a weak reference to the original Thread object so
    recycled numeric thread identifiers cannot make dead owners look alive.
    """
    with _POOL_LOCK:
        connections = list(_ALL_CONNECTIONS)
        info_by_id = dict(_CONNECTION_INFO)

    unique = []
    seen = set()
    for con in connections:
        marker = id(con)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(con)

    owner_alive = 0
    owner_dead = 0
    owner_unknown = 0
    readonly_count = 0
    cache_cap_mb = 0
    groups = {}
    now = time.monotonic()
    oldest_seconds = 0.0

    for con in unique:
        info = info_by_id.get(id(con)) or {}
        cache_cap_mb += int(info.get("cache_mb") or 0)
        readonly_count += int(bool(info.get("readonly")))
        created_at = float(info.get("created_at") or now)
        oldest_seconds = max(oldest_seconds, max(0.0, now - created_at))

        state = _pooled_owner_state(info)
        if state == "alive":
            owner_alive += 1
        elif state == "dead":
            owner_dead += 1
        else:
            owner_unknown += 1

        bucket = _normalize_pool_thread_name(info.get("owner_name") or "unknown")
        row = groups.setdefault(bucket, {"name": bucket, "total": 0, "alive": 0, "dead": 0, "unknown": 0})
        row["total"] += 1
        row[state] += 1

    group_rows = sorted(groups.values(), key=lambda row: (-int(row["total"]), str(row["name"])))
    return {
        "pooled": len(unique),
        "open": len(unique),
        "owner_alive": owner_alive,
        "owner_dead": owner_dead,
        "owner_unknown": owner_unknown,
        "readonly": readonly_count,
        "writable": max(0, len(unique) - readonly_count),
        "cache_cap_mb": cache_cap_mb,
        "oldest_seconds": oldest_seconds,
        "thread_groups": group_rows,
    }


def get_pooled_connection(settings, *, readonly: bool = False):
    """Return one SQLite connection per thread and db path.

    This keeps WAL connections warm during gallery + tagger + subscription work
    and avoids opening/closing SQLite for every tiny query.  Connections are
    thread-local, so callers do not share a sqlite3.Connection across threads.
    """
    key = _pooled_key(settings, readonly)
    cache = getattr(_TLS, "connections", None)
    if cache is None:
        cache = {}
        _TLS.connections = cache
    con = cache.get(key)
    if con is not None:
        try:
            con.execute("SELECT 1")
            return con
        except Exception:
            try: con.close()
            except Exception: pass
            cache.pop(key, None)
            _unregister_pooled_connection(con)
    # A new owner thread is about to add a pooled connection. Reap exited
    # owners first so the global diagnostic/shutdown registry cannot become a
    # lifetime-long strong-reference leak under worker-thread churn.
    reap_dead_pooled_connections()
    con = connect(settings, readonly=readonly)
    ensure_initialized(con, settings=settings)
    cache[key] = con
    _register_pooled_connection(con, key=key, settings=settings, readonly=readonly)
    return con



def close_thread_pooled_connections(settings=None, *, readonly: bool | None = None) -> int:
    """Close pooled SQLite connections owned by the current thread only.

    Useful before an explicit UI refresh: the gallery should open a fresh
    read-only connection/snapshot after downloader writes, without touching
    worker-thread connections that may still be active.
    """
    cache = getattr(_TLS, "connections", None)
    if not cache:
        return 0
    path_prefix = None
    if settings is not None:
        try:
            path_prefix = str(db_path(settings)) + "|"
        except Exception:
            path_prefix = None
    n = 0
    for key, con in list(cache.items()):
        if path_prefix and not str(key).startswith(path_prefix):
            continue
        if readonly is not None and f"|ro={int(bool(readonly))}" not in str(key):
            continue
        try:
            con.close()
            n += 1
        except Exception:
            pass
        try:
            cache.pop(key, None)
        except Exception:
            pass
        try:
            _unregister_pooled_connection(con)
        except Exception:
            pass
    return n

def close_pooled_connections() -> int:
    """Close known pooled connections during graceful shutdown."""
    n = 0
    with _POOL_LOCK:
        cons = list(_ALL_CONNECTIONS)
        _ALL_CONNECTIONS.clear()
        _CONNECTION_INFO.clear()
    for con in cons:
        try:
            con.close(); n += 1
        except Exception:
            pass
    try:
        _TLS.connections = {}
    except Exception:
        pass
    return n

@contextmanager
def write_txn(settings, *, allow_blocked_write: bool = False):
    """Serialised SQLite write transaction.

    Local Booru still uses worker-thread-owned SQLite connections, but write
    entry points must not compete for BEGIN/commit at random.  This lock keeps
    parser, downloader, subscription and maintenance writers ordered while WAL
    readers remain concurrent.
    """
    if writes_blocked() and not allow_blocked_write:
        raise DatabaseWriteBlockedError("SQLite работает в безопасном режиме только чтения: " + writes_blocked_reason())
    use_pool = bool((settings or {}).get("sqlite_connection_pool", True))
    con = get_pooled_connection(settings, readonly=False) if use_pool else connect(settings, readonly=False)
    with db_write_lock:
        try:
            if not use_pool:
                ensure_initialized(con, settings=settings)
            # Thread-local pooled connections may re-enter write_txn through a
            # lower-level storage helper. Join the outer transaction instead of
            # issuing a second BEGIN IMMEDIATE on the same SQLite connection.
            if con.in_transaction:
                yield con
                return
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            try:
                con.rollback()
            except Exception:
                pass
            raise
        finally:
            if not use_pool:
                try:
                    con.close()
                except Exception:
                    pass


@contextmanager
def db(settings, write: bool = False, readonly: bool = False, allow_blocked_write: bool = False):
    """Context-managed SQLite connection.

    Write=True now routes through write_txn(), which serialises application
    writers and starts BEGIN IMMEDIATE under a process-wide RLock.
    """
    if write:
        with write_txn(settings, allow_blocked_write=allow_blocked_write) as con:
            yield con
        return
    use_pool = bool((settings or {}).get("sqlite_connection_pool", True))
    con = get_pooled_connection(settings, readonly=readonly) if use_pool else connect(settings, readonly=readonly)
    try:
        if not use_pool:
            ensure_initialized(con, settings=settings)
        yield con
    finally:
        if not use_pool:
            try:
                con.close()
            except Exception:
                pass


def get_connection(settings):
    """Compatibility helper. Returns a pooled connection by default.
    Prefer using the db() context manager for writes.
    """
    if bool((settings or {}).get("sqlite_connection_pool", True)):
        return get_pooled_connection(settings)
    con = connect(settings)
    ensure_initialized(con, settings=settings)
    return con
