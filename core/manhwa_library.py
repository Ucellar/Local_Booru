"""The manhwa library (v5.44.59): long strips read by scrolling, as on Remanga — a folder of its own («Настройки → Папки
→ Папка манхвы», by default ``manhwa`` in the archive), not the manga's: the manga is turned page by page as before.

The owner (4.11.2026): «манга то листается как обычно а что манхва листается скролом и всеми сопутствующеми
хуйнями от реманги»; the manhwa — «отдельная» папка.

The folder:

* every folder at its top is one title; ``info.json`` (or ``meta.json``) in it is its description — written by
  «Скачать манхву» or by hand: ``title``, ``alt_titles``, ``type``, ``status``, ``year``, ``age``, ``is_erotic``,
  ``genres``, ``tags``, ``description``, ``source_url``; ``cover.*`` is its cover (else the first page of its first
  chapter);
* its chapters are its sub-folders and .zip/.cbz files, named «Том 1 Глава 7 - Имя», «Глава 12», «Chapter 12.5»,
  «12»: the numbers come from the name — or exactly from ``chapter.json`` inside a chapter folder (``tome``,
  ``chapter``, ``name``, ``pages`` [{``file``, ``w``, ``h``}] — the sizes of its pages, so the strip never jumps while
  they load); a sub-folder with no pictures and chapters inside («Том 2») is a tome of them; pictures right in the
  title's folder (not the cover) are one chapter;
* the pages of a chapter are its pictures in the order of their names: the slices of one strip, read glued together.

Ids are made from the path inside the folder, so links and what was read stay the same between runs.  A scan is kept in
memory and redone at most once a minute (or on demand), as the manga library does.  What was read — the last chapter,
the place in each chapter, the chapters read through — is kept in ``manhwa_progress.json`` next to the program
settings, not in the manhwa folder (``Progress``).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

_LOG = logging.getLogger("local_booru.manhwa")

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}
ARCHIVE_EXTS = {".zip", ".cbz"}
INFO_NAMES = ("info.json", "meta.json")
CHAPTER_INFO = "chapter.json"
COVER_NAMES = ("cover", "poster", "folder")
MAX_INFO_BYTES = 4 * 1024 * 1024
MAX_PAGE_BYTES = 96 * 1024 * 1024            # a slice is a few hundred KB; a crafted archive must not take the memory
_SKIP_DIRS = {"$recycle.bin", "system volume information", "@eadir", "_to_delete", ".lb"}


def manhwa_root(settings: dict) -> str:
    """The manhwa folder: «Настройки → Папки → Папка манхвы», by default ``manhwa`` in the archive — next to its
    ``video`` (``Local_Booru_Archive\\manhwa``)."""
    chosen = str((settings or {}).get("manhwa_root") or "").strip()
    if chosen:
        return chosen
    from core.archive_switch import open_archive
    return str(Path(open_archive()) / "manhwa")


def natural_key(text) -> list:
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(text))]


def _id_for(root: Path, path: Path, kind: str = "manhwa") -> str:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()
    return hashlib.md5((kind + "|" + rel.lower()).encode("utf-8")).hexdigest()[:12]


def _read_json(path: Path) -> dict:
    try:
        if path.stat().st_size > MAX_INFO_BYTES:
            _LOG.warning("manhwa description %s is too big, skipped", path)
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        if path.exists():
            _LOG.warning("manhwa description %s unreadable: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


# ── what is in the folder ──────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Page:
    path: str                       # the picture, or the archive it is in
    member: str = ""                # its name inside the archive
    width: int = 0
    height: int = 0

    @property
    def name(self) -> str:
        return self.member or Path(self.path).name

    @property
    def ext(self) -> str:
        return Path(self.name).suffix.lower()

    def read_bytes(self) -> bytes:
        if not self.member:
            return Path(self.path).read_bytes()
        with zipfile.ZipFile(self.path) as zf:
            info = zf.getinfo(self.member)
            if info.file_size > MAX_PAGE_BYTES:
                raise OSError(f"{self.member}: {info.file_size} bytes — too big for a page")
            return zf.read(self.member)


@dataclass
class Chapter:
    id: str
    title_id: str
    path: str
    tome: int = 0                   # 0 — the name says no tome
    number: str = ""                # «7», «7.5»; "" — no number in the name
    name: str = ""                  # its own name («Возвращение»), if any
    pages: list = field(default_factory=list)          # Page, in reading order
    index: int = 0                  # its place among the title's chapters
    mtime: float = 0.0
    info: dict = field(default_factory=dict)            # chapter.json

    @property
    def label(self) -> str:
        """As Remanga names it: «Том 1 Глава 7», «Глава 7», or its folder's name."""
        if self.number:
            return (f"Том {self.tome} " if self.tome else "") + f"Глава {self.number}"
        return self.name or Path(self.path).stem

    @property
    def full_label(self) -> str:
        return self.label + (f" — {self.name}" if self.number and self.name else "")


