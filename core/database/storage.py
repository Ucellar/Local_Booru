
from __future__ import annotations

from pathlib import Path
import json
import time
from typing import Iterable
from urllib.parse import urlparse, parse_qs

from .connection import db
from core.media_utils import (
    VIDEO_EXTS,
    bucket_for_path,
    image_size,
    safe_stat,
    host_from_url,
)
from core.tag_utils import normalize_tag


_META_TAG_OVERRIDES = {"artist_request"}

def _category_for_tag(name: str, category: str) -> str:
    """Apply Local Booru presentation rules to technical booru tags."""
    norm = normalize_tag(str(name or ""))
    if norm in _META_TAG_OVERRIDES:
        return "meta"
    return str(category or "general")



def _has_column(con, table: str, column: str) -> bool:
    try:
        return column in {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return False

def _ensure_image(con, media_path, status="", original_path="", hash_md5=None, lifecycle=None, inbox_until=0, import_origin=""):
    """Ensure row; lifecycle is only changed when the caller explicitly supplies it."""
    p = Path(media_path)
    size, mtime_ns = safe_stat(p)
    width, height = image_size(p) if p.exists() else (0, 0)
    bucket = bucket_for_path(p)
    initial_lifecycle = str(lifecycle or "archive")
    con.execute("""
        INSERT INTO images(path, file_name, bucket, size_bytes, width, height, hash_md5, mtime_ns, is_video, indexed_at, lifecycle, inbox_until, import_origin)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(path) DO UPDATE SET
            file_name=excluded.file_name,
            bucket=excluded.bucket,
            size_bytes=excluded.size_bytes,
            width=excluded.width,
            height=excluded.height,
            hash_md5=COALESCE(excluded.hash_md5, images.hash_md5),
            mtime_ns=excluded.mtime_ns,
            is_video=excluded.is_video,
            indexed_at=excluded.indexed_at
    """, (str(p), p.name, bucket, size, width, height, hash_md5, mtime_ns, int(p.suffix.lower() in VIDEO_EXTS), int(time.time()), initial_lifecycle, int(inbox_until or 0), str(import_origin or "")))
    image_id = int(con.execute("SELECT id FROM images WHERE path=?", (str(p),)).fetchone()["id"])
    try:
        if _has_column(con, "images", "original_file_name"):
            original_name = Path(original_path).name if original_path else p.name
            con.execute(
                "UPDATE images SET original_file_name=CASE WHEN COALESCE(original_file_name,'')='' THEN ? ELSE original_file_name END, content_name_policy=CASE WHEN COALESCE(content_name_policy,'')='' AND COALESCE(hash_md5,'')<>'' AND file_name LIKE '%__%' THEN 'md5_suffix' ELSE COALESCE(content_name_policy,'') END WHERE id=?",
                (original_name, image_id),
            )
    except Exception:
        pass
    if lifecycle is not None:
        con.execute("UPDATE images SET lifecycle=?, inbox_until=?, import_origin=CASE WHEN ?<>'' THEN ? ELSE import_origin END, deleted=0 WHERE id=?", (str(lifecycle), int(inbox_until or 0), str(import_origin or ""), str(import_origin or ""), image_id))
    if original_path or status:
        op = Path(original_path) if original_path else p
        con.execute("""
            INSERT INTO processed_files(original_path, original_name, media_path, status, bucket, processed_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(media_path) DO UPDATE SET
                original_path=excluded.original_path,
                original_name=excluded.original_name,
                status=excluded.status,
                bucket=excluded.bucket,
                processed_at=excluded.processed_at
        """, (str(original_path or ""), op.name, str(p), status or "", bucket, int(time.time())))
    return image_id


def add_image_tags(con, image_id, groups):
    """Add tag links without dropping tags discovered by another source."""
    groups = groups or {}
    if isinstance(groups, list):
        groups = {"general": groups}
    for category, tags in groups.items():
        for raw in tags or []:
            name = normalize_tag(str(raw))
            if not name:
                continue
            cat = _category_for_tag(name, category)
            norm = normalize_tag(name)
            con.execute("INSERT OR IGNORE INTO tags(name, normalized_name, category) VALUES(?,?,?)", (name, norm, cat))
            row = con.execute("SELECT id, category FROM tags WHERE normalized_name=?", (norm,)).fetchone()
            if not row:
                continue
            old_cat = (row["category"] or "general")
            if cat and ((cat == "species" and old_cat != "species") or (cat != "general" and old_cat in ("", "general"))):
                con.execute("UPDATE tags SET category=? WHERE id=?", (cat, int(row["id"])))
            con.execute("INSERT OR IGNORE INTO image_tags(image_id, tag_id) VALUES(?,?)", (image_id, int(row["id"])))


def replace_image_tags(con, image_id, groups):
    con.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
    add_image_tags(con, image_id, groups)


def _is_navigational_source_url(url: str) -> bool:
    """Reject gallery/search/navigation URLs that are not evidence for one post.

    Reverse services occasionally emit Gelbooru list links such as
    ``page=post&s=list&tags=all`` alongside a real result on another site.
    They must never become image sources because opening them shows the site
    gallery rather than the confirmed file.  This guard is deliberately narrow:
    concrete post URLs and ordinary external/file source URLs stay valid.
    """
    try:
        parsed = urlparse(str(url or ""))
        path = str(parsed.path or "").lower()
        query = {str(k).lower(): [str(v).lower() for v in vals] for k, vals in parse_qs(parsed.query).items()}
        full = (path + "?" + str(parsed.query or "")).lower()
        if "/posts/random" in full or "/post/random" in full:
            return True
        page = (query.get("page") or [""])[0]
        action = (query.get("s") or [""])[0]
        if page == "post" and action == "list":
            return True
        return False
    except Exception:
        return False


def _ensure_source(con, url: str):
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    if _is_navigational_source_url(url):
        return None
    host = host_from_url(url)
    if not host:
        return None
    con.execute("INSERT OR IGNORE INTO sources(host, url) VALUES(?,?)", (host, url))
    row = con.execute("SELECT id FROM sources WHERE host=? AND url=?", (host, url)).fetchone()
    return (int(row["id"]), host, url) if row else None


def add_image_sources(con, image_id, source_text="", extra_sources=None):
    """Add source links without dropping existing source history; return linked IDs."""
    urls = []
    for line in str(source_text or "").splitlines():
        for part in line.split():
            if part.startswith(("http://", "https://")):
                urls.append(part.strip())
    for u in extra_sources or []:
        if u:
            urls.append(str(u))
    seen = set()
    linked = []
    for url in urls:
        item = _ensure_source(con, url)
        if not item:
            continue
        source_id, host, url = item
        key = (host, url)
        if key in seen:
            continue
        seen.add(key)
        con.execute("INSERT OR IGNORE INTO image_sources(image_id, source_id) VALUES(?,?)", (image_id, source_id))
        linked.append({"source_id": source_id, "host": host, "url": url})
    return linked


def _iter_source_tag_entries(source_tag_groups):
    for entry in source_tag_groups or []:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        groups = entry.get("groups") or {}
        tags = entry.get("tags") or []
        if not groups and tags:
            groups = {"general": list(tags)}
        if url and groups:
            yield url, groups, str(entry.get("method") or "")


def _sync_merged_image_tags(con, image_id: int) -> None:
    """Make the fast All/source-search index equal the union of stored site sets."""
    row = con.execute("SELECT 1 FROM image_source_tags WHERE image_id=? LIMIT 1", (int(image_id),)).fetchone()
    if not row:
        return
    con.execute("DELETE FROM image_tags WHERE image_id=?", (int(image_id),))
    con.execute("""
        INSERT OR IGNORE INTO image_tags(image_id, tag_id)
        SELECT image_id, tag_id FROM image_source_tags WHERE image_id=?
    """, (int(image_id),))


def add_image_source_tag_groups(con, image_id, source_tag_groups, *, replace_sources=False):
    """Store one independent confirmed tag set per site/host.

    A site's newest confirmed lookup replaces that site's previous tag set for
    this image.  Different sites remain independent and their deduplicated union
    is mirrored in ``image_tags`` for fast ordinary search.
    """
    now = int(time.time())
    stored = 0
    touched_hosts = set()
    for url, groups, method in _iter_source_tag_entries(source_tag_groups):
        source = _ensure_source(con, url)
        if not source:
            continue
        source_id, host, _url = source
        con.execute("INSERT OR IGNORE INTO image_sources(image_id, source_id) VALUES(?,?)", (image_id, source_id))
        # Tags belong to the site, not to one historical result URL. If a later
        # exact MD5 match supersedes an earlier IQDB candidate on the same host,
        # never combine the two posts' metadata.  If a buggy caller submits two
        # candidates for one host in one transaction, the last confirmed set wins.
        con.execute("""
            DELETE FROM image_source_tags
            WHERE image_id=? AND source_id IN (SELECT id FROM sources WHERE host=?)
        """, (image_id, host))
        touched_hosts.add(host)
        for category, values in (groups or {}).items():
            for raw in values or []:
                name = normalize_tag(str(raw))
                if not name:
                    continue
                cat = _category_for_tag(name, category)
                norm = normalize_tag(name)
                con.execute("INSERT OR IGNORE INTO tags(name, normalized_name, category) VALUES(?,?,?)", (name, norm, cat))
                row = con.execute("SELECT id, category FROM tags WHERE normalized_name=?", (norm,)).fetchone()
                if not row:
                    continue
                tag_id = int(row["id"])
                old_cat = str(row["category"] or "general")
                if cat and ((cat == "species" and old_cat != "species") or (cat != "general" and old_cat in ("", "general")) or norm in _META_TAG_OVERRIDES):
                    con.execute("UPDATE tags SET category=? WHERE id=?", (cat, tag_id))
                con.execute("""
                    INSERT INTO image_source_tags(image_id, source_id, tag_id, category, acquisition, updated_at)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(image_id, source_id, tag_id) DO UPDATE SET
                        category=excluded.category,
                        acquisition=CASE WHEN excluded.acquisition<>'' THEN excluded.acquisition ELSE image_source_tags.acquisition END,
                        updated_at=excluded.updated_at
                """, (image_id, source_id, tag_id, cat, method, now))
                stored += 1
    if touched_hosts:
        _sync_merged_image_tags(con, image_id)
    return stored


def replace_image_sources(con, image_id, source_text="", extra_sources=None):
    con.execute("DELETE FROM image_sources WHERE image_id=?", (image_id,))
    con.execute("DELETE FROM image_source_tags WHERE image_id=?", (image_id,))
    add_image_sources(con, image_id, source_text, extra_sources)




def replace_media_tag_groups(settings, media_path, groups) -> bool:
    """Apply a user edit to one card using SQLite only; no sidecar files exist in live mode."""
    with db(settings, write=True) as con:
        row = con.execute("SELECT id FROM images WHERE path=? AND deleted=0", (str(Path(media_path)),)).fetchone()
        if not row:
            return False
        replace_image_tags(con, int(row["id"]), groups or {})
        con.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM image_tags)")
    return True


