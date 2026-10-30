"""«What the library has», compact, for the browser extension's offline mode.

The extension keeps a copy and marks cards «Уже есть» by it while Local Booru is closed:
the MD5 of live cards (and the site MD5 bound to them), the MD5 of deleted files, and the booru
posts the cards came from (site -> post ids, from the stored source URLs).

The copy is updated by difference: the extension sends the cursor of its copy and gets only what
was added since (new cards, new aliases, new sources, deleted-rules changed since).  Anything the
difference cannot express (a card put into the trash or back out of it) is corrected by a full copy,
which the extension asks for once a day.  With the program running, the extension asks the program
itself (``/extension/check``) and this copy is not used.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any
from urllib.parse import parse_qs, urlparse

from core.database.connection import db

_MD5 = re.compile(r"^[0-9a-f]{32}$")
_POST_PATH = re.compile(r"/(?:posts|post/show|artworks)/(\d+)(?:[/?#]|$)", re.I)


def _cursor(value: Any) -> dict:
    keys = ("images", "aliases", "sources", "deleted")
    if not isinstance(value, dict):
        return {}
    out = {}
    for key in keys:
        try:
            out[key] = max(0, int(value.get(key) or 0))
        except (TypeError, ValueError):
            return {}
    return out


def post_key(host: str, url: str) -> tuple[str, str] | None:
    """(site, post id) of a post page URL, as the extension names them; None for anything else."""
    from core.browser_companion_api import normalize_host
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return None
    site = normalize_host(host or parsed.netloc)
    if not site:
        return None
    query = parse_qs(parsed.query)
    if "page=post" in parsed.query and query.get("id") and str(query["id"][0]).isdigit():
        return site, str(query["id"][0])
    m = _POST_PATH.search(parsed.path or "")
    return (site, m.group(1)) if m else None


def resolve_post_identity(site: str, post_url: str, post_id: Any, *, trusted: bool = False,
                          is_post_page: bool = False, drop_non_post_url: bool = False) -> tuple[str, str]:
    """(post page URL, post id) of an extension item, with what older extensions got wrong undone.

    The extension 0.6.1+ (``trusted``) describes every card by itself.  Older ones took the page
    for the card: «related» thumbnails on a post page got the id of the page's post, and a right
    click on a search grid sent the search page as the post page (the import then took that page's
    logo or first thumbnail as «the original», and the tags of every post on it as its tags).
    So a post page address wins over a different post id, and (``drop_non_post_url``, for imports)
    an address that is not a post page is dropped unless the item is the page's own post.
    """
    url = str(post_url or "").strip()
    pid = str(post_id or "").strip()
    if trusted:
        return url, pid
    key = post_key(site, url) if url else None
    if key:
        return url, key[1]
    if not url or is_post_page or not drop_non_post_url:
        return url, pid
    from core.parser.sites import post_page_url
    return (post_page_url(site, pid) if pid else ""), pid


def build_index(settings: dict, cursor: Any = None) -> dict:
    """The whole copy (``cursor`` empty/invalid) or what changed after ``cursor``."""
    since = _cursor(cursor)
    full = not since
    since = since or {"images": 0, "aliases": 0, "sources": 0, "deleted": 0}
    have, deleted, undeleted = [], [], []
    posts: dict[str, list] = {}
    nxt = dict(since)
    # The join orders are fixed on purpose (``+deleted`` and CROSS JOIN): left to itself, SQLite walks
    # every live card for every source row (index on «deleted»), which on 100k cards takes hours.
    with db(settings, readonly=True) as con:
        for row in con.execute(
                "SELECT id, lower(hash_md5) FROM images WHERE id>? AND +deleted=0 AND length(hash_md5)=32 ORDER BY id",
                (since["images"],)):
            nxt["images"] = max(nxt["images"], int(row[0]))
            if _MD5.match(row[1] or ""):
                have.append(row[1])
        top = con.execute("SELECT MAX(id) FROM images").fetchone()[0]
        nxt["images"] = max(nxt["images"], int(top or 0))
        try:
            for row in con.execute(
                    """SELECT a.rowid, lower(a.site_md5) FROM image_site_md5_aliases a
                       CROSS JOIN images i ON i.id=a.image_id
                       WHERE a.rowid>? AND i.deleted=0 ORDER BY a.rowid""", (since["aliases"],)):
                nxt["aliases"] = max(nxt["aliases"], int(row[0]))
                if _MD5.match(row[1] or ""):
                    have.append(row[1])
        except sqlite3.OperationalError as exc:  # an old library without the alias table: aliases are an extra
            if "no such table" not in str(exc):
                raise
        for row in con.execute(
                "SELECT lower(md5), active, updated_at FROM deleted_media_rules WHERE updated_at>?",
                (since["deleted"],)):
            nxt["deleted"] = max(nxt["deleted"], int(row[2] or 0))
            if _MD5.match(row[0] or ""):
                (deleted if int(row[1] or 0) else undeleted).append(row[0])
        for row in con.execute(
                """SELECT s.id, s.host, s.url FROM sources s WHERE s.id>? AND EXISTS (
                       SELECT 1 FROM image_sources x CROSS JOIN images i ON i.id=x.image_id
                       WHERE x.source_id=s.id AND i.deleted=0)
                   ORDER BY s.id""", (since["sources"],)):
            nxt["sources"] = max(nxt["sources"], int(row[0]))
            key = post_key(row[1], row[2])
            if key:
                posts.setdefault(key[0], []).append(int(key[1]))
    if full:
        undeleted = []
    return {
        "ok": True,
        "full": full,
        "cursor": nxt,
        "md5": "".join(dict.fromkeys(have)),
        "deleted": "".join(dict.fromkeys(deleted)),
        "undeleted": "".join(dict.fromkeys(undeleted)),
        "posts": {site: sorted(set(ids)) for site, ids in posts.items()},
    }
