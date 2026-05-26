from pathlib import Path
import json, re, os, html, zipfile
from collections import Counter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QGridLayout, QFrame, QSplitter, QListWidget,
    QListWidgetItem, QComboBox, QToolButton, QMenu, QFileDialog,
    QInputDialog, QMessageBox, QStackedWidget
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QImage, QColor

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
PDF_EXTS = {".pdf"}
ARCHIVE_EXTS = {".zip", ".cbz"}
DOC_EXTS = IMG_EXTS | PDF_EXTS
MANGA_META_NAMES = {"meta.json", "info.json", "metadata.json", "tags.json", "gallery.tags.json"}

GROUP_ORDER = ["artist", "character", "parody", "copyright", "general", "language", "category", "group", "pages"]
GROUP_TITLES_RU = {
    "artist": "Авторы",
    "character": "Персонажи",
    "parody": "Произведения",
    "copyright": "Копирайт",
    "general": "Общие",
    "language": "Языки",
    "category": "Категории",
    "group": "Группы",
    "pages": "Страницы",
}
GROUP_COLORS = {
    "artist": "#ff3838",
    "character": "#55dd55",
    "parody": "#ff54a7",
    "copyright": "#ff54a7",
    "general": "#6699ff",
    "language": "#ffbb55",
    "category": "#55dddd",
    "group": "#ffaa77",
    "pages": "#aaaaaa",
}
GROUP_ALIASES = {
    "tag": "general",
    "tags": "general",
    "general": "general",
    "meta": "category",
    "metadata": "category",
    "circle": "group",
}



def normalize_group_name(group):
    group = str(group or "general").lower().strip()
    group = GROUP_ALIASES.get(group, group)
    return group if group in GROUP_ORDER else "general"


def _is_virtual_archive_path(path):
    return isinstance(path, str) and path.startswith("zip://") and "::" in path

def _split_virtual_archive_path(path):
    raw = str(path)[6:]
    archive, member = raw.split("::", 1)
    return Path(archive), member

def _archive_page_path(archive: Path, member: str):
    return f"zip://{archive}::{member}"

def _read_archive_bytes(archive: Path, member: str):
    with zipfile.ZipFile(archive, "r") as zf:
        return zf.read(member)

def _safe_json_loads_bytes(data):
    for enc in ("utf-8", "utf-8-sig", "cp932", "shift_jis", "latin-1"):
        try:
            return json.loads(data.decode(enc, errors="ignore"))
        except Exception:
            pass
    return None

def clean_manga_display_title(name: str):
    title = Path(str(name)).stem
    source = {}
    m = re.match(r"^nhentai[-_ ]*(\d+)\s*[-–—]?\s*(.*)$", title, flags=re.I)
    if m:
        source = {"source": "nhentai", "id": int(m.group(1))}
        title = m.group(2).strip() or title
    title = re.sub(r"\s+", " ", title).strip(" -_	")
    return title or Path(str(name)).stem, source

def _add_tag(info, group, tag):
    tag = norm_tag(tag)
    if not tag:
        return
    group = normalize_group_name(group)
    info.setdefault("tags", []).append(tag)
    info.setdefault("groups", {g: [] for g in GROUP_ORDER})
    info["groups"].setdefault(group, []).append(tag)

def apply_manga_metadata(info, data):
    if not isinstance(data, dict):
        return info
    # nhentai-style title object
    title = data.get("title")
    if isinstance(title, dict):
        info["title"] = title.get("english") or title.get("pretty") or title.get("japanese") or info.get("title")
        info["title_japanese"] = title.get("japanese") or ""
    elif isinstance(title, str) and title.strip():
        info["title"] = title.strip()
    if data.get("id") is not None:
        try:
            info["source"] = "nhentai"
            info["source_id"] = int(data.get("id"))
            info["source_url"] = f"https://nhentai.net/g/{info['source_id']}/"
        except Exception:
            pass
    if data.get("num_pages") is not None:
        try: _add_tag(info, "pages", f"pages:{int(data.get('num_pages'))}")
        except Exception: pass
    tags = data.get("tags")
    if isinstance(tags, list):
        for item in tags:
            if isinstance(item, dict):
                group = normalize_group_name(item.get("type") or "general")
                name = item.get("name") or item.get("tag")
                _add_tag(info, group, name)
            else:
                _add_tag(info, "general", item)
    # generic flat/grouped json
    for key in ("tags", "tag", "general"):
        val = data.get(key)
        if isinstance(val, str):
            for x in re.split(r"[,\n\s]+", val): _add_tag(info, "general", x)
        elif isinstance(val, list) and key != "tags":
            for x in val: _add_tag(info, "general", x)
    for g in GROUP_ORDER:
        val = data.get(g) or data.get(GROUP_TITLES_RU.get(g, ""))
        if isinstance(val, str):
            for x in re.split(r"[,\n]+", val): _add_tag(info, g, x)
        elif isinstance(val, list):
            for x in val: _add_tag(info, g, x)
    return info

