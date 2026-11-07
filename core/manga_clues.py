"""(v5.44.81) What tells the title of a folder of «Скачать → Из архива» besides its name (``core/manga_archive.py``).

The owner (5.11.2026): «у меня часто манга и манхва без названия или с названием но мне надо что бы парсер сам находил
и не парсил такое»; of the ways to know them — «нужен не один вариант а несколько если не сработает».  Nothing here asks
the network; the files are only read (a few of them, a little of each).

* :func:`junk_name` — a name that is not a title's: «Новая папка (2)», «manga», «Загрузки», «123», «Том 3», «IMG_0042»,
  a hash, a date.  The program never searches by one (it would find something else and link it).
* :func:`clues` — what the folder's own files say:

  - the links of the sites the program knows: Windows' shortcuts (``*.url``), macOS' (``*.webloc``), a small ``*.txt``
    with one title's link, the descriptions (``info.json``, ``meta.json``, Kotatsu's ``index.json``: ``url``,
    ``public_url``…), ComicInfo.xml's ``<Web>`` — in the folder, in its first chapter folders, in its first archives;
  - the names: the descriptions' ``title``/``alt_titles``/``manga``/``series``/``name``…, ComicInfo.xml's
    ``<Series>``/``<LocalizedSeries>``, Tachiyomi's and Mihon's ``details.json`` (``title``), and the name the chapter
    files and folders share («Solo Leveling - Chapter 001.cbz», «Solo Leveling - Chapter 002.cbz» → «Solo Leveling»).

  Each with where it was found («ComicInfo.xml в «Том 1.cbz»»), which the page shows.
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from core import manhwa_sources as S

READ_LIMIT = 256 * 1024                # a file of names or links read at most this much
ARCHIVES = 3                           # archives of a folder looked into (their ComicInfo.xml, index.json)
CHAPTER_FOLDERS = 3                    # chapter folders looked into
NAMED_FILES = 40                       # chapter files and folders whose shared name is looked for
LINKS = 6                              # links kept
NAMES = 6                              # names kept
ARCHIVE_TYPES = (".cbz", ".zip")
DESCRIPTIONS = ("info.json", "meta.json", "index.json", "details.json")

# the keys of a description that hold the title's name, its other names, a link (in the order they are trusted:
# gallery-dl's «manga» is the title, its «title» — a chapter's)
NAME_KEYS = ("manga", "series", "title", "name", "main_name", "rus_name", "title_ru", "russian", "eng_name",
             "title_en", "english", "secondary_name", "title_english", "title_romaji", "romaji", "japanese",
             "title_japanese")
ALT_KEYS = ("alt_titles", "alt_title", "title_alt", "altTitle", "altTitles", "alt_names", "other_names", "aliases",
            "synonyms", "another_name")
LINK_KEYS = ("url", "public_url", "publicUrl", "source_url", "sourceUrl", "link", "web", "href", "manga_url",
             "source")

_URL = re.compile(r"https?://[^\s\"'<>\]\[)(]+", re.I)

# a name that names no title (compared letters and digits alone, one case — ``S.name_key``)
_JUNK_WORDS = {S.name_key(w) for w in (
    "новая папка", "new folder", "новая", "новый", "новое", "new", "untitled", "без названия", "безымянный", "безымянная",
    "noname", "no name", "unnamed", "unknown", "без имени", "неизвестно", "неизвестный", "manga", "манга", "manhwa", "манхва",
    "manhua", "маньхуа", "comics", "comic", "комиксы", "комикс", "webtoon", "webtoons", "вебтун", "вебтуны", "read",
    "читать", "прочитано", "прочитать", "почитать", "download", "downloads", "downloaded", "скачанное", "скачано",
    "скачать", "загрузки", "загрузка", "temp", "tmp", "temporary", "временное", "разное", "misc", "other", "others",
    "прочее", "другое", "archive", "архив", "backup", "бэкап", "copy", "копия", "folder", "папка", "files", "файлы",
    "чтиво", "хентай", "hentai", "doujin", "doujinshi", "додзинси", "додзин", "images", "pictures", "картинки",
    "изображения", "img", "image", "dsc", "photo", "фото", "screenshot", "скриншот", "снимок", "scan", "scans",
    "сканы", "raw", "raws", "telegram desktop", "telegram", "chrome", "desktop", "рабочий стол", "documents",
    "документы", "chapters", "chapter", "главы", "глава", "volumes", "volume", "тома", "том", "все тома", "all",
    "все", "complete", "completed", "end", "ongoing", "rus", "eng", "ru", "en", "jp", "kr", "cn", "test", "тест",
    "lib", "library", "библиотека", "collection", "коллекция", "сборник", "favorites", "favourites", "избранное",
    "best", "лучшее", "top", "топ", "toread", "на потом", "later", "unsorted", "sort", "неразобранное",
    "разобрать", "part", "часть", "book", "книга", "xxx")}
# a copy Windows made: «Name - Copy», «Name - копия (2)», «Копия Name», «Copy of Name»
_COPY = re.compile(r"\s+[-–—]\s+(?:копия|copy)(?:\s*\(\d+\))?\s*$|^\s*(?:copy of|копия)\s+", re.I)
_HEX = re.compile(r"[0-9a-f]{8,}")
_LEAD_NUMBER = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*[-–—._)]\s*")
_SPLIT = re.compile(r"[\s\-–—_.,:;!?/\\()\[\]{}«»\"'+&]+")


def junk_name(name) -> bool:
    """A name that is not a title's: empty once the group, the tomes and chapters are taken away («Том 3», «Глава
    1-20»), digits alone (a number, a date), one letter, words of the folders alone («Новая папка (2)», «Manga 2»,
    «Скачанное», «IMG_0042»), a hash («a3f9c2d17b»)."""
    from core.manga_archive import clean_name
    text = str(name or "")
    for _ in range(2):                                # «Name - Copy (2) - Copy»
        text = _COPY.sub("", text)
    cleaned = clean_name(text)
    key = S.name_key(cleaned)
    if len(key) < 2 or key.isdigit():
        return True
    if not re.search(r"\s", cleaned.strip()) and (_HEX.fullmatch(key) or len(key) >= 10 and key.isascii() and
                                                  sum(c.isdigit() for c in key) >= 3 and
                                                  sum(c.isalpha() for c in key) >= 3):
        return True                                   # «a3f9c2d17b», «x7Hk2pQ9mL4» — a hash, not a name
    words = [w for w in (S.name_key(w) for w in _SPLIT.split(cleaned)) if w and not w.isdigit()]
    if not words or "".join(words) in _JUNK_WORDS:   # «Новая папка 2», «Telegram Desktop», «Рабочий стол»
        return True
    return all(w in _JUNK_WORDS for w in words)       # «Manga Downloads», «Скачанное разное»


def good_names(names) -> list:
    """The names that are titles' (not :func:`junk_name`), each once (by ``S.name_key``)."""
    out, seen = [], set()
    for name in names:
        if not isinstance(name, str):
            continue
        name = " ".join(name.split())
        key = S.name_key(name)
        if not name or not key or key in seen or junk_name(name):
            continue
        seen.add(key)
        out.append(name)
    return out


