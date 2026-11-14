"""(v5.44.84) Manga in the booru parser's own folder: is this folder a manga (or a manhwa) — the pages of its chapters,
its chapters, its tomes in archives — or the pictures of the booru sites?

The owner (5.11.2026): «я говорил про архив что бы оно сверяло именно из архива который использует парсер booru а не
из того в котором манга и манхва»; «парсер должен проверить эта папка манга если да то пытаться найти что это за манга
а если нет то просто пропарсить через обычные боору потому что у меня папка в папке в папке в папке из за чего могут
быть проблемы».

A folder is a manga's when (any of):

* ``ComicInfo.xml`` is in it (what readers and downloaders of manga put beside a chapter);
* its pictures are a chapter's pages: at least :data:`MIN_PAGES`, nearly all of them named by a page's number («001.jpg»,
  «12.png», «page_03.webp», «p7.jpg») and the numbers one run from 0 or 1 with few gaps — or at least two such pages
  in a folder named as a chapter («Глава 12», «Chapter 5», «Том 1 Глава 3 - Имя», «ch. 7», «Vol 2», «c001», «12»);
* it holds a manga's archives (.cbz, .cbr, .cb7) — two or more, or one and nothing else.

Its title is the first folder above it whose name is not a chapter's or a tome's («Ван-Пис/Глава 1» → «Ван-Пис»;
«Архив/2023/Some Title/Vol 1/Chapter 3» → «Some Title»), never the parser's folder itself; a folder of pages with a
name of its own is a title by itself (a one-shot, a doujin).  Everything under a title is its own: (v5.44.125) the
parser holds it back until ``core/parser_manga.py`` finds out what it is (it was «Скачать → Из архива»).  The art of the booru
sites is never named by pages' numbers (MD5s, «12345678_p0», «id - tags»), so it goes on to the sites as before.  A
folder the owner said is not a manga («Это не манга») — never, nor anything under it.

The titles found by the last look at the whole folder (:func:`scan`) — ``parser_manga.json`` in the settings folder.

(v5.44.125) «Манга через парсер» — the owner (7.11.2026): «оно должно посмотреть по папке … проверить через snao
манга ли это если да то в мангу … а если нет то просто в сборку потому что я раньше часто даже не мангу помечал 1 2 3
так же манга должна собираться в сборку из манг … не давать ложных результатов манги»; «сделай кароче так что бы в
мангу проходила только подтвержденная манга а не рандом без тегов».  What is found here is a **candidate** only
(:func:`find`, :class:`Candidate`): the names and the make-up of the folders alone — no picture is opened (thousands of
folders); ``core/parser_manga.py`` confirms it by a site of manga (ComicInfo.xml, a link, a title of a site whose
pages are the folder's — by its name or SauceNAO's) or parses it as the booru's.

* pages by numbers — «001.jpg», «page_03.webp» and VK's «01-bLEqzb4tFuw.jpg» / «001-UyH2-4z8Pmo.jpg» (a number, a
  dash, VK's own letters: :func:`page_number`);
* «М4.», «M12.», «01.» at the start of a folder's name — the owner's order of his folders, not a name
  (:func:`bare_name`);
* folders side by side whose names are one name with a number at the end («М4. Сильвер Соул 0» … «М4. Сильвер Соул
  12») — the parts of one manga, by that number (:class:`Work`: its parts — its chapters);
* a folder holding several manga («silver soul»: «Сильвер Соул», «Сильвер Соул Близнецы», «Восхождение драконов»…) —
  their сборка, by its own name (never the parser's folder itself, never one named «манга», «Новая папка»…);
  (v5.44.166) **only a folder named after one of its manga** (:func:`named_after`: «silver soul» — «Сильвер Соул»,
  the Latin and the Cyrillic spelling read as one).  A folder of a kind is not a work: the owner (12.11.2026): «у
  меня манга собралась в сборку из за того что она в папке в папке (у меня манга по категориям была и bdsm собрало в
  сборку хотя не надо было)» — the manga of «bdsm», «яой», an author's folder are each a manga by itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

_LOG = logging.getLogger("local_booru.manhwa")

IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".jxl"}
ARCHIVES = {".cbz", ".cbr", ".cb7"}
MIN_PAGES = 3                           # pages of a chapter not named as one, beside another such chapter
NAMED_PAGES = 2                         # pages of a folder named as a chapter
LONE_PAGES = 12                         # pages of a folder by itself (a one-shot, a doujin); (v5.44.125) any sizes:
                                        # a candidate only — the pictures are never opened here
SHARE = 0.8                             # pictures named by a page's number at least
RUN = 0.8                               # the numbers one run: as many as the run's length × this at least
STORE_FILE = "parser_manga.json"
NOT_FILE = "parser_not_manga.json"
SCAN_EVERY = 6 * 3600                   # the whole folder looked at again after this long (seconds)
RULES = 5                               # (v5.44.113) +1 when the rules change: the folder is looked at again at once
                                        # (2: v5.44.111 — 15 pages by numbers of any sizes; 3: v5.44.115 — taken back;
                                        # 4: v5.44.125 — candidates: VK's names, parts of one manga, сборки;
                                        # 5: v5.44.166 — a сборка only of a folder named after one of its manga)
FIRST_PAGES = 3                         # pages kept of a title (for SauceNAO and its cover on the page)
# pictures beside the pages that are none of them (a cover the program or a reader put there)
ASIDE = {"cover", "folder", "poster", "thumb", "thumbnail", "back", "preview", "banner"}

_PAGE = re.compile(r"^(?:p|pg|page|pages|стр|страница|img|image|scan|seite)?[\s_.\-#№]*(\d{1,4})"
                   r"(?:[\s_.\-]*(?:[a-z]|\d{1,2}))?$", re.I)
_CHAPTER = re.compile(r"(?:^|[\s_\-\[(.,])(?:глава|гл|chapter|chap|ch|эпизод|episode|ep|том|vol|volume|tome|часть|"
                      r"part|выпуск|issue|c|v|t)[\s_.\-#№]*\d", re.I)
_NUMBER = re.compile(r"^\s*[#№]?\s*\d{1,4}(?:[.,]\d{1,2})?\s*$")
# a name that IS a chapter's or a tome's (not a title's with a tome in it, «Berserk Vol 1-2»): it begins as one —
# «Глава 12», «Chapter 5 - Имя», «Том 1 Глава 3», «Vol. 1 Ch. 5», «c001», «012 - Имя», «[Group] Ch. 7»,
# «Scanlator_Ch. 1» (Tachiyomi's downloads)
_CHAPTER_START = re.compile(r"^\s*(?:\[[^\]]{0,60}\]\s*|\([^)]{0,60}\)\s*|[^_/\\]{1,40}_)?"
                            r"(?:(?:глава|гл|chapter|chap|ch|эпизод|episode|ep|том|vol|volume|tome|часть|part|выпуск|"
                            r"issue|c|v|t)\.?[\s_.\-#№]*\d|\d{1,4}(?:[.,]\d{1,2})?(?:\s*[-–—.:)]\s*\S.*|\s*$))", re.I)


# (v5.44.125) the pictures VK saves: «01-bLEqzb4tFuw», «001-UyH2-4z8Pmo» — the page's number, a dash and VK's own
# letters (6–20 of them, a letter among them: never a date «2023-05-12» nor «12-34»)
_VK = re.compile(r"^(\d{1,4})-(?=[A-Za-z0-9_-]{6,20}$)[A-Za-z0-9_-]*[A-Za-z][A-Za-z0-9_-]*$")
# (v5.44.125) «М4. », «M12) », «01. » at the start of a folder's name: the owner's order of his folders, not a name
_ORDER = re.compile(r"^\s*([A-Za-zА-Яа-яЁё]{0,2}\s?\d{1,3}\s*[.)])\s+(?=\S)")
# …and a number at its end: the part of one manga («Сильвер Соул 12», «Silver_Soul_01»)
_TAIL = re.compile(r"^(?P<base>.*?\S)[\s_\-#№]*(?P<n>\d{1,4})$")


def page_number(stem: str):
    """The number of a page by its file's name (without the extension) — None: not a page's name."""
    text = str(stem or "").strip()
    found = _PAGE.match(text) or _VK.match(text)
    return int(found.group(1)) if found else None


def vk_name(stem: str) -> bool:
    """(v5.44.125) A page named as VK saves it («01-bLEqzb4tFuw»)."""
    return bool(_VK.match(str(stem or "").strip()))


def bare_name(name: str) -> str:
    """(v5.44.125) A folder's name without the owner's order at its start («М4. Сильвер Соул 0» → «Сильвер Соул 0»)."""
    text = str(name or "").strip()
    rest = _ORDER.sub("", text, count=1).strip()
    return rest or text


def order_prefix(name: str) -> str:
    """(v5.44.125) «М4.» of «М4. Сильвер Соул 0»; "" — none."""
    found = _ORDER.match(str(name or ""))
    return found.group(1).replace(" ", "") if found else ""


def tail_number(name: str) -> tuple:
    """(v5.44.125) (the name without the number at its end, the number) — («Сильвер Соул», 12); (name, None) — none
    (or nothing but a number)."""
    text = str(name or "").strip()
    found = _TAIL.match(text)
    if not found or not re.search(r"[^\W\d_]", found.group("base")):
        return text, None
    return found.group("base").strip(" _-#№"), int(found.group("n"))


def page_numbers(stems: list) -> list:
    """(v5.44.111) The pages' numbers of a folder's pictures (their names without the extension; None — not a page's
    name): by :func:`page_number`, else — when nearly none is a bare number — the names without the words all of
    them share before and after the number («Silver_Soul_01» … «Silver_Soul_17» → 1 … 17; «01_rus» → 1)."""
    stems = [str(x or "").strip() for x in stems]
    direct = [page_number(x) for x in stems]
    if len(stems) < 2 or sum(n is not None for n in direct) >= SHARE * len(stems):
        return direct
    prefix = os.path.commonprefix(stems).rstrip("0123456789")
    suffix = os.path.commonprefix([x[::-1] for x in stems])[::-1].lstrip("0123456789")
    if (not prefix and not suffix) or re.fullmatch(r"\d{4,}_p", prefix, re.I):
        return direct                               # pixiv's pages of a work («12345678_p0»): the booru sites' art
    out = []
    for x in stems:
        middle = x[len(prefix):len(x) - len(suffix)] if len(x) > len(prefix) + len(suffix) else ""
        out.append(page_number(middle) if middle else None)
    return out


def chapter_like(name: str) -> bool:
    """A folder's name speaks of a chapter or a tome somewhere («Глава 12», «Berserk Ch 5», «Том 1», «12»)."""
    text = str(name or "").strip()
    return bool(_NUMBER.match(text) or _CHAPTER.search(" " + text))