def _dedupe_info(info):
    seen=set(); clean=[]
    for t in info.get("tags", []) + sum((info.get("groups", {}).get(g, []) for g in GROUP_ORDER), []):
        nt=norm_tag(t)
        if nt and nt not in seen:
            seen.add(nt); clean.append(nt)
    info["tags"] = clean
    for g in GROUP_ORDER:
        seen=set(); arr=[]
        for t in info.get("groups", {}).get(g, []):
            nt=norm_tag(t)
            if nt and nt not in seen:
                seen.add(nt); arr.append(nt)
        info.setdefault("groups", {})[g]=arr
    return info


def pixmap_from_file(path, size=None):
    if _is_virtual_archive_path(path):
        try:
            archive, member = _split_virtual_archive_path(path)
            ext = Path(member).suffix.lower()
            data = _read_archive_bytes(archive, member)
            if ext in IMG_EXTS:
                pix = QPixmap()
                pix.loadFromData(data)
                return pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation) if (size and not pix.isNull()) else pix
            if ext in PDF_EXTS:
                try:
                    import fitz
                    doc = fitz.open(stream=data, filetype="pdf")
                    if len(doc) <= 0:
                        return QPixmap()
                    page = doc.load_page(0)
                    pm = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                    qimg = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format_RGB888)
                    pix = QPixmap.fromImage(qimg.copy())
                    doc.close()
                    return pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation) if size else pix
                except Exception:
                    return QPixmap()
        except Exception:
            return QPixmap()
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext in IMG_EXTS:
            pix = QPixmap(str(p))
            if not pix.isNull():
                return pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation) if size else pix
            try:
                from PIL import Image
                im = Image.open(p).convert("RGBA")
                data = im.tobytes("raw", "RGBA")
                qimg = QImage(data, im.width, im.height, QImage.Format_RGBA8888)
                pix = QPixmap.fromImage(qimg.copy())
                return pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation) if size else pix
            except Exception:
                return QPixmap()
        if ext in PDF_EXTS:
            try:
                import fitz
                doc = fitz.open(str(p))
                if len(doc) <= 0:
                    return QPixmap()
                page = doc.load_page(0)
                pm = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                qimg = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format_RGB888)
                pix = QPixmap.fromImage(qimg.copy())
                doc.close()
                return pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation) if size else pix
            except Exception:
                return QPixmap()
    except Exception:
        return QPixmap()
    return QPixmap()


def norm_tag(t):
    return html.unescape(str(t)).strip().replace(" ", "_").lower()


def natural_key(s):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(s))]


def read_info(folder: Path):
    display_title, source_meta = clean_manga_display_title(folder.name)
    info = {"title": display_title, "path": str(folder), "tags": [], "groups": {g: [] for g in GROUP_ORDER}, "pages": [], "cover": "", "mtime": 0, "chapter_key": ""}
    info.update(source_meta)
    try:
        info["mtime"] = folder.stat().st_mtime
    except Exception:
        pass
    pages = []
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in DOC_EXTS:
            pages.append(p)
    pages.sort(key=lambda p: natural_key(p.name))
    info["pages"] = [str(p) for p in pages]
    if pages:
        info["cover"] = str(pages[0])

    for name in MANGA_META_NAMES:
        p = folder / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        apply_manga_metadata(info, data)
        if isinstance(data.get("title"), str):
            info["title"] = data.get("title") or data.get("name") or info["title"]
        elif data.get("name"):
            info["title"] = data.get("name") or info["title"]
        for key in ("tags", "tag", "general"):
            val = data.get(key)
            if isinstance(val, str):
                info["tags"] += [x for x in re.split(r"[,\s]+", val) if x]
            elif isinstance(val, list):
                # nhentai meta.json stores tags as dicts; those are handled by apply_manga_metadata().
                info["tags"] += [str(x) for x in val if not isinstance(x, dict) and str(x).strip()]
        for g in GROUP_ORDER:
            val = data.get(g) or data.get(GROUP_TITLES_RU.get(g, ""))
            if isinstance(val, str):
                info["groups"][g] += [x for x in re.split(r"[,\s]+", val) if x]
            elif isinstance(val, list):
                info["groups"][g] += [str(x) for x in val if str(x).strip()]

    for name in ("tags.txt", ".tags.txt"):
        p = folder / name
        if p.exists():
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
                info["tags"] += [x.strip() for x in re.split(r"[,\n]+", txt) if x.strip()]
            except Exception:
                pass

    title = info["title"]
    if "[" in title and "]" in title:
        for part in re.findall(r"\[([^\]]+)\]", title):
            for token in re.split(r"[,;/]+", part):
                token = token.strip()
                if token:
                    info["tags"].append(token)

    seen = set(); clean = []
    for t in info["tags"] + sum((info["groups"].get(g, []) for g in GROUP_ORDER), []):
        nt = norm_tag(t)
        if nt and nt not in seen:
            seen.add(nt); clean.append(nt)
    info["tags"] = clean

    stem = re.sub(r"([_\-\s](chapter|ch|c)?\s*\d+)$", "", folder.name, flags=re.I).strip("_- ")
    stem = re.sub(r"[_\- ]\d+$", "", stem).strip("_- ")
    info["chapter_key"] = stem.lower() or folder.name.lower()
    return _dedupe_info(info)


