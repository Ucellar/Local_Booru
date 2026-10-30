"""Migration 032: archive-enrichment foundation.

This migration is deliberately schema/invariant only.  Automatic physical media
replacement is a later stage; v32 provides durable records for revisions,
families, typed sources, source-scoped tag relations, notes, download intents,
paired companions, and reusable background lanes.
"""
from __future__ import annotations

NAME = "archive_enrichment_foundation"

_REQUIRED_TABLES = {
    "media_revisions",
    "retired_media_tombstones",
    "media_families",
    "media_family_items",
    "image_source_roles",
    "tag_relations",
    "tag_relation_sync_state",
    "image_notes",
    "image_note_revisions",
    "background_lane_jobs",
    "download_intents",
    "paired_extensions",
}


def _columns(con, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def is_applied(con) -> bool:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (%s)"
        % ",".join("?" for _ in _REQUIRED_TABLES),
        tuple(sorted(_REQUIRED_TABLES)),
    ).fetchall()
    present = {str(row[0]) for row in rows}
    alias_cols = _columns(con, "image_site_md5_aliases")
    relation_cols = _columns(con, "tag_relations")
    sync_cols = _columns(con, "tag_relation_sync_state")
    return (
        _REQUIRED_TABLES.issubset(present)
        and {"revision_id", "rebound_at"}.issubset(alias_cols)
        and "sync_generation" in relation_cols
        and "generation" in sync_cols
    )


