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
* it holds a manga's archives (.cbz, .cbr, .cb7) — two or more, or one and nothing else;
* (v5.44.111; the owner: «если 15+ картинок … то в мангу», of his comics — each in a folder of its own) at least
  :data:`MANY_PAGES` pictures named by a page's number in one run, whatever their sizes — and the names may share a
  word before or after the number («Silver_Soul_01.jpg», «01_rus.jpg»: :func:`page_numbers`).

Its title is the first folder above it whose name is not a chapter's or a tome's («Ван-Пис/Глава 1» → «Ван-Пис»;
«Архив/2023/Some Title/Vol 1/Chapter 3» → «Some Title»), never the parser's folder itself; a folder of pages with a
name of its own is a title by itself (a one-shot, a doujin).  Everything under a title is its own: the parser leaves it
(:func:`split`) to «Скачать → Из архива» (``core/manga_archive.py``), which finds out what it is.  The art of the booru
sites is never named by pages' numbers (MD5s, «12345678_p0», «id - tags»), so it goes on to the sites as before.  A
folder the owner said is not a manga («Это не манга») — never, nor anything under it.

The titles found by the last look at the whole folder (:func:`scan`) — ``parser_manga.json`` in the settings folder.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

_LOG = logging.getLogger("local_booru.manhwa")

IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".jxl"}
ARCHIVES = {".cbz", ".cbr", ".cb7"}
MIN_PAGES = 3                           # pages of a chapter not named as one, beside another such chapter
NAMED_PAGES = 2                         # pages of a folder named as a chapter
LONE_PAGES = 12                         # pages of a folder by itself (a one-shot, a doujin) — and of one size
MANY_PAGES = 15                         # (v5.44.111) …or this many, whatever their sizes
SHARE = 0.8                             # pictures named by a page's number at least
RUN = 0.8                               # the numbers one run: as many as the run's length × this at least
STORE_FILE = "parser_manga.json"
NOT_FILE = "parser_not_manga.json"
SCAN_EVERY = 6 * 3600                   # the whole folder looked at again after this long (seconds)
RULES = 2                               # (v5.44.113) +1 when the rules change: the folder is looked at again at once
                                        # (2: v5.44.111 — 15 pages by numbers of any sizes)
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


def page_number(stem: str):
    """The number of a page by its file's name (without the extension) — None: not a page's name."""
    found = _PAGE.match(str(stem or "").strip())
    return int(found.group(1)) if found else None


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
    numbers = sorted({n for n in page_numbers([Path(p).stem for p in pictures]) if n is not None})
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