@dataclass
class Title:
    id: str
    title: str
    path: str
    info: dict = field(default_factory=dict)
    cover: str = ""                 # a picture file, or "" (the first page of the first chapter)
    chapters: list = field(default_factory=list)       # Chapter, in reading order
    mtime: float = 0.0
    folder_title: str = ""

    def chapter(self, chapter_id: str) -> Chapter | None:
        return next((c for c in self.chapters if c.id == chapter_id), None)

    def neighbours(self, chapter: Chapter) -> tuple[Chapter | None, Chapter | None]:
        """The chapters before and after it."""
        i = chapter.index
        before = self.chapters[i - 1] if i > 0 else None
        after = self.chapters[i + 1] if i + 1 < len(self.chapters) else None
        return before, after

    @property
    def alt_titles(self) -> list:
        value = self.info.get("alt_titles") or []
        if isinstance(value, str):
            value = [value]
        return [str(x) for x in value if str(x).strip()] if isinstance(value, list) else []

    def words(self) -> str:
        """Everything a search on the tab looks in."""
        parts = [self.title, self.folder_title, *self.alt_titles]
        for key in ("genres", "tags", "type", "status", "year", "author", "artist"):
            value = self.info.get(key)
            parts += value if isinstance(value, list) else [value or ""]
        return " ".join(str(p) for p in parts).lower().replace("ё", "е")

    def list_of(self, key: str) -> list:
        value = self.info.get(key) or []
        if isinstance(value, str):
            value = [x.strip() for x in re.split(r"[,;\n]+", value)]
        return [str(x) for x in value if str(x).strip()] if isinstance(value, list) else []


_TOME = re.compile(r"(?:том|vol(?:ume)?\.?|\bv)\s*\.?\s*(\d{1,4})", re.I)
_CHAPTER = re.compile(r"(?:глава|гл\.?|chapter|chap\.?|ch\.?|\bc)\s*\.?\s*(\d{1,5}(?:[.,]\d{1,3})?)", re.I)
_BARE = re.compile(r"^\s*(\d{1,5}(?:[.,]\d{1,3})?)(?=$|[\s_\-—–.])")
_NAME_SPLIT = re.compile(r"\s+[-—–]\s+|\s*:\s+")


def parse_name(name: str) -> tuple[int, str, str]:
    """(tome, chapter number, its own name) from a chapter's folder or file name."""
    stem = Path(name).stem if Path(name).suffix.lower() in ARCHIVE_EXTS else name
    tome_m = _TOME.search(stem)
    chap_m = _CHAPTER.search(stem)
    tome = int(tome_m.group(1)) if tome_m else 0
    number = ""
    rest = stem
    if chap_m:
        number = chap_m.group(1).replace(",", ".")
        rest = stem[chap_m.end():]
    elif not tome_m:
        bare = _BARE.match(stem)
        if bare:
            number = bare.group(1).replace(",", ".")
            rest = stem[bare.end():]
    else:
        rest = stem[tome_m.end():]
    parts = _NAME_SPLIT.split(rest, maxsplit=1)
    own = (parts[1] if len(parts) > 1 else rest).strip(" -—–_.:")
    if number:
        number = number.lstrip("0") or "0"
        if "." in number:
            number = number.rstrip("0").rstrip(".") if not number.startswith(".") else "0" + number
    return tome, number, own if number or tome else stem.strip()


_FIRST = re.compile(r"пролог|prologue|предисловие|вступление", re.I)


def _order_key(chapter: Chapter) -> tuple:
    """Reading order: by tome and number; a prologue without a number first, other chapters without one
    («Экстра», «Послесловие») after the numbered ones of their tome — or of all, without a tome."""
    name = natural_key(Path(chapter.path).name)
    if chapter.number:
        return (chapter.tome, _number_key(chapter.number), name)
    if _FIRST.search(chapter.name or Path(chapter.path).name):
        return (-1, 0.0, name)
    return (chapter.tome or float("inf"), float("inf"), name)


