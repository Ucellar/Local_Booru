"""Grabber cards: one file and the posts of every site that has it.

A post found by a tag search becomes an *entry* (site, post page, MD5, tags, the URLs of its
preview, sample and original, size, kind).  Entries with the same MD5 — one file on several
sites — make one card; an entry without an MD5 (Danbooru hides it on restricted posts) is a card
of its own.  :func:`mark_owned` says which cards the library already has.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import urlparse

VIDEO_EXTS = {"mp4", "webm", "mkv", "mov", "m4v", "avi", "zip"}      # zip: Danbooru ugoira
OWNED_FILE, OWNED_POST, OWNED_TRASH = "file", "post", "trash"
_PREVIEW_MARKS = ("/preview", "thumbnail", "/180x180/", "/360x360/", "/thumb/")


def _ext_of(url: str) -> str:
    try:
        return PurePosixPath(urlparse(url).path).suffix.lower().lstrip(".")
    except ValueError:
        return ""


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _size(raw: dict) -> tuple[int, int]:
    file_info = raw.get("file") if isinstance(raw.get("file"), dict) else {}
    width = _int(raw.get("image_width") or raw.get("width") or file_info.get("width"))
    height = _int(raw.get("image_height") or raw.get("height") or file_info.get("height"))
    return width, height


def entry(site, post, raw: dict) -> dict:
    """An entry from a post of a tag search (``site`` — the parser's site client)."""
    from core.downloader_utils import _absolute_url, _media_url_groups
    host = site.host
    originals, reduced, _pages = _media_url_groups(raw, host)
    original = originals[0] if originals else ""
    preview = _absolute_url(post.preview_url, host) or (reduced[-1] if reduced else "")
    # The larger copy for the post view: a sample when the site has one; when it has none (a small
    # picture: Danbooru's large_file_url is the file itself) the original — never the preview again.
    samples = [u for u in reduced if u != preview and not any(m in u.lower() for m in _PREVIEW_MARKS)]
    sample = samples[0] if samples else (original or preview)
    file_info = raw.get("file") if isinstance(raw.get("file"), dict) else {}
    ext = str(raw.get("file_ext") or file_info.get("ext") or _ext_of(original or sample)).lower().lstrip(".")
    width, height = _size(raw)
    return {
        "site": host, "label": site.label, "post_id": str(post.post_id), "url": post.url, "md5": post.md5,
        "rating": post.rating, "tags": list(post.tags), "groups": post.groups, "sources": list(post.sources),
        "preview": preview, "sample": sample, "original": original, "width": width, "height": height,
        "ext": ext, "video": ext in VIDEO_EXTS, "raw": raw,
    }


def card_key(item: dict) -> str:
    return f"m:{item['md5']}" if item.get("md5") else f"p:{item['site']}:{item['post_id']}"


def new_card(item: dict) -> dict:
    return {"key": card_key(item), "md5": item.get("md5") or "", "entries": [item], "owned": "", "image_id": 0}


def identities(card: dict) -> list:
    """What «Скрыть» remembers (core/grabber_exclusions): the MD5, the post pages, the files."""
    out = [("key", card["key"])]
    if card.get("md5"):
        out.append(("md5", card["md5"]))
    for item in card["entries"]:
        out.append(("post_url", item.get("url")))
        if item.get("original"):
            out.append(("file_url", item["original"]))
    return out


def is_hidden(card: dict, hidden: set) -> bool:
    from core.grabber_exclusions import compact_identities
    return any(f"{typ}:{value}" in hidden for typ, value in compact_identities(identities(card)))


def tags_of(card: dict) -> list:
    """Tags of every site of the card, in order of first appearance."""
    seen, out = set(), []
    for item in card["entries"]:
        for tag in item.get("tags") or []:
            low = str(tag).lower()
            if low not in seen:
                seen.add(low)
                out.append(str(tag))
    return out


def mark_owned(settings: dict, cards: list) -> None:
    """``owned`` of each card: «file» — the same file is in the library (its MD5, or a site MD5 of
    a card), «post» — one of its posts is a source of a card (found by reverse search: maybe not
    the same bytes), «trash» — the file is in the trash; ``image_id`` — that card."""
    from core.database.connection import db
    from core.media_utils import host_from_url
    md5s = sorted({c["md5"] for c in cards if c.get("md5")})
    pages: dict[str, list] = {}
    for card in cards:
        for item in card["entries"]:
            if item.get("url"):
                pages.setdefault(host_from_url(item["url"]), []).append(item["url"])
    by_md5: dict[str, tuple] = {}
    by_page: dict[str, tuple] = {}

    def keep(store: dict, key: str, image_id: int, deleted: int) -> None:
        old = store.get(key)
        if old is None or (old[1] and not deleted):            # a live card beats one in the trash
            store[key] = (int(image_id), int(deleted or 0))

    with db(settings, readonly=True) as con:
        for start in range(0, len(md5s), 400):
            chunk = md5s[start:start + 400]
            marks = ",".join("?" for _ in chunk)
            for md5, image_id, deleted in con.execute(
                    f"SELECT hash_md5, id, deleted FROM images WHERE hash_md5 IN ({marks})", chunk):
                keep(by_md5, str(md5).lower(), image_id, deleted)
            for md5, image_id, deleted in con.execute(
                    f"SELECT a.site_md5, i.id, i.deleted FROM image_site_md5_aliases a JOIN images i ON i.id=a.image_id "
                    f"WHERE a.site_md5 IN ({marks})", chunk):
                keep(by_md5, str(md5).lower(), image_id, deleted)
        for host, urls in pages.items():
            urls = sorted(set(urls))
            for start in range(0, len(urls), 400):
                chunk = urls[start:start + 400]
                marks = ",".join("?" for _ in chunk)
                for url, image_id, deleted in con.execute(
                        f"SELECT s.url, i.id, i.deleted FROM sources s JOIN image_sources x ON x.source_id=s.id "
                        f"JOIN images i ON i.id=x.image_id WHERE s.host=? AND s.url IN ({marks})", [host, *chunk]):
                    keep(by_page, str(url), image_id, deleted)
    for card in cards:
        hit = by_md5.get(card.get("md5") or "")
        if hit and not hit[1]:
            card["owned"], card["image_id"] = OWNED_FILE, hit[0]
            continue
        page_hit = next((by_page[i["url"]] for i in card["entries"] if i.get("url") in by_page), None)
        if page_hit and not page_hit[1]:
            card["owned"], card["image_id"] = OWNED_POST, page_hit[0]
        elif hit or page_hit:
            card["owned"], card["image_id"] = OWNED_TRASH, (hit or page_hit)[0]
        else:
            card["owned"], card["image_id"] = "", 0
