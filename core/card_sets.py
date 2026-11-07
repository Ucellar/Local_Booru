"""Сборки of the library's own cards: the pictures of one work, of one series, of a parent post and its children —
kept in the base (``card_sets``, ``card_set_members``: a card is in one сборка at most), shown in the gallery as one
tile and browsed on the card's page (``webui/pages/post_sets.py``).  Only downloaded cards make a сборка: nothing is
taken from a site but what it said about the cards' own posts (``core/site_posts.py``).

The program puts them together by itself (:func:`rebuild`, a background duty), the way a reference booru client folds the posts it has
loaded (``foldLocal`` of its ``sets.js``) — on every site separately:

- **родитель и дочерние** — posts whose parent is another post of the library, or posts of the same parent;
- **одна работа** — the same work by the links the posts and the cards carry: a pixiv work (``artworks/N``, the pages
  ``N_p0``, ``N_p1``… of pximg, also in the file's own name), a tweet, a Patreon / Fanbox / Fantia / Inkbunny /
  FurAffinity / DeviantArt / Tumblr… post, a nhentai / e-hentai gallery, an album, the pages of a family the browser
  extension brought.  A tweet or a bluesky post holds four pictures: a link of that kind on more than :data:`FEW_IMG_MAX`
  cards is the artist's everywhere-link, not a work;
- **серия** — the posts of one uploader in the order of their numbers: next to each other, the same family, at most
  :data:`LOCAL_GAP` numbers apart and alike — at least :data:`LINK` points: the share of common tags (0…100), +15 the
  same artist (−10 another), +10 the same size (+5 the same proportions), +10 uploaded within :data:`GAP_NEAR` numbers
  (+5 within :data:`GAP_CLOSE`, −10 more than :data:`GAP_FAR` apart); links to two works of one site — never one work;
  every new page has to be like the first one a little too (:data:`ANCHOR` points: the chain does not wander off);
  AI pictures of different days never.  **Comics**: pages with a comic tag, the same artist, the same canvas (or the
  same proportions, uploaded near) and a character the found pages have (or half of the tags) — over up to
  :data:`MISS_COMIC` other cards of the uploader between them;
- what links two cards links their сборки: one card — one сборка;
- (v5.44.111; the owner, of a comic found on e621 page by page: «они обязанны были войти в мангу или хотя бы в
  сборку») **a pool of e621** of the category «series» — a comic — is one work: its cards (any uploader), in the
  order of the pool's pages (``site_posts.pools``, ``site_pools``: :func:`pool_key`).  A pool of the category
  «collection» (a theme) is not, nor one the site has not described yet;
- (v5.44.125; the owner: «а если нет то просто в сборку потому что я раньше часто даже не мангу помечал 1 2 3») **a
  folder of the parser found «not a manga»** (``core/parser_manga.folder_cards``: its pages by numbers, the cards the
  parser made of them — ``source_originals``) is one work, in the folder's order («dir:<its id>»).

Videos are never in a сборка; a card in the trash leaves its automatic сборка (and comes back with the card).

**The person's word wins.**  A сборка the person changed (took a card out, moved one, named it) is ``locked``: the
program never rebuilds it — only new cards that belong to it are added at its end; one the person made of chosen cards
(``kind='manual'``) is never touched at all.  A card taken out of a сборка (``card_set_optout``) is never put into one
by the program again.  «Разобрать» takes all its cards out.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

_LOG = logging.getLogger("local_booru.card_sets")

BUILD_VERSION = 3               # +1 when the rules change: the next run rebuilds (2: v5.44.10, the sites' own tags;
                                # 3: v5.44.111, the pools of e621)
SET_MAX = 120                   # cards in one сборка (a manga of 60+ pages is one)
LINK = 85                       # points of two neighbouring posts of one uploader to be one work (see linkScore)
PTS = {"artist": 15, "artist_other": -10, "size": 10, "aspect": 5, "gap_near": 10, "gap_close": 5, "gap_far": -10}
GAP_NEAR, GAP_CLOSE, GAP_FAR = 60, 300, 1000   # post numbers apart (rule34: about 4 posts a minute)
ANCHOR = 45                     # …and a new page has to be a little like the first one
LOCAL_GAP = 1000                # neighbours by tags only this many numbers apart
MISS = 2                        # other posts of the uploader between two pages alike by tags
MISS_WORK = 8                   # …between two parts of one work (a link, a parent)
MISS_COMIC = 40                 # other cards of the uploader between two pages of a comic
SCALED_GAP = 6000               # a smaller copy of a comic's canvas only this near to a page
FEW_IMG_MAX = 8                 # a tweet / a bluesky post on more cards: the artist's everywhere-link, not a work
FEW_IMG_SPAN = 500000           # …and on one site two posts with it this far apart are not its pictures
TAG_BATCH = 5000                # cards whose tags are in memory at once
KINDS = ("parent", "work", "series")            # what made an automatic сборка, the strongest first
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".swf", ".flv", ".wmv"}

_COMIC = {"comic", "comic_page", "webcomic", "manga", "manhwa", "doujinshi"}
_AI = {"ai_generated", "ai-generated", "ai_assisted", "ai-assisted", "ai_art", "stable_diffusion", "novelai",
       "nai_diffusion", "midjourney", "dall-e", "ai_generated_art"}


# ── links that name one work ──────────────────────────────────────────────────────────
_DC_GENERIC = {"image", "unknown", "img", "photo", "picture", "pic", "file", "screenshot", "screen", "untitled",
               "download", "attachment", "video", "sketch", "wip", "final", "commission", "comm", "ych", "art", "drawing",
               "export", "edit", "test", "render", "page", "comic", "reference", "reward", "part", "version", "clean",
               "nude", "censored", "uncensored", "spoiler", "message", "hero"}
_PROFILE = re.compile(r"/(users?|members?|member\.php|profile|artists?|creators?|c|u|channel|followers|following|likes|"
                      r"media|gallery|favourites|favorites)(/|$|\?)", re.I)
_BOORU_HOSTS = ("donmai.us", "gelbooru", "rule34", "e621.net", "e926.net", "yande.re", "konachan", "sankaku",
                "allthefallen", "aibooru", "safebooru", "xbooru", "realbooru", "tbib", "hypnohub", "lolibooru", "booru")
_MD5_FILE = re.compile(r"(?:^|/|_)([0-9a-f]{32})\.(?:jpe?g|png|gif|webp|mp4|webm)$", re.I)
_PIXIV_FILE = re.compile(r"(?:^|[\\/])(\d{6,})_p(\d{1,3})(?:_master\d+)?\.[a-z0-9]{2,5}$", re.I)


def _dc_name(name: str):
    """A Discord attachment's name → (the series key, the page or None), or None (a faceless name)."""
    stem = re.sub(r"\.[a-z0-9]{2,5}$", "", unquote(str(name or "")), flags=re.I)
    stem = re.sub(r"^SPOILER_", "", stem, flags=re.I)
    m = re.match(r"^(.*?)[\s_.-]*(\d{1,3})$", stem)
    page = int(m.group(2)) if m and m.group(1) else None
    if m and m.group(1):
        stem = m.group(1)
    norm = re.sub(r"[^a-z0-9]", "", stem.lower())
    letters = len(re.sub(r"[0-9]", "", norm))
    if letters < 5 or norm in _DC_GENERIC or re.fullmatch(r"[0-9a-f]{12,}", norm) or re.match(r"^(img|dsc|pxl|screenshot|image)\d", norm):
        return None
    return "dc:" + norm, page


