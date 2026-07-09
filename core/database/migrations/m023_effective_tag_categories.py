NAME = "effective_tag_category_materialization"


def _has_table(con, name):
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
    ).fetchone()
    return bool(row)


def apply(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS image_effective_tag_category (
        image_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL,
        category TEXT NOT NULL DEFAULT 'general',
        updated_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(image_id, tag_id),
        FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE,
        FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_effective_tag_category_cat_tag_image
        ON image_effective_tag_category(category, tag_id, image_id);
    CREATE INDEX IF NOT EXISTS idx_effective_tag_category_tag_image
        ON image_effective_tag_category(tag_id, image_id);
    """)
    if not _has_table(con, "images") or not _has_table(con, "tags"):
        return
    # One-time rebuild.  This makes tag_group_counts able to use a compact
    # materialized table instead of the old window/CTE over image_source_tags.
    con.execute("DELETE FROM image_effective_tag_category")
    if _has_table(con, "image_source_tags"):
        con.executescript("""
        INSERT OR REPLACE INTO image_effective_tag_category(image_id, tag_id, category, updated_at)
        WITH ranked AS (
            SELECT ist.image_id, ist.tag_id,
                   LOWER(COALESCE(NULLIF(ist.category, ''), 'general')) AS category,
                   ROW_NUMBER() OVER (
                       PARTITION BY ist.image_id, ist.tag_id
                       ORDER BY CASE LOWER(COALESCE(NULLIF(ist.category, ''), 'general'))
                           WHEN 'artist' THEN 0 WHEN 'contributor' THEN 1 WHEN 'character' THEN 2
                           WHEN 'copyright' THEN 3 WHEN 'species' THEN 4 WHEN 'meta' THEN 5
                           WHEN 'lore' THEN 6 WHEN 'invalid' THEN 7 WHEN 'parody' THEN 8
                           WHEN 'language' THEN 9 WHEN 'category' THEN 10 WHEN 'pages' THEN 11
                           WHEN 'general' THEN 99 ELSE 50 END,
                           ist.source_id
                   ) AS rn
            FROM image_source_tags ist
        )
        SELECT image_id, tag_id, category, CAST(strftime('%s','now') AS INTEGER)
        FROM ranked WHERE rn=1;
        """)
    # Legacy images without source provenance still need visible categories.
    if _has_table(con, "image_tags"):
        con.executescript("""
        INSERT OR IGNORE INTO image_effective_tag_category(image_id, tag_id, category, updated_at)
        SELECT it.image_id, it.tag_id, LOWER(COALESCE(NULLIF(t.category, ''), 'general')),
               CAST(strftime('%s','now') AS INTEGER)
        FROM image_tags it
        JOIN tags t ON t.id=it.tag_id;
        """)
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('effective_tag_category_complete','1')")
