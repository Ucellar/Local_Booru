"""Migration 038: one spelling per source host ("www.pixiv.net" -> "pixiv.net").

Two writers stored hosts differently, so one URL could be two ``sources`` rows and was shown twice.
A row with a "www." host is merged into the plain-host row of the same URL (links and roles move
over; per-site tag sets were always stored under plain hosts) or simply renamed.

This is a cosmetic cleanup, so it never stops the program from starting: a row that cannot be
merged is left as it was, and the migration is marked done once it has run.
"""
from __future__ import annotations

import logging
import sqlite3

NAME = "normalize_source_hosts"
_DONE_KEY = "source_hosts_normalized"


def _plain(host: str) -> str:
    text = str(host or "").strip().lower()
    while text.startswith("www."):
        text = text[4:]
    return text


def is_applied(con) -> bool:
    row = con.execute("SELECT value FROM meta WHERE key=?", (_DONE_KEY,)).fetchone()
    if row is not None:
        return True
    return con.execute("SELECT 1 FROM sources WHERE lower(host) LIKE 'www.%' LIMIT 1").fetchone() is None


def _tables(con) -> set[str]:
    return {str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def apply(con) -> None:
    log = logging.getLogger("local_booru.migrations")
    tables = _tables(con)
    # Lookups by source_id below (and the foreign-key cascades) would scan these tables per row.
    if "image_source_tags" in tables:
        con.execute("CREATE INDEX IF NOT EXISTS idx_image_source_tags_source ON image_source_tags(source_id)")
    if "image_source_roles" in tables:
        con.execute("CREATE INDEX IF NOT EXISTS idx_image_source_roles_source ON image_source_roles(source_id)")
    rows = con.execute("SELECT id, host, url FROM sources WHERE lower(host) LIKE 'www.%'").fetchall()
    merged = renamed = skipped = 0
    for row in rows:
        old_id, url = int(row[0]), str(row[2])
        plain = _plain(row[1])
        if not plain:
            skipped += 1
            continue
        con.execute("SAVEPOINT m038_row")
        try:
            target = con.execute("SELECT id FROM sources WHERE host=? AND url=?", (plain, url)).fetchone()
            if target is None:
                con.execute("UPDATE sources SET host=? WHERE id=?", (plain, old_id))
                renamed += 1
            else:
                new_id = int(target[0])
                # Only links of cards that still exist: orphan rows would break the foreign keys.
                con.execute("INSERT OR IGNORE INTO image_sources(image_id, source_id) "
                            "SELECT x.image_id, ? FROM image_sources x JOIN images i ON i.id=x.image_id "
                            "WHERE x.source_id=?", (new_id, old_id))
                if "image_source_tags" in tables:
                    con.execute("INSERT OR IGNORE INTO image_source_tags(image_id, source_id, tag_id, category, acquisition, updated_at) "
                                "SELECT x.image_id, ?, x.tag_id, x.category, x.acquisition, x.updated_at "
                                "FROM image_source_tags x JOIN images i ON i.id=x.image_id JOIN tags t ON t.id=x.tag_id "
                                "WHERE x.source_id=?", (new_id, old_id))
                if "image_source_roles" in tables:
                    con.execute("INSERT OR IGNORE INTO image_source_roles(image_id, source_id, role, is_preferred, provenance, observed_at) "
                                "SELECT x.image_id, ?, x.role, x.is_preferred, x.provenance, x.observed_at "
                                "FROM image_source_roles x JOIN images i ON i.id=x.image_id WHERE x.source_id=?",
                                (new_id, old_id))
                for table in ("image_sources", "image_source_tags", "image_source_roles"):
                    if table in tables:
                        con.execute(f"DELETE FROM {table} WHERE source_id=?", (old_id,))
                con.execute("DELETE FROM sources WHERE id=?", (old_id,))
                merged += 1
            con.execute("RELEASE m038_row")
        except sqlite3.Error as exc:
            con.execute("ROLLBACK TO m038_row")
            con.execute("RELEASE m038_row")
            skipped += 1
            log.warning("migration 038: source %s (%s) left as it was: %s", old_id, url, exc)
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                (_DONE_KEY, f"merged={merged} renamed={renamed} skipped={skipped}"))