def work_key(link: str):
    """(work key, page or None) of a link, False — surely not a work (an image of a record that has one, a Discord
    file without a name), None — not known as a work.  A port of the reference client's ``srcWork`` + ``parseSource``."""
    text = str(link or "").strip()
    text = re.sub(r"&amp;|&#0*38;|&#x0*26;", "&", text, flags=re.I)
    if not re.match(r"^https?://", text, re.I):
        if re.match(r"^(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}/", text, re.I):
            text = "https://" + text
        else:
            return None
    try:
        url = urlparse(text)
        host = (url.hostname or "").lower()
    except ValueError:
        return None
    host = re.sub(r"^(www|m|mobile|old|new)\.", "", host)
    path, query = url.path or "", url.query or ""

    def has(domain: str) -> bool:
        return host == domain or host.endswith("." + domain)

    def q(name: str) -> str:
        return (parse_qs(query).get(name) or [""])[0]

    if any(mark in host for mark in _BOORU_HOSTS):
        return None                                   # a booru's own post or file: not a work
    if re.fullmatch(r"cdn\.discordapp\.com|media\.discordapp\.net|cdn\.discord\.com", host):
        m = re.match(r"^/attachments/\d+/\d+/([^/]+)$", path)
        return (_dc_name(m.group(1)) or False) if m else False
    if re.fullmatch(r"(discord|discordapp)\.com", host):
        m = re.match(r"^/channels/\d+/\d+/(\d{6,})", path)
        return ("dm:" + m.group(1), None) if m else None
    if has("patreon.com"):
        m = re.match(r"^/posts/(?:[^/]*?-)?(\d{5,})/?$", path)
        return ("pt:" + m.group(1), None) if m else None
    if has("patreonusercontent.com"):
        m = re.search(r"/p/post/(\d{5,})/", path)
        return ("pt:" + m.group(1), None) if m else None
    m = re.fullmatch(r"(kemono|coomer)\.(su|party|cr|st)", host)
    if m:
        m = re.match(r"^/([a-z]+)/user/[^/]+/post/([A-Za-z0-9_-]+)", path)
        if not m:
            return None
        svc = {"patreon": "pt", "fanbox": "fb", "fantia": "fn", "subscribestar": "ss"}.get(m.group(1))
        return (f"{svc}:{m.group(2)}" if svc else f"km:{m.group(1)}:{m.group(2)}", None)
    if has("fanbox.cc"):
        m = re.search(r"/posts/(\d{4,})", path) or re.match(r"^/images/post/(\d{4,})/", path)
        return ("fb:" + m.group(1), None) if m else None
    if has("fantia.jp"):
        m = re.match(r"^/posts/(\d{3,})", path) or re.match(r"^/uploads/post/file/(\d{3,})/", path)
        return ("fn:" + m.group(1), None) if m else None
    if re.fullmatch(r"subscribestar\.(adult|com)", host):
        m = re.match(r"^/posts/(\d{3,})", path)
        return ("ss:" + m.group(1), None) if m else None
    if has("furaffinity.net"):
        m = re.match(r"^/(?:view|full)/(\d{3,})", path)
        return ("fa:" + m.group(1), None) if m else None
    if has("inkbunny.net"):
        m = re.match(r"^/s/(\d{3,})(?:-p(\d+))?", path)
        if m:
            return "ik:" + m.group(1), int(m.group(2)) if m.group(2) else 1
        if path.endswith("submissionview.php") and q("id").isdigit() and len(q("id")) >= 3:
            page = q("page")
            return "ik:" + q("id"), int(page) if page.isdigit() else 1
        return None
    if has("deviantart.com"):
        m = re.search(r"/art/(?:[^/]*-)?(\d{4,})/?$", path) or re.match(r"^/(?:deviation|view)/(\d{4,})", path)
        return ("da:" + m.group(1), None) if m else None
    if host == "fav.me":
        m = re.match(r"^/d([0-9a-z]{3,8})$", path, re.I)
        return ("da:" + str(int(m.group(1), 36)), None) if m else None
    if host.endswith("wixmp.com") or host.endswith("deviantart.net"):
        m = re.search(r"[/-]d([0-9a-z]{5,7})(?:-[0-9a-f-]{36})?(?:-pre|-fullview|-375w|-414w)?\.\w+$", path, re.I)
        return ("da:" + str(int(m.group(1), 36)), None) if m else None
    if has("tumblr.com"):
        m = re.match(r"^/(?:post|image)/(\d{6,})", path)
        if not m and host == "tumblr.com":
            m = re.match(r"^/(?:blog/view/)?[a-z0-9-]+/(\d{6,})", path, re.I)
        return ("tb:" + m.group(1), None) if m else None
    if re.fullmatch(r"nhentai\.(net|to|xxx)", host):
        m = re.match(r"^/g/(\d{2,})(?:/(\d+))?", path)
        return ("nh:" + m.group(1), int(m.group(2)) if m.group(2) else None) if m else None
    if re.fullmatch(r"(e-hentai|exhentai)\.org", host):
        m = re.match(r"^/g/(\d{3,})/", path)
        if m:
            return "eh:" + m.group(1), None
        m = re.match(r"^/s/[0-9a-f]{6,}/(\d{3,})-(\d+)", path)
        return ("eh:" + m.group(1), int(m.group(2))) if m else None
    if host == "hitomi.la":
        m = re.search(r"(\d{3,})\.html$", path)
        page = re.match(r"^(\d+)$", url.fragment or "")
        return ("hi:" + m.group(1), int(page.group(1)) if page else None) if m else None
    if host == "imgur.com":
        m = re.match(r"^/(?:a|gallery)/(?:[^/]*-)?([A-Za-z0-9]{5,8})/?$", path)
        return ("ia:" + m.group(1), None) if m else None
    if host == "bsky.app":
        m = re.match(r"^/profile/[^/]+/post/([a-z2-7]{10,16})", path)
        return ("bs:" + m.group(1), None) if m else None
    if has("reddit.com") or host == "redd.it":
        m = (re.search(r"/(?:comments|gallery)/([a-z0-9]{4,10})(?:/|$)", path, re.I) if has("reddit.com")
             else re.match(r"^/([a-z0-9]{4,10})$", path, re.I))
        return ("rd:" + m.group(1).lower(), None) if m else None
    if host == "nijie.info":
        if re.match(r"^/view(?:_popup)?\.php$", path) and q("id").isdigit() and len(q("id")) >= 3:
            return "nj:" + q("id"), None
        return None
    if has("hentai-foundry.com"):
        m = re.match(r"^/pictures/user/[^/]+/(\d{3,})", path) or re.search(r"/[^/]+-(\d{3,})-[^/]*\.\w+$", path)
        return ("hf:" + m.group(1), None) if m else None
    if has("newgrounds.com"):
        m = re.match(r"^/art/view/([^/]+)/([^/?#]+)", path)
        return (f"ng:{m.group(1).lower()}/{m.group(2).lower()}", None) if m else None
    if host == "art.ngfiles.com" or re.fullmatch(r"cdn[a-z]?\.artstation\.com", host):
        return False
    if has("artstation.com"):
        m = re.match(r"^/artwork/([A-Za-z0-9]{4,12})/?$", path)
        return ("as:" + m.group(1), None) if m else None
    # pixiv and tweets (the reference client's parseSource)
    if host.endswith("pximg.net") or host.endswith("pixiv.net"):
        m = re.search(r"/(\d{5,})_p(\d+)", path)
        if m:
            return "px:" + m.group(1), int(m.group(2))
        m = re.search(r"/(\d{5,})_ugoira", path)
        if m:
            return "px:" + m.group(1), None
        if host.endswith("pixiv.net"):
            m = re.search(r"/artworks/(\d{5,})", path)
            if m:
                return "px:" + m.group(1), None
            if q("illust_id").isdigit() and len(q("illust_id")) >= 5:
                return "px:" + q("illust_id"), None
        return None
    if re.search(r"(^|\.)(twitter|x|fxtwitter|vxtwitter|fixupx|nitter)\.(com|net|poast\.org)$", host) or host.startswith("nitter."):
        m = re.search(r"/status(?:es)?/(\d{6,})(?:/(?:photo|video)/(\d))?", path)
        return ("ts:" + m.group(1), int(m.group(2)) if m and m.group(2) else None) if m else None
    if host == "pbs.twimg.com":
        return None
    if _MD5_FILE.search(path) or re.search(r"/(?:thread|res)/(\d{4,})", path):
        return None                                   # one picture, or a thread of an imageboard: not a work
    if _PROFILE.search(path):
        return None
    m = re.search(r"(\d{5,})", path)
    if m:
        return "u:" + host + re.sub(r"/+$", "", path).lower()[:160], None
    m = re.search(r"[?&](id|post|illust_id|p|submission|work)=(\d{5,})", "?" + query, re.I)
    if m:
        return f"u:{host}{re.sub(r'/+$', '', path).lower()[:160]}?{m.group(1).lower()}={m.group(2)}", None
    return None


def _platform(key: str) -> str:
    """The site of a work key (two keys of one site — two works of that site)."""
    if key.startswith("u:"):
        return "u:" + key[2:].split("/", 1)[0]
    return key.split(":", 1)[0]


_DC_STEM = re.compile(r"[A-Za-z0-9_-]{5,80}")
_SEARCHED = {"px": 6, "ts": 6, "pt": 5, "fb": 4, "ik": 3, "tb": 6}    # a work's number on a site: digits at least


def _dc_stem(name: str) -> str:
    """The base of a Discord attachment's name as the links carry it (``SPOILER_LyaxWerewolf4.png`` → ``LyaxWerewolf``),
    "" — none to look for."""
    stem = re.sub(r"\.[a-z0-9]{2,5}$", "", unquote(str(name or "")), flags=re.I)
    stem = re.sub(r"^SPOILER_", "", stem, flags=re.I)
    m = re.match(r"^(.*?)[\s_.-]*(\d{1,3})$", stem)
    if m and m.group(1):
        stem = m.group(1)
    return stem if _DC_STEM.fullmatch(stem) else ""


def work_query(link: str):
    """(work key, what a site's ``source:*…*`` looks for) of a link, or None — a link whose work is not looked for on
    the sites (the reference client's ``workQueries``: the name of a Discord series, the number of a pixiv work, a tweet, a Patreon /
    Fanbox / Inkbunny / Tumblr post)."""
    found = work_key(link)
    if not found:
        return None
    key = found[0]
    platform, _sep, rest = key.partition(":")
    if platform == "dc":
        m = re.search(r"/attachments/\d+/\d+/([^/?#]+)", str(link))
        stem = _dc_stem(m.group(1)) if m else ""
        return (key, stem) if stem else None
    if platform == "km":
        number = rest.rpartition(":")[2]
        return (key, number) if re.fullmatch(r"\d{5,}", number) else None
    least = _SEARCHED.get(platform)
    if least and rest.isdigit() and len(rest) >= least:
        return key, rest
    return None


