"""Library lifecycle helpers: Inbox, Trash, backups, metadata export and cache upkeep.

This keeps destructive file operations out of UI code. A file moved to trash
retains its database metadata so it can be restored. Only permanent deletion
removes related rows and cached thumbnails.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Iterable

from core.database.connection import db, db_path
from core.paths import CACHE_DIR, LOGS_DIR, result_output_base, DATA_DIR
from core.media_utils import file_md5, host_from_url
from core.source_protection import require_managed_media_mutation, is_managed_media_path
from core.services.media_storage_service import move_managed, unlink_managed


def _now() -> int:
    return int(time.time())


def _trash_media_dir(settings: dict) -> Path:
    root = result_output_base(settings) / "trash" / "media"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _unique_target(folder: Path, source: Path, image_id: int | None = None) -> Path:
    name = source.name
    target = folder / name
    if not target.exists():
        return target
    stem = source.stem
    suffix = source.suffix
    marker = f"_{image_id}" if image_id else "_deleted"
    target = folder / f"{stem}{marker}{suffix}"
    n = 1
    while target.exists():
        target = folder / f"{stem}{marker}_{n}{suffix}"
        n += 1
    return target


def force_backup_database(settings: dict, reason: str = "operation") -> str:
    """Create an immediate, independent SQLite backup before destructive work."""
    source = db_path(settings)
    if not source.exists():
        return ""
    folder = Path(DATA_DIR) / "db_backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(reason))[:40]
    target = folder / f"local_booru_{stamp}_{safe_reason or 'backup'}.sqlite3"
    try:
        try:
            from core.preflight import ensure_space_for_write
            _ok, _msg = ensure_space_for_write(settings, target, int(source.stat().st_size if source.exists() else 0))
            if not _ok:
                return ""
        except Exception:
            pass
        src = sqlite3.connect(str(source), timeout=60)
        dst = sqlite3.connect(str(target), timeout=60)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return str(target)
    except Exception:
        # Fallback is still better than silently continuing without any copy.
        try:
            shutil.copy2(source, target)
            return str(target)
        except Exception:
            return ""


def import_lifecycle_kwargs(settings: dict, origin: str) -> dict:
    add_inbox = bool(settings.get("imports_to_inbox", True))
    hours = max(1, int(settings.get("inbox_auto_archive_hours", 24) or 24))
    return {
        "lifecycle": "inbox" if add_inbox else "archive",
        "inbox_until": (_now() + hours * 3600) if add_inbox else 0,
        "import_origin": str(origin or ""),
    }


def mark_new_or_archive(settings: dict, image_id: int, *, origin: str = "") -> None:
    add_inbox = bool(settings.get("imports_to_inbox", True))
    hours = max(1, int(settings.get("inbox_auto_archive_hours", 24) or 24))
    lifecycle = "inbox" if add_inbox else "archive"
    until = _now() + hours * 3600 if add_inbox else 0
    with db(settings, write=True) as con:
        con.execute(
            "UPDATE images SET lifecycle=?, inbox_until=?, import_origin=CASE WHEN ?<>'' THEN ? ELSE import_origin END WHERE id=? AND deleted=0",
            (lifecycle, until, str(origin or ""), str(origin or ""), int(image_id)),
        )


def archive_expired_inbox(settings: dict) -> int:
    now = _now()
    with db(settings, write=True) as con:
        cur = con.execute(
            "UPDATE images SET lifecycle='archive', inbox_until=0 WHERE deleted=0 AND lifecycle='inbox' AND inbox_until>0 AND inbox_until<=?",
            (now,),
        )
        return int(cur.rowcount or 0)


def set_archived(settings: dict, image_ids: Iterable[int]) -> int:
    ids = [int(x) for x in image_ids if x is not None]
    if not ids:
        return 0
    ph = ",".join("?" for _ in ids)
    with db(settings, write=True) as con:
        cur = con.execute(f"UPDATE images SET lifecycle='archive', inbox_until=0 WHERE id IN ({ph}) AND deleted=0", ids)
        return int(cur.rowcount or 0)


def move_to_trash(settings: dict, image_rows: Iterable[dict], *, reason: str = "delete", tag_or_source: str = "", make_backup: bool = True) -> dict:
    rows = [dict(r) for r in (image_rows or [])]
    if not rows:
        return {"trashed_files": 0, "errors": 0, "trashed_records": 0, "backup": ""}
    backup = force_backup_database(settings, reason) if make_backup else ""
    if make_backup and db_path(settings).exists() and not backup:
        return {"trashed_files": 0, "deleted_files": 0, "errors": len(rows), "trashed_records": 0, "deleted_records": 0, "backup": "", "error": "Не удалось создать резервную копию базы. Удаление отменено."}
    trash = _trash_media_dir(settings)
    moved = errors = records = protected_source_skipped = 0
    now = _now()
    with db(settings, write=True) as con:
        for row in rows:
            try:
                image_id = int(row.get("id") or 0)
                old = Path(str(row.get("path") or ""))
                if not image_id:
                    continue
                new_path = old
                if not require_managed_media_mutation(settings, old, "move_to_trash"):
                    protected_source_skipped += 1
                    continue
                if old.exists() and old.is_file():
                    new_path = _unique_target(trash, old, image_id)
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    if move_managed(settings, old, new_path, operation="move_to_trash"):
                        moved += 1
                    else:
                        protected_source_skipped += 1
                        continue
                con.execute(
                    """UPDATE images SET path=?, file_name=?, deleted=1, lifecycle='trash',
                       original_media_path=CASE WHEN original_media_path='' THEN ? ELSE original_media_path END,
                       trashed_at=?, inbox_until=0 WHERE id=?""",
                    (str(new_path), new_path.name, str(old), now, image_id),
                )
                con.execute(
                    "INSERT INTO delete_log(path,file_name,reason,tag_or_source,deleted_at) VALUES(?,?,?,?,?)",
                    (str(old), old.name, reason, tag_or_source, now),
                )
                records += 1
            except Exception:
                errors += 1
    return {"trashed_files": moved, "deleted_files": moved, "errors": errors, "trashed_records": records, "deleted_records": records, "protected_source_skipped": protected_source_skipped, "backup": backup}


def trash_rows(settings: dict) -> list[dict]:
    """Return trash entries together with the last recorded reason.

    A visible reason is critical because some download/dedup workflows may put
    recoverable files into Trash automatically.  Previously the UI showed only
    a count, leaving the user unable to distinguish manual deletion from an
    automatic duplicate cleanup.
    """
    with db(settings, readonly=True) as con:
        rows = con.execute(
            """
            SELECT i.id,i.path,i.file_name,i.bucket,i.size_bytes,i.width,i.height,i.hash_md5,i.is_video,
                   i.original_media_path,i.trashed_at,
                   COALESCE((
                       SELECT d.reason FROM delete_log d
                       WHERE d.path = CASE WHEN COALESCE(i.original_media_path,'')<>'' THEN i.original_media_path ELSE i.path END
                       ORDER BY d.deleted_at DESC, d.id DESC LIMIT 1
                   ), 'unknown') AS delete_reason,
                   COALESCE((
                       SELECT d.tag_or_source FROM delete_log d
                       WHERE d.path = CASE WHEN COALESCE(i.original_media_path,'')<>'' THEN i.original_media_path ELSE i.path END
                       ORDER BY d.deleted_at DESC, d.id DESC LIMIT 1
                   ), '') AS delete_target
            FROM images i
            WHERE i.deleted=1 AND i.lifecycle='trash'
            ORDER BY i.trashed_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def _merge_image_metadata(con, keep_id: int, source_id: int) -> None:
    """Preserve all discovered metadata before hiding an exact byte duplicate."""
    if int(keep_id) == int(source_id):
        return
    con.execute("INSERT OR IGNORE INTO image_tags(image_id,tag_id) SELECT ?,tag_id FROM image_tags WHERE image_id=?", (int(keep_id), int(source_id)))
    con.execute("INSERT OR IGNORE INTO image_sources(image_id,source_id) SELECT ?,source_id FROM image_sources WHERE image_id=?", (int(keep_id), int(source_id)))
    score = con.execute("SELECT rating,favorite FROM images WHERE id=?", (int(source_id),)).fetchone()
    if score:
        con.execute("UPDATE images SET rating=MAX(COALESCE(rating,0),?), favorite=MAX(COALESCE(favorite,0),?) WHERE id=?", (int(score["rating"] or 0), int(score["favorite"] or 0), int(keep_id)))


