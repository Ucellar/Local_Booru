"""(v5.44.123) The cover of a title as the owner chooses it — step 7 of the plan, his approved mockup F (7.11.2026:
«сделать возможнотсь выбора обложки манги и аниме (они разные у сайтов) нажал на обложку вылезла менюшка с выбором
обложки»; the mockup — «норм мне нравится»).

The menu of covers (``webui/pages/covers.py``, ``static/covers.js``) offers the covers of the sites the title was
found on (a title of manga or manhwa: its own site and the ones «Другие сайты» and the description found it on —
``manhwa_download.Found.covers``; a series: its site's poster, AniLiberty's), the picture in its folder and «Своя
картинка…»; a click — the new cover at once.  Where it goes:

* a title with its folder in the folder of manga or manhwa (the program looks after it: ``.lb/source.json``) — into
  the folder as ``cover.<ext>``; a series of the video folder — ``poster.<ext>`` (a film alone — ``<its name>-poster
  .<ext>``).  The one shown before stays beside: ``cover.old-<date>.<ext>`` (:func:`put`) — the owner's files are never
  deleted; a picture chosen again that is one of those beside is renamed back (no copies of it pile up).  The
  libraries take no ``*.old-*`` for a cover or a page (:func:`is_aside`).  The booru parser's own folder is never
  written into: its titles keep the choice here, as a title without a folder;
* a title without a folder — the choice is kept here, in ``title_covers.json`` of the settings folder, under every
  name the title is known by («remanga:<dir>», «mangalib:<slug>», «yummy:<alias>»…; a picture of the person's own —
  ``title_covers/<md5>.<ext>`` beside it): the title's page and «Лента» show it, its first download writes it into the
  new folder (``manhwa_download.look``; a series — once its download has ended, ``webui/pages/covers.py``).

What the program put into a folder is noted here too (its ``md5``): the menu knows which site's cover the folder's is.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

_LOG = logging.getLogger("local_booru.covers")

FILE_NAME = "title_covers.json"
OWN_DIR = "title_covers"              # the person's own pictures of titles without a folder (in the settings folder)
EXTS = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".bmp")      # what a cover / a poster may be
KEPT_EXTS = (".jpg", ".png", ".webp")  # a picture is kept as it came in these; another one — made a JPEG
MAX_PICTURE = 10 * 1024 * 1024        # bytes of a picture of one's own (the server takes no larger request)
MIN_SIDE = 40                         # px: a smaller picture is no cover
KEPT_TITLES = 3000                    # choices kept at most (the oldest go)
_ASIDE = re.compile(r"\.old-\d{8}-\d{6}(?:-\d+)?\.[A-Za-z0-9]{2,5}$")
_OWN_NAME = re.compile(r"[0-9a-f]{32}\.(?:jpg|png|webp)")
_LOCK = threading.RLock()             # the file of choices, the folders' pictures — one change at a time
_KEPT: dict = {"stamp": None, "titles": []}


class CoverError(Exception):
    """Words for the page: why the cover was not changed."""


# ── pictures ────────────────────────────────────────────────────────────────────────────────────────────────────────
def is_aside(name: str) -> bool:
    """A picture put aside when another cover was chosen (``cover.old-20261107-101500.jpg``, ``poster.old-….png``):
    never a cover, never a page."""
    return bool(_ASIDE.search(str(name or "")))


def picture_ext(data: bytes) -> str:
    """The extension of a picture by its first bytes ("" — not a picture the program keeps)."""
    from core.manhwa_download import _picture_type
    return _picture_type(bytes(data[:16]))


def size_of(data: bytes) -> tuple:
    """(width, height) of a picture, (0, 0) — Pillow cannot read it."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:  # noqa: BLE001 - Pillow raises many kinds of errors for bytes that are not a picture
        return (0, 0)