def remove_media_tag_link(settings, media_path, tag_name, source_host="all") -> bool:
    """Remove one visible tag while respecting per-source metadata sets.

    In ``all`` mode the user is removing the tag from the image, so every
    source-specific link for that tag is removed.  In a source view only that
    site's copy is removed; other sites remain intact.  Legacy rows without
    provenance continue to use ``image_tags``.
    """
    path = str(Path(media_path))
    norm = normalize_tag(str(tag_name or ""))
    host = str(source_host or "all").strip().lower().replace("www.", "")
    if not norm:
        return False
    with db(settings, write=True) as con:
        image = con.execute("SELECT id FROM images WHERE path=? AND deleted=0", (path,)).fetchone()
        tag = con.execute("SELECT id FROM tags WHERE normalized_name=?", (norm,)).fetchone()
        if not image or not tag:
            return False
        image_id = int(image["id"]); tag_id = int(tag["id"])
        has_provenance = bool(con.execute("SELECT 1 FROM image_source_tags WHERE image_id=? LIMIT 1", (image_id,)).fetchone())
        if has_provenance:
            if host and host != "all":
                con.execute("""
                    DELETE FROM image_source_tags
                    WHERE image_id=? AND tag_id=? AND source_id IN (SELECT id FROM sources WHERE LOWER(REPLACE(host,'www.',''))=?)
                """, (image_id, tag_id, host))
            else:
                con.execute("DELETE FROM image_source_tags WHERE image_id=? AND tag_id=?", (image_id, tag_id))
            if con.execute("SELECT 1 FROM image_source_tags WHERE image_id=? LIMIT 1", (image_id,)).fetchone():
                _sync_merged_image_tags(con, image_id)
            else:
                con.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
        else:
            con.execute("DELETE FROM image_tags WHERE image_id=? AND tag_id=?", (image_id, tag_id))
        con.execute("""
            DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM image_tags)
                                AND id NOT IN (SELECT DISTINCT tag_id FROM image_source_tags)
        """)
    return True


