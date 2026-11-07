"""(v5.44.101) The filters of the program that no switch turns off — in one file (the owner, 06.11.2026: «все фильтры,
которые нельзя отключить, перенести в отдельный файл … сделать отдельным файлом filter»).  They were in
``core/catalog.py``, ``core/manhwa_sources.py`` and ``core/nhentai.py``; those modules take them from here (their old
names stay there, as imports, for the code and the checks that use them).

What the mode «SFW / Всё / NSFW» shows (``core/content_mode``) is a switch, not here.

1. **The program's rule about minors** (v5.44.85–5.44.97) — in every mode, at every site:

   * :func:`depicts_minors` — a title of a site of hentai whose tags, genres or name say that it depicts minors
     (:data:`MINOR_WORDS` — whole words, :data:`MINOR_STEMS` — beginnings of words, :data:`MINOR_PARTS` — inside
     Japanese text, which has no spaces) is neither shown, nor opened, nor downloaded (``manhwa_sources.Excluded``):
     Хентай-тян, HentaiLib, AllHentai, nhentai, Hentasis;
   * :data:`CATALOG_NEVER_TAGS` — the tag «Лоли» of Remanga and MangaLib: never offered in «Лента»'s filters, always
     asked left out (``catalog.NEVER_IDS`` — its numbers at each site);
   * :data:`HENTAILIB_NEVER_TAGS`, :data:`HENTAILIB_NEVER_GENRES` — HentaiLib's «Лоликон», «Сетакон», «Юные»,
     «Девочки-волшебницы», «Лоли» and «Школа», «Кодомо»: left out of every list of it (:func:`never_genres` — of
     MangaLib's «Эротика» in «NSFW» too: the Lib sites number their genres alike);
   * :data:`NHENTAI_NEVER_TAGS`, :data:`NHENTAI_NEVER_IDS` — nhentai's tags of that kind (names and numbers, asked
     6.11.2026): left out of every search (``nhentai.search_words``), a gallery with one is not shown
     (:func:`nhentai_forbidden`).

2. **What is downloaded already is not in «Лента»** (v5.44.93, the owner: «оно не должно показывать то что уже
   скачано») — :func:`downloaded`, :class:`Have`: the titles of the program's folders; a tile of one of them is left out
   (``catalog`` — «Уже скачанные не показаны: N»).

3. **«Видео → Лента», «Новые» without a status chosen** — :data:`VIDEO_NEW_STATUSES`: out already or coming out (the
   announcements of years ahead would be the whole first page); a status chosen in the filters — that one.
"""
from __future__ import annotations

import html as _html
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

_LOG = logging.getLogger("local_booru.catalog")


# ── 1. the program's rule about minors ───────────────────────────────────────────────────────────────────────────
MINOR_WORDS = frozenset({"loli", "lolicon", "lolikon", "lolcon", "лоли", "лоликон", "лолькон", "shota", "shotacon",
                         "shotakon", "сёта", "сета", "сётакон", "сетакон", "шота", "шотакон", "schoolgirl",
                         "schoolgirls", "schoolboy", "schoolboys", "малолетка", "малолетки", "девочка", "девочки",
                         "девочек", "дети", "ребенок", "ребёнок",
                         # (v5.44.96) HentaiLib's tag «Юные» and genre «Кодомо» (children's manga)
                         "юные", "юная", "юный", "юных", "кодомо", "kodomo",
                         # (v5.44.97) nhentai's English tags and titles: «kindergarten uniform», «randoseru», «JK»…
                         "kindergarten", "randoseru", "jk", "jc", "js"})
# (v5.44.97) the beginnings of such words: «lolibaba», «oppai loli», «school swimsuit», «school gym uniform» (nhentai's
# tags — as HentaiLib's genre «Школа» is left out), «kodomokai»
MINOR_STEMS = ("школ", "учени", "loli", "shota", "school", "kodomo")
# (v5.44.97) …and of Japanese titles, which have no spaces between words: inside the text
MINOR_PARTS = ("ロリ", "ショタ", "幼女", "幼児", "園児", "小学生", "中学生", "高校生", "女子高生", "女子校生", "ランドセル",
               "子供", "こども", "児童")


