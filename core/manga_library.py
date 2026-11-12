"""The manga library: what lies in the manga folder, read without Qt (for the web pages).

The rules are those of the old window's manga page (``ui/manga/helpers.py``, gone in v5.40), so a
folder shows the same titles as it did there:

* every entry at the top of the manga folder is one title;
* a ``.zip``/``.cbz`` file is one title, its pages are the pictures inside;
* a folder with archives inside is a series, each archive a chapter;
* a folder whose sub-folders hold pages is a series, each such sub-folder a chapter;
* a folder with pages directly inside is one title (pages are collected from sub-folders too).

Titles, tags and groups come from nhentai-style ``meta.json`` / ``info.json`` / ``metadata.json``
(also inside an archive or as ``<archive>.metadata.json`` next to it), ``tags.txt`` and the
``[brackets]`` of the folder name.

Each title and chapter gets an id made from its path relative to the manga folder, so links stay
the same between runs.  A scan is kept in memory and redone at most once a minute (or on demand).
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from core.manhwa_library import Progress

_LOG = logging.getLogger("local_booru.manga")

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}
PDF_EXTS = {".pdf"}
ARCHIVE_EXTS = {".zip", ".cbz"}
PAGE_EXTS = IMG_EXTS | PDF_EXTS
META_NAMES = {"meta.json", "info.json", "metadata.json", "tags.json", "gallery.tags.json"}

GROUP_ORDER = ("parody", "copyright", "character", "general", "artist", "group", "language", "category")
GROUP_TITLES = {
    "parody": "Пародии", "copyright": "Копирайт", "character": "Персонажи", "general": "Теги",
    "artist": "Артисты", "group": "Группы", "language": "Языки", "category": "Категории",
}
_GROUP_ALIASES = {"tag": "general", "tags": "general", "meta": "category", "metadata": "category", "circle": "group",
                  "genres": "general", "genre": "general",       # (v5.44.76) the description a site gives (info.json)
                  "artists": "artist", "characters": "character", "parodies": "parody", "groups": "group",
                  "languages": "language", "categories": "category"}
_RU_GROUP_KEYS = {"Авторы": "artist", "Персонажи": "character", "Произведения": "parody", "Копирайт": "copyright",
                  "Общие": "general", "Языки": "language", "Категории": "category", "Группы": "group"}


def norm_tag(value) -> str:
    return html.unescape(str(value or "")).strip().replace(" ", "_").lower()


def natural_key(text) -> list:
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(text))]


def _group(name) -> str:
    key = str(name or "general").strip().lower()
    key = _GROUP_ALIASES.get(key, key)
    return key if key in GROUP_TITLES else "general"


# A page inside an archive is read into memory whole: a crafted archive declaring a page of
# gigabytes must not take the program's memory.  Real pages are a few megabytes, PDFs maybe 100.
MAX_PAGE_BYTES = 256 * 1024 * 1024
MAX_META_BYTES = 4 * 1024 * 1024


def _read_member(zf: zipfile.ZipFile, name: str, limit: int) -> bytes:
    size = zf.getinfo(name).file_size          # zipfile never unpacks more than this declared size
    if size > limit:
        raise OSError(f"{name}: {size // (1024 * 1024)} MB in the archive, more than {limit // (1024 * 1024)} MB allowed")
    return zf.read(name)


@dataclass(frozen=True)
class PageRef:
    """One page: a picture file, a member of a zip archive, or one page of a PDF (``pdf_page`` >= 0)."""
    path: str
    member: str = ""
    pdf_page: int = -1

    @property
    def name(self) -> str:
        return Path(self.member or self.path).name

    @property
    def ext(self) -> str:
        return Path(self.member or self.path).suffix.lower()

    def read_bytes(self) -> bytes:
        if self.member:
            with zipfile.ZipFile(self.path, "r") as zf:
                return _read_member(zf, self.member, MAX_PAGE_BYTES)
        return Path(self.path).read_bytes()

    def stamp(self) -> str:
        """Changes when the page's file changes (for caches)."""
        try:
            st = Path(self.path).stat()
            return f"{st.st_mtime_ns}_{st.st_size}"
        except OSError:
            return "0"