def remove_media_source_link(settings, media_path, source_url="", source_host="") -> bool:
    """Remove selected source link from one live card while retaining other confirmations."""
    path = str(Path(media_path))
    url = str(source_url or "").strip()
    host = str(source_host or "").strip()
    with db(settings, write=True) as con:
        row = con.execute("SELECT id FROM images WHERE path=? AND deleted=0", (path,)).fetchone()
        if not row:
            return False
        image_id = int(row["id"])
        if url:
            source_ids = [int(r["id"]) for r in con.execute("SELECT id FROM sources WHERE url=?", (url,)).fetchall()]
        elif host:
            source_ids = [int(r["id"]) for r in con.execute("SELECT id FROM sources WHERE host=?", (host,)).fetchall()]
        else:
            return False
        for source_id in source_ids:
            con.execute("DELETE FROM image_source_tags WHERE image_id=? AND source_id=?", (image_id, source_id))
            con.execute("DELETE FROM image_sources WHERE image_id=? AND source_id=?", (image_id, source_id))
        if con.execute("SELECT 1 FROM image_source_tags WHERE image_id=? LIMIT 1", (image_id,)).fetchone():
            _sync_merged_image_tags(con, image_id)
        else:
            con.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
        con.execute("DELETE FROM sources WHERE id NOT IN (SELECT DISTINCT source_id FROM image_sources)")
    return True

def media_path_by_md5(settings, md5: str, *, preferred_bucket: str = "", require_bucket: bool = False, exclude_path: str = "") -> str:
    """Return one existing live canonical media path for an exact MD5.

    Exact byte-identical files are one library object with many sources/tags.
    ``preferred_bucket`` keeps a new FOUND result from being collapsed into an
    older NO_MATCH placeholder; callers can require that bucket when needed.
    """
    value = str(md5 or "").strip().lower()
    if not value:
        return ""
    where = ["deleted=0", "lower(COALESCE(hash_md5,''))=?"]
    params = [value]
    if exclude_path:
        where.append("path<>?")
        params.append(str(exclude_path))
    if preferred_bucket and require_bucket:
        where.append("bucket=?")
        params.append(str(preferred_bucket))
    order = "indexed_at ASC, id ASC"
    if preferred_bucket and not require_bucket:
        order = "CASE WHEN bucket=? THEN 0 WHEN bucket='found' THEN 1 ELSE 2 END, indexed_at ASC, id ASC"
        params.append(str(preferred_bucket))
    with db(settings, readonly=True) as con:
        rows = con.execute(
            f"SELECT path FROM images WHERE {' AND '.join(where)} ORDER BY {order}",
            params,
        ).fetchall()
    for row in rows:
        try:
            if Path(row["path"]).exists():
                return str(row["path"])
        except Exception:
            continue
    return ""