@dataclass
class Clues:
    """What the folder's own files say: ``links`` — [(a link of a site the program knows, where)], ``names`` — [(a
    name, where)]; ``looked`` — the files looked into («info.json», «ComicInfo.xml в «Том 1.cbz»»…); (v5.44.125)
    ``pools`` — [(the number of a pool of e621, where)] (a comic: ``core/parser_manga``)."""
    links: list = field(default_factory=list)
    names: list = field(default_factory=list)
    looked: list = field(default_factory=list)
    pools: list = field(default_factory=list)

    def add_link(self, url: str, where: str) -> None:
        url = str(url or "").strip().rstrip(".,;")
        pool = e621_pool(url)
        if pool:
            if len(self.pools) < LINKS and all(p != pool for p, _w in self.pools):
                self.pools.append((pool, where))
            return
        if not url or len(self.links) >= LINKS or any(u == url for u, _w in self.links):
            return
        if known_link(url):
            self.links.append((url, where))

    def add_name(self, name, where: str) -> None:
        if not isinstance(name, str) or len(self.names) >= NAMES:
            return
        name = " ".join(name.split())[:200]
        key = S.name_key(name)
        if not key or junk_name(name) or any(S.name_key(n) == key for n, _w in self.names):
            return
        self.names.append((name, where))


def known_link(url: str) -> bool:
    """A link of a title on a site the program knows — or of one of MangaDex's chapters (its title is asked)."""
    if S.source_for(url)[0] is not None:
        return True
    return mangadex_chapter(url) != ""


