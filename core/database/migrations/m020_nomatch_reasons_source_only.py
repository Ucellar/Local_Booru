from __future__ import annotations

NAME = "nomatch_reasons_source_only"


def _ensure_column(con, table: str, column: str, ddl: str) -> None:
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def apply(con) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS no_match_items (
        original_path TEXT PRIMARY KEY,
        media_path TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT 'no_match',
        manual_url TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1
    );
    """)
    # no_match_items used to be a flat yes/no bucket.  v157 keeps the old rows
    # valid, but records why an item is in triage and whether SauceNAO found a
    # useful unsupported/source-only hint.
    _ensure_column(con, "no_match_items", "source_url", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(con, "no_match_items", "source_label", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(con, "no_match_items", "source_host", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(con, "no_match_items", "source_similarity", "REAL NOT NULL DEFAULT 0")
    _ensure_column(con, "no_match_items", "last_error", "TEXT NOT NULL DEFAULT ''")
    con.executescript("""
    CREATE INDEX IF NOT EXISTS idx_no_match_reason_active
      ON no_match_items(active, reason, updated_at);
    CREATE INDEX IF NOT EXISTS idx_no_match_source_host
      ON no_match_items(active, source_host, updated_at);
    """)
