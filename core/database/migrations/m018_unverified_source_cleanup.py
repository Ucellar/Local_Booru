"""Migration 018: discard unverified reverse-source links with no site metadata."""
from __future__ import annotations

VERSION = 18
NAME = "unverified_reverse_source_cleanup"


def _has_table(con, table: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone())


def apply(con) -> None:
    # Remove any navigation URL that survived through raw metadata rather than
    # the normal sources table.  It is never a concrete post reference.
    if _has_table(con, "raw_metadata"):
        con.execute("""
            UPDATE raw_metadata SET post_url=''
            WHERE LOWER(COALESCE(post_url,'')) LIKE '%page=post%s=list%'
               OR LOWER(COALESCE(post_url,'')) LIKE '%/posts/random%'
               OR LOWER(COALESCE(post_url,'')) LIKE '%/post/random%'
        """)

    if not (_has_table(con, "sources") and _has_table(con, "image_sources") and _has_table(con, "image_source_tags")):
        return

    # v145 could preserve a selected Gelbooru IQDB URL even when Gelbooru
    # returned no post/tags; when another source (for example e621) succeeded,
    # this became a dead button in the post view.  A confirmed source-specific
    # tag set is the evidence required for an automatic Gelbooru link.
    con.execute("""
        DELETE FROM image_sources
        WHERE source_id IN (
            SELECT s.id FROM sources s
            WHERE LOWER(REPLACE(s.host,'www.',''))='gelbooru.com'
        )
          AND NOT EXISTS (
            SELECT 1 FROM image_source_tags ist
            WHERE ist.image_id=image_sources.image_id AND ist.source_id=image_sources.source_id
          )
          AND EXISTS (
            SELECT 1 FROM image_source_tags any_ist
            WHERE any_ist.image_id=image_sources.image_id
          )
    """)
    con.execute("DELETE FROM sources WHERE id NOT IN (SELECT DISTINCT source_id FROM image_sources)")
