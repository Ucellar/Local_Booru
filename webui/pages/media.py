"""Picture files for the pages: thumbnails (made once, kept in the thumbnail cache) and originals.

The pages ask for them as ``/media/<id>?v=<version>`` (``data.media_url``): the version is the file's md5, so
the browser may keep such an answer for good — another file of the same card, or another card under a number
used before, is another address.  An address without the version, or with an old one, is answered with the
current file and not kept (a page opened before the file changed, a link from elsewhere).
"""
from __future__ import annotations

import mimetypes
import os
import threading
from pathlib import Path
from urllib.parse import quote

from webui import data
from webui.server import Request, Response

# Decoding a big picture takes a lot of memory: a page of 40 new thumbnails must not decode 40 at once.
_MAKING = threading.BoundedSemaphore(max(2, min(6, (os.cpu_count() or 4) // 2)))

KEEP = "private, max-age=31536000, immutable"     # an address with the current version: the same bytes for good
FRESH = "no-cache"                                 # no version or an old one: asked again every time

for _ext, _type in ((".webp", "image/webp"), (".avif", "image/avif"), (".jxl", "image/jxl"), (".webm", "video/webm"),
                    (".mkv", "video/x-matroska"), (".m4v", "video/mp4"), (".mp4", "video/mp4")):
    mimetypes.add_type(_type, _ext)


def register(router) -> None:
    router.add("GET", r"/thumb/(?P<id>\d+)", thumb)
    router.add("GET", r"/media/(?P<id>\d+)", media)
    router.add("GET", r"/poster/(?P<id>\d+)", poster)


def _card_file(req: Request) -> tuple[Path | None, str]:
    """(file, Cache-Control for the answer)."""
    path, version = data.file_and_version_of(req.settings, int(req.params["id"]))
    asked = req.arg("v")
    return path, (KEEP if asked and version and asked == version else FRESH)


def thumb(req: Request) -> Response:
    path, keep = _card_file(req)
    if path is None:
        return _placeholder("нет файла")
    from core.image_safe import cached_thumbnail_path, card_thumbnail_size, safe_thumbnail_path
    size = card_thumbnail_size(req.settings)   # the size made at import: usually already on disk
    made = cached_thumbnail_path(path, *size)
    if not made:
        with _MAKING:
            made = safe_thumbnail_path(str(path), *size)
    if not made or not Path(made).is_file():
        return _placeholder("нет превью")
    return Response(200, content_type="image/jpeg", file=Path(made), headers={"Cache-Control": keep})


POSTER_SIZE = 1280


def poster(req: Request) -> Response:
    """A large frame of a video (or a large preview of a picture) for the post page."""
    path, keep = _card_file(req)
    if path is None:
        return _placeholder("нет файла")
    from core.image_safe import safe_thumbnail_path
    with _MAKING:
        made = safe_thumbnail_path(str(path), POSTER_SIZE, POSTER_SIZE)
    if not made or not Path(made).is_file():
        return _placeholder("нет кадра")
    return Response(200, content_type="image/jpeg", file=Path(made), headers={"Cache-Control": keep})


def media(req: Request) -> Response:
    path, keep = _card_file(req)
    if path is None:
        return Response.not_found("Файла этой карточки нет на диске")
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {"Cache-Control": keep}
    if req.arg("download"):
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(path.name)}"
    return Response(200, content_type=ctype, file=path, headers=headers)


def _placeholder(text: str) -> Response:
    from html import escape
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="150" height="150" viewBox="0 0 150 150">'
           '<rect width="150" height="150" fill="#8884"/>'
           '<text x="75" y="80" font-family="sans-serif" font-size="13" text-anchor="middle" fill="#888">'
           f'{escape(text)}</text></svg>')
    return Response(200, svg.encode("utf-8"), "image/svg+xml", {"Cache-Control": "no-cache"})
