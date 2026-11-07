"""One work in several titles of the manga folder: its versions and its parts (v5.44).

nhentai links nothing to anything: a translation is a gallery of its own («[Artist] Title [English]», «… [Chinese]»,
the Japanese original), and so is every part of a work («Title», «Title 2», «Title Part 3», «前編» / «後編»).  They are
told by the title and the people who made it:

* ``title_cores`` — the title's core: the ``[brackets]`` (artist, language, translator), ``(parens)`` (event,
  parody) and ``{braces}`` out, the letters lower case, the punctuation out — the same in every language version of a
  title written the same way.  A title «Romaji | English» has both cores.
* ``people`` — the artists and circles: the title page's tags, else the title's first bracket «[Circle (Artist)]».
* versions — another gallery of the same core and the same people (or, nobody named on one side, a core long enough
  not to be any title's): ``check`` answers the browser extension's question about the galleries of a site page
  (``core/browser_companion_api.py`` — ``/extension/manga``): «уже есть» (this very gallery) or «другая версия».
* parts — titles whose core is the same once the part's number at its end is taken off (``part_of``), by the same
  people: one сборка of the tab «Манга» (``sets``) — one tile, its page the parts in their order.  By hand: a title
  taken out of its сборка (``apart``), or put together with another one (``joined``) — ``manga_sets.json`` in the
  settings folder.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

_LOG = logging.getLogger("local_booru.manga")

LONG_CORE = 10              # letters of a core that is no title's but one work's, when nobody is named to compare
_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|【[^】]*】|〔[^〕]*〕|«[^»]*»")
_FIRST_BRACKET = re.compile(r"^\s*(?:\([^)]*\)\s*)*\[([^\]]+)\]")
_GALLERY = re.compile(r"nhentai\.net/g/(\d+)", re.I)
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
_HALVES = {"前編": 1, "前篇": 1, "上": 1, "上巻": 1, "zenpen": 1, "中編": 2, "中篇": 2, "中": 2, "中巻": 2, "chuuhen": 2,
           "後編": 3, "后篇": 3, "後篇": 3, "下": 3, "下巻": 3, "kouhen": 3, "完結編": 9, "kanketsuhen": 9}
_PART_WORDS = r"(?:part|pt|chapter|chap|ch|episode|ep|vol|volume|act|stage|phase|round|book|no|глава|часть|том|серия|話)"
_PART = re.compile(rf"^(?P<base>.*?)\s*(?:{_PART_WORDS}\s*)?(?P<n>(?<!\d)\d{{1,3}})$", re.I)
_PART_CJK = re.compile(r"^(?P<base>.*?)\s*第\s*(?P<n>\d{1,3})\s*[話话章巻卷部回]$")
_BARE_NUMBER = re.compile(r"^nhentai\s*\d+$")       # a download named only by its gallery's number
_PART_WORD = re.compile(r"^(?P<base>.*?)\s+(?P<w>" + "|".join(sorted((re.escape(k) for k in _HALVES), key=len, reverse=True))
                        + r"|(?:ii|iii|iv|vi|vii|viii|ix))$", re.I)
_PART_HALF = re.compile(r"^(?P<base>.*?)(?P<w>前編|前篇|中編|中篇|後編|后篇|後篇|上巻|中巻|下巻|完結編)$")


# ── titles and people ────────────────────────────────────────────────────────────
def core(text) -> str:
    """The core of one title: no brackets, lower case, no punctuation (letters and digits of any script stay)."""
    t = unicodedata.normalize("NFKC", html.unescape(str(text or "")))
    t = _BRACKETS.sub(" ", t)
    t = re.sub(r"[\W_]+", " ", t.lower())
    return " ".join(t.split())


def title_cores(*titles) -> set:
    """The cores of a gallery's titles (English, Japanese, «pretty»); «Romaji | English» gives both."""
    out = set()
    for title in titles:
        for piece in re.split(r"\s*[|｜]\s*", unicodedata.normalize("NFKC", str(title or ""))):
            found = core(piece)
            if len(found) >= 2:
                out.add(found)
    return out


