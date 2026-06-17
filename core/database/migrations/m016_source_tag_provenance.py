"""Migration 016: store tag sets and per-site categories separately per confirmed source."""
from __future__ import annotations

VERSION = 16
NAME = "source_tag_provenance"


def apply(con) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS image_source_tags (
        image_id INTEGER NOT NULL,
        source_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL,
        category TEXT NOT NULL DEFAULT 'general',
        acquisition TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (image_id, source_id, tag_id),
        FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE,
        FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE,
        FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_image_source_tags_image ON image_source_tags(image_id, source_id, tag_id);
    CREATE INDEX IF NOT EXISTS idx_image_source_tags_source ON image_source_tags(source_id, tag_id, image_id);
    CREATE INDEX IF NOT EXISTS idx_image_source_tags_tag ON image_source_tags(tag_id, image_id);
    """)
