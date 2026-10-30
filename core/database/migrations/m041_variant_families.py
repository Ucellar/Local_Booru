"""Migration 041: «families» made of the URL variants of one post stop completing themselves.

Before 5.3.1 every browser import whose card listed more than one media URL (the original, its
sample, the thumbnail, og:image…) got a ``multi_attachment`` family with one «page» per URL, and
the family-completion lane kept importing the «missing pages»: on sites without an API that saved
samples and thumbnails as separate cards, on the others it retried forever.  Nothing produces such
families any more.  The existing ones are kept (nothing is deleted) but switched off: no automatic
completion, their pending completion jobs are closed, and the extension no longer shows them.
"""
from __future__ import annotations

import time

NAME = "variant_families"
_DONE_KEY = "variant_families_switched_off"
_REASON = "5.3.1: URL variants of one post are not pages of a family"


def _has_table(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(con, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def is_applied(con) -> bool:
    return con.execute("SELECT 1 FROM meta WHERE key=?", (_DONE_KEY,)).fetchone() is not None


def apply(con) -> None:
    now = int(time.time())
    if _has_table(con, "media_families"):
        ids = [int(r[0]) for r in con.execute(
            "SELECT family_id FROM media_families WHERE family_type='multi_attachment'").fetchall()]
        columns = _columns(con, "media_families")
        if ids and {"auto_complete", "completion_status"} <= columns:
            con.execute("UPDATE media_families SET auto_complete=0, completion_status='variants' "
                        "WHERE family_type='multi_attachment'")
        if ids and _has_table(con, "background_lane_jobs"):
            for family_id in ids:
                con.execute(
                    """UPDATE background_lane_jobs SET state='failed', owner='', updated_at=?, last_error=?
                       WHERE lane='family-completion' AND identity_key LIKE ?
                         AND state IN ('pending','waiting','retry','running')""",
                    (now, _REASON, f"{family_id}:%"))
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, '1')", (_DONE_KEY,))
