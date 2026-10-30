"""Migration 034: crash-recoverable physical quality-upgrade runtime."""
from __future__ import annotations

NAME = "quality_upgrade_runtime"


def _columns(con, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def is_applied(con) -> bool:
    cols = _columns(con, "media_revisions")
    return {
        "staging_path", "retired_path", "swap_state", "verified_identity",
        "verified_quality", "recovery_action", "updated_at",
    }.issubset(cols)


def apply(con) -> None:
    cols = _columns(con, "media_revisions")
    additions = (
        ("staging_path", "TEXT NOT NULL DEFAULT ''"),
        ("retired_path", "TEXT NOT NULL DEFAULT ''"),
        ("swap_state", "TEXT NOT NULL DEFAULT 'none'"),
        ("verified_identity", "INTEGER NOT NULL DEFAULT 0"),
        ("verified_quality", "INTEGER NOT NULL DEFAULT 0"),
        ("recovery_action", "TEXT NOT NULL DEFAULT ''"),
        ("updated_at", "INTEGER NOT NULL DEFAULT 0"),
    )
    for name, ddl in additions:
        if name not in cols:
            con.execute(f"ALTER TABLE media_revisions ADD COLUMN {name} {ddl}")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_revisions_swap_state "
        "ON media_revisions(status,swap_state,updated_at)"
    )