@dataclass
class Manga:
    id: str
    title: str
    path: str
    pages: list = field(default_factory=list)          # PageRef
    groups: dict = field(default_factory=dict)         # group -> [tag]
    mtime: float = 0.0
    title_japanese: str = ""
    title_pretty: str = ""                             # nhentai's title without brackets (core/manga_works.py)
    source_url: str = ""
    chapters: list = field(default_factory=list)       # Manga (a series' chapters)
    parent_id: str = ""
    number: int = 0                                    # chapter number in its series
    key: str = ""                                      # same for loose chapters of one title ("Name ch1", "Name ch2")
    adult: str = ""                                    # (v5.44.53) its site marks it as hentai: why (core/content_marks)
    cover_page: "PageRef | None" = None                # (v5.44.76) «cover.*» of a title the program looks after
    linked: bool = False                               # (v5.44.76) the folder names its site (``.lb/source.json``)
    # (v5.44.125) a manga of a folder of several the parser's folder held («silver soul»: ``core/parser_manga``) — its
    # сборка (``info.json`` «lb_set»: its key, its name, its place)
    set_key: str = ""
    set_name: str = ""
    set_place: int = 0
    info: dict = field(default_factory=dict)           # (v5.44.126) ``chapter.json`` of a chapter «Скачать» brought:
    #                                                    ``downloaded_at``, ``source`` (its site) — as a manhwa one's
    # (v5.44.135) what a search finds it by besides its titles and tags (the description of its site, ``info.json``):
    # its other names, its authors, its type, status and year
    others: list = field(default_factory=list)
    authors: list = field(default_factory=list)
    facts: list = field(default_factory=list)

    @property
    def is_series(self) -> bool:
        return bool(self.chapters)

    @property
    def tags(self) -> list:
        return [t for g in GROUP_ORDER for t in self.groups.get(g, [])]

    @property
    def page_count(self) -> int:
        return len(self.pages) if not self.chapters else sum(len(c.pages) for c in self.chapters)

    @property
    def cover(self) -> PageRef | None:
        if self.cover_page is not None:
            return self.cover_page
        if self.pages:
            return self.pages[0]
        for chapter in self.chapters:
            if chapter.pages:
                return chapter.pages[0]
        return None


def _fitz():
    """PyMuPDF (optional; PDF pages need it): ``pymupdf`` in new versions, ``fitz`` in old ones."""
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        try:
            import fitz
            return fitz
        except ImportError:
            return None


def _expand_pdfs(refs: list) -> list:
    """A PDF is as many pages as it has (the old page showed only its first one)."""
    fitz = _fitz()
    out = []
    for ref in refs:
        if ref.ext not in PDF_EXTS:
            out.append(ref)
            continue
        count = 1
        if fitz is not None:
            try:
                doc = fitz.open(stream=ref.read_bytes(), filetype="pdf") if ref.member else fitz.open(ref.path)
                count = max(1, doc.page_count)
                doc.close()
            except Exception as exc:  # PyMuPDF raises its own error types for broken files
                _LOG.warning("PDF %s unreadable: %s", ref.path, exc)
        out.extend(PageRef(ref.path, ref.member, n) for n in range(count))
    return out


def render_page(ref: PageRef) -> tuple[bytes, str] | None:
    """The page as image bytes and their type; a PDF page is drawn into PNG (None without PyMuPDF)."""
    if ref.pdf_page < 0:
        import mimetypes
        return ref.read_bytes(), mimetypes.guess_type("x" + ref.ext)[0] or "application/octet-stream"
    fitz = _fitz()
    if fitz is None:
        return None
    doc = fitz.open(stream=ref.read_bytes(), filetype="pdf") if ref.member else fitz.open(ref.path)
    try:
        page = doc.load_page(min(ref.pdf_page, doc.page_count - 1))
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
        return pix.tobytes("png"), "image/png"
    finally:
        doc.close()


def thumbnail(ref: PageRef, size: int) -> Path | None:
    """A JPEG of the page no larger than ``size`` px, made once and kept in the thumbnail cache."""
    from core.paths import CACHE_DIR
    key = hashlib.md5(f"manga1|{ref.path}|{ref.member}|{ref.pdf_page}|{ref.stamp()}|{size}".encode("utf-8")).hexdigest()
    out = Path(CACHE_DIR) / "thumbs" / "manga" / f"{key}.jpg"
    if out.is_file() and out.stat().st_size > 0:
        return out
    import io
    import os

    from PIL import Image, ImageOps

    from core.image_safe import swap_in
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")
    try:
        rendered = render_page(ref)
        if rendered is None:
            return None
        with Image.open(io.BytesIO(rendered[0])) as im:
            im.draft("RGB", (size * 2, size * 2))
            im = ImageOps.exif_transpose(im)
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((size, size), Image.Resampling.LANCZOS)
            im.save(tmp, "JPEG", quality=90, optimize=True)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:   # broken or oversized page, unknown image
        _LOG.warning("manga page %s: no thumbnail (%s)", ref.name, exc)
        tmp.unlink(missing_ok=True)
        return None
    return out if swap_in(tmp, out) else None