_FEW = ("ts:", "bs:")                              # a tweet, a bluesky post: four pictures at most
_ONE_FILE = ("fa:", "da:", "hf:")


# ── the cards as the builder sees them ─────────────────────────────────────────────────
class _Card:
    __slots__ = ("id", "w", "h", "tags", "artists", "chars", "comic", "ai", "keys", "pages", "site_tags")

    def __init__(self, image_id: int, width: int, height: int):
        self.id = image_id
        self.w, self.h = int(width or 0), int(height or 0)
        self.tags: frozenset = frozenset()
        self.artists: frozenset = frozenset()
        self.chars: frozenset = frozenset()
        self.comic = self.ai = False
        self.keys: list = []                       # work keys
        self.pages: dict = {}                      # work key -> page number
        self.site_tags: dict = {}                  # site -> the tags that site itself gave the card (v5.44.10)


class _Post:
    """One post of one site of a card: what the reference client calls a post (``id`` — its number on that site)."""
    __slots__ = ("card", "site", "id", "parent", "owner")

    def __init__(self, card: _Card, site: str, post_id: int, parent: int, owner: str):
        self.card, self.site, self.id, self.parent, self.owner = card, site, int(post_id), int(parent or 0), owner


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 0.0
    n = len(a & b)
    return n / max(1, len(a) + len(b) - n)


def _kin(a: _Post, b: _Post) -> bool:
    return bool((a.parent and (a.parent == b.id or a.parent == b.parent)) or (b.parent and b.parent == a.id))


def _same_family(a: _Post, b: _Post) -> bool:
    return not (a.parent or b.parent) or (a.parent or a.id) == (b.parent or b.id)


def _diff_work(a: _Card, b: _Card) -> bool:
    """Links to two works of one site: surely not one work."""
    if not a.keys or not b.keys or set(a.keys) & set(b.keys):
        return False
    return bool({_platform(k) for k in a.keys} & {_platform(k) for k in b.keys})


def _seq_link(a: _Post, b: _Post) -> bool:
    """A parent / a child, or the same work (a tweet's pictures uploaded within :data:`FEW_IMG_SPAN` numbers)."""
    if _kin(a, b):
        return True
    shared = set(a.card.keys) & set(b.card.keys)
    return any(not k.startswith(_FEW) or abs(a.id - b.id) <= FEW_IMG_SPAN for k in shared)


def _compared_tags(a: _Post, b: _Post) -> tuple:
    """(tags of a, tags of b, artists of a, artists of b) the two posts are compared by: what their site gave each card
    when both have it — the reference client compares the posts of one site, and a card's tags are of every site that
    has its file (one card with three sites against one with a single site were «unlike»: v5.44.10); else the cards'."""
    ta = a.card.site_tags.get(a.site) if a.card.site_tags else None
    tb = b.card.site_tags.get(b.site) if b.card.site_tags else None
    if ta and tb:
        return ta, tb, a.card.artists & ta, b.card.artists & tb
    return a.card.tags, b.card.tags, a.card.artists, b.card.artists


def link_score(a: _Post, b: _Post) -> int:
    """Points of «one work» of two posts of one uploader (the reference client's ``linkScore``)."""
    ca, cb = a.card, b.card
    ta, tb, aa, ab = _compared_tags(a, b)
    score = _jaccard(ta, tb) * 100
    if aa and ab:
        score += PTS["artist"] if aa & ab else PTS["artist_other"]
    if ca.w > 0 and ca.h > 0 and cb.w > 0 and cb.h > 0:
        if ca.w == cb.w and ca.h == cb.h:
            score += PTS["size"]
        elif abs(ca.w / ca.h - cb.w / cb.h) < 0.02:
            score += PTS["aspect"]
    gap = abs(a.id - b.id)
    if gap > GAP_FAR and ca.ai and cb.ai:
        return 0
    score += PTS["gap_near"] if gap <= GAP_NEAR else PTS["gap_close"] if gap <= GAP_CLOSE else PTS["gap_far"] if gap > GAP_FAR else 0
    if _diff_work(ca, cb):
        return 0
    return round(score)


def _tag_link(a: _Post, b: _Post) -> bool:
    return link_score(a, b) >= LINK


def _near_px(x: int, y: int) -> bool:
    return abs(x - y) <= max(2, min(x, y) * 0.01)


def _same_canvas(a: _Card, b: _Card, scaled: bool) -> bool:
    if not (a.w > 0 and a.h > 0 and b.w > 0 and b.h > 0):
        return False
    if _near_px(a.w, b.w) and _near_px(a.h, b.h):
        return True
    return scaled and abs(a.w / a.h - b.w / b.h) <= 0.003 * (a.w / a.h)


def _comic_kin(a: _Post, b: _Post, scaled: bool | None = None) -> bool:
    ca, cb = a.card, b.card
    if not ca.comic or not cb.comic or ca.ai or cb.ai:
        return False
    if not _same_canvas(ca, cb, abs(a.id - b.id) <= SCALED_GAP if scaled is None else scaled):
        return False
    if not ca.artists or not ca.artists & cb.artists:
        return False
    return not _diff_work(ca, cb)


def _foreign_child(pages: list, c: _Post) -> bool:
    return bool(c.parent) and not any(_kin(m, c) for m in pages)


def _comic_next(pages: list, last: _Post, first: _Post, c: _Post) -> bool:
    if not _comic_kin(last, c) or not _comic_kin(first, c, True):
        return False
    have = set()
    for p in pages:
        have |= p.card.chars
    return bool(c.card.chars & have) or _jaccard(last.card.tags, c.card.tags) >= 0.5


def _comic_fits(pages: list, c: _Post) -> bool:
    if not c.card.comic or _foreign_child(pages, c):
        return False
    near, first = None, pages[0]
    for p in pages:
        if p.id < first.id:
            first = p
        if _comic_kin(p, c) and (near is None or abs(p.id - c.id) < abs(near.id - c.id)):
            near = p
    return near is not None and _comic_next(pages, near, first, c)


# ── union of cards ─────────────────────────────────────────────────────────────────────
class _Union:
    def __init__(self):
        self.up: dict = {}
        self.level: dict = {}                       # root -> the weakest link inside (0 parent, 1 work, 2 series)

    def find(self, x: int) -> int:
        up = self.up
        up.setdefault(x, x)
        root = x
        while up[root] != root:
            root = up[root]
        while up[x] != root:
            up[x], x = root, up[x]
        return root

    def join(self, a: int, b: int, level: int) -> None:
        ra, rb = self.find(a), self.find(b)
        lv = max(level, self.level.get(ra, 0), self.level.get(rb, 0))
        if ra != rb:
            self.up[ra] = rb
        self.level[rb] = lv

    def groups(self) -> dict:
        out: dict = {}
        for x in list(self.up):
            out.setdefault(self.find(x), []).append(x)
        return out


def _fold(seq: list, union: _Union) -> None:
    """The posts of one uploader on one site (sorted by number) folded the way the reference client walks them (``autoSet`` over
    what is loaded, ``foldLocal``): from each post not taken yet, the next page is a post linked to the last page found —
    a link of the same work, a parent, or alike by tags (a neighbour of the same family within :data:`LOCAL_GAP`
    numbers, :data:`LINK` points, and a little like the first page — :data:`ANCHOR`); up to :data:`MISS` other posts of
    the uploader may lie between two pages (:data:`MISS_WORK` between the parts of one work).  Then the pages of a
    comic over other posts of the uploader."""
    units: list = []
    used = [False] * len(seq)
    for i, first in enumerate(seq):
        if used[i]:
            continue
        used[i] = True
        chain, last, miss = [first], first, 0
        for j in range(i + 1, len(seq)):
            if used[j]:
                continue
            if len(chain) >= SET_MAX:
                break
            c = seq[j]
            level = -1
            if not _foreign_child(chain, c):
                if _seq_link(last, c):
                    level = 0 if _kin(last, c) else 1
                elif (miss <= MISS and _same_family(last, c) and c.id - last.id <= LOCAL_GAP and _tag_link(last, c)
                      and (len(chain) < 2 or link_score(first, c) >= ANCHOR)):
                    level = 2
            if level < 0:
                miss += 1
                if miss > MISS and not (last.card.keys or last.parent):
                    break                                # nothing but tags to go on: the series is over
                if miss > MISS_WORK:
                    break
                continue
            union.join(last.card.id, c.card.id, level)
            chain.append(c)
            used[j] = True
            last, miss = c, 0
        units.append(chain)
    units.sort(key=lambda u: u[0].id)
    # the pages of a comic over other posts of the uploader (and other parts of it found by links and tags)
    taken: set = set()
    for i in range(len(units)):
        if i in taken or not units[i][0].card.comic:
            continue
        pages, miss = units[i], 0
        for j in range(i + 1, len(units)):
            if j in taken:
                continue
            v = units[j]
            if len(pages) + len(v) <= SET_MAX and _comic_fits(pages, v[0]):
                union.join(pages[0].card.id, v[0].card.id, 2)
                pages = pages + v
                taken.add(j)
                miss = 0
            else:
                miss += len(v)
                if miss > MISS_COMIC:
                    break
        units[i] = pages
    # a page passed on the way (only a character not found yet) — to the comic it fits now, the smaller to the larger
    comics = [k for k, c in enumerate(units) if k not in taken and len(c) >= 2 and any(p.card.comic for p in c)]
    for i in range(len(units)):
        u = units[i]
        if i in taken or not u[0].card.comic:
            continue
        for k in comics:
            c = units[k]
            if (k != i and k not in taken and len(c) >= max(2, len(u)) and len(c) + len(u) <= SET_MAX
                    and _comic_fits(c, u[0])):
                union.join(c[0].card.id, u[0].card.id, 2)
                units[k] = sorted(c + u, key=lambda p: p.id)
                taken.add(i)
                break


