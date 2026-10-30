from __future__ import annotations

NAME = "browser_site_md5_aliases"


def is_applied(con) -> bool:
    """Verify the v25 table and both supporting indexes actually exist."""
    required = {
        ("table", "image_site_md5_aliases"),
        ("index", "idx_image_site_md5_aliases_image"),
        ("index", "idx_image_site_md5_aliases_site"),
    }
    rows = con.execute(
        "SELECT type,name FROM sqlite_master WHERE name IN (?,?,?)",
        tuple(name for _kind, name in required),
    ).fetchall()
    present = {(str(row[0]), str(row[1])) for row in rows}
    return required.issubset(present)


def apply(con) -> None:
    """Bind authoritative remote/site MD5 values to one local canonical image.

    A browser import may download bytes whose local MD5 differs from the MD5
    published by the booru post (proxy/transcode/sample/original differences).
    The local byte hash remains the canonical ``images.hash_md5``.  This table
    records that the site's authoritative hash belongs to that local image so
    future extension checks and imports can deduplicate before parsing.
    """
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS image_site_md5_aliases (
            site_md5 TEXT PRIMARY KEY,
            image_id INTEGER NOT NULL,
            site TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            local_md5 TEXT NOT NULL DEFAULT '',
            observed_at INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_image_site_md5_aliases_image
            ON image_site_md5_aliases(image_id);
        CREATE INDEX IF NOT EXISTS idx_image_site_md5_aliases_site
            ON image_site_md5_aliases(site, site_md5);
        """
    )