def _number_key(number: str) -> float:
    try:
        return float(number)
    except ValueError:
        return float("inf")


def _pictures(folder: Path) -> list[Path]:
    from core.title_covers import is_aside       # (v5.44.123) a cover put aside («cover.old-…») is no page
    try:
        found = [p for p in folder.iterdir() if p.suffix.lower() in IMG_EXTS and p.is_file()
                 and not p.name.startswith(".") and not is_aside(p.name)]
    except OSError:
        return []
    return sorted(found, key=lambda p: natural_key(p.name))


def _chapter_from_folder(root: Path, title_id: str, folder: Path, tome: int = 0) -> Chapter | None:
    pages = [p for p in _pictures(folder)]
    if not pages:
        return None
    info = _read_json(folder / CHAPTER_INFO)
    t, number, own = parse_name(folder.name)
    sizes = {}
    for item in info.get("pages") or []:
        if isinstance(item, dict) and item.get("file"):
            try:
                sizes[str(item["file"])] = (int(item.get("w") or 0), int(item.get("h") or 0))
            except (TypeError, ValueError):
                continue
    chapter = Chapter(id=_id_for(root, folder), title_id=title_id, path=str(folder), tome=t or tome, number=number,
                      name=own, info=info,
                      pages=[Page(str(p), "", *sizes.get(p.name, (0, 0))) for p in pages])
    _apply_chapter_info(chapter)
    try:
        chapter.mtime = folder.stat().st_mtime
    except OSError:
        pass
    return chapter


def _chapter_from_archive(root: Path, title_id: str, archive: Path, tome: int = 0) -> Chapter | None:
    try:
        with zipfile.ZipFile(archive) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/") and Path(n).suffix.lower() in IMG_EXTS
                     and not Path(n).name.startswith(".")]
            info = {}
            for n in zf.namelist():
                if Path(n).name.lower() == CHAPTER_INFO:
                    zi = zf.getinfo(n)
                    if zi.file_size <= MAX_INFO_BYTES:
                        try:
                            data = json.loads(zf.read(n).decode("utf-8-sig"))
                            info = data if isinstance(data, dict) else {}
                        except (ValueError, UnicodeDecodeError):
                            info = {}
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        _LOG.warning("manhwa chapter %s unreadable: %s", archive, exc)
        return None
    if not names:
        return None
    names.sort(key=natural_key)
    t, number, own = parse_name(archive.name)
    sizes = {}
    for item in info.get("pages") or []:
        if isinstance(item, dict) and item.get("file"):
            try:
                sizes[str(item["file"])] = (int(item.get("w") or 0), int(item.get("h") or 0))
            except (TypeError, ValueError):
                continue
    chapter = Chapter(id=_id_for(root, archive), title_id=title_id, path=str(archive), tome=t or tome, number=number,
                      name=own, info=info,
                      pages=[Page(str(archive), n, *sizes.get(Path(n).name, (0, 0))) for n in names])
    _apply_chapter_info(chapter)
    try:
        chapter.mtime = archive.stat().st_mtime
    except OSError:
        pass
    return chapter


def _apply_chapter_info(chapter: Chapter) -> None:
    """chapter.json says the numbers exactly (the name may be shortened)."""
    info = chapter.info
    try:
        if info.get("tome") not in (None, ""):
            chapter.tome = max(0, int(info["tome"]))
    except (TypeError, ValueError):
        pass
    if str(info.get("chapter") or "").strip():
        chapter.number = str(info["chapter"]).strip()
    if isinstance(info.get("name"), str):
        chapter.name = info["name"].strip()


def _find_cover(folder: Path) -> str:
    for name in COVER_NAMES:
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".avif"):
            candidate = folder / (name + ext)
            if candidate.is_file():
                return str(candidate)
    return ""


