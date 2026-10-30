"""Which of a site's posts are parts of a сборка (v5.32): the reference client's way of finding a series and a work on rule34 (its
``sets.js``), on the posts the grabber's searches brought (``core/set_remote.py`` asks; nothing here goes to the
network).

- **the uploader's posts around** (``autoSet``): the сборка's own posts on the site are its pages; a post of the same
  uploader between them, after the last or before the first goes in when it is linked to the last page found — a
  parent / a child, the same work by the links, a page of the same comic, or alike by tags (and a little like the
  first page); up to :data:`~core.card_sets.MISS` other posts of the uploader between two pages alike by tags,
  :data:`~core.card_sets.MISS_WORK` between parts of one work, :data:`~core.card_sets.MISS_COMIC` between the pages
  of a comic.  Then the pages of the comic passed on the way (a character not found yet).
- **the pages of a comic uploaded by others** (``comicByArtist``): the artist's posts with the comic's tag around, by
  the rule of the pages of a comic (the same canvas, the artist, a character of the found pages).
- **the same work by its source** (``workSet``): the posts whose link names the same work (``source:*номер*`` brought
  them: the number may stand inside another link — only the same work counts; a tweet or a bluesky post holds four
  pictures, a link on more posts is the artist's everywhere-link); a post with the tags of a set of pictures
  (``alternate_version``, ``comic``…) — the artist's posts of the same canvas with a common character; one file of a
  FurAffinity / DeviantArt / Hentai Foundry record uploaded twice is one picture.

The rules are those of the сборки of the library's own cards (``core/card_sets.py``): a post here is a ``_Post``
whose ``_Card`` has its tags as names.
"""
from __future__ import annotations

import re

from core.card_sets import (ANCHOR, FEW_IMG_MAX, MISS, MISS_COMIC, MISS_WORK, SET_MAX, _AI, _COMIC, _FEW, _ONE_FILE,
                            _Card, _Post, _comic_fits, _diff_work, _foreign_child, _seq_link, _tag_link, link_score,
                            work_key)

SEQ_MORE = 1            # pages more of the uploader's posts in one direction while the сборка goes on
COMIC_MORE = 5          # …while the pages of a comic go on (a web comic is uploaded over months)
HINT = frozenset({"alternate_version", "alternate_version_available", "alternate_version_at_source", "continuation",
                  "story", "variant_set", "sequence", "multiple_images", "image_set", "before_and_after", "comic",
                  "comic_page", "webcomic", "manga", "minicomic", "short_comic", "2koma", "3koma", "4koma", "series"})
GENERIC_CHAR = frozenset({"original", "original_character", "oc", "unknown_character", "character_request",
                          "anonymous", "male", "female", "human"})
_FAKE_ARTIST = re.compile(r"^(artist_request|anonymous_artist|unknown_artist|artist_unknown|third[-_]party_edit|"
                          r"ai_generated|ai_assisted|sound_warning|uncredited|edit|unknown_artist_signature|anonymous|"
                          r"nameless|tagme_\(artist\)|artist_name)$")
_ARTIST_OK = re.compile(r"[^\s~*-][^\s~*]*")


# ── the site's posts as the rules see them ─────────────────────────────────────────────
def site_post(entry: dict, engine: str) -> _Post | None:
    """A grabber entry (``core/grabber/cards.entry``) as a post of the rules, or None (not a post: no number)."""
    from core.site_posts import fields
    row = fields(entry.get("raw") or {}, engine)
    if not row:
        return None
    card = _Card(-int(row["post_id"]), entry.get("width") or 0, entry.get("height") or 0)
    tags = frozenset(str(t).lower() for t in entry.get("tags") or [] if t)
    groups = entry.get("groups") or {}
    card.tags = tags
    card.artists = frozenset(str(t).lower() for t in groups.get("artist") or [] if t)
    card.chars = frozenset(str(t).lower() for t in groups.get("character") or [] if t)
    card.comic = not tags.isdisjoint(_COMIC)
    card.ai = not tags.isdisjoint(_AI)
    for url in str(row.get("source") or "").split("\n")[:6]:
        found = work_key(url)
        if found:
            if found[0] not in card.keys:
                card.keys.append(found[0])
            if found[1] is not None and found[0] not in card.pages:
                card.pages[found[0]] = int(found[1])
    return _Post(card, str(entry.get("site") or ""), int(row["post_id"]), int(row["parent_id"] or 0),
                 str(row.get("uploader") or ""))