def prepared(data: bytes) -> tuple:
    """(bytes, extension) of a picture to keep as a cover: JPG, PNG, WebP as they are, another kind (GIF, BMP, AVIF…)
    made a JPEG; :class:`CoverError` — not a picture, or too small to be a cover."""
    data = bytes(data or b"")
    if not data:
        raise CoverError("Файл пустой")
    if len(data) > MAX_PICTURE:
        raise CoverError(f"Картинка больше {MAX_PICTURE // (1024 * 1024)} МБ — возьмите поменьше")
    width, height = size_of(data)
    if not width:
        raise CoverError("Это не картинка, которую программа открывает — нужна JPG, PNG или WebP")
    if width < MIN_SIDE or height < MIN_SIDE:
        raise CoverError(f"Картинка слишком маленькая для обложки ({width} × {height})")
    ext = picture_ext(data)
    if ext in KEPT_EXTS:
        return data, ext
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            out = io.BytesIO()
            im.convert("RGB").save(out, "JPEG", quality=92)
    except Exception as exc:  # noqa: BLE001 - a picture Pillow opens but cannot draw
        raise CoverError("Эту картинку программа не открывает — нужна JPG, PNG или WebP") from exc
    return out.getvalue(), ".jpg"


def content_type(ext: str) -> str:
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
            ".gif": "image/gif", ".avif": "image/avif", ".bmp": "image/bmp"}.get(str(ext).lower(), "image/jpeg")


def md5_of(data: bytes) -> str:
    return hashlib.md5(bytes(data)).hexdigest()


def _md5_file(path: Path) -> str:
    try:
        return md5_of(path.read_bytes())
    except OSError:
        return ""


# ── the folders ─────────────────────────────────────────────────────────────────────────────────────────────────────
def current(folder: Path, stem: str = "cover") -> "Path | None":
    """The picture that is the cover (``stem``) of a folder now — the first by the libraries' order of extensions."""
    folder = Path(folder)
    for ext in EXTS:
        path = folder / f"{stem}{ext}"
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


def in_library(folder: Path, settings: dict) -> str:
    """"manga" / "manhwa" — the folder is a title of the program's folder of manga or manhwa (the menu of covers writes
    into it); "" — it is not (the booru parser's own folder: never written into)."""
    from core.manhwa_download import roots
    try:
        parent = os.path.normcase(os.path.abspath(str(Path(folder).parent)))
        for kind, root in roots(settings).items():
            if root and os.path.normcase(os.path.abspath(str(root))) == parent:
                return kind
    except (OSError, ValueError):
        return ""
    return ""


def stamp(folder: Path, stem: str = "cover") -> str:
    """Changes when the folder's cover changes (for the addresses of its picture): its time and size."""
    path = current(folder, stem)
    try:
        st = path.stat() if path is not None else None
    except OSError:
        st = None
    return f"{st.st_mtime_ns // 1_000_000}-{st.st_size}" if st else ""


def _aside_path(path: Path) -> Path:
    """``cover.jpg`` → ``cover.old-20261107-101500.jpg`` (a name not taken yet)."""
    when = time.strftime("%Y%m%d-%H%M%S")
    base = path.with_name(f"{path.stem}.old-{when}{path.suffix}")
    n = 2
    while base.exists():
        base = path.with_name(f"{path.stem}.old-{when}-{n}{path.suffix}")
        n += 1
    return base