def found_media_path_by_md5(settings, md5: str, *, exclude_path: str = "") -> str:
    """Return a live FOUND-like canonical path for identical content."""
    return (
        media_path_by_md5(settings, md5, preferred_bucket="found", require_bucket=True, exclude_path=exclude_path)
        or media_path_by_md5(settings, md5, preferred_bucket="downloaded_found", require_bucket=True, exclude_path=exclude_path)
    )


def md5_exists(settings, md5: str) -> bool:
    """Return True if a file with this MD5 is already in the library."""
    if not md5:
        return False
    with db(settings, readonly=True) as con:
        row = con.execute(
            "SELECT 1 FROM images WHERE hash_md5 = ? LIMIT 1", (md5.lower(),)
        ).fetchone()
    return row is not None


def ensure_image(settings, media_path, status="", original_path="", hash_md5=None, lifecycle=None, inbox_until=0, import_origin=""):
    """Ensure an image row exists and return its id without replacing tags/source."""
    with db(settings, write=True) as con:
        return _ensure_image(con, media_path, status=status, original_path=original_path, hash_md5=hash_md5, lifecycle=lifecycle, inbox_until=inbox_until, import_origin=import_origin)


def upsert_media_metadata(settings, media_path, tags=None, groups=None, source_text="", status="tagged", original_path="", hash_md5=None, raw=None, post_url="", file_url="", site="", merge_existing=False, lifecycle=None, inbox_until=0, import_origin="", source_tag_groups=None):
    with db(settings, write=True) as con:
        image_id = _ensure_image(con, media_path, status=status, original_path=original_path, hash_md5=hash_md5, lifecycle=lifecycle, inbox_until=inbox_until, import_origin=import_origin)
        if groups is None:
            groups = {"general": list(tags or [])}
        if merge_existing:
            add_image_tags(con, image_id, groups)
            add_image_sources(con, image_id, source_text, [post_url, file_url])
            add_image_source_tag_groups(con, image_id, source_tag_groups, replace_sources=False)
        else:
            replace_image_tags(con, image_id, groups)
            replace_image_sources(con, image_id, source_text, [post_url, file_url])
            add_image_source_tag_groups(con, image_id, source_tag_groups, replace_sources=True)
        safe_post_url = str(post_url or "").strip()
        if _is_navigational_source_url(safe_post_url):
            safe_post_url = ""
        if raw is not None or safe_post_url or file_url or site:
            try:
                raw_json = json.dumps(raw if raw is not None else {}, ensure_ascii=False)
            except Exception:
                raw_json = "{}"
            con.execute("""
                INSERT INTO raw_metadata(image_id, site, post_url, file_url, raw_json, updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(image_id) DO UPDATE SET
                    site=excluded.site, post_url=excluded.post_url, file_url=excluded.file_url,
                    raw_json=excluded.raw_json, updated_at=excluded.updated_at
            """, (image_id, site or host_from_url(safe_post_url or file_url), safe_post_url, file_url or "", raw_json, int(time.time())))
        return image_id


def refine_source_tag_categories(settings, media_path, source_url, groups, *, method="category_refine"):
    """Refine categories for tags already stored for one source only.

    HTML/category lookups are never allowed to add new tags here.  This keeps a
    noisy sidebar or recommendation block from polluting an image record.
    """
    path = str(Path(media_path))
    url = str(source_url or "").strip()
    candidate_count = sum(len(v or []) for v in (groups or {}).values())
    if not path or not url:
        return {"updated": 0, "ignored": candidate_count, "known": 0}
    with db(settings, write=True) as con:
        image = con.execute("SELECT id FROM images WHERE path=? AND deleted=0", (path,)).fetchone()
        source = con.execute("SELECT id FROM sources WHERE url=?", (url,)).fetchone()
        if not image or not source:
            return {"updated": 0, "ignored": candidate_count, "known": 0}
        image_id, source_id = int(image["id"]), int(source["id"])
        existing = con.execute("""
            SELECT ist.tag_id, t.normalized_name FROM image_source_tags ist
            JOIN tags t ON t.id=ist.tag_id
            WHERE ist.image_id=? AND ist.source_id=?
        """, (image_id, source_id)).fetchall()
        known = {str(r["normalized_name"]): int(r["tag_id"]) for r in existing}
        updated = 0
        seen_candidates = set()
        now = int(time.time())
        for category, values in (groups or {}).items():
            cat = str(category or "general")
            for raw in values or []:
                norm = normalize_tag(str(raw))
                if not norm:
                    continue
                seen_candidates.add(norm)
                tag_id = known.get(norm)
                if tag_id is None:
                    continue
                cat = _category_for_tag(norm, cat)
                con.execute("UPDATE image_source_tags SET category=?, acquisition=?, updated_at=? WHERE image_id=? AND source_id=? AND tag_id=?",
                            (cat, str(method or "category_refine"), now, image_id, source_id, tag_id))
                old = con.execute("SELECT category FROM tags WHERE id=?", (tag_id,)).fetchone()
                old_cat = str(old["category"] or "general") if old else "general"
                if cat != "general" and old_cat in ("", "general"):
                    con.execute("UPDATE tags SET category=? WHERE id=?", (cat, tag_id))
                updated += 1
        ignored = len(seen_candidates - set(known))
        return {"updated": updated, "ignored": ignored, "known": len(known)}


def mark_processed(settings, media_path, status="nomatch", original_path=""):
    with db(settings, write=True) as con:
        return _ensure_image(con, media_path, status=status, original_path=original_path)