def read_info_archive(archive: Path):
    display, src = clean_manga_display_title(archive.name)
    info = {"title": display, "path": str(archive), "tags": [], "groups": {g: [] for g in GROUP_ORDER}, "pages": [], "cover": "", "mtime": 0, "chapter_key": display.lower(), "chapters": [], "is_series": False}
    info.update(src)
    try:
        info["mtime"] = archive.stat().st_mtime
    except Exception:
        pass
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            for n in names:
                base = Path(n).name.lower()
                if base in MANGA_META_NAMES:
                    data = _safe_json_loads_bytes(zf.read(n))
                    if isinstance(data, dict):
                        apply_manga_metadata(info, data)
            pages = [n for n in names if Path(n).suffix.lower() in DOC_EXTS and not Path(n).name.startswith(".")]
            pages.sort(key=natural_key)
            info["pages"] = [_archive_page_path(archive, n) for n in pages]
            if pages:
                info["cover"] = info["pages"][0]
    except Exception:
        pass
    # sidecar metadata next to archive may override/extend archive meta
    for p in (archive.with_suffix(archive.suffix + ".metadata.json"), archive.with_suffix(".metadata.json"), archive.parent / (archive.stem + ".metadata.json")):
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
                apply_manga_metadata(info, data)
            except Exception:
                pass
    return _dedupe_info(info)


def _folder_has_pages(folder: Path):
    try:
        return any(p.is_file() and p.suffix.lower() in DOC_EXTS for p in folder.rglob("*"))
    except Exception:
        return False


def _folder_has_direct_pages(folder: Path):
    """Есть ли страницы прямо в этой папке, без рекурсивного обхода.
    Это важно для отличия библиотеки манги от папки конкретной манги с главами.
    """
    try:
        return any(p.is_file() and p.suffix.lower() in DOC_EXTS for p in folder.iterdir())
    except Exception:
        return False


def _merge_manga_infos(parent: Path, chapters):
    base = {"title": parent.name, "path": str(parent), "tags": [], "groups": {g: [] for g in GROUP_ORDER}, "pages": [], "cover": "", "mtime": 0, "chapter_key": parent.name.lower(), "chapters": chapters, "is_series": True}
    # metadata on parent wins; chapter tags are aggregated for search/sidebar.
    parent_info = read_info(parent) if any(p.is_file() and p.name in tuple(MANGA_META_NAMES) + ("tags.txt",) for p in parent.iterdir()) else None
    if parent_info:
        base["title"] = parent_info.get("title") or base["title"]
        base["tags"] += parent_info.get("tags", [])
        for g in GROUP_ORDER:
            base["groups"][g] += parent_info.get("groups", {}).get(g, [])
    for ch in chapters:
        if not base["cover"] and ch.get("cover"):
            base["cover"] = ch.get("cover")
        base["pages"] += ch.get("pages", [])
        base["tags"] += ch.get("tags", [])
        base["mtime"] = max(base.get("mtime", 0), ch.get("mtime", 0))
        for g in GROUP_ORDER:
            base["groups"][g] += ch.get("groups", {}).get(g, [])
    seen=set(); clean=[]
    for t in base["tags"] + sum((base["groups"].get(g, []) for g in GROUP_ORDER), []):
        nt=norm_tag(t)
        if nt and nt not in seen:
            seen.add(nt); clean.append(nt)
    base["tags"] = clean
    for g in GROUP_ORDER:
        seen=set(); arr=[]
        for t in base["groups"].get(g, []):
            nt=norm_tag(t)
            if nt and nt not in seen:
                seen.add(nt); arr.append(nt)
        base["groups"][g]=arr
    return base


def _looks_like_chapter_dir(folder: Path):
    """Папка похожа на главу: 1, 01, ch1, chapter_2 и т.п."""
    name = folder.name.strip().lower()
    return bool(re.fullmatch(r"(chapter|chap|ch|c)?[ _\-\.]*\d+([ _\-\.].*)?", name) or re.fullmatch(r"\d+", name))