def put(folder: Path, stem: str, data: bytes, ext: str) -> dict:
    """The picture ``data`` as the folder's ``<stem><ext>``: the cover (every ``<stem>.*`` of it) shown before is renamed
    aside (``<stem>.old-<date>.<ext>`` — never deleted); a picture that is one of those beside already is renamed back
    instead of written again.  -> {"path", "aside": [names], "back": the name it had beside, "same": nothing to do}.
    :class:`CoverError` — the folder would not take it (what was renamed is put back)."""
    folder = Path(folder)
    digest = md5_of(data)
    with _LOCK:
        shown = [folder / f"{stem}{e}" for e in EXTS if (folder / f"{stem}{e}").is_file()]
        if shown and _md5_file(shown[0]) == digest and len(shown) == 1:
            return {"path": str(shown[0]), "aside": [], "back": "", "same": True}
        moved: list = []
        try:
            for path in shown:
                kept = _aside_path(path)
                os.replace(path, kept)
                moved.append((path, kept))
            match = None
            try:
                beside = sorted(p for p in folder.iterdir() if p.name.lower().startswith(stem.lower() + ".old-")
                                and is_aside(p.name) and p.is_file())
            except OSError:
                beside = []
            for old in reversed(beside):                 # the newest first
                try:
                    if old.stat().st_size == len(data) and _md5_file(old) == digest:
                        match = old
                        break
                except OSError:
                    continue
            if match is not None:
                target = folder / f"{stem}{match.suffix.lower()}"
                os.replace(match, target)
                back = match.name
            else:
                target = folder / f"{stem}{ext}"
                tmp = folder / f".{stem}{ext}.{os.getpid()}.part"
                tmp.write_bytes(data)
                os.replace(tmp, target)
                back = ""
        except OSError as exc:
            for path, kept in reversed(moved):           # as it was: the cover shown before back in its place
                try:
                    if not path.exists():
                        os.replace(kept, path)
                except OSError:                          # names only: no paths in the log
                    _LOG.warning("cover %s of %s not put back from %s", path.name, folder.name, kept.name)
            raise CoverError(f"Папка не приняла обложку: {exc.strerror or exc}") from exc
    _LOG.info("cover of %s: %s%s", folder.name, target.name, f" (back from {back})" if back else "")
    return {"path": str(target), "aside": [k.name for _p, k in moved if k.exists()], "back": back, "same": False}


# ── what the program keeps: the choices of titles without a folder, what it put into folders ──────────────────────
def _file() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / FILE_NAME


def _own_dir() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / OWN_DIR


def _titles() -> list:
    """The choices (read again when the file changed)."""
    path = _file()
    try:
        st = path.stat()
        mark = (st.st_mtime_ns, st.st_size)
    except OSError:
        mark = None
    with _LOCK:
        if _KEPT["stamp"] == mark and mark is not None:
            return _KEPT["titles"]
        titles: list = []
        if mark is not None:
            from core.file_safety import read_json_kept
            data = read_json_kept(path, dict, what="title covers")
            titles = [t for t in (data or {}).get("titles") or [] if isinstance(t, dict) and t.get("ids")]
        _KEPT.update(stamp=mark, titles=titles)
        return titles


def _write(titles: list) -> None:
    from core.file_safety import write_json_kept
    titles = sorted(titles, key=lambda t: -float(t.get("at") or 0))[:KEPT_TITLES]
    write_json_kept(_file(), json.dumps({"titles": titles}, ensure_ascii=False, indent=1), dict)
    _KEPT["stamp"] = None


