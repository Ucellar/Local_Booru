"""Migration 017: remove saved gallery/search URLs mistakenly recorded as post sources."""
from __future__ import annotations

VERSION = 17
NAME = "invalid_navigation_sources_cleanup"


def _has_table(con, table: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone())


def apply(con) -> None:
    # Some migration tests and partially recovered v13 databases intentionally
    # contain only the minimum baseline table. Cleanup is unnecessary until the
    # source tables exist.
    if not _has_table(con, "sources") or not _has_table(con, "image_sources"):
        return

    con.executescript("""
    CREATE TEMP TABLE IF NOT EXISTS _invalid_navigation_source_ids(id INTEGER PRIMARY KEY);
    DELETE FROM _invalid_navigation_source_ids;
    INSERT OR IGNORE INTO _invalid_navigation_source_ids(id)
    SELECT id FROM sources
     WHERE LOWER(url) LIKE '%page=post%s=list%'
        OR LOWER(url) LIKE '%/posts/random%'
        OR LOWER(url) LIKE '%/post/random%';
    """)
    if _has_table(con, "image_source_tags"):
        con.execute("DELETE FROM image_source_tags WHERE source_id IN (SELECT id FROM _invalid_navigation_source_ids)")
    con.execute("DELETE FROM image_sources WHERE source_id IN (SELECT id FROM _invalid_navigation_source_ids)")
    con.execute("DELETE FROM sources WHERE id IN (SELECT id FROM _invalid_navigation_source_ids)")
    con.execute("DROP TABLE _invalid_navigation_source_ids")
