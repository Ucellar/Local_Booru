"""Previews and samples of grabber cards through the local server, with a disk cache.

Only pictures of cards of a search are fetched: addresses the sites gave, never one from the page,
with the site's session (its cookies, User-Agent) and the post page as the Referer, at most
:data:`PER_HOST` at a time per host.  Only common picture and video types are served — an SVG
or HTML answer is refused (an SVG opened as a page would run its script on the gallery's address).
The cache (``cache/grabber_web``) keeps the size set by ``grabber_cache_limit_mb``; the oldest
files go first.
"""
from __future__ import annotations

import hashlib
import threading
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

PREVIEW_MAX = 6 * 1024 * 1024
SAMPLE_MAX = 60 * 1024 * 1024
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


def _pick(card: dict, variant: str) -> tuple[dict, str]:
    """(entry, URL): the preview — the first site's; the sample — the first site that has one."""
    for item in card["entries"]:
        url = item.get("preview") if variant == "preview" else (item.get("sample") or item.get("preview"))
        if url:
            return item, url
    return card["entries"][0], ""


def _cache_path(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return cache_dir() / digest[:2] / digest


def _cached(url: str) -> tuple[bytes, str] | None:
    base = _cache_path(url)
    for ctype, ext in TYPES.items():
        path = base.with_name(f"{base.name}.{ext}")
        try:
            data = path.read_bytes()
        except OSError:
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


def media(search, key: str, variant: str) -> tuple[bytes, str]:
    """(bytes, content type) of a card's preview (``variant`` "preview") or sample ("sample")."""
    card = search.card(key)
    if card is None:
        raise MediaError(404, "такой карточки нет в этом поиске")
    item, url = _pick(card, "preview" if variant == "preview" else "sample")
    if not url:
        raise MediaError(404, "у поста нет картинки для показа")
    return fetch_picture(search.http, item["site"], url, item.get("url") or f"https://{item['site']}/", search.settings,
                         limit=PREVIEW_MAX if variant == "preview" else SAMPLE_MAX)


def fetch_picture(http, site: str, url: str, referer: str, settings: dict, *, limit: int = PREVIEW_MAX) -> tuple[bytes, str]:
    """(bytes, content type) of a site's picture (an address the site gave — never one from a page), from the cache or
    with the site's session (``http`` — the parser's client) and the post's page as the Referer."""
    hit = _cached(url)
    if hit is not None:
        return hit
    data, ctype = _download(http, site, url, referer, limit)
    _store(url, data, ctype, settings)
    return data, ctype


def _download(http, site: str, url: str, referer: str, limit: int) -> tuple[bytes, str]:
    import requests
    from core.network import PermanentlyDisabledHostError, assert_url_allowed
    try:
        assert_url_allowed(url)
    except PermanentlyDisabledHostError as exc:
        raise MediaError(403, str(exc)) from exc
    host = (urlparse(url).hostname or "").lower()
    with _slot(host):
        session = http.session(site)
        try:
            response = session.get(url, headers={"Referer": referer,
                                                 "Accept": "image/avif,image/webp,image/*,video/*;q=0.9,*/*;q=0.5"},
                                   timeout=(10, 40), stream=True)
        except requests.RequestException as exc:
            raise MediaError(502, f"{host}: нет ответа ({type(exc).__name__})") from exc
        try:
            if response.status_code >= 400:
                raise MediaError(502, f"{host} ответил {response.status_code}")
            ctype = str(response.headers.get("content-type") or "").split(";")[0].strip().lower()
            if ctype not in TYPES:
                raise MediaError(502, f"{host} прислал не картинку ({ctype or 'без типа'})")
            chunks, size = [], 0
            for chunk in response.iter_content(128 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    raise MediaError(502, "файл слишком большой для просмотра")
                chunks.append(chunk)
            return b"".join(chunks), ctype
        except requests.RequestException as exc:
            raise MediaError(502, f"{host}: связь оборвалась ({type(exc).__name__})") from exc
        finally:
            response.close()