def apply(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS media_revisions (
            revision_id INTEGER PRIMARY KEY,
            image_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',
            old_md5 TEXT NOT NULL DEFAULT '',
            old_path TEXT NOT NULL DEFAULT '',
            old_width INTEGER NOT NULL DEFAULT 0,
            old_height INTEGER NOT NULL DEFAULT 0,
            old_file_size INTEGER NOT NULL DEFAULT 0,
            old_format TEXT NOT NULL DEFAULT '',
            new_md5 TEXT NOT NULL DEFAULT '',
            new_path TEXT NOT NULL DEFAULT '',
            new_width INTEGER NOT NULL DEFAULT 0,
            new_height INTEGER NOT NULL DEFAULT 0,
            new_file_size INTEGER NOT NULL DEFAULT 0,
            new_format TEXT NOT NULL DEFAULT '',
            replacement_reason TEXT NOT NULL DEFAULT 'quality_upgrade',
            identity_basis TEXT NOT NULL DEFAULT '',
            quality_basis TEXT NOT NULL DEFAULT '',
            source_site TEXT NOT NULL DEFAULT '',
            source_post_id TEXT NOT NULL DEFAULT '',
            journal_id TEXT NOT NULL DEFAULT '',
            old_file_deleted INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT 0,
            verified_at INTEGER NOT NULL DEFAULT 0,
            committed_at INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_media_revisions_image
            ON media_revisions(image_id, revision_id DESC);
        CREATE INDEX IF NOT EXISTS idx_media_revisions_status
            ON media_revisions(status, created_at);

        CREATE TABLE IF NOT EXISTS retired_media_tombstones (
            tombstone_id INTEGER PRIMARY KEY,
            image_id INTEGER NOT NULL,
            revision_id INTEGER NOT NULL DEFAULT 0,
            old_path TEXT NOT NULL DEFAULT '',
            old_md5 TEXT NOT NULL DEFAULT '',
            replaced_by_md5 TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT 'quality_upgrade',
            retired_at INTEGER NOT NULL DEFAULT 0,
            physical_deleted INTEGER NOT NULL DEFAULT 0,
            UNIQUE(old_path, old_md5),
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE,
            FOREIGN KEY(revision_id) REFERENCES media_revisions(revision_id) ON DELETE SET DEFAULT
        );
        CREATE INDEX IF NOT EXISTS idx_retired_media_md5
            ON retired_media_tombstones(old_md5);
        CREATE INDEX IF NOT EXISTS idx_retired_media_image
            ON retired_media_tombstones(image_id, retired_at DESC);

        CREATE TABLE IF NOT EXISTS media_families (
            family_id INTEGER PRIMARY KEY,
            family_type TEXT NOT NULL DEFAULT '',
            family_source TEXT NOT NULL DEFAULT '',
            external_family_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL DEFAULT '',
            total_items INTEGER NOT NULL DEFAULT 0,
            order_confidence REAL NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0,
            UNIQUE(family_source, family_type, external_family_id)
        );
        CREATE INDEX IF NOT EXISTS idx_media_families_source
            ON media_families(family_source, family_type, external_family_id);

        CREATE TABLE IF NOT EXISTS media_family_items (
            family_id INTEGER NOT NULL,
            family_index INTEGER NOT NULL,
            image_id INTEGER,
            external_item_id TEXT NOT NULL DEFAULT '',
            page_label TEXT NOT NULL DEFAULT '',
            relation_type TEXT NOT NULL DEFAULT 'page',
            is_missing INTEGER NOT NULL DEFAULT 0,
            source_url TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(family_id, family_index),
            UNIQUE(family_id, image_id),
            FOREIGN KEY(family_id) REFERENCES media_families(family_id) ON DELETE CASCADE,
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_media_family_items_image
            ON media_family_items(image_id, family_id);

        CREATE TABLE IF NOT EXISTS image_source_roles (
            image_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'mirror_post',
            is_preferred INTEGER NOT NULL DEFAULT 0,
            provenance TEXT NOT NULL DEFAULT '',
            observed_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(image_id, source_id, role),
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE,
            FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_image_source_roles_role
            ON image_source_roles(image_id, role, is_preferred DESC);

        CREATE TABLE IF NOT EXISTS tag_relations (
            relation_id INTEGER PRIMARY KEY,
            source_site TEXT NOT NULL,
            relation_type TEXT NOT NULL DEFAULT 'alias',
            source_tag TEXT NOT NULL,
            target_tag TEXT NOT NULL,
            source_record_id TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            fetched_at INTEGER NOT NULL DEFAULT 0,
            valid_from INTEGER NOT NULL DEFAULT 0,
            valid_to INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 1.0,
            sync_generation INTEGER NOT NULL DEFAULT 0,
            UNIQUE(source_site, relation_type, source_tag, target_tag)
        );
        CREATE INDEX IF NOT EXISTS idx_tag_relations_lookup
            ON tag_relations(source_site, relation_type, source_tag, status);
        CREATE INDEX IF NOT EXISTS idx_tag_relations_target
            ON tag_relations(source_site, target_tag, status);

        CREATE TABLE IF NOT EXISTS tag_relation_sync_state (
            source_site TEXT PRIMARY KEY,
            source_url TEXT NOT NULL DEFAULT '',
            cursor TEXT NOT NULL DEFAULT '',
            last_page INTEGER NOT NULL DEFAULT 0,
            last_success_at INTEGER NOT NULL DEFAULT 0,
            retry_after INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'idle',
            last_error TEXT NOT NULL DEFAULT '',
            rows_seen INTEGER NOT NULL DEFAULT 0,
            rows_written INTEGER NOT NULL DEFAULT 0,
            generation INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS image_notes (
            note_id INTEGER PRIMARY KEY,
            image_id INTEGER NOT NULL,
            source_site TEXT NOT NULL DEFAULT '',
            source_post_id TEXT NOT NULL DEFAULT '',
            external_note_id TEXT NOT NULL DEFAULT '',
            x REAL NOT NULL DEFAULT 0,
            y REAL NOT NULL DEFAULT 0,
            width REAL NOT NULL DEFAULT 0,
            height REAL NOT NULL DEFAULT 0,
            x_normalized REAL NOT NULL DEFAULT 0,
            y_normalized REAL NOT NULL DEFAULT 0,
            width_normalized REAL NOT NULL DEFAULT 0,
            height_normalized REAL NOT NULL DEFAULT 0,
            body TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            fetched_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0,
            UNIQUE(image_id, source_site, external_note_id),
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_image_notes_image
            ON image_notes(image_id, is_active, source_site);

        CREATE TABLE IF NOT EXISTS image_note_revisions (
            note_revision_id INTEGER PRIMARY KEY,
            note_id INTEGER NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            x REAL NOT NULL DEFAULT 0,
            y REAL NOT NULL DEFAULT 0,
            width REAL NOT NULL DEFAULT 0,
            height REAL NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            recorded_at INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(note_id) REFERENCES image_notes(note_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_image_note_revisions_note
            ON image_note_revisions(note_id, recorded_at DESC);

        CREATE TABLE IF NOT EXISTS background_lane_jobs (
            job_id INTEGER PRIMARY KEY,
            lane TEXT NOT NULL,
            identity_key TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 0,
            retry_after INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            queued_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0,
            owner TEXT NOT NULL DEFAULT '',
            generation INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            dedupe_key TEXT NOT NULL DEFAULT '',
            UNIQUE(lane, identity_key)
        );
        CREATE INDEX IF NOT EXISTS idx_background_lane_due
            ON background_lane_jobs(lane, state, retry_after, priority DESC, queued_at, job_id);
        CREATE INDEX IF NOT EXISTS idx_background_lane_owner
            ON background_lane_jobs(lane, owner, generation, state);

        CREATE TABLE IF NOT EXISTS download_intents (
            intent_id INTEGER PRIMARY KEY,
            operation TEXT NOT NULL,
            source_site TEXT NOT NULL DEFAULT '',
            media_url TEXT NOT NULL DEFAULT '',
            post_url TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            state TEXT NOT NULL DEFAULT 'blocked_session_policy',
            reason TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0,
            UNIQUE(operation, source_site, media_url)
        );
        CREATE INDEX IF NOT EXISTS idx_download_intents_state
            ON download_intents(state, created_at);

        CREATE TABLE IF NOT EXISTS paired_extensions (
            extension_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            token_hash TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT 0,
            last_seen_at INTEGER NOT NULL DEFAULT 0,
            revoked_at INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )

    alias_columns = _columns(con, "image_site_md5_aliases")
    if "revision_id" not in alias_columns:
        con.execute("ALTER TABLE image_site_md5_aliases ADD COLUMN revision_id INTEGER NOT NULL DEFAULT 0")
    if "rebound_at" not in alias_columns:
        con.execute("ALTER TABLE image_site_md5_aliases ADD COLUMN rebound_at INTEGER NOT NULL DEFAULT 0")
    relation_columns = _columns(con, "tag_relations")
    if "sync_generation" not in relation_columns:
        con.execute("ALTER TABLE tag_relations ADD COLUMN sync_generation INTEGER NOT NULL DEFAULT 0")
    sync_columns = _columns(con, "tag_relation_sync_state")
    if "generation" not in sync_columns:
        con.execute("ALTER TABLE tag_relation_sync_state ADD COLUMN generation INTEGER NOT NULL DEFAULT 0")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_image_site_md5_aliases_revision "
        "ON image_site_md5_aliases(image_id, revision_id)"
    )
    for site, url in (
        ("rule34.xxx", "https://rule34.xxx/index.php?page=alias&s=list&pid=0"),
        ("gelbooru.com", "https://gelbooru.com/index.php?page=alias&s=list"),
        ("danbooru.donmai.us", "https://danbooru.donmai.us/tag_aliases"),
        ("e621.net", "https://e621.net/tag_aliases"),
        ("booru.allthefallen.moe", "https://booru.allthefallen.moe/tag_aliases"),
    ):
        con.execute(
            "INSERT OR IGNORE INTO tag_relation_sync_state(source_site,source_url,status) VALUES(?,?,'idle')",
            (site, url),
        )
