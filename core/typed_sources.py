"""Typed source roles layered over the existing sources/image_sources schema."""
from __future__ import annotations

import time
import re


MIRROR_HOSTS = {
    "rule34.xxx", "gelbooru.com", "danbooru.donmai.us", "e621.net", "e926.net", "booru.allthefallen.moe",
}
_MEDIA_SUFFIX_RE = re.compile(r"\.(?:jpe?g|png|gif|webp|avif|bmp|tiff?|mp4|webm|mov|mkv|avi)(?:$|[?#])", re.I)


def _host(value: str) -> str:
    from core.media_utils import normalize_host
    return normalize_host(value)


def classify_source_role(url: str, *, post_url: str = "", file_url: str = "", site: str = "") -> tuple[str, bool]:
    """Return a conservative role and whether it may become preferred.

    This is the one shared classifier for parser and Browser Companion paths.
    It never infers aliases or author identity from captions.
    """
    clean = str(url or "").strip()
    if not clean.startswith(("http://", "https://")):
        return "", False
    if clean == str(file_url or "").strip():
        return "download_source", False
    host = _host(clean)
    site_host = _host(site)
    if clean == str(post_url or "").strip():
        return ("mirror_post", False) if host in MIRROR_HOSTS or site_host in MIRROR_HOSTS else ("original_source", True)
    if host in MIRROR_HOSTS:
        return "mirror_post", False
    if _MEDIA_SUFFIX_RE.search(clean):
        return "media_url", False
    return "original_source", True

VALID_SOURCE_ROLES = {
    "original_source", "mirror_post", "download_source", "media_url",
    "reverse_match", "author_profile", "related_work", "archive_source",
}


def add_typed_source(con, *, image_id: int, url: str, role: str, host: str = "", preferred: bool = False, provenance: str = "") -> int:
    role = str(role or "mirror_post")
    if role not in VALID_SOURCE_ROLES:
        raise ValueError(f"invalid source role: {role}")
    clean_url = str(url or "").strip()
    if not clean_url:
        raise ValueError("source URL is required")
    # One spelling per host (no "www."), the same as storage._ensure_source: otherwise one URL
    # became two source rows and was shown twice.
    clean_host = _host(host) or _host(clean_url)
    con.execute("INSERT OR IGNORE INTO sources(host,url) VALUES(?,?)", (clean_host, clean_url))
    row = con.execute("SELECT id FROM sources WHERE host=? AND url=?", (clean_host, clean_url)).fetchone()
    source_id = int(row[0])
    con.execute("INSERT OR IGNORE INTO image_sources(image_id,source_id) VALUES(?,?)", (int(image_id), source_id))
    if preferred:
        con.execute("UPDATE image_source_roles SET is_preferred=0 WHERE image_id=? AND role=?", (int(image_id), role))
    con.execute(
        """INSERT INTO image_source_roles(image_id,source_id,role,is_preferred,provenance,observed_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(image_id,source_id,role) DO UPDATE SET
             is_preferred=excluded.is_preferred, provenance=excluded.provenance, observed_at=excluded.observed_at""",
        (int(image_id), source_id, role, 1 if preferred else 0, str(provenance or ""), int(time.time())),
    )
    return source_id