# ── reading the library ────────────────────────────────────────────────────────────────
def _chunks(values: list, size: int = 800):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def pool_key(site: str, pool_id: int) -> str:
    """(v5.44.111) The work key of a pool of a site («pool:e621.net:15766»)."""
    return f"pool:{site}:{int(pool_id)}"


def _series_pools(con) -> dict:
    """(v5.44.111) (site, pool) -> {post id: its place} of the pools the sites called «series» (a comic)."""
    out: dict = {}
    try:
        rows = con.execute("SELECT site, pool_id, post_ids FROM site_pools WHERE state='ok' AND category='series'")
        for site, pool_id, post_ids in rows:
            try:
                numbers = json.loads(post_ids or "[]")
            except ValueError:
                numbers = []
            out[(str(site), int(pool_id))] = {int(n): k for k, n in enumerate(numbers) if str(n).isdigit()}
    except Exception as exc:  # an old base without the pools: nothing from there
        _LOG.debug("pools not read: %s", exc)
    return out


def _load(con) -> dict:
    """What the build needs: the live picture cards, their posts, work keys and (of the uploaders' cards) tags."""
    cards: dict = {}
    video_ends = tuple(VIDEO_EXTS)
    for image_id, width, height, path, is_video in con.execute(
            "SELECT id, width, height, path, is_video FROM images WHERE deleted=0"):
        path = str(path or "")
        if is_video or path.lower().endswith(video_ends):
            continue
        card = cards[image_id] = _Card(image_id, width, height)
        if "_p" in path:
            m = _PIXIV_FILE.search(path)
            if m:                                   # pixiv's own name of a page: 123456_p0.png
                key = "px:" + m.group(1)
                card.keys.append(key)
                card.pages[key] = int(m.group(2))
    posts: list = []
    links: dict = {}                                # image id -> [links of its posts and of the card]
    series = _series_pools(con)
    for image_id, site, post_id, parent, owner, source, pools in con.execute(
            "SELECT image_id, site, post_id, parent_id, uploader, source, pools FROM site_posts WHERE state='ok'"):
        card = cards.get(int(image_id))
        if card is None:
            continue
        posts.append(_Post(card, str(site), int(post_id), int(parent or 0), str(owner or "")))
        if source:
            links.setdefault(card.id, []).extend(str(source).split("\n"))
        for text in str(pools or "").split(","):    # (v5.44.111) a comic of e621: its pool, the page's place in it
            places = series.get((str(site), int(text))) if text.isdigit() else None
            if places is None:
                continue
            key = pool_key(str(site), int(text))
            if key not in card.keys:
                card.keys.append(key)
            card.pages.setdefault(key, places.get(int(post_id), 100000 + int(post_id)))
    hosts = [str(r[0]) for r in con.execute("SELECT DISTINCT host FROM sources")
             if r[0] and not any(mark in str(r[0]).lower() for mark in _BOORU_HOSTS)]
    for part in _chunks(hosts, 400):
        marks = ",".join("?" * len(part))
        for image_id, url in con.execute(f"SELECT x.image_id, s.url FROM image_sources x JOIN sources s ON s.id=x.source_id "
                                         f"WHERE s.host IN ({marks})", part):
            if int(image_id) in cards:
                links.setdefault(int(image_id), []).append(str(url or ""))
    for image_id, urls in links.items():
        card = cards[image_id]
        for url in urls[:24]:
            found = work_key(url)
            if not found:
                continue
            key, page = found
            if key not in card.keys:
                card.keys.append(key)
            if page is not None and key not in card.pages:
                card.pages[key] = int(page)
    try:                                            # the pages of a family the browser extension brought
        for image_id, family_id, index in con.execute(
                "SELECT i.image_id, i.family_id, i.family_index FROM media_family_items i "
                "JOIN media_families f ON f.family_id=i.family_id "
                "WHERE i.image_id IS NOT NULL AND f.family_type<>'multi_attachment'"):
            card = cards.get(int(image_id or 0))
            if card is not None:
                key = f"mf:{int(family_id)}"
                card.keys.append(key)
                card.pages.setdefault(key, int(index or 0))
    except Exception as exc:  # an old base without families: nothing from there
        _LOG.debug("families not read: %s", exc)
    try:                                            # (v5.44.125) a folder of the parser found «not a manga»: one work
        from core.parser_manga import folder_cards
        for image_id, key, place in folder_cards(con):
            card = cards.get(int(image_id))
            if card is not None and key not in card.keys:
                card.keys.append(key)
                card.pages[key] = int(place)
    except Exception as exc:  # noqa: BLE001 - what the parser's folder holds unread: no such сборки this time
        _LOG.debug("the parser's folders not read: %s", exc)
    sets = {int(r[0]): {"kind": str(r[1] or ""), "locked": bool(r[2]), "title": str(r[3] or "")}
            for r in con.execute("SELECT set_id, kind, locked, title FROM card_sets")}
    members = {int(r[0]): (int(r[1]), int(r[2])) for r in con.execute("SELECT image_id, set_id, position FROM card_set_members")}
    optout = {int(r[0]) for r in con.execute("SELECT image_id FROM card_set_optout")}
    return {"cards": cards, "posts": posts, "sets": sets, "members": members, "optout": optout}


def _load_tags(con, cards: dict, ids: list) -> None:
    """Tags of the cards that are compared by tags: ids, the artists, the characters, comic and AI marks (joined by
    SQLite per card: 2.6 million tag links of 100 thousand cards in a few seconds)."""
    from core.site_posts import PAGE_METHODS, canonical_site
    sites: dict = {}                                # a source's host -> its site as ``site_posts`` names it
    names = sorted(_COMIC | _AI)
    marks = ",".join("?" * len(names))
    comic_ids, ai_ids = set(), set()
    for tag_id, name in con.execute(f"SELECT id, LOWER(normalized_name) FROM tags WHERE LOWER(normalized_name) IN ({marks})", names):
        (comic_ids if name in _COMIC else ai_ids).add(int(tag_id))

    def numbers(text) -> frozenset:
        return frozenset(map(int, str(text).split(","))) if text else frozenset()
    for part in _chunks(ids):
        marks = ",".join("?" * len(part))
        for image_id, joined in con.execute(f"SELECT image_id, group_concat(tag_id) FROM image_tags "
                                            f"WHERE image_id IN ({marks}) GROUP BY image_id", part):
            card = cards[image_id]
            card.tags = numbers(joined)
            card.comic = not card.tags.isdisjoint(comic_ids)
            card.ai = not card.tags.isdisjoint(ai_ids)
        for category, slot in (("artist", "artists"), ("character", "chars")):
            for image_id, joined in con.execute(
                    f"SELECT it.image_id, group_concat(it.tag_id) FROM image_tags it JOIN tags t ON t.id=it.tag_id "
                    f"WHERE t.category=? AND it.image_id IN ({marks}) GROUP BY it.image_id", [category, *part]):
                setattr(cards[image_id], slot, numbers(joined))
        # (v5.44.10) the tags each site itself gave the card — not what the browser extension saw on a page
        pages = ",".join("?" * len(PAGE_METHODS))
        for image_id, host, joined in con.execute(
                f"SELECT ist.image_id, s.host, group_concat(ist.tag_id) FROM image_source_tags ist "
                f"JOIN sources s ON s.id=ist.source_id WHERE ist.image_id IN ({marks}) "
                f"AND ist.acquisition NOT IN ({pages}) GROUP BY ist.image_id, s.host", [*part, *PAGE_METHODS]):
            host = str(host or "")
            site = sites.get(host)
            if site is None:
                site = sites[host] = canonical_site(host)
            card = cards[image_id]
            card.site_tags[site] = card.site_tags.get(site, frozenset()) | numbers(joined)


