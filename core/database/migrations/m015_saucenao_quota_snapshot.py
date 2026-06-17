"""Migration 015: persist the last SauceNAO quota snapshot for UI and diagnostics."""
from __future__ import annotations

VERSION = 15
NAME = "saucenao_quota_snapshot"


def _has_column(con, table: str, column: str) -> bool:
    return any(str(row[1]) == column for row in con.execute(f"PRAGMA table_info({table})").fetchall())


def apply(con) -> None:
    # Consolidated v13 libraries already have service_state; create it defensively
    # for partially reconstructed/test databases before adding observation fields.
    con.execute("""CREATE TABLE IF NOT EXISTS service_state (
        service TEXT PRIMARY KEY,
        cooldown_until INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL DEFAULT 0
    )""")
    # Add only optional observation columns: this never changes queue semantics.
    if not _has_column(con, "service_state", "short_remaining"):
        con.execute("ALTER TABLE service_state ADD COLUMN short_remaining INTEGER NOT NULL DEFAULT -1")
    if not _has_column(con, "service_state", "long_remaining"):
        con.execute("ALTER TABLE service_state ADD COLUMN long_remaining INTEGER NOT NULL DEFAULT -1")
    if not _has_column(con, "service_state", "quota_checked_at"):
        con.execute("ALTER TABLE service_state ADD COLUMN quota_checked_at INTEGER NOT NULL DEFAULT 0")
