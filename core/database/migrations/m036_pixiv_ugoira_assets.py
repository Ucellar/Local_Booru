"""Migration 036: durable Pixiv ugoira source assets and conversion state."""
from __future__ import annotations

NAME = "pixiv_ugoira_assets"


def is_applied(con) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pixiv_ugoira_assets'").fetchone()
    return bool(row)


def apply(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS pixiv_ugoira_assets (
            asset_id INTEGER PRIMARY KEY,
            image_id INTEGER,
            illust_id TEXT NOT NULL,
            zip_path TEXT NOT NULL DEFAULT '',
            converted_path TEXT NOT NULL DEFAULT '',
            original_url TEXT NOT NULL DEFAULT '',
            frames_json TEXT NOT NULL DEFAULT '[]',
            conversion_format TEXT NOT NULL DEFAULT '',
            conversion_status TEXT NOT NULL DEFAULT 'pending',
            last_error TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0,
            UNIQUE(illust_id),
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pixiv_ugoira_image
          ON pixiv_ugoira_assets(image_id,conversion_status);
        """
    )