def norm_person(name) -> str:
    """As the manga folder keeps a tag: lower case, spaces as «_»."""
    return re.sub(r"\s+", "_", html.unescape(str(name or "")).strip().lower()).strip("_")


def people_of_title(title) -> set:
    """«(C99) [Circle (Artist A, Artist B)] Title» → {circle, artist_a, artist_b}."""
    found = _FIRST_BRACKET.match(unicodedata.normalize("NFKC", html.unescape(str(title or ""))))
    if not found:
        return set()
    text = found.group(1)
    names = re.split(r"[(),、&/×+]|\s+and\s+", text)
    out = {norm_person(n) for n in names}
    return {n for n in out if n and n not in {"english", "chinese", "japanese", "korean", "russian", "digital"}}


def people_of(manga) -> set:
    """The artists and circles of a title of the manga folder (its tags; else its title's first bracket)."""
    named = {norm_person(x) for g in ("artist", "group") for x in (manga.groups or {}).get(g, [])}
    named.discard("")
    return named or people_of_title(manga.title)


def gallery_id(manga) -> int:
    found = _GALLERY.search(str(getattr(manga, "source_url", "") or ""))
    return int(found.group(1)) if found else 0


def languages(manga) -> list:
    return [x for x in (manga.groups or {}).get("language", []) if x not in {"translated", "rewrite", "speechless"}]


def same_people(a: set, b: set, cores: set) -> bool:
    """The same people made both — or, nobody named on one side, a core long enough to be one work's."""
    if a and b:
        return bool(a & b)
    return any(len(c) >= LONG_CORE for c in cores)


# ── versions: the browser extension asks about the galleries of a site page ─────────────────
@dataclass
class _Entry:
    manga: object
    cores: set
    people: set


class Works:
    """The manga folder's titles by gallery number and by core (made once a scan of the folder)."""

    def __init__(self, items: list):
        self.by_gallery: dict = {}
        self.by_core: dict = {}
        for manga in items:
            for one in [manga, *getattr(manga, "chapters", [])]:
                entry = _Entry(one, title_cores(one.title, getattr(one, "title_japanese", ""),
                                                getattr(one, "title_pretty", "")), people_of(one))
                number = gallery_id(one)
                if number:
                    self.by_gallery.setdefault(number, entry)
                for c in entry.cores:
                    self.by_core.setdefault(c, []).append(entry)

    def answer(self, item: dict) -> dict:
        """What the folder has of one gallery of a site page: ``same`` (this very gallery), ``version`` (another one
        of the same work: another language, another upload), or nothing."""
        try:
            number = int(item.get("id") or 0)
        except (TypeError, ValueError):
            number = 0
        out = {"id": number, "status": ""}
        entry = self.by_gallery.get(number) if number else None
        if entry is not None:
            return {**out, **_said(entry, "same")}
        titles = [item.get(k) for k in ("title", "japanese", "pretty") if item.get(k)]
        cores = title_cores(*titles)
        if not cores:
            return out
        people = {norm_person(x) for k in ("artists", "groups") for x in (item.get(k) or []) if isinstance(x, str)}
        people.discard("")
        if not people:
            for title in titles:
                people |= people_of_title(title)
        for c in sorted(cores, key=len, reverse=True):
            for entry in self.by_core.get(c, []):
                if gallery_id(entry.manga) == number and number:
                    continue
                if same_people(people, entry.people, {c}):
                    return {**out, **_said(entry, "version")}
        return out


def _said(entry: _Entry, status: str) -> dict:
    manga = entry.manga
    return {"status": status, "manga_id": manga.id, "title": manga.title, "languages": languages(manga)}


_WORKS = {"key": None, "works": None}
_WORKS_LOCK = threading.Lock()