def chapter_name(name: str) -> bool:
    """A folder's name is a chapter's or a tome's, not a title's (:data:`_CHAPTER_START`) — a year is not one."""
    text = str(name or "").strip()
    if re.fullmatch(r"(19[5-9]\d|20\d\d)", text):
        return False
    return bool(_CHAPTER_START.match(text))


def pages_folder(names: list, *, named: bool = False, comic_info: bool = False, least: int = 0) -> bool:
    """The pictures of a folder (their file names) are a chapter's pages: nearly all named by numbers that make one
    run from 0 or 1 (``least`` — so many of them at least)."""
    pictures = [n for n in names if Path(n).suffix.lower() in IMAGES]
    if comic_info and pictures:
        return True
    if len(pictures) < (least or (NAMED_PAGES if named else MIN_PAGES)):
        return False
    numbers = sorted({n for n in (page_number(Path(p).stem) for p in pictures) if n is not None})   # (v5.44.115)
    if len(numbers) < SHARE * len(pictures):
        return False
    if not numbers or numbers[0] > 2:
        return False
    span = numbers[-1] - numbers[0] + 1
    return len(numbers) >= RUN * span


@dataclass
class Folder:
    """What one folder holds (the pictures' names, the archives, ComicInfo.xml) — what a decision needs."""
    path: Path
    pictures: list = field(default_factory=list)
    archives: list = field(default_factory=list)
    comic_info: bool = False
    dirs: int = 0


