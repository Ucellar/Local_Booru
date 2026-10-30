"""«Ссылка вместо тегов» of the grabber: the page of a post on a booru site — or its file, when the file's name is
the post's MD5 (``cdn.donmai.us/original/…/<md5>.png``, ``img.rule34.xxx/images/…/<md5>.jpeg``) — becomes a search of
one card: the same card, «есть» mark, post view and «Скачать» as a tag search.  Any site the program knows works,
ticked on «Сайты» or not (``core.parser.sites.site_for_host``); the post is asked by its number (``id:N``) or its
MD5 (``md5:…``) through the site's tag search, or — rule34.us, which has no such search — from the post's page."""
from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import urlparse

EXAMPLE = "https://danbooru.donmai.us/posts/123 или https://rule34.xxx/index.php?page=post&s=view&id=123"


class LinkError(ValueError):
    """What is wrong with the address, said to the person as it is."""


def looks_like_link(text: str) -> bool:
    from core.external_browser import is_web_url
    return is_web_url(str(text or "").strip())


def parse(url: str) -> tuple[str, str, bool]:
    """(the host, the query that finds the post — ``id:N`` or ``md5:…``, whether the host may be a server of the
    site rather than the site itself) — or LinkError."""
    from core.parser.util import host_of, md5_from_filename, post_id_from_url
    text = str(url or "").strip()
    if not looks_like_link(text):
        raise LinkError("Нужна веб-ссылка — она начинается с https:// или http://")
    host = host_of(text)
    post_id = post_id_from_url(text)
    if host and post_id:
        return host, f"id:{post_id}", False
    try:
        name = PurePosixPath(urlparse(text).path).name
    except ValueError:
        name = ""
    md5 = md5_from_filename(name)
    if host and md5:
        return host, f"md5:{md5}", True
    raise LinkError(f"Это не ссылка на пост или файл booru-сайта. Нужна страница поста, например {EXAMPLE}, "
                    "или файл, в имени которого MD5")


def raw_of(post) -> dict:
    """A post read from its page (rule34.us) as the downloader takes posts of an API."""
    return {"id": post.post_id, "md5": post.md5, "file_url": post.file_url, "preview_url": post.preview_url,
            "tags": " ".join(post.tags), "rating": post.rating, "source": (post.sources or [""])[0]}
