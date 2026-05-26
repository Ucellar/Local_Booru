from __future__ import annotations

from pathlib import Path
import sqlite3
from contextlib import contextmanager
from threading import Lock

_INIT_LOCK = Lock()
_INIT_DONE = set()


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
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    else:
        con = sqlite3.connect(str(path), timeout=60)
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


@contextmanager
def db(settings, write: bool = False, readonly: bool = False):
    """Context-managed SQLite connection.

    Every function that touches SQLite should go through this helper. It fixes
    the old WAL lock leaks by closing connections deterministically.
    """
    con = connect(settings, readonly=readonly and not write)
    try:
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
        try:
            con.close()
        except Exception:
            pass
