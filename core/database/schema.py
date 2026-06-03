from .migrations import CURRENT_SCHEMA_VERSION as SCHEMA_VERSION, run_migrations


def init_db(con, force=False):
    """Create or migrate the SQLite schema.

    SQLite is the source of truth in the new architecture. Sidecar files are no
    longer required for normal operation.
    """
    con.executescript("""
    CREATE TABLE IF NOT EXISTS images (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL UNIQUE,
        file_name TEXT NOT NULL,
        bucket TEXT NOT NULL DEFAULT '',
        size_bytes INTEGER NOT NULL DEFAULT 0,
        width INTEGER NOT NULL DEFAULT 0,
        height INTEGER NOT NULL DEFAULT 0,
        hash_md5 TEXT,
        hash_phash TEXT,
        rating      INTEGER DEFAULT 0,
        mtime_ns INTEGER NOT NULL DEFAULT 0,
        is_video INTEGER NOT NULL DEFAULT 0,
        indexed_at INTEGER NOT NULL DEFAULT 0,
        deleted INTEGER NOT NULL DEFAULT 0,
        lifecycle TEXT NOT NULL DEFAULT 'archive',
        inbox_until INTEGER NOT NULL DEFAULT 0,
        original_media_path TEXT NOT NULL DEFAULT '',
        trashed_at INTEGER NOT NULL DEFAULT 0,
        favorite INTEGER NOT NULL DEFAULT 0,
        last_viewed_at INTEGER NOT NULL DEFAULT 0,
        import_origin TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL UNIQUE,
        category TEXT NOT NULL DEFAULT 'general'
    );

    CREATE TABLE IF NOT EXISTS image_tags (
        image_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL,
        PRIMARY KEY (image_id, tag_id),
        FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE,
        FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY,
        host TEXT NOT NULL,
        url TEXT NOT NULL,
        UNIQUE(host, url)
    );

    CREATE TABLE IF NOT EXISTS image_sources (
        image_id INTEGER NOT NULL,
        source_id INTEGER NOT NULL,
        PRIMARY KEY (image_id, source_id),
        FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE,
        FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS processed_files (
        id INTEGER PRIMARY KEY,
        original_path TEXT,
        original_name TEXT NOT NULL,
        media_path TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT '',
        bucket TEXT NOT NULL DEFAULT '',
        processed_at INTEGER NOT NULL DEFAULT 0
    );

    -- Per-site exact lookup journal. A file is not globally "done" for newly
    -- enabled sites: every source independently records whether it was checked.
    CREATE TABLE IF NOT EXISTS site_scan_status (
        original_path TEXT NOT NULL,
        site_key TEXT NOT NULL,
        engine TEXT NOT NULL DEFAULT '',
        scan_revision INTEGER NOT NULL DEFAULT 1,
        outcome TEXT NOT NULL DEFAULT '',
        checked_md5 TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '',
        checked_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(original_path, site_key, scan_revision)
    );

    -- Reverse-search requests intentionally delayed by an API quota/cooldown.
    -- A deferred SauceNAO check is not a NO_MATCH result and must survive restart.
    CREATE TABLE IF NOT EXISTS reverse_retry_queue (
        original_path TEXT NOT NULL,
        service TEXT NOT NULL DEFAULT 'saucenao',
        retry_after INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT '',
        queued_at INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(original_path, service)
    );

    -- Durable low-priority metadata enrichment. Exact-search lanes must only
    -- collect tags/sources; flat-tag category recovery runs in the background.
    CREATE TABLE IF NOT EXISTS tag_enrichment_queue (
        original_path TEXT NOT NULL,
        job_key TEXT NOT NULL,
        media_path TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        retry_after INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        queued_at INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(original_path, job_key, source_url)
    );

    CREATE TABLE IF NOT EXISTS raw_metadata (
        image_id INTEGER PRIMARY KEY,
        site TEXT,
        post_url TEXT,
        file_url TEXT,
        raw_json TEXT,
        updated_at INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS delete_log (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL,
        file_name TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        tag_or_source TEXT NOT NULL DEFAULT '',
        deleted_at INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS task_log (
        id INTEGER PRIMARY KEY,
        task_type TEXT NOT NULL,
        status TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS app_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    -- Content-level re-import rules. Only deliberate user deletion creates an
    -- active block; automatic dedupe/maintenance rows remain audit history.
    CREATE TABLE IF NOT EXISTS deleted_media_rules (
        md5 TEXT PRIMARY KEY,
        active INTEGER NOT NULL DEFAULT 1,
        manual_delete INTEGER NOT NULL DEFAULT 1,
        reason TEXT NOT NULL DEFAULT '',
        path TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL DEFAULT '',
        size_bytes INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL DEFAULT 0
    );

    -- Service-level persistent runtime state. The retry queue and its cooldown
    -- now live in the same transactional SQLite store.
    CREATE TABLE IF NOT EXISTS service_state (
        service TEXT PRIMARY KEY,
        cooldown_until INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL DEFAULT 0
    );

    -- NO_MATCH is library state, not a sidecar marker/cache file.
    CREATE TABLE IF NOT EXISTS no_match_items (
        original_path TEXT PRIMARY KEY,
        media_path TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT 'no_match',
        manual_url TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1
    );


    -- Operation journal: crash/recovery trace for multi-step library operations.
    CREATE TABLE IF NOT EXISTS operation_journal (
        op_id TEXT PRIMARY KEY,
        op_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running',
        target_type TEXT NOT NULL DEFAULT '',
        target_id TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL DEFAULT '{}',
        error TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL DEFAULT 0,
        finished_at INTEGER NOT NULL DEFAULT 0
    );

    -- Integrity checker output. Kept in DB so the UI can show what was found/repaired.
    CREATE TABLE IF NOT EXISTS integrity_issues (
        id INTEGER PRIMARY KEY,
        issue_type TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'warning',
        image_id INTEGER DEFAULT 0,
        path TEXT NOT NULL DEFAULT '',
        details TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        created_at INTEGER NOT NULL DEFAULT 0,
        repaired_at INTEGER NOT NULL DEFAULT 0
    );

    -- Duplicate relation graph. dup_groups is the fast component table; this stores why files are related.
    CREATE TABLE IF NOT EXISTS duplicate_relations (
        id INTEGER PRIMARY KEY,
        image_a INTEGER NOT NULL,
        image_b INTEGER NOT NULL,
        relation TEXT NOT NULL DEFAULT 'potential_duplicate',
        distance INTEGER DEFAULT 0,
        confidence REAL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'unreviewed',
        created_at INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL DEFAULT 0,
        UNIQUE(image_a, image_b, relation)
    );

    -- URLs seen by downloader/subscriptions; prevents needless re-fetches and makes retries explainable.
    CREATE TABLE IF NOT EXISTS url_history (
        url TEXT PRIMARY KEY,
        host TEXT NOT NULL DEFAULT '',
        image_id INTEGER DEFAULT 0,
        status TEXT NOT NULL DEFAULT '',
        last_error TEXT NOT NULL DEFAULT '',
        first_seen INTEGER NOT NULL DEFAULT 0,
        last_seen INTEGER NOT NULL DEFAULT 0
    );

    -- A dismissed visual-pair applies only to the exact MD5 pair, never to future unknown files.
    CREATE TABLE IF NOT EXISTS ignored_duplicate_pairs (
        md5_a TEXT NOT NULL,
        md5_b TEXT NOT NULL,
        relation TEXT NOT NULL DEFAULT 'not_duplicate',
        created_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(md5_a, md5_b)
    );

    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)

    # Conservative migrations for older databases.
    _ensure_column(con, "images", "deleted",   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(con, "images", "rating",    "INTEGER DEFAULT 0")
    _ensure_column(con, "images", "duration",  "REAL DEFAULT 0")
    _ensure_column(con, "images", "file_name", "TEXT DEFAULT ''")
    _ensure_column(con, "images", "lifecycle", "TEXT NOT NULL DEFAULT 'archive'")
    _ensure_column(con, "images", "inbox_until", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(con, "images", "original_media_path", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(con, "images", "trashed_at", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(con, "images", "favorite", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(con, "images", "last_viewed_at", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(con, "images", "import_origin", "TEXT NOT NULL DEFAULT ''")

    con.executescript("""
    CREATE INDEX IF NOT EXISTS idx_images_bucket ON images(bucket);
    CREATE INDEX IF NOT EXISTS idx_images_mtime ON images(mtime_ns);
    CREATE INDEX IF NOT EXISTS idx_images_md5 ON images(hash_md5);
    CREATE INDEX IF NOT EXISTS idx_images_phash ON images(hash_phash);

    -- VP-tree for fast phash similarity search (Hydrus-inspired)
    -- Stores the tree structure for O(log n) nearest-neighbor search
    CREATE TABLE IF NOT EXISTS vp_tree (
        node_id     INTEGER PRIMARY KEY,
        image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
        phash       TEXT NOT NULL,
        radius      REAL,           -- split radius at this node
        inner_id    INTEGER,        -- child node where dist <= radius
        outer_id    INTEGER,        -- child node where dist > radius
        inner_pop   INTEGER DEFAULT 0,
        outer_pop   INTEGER DEFAULT 0,
        parent_id   INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_vp_parent ON vp_tree(parent_id);
    CREATE INDEX IF NOT EXISTS idx_vp_image  ON vp_tree(image_id);

    -- Duplicate groups: connected components of similar files
    CREATE TABLE IF NOT EXISTS dup_groups (
        group_id    INTEGER NOT NULL,
        image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
        similarity  REAL    DEFAULT 100.0,
        PRIMARY KEY (group_id, image_id)
    );
    CREATE INDEX IF NOT EXISTS idx_dup_image ON dup_groups(image_id);

    -- Tag siblings: canonical tag mapping
    CREATE TABLE IF NOT EXISTS tag_siblings (
        tag         TEXT NOT NULL,
        canonical   TEXT NOT NULL,
        PRIMARY KEY (tag)
    );
    CREATE INDEX IF NOT EXISTS idx_sib_canonical ON tag_siblings(canonical);
    CREATE INDEX IF NOT EXISTS idx_images_deleted_bucket ON images(deleted, bucket);
    CREATE INDEX IF NOT EXISTS idx_images_lifecycle ON images(deleted, lifecycle, inbox_until);
    CREATE INDEX IF NOT EXISTS idx_images_favorite ON images(deleted, favorite);
    CREATE INDEX IF NOT EXISTS idx_images_file_name ON images(file_name COLLATE NOCASE);
    CREATE INDEX IF NOT EXISTS idx_tags_norm ON tags(normalized_name);
    CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name COLLATE NOCASE);
    CREATE INDEX IF NOT EXISTS idx_tags_category ON tags(category, name COLLATE NOCASE);
    CREATE INDEX IF NOT EXISTS idx_image_tags_tag ON image_tags(tag_id, image_id);
    CREATE INDEX IF NOT EXISTS idx_image_tags_image ON image_tags(image_id, tag_id);
    CREATE INDEX IF NOT EXISTS idx_sources_host ON sources(host);
    CREATE INDEX IF NOT EXISTS idx_sources_url ON sources(url COLLATE NOCASE);
    CREATE INDEX IF NOT EXISTS idx_image_sources_source ON image_sources(source_id, image_id);
    CREATE INDEX IF NOT EXISTS idx_processed_original_path ON processed_files(original_path);
    CREATE INDEX IF NOT EXISTS idx_processed_original_name ON processed_files(original_name);
    CREATE INDEX IF NOT EXISTS idx_processed_status ON processed_files(status);
    CREATE INDEX IF NOT EXISTS idx_site_scan_path ON site_scan_status(original_path, scan_revision);
    CREATE INDEX IF NOT EXISTS idx_site_scan_site ON site_scan_status(site_key, scan_revision, outcome);
    CREATE INDEX IF NOT EXISTS idx_reverse_retry_due ON reverse_retry_queue(service, retry_after);
    CREATE INDEX IF NOT EXISTS idx_tag_enrichment_pending ON tag_enrichment_queue(status, retry_after, job_key);
    CREATE INDEX IF NOT EXISTS idx_deleted_rules_active ON deleted_media_rules(active, manual_delete);
    CREATE INDEX IF NOT EXISTS idx_service_state_cooldown ON service_state(cooldown_until);
    CREATE INDEX IF NOT EXISTS idx_no_match_active ON no_match_items(active, updated_at);
    CREATE INDEX IF NOT EXISTS idx_delete_log_path ON delete_log(path);
    CREATE INDEX IF NOT EXISTS idx_task_log_status ON task_log(status, task_type);
    CREATE INDEX IF NOT EXISTS idx_operation_journal_status ON operation_journal(status, op_type);
    CREATE INDEX IF NOT EXISTS idx_operation_journal_target ON operation_journal(target_type, target_id);
    CREATE INDEX IF NOT EXISTS idx_integrity_issues_status ON integrity_issues(status, issue_type);
    CREATE INDEX IF NOT EXISTS idx_duplicate_relations_status ON duplicate_relations(status, relation);
    CREATE INDEX IF NOT EXISTS idx_duplicate_relations_a ON duplicate_relations(image_a);
    CREATE INDEX IF NOT EXISTS idx_duplicate_relations_b ON duplicate_relations(image_b);
    CREATE INDEX IF NOT EXISTS idx_url_history_status ON url_history(status, last_seen);
    """)
    run_migrations(con)
    con.commit()


def _ensure_column(con, table, column, ddl):
    try:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except Exception:
        pass