def chapter_key(name: str) -> str:
    """The title without a chapter number at the end: «Saga ch2.zip» and «Saga_03» both give «saga»
    (as the old reader's «Другие главы этого тайтла»).  A bare «nhentai-123456» keeps its number:
    downloads named only by their id are different works, not chapters of one."""
    stem = Path(name).stem if Path(name).suffix.lower() in ARCHIVE_EXTS | PDF_EXTS else name
    if re.fullmatch(r"\s*nhentai[-_ ]*\d+\s*", stem, flags=re.I):
        return stem.strip().lower()
    title, _source = _title_from_name(stem)
    key = re.sub(r"([_\-\s](chapter|chap|ch|c|vol|v|глава|гл|том)?\.?\s*\d+)$", "", title, flags=re.I).strip("_- ")
    key = re.sub(r"[_\- ]\d+$", "", key).strip("_- ")
    return (key or title or name).lower()


def _id_for(root: Path, path: Path) -> str:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()
    return hashlib.md5(rel.lower().encode("utf-8")).hexdigest()[:12]


def _json_from_bytes(data: bytes):
    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis", "latin-1"):
        try:
            return json.loads(data.decode(encoding))
        except (UnicodeDecodeError, ValueError):
            continue
    return None


def _add(groups: dict, group, tag) -> None:
    name = norm_tag(tag)
    if not name:
        return
    bucket = groups.setdefault(_group(group), [])
    if name not in bucket:
        bucket.append(name)


def _apply_meta(manga: Manga, data) -> None:
    """nhentai JSON (title object, id, tags [{type, name}]) and simpler {title, tags, artist: [...]}."""
    if not isinstance(data, dict):
        return
    title = data.get("title")
    if isinstance(title, dict):
        manga.title = title.get("english") or title.get("pretty") or title.get("japanese") or manga.title
        manga.title_japanese = title.get("japanese") or manga.title_japanese
        manga.title_pretty = title.get("pretty") or manga.title_pretty
    elif isinstance(title, str) and title.strip():
        manga.title = title.strip()
    elif isinstance(data.get("name"), str) and data["name"].strip():
        manga.title = data["name"].strip()
    if data.get("id") is not None and str(data.get("id")).isdigit():
        manga.source_url = f"https://nhentai.net/g/{int(data['id'])}/"
    if isinstance(data.get("url"), str) and data["url"].startswith("http"):
        manga.source_url = data["url"]
    adult = _adult_flag(data)
    if adult:
        manga.adult = adult
    together = data.get("lb_set")                         # (v5.44.125) one folder's manga: one сборка (manga_works)
    if isinstance(together, dict) and str(together.get("key") or "").strip():
        manga.set_key = str(together["key"]).strip()[:64]
        manga.set_name = str(together.get("name") or "").strip()[:200]
        try:
            manga.set_place = int(together.get("place") or 0)
        except (TypeError, ValueError):
            manga.set_place = 0
    tags = data.get("tags")
    if isinstance(tags, list):
        for item in tags:
            if isinstance(item, dict):
                _add(manga.groups, item.get("type") or "general", item.get("name") or item.get("tag"))
            else:
                _add(manga.groups, "general", item)
    for key in ("tags", "tag", "general"):
        value = data.get(key)
        if isinstance(value, str):
            for part in re.split(r"[,\n]+", value):
                _add(manga.groups, "general", part)
        elif isinstance(value, list) and key != "tags":
            for part in value:
                _add(manga.groups, "general", part)
    if isinstance(data.get("source_url"), str) and data["source_url"].startswith("http") and not manga.source_url:
        manga.source_url = data["source_url"]               # (v5.44.76) the description of a site's title
    # (v5.44.135) the other names, the authors and the facts a site says («Скачать» writes them; nhentai's own meta has
    # its artists as tags) — for the search of the folder (``webui/pages/manga._matches``) and the page
    for key in ("alt_titles", "other_names", "otherNames", "alt_names", "synonyms"):
        _words_into(manga.others, data.get(key))
    for key in ("authors", "author", "mangaka"):
        _words_into(manga.authors, data.get(key))
    for key in ("type", "status", "year"):
        _words_into(manga.facts, data.get(key))
    for key, value in data.items():
        group = _RU_GROUP_KEYS.get(str(key)) or (_group(key) if _group(key) != "general" or key in (
            "general", "genres", "genre") else "")
        if not group or key in {"tags", "tag", "general"}:
            continue
        if isinstance(value, str):
            for part in re.split(r"[,\n]+", value):
                _add(manga.groups, group, part)
        elif isinstance(value, list):
            for part in value:
                if not isinstance(part, dict):
                    _add(manga.groups, group, part)


