"""Migration 046: a tag's category is what most sites say of it (``core/tag_consensus.py``, v5.44.49).

- ``tag_category_review`` — the tags a site's answer called otherwise than the library knows them: ``pending`` — how
  many such answers came since the tag was last looked at, ``needed`` — how many it takes to be looked at again (the
  lead of the winning category the last time: a tag most sites call «general» thousands of times is not counted again
  for one answer that calls it a character), ``reviewed_at`` — when it was last looked at.

The categories themselves are put right once by the background duty (``tag_consensus.repair``, the mark
``tag_consensus_v54449`` in ``meta``), not here: it reads every site's set of tags, and the start of the program should
not wait for it.
"""
from __future__ import annotations

NAME = "tag_category_review"


def is_applied(con) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tag_category_review'").fetchone() is not None


def apply(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS tag_category_review (
            tag_id INTEGER PRIMARY KEY,
            pending INTEGER NOT NULL DEFAULT 0,
            needed INTEGER NOT NULL DEFAULT 0,
            reviewed_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_tag_category_review_due ON tag_category_review(pending, needed);
        """
    )
