
from __future__ import annotations

from pathlib import Path
import json
import time

from .connection import db
from core.media_utils import (
    MEDIA_EXTS,
    VIDEO_EXTS,
    file_md5,
    image_size,
    scan_roots,
    bucket_for_path,
    clean_tags,
    iter_media_files,
)
from core.tag_utils import normalize_tag


def read_tag_json(path: Path, settings):
    candidates = [path.with_suffix(".tags.json"), Path(str(path) + ".tags.json")]
    try:
        if path.parent.name == "media" and path.parent.parent.name in ("found", "partial_match", "no_match"):
            candidates.append(path.parent.parent / "tags" / (path.stem + ".tags.json"))
    except Exception:
        pass
    for c in candidates:
        if c.exists():
            try:
                d = json.loads(c.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    groups = d.get("groups") if isinstance(d.get("groups"), dict) else d
                    out = {}
                    for k, v in groups.items():
                        if isinstance(v, list):
                            out[str(k)] = clean_tags(v, settings)
                    if out:
                        return out
            except Exception:
                pass
    return None


def read_tags_txt(path: Path, settings):
    suffix = settings.get("tags_suffix", ".tags.txt")
    candidates = [path.with_suffix(suffix), path.with_suffix(".tags.txt"), path.with_suffix(".txt"), Path(str(path)+".txt")]
    try:
        if path.parent.name == "media" and path.parent.parent.name in ("found", "partial_match", "no_match"):
            candidates += [path.parent.parent / "tags" / (path.stem + ".tags.txt")]
    except Exception:
        pass
    for c in candidates:
        if c.exists():
            try:
                text = c.read_text(encoding="utf-8", errors="ignore").replace("\n", ",")
                return clean_tags([x.strip() for x in text.split(",") if x.strip()], settings)
            except Exception:
                return []
    return []


def read_sources(path: Path, settings):
    from core.media_utils import host_from_url
    suffix = settings.get("sources_suffix", ".sources.txt")
    candidates = [path.with_suffix(suffix), path.with_suffix(".sources.txt"), Path(str(path)+".sources.txt")]
    try:
        if path.parent.name == "media" and path.parent.parent.name in ("found", "partial_match", "no_match"):
            candidates.append(path.parent.parent / "source" / (path.stem + ".sources.txt"))
    except Exception:
        pass
    out = []
    for c in candidates:
        if not c.exists():
            continue
        try:
            for line in c.read_text(encoding="utf-8", errors="ignore").splitlines():
                urls = [p for p in line.strip().split() if p.startswith(("http://", "https://"))]
                if urls:
                    u = urls[-1]
                    out.append({"host": host_from_url(u), "url": u})
        except Exception:
            pass
        if out:
            break
    return out


def upsert_tags(con, image_id, groups):
    con.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
    for category, tags in (groups or {}).items():
        cat = str(category or "general")
        for tag in tags:
            norm = normalize_tag(tag)
            if not norm:
                continue
            con.execute("INSERT OR IGNORE INTO tags(name, normalized_name, category) VALUES(?,?,?)", (tag, norm, cat))
            row = con.execute("SELECT id, category FROM tags WHERE normalized_name=?", (norm,)).fetchone()
            if row:
                old = row["category"] or "general"
                if (cat == "species" and old != "species") or (cat != "general" and old in ("", "general")):
                    con.execute("UPDATE tags SET category=? WHERE id=?", (cat, int(row["id"])))
                con.execute("INSERT OR IGNORE INTO image_tags(image_id, tag_id) VALUES(?,?)", (image_id, row["id"]))


def upsert_sources(con, image_id, sources):
    con.execute("DELETE FROM image_sources WHERE image_id=?", (image_id,))
    for s in sources or []:
        host, url = s.get("host",""), s.get("url","")
        if not url:
            continue
        con.execute("INSERT OR IGNORE INTO sources(host, url) VALUES(?,?)", (host, url))
        row = con.execute("SELECT id FROM sources WHERE host=? AND url=?", (host, url)).fetchone()
        if row:
            con.execute("INSERT OR IGNORE INTO image_sources(image_id, source_id) VALUES(?,?)", (image_id, row["id"]))


def index_library(settings, force=False, progress=None, stop_check=None, compute_md5=None, import_legacy_sidecars=False):
    """Rebuild/update SQLite index.

    Heavy full-file MD5 remains opt-in. UI must call this from a worker, not
    directly while opening pages.
    """
    now = int(time.time())
    indexed = skipped = removed = scanned = 0
    if compute_md5 is None:
        compute_md5 = bool(settings.get("sqlite_compute_md5_on_index", False))
    with db(settings, write=True) as con:
        for path in iter_media_files(scan_roots(settings), stop_check=stop_check):
            scanned += 1
            sp = str(path)
            try:
                st = path.stat()
                mtime_ns, size = int(st.st_mtime_ns), int(st.st_size)
            except Exception:
                continue
            old = con.execute("SELECT id, mtime_ns, size_bytes, hash_phash FROM images WHERE path=?", (sp,)).fetchone()
            if old and not force and int(old["mtime_ns"]) == mtime_ns and int(old["size_bytes"]) == size:
                # Fast path: file unchanged. If phash was missing from an older
                # index, fill it opportunistically without rebuilding metadata.
                if bool(settings.get("sqlite_compute_phash_on_index", True)) and not old["hash_phash"] and path.suffix.lower() not in VIDEO_EXTS:
                    try:
                        from core.tagger.engine import file_phash
                        ph = file_phash(path)
                        if ph:
                            con.execute("UPDATE images SET hash_phash=? WHERE id=?", (ph, old["id"]))
                            from core.vptree import VPTree
                            VPTree(con).insert(int(old["id"]), ph)
                    except Exception:
                        pass
                skipped += 1
                continue
            # SQLite is the only live metadata store. Sidecar files are read
            # only during the explicit one-time legacy import operation.
            groups = None
            sources = []
            if import_legacy_sidecars:
                groups = read_tag_json(path, settings)
                if not groups:
                    tags = read_tags_txt(path, settings)
                    groups = {"general": tags} if tags else None
                sources = read_sources(path, settings)
            width, height = image_size(path)
            md5 = None
            if compute_md5:
                try:
                    md5 = file_md5(path)
                except Exception:
                    pass
            phash = None
            if bool(settings.get("sqlite_compute_phash_on_index", True)) and path.suffix.lower() not in VIDEO_EXTS:
                try:
                    from core.tagger.engine import file_phash
                    phash = file_phash(path)
                except Exception:
                    phash = None
            con.execute("""
                INSERT INTO images(path, file_name, bucket, size_bytes, width, height, hash_md5, hash_phash, mtime_ns, is_video, indexed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                    file_name=excluded.file_name,
                    bucket=excluded.bucket,
                    size_bytes=excluded.size_bytes,
                    width=excluded.width,
                    height=excluded.height,
                    hash_md5=COALESCE(excluded.hash_md5, images.hash_md5),
                    hash_phash=COALESCE(excluded.hash_phash, images.hash_phash),
                    mtime_ns=excluded.mtime_ns,
                    is_video=excluded.is_video,
                    indexed_at=excluded.indexed_at
            """, (sp, path.name, bucket_for_path(path), size, width, height, md5, phash, mtime_ns, int(path.suffix.lower() in VIDEO_EXTS), now))
            image_id = con.execute("SELECT id FROM images WHERE path=?", (sp,)).fetchone()["id"]
            if phash:
                try:
                    from core.vptree import VPTree
                    VPTree(con).insert(int(image_id), phash)
                except Exception:
                    pass
            if groups is not None:
                upsert_tags(con, image_id, groups)
            if sources:
                upsert_sources(con, image_id, sources)
            # Persistent thumbnail pre-generation.  This writes data/cache/thumbs/*.jpg
            # once during indexing, so gallery opening only loads cached JPEGs.
            if bool(settings.get("thumbs_pregen_on_index", True)):
                try:
                    from core.image_safe import safe_thumbnail_path
                    safe_thumbnail_path(path, int(settings.get("thumb_cache_w", 256)), int(settings.get("thumb_cache_h", 256)))
                    # Also prewarm the common gallery card size used by defaults.
                    safe_thumbnail_path(path, int(settings.get("thumb_cache_card_w", 240)), int(settings.get("thumb_cache_card_h", 220)))
                except Exception:
                    pass
            indexed += 1
            _batch_commit = max(1, int(settings.get("db_batch_commit_size", 100) or 100))
            if indexed % _batch_commit == 0:
                con.commit()
                if progress:
                    progress(indexed, skipped)
        for row in con.execute("SELECT id, path FROM images").fetchall():
            if stop_check and stop_check():
                break
            if not Path(row["path"]).exists():
                con.execute("DELETE FROM images WHERE id=?", (row["id"],))
                removed += 1
        con.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM image_tags)")
        con.execute("DELETE FROM sources WHERE id NOT IN (SELECT DISTINCT source_id FROM image_sources)")
        db_file = str(con.execute("PRAGMA database_list").fetchone()[2])
    return {"scanned": scanned, "indexed": indexed, "skipped": skipped, "removed": removed, "db": db_file}