def depicts_minors(tags) -> bool:
    """(v5.44.85) A title of a site of hentai that, by its tags (genres, names), depicts minors
    (``manhwa_sources.Excluded``)."""
    for tag in tags or ():
        text = unicodedata.normalize("NFKC", _html.unescape(unquote(str(tag or "")))).casefold()
        if any(part in text for part in MINOR_PARTS):
            return True
        for word in re.split(r"[\W_]+", text):
            if word and (word in MINOR_WORDS or word.startswith(MINOR_STEMS)):
                return True
    return False


# «Лента»: the tag never offered and always left out at Remanga and MangaLib (its numbers there — ``catalog.NEVER_IDS``)
CATALOG_NEVER_TAGS = ("Лоли",)
# HentaiLib (site 4 of the Lib sites): «Лоликон», «Сетакон», «Юные», «Девочки-волшебницы» (its own), «Лоли» (MangaLib's:
# its titles carry those numbers too, 6.11.2026) — left out of every list of it; …and the genres «Школа», «Кодомо»
HENTAILIB_NEVER_TAGS = (372, 373, 145, 50, 187)
HENTAILIB_NEVER_GENRES = (70, 76)
NEVER_GENRE_IDS = {"hentailib": HENTAILIB_NEVER_GENRES}


def never_genres(site: str, mode: str) -> tuple:
    """(v5.44.96) The genres a site's list leaves out always in ``mode``: HentaiLib's (:data:`NEVER_GENRE_IDS`);
    MangaLib in «NSFW» — its «Эротика» alone — the same (the Lib sites number their genres alike): hentai «Школа» or
    «Кодомо»."""
    if site == "mangalib" and mode == "nsfw":
        return NEVER_GENRE_IDS["hentailib"]
    return NEVER_GENRE_IDS.get(site, ())


# nhentai: the tags of that kind (their names on the site and their numbers, 6.11.2026) — left out of every search, and a
# gallery with one of them is never shown; any other tag whose words say so — by its name (:func:`depicts_minors`)
NHENTAI_NEVER_TAGS = ("lolicon", "shotacon", "oppai loli", "low lolicon", "low shotacon", "lolibaba", "kodomo doushi",
                      "kodomo only", "schoolgirl uniform", "schoolboy uniform", "school swimsuit", "school gym uniform")
NHENTAI_NEVER_IDS = frozenset({19440, 32341, 25663, 14362, 27598, 166714, 152252, 152505, 10314, 7752, 29565, 132870})


