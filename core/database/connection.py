from __future__ import annotations

from pathlib import Path
import os
import sqlite3
from contextlib import contextmanager
from threading import Lock, local

_INIT_LOCK = Lock()
_INIT_DONE = set()
_TLS = local()
_POOL_LOCK = Lock()
_ALL_CONNECTIONS = []
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
        root.mkdir(parents=True, exist_ok=True)
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


def _guard_missing_existing_db(settings, path: Path, *, readonly: bool) -> None:
    if path.exists():
        return
    if readonly:
        return
    if _allow_missing_db_recreate(settings, path):
        return
    already_init = False
    try:
        already_init = bool((settings or {}).get("db_initialized_once", False))
    except Exception:
        already_init = False
    try:
        already_init = already_init or _db_init_marker(path).exists()
    except Exception:
        pass
    if already_init:
        raise DatabaseMissingError(
            "SQLite database is missing and Local Booru will not create a new empty DB automatically: "
            + str(path)
            + ". Проверь диск/путь. Если это намеренный полный сброс базы, создай рядом файл "
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
    try:
        cache_mb = int((settings or {}).get("sqlite_cache_mb", 40) or 40)
    except Exception:
        cache_mb = 40
    cache_mb = max(8, min(512, cache_mb))
    con.execute(f"PRAGMA cache_size={-cache_mb * 1024}")
    return con


def ensure_initialized(con, *, force=False, settings=None):
    try:
        path = con.execute("PRAGMA database_list").fetchone()[2]
    except Exception:
        path = "memory"
    if writes_blocked():
        # The DB failed startup validation.  Do not attempt schema changes while
        # pages are opening; read-only inspection and manual recovery remain possible.
        return
    if not force and path in _INIT_DONE:
        return
    with _INIT_LOCK:
        if not force and path in _INIT_DONE:
            return
        from .schema import init_db
        try:
            init_db(con, force=True)
        except Exception as e:
            if "readonly" in str(e).lower():
                # Read-only connection: DB was already initialised elsewhere, safe to continue.
                pass
            else:
                raise
        try:
            _mark_db_initialized(settings, Path(path))
        except Exception:
            pass
        _INIT_DONE.add(path)



def _pooled_key(settings, readonly: bool) -> str:
    return f"{db_path(settings)}|ro={int(bool(readonly))}"

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
    con = connect(settings, readonly=readonly)
    ensure_initialized(con, settings=settings)
    cache[key] = con
    with _POOL_LOCK:
        _ALL_CONNECTIONS.append(con)
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
            with _POOL_LOCK:
                while con in _ALL_CONNECTIONS:
                    _ALL_CONNECTIONS.remove(con)
        except Exception:
            pass
    return n

def close_pooled_connections() -> int:
    """Close known pooled connections during graceful shutdown."""
    n = 0
    with _POOL_LOCK:
        cons = list(_ALL_CONNECTIONS)
        _ALL_CONNECTIONS.clear()
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
def db(settings, write: bool = False, readonly: bool = False, allow_blocked_write: bool = False):
    """Context-managed SQLite connection.

    Every function that touches SQLite should go through this helper. It fixes
    the old WAL lock leaks by closing connections deterministically.
    """
    if write and writes_blocked() and not allow_blocked_write:
        raise DatabaseWriteBlockedError("SQLite работает в безопасном режиме только чтения: " + writes_blocked_reason())
    use_pool = bool((settings or {}).get("sqlite_connection_pool", True))
    con = get_pooled_connection(settings, readonly=readonly and not write) if use_pool else connect(settings, readonly=readonly and not write)
    try:
        if not use_pool:
            ensure_initialized(con, settings=settings)
        yield con
        if write:
            con.commit()
    except Exception:
        if write:
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


def get_connection(settings):
    """Compatibility helper. Returns a pooled connection by default.
    Prefer using the db() context manager for writes.
    """
    if bool((settings or {}).get("sqlite_connection_pool", True)):
        return get_pooled_connection(settings)
    con = connect(settings)
    ensure_initialized(con, settings=settings)
    return con
