
from __future__ import annotations

from pathlib import Path
import json
import time
from typing import Iterable

from .connection import db
from core.media_utils import (
    VIDEO_EXTS,
    bucket_for_path,
    image_size,
    safe_stat,
    host_from_url,
)
from core.tag_utils import normalize_tag


def _ensure_image(con, media_path, status="", original_path="", hash_md5=None):
    p = Path(media_path)
    size, mtime_ns = safe_stat(p)
    width, height = image_size(p) if p.exists() else (0, 0)
    bucket = bucket_for_path(p)
    con.execute("""
        INSERT INTO images(path, file_name, bucket, size_bytes, width, height, hash_md5, mtime_ns, is_video, indexed_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
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
    """, (str(p), p.name, bucket, size, width, height, hash_md5, mtime_ns, int(p.suffix.lower() in VIDEO_EXTS), int(time.time())))
    image_id = int(con.execute("SELECT id FROM images WHERE path=?", (str(p),)).fetchone()["id"])
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
        cat = str(category or "general")
        for raw in tags or []:
            name = normalize_tag(str(raw))
            if not name:
                continue
            norm = normalize_tag(name)
            con.execute("INSERT OR IGNORE INTO tags(name, normalized_name, category) VALUES(?,?,?)", (name, norm, cat))
            row = con.execute("SELECT id, category FROM tags WHERE normalized_name=?", (norm,)).fetchone()
            if not row:
                continue
            old_cat = (row["category"] or "general")
            if cat and cat != "general" and old_cat in ("", "general"):
                con.execute("UPDATE tags SET category=? WHERE id=?", (cat, int(row["id"])))
            con.execute("INSERT OR IGNORE INTO image_tags(image_id, tag_id) VALUES(?,?)", (image_id, int(row["id"])))


def replace_image_tags(con, image_id, groups):
    con.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
    add_image_tags(con, image_id, groups)


def add_image_sources(con, image_id, source_text="", extra_sources=None):
    """Add source links without dropping existing source history."""
    urls = []
    for line in str(source_text or "").splitlines():
        for part in line.split():
            if part.startswith(("http://", "https://")):
                urls.append(part.strip())
    for u in extra_sources or []:
        if u:
            urls.append(str(u))
    seen = set()
    for url in urls:
        host = host_from_url(url)
        key = (host, url)
        if not url or key in seen:
            continue
        seen.add(key)
        con.execute("INSERT OR IGNORE INTO sources(host, url) VALUES(?,?)", (host, url))
        row = con.execute("SELECT id FROM sources WHERE host=? AND url=?", (host, url)).fetchone()
        if row:
            con.execute("INSERT OR IGNORE INTO image_sources(image_id, source_id) VALUES(?,?)", (image_id, int(row["id"])))


def replace_image_sources(con, image_id, source_text="", extra_sources=None):
    con.execute("DELETE FROM image_sources WHERE image_id=?", (image_id,))
    add_image_sources(con, image_id, source_text, extra_sources)


def media_path_by_md5(settings, md5: str) -> str:
    """Return an existing on-disk library path for MD5, or an empty string."""
    if not md5:
        return ""
    with db(settings, readonly=True) as con:
        rows = con.execute(
            "SELECT path FROM images WHERE deleted=0 AND hash_md5=? ORDER BY indexed_at DESC",
            (str(md5).lower(),),
        ).fetchall()
    for row in rows:
        try:
            if Path(row["path"]).exists():
                return str(row["path"])
        except Exception:
            continue
    return ""


def md5_exists(settings, md5: str) -> bool:
    """Return True if a file with this MD5 is already in the library."""
    if not md5:
        return False
    with db(settings, readonly=True) as con:
        row = con.execute(
            "SELECT 1 FROM images WHERE hash_md5 = ? LIMIT 1", (md5.lower(),)
        ).fetchone()
    return row is not None


def ensure_image(settings, media_path, status="", original_path="", hash_md5=None):
    """Ensure an image row exists and return its id without replacing tags/source."""
    with db(settings, write=True) as con:
        return _ensure_image(con, media_path, status=status, original_path=original_path, hash_md5=hash_md5)


def upsert_media_metadata(settings, media_path, tags=None, groups=None, source_text="", status="tagged", original_path="", hash_md5=None, raw=None, post_url="", file_url="", site="", merge_existing=False):
    with db(settings, write=True) as con:
        image_id = _ensure_image(con, media_path, status=status, original_path=original_path, hash_md5=hash_md5)
        if groups is None:
            groups = {"general": list(tags or [])}
        if merge_existing:
            add_image_tags(con, image_id, groups)
            add_image_sources(con, image_id, source_text, [post_url, file_url])
        else:
            replace_image_tags(con, image_id, groups)
            replace_image_sources(con, image_id, source_text, [post_url, file_url])
        if raw is not None or post_url or file_url or site:
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
            """, (image_id, site or host_from_url(post_url or file_url), post_url or "", file_url or "", raw_json, int(time.time())))
        return image_id


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


def delete_image_records(settings, media_paths):
    with db(settings, write=True) as con:
        for p in media_paths:
            con.execute("DELETE FROM images WHERE path=?", (str(p),))
            con.execute("DELETE FROM processed_files WHERE media_path=?", (str(p),))
        _cleanup_orphan_rows(con)


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
    deleted_files = errors = 0
    ids = []
    paths = []
    for r in image_rows or []:
        ids.append(int(r["id"]))
        paths.append(Path(r["path"]))
    if delete_files:
        for p in paths:
            try:
                if p.exists() and p.is_file():
                    try:
                        mark_deleted(p, reason="sqlite_delete")
                    except Exception:
                        pass
                    p.unlink()
                    deleted_files += 1
            except Exception:
                errors += 1
            try:
                bucket = p.parent.parent if p.parent.name == "media" else p.parent
                for sub in ("cache", "searched", "tags", "source"):
                    d = bucket / sub
                    if d.exists():
                        for f in d.glob(p.stem + "*"):
                            if f.is_file():
                                f.unlink(missing_ok=True)
            except Exception:
                pass
    with db(settings, write=True) as con:
        for image_id in ids:
            con.execute("DELETE FROM images WHERE id=?", (image_id,))
        for p in paths:
            con.execute("DELETE FROM processed_files WHERE media_path=?", (str(p),))
        _cleanup_orphan_rows(con)
    return {"deleted_files": deleted_files, "errors": errors, "deleted_records": len(ids)}


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