@dataclass
class Title:
    """A manga's folder in the parser's folder: its pages and chapters counted, its first pages."""
    path: str
    pages: int = 0
    chapters: int = 0
    archives: int = 0
    first: list = field(default_factory=list)       # [path] or [[archive, member]] — up to FIRST_PAGES
    why: str = ""

    @property
    def id(self) -> str:
        return key_of(self.path)

    def public(self) -> dict:
        return {"path": self.path, "pages": self.pages, "chapters": self.chapters, "archives": self.archives,
                "first": self.first, "why": self.why}


def key_of(path) -> str:
    return hashlib.md5(os.path.normcase(os.path.abspath(str(path))).encode("utf-8")).hexdigest()[:12]


def _under(path: Path, folders: set) -> bool:
    """``path`` or a folder above it is one of ``folders`` (normcase'd absolute paths)."""
    node = os.path.normcase(os.path.abspath(str(path)))
    while True:
        if node in folders:
            return True
        parent = os.path.dirname(node)
        if parent == node:
            return False
        node = parent


_TOME = re.compile(r"^\s*(?:том|vol|volume|tome|v|t)\.?[\s_.\-#№]*\d", re.I)


def _title_of(folder: Path, root: Path) -> Path:
    """The title of a folder of pages (or of archives): its folder when its own name is a chapter's or a tome's
    («Ван-Пис/Глава 1», «Title/12»), and above the tomes («Title/Vol 1/Chapter 3»); a folder named by a number above a
    chapter («123/Глава 1») is a title's own name.  Never the parser's folder itself."""
    root_key = os.path.normcase(os.path.abspath(str(root)))

    def up(node: Path):
        parent = node.parent
        if parent == node or os.path.normcase(os.path.abspath(str(parent))) == root_key:
            return None
        return parent
    node = folder
    if chapter_name(node.name) and up(node) is not None:
        node = up(node)
    while _TOME.match(node.name) and up(node) is not None:
        node = up(node)
    return node


def _series_name(name: str) -> str:
    """(v5.44.115) What folders of one title's chapters share: the name without its numbers and signs («Title 01»,
    «Title 02» → «title»; «1», «2» → "").  Two folders of an artist's numbered pictures side by side («Artist», «Few»)
    are not two chapters of one title (the review of 7.11.2026: they were taken for manga)."""
    return re.sub(r"[\W\d_]+", " ", str(name or "").lower()).strip()