# ── building ───────────────────────────────────────────────────────────────────────────
def compute(con) -> dict:
    """What the сборки should be: {"auto": [(kind, [image ids in order])], "extend": {locked set id: [image ids]}}."""
    data = _load(con)
    cards, sets, members, optout = data["cards"], data["sets"], data["members"], data["optout"]
    # cards of a сборка the person made are not the program's; those the person took out never go in again
    manual = {image_id for image_id, (set_id, _pos) in members.items()
              if sets.get(set_id, {}).get("kind") == "manual"}
    out_of_play = optout | manual
    posts = [p for p in data["posts"] if p.card.id not in out_of_play]
    for card_id in list(cards):
        if card_id in out_of_play:
            cards.pop(card_id)
    # a link of a tweet / a bluesky post on too many cards is the artist's everywhere-link
    count: dict = {}
    for card in cards.values():
        for key in card.keys:
            count[key] = count.get(key, 0) + 1
    for card in cards.values():
        if any(k.startswith(_FEW) and count[k] > FEW_IMG_MAX for k in card.keys):
            card.keys = [k for k in card.keys if not (k.startswith(_FEW) and count[k] > FEW_IMG_MAX)]
    # the pages of comics are known before their tags are read (comics are folded over other cards)
    names = sorted(_COMIC)
    marks = ",".join("?" * len(names))
    for (image_id,) in con.execute(f"SELECT it.image_id FROM image_tags it JOIN tags t ON t.id=it.tag_id "
                                   f"WHERE LOWER(t.normalized_name) IN ({marks})", names):
        card = cards.get(image_id)
        if card is not None:
            card.comic = True
    # the uploaders' sequences (one site each) — their cards are compared by tags, read a few thousand at a time
    seqs: dict = {}
    for p in posts:
        if p.owner:
            seqs.setdefault((p.site, p.owner), []).append(p)
    union = _Union()
    batch: list = []
    size = 0
    for seq in [s for s in seqs.values() if len(s) >= 2]:
        seq.sort(key=lambda p: (p.id, p.card.id))
        unique, seen = [], set()
        for p in seq:
            if p.card.id not in seen:
                seen.add(p.card.id)
                unique.append(p)
        if len(unique) < 2:
            continue
        batch.append(unique)
        size += len(unique)
        if size >= TAG_BATCH:
            _fold_batch(con, cards, batch, union)
            batch, size = [], 0
    if batch:
        _fold_batch(con, cards, batch, union)
    # a parent and its children (any uploader), on every site
    families: dict = {}
    for p in posts:
        families.setdefault((p.site, p.parent or p.id), []).append(p.card.id)
    for ids in families.values():
        for other in ids[1:]:
            union.join(ids[0], other, 0)
    # one work by its links (any uploader, any site)
    by_key: dict = {}
    for card in cards.values():
        for key in card.keys:
            by_key.setdefault(key, []).append(card)
    posts_of: dict = {}
    for p in posts:
        posts_of.setdefault(p.card.id, []).append(p)
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        if key.startswith(_FEW):                    # a tweet's pictures are uploaded together
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    if _near_on_sites(posts_of.get(a.id, []), posts_of.get(b.id, [])):
                        union.join(a.id, b.id, 1)
            continue
        for other in group[1:]:
            union.join(group[0].id, other.id, 1)
    # the сборки: a group with a card of a сборка the person changed grows that сборка; the rest are the program's
    auto, extend = [], {}
    for root, ids in union.groups().items():
        ids = [i for i in ids if i in cards]
        if len(ids) < 2:
            continue
        locked = {members[i][0] for i in ids if i in members and sets.get(members[i][0], {}).get("locked")}
        free = [i for i in ids if not (i in members and sets.get(members[i][0], {}).get("locked"))]
        level = union.level.get(root, 0)
        if locked:
            if len(locked) == 1 and free:
                extend.setdefault(next(iter(locked)), []).extend(_ordered(free, cards, posts_of, KINDS[level]))
            continue
        for start in range(0, len(ids), SET_MAX):    # a group larger than a сборка holds: in parts, in order
            part = _ordered(ids, cards, posts_of, KINDS[level])[start:start + SET_MAX]
            if len(part) >= 2:
                auto.append((KINDS[level], part))
    return {"auto": auto, "extend": extend, "members": members, "sets": sets}


def _fold_batch(con, cards: dict, batch: list, union: _Union) -> None:
    """Fold the sequences of ``batch``, the tags of their cards read first — only of the cards that are compared by
    tags: a neighbour within :data:`LOCAL_GAP` numbers, a page of a comic, a card with a link of a work or a parent."""
    need = set()
    for seq in batch:
        for n, p in enumerate(seq):
            if (p.card.comic or p.card.keys or p.parent or (n and p.id - seq[n - 1].id <= LOCAL_GAP)
                    or (n + 1 < len(seq) and seq[n + 1].id - p.id <= LOCAL_GAP)):
                need.add(p.card.id)
    ids = sorted(need)
    _load_tags(con, cards, ids)
    for seq in batch:
        _fold(seq, union)
    for i in ids:                                    # not kept: the next batch reads its own (the comic mark stays)
        card = cards[i]
        card.tags = card.artists = card.chars = frozenset()
        card.site_tags = {}


def _near_on_sites(a_posts: list, b_posts: list) -> bool:
    for a in a_posts:
        for b in b_posts:
            if a.site == b.site and abs(a.id - b.id) > FEW_IMG_SPAN:
                return False
    return True


def _ordered(ids: list, cards: dict, posts_of: dict, kind: str) -> list:
    """The order of a сборка: the pages of one work by their numbers in the links, when all have one; else by the
    number of the posts on the site most of them are on (a parent first), the rest by the card's number."""
    keys = [cards[i].pages for i in ids]
    common = set(keys[0]) if keys else set()
    for pages in keys[1:]:
        common &= set(pages)
    for key in sorted(common):
        numbers = [cards[i].pages[key] for i in ids]
        if len(set(numbers)) > 1:
            return [i for _n, i in sorted(zip(numbers, ids), key=lambda x: (x[0], _first_post(posts_of, x[1]), x[1]))]
    sites: dict = {}
    for i in ids:
        for p in posts_of.get(i, []):
            sites[p.site] = sites.get(p.site, 0) + 1
    main = min(sites, key=lambda s: (-sites[s], s)) if sites else ""
    roots = set()
    if kind == "parent" and main:
        for i in ids:
            for p in posts_of.get(i, []):
                if p.site == main and p.parent:
                    roots.add(p.parent)

    def key(i: int):
        on_main = [p for p in posts_of.get(i, []) if p.site == main]
        if not on_main:
            return (2, 0, i)
        number = min(p.id for p in on_main)
        return (0 if any(p.id in roots for p in on_main) else 1, number, i)
    return sorted(ids, key=key)


def _first_post(posts_of: dict, image_id: int) -> int:
    numbers = [p.id for p in posts_of.get(image_id, [])]
    return min(numbers) if numbers else 0


# ── writing ────────────────────────────────────────────────────────────────────────────
def _alive(con, ids) -> set:
    found = set()
    ordered = sorted(set(ids))
    for part in _chunks(ordered):
        marks = ",".join("?" * len(part))
        found |= {int(r[0]) for r in con.execute(f"SELECT id FROM images WHERE id IN ({marks})", part)}
    return found


def _apply(con, plan: dict, now: int) -> dict:
    """Write the plan inside the caller's write: the program's сборки keep their numbers (each takes the one it shares
    most cards with); only what changed is written.  The base is read again here — a card the person put into a
    сборка or took out meanwhile is left as the person left it."""
    sets = {int(r[0]): {"kind": str(r[1] or ""), "locked": bool(r[2])}
            for r in con.execute("SELECT set_id, kind, locked FROM card_sets")}
    members = {int(r[0]): (int(r[1]), int(r[2])) for r in con.execute("SELECT image_id, set_id, position FROM card_set_members")}
    optout = {int(r[0]) for r in con.execute("SELECT image_id FROM card_set_optout")}
    held = {i for i, (sid, _p) in members.items() if sets.get(sid, {}).get("locked")}
    current: dict = {}                                 # the program's сборки now: set id -> cards
    for image_id, (set_id, _pos) in members.items():
        if set_id in sets and not sets[set_id]["locked"]:
            current.setdefault(set_id, set()).add(image_id)
    planned = [i for _kind, ids in plan["auto"] for i in ids] + [i for ids in plan["extend"].values() for i in ids]
    alive = _alive(con, planned)
    want: dict = {}                                    # image id -> (set id, position)
    used: set = set()
    created = changed = 0
    for kind, ids in sorted(plan["auto"], key=lambda x: -len(x[1])):
        ids = [i for i in ids if i in alive and i not in held and i not in optout]
        if len(ids) < 2:
            continue
        votes: dict = {}
        for i in ids:
            sid = members.get(i, (0, 0))[0]
            if sid in current and sid not in used:
                votes[sid] = votes.get(sid, 0) + 1
        set_id = max(votes, key=lambda x: (votes[x], -x)) if votes else 0
        if set_id:
            used.add(set_id)
            if sets[set_id]["kind"] != kind:
                con.execute("UPDATE card_sets SET kind=?, updated_at=? WHERE set_id=?", (kind, now, set_id))
                changed += 1
        else:
            set_id = int(con.execute("INSERT INTO card_sets(kind, title, locked, created_at, updated_at) VALUES(?,?,0,?,?)",
                                     (kind, "", now, now)).lastrowid)
            created += 1
        for pos, i in enumerate(ids):
            want[i] = (set_id, pos)
    for set_id, extra in plan["extend"].items():
        if not sets.get(set_id, {}).get("locked"):
            continue                                   # dissolved meanwhile
        extra = [i for i in extra if i in alive and i not in held and i not in optout and i not in want]
        if not extra:
            continue
        top = con.execute("SELECT COALESCE(MAX(position),-1) FROM card_set_members WHERE set_id=?", (set_id,)).fetchone()[0]
        for n, i in enumerate(extra):
            want[i] = (set_id, int(top) + 1 + n)
        con.execute("UPDATE card_sets SET updated_at=? WHERE set_id=?", (now, set_id))
    moved = 0
    for image_id, (set_id, _pos) in members.items():  # cards that left the program's сборки (their group, the trash)
        if set_id in current and image_id not in want:
            con.execute("DELETE FROM card_set_members WHERE image_id=? AND set_id=?", (image_id, set_id))
            moved += 1
    for image_id, (set_id, pos) in want.items():
        if members.get(image_id) != (set_id, pos):
            con.execute("INSERT OR REPLACE INTO card_set_members(image_id, set_id, position) VALUES(?,?,?)",
                        (image_id, set_id, pos))
            moved += 1
    removed = 0
    for set_id in current:
        if set_id not in used:
            con.execute("DELETE FROM card_sets WHERE set_id=?", (set_id,))
            removed += 1
    # a сборка left with fewer than two cards (the others deleted for good) is no сборка
    for (set_id,) in con.execute("SELECT s.set_id FROM card_sets s WHERE "
                                 "(SELECT COUNT(*) FROM card_set_members m WHERE m.set_id=s.set_id) < 2").fetchall():
        con.execute("DELETE FROM card_sets WHERE set_id=?", (int(set_id),))
        removed += 1
    return {"sets": len(used) + created, "created": created, "removed": removed, "changed": changed, "moved": moved}