_POOL = re.compile(r"^https?://(?:www\.)?e(?:621|926)\.net/pools/(\d+)(?:[/?#].*)?$", re.I)


def e621_pool(url: str) -> int:
    """(v5.44.125) The number of a pool of e621 in its link (``e621.net/pools/15766``); 0 — not one."""
    found = _POOL.match(str(url or "").strip())
    return int(found.group(1)) if found else 0


def mangadex_chapter(url: str) -> str:
    """The id of one of MangaDex's chapters in its link (``mangadex.org/chapter/<id>``); "" — not one."""
    m = re.match(r"https?://(?:www\.)?mangadex\.org/chapter/(" + S.MangaDex.UUID + r")", str(url or "").strip(), re.I)
    return m.group(1).lower() if m else ""


def title_of(url: str) -> tuple:
    """(the site, the title's key) a link names — for telling two links of one title apart; ("", url) — a chapter
    of MangaDex (its title is known only by asking)."""
    source, key = S.source_for(url)
    return (source.name, key) if source is not None else ("", url)


# ── reading ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _small(path: Path) -> bytes:
    try:
        if path.stat().st_size > READ_LIMIT:
            return b""
        return path.read_bytes()
    except OSError:
        return b""


def _member(zf: zipfile.ZipFile, wanted: str) -> bytes:
    """The archive's file named ``wanted`` (any case, any folder of it), at most :data:`READ_LIMIT`; b"" — none."""
    for info in zf.infolist():
        if Path(info.filename).name.lower() == wanted and not info.is_dir():
            if info.file_size > READ_LIMIT:
                return b""
            try:
                return zf.read(info)
            except (OSError, zipfile.BadZipFile, RuntimeError, ValueError, NotImplementedError):
                return b""
    return b""


def _text(data: bytes) -> str:
    for coding in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1251"):
        try:
            return data.decode(coding)
        except (UnicodeDecodeError, ValueError):
            continue
    return ""


def names_in(data: dict) -> list:
    """The names a description gives its title: :data:`NAME_KEYS`, then :data:`ALT_KEYS` (nhentai's {english,
    japanese, pretty}, MangaDex's [{"en": …}] too); gallery-dl's (it has «manga») — without its chapter's «title»."""
    out: list = []
    if not isinstance(data, dict):
        return out
    chapter_like = isinstance(data.get("manga"), str) and bool(data.get("manga").strip())
    for key in NAME_KEYS:
        if chapter_like and key in ("title", "name"):
            continue
        value = data.get(key)
        if isinstance(value, dict):                  # nhentai-style {english, japanese, pretty}
            value = [value.get("pretty"), value.get("english"), value.get("japanese")]
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
    for key in ALT_KEYS:
        value = data.get(key)
        if isinstance(value, str):
            value = re.split(r"\s*(?:\n|;| / )\s*", value)
        for item in value if isinstance(value, list) else []:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):             # MangaDex's [{"en": "…"}]
                out += [v.strip() for v in item.values() if isinstance(v, str) and v.strip()]
    return out


def links_in(data: dict) -> list:
    """The links a description gives its title: :data:`LINK_KEYS` of it and of what it holds one level down
    ({"source": {"url": …}}) — never the other strings (a description may name other titles)."""
    out: list = []
    if not isinstance(data, dict):
        return out
    for holder in [data] + [v for v in data.values() if isinstance(v, dict)][:8]:
        for key in LINK_KEYS:
            value = holder.get(key)
            if isinstance(value, str) and value.strip().lower().startswith("http"):
                out.append(value.strip())
    return out


def _description(data: bytes, where: str, found: Clues) -> None:
    try:
        parsed = json.loads(_text(data))
    except ValueError:
        return
    if not isinstance(parsed, dict):
        return
    found.looked.append(where)
    for url in links_in(parsed):
        found.add_link(url, where)
    for name in names_in(parsed):
        found.add_name(name, where)


def comic_info(data: bytes) -> dict:
    """ComicInfo.xml: {"series": [...], "links": [...]} (``<Series>``, ``<LocalizedSeries>``, ``<AlternateSeries>``;
    ``<Web>`` — links by spaces or commas)."""
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, ValueError):
        return {}
    out: dict = {"series": [], "links": []}
    for node in root.iter():
        tag = str(node.tag).rsplit("}", 1)[-1].lower()
        text = (node.text or "").strip()
        if not text:
            continue
        if tag in ("series", "localizedseries", "alternateseries"):
            out["series"].append(text[:200])
        elif tag == "web":
            out["links"] += [u for u in re.split(r"[\s,]+", text) if u.lower().startswith("http")]
    return out


