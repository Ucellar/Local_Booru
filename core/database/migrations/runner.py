from __future__ import annotations

import time
from typing import Callable

from . import m014_database_health_maintenance, m015_saucenao_quota_snapshot, m016_source_tag_provenance, m017_invalid_navigation_sources, m018_unverified_source_cleanup, m019_performance_indexes, m020_nomatch_reasons_source_only, m021_v158_maintenance_indexes, m022_v159_content_safe_filenames, m023_effective_tag_categories, m024_reverse_branch_status

# Version 13 is the consolidated SQLite schema shipped before the numbered
# runner existed.  v130 records that existing working state as a baseline and
# applies only subsequent migrations.
BASELINE_VERSION = 13
CURRENT_SCHEMA_VERSION = 24
MIGRATIONS = {
    14: m014_database_health_maintenance,
    15: m015_saucenao_quota_snapshot,
    16: m016_source_tag_provenance,
    17: m017_invalid_navigation_sources,
    18: m018_unverified_source_cleanup,
    19: m019_performance_indexes,
    20: m020_nomatch_reasons_source_only,
    21: m021_v158_maintenance_indexes,
    22: m022_v159_content_safe_filenames,
    23: m023_effective_tag_categories,
    24: m024_reverse_branch_status,
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
        name = str(getattr(module, "NAME", module.__name__.rsplit(".", 1)[-1]))
        try:
            module.apply(con)
            con.execute(
                "INSERT OR REPLACE INTO schema_migrations(version,name,status,error,applied_at) VALUES(?,?,?,?,?)",
                (version, name, "applied", "", int(time.time())),
            )
            actions.append(f"applied:{version}:{name}")
            current = version
            log(f"SQLite migration applied: {version:03d} {name}")
        except Exception as exc:
            con.execute(
                "INSERT OR REPLACE INTO schema_migrations(version,name,status,error,applied_at) VALUES(?,?,?,?,?)",
                (version, name, "failed", str(exc), int(time.time())),
            )
            raise RuntimeError(f"SQLite migration {version:03d} {name} failed: {exc}") from exc

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