def _live_row_with_md5(con, md5: str, *, excluding_id: int = 0):
    value = str(md5 or "").strip().lower()
    if not value:
        return None
    rows = con.execute(
        "SELECT * FROM images WHERE deleted=0 AND lower(COALESCE(hash_md5,''))=? AND id<>? ORDER BY indexed_at ASC, id ASC",
        (value, int(excluding_id or 0)),
    ).fetchall()
    for item in rows:
        try:
            if Path(str(item["path"])).exists():
                return item
        except Exception:
            continue
    return None


def restore_from_trash(settings: dict, image_ids: Iterable[int]) -> dict:
    """Restore recoverable files without creating a second live MD5-identical copy."""
    ids = [int(x) for x in image_ids if x is not None]
    restored = errors = skipped_existing = merged_existing = unblocked_md5 = protected_source_skipped = 0
    unblock_after_commit = set()
    try:
        from core.deleted_registry import forget_deleted_md5
    except Exception:
        forget_deleted_md5 = lambda _md5, **_kw: 0
    with db(settings, write=True) as con:
        for image_id in ids:
            row = con.execute("SELECT * FROM images WHERE id=? AND lifecycle='trash'", (image_id,)).fetchone()
            if not row:
                continue
            try:
                cur = Path(row["path"])
                md5 = str(row["hash_md5"] or "").strip().lower()
                if not md5 and cur.exists() and cur.is_file():
                    md5 = file_md5(cur).lower()
                    con.execute("UPDATE images SET hash_md5=? WHERE id=?", (md5, image_id))
                already_live = _live_row_with_md5(con, md5, excluding_id=image_id)
                if already_live is not None:
                    _merge_image_metadata(con, int(already_live["id"]), image_id)
                    skipped_existing += 1
                    merged_existing += 1
                    if md5:
                        unblock_after_commit.add(md5)
                    continue
                desired = Path(row["original_media_path"] or row["path"])
                # The source archive is an immutable rebuild seed. Old rows may
                # remember a source path; restoring Trash must never move bytes
                # back into or overwrite that archive.
                if not is_managed_media_path(settings, desired):
                    bucket = str(row["bucket"] or "found") if "bucket" in row.keys() else "found"
                    safe_bucket = "partial_match" if "partial" in bucket else ("no_match" if "no_match" in bucket else "found")
                    desired = result_output_base(settings) / safe_bucket / "media" / Path(row["file_name"] or cur.name).name
                desired.parent.mkdir(parents=True, exist_ok=True)
                dest = _unique_target(desired.parent, desired, image_id) if desired.exists() else desired
                if cur.exists():
                    if not require_managed_media_mutation(settings, cur, "restore_from_trash_source"):
                        protected_source_skipped += 1
                        continue
                    if not move_managed(settings, cur, dest, operation="restore_from_trash"):
                        protected_source_skipped += 1
                        continue
                elif not dest.exists():
                    errors += 1
                    continue
                con.execute(
                    "UPDATE images SET path=?,file_name=?,deleted=0,lifecycle='archive',original_media_path='',trashed_at=0 WHERE id=?",
                    (str(dest), dest.name, image_id),
                )
                if md5:
                    unblock_after_commit.add(md5)
                restored += 1
            except Exception:
                errors += 1
    return {"restored": restored, "skipped_existing": skipped_existing, "merged_existing": merged_existing, "unblocked_md5": unblocked_md5, "protected_source_skipped": protected_source_skipped, "errors": errors}


