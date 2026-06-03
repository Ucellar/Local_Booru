from __future__ import annotations

from pathlib import Path
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


def set_writes_blocked(reason: str = "") -> None:
    global _WRITE_BLOCK_REASON
    _WRITE_BLOCK_REASON = str(reason or "").strip()


def writes_blocked_reason() -> str:
    return _WRITE_BLOCK_REASON


def writes_blocked() -> bool:
    return bool(_WRITE_BLOCK_REASON)


def db_path(settings):
    folder = str((settings or {}).get("sqlite_db_folder", "")).strip()
    if folder:
        root = Path(folder).expanduser()
    else:
        try:
            from core.paths import DB_DIR
            root = Path(DB_DIR)
        except Exception:
            root = Path.home() / "Documents" / "Local_Booru" / "db"
    root.mkdir(parents=True, exist_ok=True)
    return root / "local_booru_index.sqlite3"


def connect(settings, *, readonly: bool = False):
    path = db_path(settings)
    if readonly and path.exists():
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    else:
        con = sqlite3.connect(str(path), timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=60000")
    # Bigger cache improves tag intersection queries without storing 500k items in Python.
    con.execute("PRAGMA cache_size=-131072")  # about 128 MB
    return con


def ensure_initialized(con, *, force=False):
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
    ensure_initialized(con)
    cache[key] = con
    with _POOL_LOCK:
        _ALL_CONNECTIONS.append(con)
    return con

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
            ensure_initialized(con)
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
    ensure_initialized(con)
    return con