def _comic_info(data: bytes, where: str, found: Clues) -> None:
    info = comic_info(data)
    if not info:
        return
    found.looked.append(where)
    for url in info.get("links") or []:
        found.add_link(url, where)
    for name in info.get("series") or []:
        found.add_name(name, where)


def _shortcut(path: Path, found: Clues) -> None:
    """A shortcut (``.url``, ``.webloc``) — its link; a ``.txt`` — its link only when it names one title (a note may
    name others)."""
    text = _text(_small(path))
    if not text:
        return
    urls = [u.rstrip(".,;") for u in _URL.findall(text)
            if known_link(u.rstrip(".,;")) or e621_pool(u.rstrip(".,;"))]
    if path.suffix.lower() == ".txt" and len({title_of(u) if not e621_pool(u) else ("e621", e621_pool(u))
                                              for u in urls}) != 1:
        return
    where = f"«{path.name}»"
    before = len(found.links) + len(found.pools)
    for url in urls:
        found.add_link(url, where)
    if len(found.links) + len(found.pools) > before:
        found.looked.append(where)


def shared_name(names: list) -> str:
    """The name the chapter files and folders share: «Solo Leveling - Chapter 001», «Solo Leveling - Chapter 002»…
    → «Solo Leveling» (made clean, at least two of them and half of them); "" — none."""
    from core.manga_archive import clean_name
    counted: dict = {}
    shown: dict = {}
    for name in names:
        name = re.sub(r"\.(?:cbz|zip|cbr|rar|pdf|7z)$", "", str(name), flags=re.I)
        cleaned = clean_name(_LEAD_NUMBER.sub("", name))
        if junk_name(cleaned):
            continue
        key = S.name_key(cleaned)
        counted[key] = counted.get(key, 0) + 1
        shown.setdefault(key, cleaned)
    if not counted:
        return ""
    key, count = max(counted.items(), key=lambda kv: kv[1])
    return shown[key] if count >= 2 and count * 2 >= len(names) else ""


def _natural(path: Path) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def clues(folder: Path) -> Clues:
    """What the files of the title's folder say of it (see the module's words); a folder that cannot be read says
    nothing."""
    folder = Path(folder)
    found = Clues()
    try:
        items = sorted(folder.iterdir(), key=_natural)
    except OSError:
        return found
    files, folders = [], []
    for path in items:
        try:
            if path.is_file():
                files.append(path)
            elif path.is_dir() and not path.name.startswith("."):
                folders.append(path)
        except OSError:
            continue
    for path in files:
        low = path.name.lower()
        if low in DESCRIPTIONS:
            _description(_small(path), f"«{path.name}»", found)
        elif low == "comicinfo.xml":
            _comic_info(_small(path), f"«{path.name}»", found)
        elif path.suffix.lower() in (".url", ".webloc", ".txt"):
            _shortcut(path, found)
    for sub in folders[:CHAPTER_FOLDERS]:
        for name in ("ComicInfo.xml", "info.json", "details.json"):
            path = next((p for p in (sub / name, sub / name.lower()) if p.is_file()), None)
            if path is not None:
                where = f"«{path.name}» в «{sub.name}»"
                (_comic_info if name.endswith(".xml") else _description)(_small(path), where, found)
                break
    for archive in [p for p in files if p.suffix.lower() in ARCHIVE_TYPES][:ARCHIVES]:
        try:
            with zipfile.ZipFile(archive) as zf:
                xml = _member(zf, "comicinfo.xml")
                index = _member(zf, "index.json") or _member(zf, "info.json")
        except (OSError, zipfile.BadZipFile, RuntimeError, ValueError, NotImplementedError):
            continue
        if xml:
            _comic_info(xml, f"«ComicInfo.xml» в «{archive.name}»", found)
        if index:
            _description(index, f"«index.json» в «{archive.name}»", found)
    parts = [p.name for p in folders] + \
        [p.name for p in files if p.suffix.lower() in (*ARCHIVE_TYPES, ".cbr", ".rar", ".pdf", ".7z")]
    common = shared_name(parts[:NAMED_FILES])
    if common:
        found.add_name(common, "имён глав в папке")
    return found