def cleanup_live_exact_duplicates(settings: dict, *, make_backup: bool = True, progress=None, cancel_check=None) -> dict:
    """Normalize the live library to one physical row per exact MD5.

    All tags, all source links, favourite/rating state are merged into the
    canonical row before redundant files are moved to Trash. Older database
    rows may not yet carry ``hash_md5``; this explicit repair operation computes
    their hashes first, with optional UI progress/cancellation. It never uses
    pHash and never removes the final physical copy of any content.
    """
    backup = force_backup_database(settings, "normalize_exact_md5_library") if make_backup else ""
    if make_backup and db_path(settings).exists() and not backup:
        return {"groups": 0, "trashed_files": 0, "trashed_records": 0, "errors": 1, "backup": "", "error": "Не удалось создать резервную копию базы. Склейка отменена."}

    # Old builds registered parser output before copying bytes and therefore
    # left some live rows without a hash. Compute it only for this one-time
    # repair; new imports now store MD5 immediately.
    with db(settings, readonly=True) as con:
        missing = [dict(r) for r in con.execute(
            "SELECT id,path FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')='' ORDER BY id"
        ).fetchall()]
    hashed_missing = hash_errors = 0
    total_missing = len(missing)
    for pos, row in enumerate(missing, start=1):
        if cancel_check is not None and cancel_check():
            return {"groups": 0, "trashed_files": 0, "trashed_records": 0, "merged_existing": 0, "hashed_missing": hashed_missing, "errors": hash_errors, "cancelled": True, "backup": backup}
        try:
            path = Path(str(row.get("path") or ""))
            if path.exists() and path.is_file():
                value = file_md5(path).lower()
                with db(settings, write=True) as con:
                    con.execute("UPDATE images SET hash_md5=? WHERE id=?", (value, int(row["id"])))
                hashed_missing += 1
        except Exception:
            hash_errors += 1
        if progress is not None:
            try:
                progress("hash", pos, total_missing)
            except Exception:
                pass

    with db(settings, readonly=True) as con:
        rows = [dict(r) for r in con.execute(
            """SELECT * FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>''
               AND lower(hash_md5) IN (SELECT lower(hash_md5) FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>'' GROUP BY lower(hash_md5) HAVING COUNT(*)>1)
               ORDER BY lower(hash_md5), indexed_at ASC, id ASC"""
        ).fetchall()]
    by_md5 = {}
    for row in rows:
        try:
            if not Path(str(row.get("path") or "")).exists():
                continue
        except Exception:
            continue
        by_md5.setdefault(str(row.get("hash_md5") or "").lower(), []).append(row)
    groups = 0
    extras = []
    merge_pairs = []
    duplicate_md5s = []
    for _md5, items in by_md5.items():
        if len(items) < 2:
            continue
        def keep_key(item):
            path = Path(str(item.get("path") or ""))
            restored_suffix = path.stem.endswith("_" + str(int(item.get("id") or 0)))
            bucket = str(item.get("bucket") or "")
            bucket_rank = 0 if bucket in ("found", "downloaded_found") else (1 if "partial" in bucket else 2)
            return (bucket_rank, 1 if restored_suffix else 0, -int(item.get("favorite") or 0), int(item.get("indexed_at") or 0), int(item.get("id") or 0))
        ordered = sorted(items, key=keep_key)
        keep = ordered[0]
        duplicate_items = ordered[1:]
        groups += 1
        duplicate_md5s.append(_md5)
        for extra in duplicate_items:
            extras.append({"id": int(extra["id"]), "path": str(extra["path"]), "file_name": str(extra["file_name"]), "bucket": str(extra.get("bucket") or ""), "size_bytes": int(extra.get("size_bytes") or 0)})
            merge_pairs.append((int(keep["id"]), int(extra["id"])))
    if not extras:
        return {"groups": 0, "trashed_files": 0, "trashed_records": 0, "merged_existing": 0, "unblocked_md5": 0, "hashed_missing": hashed_missing, "errors": hash_errors, "backup": backup}
    with db(settings, write=True) as con:
        for keep_id, source_id in merge_pairs:
            _merge_image_metadata(con, keep_id, source_id)
    result = move_to_trash(
        settings, extras, reason="exact_md5_auto_normalized",
        tag_or_source="точный MD5; источники и теги склеены в одну запись", make_backup=False,
    )
    unblocked_md5 = 0
    try:
        from core.deleted_registry import forget_deleted_md5
        for value in set(duplicate_md5s):
            unblocked_md5 += int(forget_deleted_md5(value, settings=settings) or 0)
    except Exception:
        pass
    result.update({"groups": groups, "merged_existing": len(merge_pairs), "unblocked_md5": unblocked_md5, "hashed_missing": hashed_missing, "errors": int(result.get("errors", 0)) + hash_errors, "backup": backup})
    return result