# ── when to build ──────────────────────────────────────────────────────────────────────
_BUILD_LOCK = threading.Lock()
_SIGNATURE_KEY = "card_sets_built"


def auto_enabled(settings: dict) -> bool:
    """«Настройки → Галерея → Собирать сборки самой»."""
    return bool((settings or {}).get("card_sets_auto", True))


def tiles_enabled(settings: dict) -> bool:
    """«Настройки → Галерея → Сборка в галерее — одной плиткой»."""
    return bool((settings or {}).get("card_sets_tiles", True))


def _library_signature(con) -> list:
    parts = [BUILD_VERSION]
    for sql in ("SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(SUM(deleted),0) FROM images",
                "SELECT COUNT(*), COALESCE(MAX(checked_at),0) FROM site_posts",
                "SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM image_sources"):
        parts.extend(con.execute(sql).fetchone())
    try:                                             # (v5.44.111) a pool described: its cards are one сборка
        parts.extend(con.execute("SELECT COUNT(*), COALESCE(MAX(checked_at),0) FROM site_pools").fetchone())
    except Exception:  # an old base without the pools
        parts.extend([0, 0])
    # (v5.44.10) the cards' tags changed (a site's tags mended, a card tagged again): its сборка is looked at again —
    # the counter the base's triggers move on every change of the tags of a card (migration 026)
    try:
        row = con.execute("SELECT revision FROM derived_source_revision WHERE cache_name='image_effective_tag_category'"
                          ).fetchone()
        parts.append(int(row[0] or 0) if row else 0)
    except Exception:  # an old base without the counter: as before, the tags are not in the signature
        parts.append(0)
    try:                                             # (v5.44.125) the parser's folders found «not a manga»
        from core.parser_manga import signature
        parts.extend(signature())
    except Exception:  # noqa: BLE001
        parts.extend([0, 0])
    return parts


def _edits_signature(con) -> list:
    """What the person changes: the сборки of the person's (made, changed) and the cards taken out."""
    parts = list(con.execute("SELECT COUNT(*), COALESCE(MAX(updated_at),0), COALESCE(SUM(set_id),0) FROM card_sets "
                             "WHERE locked=1").fetchone())
    parts.extend(con.execute("SELECT COUNT(*), COALESCE(SUM(image_id),0) FROM card_set_optout").fetchone())
    parts.extend(con.execute("SELECT COUNT(*), COALESCE(SUM(m.image_id),0) FROM card_set_members m "
                             "JOIN card_sets s ON s.set_id=m.set_id WHERE s.locked=1").fetchone())
    return parts


def _drop_auto(settings: dict) -> dict:
    """«Собирать сборки самой» switched off: the program's сборки go (the person's stay); switched on again, they are
    built anew."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        if not con.execute("SELECT 1 FROM card_sets WHERE locked=0 LIMIT 1").fetchone():
            return {"skipped": True}
    with db(settings, write=True) as con:
        removed = con.execute("DELETE FROM card_sets WHERE locked=0").rowcount
        con.execute("DELETE FROM meta WHERE key=?", (_SIGNATURE_KEY,))
    _LOG.info("сборки: %d of the program's taken apart («Собирать сборки самой» is off)", removed)
    return {"skipped": True, "removed": int(removed or 0)}


def rebuild(settings: dict, *, force: bool = False, wait: float = 0.0) -> dict:
    """Put the сборки together again when the library or the person's сборки changed since the last time (``force`` —
    anyway).  {"skipped": True} — nothing changed / switched off / another build is going (``wait`` — seconds to wait
    for it); else what changed."""
    from core.database.connection import db, writes_blocked
    if writes_blocked():
        return {"skipped": True}
    if not (_BUILD_LOCK.acquire(timeout=wait) if wait > 0 else _BUILD_LOCK.acquire(blocking=False)):
        return {"skipped": True}
    try:
        if not auto_enabled(settings):
            return _drop_auto(settings)
        started = time.monotonic()
        with db(settings, readonly=True) as con:
            library, edits = _library_signature(con), _edits_signature(con)
            row = con.execute("SELECT value FROM meta WHERE key=?", (_SIGNATURE_KEY,)).fetchone()
            if not force and row is not None and str(row[0]) == json.dumps([library, edits]):
                return {"skipped": True}
            plan = compute(con)
        with db(settings, write=True) as con:
            if _edits_signature(con) != edits:         # the person changed a сборка meanwhile: built again next time
                return {"skipped": True, "again": True}
            result = _apply(con, plan, int(time.time()))
            # the library as it was when the plan was made: what came meanwhile makes the next build
            con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                        (_SIGNATURE_KEY, json.dumps([library, _edits_signature(con)])))
        result["seconds"] = round(time.monotonic() - started, 2)
        if result["created"] or result["removed"] or result["moved"]:
            _LOG.info("сборки: %(sets)d (new %(created)d, gone %(removed)d, cards moved %(moved)d) in %(seconds)s s", result)
        return result
    finally:
        _BUILD_LOCK.release()


# ── what the person does ───────────────────────────────────────────────────────────────
class SetError(ValueError):
    """An edit that cannot be done (said to the person as it is)."""


def _set_id_of(con, image_id: int) -> int:
    row = con.execute("SELECT set_id FROM card_set_members WHERE image_id=?", (int(image_id),)).fetchone()
    return int(row[0]) if row else 0


def _order_of(con, set_id: int) -> list:
    return [int(r[0]) for r in con.execute("SELECT image_id FROM card_set_members WHERE set_id=? ORDER BY position, image_id",
                                           (int(set_id),))]


def _write_order(con, set_id: int, ids: list) -> None:
    for pos, image_id in enumerate(ids):
        con.execute("UPDATE card_set_members SET position=? WHERE image_id=? AND set_id=?", (pos, int(image_id), int(set_id)))


def _touch(con, set_id: int, now: int, *, lock: bool = True) -> None:
    con.execute(f"UPDATE card_sets SET updated_at=?{', locked=1' if lock else ''} WHERE set_id=?", (now, int(set_id)))


def _drop_small(con, set_ids) -> list:
    """Сборки left with fewer than two cards are no сборки: removed (their card stays where it is in the gallery)."""
    gone = []
    for set_id in set(set_ids):
        if con.execute("SELECT COUNT(*) FROM card_set_members WHERE set_id=?", (int(set_id),)).fetchone()[0] < 2:
            con.execute("DELETE FROM card_sets WHERE set_id=?", (int(set_id),))
            gone.append(int(set_id))
    return gone


def _check_set(con, set_id: int) -> dict:
    row = con.execute("SELECT set_id, kind, locked, title FROM card_sets WHERE set_id=?", (int(set_id),)).fetchone()
    if row is None:
        raise SetError("Такой сборки уже нет — обновите страницу")
    return {"id": int(row[0]), "kind": str(row[1]), "locked": bool(row[2]), "title": str(row[3] or "")}


def _cards_exist(con, ids: list) -> list:
    alive = _alive(con, ids)
    return [int(i) for i in dict.fromkeys(int(i) for i in ids) if int(i) in alive]


def remove_cards(settings: dict, ids: list) -> dict:
    """Take cards out of their сборки.  The program never puts them into one again; a сборка of the program's becomes
    the person's (as it is now, without them).  {"removed": n, "dissolved": [set ids left with fewer than two]}."""
    from core.database.connection import db
    now = int(time.time())
    removed, touched = 0, set()
    with db(settings, write=True) as con:
        for image_id in _cards_exist(con, ids):
            set_id = _set_id_of(con, image_id)
            if not set_id:
                continue
            _touch(con, set_id, now)
            con.execute("DELETE FROM card_set_members WHERE image_id=?", (image_id,))
            con.execute("INSERT OR IGNORE INTO card_set_optout(image_id, created_at) VALUES(?,?)", (image_id, now))
            touched.add(set_id)
            removed += 1
        for set_id in touched:
            _write_order(con, set_id, _order_of(con, set_id))
        gone = _drop_small(con, touched)
    return {"removed": removed, "dissolved": gone}