def version() -> str:
    """Changes whenever a choice is kept or taken back (for an address of a picture that shows the chosen one: the
    window does not keep the old one); "" — nothing was ever chosen."""
    try:
        return str(_file().stat().st_mtime_ns // 1_000_000)
    except OSError:
        return ""


def _ids(ids) -> list:
    return [str(x) for x in dict.fromkeys(ids or []) if str(x or "").strip()]


def chosen(ids) -> "dict | None":
    """The choice kept for a title known by any of ``ids`` (the newest of them), None — none."""
    wanted = set(_ids(ids))
    if not wanted:
        return None
    best = None
    for entry in _titles():
        if wanted & set(entry.get("ids") or []) and (best is None or float(entry.get("at") or 0) > float(
                best.get("at") or 0)):
            best = entry
    return dict(best) if best is not None else None


def remember(ids, **entry) -> dict:
    """Keep the choice of a title known by ``ids`` (its other names of before join them): ``how`` — "site" (``site``,
    ``label``, ``url``, ``page``) or "own" (``file``), ``md5`` of the picture, ``folder`` — where it was put ("" —
    nowhere yet)."""
    ids = _ids(ids)
    with _LOCK:
        titles = list(_titles())
        joined = [t for t in titles if set(ids) & set(t.get("ids") or [])]
        merged = _ids(ids + [x for t in joined for x in t.get("ids") or []])
        record = {k: v for k, v in entry.items() if v not in (None, "")}
        record.update(ids=merged, at=time.time())
        titles = [t for t in titles if t not in joined] + [record]
        _write(titles)
    return record


def forget(ids) -> bool:
    """The choice of a title taken back («Вернуть обложку сайта»): its site's cover again."""
    ids = set(_ids(ids))
    with _LOCK:
        titles = list(_titles())
        left = [t for t in titles if not ids & set(t.get("ids") or [])]
        if len(left) == len(titles):
            return False
        _write(left)
    return True


def own_store(data: bytes, ext: str) -> str:
    """A picture of the person's own for a title without a folder, kept in the settings folder: its file's name."""
    name = f"{md5_of(data)}{ext if ext in KEPT_EXTS else '.jpg'}"
    folder = _own_dir()
    path = folder / name
    if not path.is_file():
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f".{name}.{os.getpid()}.part"
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return name


def own_bytes(name: str) -> "bytes | None":
    name = str(name or "")
    if not _OWN_NAME.fullmatch(name):
        return None
    try:
        return (_own_dir() / name).read_bytes()
    except OSError:
        return None


_FETCHED: dict = {}                   # a cover's address → (when, bytes, type): the ones asked through a site's own way
FETCHED_KEPT = 40
FETCHED_TTL = 600.0


def fetch(url: str, page: str = "", site: str = "", key: str = "") -> "tuple | None":
    """(bytes, type) of a site's cover at ``url`` — whole (its size is the site's): from the sites' own hosts
    (``manhwa_search.fetch_cover``, kept in memory a while); a host it does not know (a site of mirrors, com-x.life) —
    as the title's look asks it, on that site's hosts with the site as the Referer (``site`` — a site of manga, ``key``
    — the title there); None — it did not come."""
    if not str(url or "").startswith(("http://", "https://")):
        return None
    from core import manhwa_search as MS
    got = MS.fetch_cover(url, page)
    if got is not None or not site:
        return got
    with _LOCK:
        kept = _FETCHED.get(url)
    if kept is not None and time.monotonic() - kept[0] < FETCHED_TTL:
        return kept[1], kept[2]
    from core import manhwa_download as D, manhwa_sources as S
    source = S.by_name(site)
    if source is None:
        return None
    ref = S.TitleRef(site=source.name, key=str(key or ""), url=str(page or ""), title="", cover_url=str(url))
    try:
        data, ctype = D._cover_of(S.Net(), source, ref)
    except S.SourceError as exc:
        _LOG.info("a cover of %s not taken: %s", source.label, exc)
        return None
    if not picture_ext(data):
        return None
    got = (bytes(data), content_type(picture_ext(data)))
    with _LOCK:
        _FETCHED[url] = (time.monotonic(), got[0], got[1])
        while len(_FETCHED) > FETCHED_KEPT:
            _FETCHED.pop(next(iter(_FETCHED)))
    return got


def picture_of(entry: dict) -> "tuple | None":
    """(bytes, type) of a choice kept here: the person's own picture, or the site's cover."""
    if not entry:
        return None
    if entry.get("how") == "own":
        data = own_bytes(entry.get("file") or "")
        return (data, content_type(picture_ext(data))) if data else None
    return fetch(entry.get("url") or "", entry.get("page") or "", entry.get("site") or "", entry.get("key") or "")


# ── «Лента»: a tile of a title chosen for ───────────────────────────────────────────────────────────────────────────
OWN_MARK = "lb-own:"


def catalog_id(entry: dict) -> str:
    """The number ``/api/catalog/cover`` gives a choice's picture by (``core.catalog.cover_id``); "" — none."""
    from core import catalog
    if not entry:
        return ""
    if entry.get("how") == "own":
        return catalog.cover_id(OWN_MARK + str(entry.get("file") or "")) if entry.get("file") else ""
    return catalog.cover_id(str(entry.get("url") or ""), str(entry.get("page") or "")) if entry.get("url") else ""


def own_picture(address: str, shrink: int = 0) -> "tuple | None":
    """(bytes, type) of a person's own picture by its «Лента» address (:data:`OWN_MARK`), smaller than ``shrink``."""
    data = own_bytes(str(address or "")[len(OWN_MARK):]) if str(address or "").startswith(OWN_MARK) else None
    if not data:
        return None
    got = (data, content_type(picture_ext(data)))
    if shrink:
        from core.manhwa_search import _smaller
        got = _smaller(got, shrink)
    return got