# (v5.44.166) a name as its Cyrillic and its Latin spelling agree on it: «Сильвер Соул» — «silver soul»
_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "i", "ь": "",
    "э": "e", "ю": "iu", "я": "ia"})
# …and the ways one sound is written in Latin letters («kh» — «h», «ts» — «c», «yu» — «iu», «w» — «v»)
_SOUNDS = (("shch", "sch"), ("kh", "h"), ("ts", "c"), ("tz", "c"), ("yu", "iu"), ("ju", "iu"), ("ya", "ia"),
           ("ja", "ia"), ("yo", "e"), ("jo", "e"), ("ye", "e"), ("je", "e"), ("ph", "f"), ("y", "i"), ("j", "i"),
           ("w", "v"), ("x", "ks"))


def name_skeleton(name: str) -> str:
    """(v5.44.166) A folder's or a manga's name as its spellings agree on it: no order («М4.»), no numbers and signs,
    one case, Cyrillic letters as Latin ones, one sound written one way, no letter twice in a row — «Сильвер Соул»
    and «silver soul» are «silversoul»."""
    text = _series_name(bare_name(name)).translate(_LATIN)
    for wrote, sound in _SOUNDS:
        text = text.replace(wrote, sound)
    return re.sub(r"(.)\1+", r"\1", text.replace(" ", ""))


def named_after(folder_name: str, works: list) -> bool:
    """(v5.44.166) A folder holding several manga is named after one of them («silver soul»: «Сильвер Соул», its
    twins and the others) — the works of one name: their сборка.  A folder of a kind («bdsm», «яой», «прочитано»,
    an author's) is named after none of its manga: each of them is a manga by itself."""
    mine = name_skeleton(folder_name)
    if len(mine) < 3:
        return False
    for work in works:
        if name_skeleton(work.name) == mine:
            return True
        base, number = tail_number(bare_name(work.name))
        if number is not None and name_skeleton(base) == mine:
            return True
    return False


# ── (v5.44.125) candidates: what may be a manga, by the names alone ─────────────────────────────────────────────────
PARTS_WORD = "Глава"                    # the parts of one manga in its copy — its chapters: «Глава 0» … «Глава 12»


def side_parts(work: "Work") -> bool:
    """(v5.44.125) The parts of ``work`` are folders of one name with a number at its end side by side («М4. Сильвер
    Соул 0» … «12» — «части»), not the chapter folders of a title («Дом у маяка/Глава 1» … — «главы»): their labels
    in the copy («Глава N») are not their folders' own names."""
    return len(work.parts) > 1 and all(p.label.startswith(PARTS_WORD + " ") and Path(p.path).name != p.label
                                       for p in work.parts)


@dataclass
class Part:
    """One part of a manga: a folder of its pages (or an archive of them)."""
    path: str
    label: str = ""                      # its folder in the copy («Глава 7», «Vol 1 Chapter 3»); "" — the pages of a
    #                                      manga of one folder lie right in its copy
    number: float = 0.0                  # its place among the parts
    archive: bool = False                # a .cbz/.cbr/.cb7 file, not a folder

    def public(self) -> dict:
        return {"path": self.path, "label": self.label, "number": self.number, "archive": self.archive}


@dataclass
class Work:
    """One manga of a candidate: its name (no «М4.», no part's number), its parts in order, the folders that are its
    own (their files are its pages)."""
    key: str
    name: str
    path: str                            # its folder — or, its parts lying beside others, «<their folder>/<its name>»
    parts: list = field(default_factory=list)       # [Part]
    roots: list = field(default_factory=list)       # [str] the folders whose files are its own
    why: str = ""
    linked: bool = False                 # a folder «Из архива» linked where it lies (its .lb/source.json)

    def public(self) -> dict:
        return {"key": self.key, "name": self.name, "path": self.path, "parts": [p.public() for p in self.parts],
                "roots": list(self.roots), "why": self.why, "linked": self.linked}

    @classmethod
    def of(cls, data: dict) -> "Work":
        parts = [Part(path=str(p.get("path") or ""), label=str(p.get("label") or ""),
                      number=float(p.get("number") or 0), archive=bool(p.get("archive")))
                 for p in data.get("parts") or [] if isinstance(p, dict) and p.get("path")]
        return cls(key=str(data.get("key") or ""), name=str(data.get("name") or ""), path=str(data.get("path") or ""),
                   parts=parts, roots=[str(r) for r in data.get("roots") or [] if r], why=str(data.get("why") or ""),
                   linked=bool(data.get("linked")))