def dissolve(settings: dict, set_ids: list) -> dict:
    """«Разобрать»: the сборки are no more; their cards are never put into one by the program again."""
    from core.database.connection import db
    now = int(time.time())
    done, cards = 0, 0
    with db(settings, write=True) as con:
        for set_id in dict.fromkeys(int(s) for s in set_ids):
            members = _order_of(con, set_id)
            if not con.execute("SELECT 1 FROM card_sets WHERE set_id=?", (set_id,)).fetchone():
                continue
            for image_id in members:
                con.execute("INSERT OR IGNORE INTO card_set_optout(image_id, created_at) VALUES(?,?)", (image_id, now))
            con.execute("DELETE FROM card_sets WHERE set_id=?", (set_id,))
            done += 1
            cards += len(members)
    return {"dissolved": done, "cards": cards}


def move(settings: dict, set_id: int, image_id: int, to: int) -> dict:
    """Put a card of a сборка to place ``to`` (0 — first); the сборка becomes the person's."""
    from core.database.connection import db
    with db(settings, write=True) as con:
        _check_set(con, set_id)
        order = _order_of(con, set_id)
        if int(image_id) not in order:
            raise SetError("Этой карточки в сборке уже нет — обновите страницу")
        order.remove(int(image_id))
        to = max(0, min(len(order), int(to)))
        order.insert(to, int(image_id))
        _write_order(con, set_id, order)
        _touch(con, set_id, int(time.time()))
    return {"order": order}


def rename(settings: dict, set_id: int, title: str) -> dict:
    """The сборка's name ("" — none); the сборка becomes the person's."""
    from core.database.connection import db
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:200]
    with db(settings, write=True) as con:
        _check_set(con, set_id)
        con.execute("UPDATE card_sets SET title=? WHERE set_id=?", (title, int(set_id)))
        _touch(con, set_id, int(time.time()))
    return {"title": title}


def _expand(con, ids: list) -> list:
    """The chosen cards, a card of a сборка standing for its whole сборка (a tile of the gallery is the сборка)."""
    out: list = []
    for image_id in ids:
        set_id = _set_id_of(con, image_id)
        for member in (_order_of(con, set_id) if set_id else [image_id]):
            if member not in out:
                out.append(member)
    return out


def expand(settings: dict, ids: list) -> list:
    """(v5.44.118) The cards of the chosen tiles of the gallery: a card of a сборка stands for its whole сборка
    (:func:`_expand`) — «В корзину выбранные»."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        return _expand(con, [int(i) for i in ids])


def gather(settings: dict, ids: list, *, into: int = 0, title: str = "") -> dict:
    """«Собрать в одну сборку»: the chosen cards (a card of a сборка — with its whole сборка) in one сборка of the
    person's — a new one, or ``into`` (they are added at its end).  {"set": id, "cards": n}."""
    from core.database.connection import db
    now = int(time.time())
    with db(settings, write=True) as con:
        chosen = _cards_exist(con, ids)
        if into:
            target = _check_set(con, into)["id"]
            have = _order_of(con, target)
            adding = [i for i in _expand(con, chosen) if i not in have]
            if not adding:
                raise SetError("Эти карточки уже в этой сборке")
            order = have + adding
        else:
            order = _expand(con, chosen)
            if len(order) < 2:
                raise SetError("Нужно выбрать хотя бы две карточки")
            old = {_set_id_of(con, i) for i in order}
            if len(old) == 1 and 0 not in old and set(_order_of(con, next(iter(old)))) == set(order):
                raise SetError("Эти карточки уже одна сборка")
            target = int(con.execute("INSERT INTO card_sets(kind, title, locked, created_at, updated_at) "
                                     "VALUES('manual', ?, 1, ?, ?)", (str(title or "")[:200], now, now)).lastrowid)
            adding = order
        if len(order) > 1000:
            raise SetError("Слишком много карточек для одной сборки (больше 1000)")
        left = set()
        for image_id in adding:
            old = _set_id_of(con, image_id)
            if old and old != target:                  # it leaves its сборка (one of the program's is built again)
                left.add(old)
            con.execute("INSERT OR REPLACE INTO card_set_members(image_id, set_id, position) VALUES(?,?,?)",
                        (image_id, target, 0))
            con.execute("DELETE FROM card_set_optout WHERE image_id=?", (image_id,))
        _write_order(con, target, order)
        _touch(con, target, now)
        for set_id in left:
            _write_order(con, set_id, _order_of(con, set_id))
        _drop_small(con, left)
    return {"set": target, "cards": len(order)}


def set_id_of(settings: dict, image_id: int) -> int:
    """The сборка the card is in (0 — none)."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        return _set_id_of(con, image_id)


def join_part(settings: dict, set_id: int, image_id: int, *, site: str = "", wait: float = 60.0) -> dict:
    """A part of a сборка downloaded from its page («Скачать» on a part from the site, v5.44.10): in the сборка at once
    — the сборки are built again now (before, up to three minutes later: the card just downloaded stood alone, «не
    собирается»); when the rules leave it out of every сборка, it goes into this one all the same, at its place by its
    post's number on ``site`` — the person downloaded it as a part of it (the сборка becomes the person's: only new
    parts are added to it).  Not a card the person took out of сборки.  {"set": its сборка, "how": "rules" / "person"
    / ""}."""
    from core.database.connection import db, writes_blocked
    built = rebuild(settings, force=True, wait=wait)
    if built.get("skipped") or writes_blocked():
        return {"set": 0, "how": ""}
    now = int(time.time())
    with db(settings, write=True) as con:
        current = _set_id_of(con, image_id)
        if current:
            return {"set": current, "how": "rules"}
        alive = con.execute("SELECT 1 FROM images WHERE id=? AND deleted=0", (int(image_id),)).fetchone()
        taken_out = con.execute("SELECT 1 FROM card_set_optout WHERE image_id=?", (int(image_id),)).fetchone()
        row = con.execute("SELECT kind FROM card_sets WHERE set_id=?", (int(set_id),)).fetchone()
        if not alive or taken_out or row is None:
            return {"set": 0, "how": ""}
        order = _order_of(con, set_id)
        numbers = {}
        if site:
            marks = ",".join("?" * (len(order) + 1))
            for card, number in con.execute(
                    f"SELECT image_id, MIN(post_id) FROM site_posts WHERE state='ok' AND site=? AND image_id IN ({marks}) "
                    f"GROUP BY image_id", [site, *order, int(image_id)]):
                numbers[int(card)] = int(number)
        mine = numbers.get(int(image_id), 0)
        at = len(order)
        if mine:
            for pos, card in enumerate(order):
                if numbers.get(card, 0) > mine:
                    at = pos
                    break
        order.insert(at, int(image_id))
        con.execute("INSERT OR REPLACE INTO card_set_members(image_id, set_id, position) VALUES(?,?,?)",
                    (int(image_id), int(set_id), at))
        _write_order(con, set_id, order)
        _touch(con, set_id, now)
    _LOG.info("сборки: card %s downloaded as a part of сборка %s put into it", image_id, set_id)
    return {"set": int(set_id), "how": "person"}


# ── for the pages ──────────────────────────────────────────────────────────────────────
KIND_WORDS = {"parent": "родитель и дочерние", "work": "одна работа", "series": "серия", "manual": "сборка"}


def set_of(settings: dict, image_id: int) -> dict | None:
    """The card's сборка for its page: {"id", "kind", "title", "locked", "members": [cards: id, path, md5, size, mtime,
    is_video, width, height], "index" — the card's place among them, "parents" — the members that are a parent post of
    another, "root": (site, post) of the parent, "post_ids": {card: its post's number on the root's site}}; None — no
    сборка (or fewer than two cards out of the trash)."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT s.set_id, s.kind, s.title, s.locked FROM card_set_members m JOIN card_sets s "
                          "ON s.set_id=m.set_id WHERE m.image_id=?", (int(image_id),)).fetchone()
        if row is None:
            return None
        set_id = int(row[0])
        members = [dict(r) for r in con.execute(
            "SELECT i.id, i.path, i.hash_md5, i.size_bytes, i.mtime_ns, i.is_video, i.width, i.height, i.deleted "
            "FROM card_set_members m JOIN images i ON i.id=m.image_id WHERE m.set_id=? AND i.deleted=0 "
            "ORDER BY m.position, m.image_id", (set_id,))]
        if len(members) < 2:
            return None
        ids = [int(m["id"]) for m in members]
        marks = ",".join("?" * len(ids))
        posts = con.execute(f"SELECT image_id, site, post_id, parent_id FROM site_posts WHERE state='ok' "
                            f"AND image_id IN ({marks})", ids).fetchall()
    parents_of = {(str(p[1]), int(p[3])) for p in posts if int(p[3] or 0)}
    parents = sorted({int(p[0]) for p in posts if (str(p[1]), int(p[2])) in parents_of})
    root = ()
    if parents_of:
        counted: dict = {}
        for p in posts:
            if int(p[3] or 0):
                counted[(str(p[1]), int(p[3]))] = counted.get((str(p[1]), int(p[3])), 0) + 1
        root = max(counted, key=lambda k: (counted[k], k))
    index = ids.index(int(image_id)) if int(image_id) in ids else -1
    post_ids = {int(p[0]): int(p[2]) for p in posts if root and str(p[1]) == root[0]}
    return {"id": set_id, "kind": str(row[1] or "series"), "title": str(row[2] or ""), "locked": bool(row[3]),
            "members": members, "index": index, "parents": parents, "root": root, "post_ids": post_ids,
            "posts": [(int(p[0]), str(p[1]), int(p[2])) for p in posts]}


