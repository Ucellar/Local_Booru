"""Migration 044: indexes that repeat another index exactly.

Migrations 019 and 021 (v150, v158) made indexes that the base schema, a table's own key or migration 019 had made
already — the same columns in the same order and collation: ``image_tags`` had seven indexes on its two columns, and
every tag of every save went into each of them; every site's answer went into two equal ones of ``site_scan_status``.
Only these copies are dropped, and only after checking in this very base that the index kept has the same key columns
(name, order, collation) and that neither is partial; a pair that does not match is left as it is.
``idx_v158_ist_image_source`` is the beginning of the table's own unique index, which answers the same lookups.  (The
base schema makes its indexes again at every start — so those of 019/021 go, never those.)
"""
from __future__ import annotations

NAME = "drop_duplicate_indexes"

# (index dropped, index kept — the same key columns; "prefix": the dropped one is the beginning of the kept one)
PAIRS = (
    ("idx_v150_image_tags_image_tag", "idx_image_tags_image", "same"),
    ("idx_v158_image_tags_image_tag", "idx_image_tags_image", "same"),
    ("idx_v150_image_tags_tag_image", "idx_image_tags_tag", "same"),
    ("idx_v158_image_tags_tag_image", "idx_image_tags_tag", "same"),
    ("idx_v158_ist_tag_image", "idx_image_source_tags_tag", "same"),
    ("idx_v158_ist_source_tag_image", "idx_image_source_tags_source", "same"),
    ("idx_v158_ist_image_source", "sqlite_autoindex_image_source_tags_1", "prefix"),
    ("idx_v158_images_live_mtime_id", "idx_v150_images_live_mtime_id", "same"),
    ("idx_v158_sources_host_id", "idx_v150_sources_host_id", "same"),
    ("idx_v158_sources_url_nocase", "idx_sources_url", "same"),
    ("idx_v158_tags_norm_id", "idx_v150_tags_norm_id", "same"),
    ("idx_v158_image_sources_image_source", "sqlite_autoindex_image_sources_1", "same"),
    ("idx_v158_image_sources_source_image", "idx_image_sources_source", "same"),
    ("idx_v158_site_scan_cover", "idx_v150_site_scan_cover", "same"),
    ("idx_v158_no_match_reason_active", "idx_no_match_reason_active", "same"),
    ("idx_v158_no_match_source_host", "idx_no_match_source_host", "same"),
)


def _index(con, name: str):
    """(table, key columns [(column, descending, collation)], partial) of an index — None when there is none."""
    row = con.execute("SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
    if not row:
        return None
    table = str(row[0])
    keys = tuple((str(r[2]), int(r[3]), str(r[4] or "BINARY").upper())
                 for r in con.execute(f'PRAGMA index_xinfo("{name}")').fetchall() if int(r[5]) == 1)
    partial = next((int(r[4]) for r in con.execute(f'PRAGMA index_list("{table}")').fetchall() if str(r[1]) == name), 0)
    return table, keys, bool(partial)


def removable(con, drop: str, keep: str, how: str) -> bool:
    """``drop`` repeats ``keep`` in this base (both there, same table, neither partial, the keys match)."""
    a, b = _index(con, drop), _index(con, keep)
    if a is None or b is None or a[0] != b[0] or a[2] or b[2] or not a[1]:
        return False
    return a[1] == b[1] if how == "same" else b[1][:len(a[1])] == a[1]


def is_applied(con) -> bool:
    return not any(removable(con, drop, keep, how) for drop, keep, how in PAIRS)


def apply(con) -> None:
    for drop, keep, how in PAIRS:
        if removable(con, drop, keep, how):
            con.execute(f'DROP INDEX IF EXISTS "{drop}"')
