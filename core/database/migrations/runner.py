from __future__ import annotations

import time
from typing import Callable

from . import m014_database_health_maintenance

# Version 13 is the consolidated SQLite schema shipped before the numbered
# runner existed.  v130 records that existing working state as a baseline and
# applies only subsequent migrations.
BASELINE_VERSION = 13
CURRENT_SCHEMA_VERSION = 14
MIGRATIONS = {
    14: m014_database_health_maintenance,
}


def _has_user_schema(con) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='images' LIMIT 1"
    ).fetchone()
    return bool(row)


def _ensure_journal(con) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'applied',
        error TEXT NOT NULL DEFAULT '',
        applied_at INTEGER NOT NULL DEFAULT 0
    );
    """)


def _version_from_meta(con) -> int:
    try:
        row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row and str(row[0]).strip() else 0
    except Exception:
        return 0


def run_migrations(con, *, log: Callable[[str], None] | None = None) -> dict:
    """Apply pending numbered migrations transactionally.

    Databases created by v129 and earlier already contain the consolidated
    v13 table set; they are baseline-recorded, not rebuilt or discarded.
    For the current single-developer phase the database may still be safely
    deleted and reconstructed from the protected source archive when desired.
    """
    log = log or (lambda _msg: None)
    _ensure_journal(con)
    recorded = {int(r[0]) for r in con.execute(
        "SELECT version FROM schema_migrations WHERE status='applied'"
    ).fetchall()}
    meta_version = _version_from_meta(con)
    actions: list[str] = []
    now = int(time.time())

    if BASELINE_VERSION not in recorded and _has_user_schema(con):
        con.execute(
            "INSERT OR REPLACE INTO schema_migrations(version,name,status,error,applied_at) VALUES(?,?,?,?,?)",
            (BASELINE_VERSION, "baseline_consolidated_schema_v13", "applied", "", now),
        )
        recorded.add(BASELINE_VERSION)
        actions.append("baseline:13")
        log("SQLite migrations: captured existing consolidated schema as baseline v13")

    current = max(recorded | {meta_version, BASELINE_VERSION if _has_user_schema(con) else 0})
    for version in sorted(MIGRATIONS):
        if version in recorded or version <= current:
            continue
        module = MIGRATIONS[version]
        try:
            module.apply(con)
            con.execute(
                "INSERT OR REPLACE INTO schema_migrations(version,name,status,error,applied_at) VALUES(?,?,?,?,?)",
                (version, module.NAME, "applied", "", int(time.time())),
            )
            actions.append(f"applied:{version}:{module.NAME}")
            current = version
            log(f"SQLite migration applied: {version:03d} {module.NAME}")
        except Exception as exc:
            con.execute(
                "INSERT OR REPLACE INTO schema_migrations(version,name,status,error,applied_at) VALUES(?,?,?,?,?)",
                (version, module.NAME, "failed", str(exc), int(time.time())),
            )
            raise RuntimeError(f"SQLite migration {version:03d} {module.NAME} failed: {exc}") from exc

    final_version = max(current, BASELINE_VERSION if _has_user_schema(con) else 0)
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(final_version),))
    return {"version": final_version, "actions": actions}


def migration_status(con) -> dict:
    try:
        rows = con.execute(
            "SELECT version,name,status,error,applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        history = [dict(r) if hasattr(r, "keys") else {
            "version": r[0], "name": r[1], "status": r[2], "error": r[3], "applied_at": r[4]
        } for r in rows]
    except Exception:
        history = []
    return {"current": _version_from_meta(con), "target": CURRENT_SCHEMA_VERSION, "history": history}