def lone_family(settings: dict, image_id: int, sites) -> dict | None:
    """(v5.44.1) A card of no сборка whose post may have a family on a site: the box over its picture asks the site
    for it (``webui/pages/post_sets.py``) — the library had one picture of a parent and its two children, and no box
    showed the other two.  {"id": 0, "kind": "parent", "lone": True, "child" — its post has a parent there, "members":
    [the card], "index": 0, "parents" — the card when its own post is asked about, "root": (site, the parent's post —
    or the card's own: its children are asked), "post_ids", "posts"}; None — it is in a сборка of two cards or more,
    the person took it out of сборки, it is in the trash, or it has no post on ``sites`` (the sites the grabber
    searches, in its order: the first one where its post has a parent, else the first)."""
    from core.database.connection import db
    order = {str(site): n for n, site in enumerate(sites or [])}
    if not order:
        return None
    with db(settings, readonly=True) as con:
        if con.execute("SELECT 1 FROM card_set_optout WHERE image_id=?", (int(image_id),)).fetchone():
            return None
        together = con.execute(
            "SELECT COUNT(*) FROM card_set_members m JOIN card_set_members x ON x.set_id=m.set_id "
            "JOIN images j ON j.id=x.image_id AND j.deleted=0 WHERE m.image_id=?", (int(image_id),)).fetchone()
        if together and int(together[0]) >= 2:
            return None
        row = con.execute("SELECT id, path, hash_md5, size_bytes, mtime_ns, is_video, width, height, deleted FROM images "
                          "WHERE id=? AND deleted=0", (int(image_id),)).fetchone()
        if row is None:
            return None
        posts = [(str(site), int(post_id), int(parent or 0)) for site, post_id, parent in con.execute(
            "SELECT site, post_id, parent_id FROM site_posts WHERE image_id=? AND state='ok' ORDER BY site, post_id",
            (int(image_id),))]
    usable = [p for p in posts if p[0] in order]
    if not usable:
        return None
    children = [p for p in usable if p[2]]
    site, post_id, parent = min(children or usable, key=lambda p: (order[p[0]], p[1]))
    card = int(row[0])
    return {"id": 0, "kind": "parent", "title": "", "locked": False, "lone": True, "child": bool(parent),
            "members": [dict(row)], "index": 0, "parents": [] if parent else [card], "root": (site, parent or post_id),
            "post_ids": {card: post_id}, "posts": [(card, s, p) for s, p, _parent in posts]}


def numbers_on(found: dict, site: str) -> dict:
    """{card: its post's number on ``site``} of a сборка of :func:`set_of`."""
    out: dict = {}
    for image_id, post_site, post_id in found.get("posts") or []:
        if post_site == site and (image_id not in out or post_id < out[image_id]):
            out[image_id] = post_id
    return out


def neighbours(settings: dict, image_id: int) -> tuple[int, int, int, int]:
    """(the card before it in its сборка, after it, the first, the last) — 0 where there is none; all 0 without a
    сборка.  Only cards out of the trash."""
    found = set_of(settings, image_id)
    if not found or found["index"] < 0:
        return 0, 0, 0, 0
    ids = [int(m["id"]) for m in found["members"]]
    i = found["index"]
    return (ids[i - 1] if i > 0 else 0, ids[i + 1] if i + 1 < len(ids) else 0, ids[0], ids[-1])


def tile_counts(settings: dict, ids: list) -> dict:
    """image id -> (set id, live cards of its сборка) for the gallery's tiles (cards of no сборка are left out)."""
    from core.database.connection import db
    out: dict = {}
    ids = [int(i) for i in ids]
    if not ids:
        return out
    with db(settings, readonly=True) as con:
        for part in _chunks(ids):
            marks = ",".join("?" * len(part))
            for image_id, set_id, count in con.execute(
                    f"SELECT m.image_id, m.set_id, (SELECT COUNT(*) FROM card_set_members x JOIN images j ON j.id=x.image_id "
                    f"AND j.deleted=0 WHERE x.set_id=m.set_id) FROM card_set_members m WHERE m.image_id IN ({marks})", part):
                if int(count) >= 2:
                    out[int(image_id)] = (int(set_id), int(count))
    return out


def describe_set(settings: dict, set_id: int) -> dict | None:
    """{"id", "kind", "title", "count" — its cards out of the trash, "first" — the first of them} or None."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT set_id, kind, title FROM card_sets WHERE set_id=?", (int(set_id),)).fetchone()
        if row is None:
            return None
        ids = [int(r[0]) for r in con.execute(
            "SELECT m.image_id FROM card_set_members m JOIN images i ON i.id=m.image_id AND i.deleted=0 "
            "WHERE m.set_id=? ORDER BY m.position, m.image_id", (int(set_id),))]
    if not ids:
        return None
    return {"id": int(row[0]), "kind": str(row[1] or ""), "title": str(row[2] or ""), "count": len(ids), "first": ids[0]}


def set_input(settings: dict, set_id: int) -> dict | None:
    """A сборка as its missing parts are asked of the sites by (``core/set_remote.py``): {"id", "kind", "ids" — its
    cards out of the trash in order, "cards": {card: a ``_Card`` with its tags as names (``tags``, ``artists``,
    ``chars``), comic / AI marks, size, work keys and pages}, "posts": [``_Post`` of its cards' posts on the sites],
    "queries": {card: [(work key, what ``source:*…*`` looks for)]}}; None — no such сборка, or fewer than two cards."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT set_id, kind FROM card_sets WHERE set_id=?", (int(set_id),)).fetchone()
        if row is None:
            return None
        ids = [int(r[0]) for r in con.execute(
            "SELECT m.image_id FROM card_set_members m JOIN images i ON i.id=m.image_id AND i.deleted=0 "
            "WHERE m.set_id=? ORDER BY m.position, m.image_id", (int(set_id),))]
        if len(ids) < 2:
            return None
        marks = ",".join("?" * len(ids))
        cards: dict = {}
        links: dict = {}
        for image_id, width, height, path in con.execute(
                f"SELECT id, width, height, path FROM images WHERE id IN ({marks})", ids):
            card = cards[int(image_id)] = _Card(int(image_id), width, height)
            m = _PIXIV_FILE.search(str(path or ""))
            if m:                                   # pixiv's own name of a page: 123456_p0.png
                links.setdefault(card.id, []).append(f"https://www.pixiv.net/artworks/{m.group(1)}")
                card.pages["px:" + m.group(1)] = int(m.group(2))
        posts: list = []
        for image_id, site, post_id, parent, owner, source in con.execute(
                f"SELECT image_id, site, post_id, parent_id, uploader, source FROM site_posts WHERE state='ok' "
                f"AND image_id IN ({marks}) ORDER BY image_id, site, post_id", ids):
            card = cards.get(int(image_id))
            if card is None:
                continue
            posts.append(_Post(card, str(site), int(post_id), int(parent or 0), str(owner or "")))
            if source:
                links.setdefault(card.id, []).extend(str(source).split("\n"))
        for image_id, url, host in con.execute(
                f"SELECT x.image_id, s.url, s.host FROM image_sources x JOIN sources s ON s.id=x.source_id "
                f"WHERE x.image_id IN ({marks}) ORDER BY x.image_id, s.id", ids):
            if host and not any(mark in str(host).lower() for mark in _BOORU_HOSTS) and int(image_id) in cards:
                links.setdefault(int(image_id), []).append(str(url or ""))
        names: dict = {}
        for image_id, name, category in con.execute(
                f"SELECT it.image_id, LOWER(t.normalized_name), t.category FROM image_tags it JOIN tags t ON t.id=it.tag_id "
                f"WHERE it.image_id IN ({marks})", ids):
            names.setdefault(int(image_id), []).append((str(name or ""), str(category or "")))
    queries: dict = {}
    for image_id, card in cards.items():
        tags = names.get(image_id, [])
        card.tags = frozenset(n for n, _c in tags if n)
        card.artists = frozenset(n for n, c in tags if c == "artist" and n)
        card.chars = frozenset(n for n, c in tags if c == "character" and n)
        card.comic = not card.tags.isdisjoint(_COMIC)
        card.ai = not card.tags.isdisjoint(_AI)
        found: list = []
        for url in links.get(image_id, [])[:24]:
            key = work_key(url)
            if key:
                if key[0] not in card.keys:
                    card.keys.append(key[0])
                if key[1] is not None and key[0] not in card.pages:
                    card.pages[key[0]] = int(key[1])
            asked = work_query(url)
            if asked and asked not in found:
                found.append(asked)
        queries[image_id] = found
    return {"id": int(row[0]), "kind": str(row[1] or ""), "ids": ids, "cards": cards, "posts": posts, "queries": queries}
