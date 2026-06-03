"""Migration 014: persistent DB maintenance and integrity history."""
from __future__ import annotations

VERSION = 14
NAME = "database_health_and_maintenance_history"


def apply(con) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS database_health_events (
        id INTEGER PRIMARY KEY,
        check_type TEXT NOT NULL DEFAULT 'quick_check',
        status TEXT NOT NULL DEFAULT '',
        details TEXT NOT NULL DEFAULT '',
        db_size_bytes INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS maintenance_history (
        id INTEGER PRIMARY KEY,
        operation TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT '',
        before_bytes INTEGER NOT NULL DEFAULT 0,
        after_bytes INTEGER NOT NULL DEFAULT 0,
        reclaimed_bytes INTEGER NOT NULL DEFAULT 0,
        backup_path TEXT NOT NULL DEFAULT '',
        details TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_database_health_created
        ON database_health_events(created_at DESC, status);
    CREATE INDEX IF NOT EXISTS idx_maintenance_history_created
        ON maintenance_history(created_at DESC, operation);
    """)
