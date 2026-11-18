"""Previews and samples of grabber cards through the local server, with a disk cache.

Only pictures of cards of a search are fetched: addresses the sites gave, never one from the page,
with the site's session (its cookies, User-Agent) and the post page as the Referer, at most
:data:`PER_HOST` at a time per host.  Only common picture and video types are served — an SVG
or HTML answer is refused (an SVG opened as a page would run its script on the gallery's address).
The cache (``cache/grabber_web``) keeps the size set by ``grabber_cache_limit_mb``; the oldest
files go first.

(v5.44.10) A copy the site does not give (the sample refused, no preview) — another copy of the same picture is
shown (the sample: the original, then the preview); a file server that names every file «octet-stream» — the first
bytes say whether it is a picture; a refusal is written to the log once in a while per site (the page of a part of a
сборка showed nothing and said nothing).
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import defaultdict
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse

from .cards import VIDEO_EXTS, _ext_of

_LOG = logging.getLogger("local_booru.grabber_media")

PREVIEW_MAX = 6 * 1024 * 1024
SAMPLE_MAX = 60 * 1024 * 1024
PREVIEW_BUDGET = 18.0
PREVIEW_TIMEOUT = 6.0
PER_HOST = 6
TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp", "image/avif": "avif",
         "image/bmp": "bmp", "video/mp4": "mp4", "video/webm": "webm"}
_SLOTS: dict = defaultdict(lambda: threading.BoundedSemaphore(PER_HOST))
_SLOTS_LOCK = threading.Lock()
_PRUNE_LOCK = threading.Lock()
_STATE: dict = {"total": None}            # bytes in the cache, counted at the first write of a run


class MediaError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def cache_dir() -> Path:
    from core.paths import CACHE_DIR
    return Path(CACHE_DIR) / "grabber_web"


def _slot(host: str) -> threading.BoundedSemaphore:
    with _SLOTS_LOCK:
        return _SLOTS[host]


def _cache_path(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return cache_dir() / digest[:2] / digest


def _cached(url: str, limit: int, still: bool) -> tuple[bytes, str] | None:
    base = _cache_path(url)
    for ext in TYPES.values():
        path = base.with_name(f"{base.name}.{ext}")
        try:
            if path.stat().st_size > limit:
                continue
            with path.open("rb") as stream:
                data = stream.read(limit + 1)
        except OSError:
            continue
        ctype = _sniffed(data[:128])
        if len(data) > limit or not ctype or still and ctype.startswith("video/"):
            continue
        try:
            path.touch()                       # the oldest unused go first
        except OSError:
            pass
        return data, ctype
    return None


def _store(url: str, data: bytes, ctype: str, settings: dict) -> None:
    base = _cache_path(url)
    path = base.with_name(f"{base.name}.{TYPES[ctype]}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        return                                  # a cache: the picture was served anyway
    with _PRUNE_LOCK:
        total = _STATE["total"]
        due = total is None or total + len(data) > _limit(settings)
        if not due:
            _STATE["total"] = total + len(data)
    if due:
        prune(settings)


def _limit(settings: dict) -> int:
    try:
        return max(20, int(settings.get("grabber_cache_limit_mb", 200) or 200)) * 1024 * 1024
    except (TypeError, ValueError):
        return 200 * 1024 * 1024


def prune(settings: dict) -> int:
    """Delete the oldest cached files over ``grabber_cache_limit_mb`` (down to 80 % of it); the
    running total of the cache is counted again."""
    limit = _limit(settings)
    files = []
    for path in cache_dir().glob("*/*"):
        try:
            st = path.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, path))
    total = sum(size for _m, size, _p in files)
    removed = 0
    if total > limit:
        for _mtime, size, path in sorted(files):
            if total <= limit * 0.8:
                break
            try:
                path.unlink()
                total -= size
                removed += 1
            except OSError:
                continue
    with _PRUNE_LOCK:
        _STATE["total"] = total
    return removed


def _tries(card: dict, variant: str) -> list:
    """[(entry, URL, limit)] to try for a card's picture, the wanted one first: (v5.44.10) a site that does not give
    one copy (a sample refused, a preview missing) still shows the post — the other copies of the same picture."""
    out, seen = [], set()

    preview = variant == "preview"
    order = ("preview", "thumbnails", "sample", "larger_copies", "original") if preview else (
        "sample", "larger_copies", "original", "preview", "thumbnails")
    for field in order:
        for item in card["entries"]:
            urls = item.get(field) or ()
            for url in (urls,) if isinstance(urls, str) else urls:
                if url in seen or item.get("video") and (field == "original" or preview and url == item.get("original")):
                    continue
                if preview and _ext_of(url) in VIDEO_EXTS:
                    continue
                seen.add(url)
                limit = PREVIEW_MAX if preview or field in ("preview", "thumbnails") else SAMPLE_MAX
                out.append((item, url, limit))
    return out


def media(search, key: str, variant: str) -> tuple[bytes, str]:
    """(bytes, content type) of a card's preview (``variant`` "preview") or sample ("sample") — or of another copy of
    the same picture when the site does not give that one (the first refusal is said when none comes)."""
    card = search.card(key)
    if card is None:
        raise MediaError(404, "такой карточки нет в этом поиске")
    still = variant == "preview"
    tries = _tries(card, "preview" if still else "sample")
    if not tries:
        raise MediaError(404, "у поста нет картинки для показа")
    deadline = monotonic() + PREVIEW_BUDGET if still else None
    first = None
    for item, url, limit in tries:
        try:
            return fetch_picture(search.http, item["site"], url, item.get("url") or f"https://{item['site']}/",
                                 search.settings, limit=limit, deadline=deadline)
        except MediaError as exc:
            first = first or exc
            _note_refusal(str(item.get("site") or "?"), exc)
    raise first


_NOTED: dict = {}                       # host -> time.monotonic() its refusal was last written to the log
NOTE_EVERY = 10 * 60


def _note_refusal(site: str, exc: Exception) -> None:
    """A site's picture not given — in the log, once in a while per site (a page of tiles is not a page of lines)."""
    import time
    now = time.monotonic()
    if now - _NOTED.get(site, -NOTE_EVERY) < NOTE_EVERY:
        return
    _NOTED[site] = now
    _LOG.info("picture of %s not shown: %s", site, exc)