def _words_into(bucket: list, value) -> None:
    """(v5.44.135) A description's names (a string — split by «,», «;», lines; a list of strings or of {"name": …}) into
    ``bucket``, each once."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(int(value)) if float(value).is_integer() else str(value)
    items = re.split(r"[,;\n]+", value) if isinstance(value, str) else value if isinstance(value, list) else []
    for item in items[:40]:
        if isinstance(item, dict):
            item = item.get("name") or item.get("title") or ""
        text = html.unescape(str(item or "")).strip()[:200]
        if text and text not in bucket:
            bucket.append(text)


def _adult_flag(data: dict) -> str:
    """(v5.44.53) The site's own word that a title is hentai, kept in its description: Remanga's ``is_erotic``,
    MangaDex's ``content_rating`` «pornographic», ``hentai: true`` (written by hand or by a downloader) — why, or ""."""
    if data.get("hentai") is True:
        return "помечена как хентай в описании"
    if data.get("is_erotic") is True:
        return "сайт пометил как эротику"
    rating = str(data.get("content_rating") or data.get("contentRating") or "").strip().lower()
    if rating == "pornographic":
        return "сайт пометил как порнографию"
    return ""


def _title_from_name(name: str) -> tuple[str, str]:
    stem = Path(name).stem if Path(name).suffix.lower() in ARCHIVE_EXTS else name
    source = ""
    m = re.match(r"^nhentai[-_ ]*(\d+)\s*[-–—]?\s*(.*)$", stem, flags=re.I)
    if m:
        source = f"https://nhentai.net/g/{int(m.group(1))}/"
        stem = m.group(2).strip() or stem
    return re.sub(r"\s+", " ", stem).strip(" -_\t") or name, source


def _bracket_tags(manga: Manga) -> None:
    for part in re.findall(r"\[([^\]]+)\]", manga.title):
        for token in re.split(r"[,;/]+", part):
            _add(manga.groups, "general", token)


LB_DIR = ".lb"                 # (v5.44.76) what the program keeps in a title's folder (core/manhwa_download.py)
COVER_STEM = "cover"


def _linked(folder: Path) -> bool:
    """(v5.44.76) The folder names its site (``.lb/source.json``): the program looks after it — its «cover.*» is the
    cover, not a page."""
    return (folder / LB_DIR / "source.json").is_file()


def _listing(folder: Path) -> tuple[list, list] | None:
    """(the folders, the files — ``os.DirEntry``) right in ``folder``, read once; None — it cannot be read.
    (v5.44.151) An entry of a folder's listing says what it is itself: the folder was read name by name and the disk
    asked of every picture whether it is a file — 39 thousand questions for 400 titles at every reading (once a
    minute while the pages are open, and before the first of them after a start)."""
    dirs, files = [], []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    (dirs if entry.is_dir() else files if entry.is_file() else []).append(entry)
                except OSError:
                    continue
    except OSError:
        return None
    return dirs, files


def _files_in(folder: Path, recursive: bool, listed: tuple | None = None) -> tuple[list, dict]:
    """(the files in ``folder`` — and, ``recursive``, in the folders inside it, the program's own ``.lb`` left out —,
    {the name without the case of a file right in it: its path}); ``listed`` — the folder's own listing when it is
    read already."""
    out, own = [], {}
    first = listed if listed is not None else _listing(folder)
    todo = [(folder, first)]
    while todo:
        place, got = todo.pop()
        if got is None:
            got = _listing(place)
            if got is None:
                continue
        dirs, files = got
        for entry in files:
            path = Path(entry.path)
            out.append(path)
            if place is folder:
                own[entry.name.lower()] = path
        if recursive:
            for entry in dirs:
                try:
                    if entry.name != LB_DIR and not entry.is_symlink():
                        todo.append((Path(entry.path), None))
                except OSError:
                    continue
    return out, own