def works_of(items: list) -> Works:
    """``Works`` of this list (the library's scan: made again only for another scan)."""
    with _WORKS_LOCK:
        if _WORKS["key"] is not items:
            _WORKS["key"], _WORKS["works"] = items, Works(items)
        return _WORKS["works"]


MAX_ASKED = 300                 # galleries of one question


def check(settings: dict, items) -> dict:
    """``/extension/manga``: [{id, title, japanese?, artists?, groups?}] of a site page → what the manga folder has."""
    from core import manga_library as M
    root = M.manga_root(settings)
    if not root or not Path(root).is_dir():
        return {"ok": True, "items": [], "reason": "no_manga_folder"}
    works = works_of(M.LIBRARY.items(root))
    asked = [x for x in (items if isinstance(items, list) else []) if isinstance(x, dict)][:MAX_ASKED]
    return {"ok": True, "items": [works.answer(x) for x in asked]}


# ── parts: сборки of the tab «Манга» ─────────────────────────────────────────────────
def part_of(text) -> tuple[str, int]:
    """(the core without the part's number at its end, the number — 0 when it has none).  «Title 2», «Title Part 3»,
    «Title Ch. 4», «Title 第5話», «Title 前編/後編», «Title II».  Of «Romaji | English» — the first."""
    c = core(re.split(r"\s*[|｜]\s*", unicodedata.normalize("NFKC", str(text or "")))[0])
    if _BARE_NUMBER.match(c):
        return c, 0                      # «nhentai-123456»: its number is the gallery's, not a part's
    for rx in (_PART_CJK, _PART):
        found = rx.match(c)
        if found and found.group("base").strip():
            return found.group("base").strip(), int(found.group("n"))
    found = _PART_WORD.match(c) or _PART_HALF.match(c)
    if found and found.group("base").strip():
        word = found.group("w").lower()
        return found.group("base").strip(), _HALVES.get(found.group("w"), _HALVES.get(word, _ROMAN.get(word, 0)))
    return c, 0


@dataclass
class MangaSet:
    id: str                              # 12 hex: stable while its first part's place and the base stay
    title: str
    parts: list = field(default_factory=list)       # Manga, in their order
    kind: str = "parts"                  # (v5.44.125) "parts" — the parts of one work; "folder" — the manga of one folder
    #                                      of the parser's («silver soul»: different manga, one сборка by its name)

    @property
    def cover(self):
        return self.parts[0] if self.parts else None


def _display(title: str) -> str:
    """A сборка's name from its first part's title: the brackets and the part's number off, the letters as written."""
    t = unicodedata.normalize("NFKC", html.unescape(str(title or "")))
    t = re.split(r"\s*[|｜]\s*", t)[0]
    t = " ".join(_BRACKETS.sub(" ", t).split()).strip(" -~_")
    t = re.sub(rf"\s*(?:{_PART_WORDS}\.?\s*)?\d{{1,3}}$", "", t, flags=re.I).strip(" -~_.:")
    return t or str(title or "")


