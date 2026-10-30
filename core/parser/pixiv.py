"""Pixiv as a donor of exact MD5.

A reverse search (or the file name, ``12345678_p0``) often points at a pixiv artwork.  Danbooru-like
sites can be asked ``pixiv_id:`` directly; the others cannot — but boorus usually keep pixiv
originals byte for byte.  So the originals of the artwork are downloaded (only hashed, never kept)
and their MD5 is looked up on every site.  R-18 works need the pixiv cookies (Companion).

Pixiv being unreachable never holds a file back: it is an extra way to find a file, not a source
that has to answer.
"""
from __future__ import annotations

import hashlib
import re

from core.parser.http import Cancelled, HttpFailure

PAGES_URL = "https://www.pixiv.net/ajax/illust/{pid}/pages"
MAX_PAGES = 3                       # originals hashed per artwork (the hinted page first)
MAX_BYTES = 60 * 1024 * 1024        # an original larger than this is not downloaded
_PAGE_RE = re.compile(r"(?<!\d)(\d{5,})_p(\d+)", re.I)


class PixivUnavailable(Exception):
    pass


def page_hint(*texts: str) -> int | None:
    """``12345678_p2`` in a URL or file name: page 2 of the artwork."""
    for text in texts:
        m = _PAGE_RE.search(str(text or ""))
        if m:
            return int(m.group(2))
    return None


def original_urls(http, pixiv_id: str, page: int | None = None) -> list:
    """Original image URLs of an artwork, the hinted page first, at most MAX_PAGES."""
    if not str(pixiv_id).isdigit():
        return []
    referer = f"https://www.pixiv.net/artworks/{pixiv_id}"
    try:
        response = http.get(PAGES_URL.format(pid=pixiv_id), attempts=2, headers={
            "Accept": "application/json", "Referer": referer, "Origin": "https://www.pixiv.net"})
    except HttpFailure as exc:
        raise PixivUnavailable(f"сеть: {exc.detail}") from exc
    if response.status_code != 200:
        raise PixivUnavailable(f"HTTP {response.status_code}" + (" (нужен вход в pixiv: cookies через Companion)"
                                                                 if response.status_code in (401, 403) else ""))
    try:
        data = response.json()
    except ValueError as exc:
        raise PixivUnavailable("ответ не JSON") from exc
    if not isinstance(data, dict) or data.get("error"):
        raise PixivUnavailable(str((data or {}).get("message") or "ошибка pixiv") if isinstance(data, dict) else "ошибка pixiv")
    urls = []
    for item in data.get("body") or []:
        original = str(((item or {}).get("urls") or {}).get("original") or "")
        if original.startswith("https://") and "pximg.net" in original and str(pixiv_id) in original:
            urls.append(original)
    if page is not None and 0 <= page < len(urls):
        urls.insert(0, urls.pop(page))
    return urls[:MAX_PAGES]


def md5_of_original(http, url: str) -> str:
    """MD5 of an original from i.pximg.net (it needs pixiv's Referer); "" when too large or refused."""
    try:
        response = http.get(url, stream=True, attempts=2, headers={"Referer": "https://www.pixiv.net/", "Accept": "image/*"})
    except HttpFailure as exc:
        raise PixivUnavailable(f"сеть: {exc.detail}") from exc
    try:
        if response.status_code != 200:
            return ""
        if int(response.headers.get("Content-Length") or 0) > MAX_BYTES:
            return ""
        digest = hashlib.md5()
        size = 0
        for chunk in response.iter_content(1 << 16):
            http.check_cancel()
            size += len(chunk)
            if size > MAX_BYTES:
                return ""
            digest.update(chunk)
        return digest.hexdigest() if size else ""
    except Cancelled:
        raise
    except OSError as exc:            # the connection broke in the middle of the file
        raise PixivUnavailable(f"сеть: {exc}") from exc
    finally:
        response.close()


def source_queries(pixiv_id: str) -> list:
    """``source:`` searches for sites without ``pixiv_id:`` — the two forms boorus store:
    i.pximg.net originals (``…/12345678_p0.png``) and pixiv pages (artworks, member_illust)."""
    return [f"source:*pximg.net*{pixiv_id}_p*", f"source:*pixiv.net*{pixiv_id}*"]