def processed_status(settings, original_path):
    p = Path(original_path)
    with db(settings) as con:
        rows = con.execute("""
            SELECT status, media_path FROM processed_files
            WHERE original_path=?
            ORDER BY processed_at DESC LIMIT 1
        """, (str(p),)).fetchall()
    for r in rows:
        try:
            if Path(r["media_path"]).exists():
                return r["status"]
        except Exception:
            pass
    return None


def processed_status_many(settings, original_paths):
    paths = [Path(x) for x in (original_paths or [])]
    if not paths:
        return {}
    result = {}
    with db(settings) as con:
        for i in range(0, len(paths), 500):
            chunk = paths[i:i+500]
            keys = [str(x) for x in chunk]
            placeholders = ",".join(["?"] * len(keys))
            rows = con.execute(f"""
                SELECT original_path, status, media_path, processed_at FROM processed_files
                WHERE original_path IN ({placeholders})
                ORDER BY processed_at DESC
            """, keys).fetchall()
            seen = set()
            for r in rows:
                op = r["original_path"]
                if op in seen:
                    continue
                seen.add(op)
                try:
                    if Path(r["media_path"]).exists():
                        result[op] = r["status"]
                except Exception:
                    pass
    return result


def processed_records_many(settings, original_paths):
    """Return latest existing archive status and media path for original inputs."""
    paths = [Path(x) for x in (original_paths or [])]
    if not paths:
        return {}
    result = {}
    with db(settings) as con:
        for i in range(0, len(paths), 500):
            keys = [str(x) for x in paths[i:i+500]]
            placeholders = ",".join(["?"] * len(keys))
            rows = con.execute(f"""
                SELECT original_path, status, media_path, processed_at FROM processed_files
                WHERE original_path IN ({placeholders})
                ORDER BY processed_at DESC
            """, keys).fetchall()
            for row in rows:
                original = str(row["original_path"] or "")
                if original in result:
                    continue
                try:
                    if Path(row["media_path"]).exists():
                        result[original] = {"status": str(row["status"] or ""), "media_path": str(row["media_path"] or "")}
                except Exception:
                    pass
    return result


def mark_site_scanned(settings, original_path, site_key, *, engine="", scan_revision=1, outcome="miss", checked_md5="", source_url=""):
    """Persist one completed exact-MD5 check for a single file/source lane.

    Network failures must not call this function: a failed lane remains pending
    and will be retried without forcing all successful sites to run again.
    """
    original = str(Path(original_path))
    key = str(site_key or "").strip().lower()
    if not original or not key:
        return
    with db(settings, write=True) as con:
        con.execute("""
            INSERT INTO site_scan_status(original_path, site_key, engine, scan_revision, outcome, checked_md5, source_url, checked_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(original_path, site_key, scan_revision) DO UPDATE SET
                engine=excluded.engine, outcome=excluded.outcome, checked_md5=excluded.checked_md5,
                source_url=excluded.source_url, checked_at=excluded.checked_at
        """, (original, key, str(engine or ""), int(scan_revision or 1), str(outcome or "miss"),
              str(checked_md5 or "").lower(), str(source_url or ""), int(time.time())))


def site_scan_status_many(settings, original_paths, site_keys, *, scan_revision=1):
    """Return {original_path: {site_key: outcome}} for the active site set."""
    paths = [str(Path(x)) for x in (original_paths or [])]
    keys = [str(x or "").strip().lower() for x in (site_keys or []) if str(x or "").strip()]
    if not paths or not keys:
        return {}
    result = {}
    with db(settings) as con:
        for i in range(0, len(paths), 400):
            chunk = paths[i:i+400]
            pp = ",".join(["?"] * len(chunk))
            kk = ",".join(["?"] * len(keys))
            rows = con.execute(f"""
                SELECT original_path, site_key, outcome FROM site_scan_status
                WHERE scan_revision=? AND original_path IN ({pp}) AND site_key IN ({kk})
            """, [int(scan_revision or 1), *chunk, *keys]).fetchall()
            for row in rows:
                result.setdefault(str(row["original_path"]), {})[str(row["site_key"])] = str(row["outcome"] or "")
    return result


def pending_site_scan_paths(settings, original_paths, site_keys, *, scan_revision=1):
    """Return paths that still need at least one of the requested site checks."""
    paths = [Path(x) for x in (original_paths or [])]
    keys = {str(x or "").strip().lower() for x in (site_keys or []) if str(x or "").strip()}
    if not keys:
        return paths, {}, 0
    done_map = site_scan_status_many(settings, paths, keys, scan_revision=scan_revision)
    pending = [p for p in paths if not keys.issubset(set(done_map.get(str(p), {})))]
    completed = len(paths) - len(pending)
    return pending, done_map, completed



# ── v158: shared category cache for flat-tag sites ───────────────────────────

