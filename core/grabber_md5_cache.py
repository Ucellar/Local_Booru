"""Persistent grabber Local Reverse Index.

This is durable parser/tagger metadata, not a thumbnail/temporary cache. Every
post discovered by the online grabber can be stored in a separate SQLite file
under settings/db together with its source URLs, tags and tag categories. Exact
real MD5 entries are additionally exposed through the compact MD5 lookup table
so the parser can later tag identical local files without network requests.

The old v255 location under settings/cache/grabber_md5_cache is treated only as
a legacy migration source. New writes go to settings/db/grabber_local_reverse_index.sqlite.

The index is deliberately exact-MD5 only for automatic parser reuse. Visual
/pHash duplicates with different byte hashes may be kept as UI hints by the
grabber, but they must not be treated as source proof for parser writes.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
import zlib
from pathlib import Path
from typing import Any

from core.paths import CACHE_DIR, DB_DIR

_SCHEMA = """
CREATE TABLE IF NOT EXISTS grabber_md5_cache (
    md5 TEXT PRIMARY KEY,
    updated_at INTEGER NOT NULL,
    payload_z BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_grabber_md5_cache_updated_at ON grabber_md5_cache(updated_at);

CREATE TABLE IF NOT EXISTS grabber_post_cache (
    identity TEXT PRIMARY KEY,
    md5 TEXT,
    site TEXT,
    post_url TEXT,
    file_url TEXT,
    updated_at INTEGER NOT NULL,
    payload_z BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_grabber_post_cache_md5 ON grabber_post_cache(md5);
CREATE INDEX IF NOT EXISTS idx_grabber_post_cache_site ON grabber_post_cache(site);
CREATE INDEX IF NOT EXISTS idx_grabber_post_cache_updated_at ON grabber_post_cache(updated_at);
"""


def enabled(settings: dict | None) -> bool:
    try:
        s = settings or {}
        return bool(s.get("grabber_disk_metadata_cache_enabled", s.get("developer_grabber_md5_cache_enabled", True)))
    except Exception:
        return False


def legacy_cache_path(settings: dict | None = None) -> Path:
    """Old v255 location, kept only so existing data is copied forward once."""
    return CACHE_DIR / "grabber_md5_cache" / "grabber_md5_cache.sqlite"


def cache_path(settings: dict | None = None) -> Path:
    # This is not thumbnail cache. It is durable local reverse-search metadata
    # and belongs with the other SQLite databases under settings/db.
    root = DB_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root / "grabber_local_reverse_index.sqlite"


def _migrate_legacy_cache_if_needed(target: Path) -> None:
    """Copy v255 cache-location DB to the durable DB location once.

    Keep the old file in place because it lived under cache and the user may
    delete it manually; after migration all new writes use target only.
    """
    try:
        target = Path(target)
        if target.exists():
            return
        legacy = legacy_cache_path(None)
        if not legacy.is_file():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(str(legacy), timeout=20) as old_con, sqlite3.connect(str(target), timeout=20) as new_con:
                old_con.backup(new_con)
        except Exception:
            shutil.copy2(str(legacy), str(target))
    except Exception:
        return


def _normalize_md5(value: Any) -> str:
    import re
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"[0-9a-f]{32}", value) else ""


def _connect(settings: dict | None = None) -> sqlite3.Connection:
    db_path = cache_path(settings)
    _migrate_legacy_cache_if_needed(db_path)
    con = sqlite3.connect(str(db_path), timeout=20)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(_SCHEMA)
    return con


def compact_groups(groups: dict | None) -> dict:
    out: dict[str, list[str]] = {}
    if not isinstance(groups, dict):
        return out
    for group, values in groups.items():
        vals = []
        for tag in values or []:
            tag = str(tag or "").strip()
            if tag and tag not in vals:
                vals.append(tag)
        if vals:
            out[str(group)] = vals
    return out


def build_payload(*, md5: str, post_urls=None, file_urls=None, groups=None, tags=None, source_tag_groups=None, sites=None, post=None, method="grabber") -> dict:
    md5 = _normalize_md5(md5)
    tags_out = []
    for t in tags or []:
        t = str(t or "").strip()
        if t and t not in tags_out:
            tags_out.append(t)
    stg_out = []
    for stg in source_tag_groups or []:
        if not isinstance(stg, dict):
            continue
        u = str(stg.get("url") or "").strip()
        if not u:
            continue
        stg_out.append({
            "url": u,
            "method": str(stg.get("method") or method),
            "groups": compact_groups(stg.get("groups") or groups or {}),
        })
    return {
        "md5": md5,
        "updated_at": int(time.time()),
        "method": method,
        "sites": list(dict.fromkeys(str(x) for x in (sites or []) if str(x or "").strip())),
        "post_urls": list(dict.fromkeys(str(x) for x in (post_urls or []) if str(x or "").strip())),
        "file_urls": list(dict.fromkeys(str(x) for x in (file_urls or []) if str(x or "").strip())),
        "tags": tags_out,
        "groups": compact_groups(groups),
        "source_tag_groups": stg_out,
        # Keep only a tiny post hint, not the full API dump.
        "post": {
            "id": str((post or {}).get("id") or "") if isinstance(post, dict) else "",
            "rating": str((post or {}).get("rating") or "") if isinstance(post, dict) else "",
        },
    }



def _merge_list_unique(*seqs) -> list[str]:
    out: list[str] = []
    for seq in seqs:
        for x in seq or []:
            x = str(x or "").strip()
            if x and x not in out:
                out.append(x)
    return out


def _merge_groups(*groups_list) -> dict:
    merged: dict[str, list[str]] = {}
    for groups in groups_list:
        for group, values in compact_groups(groups).items():
            merged.setdefault(group, [])
            for tag in values:
                if tag not in merged[group]:
                    merged[group].append(tag)
    return merged


def _merge_source_tag_groups(*stg_lists) -> list[dict]:
    """Merge per-source tag groups instead of letting the last cache write win.

    The grabber can see the same exact MD5 on ATF/e621/etc. over several page
    loads.  The persistent cache is parser-side metadata, so it must accumulate
    sources and their tags.  Replacing a payload for the same MD5 can make two
    equal cards appear with the same source set but different tag sets depending
    on which site updated the cache last.
    """
    by_url: dict[str, dict] = {}
    order: list[str] = []
    for stg_list in stg_lists:
        for stg in stg_list or []:
            if not isinstance(stg, dict):
                continue
            url = str(stg.get("url") or "").strip()
            if not url:
                continue
            if url not in by_url:
                by_url[url] = {"url": url, "method": str(stg.get("method") or "grabber"), "groups": {}}
                order.append(url)
            cur = by_url[url]
            cur["groups"] = _merge_groups(cur.get("groups") or {}, stg.get("groups") or {})
            method = str(stg.get("method") or "").strip()
            if method and method not in str(cur.get("method") or ""):
                cur["method"] = (str(cur.get("method") or "") + "+" + method).strip("+")
    return [by_url[u] for u in order]


def merge_payloads(old: dict | None, new: dict | None) -> dict:
    """Union two exact-MD5 metadata payloads for parser reuse.

    This is intentionally exact-MD5 only.  Visual/pHash matches with different
    MD5 values are UI grouping hints and must not be folded into this disk cache.
    """
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    md5 = _normalize_md5(new.get("md5") or old.get("md5"))
    merged = dict(new or old)
    merged["md5"] = md5
    merged["updated_at"] = int(time.time())
    merged["method"] = str(new.get("method") or old.get("method") or "grabber")
    merged["sites"] = _merge_list_unique(old.get("sites"), new.get("sites"))
    merged["post_urls"] = _merge_list_unique(old.get("post_urls"), new.get("post_urls"))
    merged["file_urls"] = _merge_list_unique(old.get("file_urls"), new.get("file_urls"))
    merged["tags"] = _merge_list_unique(old.get("tags"), new.get("tags"))
    merged["groups"] = _merge_groups(old.get("groups") or {}, new.get("groups") or {})
    merged["source_tag_groups"] = _merge_source_tag_groups(old.get("source_tag_groups"), new.get("source_tag_groups"))
    # Keep a tiny, stable post hint.  Prefer the newest non-empty fields.
    old_post = old.get("post") if isinstance(old.get("post"), dict) else {}
    new_post = new.get("post") if isinstance(new.get("post"), dict) else {}
    merged["post"] = {
        "id": str(new_post.get("id") or old_post.get("id") or ""),
        "rating": str(new_post.get("rating") or old_post.get("rating") or ""),
    }
    return merged




def _first(values) -> str:
    for value in values or []:
        value = str(value or "").strip()
        if value:
            return value
    return ""


def _host_from_url(value: str) -> str:
    try:
        from urllib.parse import urlparse
        host = urlparse(str(value or "")).netloc.lower().replace("www.", "")
        return host
    except Exception:
        return ""


def identity_for_payload(payload: dict | None) -> tuple[str, str]:
    """Return stable grabber post identity for the all-card cache.

    Prefer the post URL because different sites can legitimately host the same
    exact MD5 and should remain separately visible in the local index.  Fall
    back to file URL, then MD5 when a site exposes only direct media.
    """
    payload = payload if isinstance(payload, dict) else {}
    post_url = _first(payload.get("post_urls"))
    if post_url:
        return "post_url", post_url
    file_url = _first(payload.get("file_urls"))
    if file_url:
        return "file_url", file_url
    md5 = _normalize_md5(payload.get("md5"))
    if md5:
        return "md5", md5
    return "", ""



def build_item_payload(item: dict | None, *, method: str = "grabber_preview") -> dict:
    item = item if isinstance(item, dict) else {}
    md5 = _normalize_md5(item.get("md5") or (item.get("post") or {}).get("md5") or "")
    post_urls = item.get("post_urls") or []
    file_urls = list(dict.fromkeys(
        ([str(item.get("download_url") or "")] if item.get("download_url") else [])
        + list(item.get("file_urls") or [])
        + ([str(item.get("preview_url") or "")] if item.get("preview_url") else [])
    ))
    return build_payload(
        md5=md5,
        post_urls=post_urls,
        file_urls=file_urls,
        groups=item.get("groups") or {},
        tags=item.get("tags") or [],
        source_tag_groups=item.get("source_tag_groups") or [],
        sites=item.get("sites") or [],
        post=item.get("post") or {},
        method=method,
    )


def upsert_item(settings: dict | None, item: dict | None, *, thumb_path: str | Path | None = None, method: str = "grabber_preview") -> bool:
    """Store one online-grabber card in the separate Local Reverse Index.

    This writes durable metadata only:
    * grabber_post_cache: every found card/post identity with source URLs, tags
      and tag categories;
    * grabber_md5_cache: exact-MD5 shortcut used automatically by parser/tagger.

    ``thumb_path`` is accepted only for backward compatibility with older caller
    code. Preview bytes are deliberately not stored here: thumbnails belong in
    the bounded thumbnail/grabber UI cache, not in the source/tag identity DB.
    """
    if not enabled(settings) or not isinstance(item, dict):
        return False
    payload = build_item_payload(item, method=method)
    typ, value = identity_for_payload(payload)
    if not typ or not value:
        return False
    identity = f"{typ}:{value}"
    md5 = _normalize_md5(payload.get("md5"))
    post_url = _first(payload.get("post_urls"))
    file_url = _first(payload.get("file_urls"))
    site = _first(payload.get("sites")) or _host_from_url(post_url or file_url)
    now = int(time.time())

    with _connect(settings) as con:
        existing = None
        row = con.execute(
            "SELECT payload_z FROM grabber_post_cache WHERE identity=? LIMIT 1",
            (identity,),
        ).fetchone()
        if row:
            try:
                existing = json.loads(zlib.decompress(row[0]).decode("utf-8", "replace"))
            except Exception:
                existing = None
        payload = merge_payloads(existing, payload)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        packed = zlib.compress(raw, level=6)
        con.execute(
            "INSERT INTO grabber_post_cache(identity, md5, site, post_url, file_url, updated_at, payload_z) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(identity) DO UPDATE SET "
            "md5=excluded.md5, site=excluded.site, post_url=excluded.post_url, file_url=excluded.file_url, "
            "updated_at=excluded.updated_at, payload_z=excluded.payload_z",
            (identity, md5, site, post_url, file_url, now, packed),
        )

    # Keep parser/tagger exact-MD5 shortcut in the compact table too.  This is
    # deliberately separate from post identity storage and refuses no-MD5 cards.
    if md5:
        return upsert(settings, payload)
    return True


def lookup_posts_by_md5(settings: dict | None, md5: str, *, limit: int = 50) -> list[dict]:
    if not enabled(settings):
        return []
    md5 = _normalize_md5(md5)
    if not md5:
        return []
    out: list[dict] = []
    try:
        with _connect(settings) as con:
            rows = con.execute(
                "SELECT payload_z FROM grabber_post_cache WHERE md5=? ORDER BY updated_at DESC LIMIT ?",
                (md5, int(limit or 50)),
            ).fetchall()
        for (payload_z,) in rows:
            try:
                data = json.loads(zlib.decompress(payload_z).decode("utf-8", "replace"))
                if isinstance(data, dict):
                    out.append(data)
            except Exception:
                continue
    except Exception:
        return []
    return out


def post_count(settings: dict | None = None) -> int:
    try:
        with _connect(settings) as con:
            row = con.execute("SELECT COUNT(*) FROM grabber_post_cache").fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0

def upsert(settings: dict | None, payload: dict | None) -> bool:
    if not enabled(settings) or not isinstance(payload, dict):
        return False
    md5 = _normalize_md5(payload.get("md5"))
    if not md5:
        return False
    payload = dict(payload)
    payload["md5"] = md5
    payload.setdefault("updated_at", int(time.time()))

    with _connect(settings) as con:
        existing = None
        row = con.execute("SELECT payload_z FROM grabber_md5_cache WHERE md5=? LIMIT 1", (md5,)).fetchone()
        if row:
            try:
                existing = json.loads(zlib.decompress(row[0]).decode("utf-8", "replace"))
            except Exception:
                existing = None
        payload = merge_payloads(existing, payload)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        packed = zlib.compress(raw, level=6)
        con.execute(
            "INSERT INTO grabber_md5_cache(md5, updated_at, payload_z) VALUES(?,?,?) "
            "ON CONFLICT(md5) DO UPDATE SET updated_at=excluded.updated_at, payload_z=excluded.payload_z",
            (md5, int(payload.get("updated_at") or time.time()), packed),
        )
    return True


def lookup(settings: dict | None, md5: str) -> dict | None:
    if not enabled(settings):
        return None
    md5 = _normalize_md5(md5)
    if not md5:
        return None
    try:
        with _connect(settings) as con:
            row = con.execute("SELECT payload_z FROM grabber_md5_cache WHERE md5=? LIMIT 1", (md5,)).fetchone()
        if not row:
            return None
        raw = zlib.decompress(row[0]).decode("utf-8", "replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def count(settings: dict | None = None) -> int:
    try:
        with _connect(settings) as con:
            row = con.execute("SELECT COUNT(*) FROM grabber_md5_cache").fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0