def joined(remote: _Post, own: _Post | None) -> _Post:
    """The сборка's own post as the site gave it (the tags of one site against the site's posts), with what the
    library knows of it more: the links of its card (a pixiv file name, the sources of other sites), the artists and
    characters when the site said no categories."""
    if own is None:
        return remote
    card = remote.card
    card.id = own.card.id
    for key in own.card.keys:
        if key not in card.keys:
            card.keys.append(key)
    for key, page in own.card.pages.items():
        card.pages.setdefault(key, page)
    if not card.artists:
        card.artists = own.card.artists
    if not card.chars:
        card.chars = own.card.chars
    card.comic = card.comic or own.card.comic
    return remote


def real_artists(post: _Post) -> list:
    return sorted(a for a in post.card.artists if not _FAKE_ARTIST.match(a) and _ARTIST_OK.fullmatch(a))


def real_chars(post: _Post) -> frozenset:
    return frozenset(c for c in post.card.chars if c not in GENERIC_CHAR)


def hinted(post: _Post) -> bool:
    """Its tags say «a part of a set» (an alternate version, a continuation, a comic…)."""
    return not post.card.tags.isdisjoint(HINT)


# ── the uploader's posts around (autoSet) ──────────────────────────────────────────────
def _hit(pages: list, last: _Post, anchor: _Post, c: _Post, miss: int) -> bool:
    if _foreign_child(pages, c):
        return False
    return bool(_seq_link(last, c) or _comic_fits(pages, c)
                or (miss <= MISS and _tag_link(last, c) and (last is anchor or link_score(anchor, c) >= ANCHOR)))


def _give_up(last: _Post, miss: int) -> bool:
    if miss > MISS and not (last.card.keys or last.parent or last.card.comic):
        return True                                 # nothing but tags to go on: the series is over
    return miss > (MISS_COMIC if last.card.comic else MISS_WORK)


class Walk:
    """The сборка's pages on one site and the posts found to belong to it (``found``, in the order found)."""

    def __init__(self, pages: list, anchor: _Post):
        self.pages = sorted(pages, key=lambda p: p.id)
        self.anchor = anchor
        self.own = {p.id for p in self.pages}
        self.found: list = []
        self._found_ids: set = set()
        self.last: _Post | None = None

    def taken(self, post: _Post) -> bool:
        return post.id in self.own or post.id in self._found_ids

    def more(self) -> int:
        """Pages more the walk may ask in its direction now (more while the pages of a comic go on)."""
        return COMIC_MORE if self.last is not None and self.last.card.comic else SEQ_MORE

    def _take(self, post: _Post) -> None:
        self.pages.append(post)
        self.found.append(post)
        self._found_ids.add(post.id)

    def inside(self, between: list) -> list:
        """The uploader's posts between the first and the last page (by number): each goes in when it is linked to the
        last page before it (the сборка's own pages always are)."""
        if not self.pages:
            return []
        seq = sorted([p for p in between if not self.taken(p)] + list(self.pages), key=lambda p: p.id)
        added: list = []
        last, miss = seq[0], 0
        for c in seq[1:]:
            if c.id in self.own:
                last, miss = c, 0
                continue
            if len(self.pages) < SET_MAX and _hit(self.pages, last, self.anchor, c, miss):
                self._take(c)
                added.append(c)
                last, miss = c, 0
            else:
                miss += 1
        return added

    def outward(self, edge: _Post, posts) -> list:
        """From ``edge`` outward over the uploader's posts (``posts`` — nearest first; it may ask :meth:`more` for
        pages): a post linked to the last page found goes in; too many others in a row — the сборка is over."""
        added: list = []
        self.last, miss = edge, 0
        for c in posts:
            if len(self.pages) >= SET_MAX:
                break
            if self.taken(c):
                continue
            if _hit(self.pages, self.last, self.anchor, c, miss):
                self._take(c)
                added.append(c)
                self.last, miss = c, 0
                continue
            miss += 1
            if _give_up(self.last, miss):
                break
        return added

    def comic_between(self, known: list) -> list:
        """The pages of a comic between the first and the last page passed on the way (only a character not found
        then) — now that every character is known."""
        if len(self.pages) < 2 or not any(p.card.comic for p in self.pages):
            return []
        lo, hi = min(p.id for p in self.pages), max(p.id for p in self.pages)
        added: list = []
        for c in sorted(known, key=lambda p: p.id):
            if lo < c.id < hi and not self.taken(c) and len(self.pages) < SET_MAX and _comic_fits(self.pages, c):
                self._take(c)
                added.append(c)
        return added

    def comic_by_artist(self, inside: list, after: list, before: list) -> list:
        """The pages of the comic uploaded by others (the artist's posts with its tag): between the pages — while one
        goes in (a new page may bring the character the next one needs); outward — until :data:`MISS_COMIC` others in a
        row."""
        added: list = []

        def take(c: _Post) -> bool:
            if self.taken(c) or len(self.pages) >= SET_MAX or not _comic_fits(self.pages, c):
                return False
            self._take(c)
            added.append(c)
            return True
        grew = True
        while grew:
            grew = False
            for c in sorted(inside, key=lambda p: p.id):
                grew = take(c) or grew
        for posts, step in ((after, 1), (before, -1)):
            miss = 0
            for c in sorted(posts, key=lambda p: step * p.id):
                if self.taken(c):
                    miss = 0
                    continue
                if take(c):
                    miss = 0
                else:
                    miss += 1
                    if miss > MISS_COMIC:
                        break
        return added


