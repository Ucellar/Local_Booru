"""Exact saved source identities for Companion cards on any permitted website."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_CACHE: dict = {}
_LOCK = threading.Lock()
TTL = 20.0
SOURCE_LOOKUP = """SELECT s.url FROM sources s WHERE s.url COLLATE NOCASE IN ({slots}) AND EXISTS (
    SELECT 1 FROM image_sources x CROSS JOIN images i ON i.id=x.image_id
    WHERE x.source_id=s.id AND i.deleted=0)"""


def canonical_url(value: str) -> str:
    try:
        p = urlsplit(str(value or "").strip())
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return ""
        host = p.hostname.lower().removeprefix("www.")
        port = p.port
        if port and port not in (80, 443):
            host += f":{port}"
        query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                                 if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")))
        path = p.path.rstrip("/")
        if not path and not query:
            return ""                 # a saved domain alone never identifies every card on that site
        return urlunsplit(("https", host, path, query, ""))
    except ValueError:
        return ""


def identity(url: str) -> tuple:
    from core import manhwa_sources as S
    url = canonical_url(url)
    if not url:
        return ()
    p = urlsplit(url)
    if p.hostname == "exhentai.org":
        # The same gallery identity only; this module never fetches either host.
        url = urlunsplit((p.scheme, "e-hentai.org", p.path, p.query, ""))
    source, key = S.source_for(url)
    if source is None or not key:
        return ()
    return S.title_identity(source.name, key)


def _index(settings: dict) -> tuple[dict, dict]:
    from core import manga_library as M, manhwa_library as H, manhwa_download as D, video_library as V
    from core import manhwa_sources as S
    roots = D.roots(settings)
    places = (roots.get("manga") or "", roots.get("manhwa") or "", V.video_root(settings))
    stamps = []
    for root in places:
        try:
            stamps.append(Path(root).stat().st_mtime_ns if root else 0)
        except OSError:
            stamps.append(0)
    signature = (places, tuple(stamps))
    with _LOCK:
        cached = _CACHE.get(signature)
        if cached and time.monotonic() - cached[0] < TTL:
            return cached[1]
    urls, keys, folders = {}, {}, set()

    def add(address, title):
        url = canonical_url(address)
        if not url:
            return
        urls[url] = str(title or "")
        key = identity(address)
        if key:
            keys[key] = str(title or "")

    for library, root in ((M.LIBRARY, places[0]), (H.LIBRARY, places[1]), (V.LIBRARY, places[2])):
        if not root or not Path(root).is_dir():
            continue
        for item in library.items(root):
            folders.add(str(Path(item.path)))
            info = getattr(item, "info", {}) or {}
            add(getattr(item, "source_url", "") or info.get("source_url"), item.title)
            came = info.get("source") or {}
            if isinstance(came, dict):
                add(came.get("url"), item.title)
            for chapter in getattr(item, "chapters", ()):
                add(getattr(chapter, "source_url", ""), item.title)
                came = (getattr(chapter, "info", {}) or {}).get("source") or {}
                if isinstance(came, dict):
                    add(came.get("url"), item.title)
    for folder, data in D.followed(settings):
        if str(folder) not in folders:
            continue                  # metadata of an unstarted download is not a local title
        title = str(data.get("title") or "")
        add(data.get("url"), title)
        site, key = str(data.get("site") or ""), str(data.get("key") or "")
        if site and key:
            keys[S.title_identity(site, key)] = title
        # Merely discovered helper titles are deliberately not marked as downloaded.
    result = urls, keys
    with _LOCK:
        _CACHE.clear()
        _CACHE[signature] = time.monotonic(), result
    return result


def _image_sources(settings: dict, addresses: list[str]) -> set[str]:
    """Exact indexed URL lookups, never substring/title or whole-domain matching."""
    from core.database.connection import db
    candidates: dict[str, str] = {}
    for address in addresses:
        canonical = canonical_url(address)
        if not canonical:
            continue
        p = urlsplit(canonical)
        candidates[address] = canonical
        for scheme in ("http", "https"):
            for host in (p.netloc, "www." + p.netloc):
                for path in (p.path, p.path + "/"):
                    candidates[urlunsplit((scheme, host, path, p.query, ""))] = canonical
    found = set()
    with db(settings, readonly=True) as con:
        values = list(candidates)
        for start in range(0, len(values), 200):
            batch = values[start:start + 200]
            query = SOURCE_LOOKUP.format(slots=",".join("?" for _ in batch))
            for row in con.execute(query, batch):
                # The existing URL index is NOCASE; retain case-sensitive paths/IDs in the exact match.
                if row[0] in candidates:
                    found.add(candidates[row[0]])
    return found


def check(settings: dict, items) -> dict:
    from core import manga_works
    asked = [x for x in (items if isinstance(items, list) else []) if isinstance(x, dict)][:300]
    legacy = [x for x in asked if not x.get("url") or
              urlsplit(canonical_url(x.get("url"))).hostname == "nhentai.net"]
    old = {x["id"]: x for x in manga_works.check(settings, legacy).get("items", [])}
    urls, keys = _index(settings)
    pending = [str(x.get("url") or "") for x in asked if x.get("url")]
    images = _image_sources(settings, pending) if pending else set()
    result = []
    for item in asked:
        address = str(item.get("url") or "")
        out = {"id": item.get("id"), "key": item.get("key"), "status": ""}
        if item in legacy:
            out.update(old.get(item.get("id"), {}))
        if address:
            canonical, site_key = canonical_url(address), identity(address)
            if canonical in urls or site_key and site_key in keys or canonical in images:
                out.update(status="same", title=urls.get(canonical, keys.get(site_key, "")), languages=[])
        result.append(out)
    return {"ok": True, "items": result}