@dataclass
class Candidate:
    """A folder of the parser that may be a manga: one manga, or (``is_set``) several of one folder — their сборка."""
    key: str
    path: str
    name: str
    works: list = field(default_factory=list)       # [Work] in the folder's order
    is_set: bool = False
    refused: bool = False                # the owner said «Это не манга» (of it or of a folder above it)

    def public(self) -> dict:
        return {"key": self.key, "path": self.path, "name": self.name, "set": self.is_set, "refused": self.refused,
                "works": [w.public() for w in self.works]}

    @classmethod
    def of(cls, data: dict) -> "Candidate":
        return cls(key=str(data.get("key") or ""), path=str(data.get("path") or ""), name=str(data.get("name") or ""),
                   works=[Work.of(w) for w in data.get("works") or [] if isinstance(w, dict)],
                   is_set=bool(data.get("set")), refused=bool(data.get("refused")))

    def roots(self) -> list:
        return [r for w in self.works for r in w.roots]


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _junk(name: str) -> bool:
    """A folder's name that names nothing («манга», «Новая папка (2)», «Разобрать», «2023»): never a сборка's."""
    from core.manga_clues import junk_name
    try:
        return junk_name(name)
    except Exception:  # noqa: BLE001 - the words of manga_clues not there: take no name for a сборка's
        return True


def _natural(text) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(text))]


def find(folders: dict, root: Path, refused: set, *, linked: "set | None" = None) -> list:
    """The candidates of the folders looked at (``folders`` — {Path: :class:`Folder`}), by their names and make-up
    alone (v5.44.125): a folder of pages by numbers (:data:`LONE_PAGES` at least; a folder named as a chapter — two),
    one with ComicInfo.xml, one of archives only; folders of numbered pages side by side with one name and a number at
    its end — one manga's parts; a folder of numbered pages beside a manga — a manga too (its folder holds manga); a
    folder of several manga named after one of them — their сборка ((v5.44.166) :func:`named_after`; a folder of a
    kind — each manga by itself).  ``linked`` — folders «Из архива» linked where they lie (a title there)."""
    root = Path(root)
    rk = _norm(root)
    linked = linked or set()
    numbered = {p: f for p, f in folders.items() if _norm(p) != rk and pages_folder(f.pictures, least=MIN_PAGES)}
    units: dict = {}
    for path, folder in folders.items():
        if _norm(path) == rk:
            continue                                        # pages right in the parser's folder: never a manga
        if _norm(path) in linked:
            units[path] = "привязан к сайту («Из архива»)"
        elif folder.comic_info and folder.pictures:
            units[path] = "ComicInfo.xml"
        elif chapter_like(path.name) and pages_folder(folder.pictures, named=True):
            units[path] = "папка главы"
        elif pages_folder(folder.pictures, least=LONE_PAGES):
            units[path] = "страницы по номерам"
        elif folder.archives and not folder.pictures and not folder.dirs:
            units[path] = "архивы глав (.cbz)"
    groups: dict = {}                                       # one manga's parts side by side
    for path in numbered:
        base, number = tail_number(bare_name(path.name))
        if number is not None and _series_name(base):
            groups.setdefault((path.parent, _series_name(base)), []).append(path)
    for paths in groups.values():
        if len(paths) >= 2:
            for path in paths:
                units.setdefault(path, "части одной манги — папки страниц по номерам")
    holders = {p.parent for p in units if _norm(p.parent) != rk and not _junk(bare_name(p.parent.name))}
    for path in numbered:                                   # beside the manga of a folder: its manga too
        if path not in units and path.parent in holders:
            units[path] = "страницы по номерам рядом с мангой"
    # the works: a title's chapters (its folders named as chapters and tomes), else one folder — or parts side by side
    titled: dict = {}
    alone: list = []
    for path in sorted(units, key=lambda p: _natural(str(p))):
        title = _title_of(path, root)
        if title != path:
            titled.setdefault(title, []).append(path)
        else:
            alone.append(path)
    works: list = []                                        # (its folder's place: the parent, Work)
    for title, chapters in titled.items():
        parts = []
        if title in units:                                  # pages right in the title's folder too: its first part
            alone.remove(title)
            parts.append(Part(path=str(title), label="1", number=0))
        chapters.sort(key=lambda p: _natural(p.relative_to(title).as_posix()))
        for n, chapter in enumerate(chapters, len(parts) + 1):
            label = " ".join(chapter.relative_to(title).parts)
            parts.append(Part(path=str(chapter), label=label, number=float(n)))
        why = units.get(chapters[0], "")
        works.append((title.parent, Work(key=key_of(title), name=bare_name(title.name), path=str(title), parts=parts,
                                         roots=[str(title)], why=why or "главы — папки страниц",
                                         linked=_norm(title) in linked or any(_norm(c) in linked for c in chapters))))
    by_base: dict = {}
    for path in alone:
        base, number = tail_number(bare_name(path.name))
        key = (path.parent, _series_name(base)) if number is not None and _series_name(base) else (path, "")
        by_base.setdefault(key, []).append((number, path))
    for (parent, base), items in by_base.items():
        numbers = [n for n, _p in items]
        if base and len(items) >= 2 and len(set(numbers)) == len(numbers):
            items.sort(key=lambda x: (x[0], _natural(x[1].name)))
            shown = tail_number(bare_name(items[0][1].name))[0]
            others = [p for p in units if p.parent == parent and p not in {x[1] for x in items}]
            own = _norm(parent) != rk and not others and not _junk(bare_name(parent.name)) \
                and _series_name(bare_name(parent.name)) == base       # «Series X/Series X 01», «…/Series X 02»
            home = parent if own else parent / shown
            parts = [Part(path=str(p), label=f"{PARTS_WORD} {n}", number=float(n)) for n, p in items]
            whys = {units[p] for _n, p in items}
            why = next((w for w in ("привязан к сайту («Из архива»)", "ComicInfo.xml") if w in whys),
                       "части одной манги — папки страниц по номерам")
            works.append((parent.parent if own else parent,
                          Work(key=key_of(home), name=shown, path=str(home), parts=parts,
                               roots=[str(p) for _n, p in items], why=why,
                               linked=any(_norm(p) in linked for _n, p in items))))
            continue
        for _number, path in items:
            folder = folders.get(path)
            if units[path].startswith("архивы") and folder is not None:
                parts = [Part(path=str(path / name), label=Path(name).stem, number=float(n), archive=True)
                         for n, name in enumerate(sorted(folder.archives, key=_natural), 1)]
            else:
                parts = [Part(path=str(path), label="", number=0)]
            works.append((path.parent, Work(key=key_of(path), name=bare_name(path.name), path=str(path), parts=parts,
                                            roots=[str(path)], why=units[path], linked=_norm(path) in linked)))
    # an archive in a folder of other things (not a manga's own) — a manga by itself («Single Book.cbz»)
    owned = {_norm(r) for _parent, w in works for r in w.roots}
    for path, folder in folders.items():
        if not folder.archives or not (folder.pictures or folder.dirs) or _norm(path) == rk or _under(path, owned):
            continue
        for name in sorted(folder.archives, key=_natural):
            lone = path / name
            works.append((path, Work(key=key_of(lone), name=bare_name(Path(name).stem), path=str(lone),
                                     parts=[Part(path=str(lone), label="", number=0, archive=True)],
                                     roots=[str(lone)], why="архив главы (.cbz)")))
    # a folder of several manga named after one of them — their сборка (by its own name), the others — each its own
    homes: dict = {}
    for parent, work in works:
        homes.setdefault(parent, []).append(work)
    out: list = []
    for parent, items in homes.items():
        items.sort(key=lambda w: _natural(Path(w.roots[0]).name if w.roots else w.name))
        named = _norm(parent) != rk and not _junk(bare_name(parent.name))
        if len(items) >= 2 and named and not any(_norm(parent) in {_norm(r) for r in w.roots} for w in items) \
                and named_after(parent.name, items):
            out.append(Candidate(key=key_of(parent), path=str(parent), name=parent.name, works=items, is_set=True,
                                 refused=_under(parent, refused)))
            continue
        for work in items:
            out.append(Candidate(key=work.key, path=work.path, name=Path(work.path).name, works=[work],
                                 refused=any(_under(Path(r), refused) for r in work.roots)))
    for cand in out:                                    # a manga of a сборка the owner refused alone: «Это не манга»
        if not cand.refused and cand.is_set and all(any(_under(Path(r), refused) for r in w.roots)
                                                    for w in cand.works):
            cand.refused = True
    return sorted(out, key=lambda c: _natural(c.path))


