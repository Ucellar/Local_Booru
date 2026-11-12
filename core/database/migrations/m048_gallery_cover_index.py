"""Migration 048 (v5.44.152): the gallery's lists and counts read an index, not every card.

The owner (10.11.2026): «некоторые страницы открываются по десять лет».  Measured on a library of 40 thousand cards:
every page of the gallery read the whole table of the cards — 4 300 places of the base, 17 MB — to count the cards of
the search and to put them in order (the count and the order need a card's length, its time, its section; the table
keeps them among its paths and names), and a page far from the first read the more the further it was.  On a disk
that is not in the system's memory (a start, a computer whose memory another program took) that is seconds and
minutes.

``idx_v152_images_gallery`` holds what a search of the gallery asks of a card besides its tags — whether it is
deleted, its time, its length, its bucket and lifecycle, whether it is a favourite, when it was indexed: the count
and the order of a page are read from it alone (about 370 places for those 40 thousand), the cards of the page —
by their numbers (``repository.search_items``).
"""
from __future__ import annotations

NAME = "gallery_cover_index"
INDEX = "idx_v152_images_gallery"
COLUMNS = ("deleted", "mtime_ns", "duration", "bucket", "lifecycle", "favorite", "indexed_at")


def _columns(con) -> set:
    return {str(row[1]) for row in con.execute("PRAGMA table_info(images)").fetchall()}


def is_applied(con) -> bool:
    if not set(COLUMNS) <= _columns(con):            # a base without these columns: nothing for this index to hold
        return True
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (INDEX,)).fetchone() is not None


def apply(con) -> None:
    if set(COLUMNS) <= _columns(con):
        con.execute(f"CREATE INDEX IF NOT EXISTS {INDEX} ON images({', '.join(COLUMNS)})")