def sets(items: list, manual: dict | None = None) -> list:
    """The сборки of the manga folder: titles of one base and the same people (two or more), with what was done by hand
    (``manual``: {"apart": [id…], "joined": {id: other id}}).  A series folder (chapters inside) stays one title."""
    manual = manual or {}
    apart = set(manual.get("apart") or [])
    joined = dict(manual.get("joined") or {})
    by_id = {m.id: m for m in items}
    parent = {m.id: m.id for m in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    bases: dict = {}
    numbers: dict = {}
    folders: dict = {}                   # (v5.44.125) a folder's key -> its manga («lb_set» of their info.json)
    for manga in items:
        if manga.id in apart:
            continue
        if getattr(manga, "set_key", ""):
            folders.setdefault(manga.set_key, []).append(manga)
            continue                     # one folder's manga: one сборка by the folder, not by their names
        found = [part_of(t) for t in (getattr(manga, "title_pretty", "") or manga.title,)]
        base, number = found[0]
        numbers[manga.id] = number
        if len(base) < 2:
            continue
        bases.setdefault(base, []).append(manga)
    for base, group in bases.items():
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if same_people(people_of(a), people_of(b), {base}) or len(base) >= LONG_CORE and \
                        not (people_of(a) and people_of(b)):
                    union(a.id, b.id)
    named: dict = {}                     # a root of the union -> the folder's (key, name)
    for key, members in folders.items():
        if len(members) < 2:
            continue
        for manga in members[1:]:
            union(members[0].id, manga.id)
        named[find(members[0].id)] = (key, members[0].set_name or key)
    for one, other in joined.items():
        if one in by_id and other in by_id and one not in apart and other not in apart:
            union(one, other)
    groups: dict = {}
    for manga in items:
        if manga.id in apart:
            continue
        groups.setdefault(find(manga.id), []).append(manga)
    folder_of = {find(m.id): named[r] for r in named for m in folders.get(named[r][0], [])}
    out = []
    for root, members in groups.items():
        if len(members) < 2:
            continue
        found = folder_of.get(root)
        if found is not None:            # one folder's manga: in their folder's order, named by it
            members.sort(key=lambda m: (getattr(m, "set_key", "") != found[0], getattr(m, "set_place", 0) or 10 ** 6,
                                        _natural(m.title)))
            set_id = hashlib.md5(("folder|" + found[0]).encode("utf-8")).hexdigest()[:12]
            out.append(MangaSet(id=set_id, title=found[1], parts=members, kind="folder"))
            continue
        members.sort(key=lambda m: (numbers.get(m.id, 0), _natural(m.title)))
        first = members[0]
        set_id = hashlib.md5(("set|" + root).encode("utf-8")).hexdigest()[:12]
        out.append(MangaSet(id=set_id, title=_display(getattr(first, "title_pretty", "") or first.title), parts=members))
    return out


def _natural(text) -> list:
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(text))]


# ── what was done by hand ────────────────────────────────────────────────────────────
_MANUAL_LOCK = threading.Lock()


def _manual_file() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / "manga_sets.json"


def manual() -> dict:
    """{"apart": [ids taken out of their сборка], "joined": {id: the other title it was put with}}."""
    try:
        data = json.loads(_manual_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"apart": [], "joined": {}}
    if not isinstance(data, dict):
        return {"apart": [], "joined": {}}
    apart = [str(x) for x in data.get("apart") or [] if isinstance(x, str)]
    joined = {str(k): str(v) for k, v in (data.get("joined") or {}).items() if isinstance(v, str)}
    return {"apart": apart, "joined": joined}


def _save(data: dict) -> None:
    """Written whole or not at all: a temporary file swapped in."""
    import os
    path = _manual_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def take_apart(manga_id: str) -> dict:
    """The title out of its сборка (and never put in one by the program again)."""
    with _MANUAL_LOCK:
        data = manual()
        if manga_id not in data["apart"]:
            data["apart"].append(manga_id)
        data["joined"] = {k: v for k, v in data["joined"].items() if manga_id not in (k, v)}
        _save(data)
        return data


def put_with(manga_id: str, other_id: str) -> dict:
    """The title in one сборка with another one (both out of «apart»)."""
    with _MANUAL_LOCK:
        data = manual()
        data["apart"] = [x for x in data["apart"] if x not in (manga_id, other_id)]
        if manga_id != other_id:
            data["joined"][manga_id] = other_id
        _save(data)
        return data


def back_to_program(manga_id: str) -> dict:
    """What was done by hand to this title is forgotten: the program decides again."""
    with _MANUAL_LOCK:
        data = manual()
        data["apart"] = [x for x in data["apart"] if x != manga_id]
        data["joined"] = {k: v for k, v in data["joined"].items() if manga_id not in (k, v)}
        _save(data)
        return data