def pages_of(part: "Part", *, strict: bool = False) -> list:
    """(v5.44.125) [(the page's number or None, its file)] of a part — by their numbers (the names alone; an archive —
    [(None, the archive)]). Recognition can request an error when a folder or one of its pages cannot be listed."""
    path = Path(part.path)
    if part.archive:
        return [(None, path)]
    try:
        names = [n for n in os.listdir(path) if Path(n).suffix.lower() in IMAGES and not _aside(n)]
        if strict:
            names = [n for n in names if stat.S_ISREG((path / n).stat().st_mode)]
        else:
            names = [n for n in names if (path / n).is_file()]
    except OSError:
        if strict:
            raise
        return []
    numbers = dict(zip(names, page_numbers([Path(n).stem for n in names])))
    names.sort(key=lambda n: (numbers.get(n) is None, numbers.get(n) or 0, _natural(n)))
    return [(numbers.get(n), path / n) for n in names]


def candidate_titles(cands: list) -> dict:
    """(v5.44.125) The :class:`Title` of each work of the candidates (what ``STORE.titles`` gives the code of before)."""
    out: dict = {}
    for cand in cands:
        for work in cand.works:
            pages, first = 0, []
            for part in work.parts:
                if part.archive:                    # an archive's pages are not counted by their names
                    continue
                listed = pages_of(part)
                pages += len(listed)
                first += [str(p) for _n, p in listed[:FIRST_PAGES - len(first)]]
            title = Title(path=work.path, pages=pages, chapters=max(1, len(work.parts)),
                          archives=sum(1 for p in work.parts if p.archive), first=first[:FIRST_PAGES], why=work.why)
            out[_norm(work.path)] = title
    return out