def read_title(root: Path, folder: Path) -> Title | None:
    title_id = _id_for(root, folder)
    info = {}
    for name in INFO_NAMES:
        info = _read_json(folder / name)
        if info:
            break
    chapters: list[Chapter] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
    except OSError as exc:
        _LOG.warning("manhwa title %s unreadable: %s", folder, exc)
        return None
    cover = _find_cover(folder)
    for entry in entries:
        if entry.name.startswith(".") or entry.name.lower() in _SKIP_DIRS:
            continue
        try:
            if entry.is_dir():
                chapter = _chapter_from_folder(root, title_id, entry)
                if chapter is not None:
                    chapters.append(chapter)
                    continue
                tome, _n, _own = parse_name(entry.name)
                for sub in sorted(entry.iterdir(), key=lambda p: natural_key(p.name)):     # a tome of chapters
                    if sub.is_dir():
                        found = _chapter_from_folder(root, title_id, sub, tome)
                    elif sub.suffix.lower() in ARCHIVE_EXTS:
                        found = _chapter_from_archive(root, title_id, sub, tome)
                    else:
                        found = None
                    if found is not None:
                        chapters.append(found)
            elif entry.suffix.lower() in ARCHIVE_EXTS:
                chapter = _chapter_from_archive(root, title_id, entry)
                if chapter is not None:
                    chapters.append(chapter)
        except OSError as exc:            # one unreadable chapter must not hide the title
            _LOG.warning("manhwa entry %s skipped: %s", entry, exc)
    loose = [p for p in _pictures(folder) if str(p) != cover]
    if loose and not chapters:            # pictures right in the title's folder: one chapter
        one = _chapter_from_folder(root, title_id, folder)
        if one is not None:
            one.pages = [p for p in one.pages if p.path != cover]
            one.id = _id_for(root, folder, "manhwa-loose")     # not the title's own id
            chapters.append(one)
    if not chapters:
        return None
    chapters.sort(key=_order_key)
    for i, chapter in enumerate(chapters):
        chapter.index = i
    raw = info.get("title")
    title = Title(id=title_id, title=str(raw).strip() if isinstance(raw, str) and raw.strip() else folder.name,
                  path=str(folder), info=info, cover=cover, chapters=chapters, folder_title=folder.name)
    try:
        title.mtime = max([folder.stat().st_mtime] + [c.mtime for c in chapters])
    except OSError:
        title.mtime = max((c.mtime for c in chapters), default=0.0)
    return title


def scan(root) -> list[Title]:
    root = Path(str(root or ""))
    if not str(root) or not root.is_dir():
        return []
    out = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: natural_key(p.name))
    except OSError as exc:
        _LOG.warning("manhwa folder %s unreadable: %s", root, exc)
        return []
    for entry in entries:
        if entry.name.startswith(".") or entry.name.lower() in _SKIP_DIRS:
            continue
        try:
            if entry.is_dir():
                title = read_title(root, entry)
                if title is not None:
                    out.append(title)
        except OSError as exc:            # one unreadable title must not hide the folder
            _LOG.warning("manhwa entry %s skipped: %s", entry, exc)
    return out


# ── the sizes of the pages (the strip is laid out before its pictures come) ───────────────────────────────────
_SIZES: dict = {}
_SIZES_LOCK = threading.Lock()


def page_size(page: Page) -> tuple[int, int]:
    """(width, height) of a page: from chapter.json, else read from the picture's header once (kept)."""
    if page.width > 0 and page.height > 0:
        return page.width, page.height
    try:
        stamp = Path(page.path).stat().st_mtime_ns
    except OSError:
        return 0, 0
    key = (page.path, page.member, stamp)
    with _SIZES_LOCK:
        known = _SIZES.get(key)
    if known is None:
        known = (0, 0)
        try:
            from PIL import Image
            if page.member:
                with zipfile.ZipFile(page.path) as zf, zf.open(page.member) as fh:
                    with Image.open(fh) as img:
                        known = img.size
            else:
                with Image.open(page.path) as img:
                    known = img.size
        except Exception as exc:  # a broken picture: the strip shows it without a size
            _LOG.debug("manhwa page size %s %s: %s", page.path, page.member, exc)
        with _SIZES_LOCK:
            if len(_SIZES) > 20000:
                _SIZES.clear()
            _SIZES[key] = known
    page.width, page.height = known
    return known


