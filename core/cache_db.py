"""Shared lightweight SQLite cache database for non-durable UI/parser caches.

This module centralizes small cache tables that do not belong in the main
library database and do not justify their own SQLite/WAL file.  Connections are
thread-local and schemas are installed once per process/path instead of running
DDL on every lookup.
"""
from __future__ import annotations

import atexit
import sqlite3
import threading
from pathlib import Path

from core.paths import CACHE_DIR

_SCHEMA_LOCK = threading.RLock()
_INITIALIZED: set[str] = set()
_TLS = threading.local()


def cache_path(settings: dict | None = None) -> Path:
    root = CACHE_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root / "cache.sqlite"


def _connection_map() -> dict[str, sqlite3.Connection]:
    conns = getattr(_TLS, "cache_connections", None)
    if conns is None:
        conns = {}
        _TLS.cache_connections = conns
    return conns


def connect(settings: dict | None = None, *, schema: str = "") -> sqlite3.Connection:
    """Return a thread-local connection to settings/cache/cache.sqlite.

    ``schema`` is executed only once per process/path.  Callers can safely use
    the returned connection in ``with`` blocks; sqlite3 commits/rolls back at
    block exit but does not close the pooled handle.
    """
    path = cache_path(settings)
    key = str(path.resolve())
    conns = _connection_map()
    con = conns.get(key)
    if con is None:
        con = sqlite3.connect(str(path), timeout=20)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        conns[key] = con
    if schema:
        init_key = key + ":" + str(hash(schema))
        if init_key not in _INITIALIZED:
            with _SCHEMA_LOCK:
                if init_key not in _INITIALIZED:
                    con.executescript(schema)
                    con.commit()
                    _INITIALIZED.add(init_key)
    return con


def close_thread_cache_connections() -> None:
    conns = getattr(_TLS, "cache_connections", None)
    if not conns:
        return
    for con in list(conns.values()):
        try:
            con.close()
        except Exception:
            pass
    conns.clear()


atexit.register(close_thread_cache_connections)