# ── the owner's «Это не манга» ───────────────────────────────────────────────────────────────────────────────────
_LOCK = threading.Lock()


def _settings_file(name: str) -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / name


def not_manga() -> set:
    """The folders the owner said are not manga (normcase'd absolute paths)."""
    from core.file_safety import read_json_kept
    data = read_json_kept(_settings_file(NOT_FILE), list, what="not manga")
    return {os.path.normcase(os.path.abspath(str(p))) for p in data or [] if str(p or "").strip()} \
        if isinstance(data, list) else set()


def set_not_manga(path: str, value: bool = True) -> None:
    from core.file_safety import read_json_kept, write_json_kept
    with _LOCK:
        data = read_json_kept(_settings_file(NOT_FILE), list, what="not manga")
        items = [str(p) for p in data or [] if isinstance(p, str)] if isinstance(data, list) else []
        key = os.path.normcase(os.path.abspath(str(path)))
        items = [p for p in items if os.path.normcase(os.path.abspath(p)) != key]
        if value:
            items.append(str(path))
        write_json_kept(_settings_file(NOT_FILE), json.dumps(items, ensure_ascii=False, indent=1), list)
    if value:
        STORE.drop(path)


# ── the parser: its files by folders ─────────────────────────────────────────────────────────────────────────────────
def _aside(name: str) -> bool:
    return Path(name).stem.lower() in ASIDE


def folders_of(files: list) -> dict:
    """(v5.44.125) {folder: :class:`Folder`} of the run's files (their names; ComicInfo.xml beside the pictures)."""
    folders: dict = {}
    for path in files:
        parent = Path(path).parent
        folder = folders.get(parent)
        if folder is None:
            folder = folders[parent] = Folder(path=parent)
        if Path(path).suffix.lower() in IMAGES and not _aside(Path(path).name):
            folder.pictures.append(Path(path).name)
    for folder in folders.values():
        if folder.pictures:
            folder.comic_info = (folder.path / "ComicInfo.xml").is_file()
    known = set(folders)
    for parent in list(known):                      # (the number of folders below a folder of the run's files)
        node = parent.parent
        if node in folders:
            folders[node].dirs += 1
    return folders


def linked_here(folders) -> set:
    """(v5.44.125) The folders «Из архива» linked where they lie (versions 5.44.84–5.44.124): their ``.lb/source.json``
    names a title of a site — a manga confirmed by its link."""
    out = set()
    for parent in folders:
        try:
            if (Path(parent) / ".lb" / "source.json").is_file():
                out.add(_norm(parent))
        except OSError:
            continue
    return out


# ── the whole folder looked at ────────────────────────────────────────────────────────────────────────────────────
def scan_candidates(root: Path, *, stop_check=None) -> list:
    """(v5.44.125) The candidates of the parser's whole folder (:func:`find`; the names alone)."""
    root = Path(root)
    folders: dict = {}
    for top, dirs, names in os.walk(str(root)):
        if stop_check is not None and stop_check():
            break
        dirs[:] = [d for d in dirs if not d.startswith(".")]        # «.lb» of the program, the system's own
        pictures = [n for n in names if Path(n).suffix.lower() in IMAGES and not _aside(n)]
        archives = [n for n in names if Path(n).suffix.lower() in ARCHIVES]
        if not pictures and not archives:
            continue
        folders[Path(top)] = Folder(path=Path(top), pictures=pictures, archives=archives,
                                    comic_info="ComicInfo.xml" in names, dirs=len(dirs))
    return find(folders, root, not_manga(), linked=linked_here(folders))


def scan(root: Path, *, stop_check=None) -> dict:
    """{a work's folder: Title} of the parser's whole folder (the code of before reads titles; v5.44.125 — one Title
    for each manga of a candidate)."""
    return candidate_titles([c for c in scan_candidates(root, stop_check=stop_check) if not c.refused])