def _read_folder(root: Path, folder: Path, *, recursive: bool = True, listed: tuple | None = None) -> Manga:
    title, source = _title_from_name(folder.name)
    manga = Manga(id=_id_for(root, folder), title=title, path=str(folder), source_url=source)
    try:
        manga.mtime = folder.stat().st_mtime
    except OSError:
        pass
    manga.linked = _linked(folder)
    files, own = _files_in(folder, recursive, listed)
    pages = []
    from core.title_covers import is_aside
    for p in files:
        try:
            if p.parent == folder and is_aside(p.name):
                continue                                  # (v5.44.123) a cover put aside («cover.old-…»): no page
            if p.suffix.lower() in PAGE_EXTS:
                if manga.linked and p.parent == folder and p.stem.lower() == COVER_STEM:
                    manga.cover_page = PageRef(str(p))
                    continue
                pages.append(p)
        except (OSError, ValueError):
            continue
    pages.sort(key=lambda p: natural_key(p.relative_to(folder).as_posix()))
    manga.pages = _expand_pdfs([PageRef(str(p)) for p in pages])
    for name in sorted(META_NAMES):
        meta = own.get(name)
        if meta is not None:
            try:
                _apply_meta(manga, _json_from_bytes(meta.read_bytes()))
            except OSError as exc:
                _LOG.warning("manga metadata %s unreadable: %s", meta, exc)
    for name in ("tags.txt", ".tags.txt"):
        txt = own.get(name)
        if txt is not None:
            try:
                for part in re.split(r"[,\n]+", txt.read_text(encoding="utf-8", errors="ignore")):
                    _add(manga.groups, "general", part)
            except OSError as exc:
                _LOG.warning("manga tags %s unreadable: %s", txt, exc)
    manga.info = _chapter_info(folder) if CHAPTER_INFO in own else {}
    _bracket_tags(manga)
    return manga


CHAPTER_INFO = "chapter.json"


def _chapter_info(folder: Path) -> dict:
    """(v5.44.126) A chapter's ``chapter.json`` — «Скачать» writes it into every chapter it brings (when it came, the
    site it came from: «новая», «с MangaLib · сегодня» on the title's pages, as a manhwa chapter's); {} — none."""
    path = folder / CHAPTER_INFO
    try:
        if not path.is_file() or path.stat().st_size > MAX_META_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:             # the journal names the folders, never the whole path
        why = (exc.strerror or type(exc).__name__) if isinstance(exc, OSError) else exc
        _LOG.warning("manga chapter description of «%s / %s» unreadable: %s", folder.parent.name, folder.name, why)
        return {}
    return data if isinstance(data, dict) else {}


def _read_archive(root: Path, archive: Path) -> Manga:
    title, source = _title_from_name(archive.name)
    manga = Manga(id=_id_for(root, archive), title=title, path=str(archive), source_url=source)
    try:
        manga.mtime = archive.stat().st_mtime
    except OSError:
        pass
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            for n in names:
                if Path(n).name.lower() in META_NAMES:
                    _apply_meta(manga, _json_from_bytes(_read_member(zf, n, MAX_META_BYTES)))
            pages = [n for n in names if Path(n).suffix.lower() in PAGE_EXTS and not Path(n).name.startswith(".")]
            pages.sort(key=natural_key)
            manga.pages = [PageRef(str(archive), n) for n in pages]
        manga.pages = _expand_pdfs(manga.pages)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:   # RuntimeError: encrypted member
        _LOG.warning("manga archive %s unreadable: %s", archive, exc)
    for side in (archive.with_name(archive.name + ".metadata.json"), archive.with_suffix(".metadata.json")):
        if side.is_file():
            try:
                _apply_meta(manga, _json_from_bytes(side.read_bytes()))
            except OSError as exc:
                _LOG.warning("manga metadata %s unreadable: %s", side, exc)
    _bracket_tags(manga)
    return manga