def _one_size(folder: Path, pictures: list) -> bool:
    """The pages of a folder by itself are a book's: upright (or a strip) and of one width (the first ones looked at:
    only the pictures' headers are read) — the art of a post («1.jpg … 9.jpg» of an artist's set) is not."""
    from PIL import Image
    sizes = []
    for name in sorted(pictures, key=lambda p: (page_number(Path(p).stem) or 0, p))[:8]:
        try:
            with Image.open(folder / name) as im:
                sizes.append(im.size)
        except (OSError, ValueError):
            continue
    if len(sizes) < 4:
        return False
    upright = [w for w, h in sizes if w > 0 and h >= 1.25 * w]
    if len(upright) < 0.75 * len(sizes):
        return False
    widths = sorted(upright)
    middle = widths[len(widths) // 2]
    return sum(1 for w in widths if abs(w - middle) <= 0.15 * middle) >= 0.75 * len(widths)


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


def decide(folders: dict, root: Path, not_manga: set) -> dict:
    """{title folder: Title} of the folders looked at (``folders`` — {Path: :class:`Folder`})."""
    titles: dict = {}

    def title_at(path: Path, why: str) -> Title:
        key = os.path.normcase(os.path.abspath(str(path)))
        if key not in titles:
            titles[key] = Title(path=str(path), why=why)
        return titles[key]

    root_key = os.path.normcase(os.path.abspath(str(root)))
    chapters_in: dict = {}                           # a folder → how many of its folders are numbered pages
    for path, folder in folders.items():
        if pages_folder(folder.pictures, least=MIN_PAGES):
            chapters_in[path.parent] = chapters_in.get(path.parent, 0) + 1
    for path, folder in sorted(folders.items(), key=lambda kv: str(kv[0]).lower()):
        if _under(path, not_manga) or os.path.normcase(os.path.abspath(str(path))) == root_key:
            continue
        named = chapter_like(path.name)
        if folder.comic_info and folder.pictures:
            why = "ComicInfo.xml"
        elif named and pages_folder(folder.pictures, named=True):
            why = "папка главы"
        elif chapters_in.get(path.parent, 0) >= 2 and pages_folder(folder.pictures, least=MIN_PAGES):
            why = "главы — папки страниц по номерам"
        elif pages_folder(folder.pictures, least=LONE_PAGES) and _one_size(path, folder.pictures):
            why = "страницы по номерам, одного размера"
        elif pages_folder(folder.pictures, least=MANY_PAGES):
            why = f"{MANY_PAGES} и больше страниц по номерам"
        else:
            why = ""
        if why:
            title = title_at(_title_of(path, root), why)
            pictures = sorted(p for p in folder.pictures if Path(p).suffix.lower() in IMAGES)
            numbers = dict(zip(pictures, page_numbers([Path(p).stem for p in pictures])))
            pictures.sort(key=lambda p: (numbers.get(p) or 0, p))
            title.pages += len(pictures)
            title.chapters += 1
            if len(title.first) < FIRST_PAGES:
                title.first += [str(path / p) for p in pictures[:FIRST_PAGES - len(title.first)]]
        elif folder.archives and not folder.pictures and not folder.dirs:     # a title's tomes or chapters, or one
            title = title_at(_title_of(path, root), "архивы глав (.cbz)")
            title.archives += len(folder.archives)
            title.chapters += len(folder.archives)
    return titles


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


# ── the parser: what it leaves ────────────────────────────────────────────────────────────────────────────────────
def _aside(name: str) -> bool:
    return Path(name).stem.lower() in ASIDE


def split(root: Path, files: list, libraries: tuple = ()) -> tuple:
    """(the files the parser parses, {title folder: Title}) — the pictures under a manga's folder are left out; so
    are those under the titles the last look found (a linked title's cover the program put beside its archives),
    under a folder the program keeps a title in (``.lb``), and under the program's own folders of manga, manhwa and
    videos (``libraries``)."""
    root = Path(root)
    folders: dict = {}
    for path in files:
        parent = Path(path).parent
        folder = folders.get(parent)
        if folder is None:
            folder = folders[parent] = Folder(path=parent)
        if Path(path).suffix.lower() in IMAGES and not _aside(Path(path).name):
            folder.pictures.append(Path(path).name)
    for folder in folders.values():                  # ComicInfo.xml beside the pictures
        if folder.pictures:
            folder.comic_info = (folder.path / "ComicInfo.xml").is_file()
    titles = decide(folders, root, not_manga())
    for parent in folders:                           # a folder the program keeps a title in (its .lb/source.json)
        if (parent / ".lb" / "source.json").is_file():
            key = os.path.normcase(os.path.abspath(str(parent)))
            titles.setdefault(key, Title(path=str(parent), why="тайтл программы (.lb)"))
    keys = set(titles) | STORE.keys(str(root)) | {os.path.normcase(os.path.abspath(str(x))) for x in libraries if x}
    if not keys:
        return list(files), {}
    kept = [p for p in files if not _under(Path(p).parent, keys)]
    if any(key not in STORE.keys(str(root)) for key in titles):
        STORE.stale = True                           # a manga the last look did not see: looked at again soon
    return kept, titles


# ── the whole folder looked at (for «Из архива») ──────────────────────────────────────────────────────────────────
def scan(root: Path, *, stop_check=None) -> dict:
    """{title folder: Title} of the parser's whole folder — and the lone archives (``None`` keys are not used)."""
    root = Path(root)
    folders: dict = {}
    lone: list = []
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
    titles = decide(folders, root, not_manga())
    keys = set(titles)
    refused = not_manga()
    for path, folder in folders.items():             # an archive in a folder of other things — a title by itself
        if folder.archives and (folder.pictures or folder.dirs) and not _under(path, keys) \
                and not _under(path, refused):
            for name in folder.archives:
                lone.append(Title(path=str(path / name), archives=1, chapters=1, why="архив главы (.cbz)"))
    for title in lone:
        titles[os.path.normcase(os.path.abspath(title.path))] = title
    return titles


class Store:
    """``parser_manga.json``: {root, at, titles: [Title.public()]} — the last look at the parser's whole folder."""

    def __init__(self):
        self._lock = threading.Lock()
        self.stale = False

    def read(self) -> dict:
        from core.file_safety import read_json_kept
        data = read_json_kept(_settings_file(STORE_FILE), dict, what="parser manga")
        return data if isinstance(data, dict) else {}

    def titles(self, root: str = "") -> list:
        data = self.read()
        if root and os.path.normcase(os.path.abspath(str(data.get("root") or ""))) != \
                os.path.normcase(os.path.abspath(str(root))):
            return []
        out = []
        for item in data.get("titles") or []:
            if isinstance(item, dict) and item.get("path"):
                out.append(Title(path=str(item["path"]), pages=int(item.get("pages") or 0),
                                 chapters=int(item.get("chapters") or 0), archives=int(item.get("archives") or 0),
                                 first=list(item.get("first") or [])[:FIRST_PAGES], why=str(item.get("why") or "")))
        return out

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

    def write(self, root: str, titles: dict) -> None:
        from core.file_safety import write_json_kept
        items = sorted((t.public() for t in titles.values()), key=lambda x: x["path"].lower())
        with self._lock:
            write_json_kept(_settings_file(STORE_FILE), json.dumps(
                {"root": str(root), "at": int(time.time()), "rules": RULES, "titles": items}, ensure_ascii=False,
                indent=1), dict)
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
                write_json_kept(_settings_file(STORE_FILE), json.dumps(data, ensure_ascii=False, indent=1), dict)


STORE = Store()
_LOOK_LOCK = threading.Lock()


def look(settings: dict, *, stop_check=None, say=None) -> list:
    """The parser's whole folder looked at now and kept (:class:`Store`): its titles."""
    root = str(settings.get("root") or "").strip()
    if not root or not Path(root).is_dir():
        if say is not None:
            say("Папка, из которой парсер берёт файлы, не найдена — её выбирают в «Настройки → Папки»")
        return []
    with _LOOK_LOCK:                                 # (v5.44.113) one look at a time (the background of «Из архива»,
        started = time.time()                        # the comics' duty, «Проверить сейчас»); never another's answer:
        #                                              it may have begun before what this caller put into the folder
        titles = scan(Path(root), stop_check=stop_check)
        if stop_check is not None and stop_check():
            return []
        STORE.write(root, titles)
    _LOG.info("manga in the parser's folder: %d titles (%.1f s)", len(titles), time.time() - started)
    if say is not None:
        say(f"Папка парсера просмотрена: манги и манхвы — {len(titles)}")
    return list(titles.values())