def cached_tag_categories(settings, host, tags):
    """Return cached {normalized_tag: category} for a site.

    Category lookup by post used to repeat the same remote tag-list calls for
    thousands of files.  This cache is site-scoped and conservative: only
    normalized tag names explicitly stored here are reused.
    """
    host = str(host or "").strip().lower().replace("www.", "")
    names = []
    seen = set()
    for tag in tags or []:
        name = normalize_tag(str(tag))
        if name and name not in seen:
            seen.add(name); names.append(name)
    if not host or not names:
        return {}
    out = {}
    with db(settings, readonly=True) as con:
        for i in range(0, len(names), 500):
            chunk = names[i:i+500]
            ph = ",".join(["?"] * len(chunk))
            try:
                rows = con.execute(
                    f"SELECT tag_name, category FROM tag_category_cache WHERE site_key=? AND tag_name IN ({ph})",
                    [host, *chunk],
                ).fetchall()
            except Exception:
                return out
            for row in rows:
                name = normalize_tag(row["tag_name"])
                cat = str(row["category"] or "general").lower()
                if name:
                    out[name] = cat or "general"
    return out


def upsert_tag_category_cache(settings, host, mapping, *, method="dapi_tag_api"):
    host = str(host or "").strip().lower().replace("www.", "")
    if not host or not mapping:
        return 0
    now = int(time.time())
    rows = []
    for tag, category in dict(mapping or {}).items():
        name = normalize_tag(str(tag))
        if not name:
            continue
        cat = str(category or "general").strip().lower() or "general"
        rows.append((host, name, cat, str(method or ""), now))
    if not rows:
        return 0
    with db(settings, write=True) as con:
        con.executemany("""
            INSERT INTO tag_category_cache(site_key, tag_name, category, source_method, updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(site_key, tag_name) DO UPDATE SET
                category=excluded.category, source_method=excluded.source_method, updated_at=excluded.updated_at
        """, rows)
    return len(rows)


def enqueue_tag_enrichment(settings, original_path, media_path, source_url, *, job_key="rule34.xxx::html-categories-v1"):
    """Queue a durable low-priority category pass for a matched post URL."""
    original = str(Path(original_path))
    media = str(Path(media_path)) if media_path else ""
    source = str(source_url or "").strip()
    key = str(job_key or "").strip().lower()
    if not original or not source or not key:
        return
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute("""
            INSERT INTO tag_enrichment_queue
            (original_path, job_key, media_path, source_url, status, retry_after, attempts, last_error, queued_at, updated_at)
            VALUES(?,?,?,?, 'pending', 0, 0, '', ?, ?)
            ON CONFLICT(original_path, job_key, source_url) DO UPDATE SET
                media_path=excluded.media_path,
                status=CASE WHEN tag_enrichment_queue.status='done' THEN 'done' ELSE 'pending' END,
                updated_at=excluded.updated_at
        """, (original, key, media, source, now, now))


def seed_background_tag_enrichment(settings, *, job_key="flat-sites::tag-groups-v4-gelbooru-live-markup-guard", hosts=("gelbooru.com", "rule34.xxx", "xbooru.com", "hypnohub.net")):
    """Backfill low-priority category jobs for sources that return flat post tags.

    Native grouped JSON sources (Danbooru/e621/ATF where categories exist in the
    same response) do not need another network pass. Gelbooru-family flat-tag
    matches are enriched later so exact-search lanes never wait for category I/O.
    """
    key = str(job_key or "").strip().lower()
    normalized = []
    for host in hosts or ():
        host = str(host or "").strip().lower().replace("www.", "")
        if host and host not in normalized:
            normalized.append(host)
    if not key or not normalized:
        return 0
    clauses = []
    args = [key, int(time.time()), int(time.time())]
    for host in normalized:
        clauses.append("(s.site_key=? OR s.site_key LIKE ?)")
        args.extend([host, host + "::%"])
    where_hosts = " OR ".join(clauses)
    with db(settings, write=True) as con:
        before = con.total_changes
        con.execute(f"""
            INSERT OR IGNORE INTO tag_enrichment_queue
            (original_path, job_key, media_path, source_url, status, retry_after, attempts, last_error, queued_at, updated_at)
            SELECT s.original_path, ?, p.media_path, s.source_url, 'pending', 0, 0, '', ?, ?
            FROM site_scan_status s
            JOIN processed_files p ON p.original_path=s.original_path
            WHERE ({where_hosts})
              AND s.outcome='match' AND COALESCE(s.source_url, '') <> ''
              AND p.status IN ('found','tagged')
        """, tuple(args))
        return max(0, con.total_changes - before)


def seed_rule34_category_enrichment(settings, *, job_key="rule34.xxx::html-categories-v1"):
    """Compatibility wrapper for older rule34-only queue versions/tests."""
    return seed_background_tag_enrichment(settings, job_key=job_key, hosts=("rule34.xxx",))


def pending_tag_enrichments(settings, *, job_key="rule34.xxx::html-categories-v1", now=None, limit=10000):
    key = str(job_key or "").strip().lower()
    stamp = int(time.time() if now is None else now)
    with db(settings) as con:
        rows = con.execute("""
            SELECT original_path, media_path, source_url, job_key, attempts
            FROM tag_enrichment_queue
            WHERE job_key=? AND status='pending' AND retry_after<=?
            ORDER BY queued_at, original_path LIMIT ?
        """, (key, stamp, int(limit))).fetchall()
    return [dict(row) for row in rows]


def complete_tag_enrichment(settings, original_path, source_url, *, job_key="rule34.xxx::html-categories-v1", status="done", error=""):
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute("""
            UPDATE tag_enrichment_queue SET status=?, last_error=?, updated_at=?
            WHERE original_path=? AND job_key=? AND source_url=?
        """, (str(status or "done"), str(error or "")[:1000], now, str(Path(original_path)), str(job_key).lower(), str(source_url or "")))


