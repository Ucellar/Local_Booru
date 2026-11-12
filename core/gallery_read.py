"""(v5.44.148) A gallery of nhentai read in the program without downloading it.

The owner (10.11.2026): «на nhentai прочтение через программу не работает там просто не кликабельные картинки» — the
page of a gallery showed its pages as small pictures that led nowhere; the gallery could only be downloaded.  Now a
page of it opens in the program's one reader (``webui/pages/gallery_read.py`` → ``webui/pages/reader.py``), as a
chapter of a manga site read without downloading does (``core/manga_online.py``).

* the gallery — its description of the site's API, as its card reads it (``manga_download.describe``, kept there):
  one a gate of the program closes is not read (:class:`Closed` with the gate's words);
* a page — fetched by the program from the site's picture servers, the way the download asks them
  (``manga_download._page``: the server that gave the last page first, then the others, then the other kinds), kept
  in memory (:data:`KEPT_BYTES`) and on the disk beside the pages of the manga sites (``manga_online.disk_write``);
* where it was left and how it is read — ``manga_online.json`` (``manga_online.PROGRESS``), a key of its own
  («nhentai:<number>»).
"""
from __future__ import annotations

import threading
from collections import OrderedDict

from core import manga_download as D
from core import manga_online as O
from core import nhentai as N

KEPT_BYTES = 60 * 1024 * 1024          # pages kept in memory at most
TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif"}
LABEL = "nhentai"

_LOCK = threading.Lock()
_PAGES: "OrderedDict[tuple, tuple]" = OrderedDict()      # (the gallery's number, the page) → (bytes, type)
_KEPT = {"bytes": 0}
_LOCAL = threading.local()


class ReadError(RuntimeError):
    """Why a gallery or a page of it is not shown — words for the page."""


class Closed(ReadError):
    """A gate of the program closed the gallery (the gate's words)."""


def gallery(number: int) -> "D.Gallery":
    """The gallery of this number as the site describes it (kept a while by ``manga_download.describe``)."""
    try:
        return D.describe(int(number))
    except D.Forbidden as exc:
        raise Closed(str(exc)) from exc
    except D.MangaError as exc:
        raise ReadError(str(exc)) from exc


def _number(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def sizes(found: "D.Gallery") -> list:
    """[(width, height)] of its pages as the description tells them ((0, 0) — not told)."""
    pages = found.data.get("pages") if isinstance(found.data.get("pages"), list) else []
    out = [(_number(p.get("width")), _number(p.get("height"))) if isinstance(p, dict) else (0, 0)
           for p in pages[:found.pages]]
    return out + [(0, 0)] * (found.pages - len(out))


def thumbs(found: "D.Gallery") -> list:
    """The addresses of its pages' small pictures on the site's servers ("" — none told)."""
    pages = found.data.get("pages") if isinstance(found.data.get("pages"), list) else []
    if pages:
        out = [N.thumb_of(p.get("thumbnail"), found.id) if isinstance(p, dict) else "" for p in pages[:found.pages]]
        return out + [""] * (found.pages - len(out))
    return [N.thumb_of(f"galleries/{found.media_id}/{n}t{ext}", found.id) for n, ext in enumerate(found.kinds, 1)]


def _session():
    session = getattr(_LOCAL, "session", None)
    if session is None:
        session = _LOCAL.session = D._session()
    return session


def _keep(key: tuple, value: tuple) -> None:
    with _LOCK:
        if key in _PAGES:
            return
        _PAGES[key] = value
        _KEPT["bytes"] += len(value[0])
        while _KEPT["bytes"] > KEPT_BYTES and _PAGES:
            _old, (data, _kind) = _PAGES.popitem(last=False)
            _KEPT["bytes"] -= len(data)


def picture(number: int, n: int) -> tuple:
    """(bytes, type) of page ``n`` (from 1) of the gallery — fetched once, kept a while (memory, then the disk)."""
    key = (int(number), int(n))
    with _LOCK:
        kept = _PAGES.get(key)
        if kept is not None:
            _PAGES.move_to_end(key)
            return kept
    found = gallery(number)
    if not 1 <= n <= found.pages:
        raise ReadError("Такой страницы у галереи нет")
    link = f"nhentai-read:{found.id}:{found.media_id}:{n}"
    on_disk = O.disk_read(link)
    if on_disk is not None:
        _keep(key, on_disk)
        return on_disk
    try:
        data, ext = D._page(_session(), found, n, lambda: False)
    except D._Refused as exc:
        raise ReadError(f"{LABEL} проверяет, что заходит не робот ({exc}) — страница не пришла") from exc
    except D.MangaError as exc:
        raise ReadError(f"{LABEL}: страница не пришла — {exc}") from exc
    kind = TYPES.get(ext, "application/octet-stream")
    O.disk_write(link, data, kind)
    _keep(key, (data, kind))
    return data, kind


def forget() -> None:
    with _LOCK:
        _PAGES.clear()
        _KEPT["bytes"] = 0


# ── where it was left ─────────────────────────────────────────────────────────────────────────────────────────────
def key_of(number: int) -> str:
    return f"nhentai:{int(number)}"


def place(number: int) -> dict:
    """{page (from 0), part, done, pages, mode, title, at} of the gallery as it was left ({} — never opened)."""
    return O.PROGRESS.record(key_of(number))


def put_place(number: int, title: str, page: int, part: float = 0.0, done: bool = False, pages: int = 0) -> None:
    O.PROGRESS.put_record(key_of(number), {
        "url": N.link_of(number), "title": str(title or ""), "ch": 1, "label": "Галерея", "page": max(0, int(page)),
        "part": max(0.0, min(1.0, float(part))), "done": bool(done), **({"pages": int(pages)} if pages else {})})


def put_mode(number: int, mode: str) -> None:
    O.PROGRESS.put_record_mode(key_of(number), mode)
