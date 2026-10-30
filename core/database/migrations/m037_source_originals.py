"""Migration 037: one row per source file the parser processed.

``processed_files`` is keyed by the library copy (``media_path``), so two identical source files
(or a later grabber import of the same picture, with an empty original) overwrote each other's
``original_path`` and the parser forgot what it had already processed.  ``source_originals`` is
keyed by the source file itself.  Existing knowledge is copied from ``processed_files``.
"""
from __future__ import annotations

import logging
import sqlite3

NAME = "source_originals"


def is_applied(con) -> bool:
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_originals'").fetchone():
        return False
    columns = {str(r[1]) for r in con.execute("PRAGMA table_info(source_originals)")}
    trigger = con.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='trg_source_originals_card_gone'").fetchone()
    return "image_id" in columns and bool(trigger)


def apply(con) -> None:
    # image_id: the card this source file became — it survives moves and quality upgrades
    # (new path, new bytes), so the source keeps counting as processed.
    con.execute(
        """CREATE TABLE IF NOT EXISTS source_originals (
               original_path TEXT PRIMARY KEY,
               image_id INTEGER NOT NULL DEFAULT 0,
               md5 TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT '',
               media_path TEXT NOT NULL DEFAULT '',
               processed_at INTEGER NOT NULL DEFAULT 0
           )"""
    )
    columns = {str(r[1]) for r in con.execute("PRAGMA table_info(source_originals)")}
    if "image_id" not in columns:          # a table made by an early draft of this migration
        con.execute("ALTER TABLE source_originals ADD COLUMN image_id INTEGER NOT NULL DEFAULT 0")
    con.execute("CREATE INDEX IF NOT EXISTS idx_source_originals_md5 ON source_originals(md5)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_source_originals_image ON source_originals(image_id)")
    # SQLite gives the id of a deleted newest row to the next new row: a source must not start
    # pointing at an unrelated new card after its own card was removed for good.
    con.execute("""CREATE TRIGGER IF NOT EXISTS trg_source_originals_card_gone
                   AFTER DELETE ON images
                   BEGIN
                       UPDATE source_originals SET image_id=0 WHERE image_id=OLD.id;
                   END""")
    # Oldest first, so the latest record of each original wins.  Only a speed-up for the parser's
    # "already processed" check (processed_files is still read as a fallback): a failure here must
    # not stop the program from starting.
    try:
        con.execute(
            """INSERT OR REPLACE INTO source_originals(original_path, image_id, md5, status, media_path, processed_at)
               SELECT p.original_path, COALESCE(i.id, 0), lower(COALESCE(i.hash_md5, '')), COALESCE(p.status, ''),
                      p.media_path, COALESCE(p.processed_at, 0)
               FROM processed_files p LEFT JOIN images i ON i.path = p.media_path
               WHERE COALESCE(p.original_path, '') <> ''
               ORDER BY p.processed_at ASC, p.rowid ASC"""
        )
    except sqlite3.Error as exc:
        logging.getLogger("local_booru.migrations").warning("migration 037: old records not copied: %s", exc)
