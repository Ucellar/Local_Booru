from __future__ import annotations

from pathlib import Path
import time
from typing import Callable

from core.architecture import INDEX_BATCH_COMMIT
from core.media_utils import iter_media_files, scan_roots, safe_stat, image_size, is_video, bucket_for_path, has_copy_suffix, file_md5
from core.database.connection import db
from core.database.storage import upsert_media_metadata


def rebuild_index(settings: dict, force: bool = False, with_md5: bool = False, progress: Callable[[str], None] | None = None, stop_check=None):
    """Rebuild SQLite image index in a streaming way.

    This is safe to run from a background worker. It intentionally does not read
    old sidecar tags as the main storage. It indexes files and keeps existing DB
    tags when path already exists.
    """
    roots = scan_roots(settings)
    scanned = indexed = skipped = removed = 0
    now = int(time.time())
    existing_paths = set()

    def log(msg):
        if progress:
            try: progress(msg)
            except Exception: pass

    log("scan roots: " + ", ".join(str(x) for x in roots))

    with db(settings, write=True) as con:
        existing_paths = {r["path"] for r in con.execute("SELECT path FROM images WHERE deleted=0").fetchall()}

    seen_paths = set()
    batch = []
    for p in iter_media_files(roots, stop_check=stop_check):
        if stop_check and stop_check():
            log("stopped")
            break
        scanned += 1
        p = Path(p)
        if settings.get("skip_copy_suffix_files", True) and has_copy_suffix(p):
            skipped += 1
            continue
        seen_paths.add(str(p))
        size, mtime_ns = safe_stat(p)
        batch.append((p, size, mtime_ns))
        if len(batch) >= INDEX_BATCH_COMMIT:
            indexed += _flush_batch(settings, batch, force=force, with_md5=with_md5, now=now)
            log(f"indexed={indexed} scanned={scanned} skipped={skipped}")
            batch.clear()
    if batch:
        indexed += _flush_batch(settings, batch, force=force, with_md5=with_md5, now=now)

    # Remove records for missing files, but only inside scanned roots.
    missing = existing_paths - seen_paths
    if missing:
        with db(settings, write=True) as con:
            for path in missing:
                if _path_under_any(path, roots):
                    con.execute("DELETE FROM images WHERE path=?", (path,))
                    con.execute("DELETE FROM processed_files WHERE media_path=?", (path,))
                    removed += 1
            try:
                from core.database.repository import cleanup_orphans
                cleanup_orphans(con)
            except Exception:
                pass

    log(f"done indexed={indexed} scanned={scanned} skipped={skipped} removed={removed}")
    return {"scanned": scanned, "indexed": indexed, "skipped": skipped, "removed": removed}


def _flush_batch(settings, batch, force=False, with_md5=False, now=0):
    count = 0
    with db(settings, write=True) as con:
        for p, size, mtime_ns in batch:
            p = Path(p)
            row = con.execute("SELECT id, size_bytes, mtime_ns FROM images WHERE path=?", (str(p),)).fetchone()
            if row and not force and int(row["size_bytes"] or 0) == size and int(row["mtime_ns"] or 0) == mtime_ns:
                continue
            width, height = image_size(p)
            md5 = None
            if with_md5:
                try: md5 = file_md5(p)
                except Exception: md5 = None
            con.execute("""
                INSERT INTO images(path,file_name,bucket,size_bytes,width,height,hash_md5,mtime_ns,is_video,indexed_at,deleted)
                VALUES(?,?,?,?,?,?,?,?,?,?,0)
                ON CONFLICT(path) DO UPDATE SET
                    file_name=excluded.file_name,
                    bucket=excluded.bucket,
                    size_bytes=excluded.size_bytes,
                    width=excluded.width,
                    height=excluded.height,
                    hash_md5=COALESCE(excluded.hash_md5, images.hash_md5),
                    mtime_ns=excluded.mtime_ns,
                    is_video=excluded.is_video,
                    indexed_at=excluded.indexed_at,
                    deleted=0
            """, (str(p), p.name, bucket_for_path(p), size, width, height, md5, mtime_ns, int(is_video(p)), now or int(time.time())))
            count += 1
    return count


def _path_under_any(path, roots):
    p = Path(path)
    for r in roots:
        try:
            p.relative_to(Path(r))
            return True
        except Exception:
            pass
    return False