def _delete_thumb_files_for_path(path: str) -> int:
    """Delete only thumbnails mapped to one purged media path.

    New thumbnails carry small ``.src`` marker files. Old unmapped cache entries
    are harmless and will disappear during normal shutdown trimming.
    """
    root = Path(CACHE_DIR) / "thumbs"
    try:
        wanted = str(Path(path).resolve())
    except Exception:
        wanted = str(path)
    removed = 0
    if not root.exists():
        return 0
    for marker in root.glob("*.jpg.src"):
        try:
            if marker.read_text(encoding="utf-8", errors="ignore").strip() != wanted:
                continue
            image = Path(str(marker)[:-4])
            if image.exists():
                image.unlink(missing_ok=True); removed += 1
            marker.unlink(missing_ok=True)
        except Exception:
            pass
    try:
        from core.thumb_service import ThumbnailService
        if ThumbnailService._instance is not None:
            ThumbnailService.instance().clear_memory_cache()
    except Exception:
        pass
    return removed


def trash_media_paths(settings: dict, paths: Iterable[str | Path], *, reason: str = "delete", make_backup: bool = True) -> dict:
    """Move arbitrary real media paths to Trash, indexing orphan files first.

    Used by old downloader/subscription cleanup code so it cannot bypass the
    recoverable Trash lifecycle merely because a file had not reached SQLite.
    """
    from core.media_utils import is_media, file_md5
    from core.database.storage import ensure_image
    clean_paths = []
    protected_source_skipped = 0
    for value in paths or []:
        p = Path(value)
        if p.exists() and p.is_file() and is_media(p):
            if require_managed_media_mutation(settings, p, "trash_media_paths"):
                clean_paths.append(p)
            else:
                protected_source_skipped += 1
    if not clean_paths:
        return {"trashed_files": 0, "deleted_files": 0, "errors": 0, "trashed_records": 0, "deleted_records": 0, "protected_source_skipped": protected_source_skipped, "backup": ""}
    rows = []
    with db(settings, readonly=True) as con:
        for p in clean_paths:
            row = con.execute("SELECT id,path,file_name,bucket,size_bytes FROM images WHERE path=? AND deleted=0", (str(p),)).fetchone()
            if row:
                rows.append(dict(row))
    known = {str(r["path"]) for r in rows}
    for p in clean_paths:
        if str(p) in known:
            continue
        try:
            md5 = file_md5(p)
        except Exception:
            md5 = None
        try:
            image_id = ensure_image(settings, p, status=reason, hash_md5=md5, lifecycle="archive", import_origin="legacy_cleanup")
            rows.append({"id": image_id, "path": str(p), "file_name": p.name, "bucket": "", "size_bytes": int(p.stat().st_size)})
        except Exception:
            pass
    result = move_to_trash(settings, rows, reason=reason, make_backup=make_backup)
    result["protected_source_skipped"] = int(result.get("protected_source_skipped", 0) or 0) + protected_source_skipped
    return result