class Store:
    """``parser_manga.json``: {root, at, rules, candidates: [Candidate.public()], titles: [Title.public()]} — the last
    look at the parser's whole folder."""

    def __init__(self):
        self._lock = threading.Lock()
        self.stale = False

    def read(self) -> dict:
        from core.file_safety import read_json_kept
        data = read_json_kept(_settings_file(STORE_FILE), dict, what="parser manga")
        return data if isinstance(data, dict) else {}

    def _here(self, data: dict, root: str) -> bool:
        return not root or os.path.normcase(os.path.abspath(str(data.get("root") or ""))) == \
            os.path.normcase(os.path.abspath(str(root)))

    def titles(self, root: str = "") -> list:
        data = self.read()
        if not self._here(data, root):
            return []
        out = []
        for item in data.get("titles") or []:
            if isinstance(item, dict) and item.get("path"):
                out.append(Title(path=str(item["path"]), pages=int(item.get("pages") or 0),
                                 chapters=int(item.get("chapters") or 0), archives=int(item.get("archives") or 0),
                                 first=list(item.get("first") or [])[:FIRST_PAGES], why=str(item.get("why") or "")))
        return out

    def candidates(self, root: str = "") -> list:
        """(v5.44.125) The candidates of the last look (``[]`` — another folder, or a look by the rules of before)."""
        data = self.read()
        if not self._here(data, root) or int(data.get("rules") or 1) != RULES:
            return []
        return [Candidate.of(c) for c in data.get("candidates") or [] if isinstance(c, dict) and c.get("key")]

    def keys(self, root: str = "") -> set:
        return {os.path.normcase(os.path.abspath(t.path)) for t in self.titles(root)}

    def due(self, root: str, now: float | None = None) -> bool:
        """The whole folder is to be looked at: never yet, another folder, long ago, a manga seen since, or (v5.44.113)
        looked at by rules of before (:data:`RULES`)."""
        data = self.read()
        if self.stale or not data or os.path.normcase(os.path.abspath(str(data.get("root") or ""))) != \
                os.path.normcase(os.path.abspath(str(root))) or int(data.get("rules") or 1) != RULES:
            return True
        return (now or time.time()) - float(data.get("at") or 0) > SCAN_EVERY

    def write(self, root: str, cands: list) -> None:
        from core.file_safety import write_json_kept
        items = [c.public() for c in cands]
        titles = sorted((t.public() for t in candidate_titles([c for c in cands if not c.refused]).values()),
                        key=lambda x: x["path"].lower())
        with self._lock:
            write_json_kept(_settings_file(STORE_FILE), json.dumps(
                {"root": str(root), "at": int(time.time()), "rules": RULES, "candidates": items, "titles": titles},
                ensure_ascii=False, indent=1), dict)
            self.stale = False

    def drop(self, path: str) -> None:
        """A folder taken out (the owner: «Это не манга»), and what is under it."""
        from core.file_safety import write_json_kept
        key = os.path.normcase(os.path.abspath(str(path)))
        with self._lock:
            data = self.read()
            items = [t for t in data.get("titles") or [] if isinstance(t, dict) and not _under(
                Path(str(t.get("path") or "")), {key})]
            if data:
                data["titles"] = items
                for cand in data.get("candidates") or []:
                    if isinstance(cand, dict) and (_under(Path(str(cand.get("path") or "")), {key}) or any(
                            _under(Path(str(r)), {key}) for w in cand.get("works") or [] for r in w.get("roots") or [])):
                        cand["refused"] = True
                write_json_kept(_settings_file(STORE_FILE), json.dumps(data, ensure_ascii=False, indent=1), dict)

    def accept(self) -> None:
        """(v5.44.166) The owner's «Это не манга» taken back (``set_not_manga(path, False)`` — «Перепроверить» of a
        row he refused): the candidates kept say «refused» by what he refuses now (as :func:`find` says it), and
        the folder is looked at anew at its next look (the titles of the ones taken back are in the list then)."""
        from core.file_safety import write_json_kept
        refused = not_manga()
        with self._lock:
            data = self.read()
            if not data:
                return
            for made in data.get("candidates") or []:
                if not isinstance(made, dict):
                    continue
                cand = Candidate.of(made)
                alone = [any(_under(Path(r), refused) for r in w.roots) for w in cand.works]
                made["refused"] = (_under(Path(cand.path), refused) or (bool(alone) and all(alone))) \
                    if cand.is_set else any(alone)
            write_json_kept(_settings_file(STORE_FILE), json.dumps(data, ensure_ascii=False, indent=1), dict)
            self.stale = True


STORE = Store()
_LOOK_LOCK = threading.Lock()


def look(settings: dict, *, stop_check=None, say=None) -> list:
    """The parser's whole folder looked at now and kept (:class:`Store`): its titles (one for each manga of its
    candidates, v5.44.125 — what the code of before reads)."""
    root = str(settings.get("root") or "").strip()
    if not root or not Path(root).is_dir():
        if say is not None:
            say("Папка, из которой парсер берёт файлы, не найдена — её выбирают на странице «Парсер»")
        return []
    with _LOOK_LOCK:                                 # (v5.44.113) one look at a time (the background of «Из архива»,
        started = time.time()                        # the comics' duty, «Проверить сейчас»); never another's answer:
        #                                              it may have begun before what this caller put into the folder
        cands = scan_candidates(Path(root), stop_check=stop_check)
        if stop_check is not None and stop_check():
            return []
        STORE.write(root, cands)
    titles = candidate_titles([c for c in cands if not c.refused])
    _LOG.info("manga in the parser's folder: %d candidates, %d titles (%.1f s)", len(cands), len(titles),
              time.time() - started)
    if say is not None:
        say(f"Папка парсера просмотрена: похожих на мангу папок — {len(cands)}")
    return list(titles.values())
