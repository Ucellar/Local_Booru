"""Migration 040: tag search ignores letter case; spoiled tag categories are forgotten.

* ``idx_tags_norm_nocase`` lets the gallery search find "Genshin_Impact" when "genshin_impact" is
  typed (and the other way round) without reading the whole tag table for every search.
* Before 5.0.3 the grabber stored "general" for every tag whose category lookup failed (an error,
  a limit, no API key) and the parser trusted that cache, so artists and characters came out as
  general tags.  Such rows cannot be told from real "general" answers, so all grabber "general"
  rows are dropped once: the worst case is one more category lookup for a really general tag.
"""
from __future__ import annotations

NAME = "tag_search_and_category_cache"
_DONE_KEY = "grabber_general_category_cache_dropped"
_GRABBER_METHOD = "grabber_dapi_tag_api_v242"


def _has_table(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def is_applied(con) -> bool:
    has_index = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_tags_norm_nocase'"
    ).fetchone() is not None
    done = con.execute("SELECT 1 FROM meta WHERE key=?", (_DONE_KEY,)).fetchone() is not None
    return has_index and done


def apply(con) -> None:
    con.execute("CREATE INDEX IF NOT EXISTS idx_tags_norm_nocase ON tags(normalized_name COLLATE NOCASE)")
    if con.execute("SELECT 1 FROM meta WHERE key=?", (_DONE_KEY,)).fetchone() is None:
        if _has_table(con, "tag_category_cache"):
            con.execute(
                "DELETE FROM tag_category_cache WHERE source_method=? AND category='general'",
                (_GRABBER_METHOD,),
            )
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, '1')", (_DONE_KEY,))
