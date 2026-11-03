
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
    """Old .tags files' tags onto the card — added: tags the card already has (a site's set, manual tags)
    stay; the one-time import used to replace them all with the file's."""
    from .storage import _has_categories, _tag_written
    categorized = _has_categories(groups)
    for category, tags in (groups or {}).items():
        cat = str(category or "general")
        for tag in tags:
            norm = normalize_tag(tag)
            if not norm:
                continue
            con.execute("INSERT OR IGNORE INTO tags(name, normalized_name, category) VALUES(?,?,?)", (tag, norm, cat))
            row = con.execute("SELECT id, category FROM tags WHERE normalized_name=?", (norm,)).fetchone()
            if row:
                _tag_written(con, int(row["id"]), norm, cat, row["category"], categorized=categorized)
                con.execute("INSERT OR IGNORE INTO image_tags(image_id, tag_id) VALUES(?,?)", (image_id, row["id"]))
    try:
        from .storage import refresh_effective_tag_categories
        refresh_effective_tag_categories(con, int(image_id))
    except Exception:
        pass


def upsert_sources(con, image_id, sources):
    """Old .sources files' links onto the card — added, the card's own sources stay."""
    for s in sources or []:
        host, url = s.get("host",""), s.get("url","")
        if not url:
            continue
        con.execute("INSERT OR IGNORE INTO sources(host, url) VALUES(?,?)", (host, url))
        row = con.execute("SELECT id FROM sources WHERE host=? AND url=?", (host, url)).fetchone()
        if row:
            con.execute("INSERT OR IGNORE INTO image_sources(image_id, source_id) VALUES(?,?)", (image_id, row["id"]))


def _prepared(path: Path, settings, *, compute_md5: bool, phash: bool, sidecars: bool) -> dict:
    """What the index writes for one file — all the reading of the file done here, before the base is
    locked: size, MD5, pHash, old .tags/.sources files."""
    groups, sources = None, []
    if sidecars:
        groups = read_tag_json(path, settings)
        if not groups:
            tags = read_tags_txt(path, settings)
            groups = {"general": tags} if tags else None
        sources = read_sources(path, settings)
    width, height = image_size(path)
    md5 = None
    if compute_md5:
        try:
            from core.file_hash_cache import get_or_compute_md5
            md5 = get_or_compute_md5(settings, path)[0]
        except Exception:
            try:
                md5 = file_md5(path)
            except Exception:
                pass
    hash_phash = None
    if phash and path.suffix.lower() not in VIDEO_EXTS:
        try:
            from core.tagger import file_phash
            hash_phash = file_phash(path)
        except Exception:
            hash_phash = None
    return {"path": path, "width": width, "height": height, "md5": md5, "phash": hash_phash,
            "groups": groups, "sources": sources}


def _write_prepared(con, item: dict, now: int) -> bool:
    """Write one prepared file — only if the base and the file are still as they were when it was read
    (the read happens outside the lock): a card moved or trashed meanwhile, a file gone or changed again,
    a row another writer made for the path meanwhile — all left alone (no card for a file that is gone,
    docs/map D18).  True when written."""
    path = item["path"]
    sp = str(path)
    st = item["stat"]
    try:
        now_st = path.stat()
    except OSError:
        return False
    if (int(now_st.st_mtime_ns), int(now_st.st_size)) != (int(st.st_mtime_ns), int(st.st_size)):
        return False                                     # changed again: the next run takes it
    values = (path.name, bucket_for_path(path), int(st.st_size), item["width"], item["height"], item["md5"],
              item["phash"], int(st.st_mtime_ns), int(path.suffix.lower() in VIDEO_EXTS), now)
    if item.get("old_id"):
        cur = con.execute("""
            UPDATE images SET file_name=?, bucket=?, size_bytes=?, width=?, height=?,
                hash_md5=COALESCE(?, hash_md5), hash_phash=COALESCE(?, hash_phash),
                mtime_ns=?, is_video=?, indexed_at=?
            WHERE id=? AND path=?""", values + (int(item["old_id"]), sp))
        if cur.rowcount != 1:
            return False                                 # the card moved or went meanwhile
        image_id = int(item["old_id"])
    else:
        cur = con.execute("""
            INSERT INTO images(path, file_name, bucket, size_bytes, width, height, hash_md5, hash_phash, mtime_ns, is_video, indexed_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(path) DO NOTHING""", (sp,) + values)
        if cur.rowcount != 1:
            return False                                 # another writer registered it meanwhile: theirs
        image_id = int(cur.lastrowid)
    if item["phash"]:
        try:
            from core.vptree import VPTree
            VPTree(con).insert(int(image_id), item["phash"])
        except Exception:
            pass
    if item["groups"] is not None:
        upsert_tags(con, image_id, item["groups"])
    if item["sources"]:
        upsert_sources(con, image_id, item["sources"])
    return True


def _relative_key(path: str) -> str:
    from core.library_lifecycle import _relative_after_output_branch
    rel = _relative_after_output_branch(str(path))
    return rel.as_posix().lower() if rel else ""


def _cards_elsewhere(settings, roots) -> set:
    """The places inside an output/ branch (relative, lowercase) of cards that point outside the library's
    folders — the old place of a moved archive, its drive still there.  A file here at such a place is
    that card's: the index does not make a second, tagless card of it (``Переписать пути`` moves the
    card to it)."""
    from core.storage_roots import path_is_within_root
    root_list = [str(root) for root in roots]
    keys = set()
    with db(settings, readonly=True) as con:
        for row in con.execute("SELECT path FROM images"):
            path = str(row[0] or "")
            if not path or any(path_is_within_root(path, root) for root in root_list):
                continue
            key = _relative_key(path)
            if key:
                keys.add(key)
    return keys