def purge_trash(settings: dict, image_ids: Iterable[int] | None = None, *, make_backup: bool = True) -> dict:
    ids = [int(x) for x in (image_ids or []) if x is not None]
    backup = force_backup_database(settings, "empty_trash") if make_backup else ""
    if make_backup and db_path(settings).exists() and not backup:
        return {"removed_files": 0, "removed_records": 0, "errors": 1, "backup": "", "error": "Не удалось создать резервную копию базы. Очистка корзины отменена."}
    reason_sql = """COALESCE((SELECT d.reason FROM delete_log d WHERE d.path=CASE WHEN COALESCE(i.original_media_path,'')<>'' THEN i.original_media_path ELSE i.path END ORDER BY d.deleted_at DESC,d.id DESC LIMIT 1), 'unknown') AS last_delete_reason"""
    with db(settings, readonly=True) as con:
        if ids:
            ph = ",".join("?" for _ in ids)
            rows = con.execute(f"SELECT i.*, {reason_sql} FROM images i WHERE i.id IN ({ph}) AND i.lifecycle='trash'", ids).fetchall()
        else:
            rows = con.execute(f"SELECT i.*, {reason_sql} FROM images i WHERE i.lifecycle='trash' AND i.deleted=1").fetchall()
    removed = errors = protected_source_skipped = 0
    hard_ids = []
    from core.deleted_registry import mark_deleted
    reimport_blocking_reasons = {"delete", "gallery_context_delete", "post_context_delete", "delete_by_tag", "delete_by_source"}
    for row in rows:
        p = Path(row["path"])
        if not require_managed_media_mutation(settings, p, "purge_trash"):
            protected_source_skipped += 1
            continue
        try:
            try:
                last_reason = str(row["last_delete_reason"] or "") if "last_delete_reason" in row.keys() else ""
                # Only an intentional content removal blocks a future import.
                # Automatic/duplicate cleanup must not poison the MD5 registry.
                if last_reason in reimport_blocking_reasons:
                    mark_deleted(p, reason=last_reason, md5=str(row["hash_md5"] or ""), size=int(row["size_bytes"] or 0), settings=settings, manual_delete=True)
            except Exception:
                pass
            if p.exists() and p.is_file():
                if unlink_managed(settings, p, operation="purge_trash"):
                    removed += 1
                else:
                    protected_source_skipped += 1
                    continue
            _delete_thumb_files_for_path(str(p))
            old_media = str(row["original_media_path"] or "") if "original_media_path" in row.keys() else ""
            if old_media:
                _delete_thumb_files_for_path(old_media)
            hard_ids.append(int(row["id"]))
        except Exception:
            errors += 1
    if hard_ids:
        ph = ",".join("?" for _ in hard_ids)
        with db(settings, write=True) as con:
            con.execute(f"DELETE FROM images WHERE id IN ({ph})", hard_ids)
            con.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM image_tags)")
            con.execute("DELETE FROM sources WHERE id NOT IN (SELECT DISTINCT source_id FROM image_sources)")
    return {"removed_files": removed, "removed_records": len(hard_ids), "protected_source_skipped": protected_source_skipped, "errors": errors, "backup": backup}