def _has_direct_pages(folder: Path, listed: tuple | None = None) -> bool:
    """A picture or a PDF right in the folder (``listed`` — its listing when it is read already)."""
    if listed is not None:
        return any(os.path.splitext(entry.name)[1].lower() in PAGE_EXTS for entry in listed[1])
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if os.path.splitext(entry.name)[1].lower() in PAGE_EXTS and entry.is_file():
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def _looks_like_chapter(name: str) -> bool:
    name = name.strip().lower()
    return bool(re.fullmatch(r"(chapter|chap|ch|c|глава|гл)?[ _\-.]*\d+([ _\-.].*)?", name))


def _series(root: Path, folder: Path, chapters: list[Manga], listed: tuple | None = None) -> Manga:
    series = _read_folder(root, folder, recursive=False, listed=listed)
    if series.cover_page is None:                      # (v5.44.76) «cover.*» of a series is its cover, not a page
        cover = next((p for p in series.pages if Path(p.path).parent == folder and
                      Path(p.path).stem.lower() == COVER_STEM), None)
        series.cover_page = cover
    series.pages = []
    series.chapters = chapters
    for n, chapter in enumerate(chapters, 1):
        chapter.parent_id = series.id
        chapter.number = n
        series.mtime = max(series.mtime, chapter.mtime)
        for group, tags in chapter.groups.items():
            for tag in tags:
                _add(series.groups, group, tag)
        for mine, theirs in ((series.others, chapter.others), (series.authors, chapter.authors),
                             (series.facts, chapter.facts)):
            _words_into(mine, theirs)           # (v5.44.135) a series is found by what its chapters' descriptions say
    return series


def _chapter_title(series_name: str, entry: Path, number: int, title: str) -> str:
    stem = entry.stem if entry.is_file() else entry.name
    if re.fullmatch(r"\d+", stem.strip()):
        return f"{series_name} — {stem.strip()}"
    if _looks_like_chapter(stem):
        return f"{series_name} — {number}"
    return title


def _mixed_series(root: Path, folder: Path, archives: list, subdirs: list, listed: tuple | None = None) -> Manga:
    """(v5.44.76) A series of archives, folders of pages and (as one chapter, first) the pictures right in its folder
    (``listed`` — the folder's listing when it is read already)."""
    from core.manhwa_library import parse_name

    def order(entry: Path) -> tuple:
        tome, number, _own = parse_name(entry.name)
        try:
            value = float(number) if number else float("inf")
        except ValueError:
            value = float("inf")
        return (tome or 0, value, natural_key(entry.name))
    chapters = []
    entries = sorted([*archives, *subdirs], key=order)
    if _has_direct_pages(folder, listed):
        loose = _read_folder(root, folder, recursive=False, listed=listed)
        loose.id = _id_for(root, folder / "__pages__")
        if loose.pages:
            loose.title = f"{_title_from_name(folder.name)[0]} — 1"
            chapters.append(loose)
    files = {str(path) for path in archives}
    for n, entry in enumerate(entries, len(chapters) + 1):
        chapter = _read_archive(root, entry) if str(entry) in files else _read_folder(root, entry)
        if chapter.pages:
            chapter.title = _chapter_title(folder.name, entry, n, chapter.title)
            chapters.append(chapter)
    return _series(root, folder, chapters, listed)