def _make_chapter_info(series_folder: Path, chapter_folder: Path, idx: int):
    ch = read_info(chapter_folder)
    raw = chapter_folder.name.strip()
    # Если папка называется просто 1/2/3 — показываем НазваниеМанги-1.
    # Если имя полноценное — оставляем его как название главы.
    if re.fullmatch(r"\d+", raw):
        ch["title"] = f"{series_folder.name}-{raw}"
    elif _looks_like_chapter_dir(chapter_folder):
        ch["title"] = f"{series_folder.name}-{idx}"
    else:
        ch["title"] = raw
    ch["series_title"] = series_folder.name
    ch["chapter_number"] = idx
    ch["is_series"] = False
    ch["chapters"] = []
    return ch


def _scan_series_folder(series_folder: Path, force: bool = False):
    """Возвращает item серии, если внутри series_folder реально лежат главы.

    ВАЖНО:
    - библиотека вида root/Manga A/Manga A chapters НЕ должна считаться одной серией root;
    - папка конкретной манги вида Manga A/1, Manga A/2 должна считаться одной серией;
    - глава = непосредственная подпапка, где страницы лежат прямо внутри неё.
    """
    try:
        subdirs = [x for x in series_folder.iterdir() if x.is_dir()]
    except Exception:
        return None
    if not subdirs:
        return None

    direct_chapter_dirs = []
    for sub in sorted(subdirs, key=lambda p: natural_key(p.name)):
        if _folder_has_direct_pages(sub):
            direct_chapter_dirs.append(sub)

    if not direct_chapter_dirs:
        return None

    # Авто-серия: подпапки похожи на главы: 1, 2, ch1, chapter_02...
    chapter_like_count = sum(1 for d in direct_chapter_dirs if _looks_like_chapter_dir(d))
    looks_like_series = chapter_like_count > 0

    # Если force=True, это уже папка конкретной манги из библиотеки, значит её подпапки считаем главами.
    if not force and not looks_like_series:
        return None

    chapters = []
    for d in direct_chapter_dirs:
        chapters.append(_make_chapter_info(series_folder, d, len(chapters) + 1))
    if not chapters:
        return None
    return _merge_manga_infos(series_folder, chapters)


def _is_library_root(root: Path):
    """Папка похожа на библиотеку: внутри лежат папки манги, а не страницы."""
    if _folder_has_direct_pages(root):
        return False
    try:
        subdirs = [x for x in root.iterdir() if x.is_dir()]
    except Exception:
        return False
    if not subdirs:
        return False
    # Если непосредственные подпапки выглядят как главы, это НЕ библиотека, а папка конкретной манги.
    chapter_like = [d for d in subdirs if _looks_like_chapter_dir(d)]
    if chapter_like and len(chapter_like) >= max(1, len(subdirs) // 2):
        return False
    return True


def _archive_files_in(folder: Path):
    try:
        return [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ARCHIVE_EXTS]
    except Exception:
        return []

def _archive_chapters_in(folder: Path):
    arcs = _archive_files_in(folder)
    if not arcs:
        return []
    chapters=[]
    for idx, arc in enumerate(sorted(arcs, key=lambda p: natural_key(p.name)), 1):
        ch = read_info_archive(arc)
        ch["series_title"] = folder.name
        ch["chapter_number"] = idx
        raw_title, _ = clean_manga_display_title(arc.name)
        if re.fullmatch(r"\d+", Path(arc).stem.strip()):
            ch["title"] = f"{folder.name}-{Path(arc).stem.strip()}"
        else:
            ch["title"] = raw_title
        chapters.append(ch)
    return chapters

def scan_manga(root):
    root = Path(root or "")
    if not root.exists():
        return []

    out = []

    # выбранная папка ВСЕГДА библиотека манги
    # каждая папка первого уровня = отдельная манга; каждый zip/cbz первого уровня = отдельная манга.
    try:
        top_entries = sorted(list(root.iterdir()), key=lambda p: natural_key(p.name))
    except Exception:
        top_entries = []

    for entry in top_entries:
        if entry.is_file() and entry.suffix.lower() in ARCHIVE_EXTS:
            info = read_info_archive(entry)
            if info.get("pages"):
                out.append(info)
            continue
        if not entry.is_dir():
            continue

        manga_folder = entry

        # Архивы внутри папки манги считаем главами-архивами.
        archive_chapters = _archive_chapters_in(manga_folder)
        if archive_chapters:
            out.append(_merge_manga_infos(manga_folder, archive_chapters))
            continue

        # Если внутри есть подпапки со страницами -> это главы.
        series = _scan_series_folder(manga_folder, force=True)
        if series:
            out.append(series)
            continue

        # Если внутри только файлы -> обычная манга.
        if _folder_has_direct_pages(manga_folder):
            info = read_info(manga_folder)
            if info.get("pages"):
                info["chapters"] = []
                info["is_series"] = False
                out.append(info)

    return out
