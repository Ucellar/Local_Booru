"""Migration 045: what the program found out about the cards whose file is a site's reduced copy of their post
(``core/reduced_copies.py``, v5.44.13: «sample_…» files get their post's original).

- ``reduced_copies`` — one row per card looked at: ``md5`` — the card's bytes the answer is about (another MD5 — the
  file changed, the card is looked at again; a number given to a new card brings nothing of the old one along),
  ``state`` — ``done`` (the original is in the card's place), ``kept`` (nothing better: the site has no larger
  original, it is another picture…), ``later`` (the network or the site failed: ``again_at``), ``rename`` (the
  original is in place, its name is still to change: the file was busy); ``detail`` — the words why.
"""
from __future__ import annotations

NAME = "reduced_copies"


def is_applied(con) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reduced_copies'").fetchone() is not None


def apply(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS reduced_copies (
            image_id INTEGER PRIMARY KEY,
            md5 TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '',
            site TEXT NOT NULL DEFAULT '',
            post_id INTEGER NOT NULL DEFAULT 0,
            revision_id INTEGER NOT NULL DEFAULT 0,
            asked_at INTEGER NOT NULL DEFAULT 0,
            again_at INTEGER NOT NULL DEFAULT 0
        );
        """
    )