def purge_expired_trash(settings: dict) -> dict:
    """Permanently remove trashed files only when the user enabled timed cleanup."""
    days = max(0, int(settings.get("trash_auto_purge_days", 0) or 0))
    if days <= 0:
        return {"removed_files": 0, "removed_records": 0, "errors": 0, "backup": ""}
    cutoff = _now() - days * 86400
    with db(settings, readonly=True) as con:
        ids = [int(r["id"]) for r in con.execute(
            "SELECT id FROM images WHERE lifecycle='trash' AND deleted=1 AND trashed_at>0 AND trashed_at<=?",
            (cutoff,),
        ).fetchall()]
    if not ids:
        return {"removed_files": 0, "removed_records": 0, "errors": 0, "backup": ""}
    return purge_trash(settings, ids, make_backup=True)


def update_url_history(settings: dict, url: str, *, status: str, image_id: int = 0, error: str = "") -> None:
    url = str(url or "").strip()
    if not url:
        return
    now = _now()
    with db(settings, write=True) as con:
        con.execute(
            """INSERT INTO url_history(url,host,image_id,status,last_error,first_seen,last_seen)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(url) DO UPDATE SET image_id=CASE WHEN excluded.image_id>0 THEN excluded.image_id ELSE url_history.image_id END,
                 status=excluded.status,last_error=excluded.last_error,last_seen=excluded.last_seen""",
            (url, host_from_url(url), int(image_id or 0), str(status or ""), str(error or "")[:500], now, now),
        )


def export_metadata(settings: dict, destination: str | Path, fmt: str = "json", image_ids: Iterable[int] | None = None) -> int:
    destination = Path(destination)
    ids = [int(x) for x in (image_ids or []) if x is not None]
    with db(settings, readonly=True) as con:
        if ids:
            ph = ",".join("?" for _ in ids)
            images = con.execute(f"SELECT * FROM images WHERE deleted=0 AND id IN ({ph}) ORDER BY path COLLATE NOCASE", ids).fetchall()
        else:
            images = con.execute("SELECT * FROM images WHERE deleted=0 ORDER BY path COLLATE NOCASE").fetchall()
        out = []
        for row in images:
            image_id = int(row["id"])
            tags = [r["name"] for r in con.execute("SELECT t.name FROM tags t JOIN image_tags it ON it.tag_id=t.id WHERE it.image_id=? ORDER BY t.name", (image_id,)).fetchall()]
            sources = [r["url"] for r in con.execute("SELECT s.url FROM sources s JOIN image_sources x ON x.source_id=s.id WHERE x.image_id=? ORDER BY s.url", (image_id,)).fetchall()]
            out.append({"path": row["path"], "md5": row["hash_md5"] or "", "tags": tags, "sources": sources, "rating": int(row["rating"] or 0), "favorite": bool(row["favorite"]), "status": row["lifecycle"]})
    destination.parent.mkdir(parents=True, exist_ok=True)
    if str(fmt).lower() == "csv":
        import csv
        with destination.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["path", "md5", "rating", "favorite", "status", "tags", "sources"])
            for item in out:
                w.writerow([item["path"], item["md5"], item["rating"], int(item["favorite"]), item["status"], " ".join(item["tags"]), "\n".join(item["sources"])])
    else:
        destination.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(out)