_UNNAMED = {"", "application/octet-stream", "binary/octet-stream", "application/binary"}


def _sniffed(head: bytes) -> str:
    """The type of a picture or a video by its first bytes ("" — none of :data:`TYPES`): for a site's file server that
    sends every file as «octet-stream» (never an SVG or a page: those are not among them)."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        avif = (b"avif", b"avis")
        end = min(len(head), int.from_bytes(head[:4], "big"))
        if head[8:12] in avif or any(head[n:n + 4] in avif for n in range(16, end - 3, 4)):
            return "image/avif"
        return "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if head.startswith(b"BM"):
        return "image/bmp"
    return ""


def fetch_picture(http, site: str, url: str, referer: str, settings: dict, *, limit: int = PREVIEW_MAX,
                  deadline: float | None = None) -> tuple[bytes, str]:
    """(bytes, content type) of a site's picture (an address the site gave — never one from a page), from the cache or
    with the site's session (``http`` — the parser's client) and the post's page as the Referer."""
    hit = _cached(url, limit, deadline is not None)
    if hit is not None:
        return hit
    data, ctype = _download(http, site, url, referer, limit, deadline)
    _store(url, data, ctype, settings)
    return data, ctype


def _download(http, site: str, url: str, referer: str, limit: int, deadline: float | None) -> tuple[bytes, str]:
    import requests
    from core.network import PermanentlyDisabledHostError, assert_url_allowed
    try:
        assert_url_allowed(url)
    except PermanentlyDisabledHostError as exc:
        raise MediaError(403, str(exc)) from exc
    host = (urlparse(url).hostname or "").lower()
    slot = _slot(host)
    wait = None if deadline is None else max(0.0, deadline - monotonic())
    if not slot.acquire(timeout=wait):
        raise MediaError(503, "сайт занят другими превью")
    try:
        timeout = (10, 40)
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise MediaError(504, "время загрузки превью истекло")
            timeout = (min(PREVIEW_TIMEOUT, remaining),) * 2
        session = http.session(site)
        try:
            response = session.get(url, headers={"Referer": referer,
                                                 "Accept": "image/avif,image/webp,image/*,video/*;q=0.9,*/*;q=0.5"},
                                   timeout=timeout, stream=True)
        except requests.RequestException as exc:
            raise MediaError(502, f"{host}: нет ответа ({type(exc).__name__})") from exc
        try:
            if response.status_code >= 400:
                raise MediaError(502, f"{host} ответил {response.status_code}")
            ctype = str(response.headers.get("content-type") or "").split(";")[0].strip().lower()
            if ctype == "image/jpg":
                ctype = "image/jpeg"
            if ctype not in TYPES and ctype not in _UNNAMED:
                raise MediaError(502, f"{host} прислал не картинку ({ctype or 'без типа'})")
            if deadline is not None and ctype.startswith("video/"):
                raise MediaError(502, f"{host} прислал видео вместо превью")
            try:
                announced = int(response.headers.get("content-length") or 0)
            except ValueError:
                announced = 0
            if announced > limit:
                raise MediaError(502, "файл слишком большой для просмотра")
            chunks, size = [], 0
            for chunk in response.iter_content(128 * 1024):
                if deadline is not None and monotonic() >= deadline:
                    raise MediaError(504, "время загрузки превью истекло")
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    raise MediaError(502, "файл слишком большой для просмотра")
                chunks.append(chunk)
            data = b"".join(chunks)
            ctype = _sniffed(data[:128])
            if not ctype or deadline is not None and ctype.startswith("video/"):
                raise MediaError(502, f"{host} прислал не картинку для показа")
            return data, ctype
        except requests.RequestException as exc:
            raise MediaError(502, f"{host}: связь оборвалась ({type(exc).__name__})") from exc
        finally:
            response.close()
    finally:
        slot.release()
