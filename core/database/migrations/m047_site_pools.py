"""Migration 047 (v5.44.111): the pools of the sites' posts — a comic of e621 is a pool, its pages in their order.

The owner (7.11.2026), of a comic whose pages the parser found on e621 one by one: «они обязанны были войти в мангу
или хотя бы в сборку но не вошли»; «если 15+ картинок в сборке то в мангу».

- ``site_posts.pools`` — the pools a post is in, as the site said («15766,3001»; "" — none; NULL — not asked yet: the
  posts kept before this version are asked again in the background, ``core/site_posts.py``);
- ``site_posts.rating`` — the post's rating as the site said it, by its first letter («s», «q», «e», «g»; NULL — not
  said): a comic is not taken to the manga folder when the program's rule forbids it (``core/comic_manga.py``);
- ``site_pools`` — a pool as its site describes it: ``name``, ``category`` (e621: «series» — one work, a comic;
  «collection» — a theme), ``post_ids`` (JSON, the pages in their order), ``state`` («ok», «gone» — the site has it no
  more), ``checked_at``.
"""
from __future__ import annotations

NAME = "site_pools"


def is_applied(con) -> bool:
    columns = {row[1] for row in con.execute("PRAGMA table_info(site_posts)").fetchall()}
    table = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_pools'").fetchone()
    return "pools" in columns and "rating" in columns and table is not None


def apply(con) -> None:
    columns = {row[1] for row in con.execute("PRAGMA table_info(site_posts)").fetchall()}
    if "pools" not in columns:
        con.execute("ALTER TABLE site_posts ADD COLUMN pools TEXT")
    if "rating" not in columns:
        con.execute("ALTER TABLE site_posts ADD COLUMN rating TEXT")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS site_pools (
            site TEXT NOT NULL,
            pool_id INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            post_ids TEXT NOT NULL DEFAULT '[]',
            state TEXT NOT NULL DEFAULT 'ok',
            checked_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (site, pool_id)
        );
        """
    )
