"""Migration 035: paired Companion and automatic family workflow indexes."""
from __future__ import annotations

NAME = "paired_companion_and_family_workflows"


def _columns(con, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _add(con, table: str, name: str, ddl: str) -> None:
    if name not in _columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def is_applied(con) -> bool:
    return {"completion_attempts", "last_completion_error", "completed_at"}.issubset(_columns(con, "media_family_items"))


def apply(con) -> None:
    _add(con, "media_family_items", "completion_attempts", "INTEGER NOT NULL DEFAULT 0")
    _add(con, "media_family_items", "last_completion_error", "TEXT NOT NULL DEFAULT ''")
    _add(con, "media_family_items", "completed_at", "INTEGER NOT NULL DEFAULT 0")
    _add(con, "paired_extensions", "protocol_version", "INTEGER NOT NULL DEFAULT 1")
    _add(con, "paired_extensions", "capabilities_json", "TEXT NOT NULL DEFAULT '{}'")
    con.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_family_items_image_order
          ON media_family_items(image_id,family_id,family_index);
        CREATE INDEX IF NOT EXISTS idx_family_anchor
          ON media_families(sort_anchor,family_id);
        CREATE INDEX IF NOT EXISTS idx_paired_extensions_token
          ON paired_extensions(token_hash,revoked_at);
        """
    )