def retry_tag_enrichment(settings, original_path, source_url, *, job_key="rule34.xxx::html-categories-v1", delay_seconds=300, error=""):
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute("""
            UPDATE tag_enrichment_queue SET status='pending', retry_after=?, attempts=attempts+1, last_error=?, updated_at=?
            WHERE original_path=? AND job_key=? AND source_url=?
        """, (now + int(delay_seconds), str(error or "")[:1000], now, str(Path(original_path)), str(job_key).lower(), str(source_url or "")))


def enqueue_reverse_retry(settings, original_path, *, service="saucenao", retry_after=0, reason="limit"):
    """Persist a reverse-search retry without marking the file as NO_MATCH."""
    original = str(Path(original_path))
    svc = str(service or "saucenao").strip().lower()
    when = int(retry_after or time.time())
    if not original or not svc:
        return
    with db(settings, write=True) as con:
        con.execute("""
            INSERT INTO reverse_retry_queue(original_path, service, retry_after, reason, queued_at, attempts)
            VALUES(?,?,?,?,?,1)
            ON CONFLICT(original_path, service) DO UPDATE SET
                retry_after=excluded.retry_after, reason=excluded.reason,
                queued_at=excluded.queued_at, attempts=reverse_retry_queue.attempts + 1
        """, (original, svc, when, str(reason or "limit"), int(time.time())))


def remove_reverse_retry(settings, original_path, *, service="saucenao"):
    original = str(Path(original_path))
    svc = str(service or "saucenao").strip().lower()
    with db(settings, write=True) as con:
        con.execute("DELETE FROM reverse_retry_queue WHERE original_path=? AND service=?", (original, svc))


def due_reverse_retry_paths(settings, *, service="saucenao", now=None, limit=10000):
    svc = str(service or "saucenao").strip().lower()
    stamp = int(time.time() if now is None else now)
    with db(settings) as con:
        rows = con.execute("""
            SELECT original_path, retry_after, reason FROM reverse_retry_queue
            WHERE service=? AND retry_after<=?
            ORDER BY retry_after, queued_at LIMIT ?
        """, (svc, stamp, int(limit))).fetchall()
    return [(Path(r["original_path"]), int(r["retry_after"] or 0), str(r["reason"] or "")) for r in rows]


def pending_reverse_retry_paths(settings, *, service="saucenao", limit=100000):
    """Return every durable retry row, including entries still in cooldown.

    The live conveyor restores these rows on application restart so a file that
    already exhausted IQDB/Ascii2D is not sent through those services again just
    because SauceNAO was cooling down when the previous run stopped.
    """
    svc = str(service or "saucenao").strip().lower()
    with db(settings) as con:
        rows = con.execute("""
            SELECT original_path, retry_after, reason FROM reverse_retry_queue
            WHERE service=?
            ORDER BY retry_after, queued_at LIMIT ?
        """, (svc, int(limit))).fetchall()
    return [(Path(r["original_path"]), int(r["retry_after"] or 0), str(r["reason"] or "")) for r in rows]


def pending_reverse_retry_info(settings, *, service="saucenao"):
    svc = str(service or "saucenao").strip().lower()
    with db(settings) as con:
        row = con.execute("""
            SELECT COUNT(*) AS n, MIN(retry_after) AS next_retry FROM reverse_retry_queue
            WHERE service=?
        """, (svc,)).fetchone()
    return int(row["n"] or 0), int(row["next_retry"] or 0)


def delete_image_records(settings, media_paths):
    with db(settings, write=True) as con:
        for p in media_paths:
            originals = [r["original_path"] for r in con.execute("SELECT original_path FROM processed_files WHERE media_path=?", (str(p),)).fetchall()]
            con.execute("DELETE FROM images WHERE path=?", (str(p),))
            con.execute("DELETE FROM processed_files WHERE media_path=?", (str(p),))
            for original in originals:
                con.execute("DELETE FROM site_scan_status WHERE original_path=?", (str(original),))
        _cleanup_orphan_rows(con)


