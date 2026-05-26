SCHEMA_VERSION = 5


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
        deleted INTEGER NOT NULL DEFAULT 0
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
    CREATE INDEX IF NOT EXISTS idx_delete_log_path ON delete_log(path);
    CREATE INDEX IF NOT EXISTS idx_task_log_status ON task_log(status, task_type);
    """)
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.commit()


def _ensure_column(con, table, column, ddl):
    try:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except Exception:
        pass