def comic_tags(pages: list) -> tuple | None:
    """(the artist, the comic's tag) the comic pages of a сборка share — what its pages by others are looked for by;
    None — no comic, or no artist they all share."""
    comic = [p for p in pages if p.card.comic]
    if not comic:
        return None
    artists = set(real_artists(comic[0]))
    tags = set(comic[0].card.tags & _COMIC)
    for p in comic[1:]:
        artists &= set(real_artists(p))
        tags &= p.card.tags
    if not artists or not tags:
        return None
    return sorted(artists)[0], "comic" if "comic" in tags else sorted(tags)[0]


# ── the same work by its source (workSet) ──────────────────────────────────────────────
def work_queries(order: list, queries: dict) -> list:
    """[(work key, what to look for)] of a сборка's cards (in its order): a Discord series first (it holds every
    page), then pixiv, the rest; two of a card at most, three in all."""
    rank = {"dc": 0, "px": 1}
    out: dict = {}
    for image_id in order:
        mine: dict = {}
        for key, text in queries.get(image_id) or []:
            mine.setdefault(key, text)
        for key in sorted(mine, key=lambda k: rank.get(k.split(":", 1)[0], 2))[:2]:
            out.setdefault(key, mine[key])
        if len(out) >= 3:
            break
    return list(out.items())[:3]


def work_hits(key: str, posts: list, own: set) -> list:
    """The posts of a ``source:`` answer whose link names the work ``key`` (not the сборка's own); none when a tweet's
    / a bluesky post's link is on more than :data:`FEW_IMG_MAX` posts — the artist's everywhere-link."""
    hits = [p for p in posts if p.id not in own and key in p.card.keys]
    if key.startswith(_FEW) and len(hits) + 1 > FEW_IMG_MAX:
        return []
    return hits


def canvases(pages: list, anchor: _Post) -> list:
    """[(page, the artist, width, height)] the artist's posts of the same canvas are looked for by — only when the
    anchor's tags say «a part of a set» (without a check of the pictures themselves a post of the same canvas could be
    any other work of the artist); two at most."""
    artists = [a for a in real_artists(anchor) if "*" not in a and "~" not in a]
    if not hinted(anchor) or not artists:
        return []
    art = artists[0]
    out: list = []
    for page in [anchor] + [p for p in pages if p is not anchor]:
        w, h = page.card.w, page.card.h
        if not (w > 0 and h > 0) or any(c[2] == w and c[3] == h for c in out):
            continue
        if page is not anchor and art not in page.card.tags:
            continue
        out.append((page, art, w, h))
        if len(out) >= 2:
            break
    return out


def canvas_hits(page: _Post, anchor: _Post, width: int, height: int, posts: list, own: set) -> list:
    """The artist's posts of the page's canvas that share a real character with it (not AI pictures, not another work
    of the same site)."""
    chars = real_chars(page)
    if not chars or page.card.ai or anchor.card.ai:
        return []
    return [c for c in posts if c.id not in own and c.card.w == width and c.card.h == height and not c.card.ai
            and not _diff_work(page.card, c.card) and real_chars(c) & chars]


def one_file_copies(pages: list, found: list) -> set:
    """Numbers of found posts that are another upload of a picture the сборка has: the same FurAffinity / DeviantArt /
    Hentai Foundry record (one file in it) and the same proportions."""
    def aspect(p: _Post) -> float:
        return p.card.w / p.card.h if p.card.w > 0 and p.card.h > 0 else 0.0
    drop: set = set()
    kept = list(pages)
    for c in found:
        keys = [k for k in c.card.keys if k.startswith(_ONE_FILE)]
        if keys and aspect(c) > 0 and any(
                set(keys) & set(p.card.keys) and aspect(p) > 0 and abs(aspect(p) - aspect(c)) <= 0.01 * aspect(p)
                for p in kept):
            drop.add(c.id)
            continue
        kept.append(c)
    return drop