def index_library(settings, force=False, progress=None, stop_check=None, compute_md5=None, import_legacy_sidecars=False):
    """Rebuild/update the SQLite index of the library's own folders (``scan_roots``).

    Heavy full-file MD5 remains opt-in. UI must call this from a worker, not
    directly while opening pages.

    The files are read (size, MD5, pHash, thumbnails, old .tags files) before the base is locked, and the
    rows are written in short batches (``db_batch_commit_size``): the window, the gallery and imports keep
    writing while a big library is indexed (one transaction over the whole walk froze them for hours).
    Rows of files that are surely gone are deleted only inside the library's own folders: a card that
    points elsewhere — the old place of a moved archive — is not the index's to judge
    (``storage.delete_missing_rows``).
    """
    from .storage import TooManyMissing, delete_missing_rows
    now = int(time.time())
    indexed = skipped = removed = scanned = elsewhere = filled = 0
    missing_refused = 0
    if compute_md5 is None:
        compute_md5 = bool(settings.get("sqlite_compute_md5_on_index", False))
    want_phash = bool(settings.get("sqlite_compute_phash_on_index", True))
    pregen = bool(settings.get("thumbs_pregen_on_index", True))
    batch = max(1, int(settings.get("db_batch_commit_size", 100) or 100))
    roots = scan_roots(settings)
    moved_cards = _cards_elsewhere(settings, roots)
    pending: list[dict] = []          # rows ready to write
    phashes: list[tuple] = []         # (image id, path, pHash) of unchanged rows that had none

    def flush() -> None:
        nonlocal indexed, filled
        if not pending and not phashes:
            return
        with db(settings, write=True) as con:
            for image_id, path, ph in phashes:
                cur = con.execute("UPDATE images SET hash_phash=? WHERE id=? AND path=? AND "
                                  "(hash_phash IS NULL OR hash_phash='')", (ph, image_id, path))
                if cur.rowcount == 1:                    # still that card at that place
                    filled += 1
                    try:
                        from core.vptree import VPTree
                        VPTree(con).insert(int(image_id), ph)
                    except Exception:
                        pass
            for item in pending:
                if _write_prepared(con, item, now):
                    indexed += 1
        pending.clear()
        phashes.clear()
        if progress:
            progress(indexed, skipped)

    with db(settings, readonly=True) as reader:
        for path in iter_media_files(roots, stop_check=stop_check):
            scanned += 1
            try:
                st = path.stat()
            except Exception:
                continue
            old = reader.execute("SELECT id, mtime_ns, size_bytes, hash_phash FROM images WHERE path=?", (str(path),)).fetchone()
            if old and not force and int(old["mtime_ns"]) == int(st.st_mtime_ns) and int(old["size_bytes"]) == int(st.st_size):
                # Fast path: file unchanged. If phash was missing from an older
                # index, fill it opportunistically without rebuilding metadata.
                if want_phash and not old["hash_phash"] and path.suffix.lower() not in VIDEO_EXTS:
                    try:
                        from core.tagger import file_phash
                        ph = file_phash(path)
                        if ph:
                            phashes.append((int(old["id"]), str(path), ph))
                    except Exception:
                        pass
                skipped += 1
                if len(phashes) >= batch:
                    flush()
                continue
            if not old and moved_cards and _relative_key(str(path)) in moved_cards:
                elsewhere += 1                           # the file of a card of the archive's old place
                continue
            item = _prepared(path, settings, compute_md5=compute_md5, phash=want_phash, sidecars=import_legacy_sidecars)
            item["stat"] = st
            item["old_id"] = int(old["id"]) if old else 0
            # Persistent thumbnail pre-generation.  This writes data/cache/thumbs/*.jpg
            # once during indexing, so gallery opening only loads cached JPEGs.
            if pregen:
                try:
                    from core.image_safe import safe_thumbnail_path
                    safe_thumbnail_path(path, int(settings.get("thumb_cache_w", 256)), int(settings.get("thumb_cache_h", 256)))
                    # Also prewarm the common gallery card size used by defaults.
                    safe_thumbnail_path(path, int(settings.get("thumb_cache_card_w", 240)), int(settings.get("thumb_cache_card_h", 220)))
                except Exception:
                    pass
            pending.append(item)
            if len(pending) >= batch:
                flush()
    flush()
    if not (stop_check and stop_check()):
        try:
            removed = delete_missing_rows(settings, within=roots, stop_check=stop_check)
        except TooManyMissing as exc:        # most of the library «gone»: an emptied or replaced folder, not losses
            missing_refused = exc.missing
    if removed or import_legacy_sidecars:    # nothing was deleted or retagged: nothing to tidy (a big library: minutes)
        with db(settings, write=True) as con:
            from .repository import cleanup_orphans
            cleanup_orphans(con)
            try:
                from .storage import refresh_effective_tag_categories
                refresh_effective_tag_categories(con, None)
            except Exception:
                pass
    with db(settings, readonly=True) as con:
        db_file = str(con.execute("PRAGMA database_list").fetchone()[2])
    return {"scanned": scanned, "indexed": indexed, "skipped": skipped, "removed": removed, "db": db_file,
            "elsewhere": elsewhere, "phash_filled": filled, "missing_refused": missing_refused}
