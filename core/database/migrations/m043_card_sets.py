"""Migration 043: сборки of the library's own cards (``core/card_sets.py``) and what the sites said about the posts
the cards came from (``core/site_posts.py``).

- ``site_posts`` — one row per post of a site a card came from: its parent post, who uploaded it, the post's own
  source links; ``state='gone'`` — the site has no such post any more (asked once: never asked again).  Written when
  a card is downloaded (grabber, subscriptions, parser) and, for the cards from before, asked of the sites once in the
  background.
- ``card_sets`` / ``card_set_members`` — the сборки: a card is in one сборка at most, ``position`` its place in it.
  ``locked=1`` — made or changed by the person: the program never rebuilds it.
- ``card_set_optout`` — cards the person took out of a сборка: never put into one automatically again.
"""
from __future__ import annotations

NAME = "card_sets"
_TABLES = {"site_posts", "card_sets", "card_set_members", "card_set_optout"}


def is_applied(con) -> bool:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (%s)" % ",".join("?" for _ in _TABLES),
        tuple(sorted(_TABLES)),
    ).fetchall()
    return {str(row[0]) for row in rows} == _TABLES


def apply(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS site_posts (
            image_id INTEGER NOT NULL,
            site TEXT NOT NULL,
            post_id INTEGER NOT NULL,
            parent_id INTEGER NOT NULL DEFAULT 0,
            uploader TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'ok',
            checked_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(image_id, site, post_id),
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_site_posts_post ON site_posts(site, post_id);
        CREATE INDEX IF NOT EXISTS idx_site_posts_uploader ON site_posts(site, uploader, post_id);

        CREATE TABLE IF NOT EXISTS card_sets (
            set_id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL DEFAULT 'series',
            title TEXT NOT NULL DEFAULT '',
            locked INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS card_set_members (
            image_id INTEGER PRIMARY KEY,
            set_id INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE,
            FOREIGN KEY(set_id) REFERENCES card_sets(set_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_card_set_members_set ON card_set_members(set_id, position, image_id);

        CREATE TABLE IF NOT EXISTS card_set_optout (
            image_id INTEGER PRIMARY KEY,
            created_at INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE CASCADE
        );
        """
    )