def _number(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def nhentai_forbidden(titles=(), tag_ids=(), names=()) -> bool:
    """A gallery of nhentai with a tag the program never shows (by its number), or whose tags' names or titles say
    that it depicts minors."""
    if {_number(i) for i in tag_ids or ()} & NHENTAI_NEVER_IDS:
        return True
    return depicts_minors([*(t for t in titles or () if t), *(n for n in names or () if n)])


# ── 2. what is downloaded already is not in «Лента» ──────────────────────────────────────────────────────────────
HAVE_TTL = 60.0                   # the titles in the program's folders, kept this long (seconds)


@dataclass
class Have:
    """(v5.44.93) The titles of the program's folders: (site, key) of the ones downloaded from the sites (and of their
    other sites), the name keys (``manhwa_sources.names_of``) of every one there."""
    keys: set = field(default_factory=set)
    names: set = field(default_factory=set)

    def holds(self, ids, names) -> bool:
        return bool(set(ids or ()) & self.keys) or bool(set(names or ()) & self.names)


_HAVE: dict = {}                                  # (the kind's group, its folders) → (when, Have, their times)
_HAVE_LOCK = threading.Lock()


def downloaded(kind: str, settings: "dict | None") -> Have:
    """(v5.44.93) What the folders of the kind hold — the owner: «оно не должно показывать то что уже скачано».  The
    manga and the manhwa (both folders for both lists: a title goes to either by its kind): ``.lb/source.json`` of
    every title the program downloaded or linked (its site and key, and its other sites'), the names of every manga
    (``title``, nhentai's ``title_pretty``, ``title_japanese``) and manhwa (its name and ``alt_titles``); the
    videos: every series — ``source_url`` (YummyAnime's alias, Hentasis's number) and its names.  Kept
    :data:`HAVE_TTL` seconds (the libraries scan in the background, as their tabs do)."""
    settings = settings or {}
    if kind == "video":
        from core.video_library import video_root
        group, roots = "video", (video_root(settings),)
    else:
        from core import manhwa_download as D
        folders = D.roots(settings)
        group, roots = "books", (folders.get("manhwa") or "", folders.get("manga") or "")
    sig = (group, roots)
    stamp = tuple(_mtime(root) for root in roots)      # a title folder added or taken away — read again at once
    with _HAVE_LOCK:
        kept = _HAVE.get(sig)
        if kept is not None and time.monotonic() - kept[0] < HAVE_TTL and kept[2] == stamp:
            return kept[1]
    try:
        have = _have_videos(roots[0]) if group == "video" else _have_books(settings, roots)
    except Exception as exc:                          # noqa: BLE001 — a folder that cannot be read hides nothing
        _LOG.warning("catalog: the folders not read: %s", exc, exc_info=True)
        have = Have()
    with _HAVE_LOCK:
        _HAVE[sig] = (time.monotonic(), have, stamp)
    return have


def _mtime(path: str) -> int:
    from pathlib import Path
    try:
        return Path(path).stat().st_mtime_ns if path else 0
    except OSError:
        return 0


def forget_have() -> None:
    with _HAVE_LOCK:
        _HAVE.clear()


def _have_books(settings: dict, roots: tuple) -> Have:
    from core import manga_library as M, manhwa_download as D, manhwa_library as H, manhwa_sources as S
    have = Have()
    for _folder, data in D.followed(settings):
        have.keys.add((str(data.get("site")), str(data.get("key"))))
        for other in data.get("helpers") or []:
            if isinstance(other, dict) and other.get("site") and other.get("key"):
                have.keys.add((str(other["site"]), str(other["key"])))
        have.names |= S.names_of(str(data.get("title") or ""))
    manhwa, manga = roots
    if manhwa and _is_dir(manhwa):
        for title in H.LIBRARY.items(manhwa):
            have.names |= S.names_of(title.title, *title.alt_titles)
    if manga and _is_dir(manga):
        for item in M.LIBRARY.items(manga):
            have.names |= S.names_of(item.title, item.title_pretty, item.title_japanese)
            gallery = re.search(r"nhentai\.net/g/(\d+)", str(item.source_url or ""))
            if gallery:                               # (v5.44.97) a gallery of nhentai — by its number
                have.keys.add(("nhentai", str(int(gallery.group(1)))))
    return have


def _have_videos(root: str) -> Have:
    from core import manhwa_sources as S, video_dubs, video_library as V
    have = Have()
    if not root or not _is_dir(root):
        return have
    for series in V.LIBRARY.items(root):
        source = str(series.info.get("source_url") or "")
        alias = video_dubs.alias_of(source) if source else ""
        if alias:
            have.keys.add(("yummy", alias))
        number = _hentasis_number(source)
        if number:
            have.keys.add(("hentasis", number))
        have.names |= S.names_of(series.title, series.folder_title, *series.alt_titles)
    return have


def _hentasis_number(address: str) -> str:
    """The number of a title of Hentasis in its address («…/1112-dark-summoner-to-dekiteiru.html»; a mirror —
    «hentasisN.top» — too), else ""."""
    from core.catalog import HENTASIS_SITE
    try:
        parsed = urlparse(str(address or ""))
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if not host or not ("hentasis" in host or host == (urlparse(HENTASIS_SITE).hostname or "").lower()):
        return ""
    number = re.search(r"/(\d+)-[^/]*\.html?$", parsed.path)
    return number.group(1) if number else ""


def _is_dir(path: str) -> bool:
    from pathlib import Path
    try:
        return Path(path).is_dir()
    except OSError:
        return False


# ── 3. «Видео → Лента», «Новые» without a status chosen ──────────────────────────────────────────────────────────
VIDEO_NEW_STATUSES = ("ongoing", "released")      # YummyAnime's: out already or coming out — no announcements
