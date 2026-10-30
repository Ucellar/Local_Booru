"""Migration 033: automatic archive healing runtime.

Adds the durable state required by the first release that may physically swap a
canonical media revision, plus Companion pairing/task credentials, declarative
site profiles, protected browser session handoff, family completion metadata,
and optional local-model results.
"""
from __future__ import annotations

NAME = "automatic_archive_healing_runtime"


def _columns(con, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _add(con, table: str, name: str, declaration: str) -> None:
    if name not in _columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def is_applied(con) -> bool:
    tables = {
        str(r[0]) for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?,?,?)",
            (
                "browser_pairing_codes",
                "browser_task_tokens",
                "browser_site_profiles",
                "domain_session_credentials",
                "local_model_results",
                "image_text_regions",
            ),
        ).fetchall()
    }
    return (
        len(tables) == 6
        and "current_revision_id" in _columns(con, "images")
        and {"staging_path", "final_path", "verification_json", "swap_state"}.issubset(
            _columns(con, "media_revisions")
        )
        and {"media_url", "site_md5", "post_id", "metadata_json"}.issubset(
            _columns(con, "media_family_items")
        )
    )


def apply(con) -> None:
    _add(con, "images", "current_revision_id", "INTEGER NOT NULL DEFAULT 0")

    for name, decl in (
        ("staging_path", "TEXT NOT NULL DEFAULT ''"),
        ("final_path", "TEXT NOT NULL DEFAULT ''"),
        ("verification_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("swap_state", "TEXT NOT NULL DEFAULT 'planned'"),
        ("identity_confidence", "REAL NOT NULL DEFAULT 0"),
        ("quality_confidence", "REAL NOT NULL DEFAULT 0"),
        ("candidate_source_url", "TEXT NOT NULL DEFAULT ''"),
        ("old_delete_error", "TEXT NOT NULL DEFAULT ''"),
        ("recovered_at", "INTEGER NOT NULL DEFAULT 0"),
    ):
        _add(con, "media_revisions", name, decl)

    for name, decl in (
        ("sort_anchor", "INTEGER NOT NULL DEFAULT 0"),
        ("auto_complete", "INTEGER NOT NULL DEFAULT 1"),
        ("completion_status", "TEXT NOT NULL DEFAULT 'unknown'"),
    ):
        _add(con, "media_families", name, decl)

    for name, decl in (
        ("media_url", "TEXT NOT NULL DEFAULT ''"),
        ("site_md5", "TEXT NOT NULL DEFAULT ''"),
        ("post_id", "TEXT NOT NULL DEFAULT ''"),
        ("metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ):
        _add(con, "media_family_items", name, decl)

    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS browser_pairing_codes (
            code_hash TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL DEFAULT 0,
            expires_at INTEGER NOT NULL DEFAULT 0,
            consumed_at INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 8,
            display_name TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_browser_pairing_codes_expiry
            ON browser_pairing_codes(expires_at, consumed_at);

        CREATE TABLE IF NOT EXISTS browser_task_tokens (
            task_id TEXT PRIMARY KEY,
            extension_id TEXT NOT NULL DEFAULT '',
            lane TEXT NOT NULL DEFAULT '',
            token_hash TEXT NOT NULL DEFAULT '',
            issued_at INTEGER NOT NULL DEFAULT 0,
            expires_at INTEGER NOT NULL DEFAULT 0,
            consumed_at INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(extension_id) REFERENCES paired_extensions(extension_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_browser_task_tokens_expiry
            ON browser_task_tokens(expires_at, consumed_at);

        CREATE TABLE IF NOT EXISTS browser_site_profiles (
            site_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1,
            revision INTEGER NOT NULL DEFAULT 0,
            profile_json TEXT NOT NULL DEFAULT '{}',
            profile_hash TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            builtin INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS domain_session_credentials (
            domain TEXT PRIMARY KEY,
            protected_blob TEXT NOT NULL DEFAULT '',
            user_agent TEXT NOT NULL DEFAULT '',
            cookie_count INTEGER NOT NULL DEFAULT 0,
            received_at INTEGER NOT NULL DEFAULT 0,
            last_verified_at INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            revoked_at INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS local_model_results (
            image_id INTEGER NOT NULL,
            model_kind TEXT NOT NULL,
            model_name TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}',
            confidence REAL NOT NULL DEFAULT 0,
            checked_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(image_id, model_kind),
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_local_model_results_kind
            ON local_model_results(model_kind, checked_at);

        CREATE TABLE IF NOT EXISTS image_text_regions (
            region_id INTEGER PRIMARY KEY,
            image_id INTEGER NOT NULL,
            model_name TEXT NOT NULL DEFAULT '',
            x_normalized REAL NOT NULL DEFAULT 0,
            y_normalized REAL NOT NULL DEFAULT 0,
            width_normalized REAL NOT NULL DEFAULT 0,
            height_normalized REAL NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_image_text_regions_image
            ON image_text_regions(image_id, confidence DESC);
        """
    )

    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_revisions_swap_state "
        "ON media_revisions(status, swap_state, created_at)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_family_items_completion "
        "ON media_family_items(family_id, is_missing, family_index)"
    )