def folder_size(path: str | Path) -> int:
    root = Path(path)
    total = 0
    if not root.exists():
        return 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except Exception:
            pass
    return total


def trim_thumbnail_cache(settings: dict) -> dict:
    """Keep only recently touched thumbnails when automatic shutdown cleanup is enabled."""
    root = Path(CACHE_DIR) / "thumbs"
    if not root.exists():
        return {"before": 0, "after": 0, "removed": 0}
    before = folder_size(root)
    if not bool(settings.get("thumb_cleanup_on_exit", True)):
        return {"before": before, "after": before, "removed": 0}
    keep = max(50, int(settings.get("thumb_keep_recent", 500) or 500))
    files = []
    for p in root.glob("*.jpg"):
        try:
            files.append((p.stat().st_mtime_ns, p))
        except Exception:
            pass
    files.sort(reverse=True)
    removed = 0
    for _, p in files[keep:]:
        try:
            p.unlink()
            p.with_suffix(p.suffix + ".src").unlink(missing_ok=True)
            removed += 1
        except Exception:
            pass
    for marker in root.glob("*.jpg.src"):
        image = Path(str(marker)[:-4])
        if not image.exists():
            try: marker.unlink(missing_ok=True)
            except Exception: pass
    return {"before": before, "after": folder_size(root), "removed": removed}


def library_stats(settings: dict) -> dict:
    with db(settings, readonly=True) as con:
        q = lambda sql, args=(): int(con.execute(sql, args).fetchone()[0] or 0)
        stats = {
            "files": q("SELECT COUNT(*) FROM images WHERE deleted=0"),
            "images": q("SELECT COUNT(*) FROM images WHERE deleted=0 AND is_video=0"),
            "videos": q("SELECT COUNT(*) FROM images WHERE deleted=0 AND is_video=1"),
            "inbox": q("SELECT COUNT(*) FROM images WHERE deleted=0 AND lifecycle='inbox'"),
            "trash": q("SELECT COUNT(*) FROM images WHERE deleted=1 AND lifecycle='trash'"),
            "tagged": q("SELECT COUNT(DISTINCT image_id) FROM image_tags"),
            "sourced": q("SELECT COUNT(DISTINCT image_id) FROM image_sources"),
            "bytes": q("SELECT COALESCE(SUM(size_bytes),0) FROM images WHERE deleted=0"),
        }
    stats["cache_bytes"] = folder_size(Path(CACHE_DIR) / "thumbs")
    try:
        stats["db_bytes"] = db_path(settings).stat().st_size
    except Exception:
        stats["db_bytes"] = 0
    return stats


def relocate_missing_library_paths(settings: dict, new_output: str | Path, *, apply: bool = False) -> dict:
    """Locate missing indexed files under a newly selected output root.

    The function only rewrites paths when the same relative position under the
    output tree exists at the new location; it never guesses by filename alone.
    """
    from core.paths import ensure_output_base
    old_base = result_output_base(settings)
    new_base = ensure_output_base(new_output, settings.get("root"))
    matches: list[tuple[int, str, str]] = []
    with db(settings, readonly=True) as con:
        rows = con.execute("SELECT id,path FROM images WHERE deleted=0 ORDER BY id").fetchall()
    for row in rows:
        old = Path(str(row["path"] or ""))
        if old.exists():
            continue
        rel = None
        try:
            rel = old.relative_to(old_base)
        except Exception:
            parts = list(old.parts)
            try:
                idx = [p.lower() for p in parts].index("output")
                rel = Path(*parts[idx + 1:])
            except Exception:
                rel = None
        if rel is None:
            continue
        candidate = new_base / rel
        if candidate.exists() and candidate.is_file():
            matches.append((int(row["id"]), str(old), str(candidate)))
    if apply and matches:
        with db(settings, write=True) as con:
            for image_id, old, new in matches:
                con.execute("UPDATE images SET path=?, file_name=? WHERE id=?", (new, Path(new).name, image_id))
                con.execute("UPDATE processed_files SET media_path=? WHERE media_path=?", (new, old))
    return {"old_base": str(old_base), "new_base": str(new_base), "found": len(matches), "updated": len(matches) if apply else 0, "matches": matches[:50]}