def get_nomatches_for_tineye(settings) -> list:
    """Return paths of files with no tags that are candidates for TinEye search."""
    from pathlib import Path as _P
    try:
        with db(settings) as con:
            rows = con.execute(
                "SELECT path FROM images WHERE "
                "(tags IS NULL OR tags = '' OR tags = '[]') "
                "ORDER BY indexed_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            p = str(row["path"] if hasattr(row, "keys") else row[0])
            if _P(p).exists():
                result.append(p)
        return result
    except Exception:
        return []


def cleanup_missing(settings):
    removed = 0
    with db(settings, write=True) as con:
        for row in con.execute("SELECT id, path FROM images").fetchall():
            if not Path(row["path"]).exists():
                con.execute("DELETE FROM images WHERE id=?", (row["id"],))
                removed += 1
        con.execute("DELETE FROM processed_files WHERE media_path NOT IN (SELECT path FROM images)")
        _cleanup_orphan_rows(con)
    return removed


def _scope_where(scope):
    scope = scope or "all"
    if scope == "tagger":
        return "i.bucket IN ('found','partial_match','no_match')", []
    if scope == "downloader":
        return "i.bucket LIKE 'downloaded%'", []
    if scope == "found":
        return "i.bucket IN ('found','partial_match','downloaded_found','downloaded_partial_match')", []
    if scope == "no_match":
        return "i.bucket IN ('no_match','downloaded_no_match')", []
    return "", []


def candidate_tags(settings, scope="all"):
    with db(settings) as con:
        where, args = _scope_where(scope)
        sql = """
            SELECT t.name, COUNT(*) c FROM tags t
            JOIN image_tags it ON it.tag_id=t.id
            JOIN images i ON i.id=it.image_id
        """
        if where:
            sql += " WHERE " + where
        sql += " GROUP BY t.id ORDER BY t.name COLLATE NOCASE"
        return [r["name"] for r in con.execute(sql, args).fetchall()]


def candidate_sources(settings, scope="all"):
    with db(settings) as con:
        where, args = _scope_where(scope)
        sql = """
            SELECT s.url, s.host, COUNT(DISTINCT i.id) c FROM sources s
            JOIN image_sources isrc ON isrc.source_id=s.id
            JOIN images i ON i.id=isrc.image_id
        """
        if where:
            sql += " WHERE " + where
        sql += " GROUP BY s.id ORDER BY s.host COLLATE NOCASE, s.url COLLATE NOCASE"
        rows = con.execute(sql, args).fetchall()
        return [r["url"] or r["host"] for r in rows]


def find_images_by_tag(settings, tag, scope="all", limit=None):
    with db(settings) as con:
        where, args = _scope_where(scope)
        clauses = ["t.normalized_name=?"]
        params = [normalize_tag(tag)]
        if where:
            clauses.append(where); params += args
        sql = """
            SELECT DISTINCT i.id, i.path, i.file_name, i.bucket FROM images i
            JOIN image_tags it ON it.image_id=i.id
            JOIN tags t ON t.id=it.tag_id
            WHERE """ + " AND ".join(clauses) + " ORDER BY i.path COLLATE NOCASE"
        if limit:
            sql += " LIMIT ?"; params.append(int(limit))
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def find_images_by_source(settings, source_text, scope="all", limit=None):
    with db(settings) as con:
        q = "%" + str(source_text or "").lower() + "%"
        where, args = _scope_where(scope)
        clauses = ["(LOWER(s.url) LIKE ? OR LOWER(s.host) LIKE ?)"]
        params = [q, q]
        if where:
            clauses.append(where); params += args
        sql = """
            SELECT DISTINCT i.id, i.path, i.file_name, i.bucket FROM images i
            JOIN image_sources isrc ON isrc.image_id=i.id
            JOIN sources s ON s.id=isrc.source_id
            WHERE """ + " AND ".join(clauses) + " ORDER BY i.path COLLATE NOCASE"
        if limit:
            sql += " LIMIT ?"; params.append(int(limit))
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def delete_images(settings, image_rows, delete_files=True):
    from core.deleted_registry import mark_deleted
    from core.source_protection import require_managed_media_mutation
    from core.services.media_storage_service import unlink_managed, delete_bucket_artifacts
    deleted_files = errors = protected_source_skipped = 0
    ids = []
    paths = []
    for r in image_rows or []:
        candidate = Path(r["path"])
        if delete_files and not require_managed_media_mutation(settings, candidate, "storage.delete_images"):
            protected_source_skipped += 1
            continue
        ids.append(int(r["id"]))
        paths.append(candidate)
    if delete_files:
        for p in paths:
            try:
                if p.exists() and p.is_file():
                    try:
                        mark_deleted(p, reason="sqlite_delete", settings=settings, manual_delete=True)
                    except Exception:
                        pass
                    if unlink_managed(settings, p, operation="storage.delete_images"):
                        deleted_files += 1
            except Exception:
                errors += 1
            delete_bucket_artifacts(settings, p, operation="storage.delete_side_artifacts")
    with db(settings, write=True) as con:
        for image_id in ids:
            con.execute("DELETE FROM images WHERE id=?", (image_id,))
        for p in paths:
            con.execute("DELETE FROM processed_files WHERE media_path=?", (str(p),))
        _cleanup_orphan_rows(con)
    return {"deleted_files": deleted_files, "errors": errors, "deleted_records": len(ids), "protected_source_skipped": protected_source_skipped}


def _cleanup_orphan_rows(con):
    con.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM image_tags)")
    con.execute("DELETE FROM sources WHERE id NOT IN (SELECT DISTINCT source_id FROM image_sources)")

# --- v27 compatibility aliases -------------------------------------------------
def cleanup_orphan_rows(settings):
    from core.database.repository import cleanup_orphans
    with db(settings, write=True) as con:
        cleanup_orphans(con)


def database_stats(settings):
    with db(settings, readonly=True) as con:
        out = {}
        for name in ("images", "tags", "image_tags", "sources", "image_sources", "processed_files", "delete_log"):
            try:
                out[name] = int(con.execute(f"SELECT COUNT(*) AS c FROM {name}").fetchone()["c"] or 0)
            except Exception:
                out[name] = 0
        return out


# --- v115 diagnostics event audit ------------------------------------------------
def record_task_event(settings, task_type, status, message=""):
    """Persist a lightweight operational proof for the diagnostics page."""
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute(
            "INSERT INTO task_log(task_type,status,message,created_at,updated_at) VALUES(?,?,?,?,?)",
            (str(task_type or ""), str(status or ""), str(message or ""), now, now),
        )
