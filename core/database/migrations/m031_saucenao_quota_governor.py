"""Migration 031: durable SauceNAO quota-governor admission state."""
from __future__ import annotations

NAME = "saucenao_restart_safe_quota_governor"

_REQUIRED = {
    "quota_probe_after",
    "quota_last_admitted_at",
    "quota_admissions_since_snapshot",
    "quota_admission_serial",
    "quota_snapshot_serial",
}


def _columns(con, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def is_applied(con) -> bool:
    return _REQUIRED.issubset(_columns(con, "service_state"))


def apply(con) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS service_state (
            service TEXT PRIMARY KEY,
            cooldown_until INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL DEFAULT 0,
            short_remaining INTEGER NOT NULL DEFAULT -1,
            long_remaining INTEGER NOT NULL DEFAULT -1,
            quota_checked_at INTEGER NOT NULL DEFAULT 0
        )"""
    )
    columns = _columns(con, "service_state")
    additions = (
        ("quota_probe_after", "INTEGER NOT NULL DEFAULT 0"),
        ("quota_last_admitted_at", "INTEGER NOT NULL DEFAULT 0"),
        ("quota_admissions_since_snapshot", "INTEGER NOT NULL DEFAULT 0"),
        ("quota_admission_serial", "INTEGER NOT NULL DEFAULT 0"),
        ("quota_snapshot_serial", "INTEGER NOT NULL DEFAULT 0"),
    )
    for name, ddl in additions:
        if name not in columns:
            con.execute(f"ALTER TABLE service_state ADD COLUMN {name} {ddl}")
    con.execute(
        """UPDATE service_state
           SET quota_probe_after=COALESCE(quota_probe_after,0),
               quota_last_admitted_at=COALESCE(quota_last_admitted_at,0),
               quota_admissions_since_snapshot=COALESCE(quota_admissions_since_snapshot,0),
               quota_admission_serial=COALESCE(quota_admission_serial,0),
               quota_snapshot_serial=COALESCE(quota_snapshot_serial,0)
        """
    )