def scan(root) -> list[Manga]:
    root = Path(str(root or ""))
    if not str(root) or not root.is_dir():
        return []
    out = []
    top = _listing(root)
    if top is None:
        _LOG.warning("manga folder %s unreadable", root)
        return []
    entries = sorted([(entry, True) for entry in top[0]] + [(entry, False) for entry in top[1]],
                     key=lambda item: natural_key(item[0].name))
    for found, is_dir in entries:
        entry = Path(found.path)
        try:
            if not is_dir:
                if entry.suffix.lower() in ARCHIVE_EXTS:
                    manga = _read_archive(root, entry)
                    if manga.pages:
                        out.append(manga)
                elif entry.suffix.lower() in PDF_EXTS:
                    title, source = _title_from_name(entry.stem)
                    manga = Manga(id=_id_for(root, entry), title=title, path=str(entry), source_url=source,
                                  pages=_expand_pdfs([PageRef(str(entry))]), mtime=entry.stat().st_mtime)
                    _bracket_tags(manga)
                    out.append(manga)
                continue
            listed = _listing(entry)
            if listed is None:
                raise OSError(f"{entry.name} cannot be read")
            archives = sorted((Path(e.path) for e in listed[1] if os.path.splitext(e.name)[1].lower() in ARCHIVE_EXTS),
                              key=lambda p: natural_key(p.name))
            subdirs = sorted((Path(e.path) for e in listed[0] if e.name != LB_DIR and _has_direct_pages(Path(e.path))),
                             key=lambda p: natural_key(p.name))
            direct = _has_direct_pages(entry, listed)
            if archives and subdirs or (archives or subdirs) and direct and _linked(entry):
                # (v5.44.76) a title of the archive the program added chapters to: its archives, folders of pages and
                # pictures right in it — all its chapters, in the order of their tomes and numbers
                out.append(_mixed_series(root, entry, archives, subdirs, listed))
                continue
            if archives:
                chapters = []
                for n, arc in enumerate(archives, 1):
                    chapter = _read_archive(root, arc)
                    if chapter.pages:
                        chapter.title = _chapter_title(entry.name, arc, n, chapter.title)
                        chapters.append(chapter)
                if chapters:
                    out.append(_series(root, entry, chapters, listed))
                continue
            if subdirs:
                chapters = []
                for n, sub in enumerate(subdirs, 1):
                    chapter = _read_folder(root, sub)
                    if chapter.pages:
                        chapter.title = _chapter_title(entry.name, sub, n, chapter.title)
                        chapters.append(chapter)
                if chapters:
                    out.append(_series(root, entry, chapters, listed))
                continue
            if direct:
                manga = _read_folder(root, entry, listed=listed)
                if manga.pages:
                    out.append(manga)
        except OSError as exc:            # one unreadable entry must not hide the whole library
            _LOG.warning("manga entry %s skipped: %s", entry, exc)
    return out


def _set_keys(items: list) -> None:
    for manga in items:
        manga.key = chapter_key(Path(manga.path).name)


def related(items: list, manga: "Manga") -> list:
    """Other top-level titles that look like chapters of the same work (the same ``key``)."""
    if not manga.key or manga.parent_id:
        return []
    others = [m for m in items if m.key == manga.key and m.id != manga.id]
    return sorted(others, key=lambda m: natural_key(Path(m.path).name))


class Library:
    """The scanned manga folder, kept in memory.

    The first request scans and waits; later, when the scan is older than ``ttl``, pages get the kept
    list at once while a background thread scans again (a big folder takes seconds).  ``refresh=True``
    (the «Обновить» link) scans right away.
    """

    def __init__(self, ttl: float = 60.0):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._root = ""
        self._at = 0.0
        self._rescanning = False
        self._items: list[Manga] = []
        self._by_id: dict[str, Manga] = {}

    def _store(self, key: str, items: list) -> None:
        _set_keys(items)
        by_id = {}
        for manga in items:
            by_id[manga.id] = manga
            for chapter in manga.chapters:
                by_id[chapter.id] = chapter
        with self._lock:
            self._root, self._at, self._items, self._by_id = key, time.time(), items, by_id

    def _rescan(self, key: str) -> None:
        try:
            with self._scan_lock:
                self._store(key, scan(key))
        finally:
            with self._lock:
                self._rescanning = False

    def items(self, root, *, refresh: bool = False) -> list[Manga]:
        key = str(root or "")
        with self._lock:
            known = key == self._root and self._at > 0
            if known and not refresh:
                if time.time() - self._at >= self.ttl and not self._rescanning:
                    self._rescanning = True
                    threading.Thread(target=self._rescan, args=(key,), name="manga-rescan", daemon=True).start()
                return self._items
        with self._scan_lock:                 # one scan at a time; the others wait for its result
            with self._lock:
                if key == self._root and self._at > 0 and not refresh:
                    return self._items
            self._store(key, scan(key))
        with self._lock:
            return self._items

    def invalidate(self) -> None:
        """The next request scans the folder again and waits for it (the folder was changed)."""
        with self._lock:
            self._at = 0.0

    def get(self, root, manga_id: str) -> Manga | None:
        self.items(root)
        with self._lock:
            return self._by_id.get(str(manga_id))


LIBRARY = Library()


def manga_root(settings: dict) -> str:
    """The manga folder from the settings (the old page fell back to the parser's picture folder)."""
    return str((settings or {}).get("manga_root") or "").strip()


# (v5.44.67) what was read: {the title (a manga, a series): its last manga or chapter, the page open in each, the ones
# read to their last page} — the phone's «Продолжить чтение» and the readers of both (webui/pages/manga.py, phone_manga.py)
PROGRESS = Progress(name="manga_progress.json")