# ── covers ───────────────────────────────────────────────────────────────────────────────────────────────────
def cover_thumb(title: Title, size: int) -> Path | None:
    """A JPEG of the title's cover no taller than ``size`` px, made once and kept in the thumbnail cache: its
    ``cover.*``, else the top of its first page cut to 2:3 (a page of a strip is much taller than wide)."""
    from core.paths import CACHE_DIR
    if title.cover:
        source = Page(title.cover)
    elif title.chapters and title.chapters[0].pages:
        source = title.chapters[0].pages[0]
    else:
        return None
    try:
        stamp = Path(source.path).stat().st_mtime_ns
    except OSError:
        return None
    key = hashlib.md5(f"manhwa1|{source.path}|{source.member}|{stamp}|{size}".encode("utf-8")).hexdigest()
    out = Path(CACHE_DIR) / "thumbs" / "manhwa" / f"{key}.jpg"
    if out.is_file() and out.stat().st_size > 0:
        return out
    import io

    from PIL import Image, ImageOps

    from core.image_safe import swap_in
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")
    try:
        with Image.open(io.BytesIO(source.read_bytes())) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode != "RGB":
                im = im.convert("RGB")
            if not title.cover:                       # the top of a strip: as tall as 1.5 of its width at most
                w, h = im.size
                im = im.crop((0, 0, w, min(h, int(w * 1.5))))
            im.thumbnail((size, size), Image.Resampling.LANCZOS)
            im.save(tmp, "JPEG", quality=88, optimize=True)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:      # a broken picture: no cover
        _LOG.warning("manhwa cover of %s not made: %s", title.path, exc)
        tmp.unlink(missing_ok=True)
        return None
    return out if swap_in(tmp, out) else None


THUMB_WIDTH = 168                              # (v5.44.117) a page small in the reader's «Лента страниц» (84 px shown)


def page_thumb(page: Page, width: int = THUMB_WIDTH) -> Path | None:
    """(v5.44.117) A JPEG of a page ``width`` px wide — the top of it, as tall as 1.5 of its width at most (a slice of a
    strip is much taller than wide) — made once and kept in the thumbnail cache; None — the picture does not open."""
    from core.paths import CACHE_DIR
    try:
        stamp = Path(page.path).stat().st_mtime_ns
    except OSError:
        return None
    key = hashlib.md5(f"manhwa-page1|{page.path}|{page.member}|{stamp}|{width}".encode("utf-8")).hexdigest()
    out = Path(CACHE_DIR) / "thumbs" / "manhwa" / f"p{key}.jpg"
    if out.is_file() and out.stat().st_size > 0:
        return out
    import io

    from PIL import Image, ImageOps

    from core.image_safe import swap_in
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")
    try:
        with Image.open(io.BytesIO(page.read_bytes())) as im:
            im.draft("RGB", (width * 2, width * 6))
            im = ImageOps.exif_transpose(im)
            if im.mode != "RGB":
                im = im.convert("RGB")
            w, h = im.size
            im = im.crop((0, 0, w, min(h, int(w * 1.5))))
            im.thumbnail((width, int(width * 1.5)), Image.Resampling.LANCZOS)
            im.save(tmp, "JPEG", quality=85, optimize=True)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:     # a broken picture: no small copy
        _LOG.info("manhwa page %s %s: no small copy (%s)", page.path, page.member, exc)
        tmp.unlink(missing_ok=True)
        return None
    return out if swap_in(tmp, out) else None


# ── the scan kept in memory ──────────────────────────────────────────────────────────────────────────────────
class Library:
    """The scanned manhwa folder (the manga library's rules: the first request scans and waits; later, when the scan
    is older than ``ttl``, pages get the kept list at once while a thread scans again)."""

    def __init__(self, ttl: float = 60.0):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._root = ""
        self._at = 0.0
        self._rescanning = False
        self._items: list[Title] = []
        self._titles: dict[str, Title] = {}
        self._chapters: dict[str, tuple[Title, Chapter]] = {}

    def _store(self, key: str, items: list) -> None:
        titles = {t.id: t for t in items}
        chapters = {c.id: (t, c) for t in items for c in t.chapters}
        with self._lock:
            self._root, self._at, self._items, self._titles, self._chapters = key, time.time(), items, titles, chapters

    def _rescan(self, key: str) -> None:
        try:
            with self._scan_lock:
                self._store(key, scan(key))
        finally:
            with self._lock:
                self._rescanning = False

    def items(self, root, *, refresh: bool = False) -> list[Title]:
        key = str(root or "")
        with self._lock:
            known = key == self._root and self._at > 0
            if known and not refresh:
                if time.time() - self._at >= self.ttl and not self._rescanning:
                    self._rescanning = True
                    threading.Thread(target=self._rescan, args=(key,), name="manhwa-rescan", daemon=True).start()
                return self._items
        with self._scan_lock:
            with self._lock:
                if key == self._root and self._at > 0 and not refresh:
                    return self._items
            self._store(key, scan(key))
        with self._lock:
            return self._items

    def invalidate(self) -> None:
        with self._lock:
            self._at = 0.0

    def get(self, root, title_id: str) -> Title | None:
        self.items(root)
        with self._lock:
            return self._titles.get(str(title_id))

    def chapter(self, root, chapter_id: str) -> tuple[Title | None, Chapter | None]:
        self.items(root)
        with self._lock:
            return self._chapters.get(str(chapter_id), (None, None))


LIBRARY = Library()


# ── what was read ────────────────────────────────────────────────────────────────────────────────────────────
class Progress:
    """``manhwa_progress.json``: {title id: {"last": chapter id, "at": unix time, "read": [chapter ids read through],
    "pos": {chapter id: [page index, part of that page above the top of the window 0…1]}}}.  The place is kept as a
    page and a part of it, not in pixels: the strip may be shown wider or narrower next time.  (v5.44.67) ``name`` —
    the same record of another tab: the manga's ``manga_progress.json`` (a title — a manga or a series, a chapter — the
    manga or its chapter, the page — the one open)."""

    def __init__(self, path: Path | None = None, name: str = "manhwa_progress.json"):
        self._path = path
        self._name = name
        self._lock = threading.Lock()
        self._data: dict | None = None

    def _file(self) -> Path:
        if self._path is not None:
            return self._path
        from core.paths import SETTINGS_DIR
        return Path(SETTINGS_DIR) / self._name

    def _load(self) -> dict:
        if self._data is None:
            path = self._file()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                data = {}
            except (OSError, ValueError) as exc:
                _LOG.warning("manhwa progress unreadable (%s): kept aside, started anew", exc)
                try:
                    path.replace(path.with_name(f"{path.name}.broken-{int(time.time())}"))
                except OSError:
                    pass
                data = {}
            self._data = data if isinstance(data, dict) else {}
        return self._data

    def _save(self) -> None:
        path = self._file()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

    def title(self, title_id: str) -> dict:
        """A copy of what was read of a title: {"last", "at", "read", "pos"}."""
        with self._lock:
            part = self._load().get(str(title_id)) or {}
            return {"last": str(part.get("last") or ""), "at": int(part.get("at") or 0),
                    "read": list(part.get("read") or []), "pos": dict(part.get("pos") or {})}

    def place(self, title_id: str, chapter_id: str) -> tuple[int, float]:
        pos = self.title(title_id)["pos"].get(str(chapter_id))
        try:
            return max(0, int(pos[0])), min(1.0, max(0.0, float(pos[1])))
        except (TypeError, ValueError, IndexError):
            return 0, 0.0

    def record(self, title_id: str, chapter_id: str, page: int, part: float, *, done: bool = False) -> None:
        with self._lock:
            data = self._load()
            entry = data.setdefault(str(title_id), {})
            entry["last"] = str(chapter_id)
            entry["at"] = int(time.time())
            entry.setdefault("pos", {})[str(chapter_id)] = [max(0, int(page)), round(min(1.0, max(0.0, float(part))), 4)]
            if done:
                read = entry.setdefault("read", [])
                if str(chapter_id) not in read:
                    read.append(str(chapter_id))
            self._save()

    def set_read(self, title_id: str, chapter_id: str, read: bool) -> None:
        with self._lock:
            data = self._load()
            entry = data.setdefault(str(title_id), {})
            marks = [x for x in entry.get("read") or [] if x != str(chapter_id)]
            if read:
                marks.append(str(chapter_id))
            entry["read"] = marks
            self._save()

    # (v5.44.83) the way the title is read, as the owner chose it in the reader: «strip», «rtl», «ltr» ("" — «авто»)
    def mode_of(self, title_id: str) -> str:
        with self._lock:
            part = self._load().get(str(title_id)) or {}
            return str(part.get("mode") or "") if isinstance(part, dict) else ""

    def set_mode(self, title_id: str, mode: str) -> None:
        with self._lock:
            data = self._load()
            entry = data.setdefault(str(title_id), {})
            if mode:
                entry["mode"] = str(mode)
            else:
                entry.pop("mode", None)
            self._save()


PROGRESS = Progress()
