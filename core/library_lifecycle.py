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
from core.paths import CACHE_DIR, LOGS_DIR, BACKUPS_DIR, result_output_base, DATA_DIR
from core.media_utils import file_md5, host_from_url
from core.source_protection import require_managed_media_mutation, is_managed_media_path
from core.services.media_storage_service import move_managed, unlink_managed, content_addressed_path, normalize_managed_content_name


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
    folder = Path(BACKUPS_DIR) / "db"
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
    con.execute("""
        INSERT OR REPLACE INTO image_source_tags(image_id,source_id,tag_id,category,acquisition,updated_at)
        SELECT ?,source_id,tag_id,category,acquisition,updated_at FROM image_source_tags WHERE image_id=?
    """, (int(keep_id), int(source_id)))
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
                if md5:
                    desired = content_addressed_path(desired, md5, original_name=Path(row["file_name"] or cur.name).name)
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


def _relative_after_output_branch(value: str) -> Path | None:
    """Return path relative to an output/ branch from a stored absolute path.

    SQLite stores Windows absolute paths.  This helper is intentionally string
    based so it also works when diagnostics/tests run on a non-Windows machine.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("/", "\\")
    marker = "\\output\\"
    lower = normalized.lower()
    idx = lower.find(marker)
    if idx < 0:
        # Legacy Local_Booru_Output support: keep path below that folder.
        legacy = "\\local_booru_output\\"
        idx2 = lower.find(legacy)
        if idx2 >= 0:
            tail = normalized[idx2 + len(legacy):]
            return Path(*[part for part in tail.split("\\") if part]) if tail else None
        return None
    tail = normalized[idx + len(marker):]
    parts = [part for part in tail.split("\\") if part]
    return Path(*parts) if parts else None


def _path_is_under(value: str, root: Path) -> bool:
    try:
        Path(value).resolve().relative_to(root.resolve())
        return True
    except Exception:
        pass
    raw = str(value or "").replace("/", "\\").lower().rstrip("\\")
    base = str(root).replace("/", "\\").lower().rstrip("\\")
    return bool(base and (raw == base or raw.startswith(base + "\\")))


def _table_columns(con, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _update_path_column_by_mapping(con, table: str, column: str, mapping: dict[str, str]) -> int:
    cols = _table_columns(con, table)
    if column not in cols:
        return 0
    changed = 0
    for old, new in mapping.items():
        cur = con.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (new, old))
        changed += int(cur.rowcount or 0)
    return changed


def relocate_missing_library_paths(settings: dict, new_output: str | Path, *, apply: bool = False) -> dict:
    """Relocate managed gallery paths after moving Local_Booru_Archive.

    Earlier versions only rewrote rows whose old path was missing.  That failed
    when the old HDD copy still existed: the gallery/database kept pointing to
    the old absolute F:\\...\\output path even though the active archive was on
    the SSD.  This version rewrites every live ``images.path`` that has the same
    relative path below the selected new output root and whose target file
    exists.  It never guesses by filename alone.
    """
    from core.paths import ensure_output_base
    new_base = ensure_output_base(new_output, settings.get("root"))
    matches: list[tuple[int, str, str]] = []
    already_ok = missing_target = no_relative = 0
    old_roots: dict[str, int] = {}

    with db(settings, readonly=True) as con:
        rows = [dict(r) for r in con.execute("SELECT id,path FROM images WHERE deleted=0 ORDER BY id").fetchall()]

    for row in rows:
        old_s = str(row.get("path") or "")
        if not old_s:
            no_relative += 1
            continue
        if _path_is_under(old_s, new_base):
            already_ok += 1
            continue
        rel = _relative_after_output_branch(old_s)
        if rel is None:
            no_relative += 1
            continue
        candidate = new_base / rel
        if candidate.exists() and candidate.is_file():
            matches.append((int(row["id"]), old_s, str(candidate)))
            # Report the old branch for diagnostics.
            raw = old_s.replace("/", "\\")
            low = raw.lower()
            idx = low.find("\\output\\")
            root = raw[:idx + len("\\output") if idx >= 0 else len(raw)]
            if root:
                old_roots[root] = old_roots.get(root, 0) + 1
        else:
            missing_target += 1

    backup = ""
    table_updates: dict[str, int] = {}
    if apply and matches:
        backup = force_backup_database(settings, "relocate_library_paths")
        if not backup:
            return {
                "old_base": "",
                "new_base": str(new_base),
                "found": len(matches),
                "updated": 0,
                "already_ok": already_ok,
                "missing_target": missing_target,
                "no_relative": no_relative,
                "backup": "",
                "error": "Не удалось создать резервную копию базы. Перенос путей отменён.",
                "matches": matches[:50],
            }
        mapping = {old: new for _image_id, old, new in matches}
        now = _now()
        with db(settings, write=True) as con:
            for image_id, old, new in matches:
                con.execute("UPDATE images SET path=?, file_name=? WHERE id=?", (new, Path(new).name, image_id))
            # Keep auxiliary managed-media references in sync. Do not rewrite
            # original_path/site_scan_status: those usually point to the read-only
            # source archive, not to Local_Booru_Archive/output.
            table_updates["processed_files.media_path"] = _update_path_column_by_mapping(con, "processed_files", "media_path", mapping)
            table_updates["no_match_items.media_path"] = _update_path_column_by_mapping(con, "no_match_items", "media_path", mapping)
            table_updates["tag_enrichment_queue.media_path"] = _update_path_column_by_mapping(con, "tag_enrichment_queue", "media_path", mapping)
            table_updates["delete_log.path"] = _update_path_column_by_mapping(con, "delete_log", "path", mapping)
            table_updates["deleted_media_rules.path"] = _update_path_column_by_mapping(con, "deleted_media_rules", "path", mapping)
            try:
                con.execute("INSERT OR REPLACE INTO app_state(key,value) VALUES(?,?)", ("last_output_path_relocation", json.dumps({
                    "new_base": str(new_base),
                    "updated": len(matches),
                    "backup": backup,
                    "at": now,
                }, ensure_ascii=False)))
            except Exception:
                pass

    old_base = next(iter(old_roots), "")
    return {
        "old_base": old_base,
        "old_roots": old_roots,
        "new_base": str(new_base),
        "found": len(matches),
        "updated": len(matches) if apply else 0,
        "already_ok": already_ok,
        "missing_target": missing_target,
        "no_relative": no_relative,
        "backup": backup,
        "table_updates": table_updates,
        "matches": matches[:50],
    }



def repair_live_md5_by_bytes(settings: dict, *, make_backup: bool = True, progress=None, cancel_check=None) -> dict:
    """Recompute real byte-MD5 for live media rows and update stale DB hashes.

    This is the parser-side repair for old archives and renamed files.  It never
    trusts a 32-hex filename.  If a row says ``hash_md5=A`` but the file bytes
    are actually ``B``, the row is updated to ``B`` and the path/stat result is
    stored in ``settings/cache/parser_file_hash_cache`` so later parser passes
    do not reread the same file.

    The function does not delete or merge files by pHash.  Exact duplicate
    cleanup remains a separate explicit maintenance action.
    """
    backup = force_backup_database(settings, "repair_live_md5_by_bytes") if make_backup else ""
    if make_backup and db_path(settings).exists() and not backup:
        return {"checked": 0, "updated": 0, "missing": 0, "errors": 1, "backup": "", "error": "Не удалось создать резервную копию базы. Ремонт MD5 отменён."}
    with db(settings, readonly=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id,path,file_name,hash_md5 FROM images WHERE deleted=0 ORDER BY id"
        ).fetchall()]
    checked = updated = missing = errors = mismatched_filename = 0
    for idx, row in enumerate(rows, 1):
        if cancel_check and cancel_check():
            return {"checked": checked, "updated": updated, "missing": missing, "errors": errors, "cancelled": True, "backup": backup}
        path = Path(str(row.get("path") or ""))
        if not path.exists() or not path.is_file():
            missing += 1
            continue
        checked += 1
        try:
            from core.file_hash_cache import get_or_compute_md5, filename_md5
            real_md5, _hit = get_or_compute_md5(settings, path)
            old_md5 = str(row.get("hash_md5") or "").strip().lower()
            name_md5 = filename_md5(path)
            if name_md5 and real_md5 and name_md5 != real_md5:
                mismatched_filename += 1
            if real_md5 and real_md5 != old_md5:
                with db(settings, write=True) as con:
                    con.execute("UPDATE images SET hash_md5=? WHERE id=?", (real_md5, int(row["id"])))
                updated += 1
        except Exception:
            errors += 1
        if progress and (idx % 100 == 0 or idx == len(rows)):
            try:
                progress(idx, len(rows))
            except Exception:
                pass
    duplicates_after = 0
    try:
        with db(settings, readonly=True) as con:
            duplicates_after = int(con.execute(
                """SELECT COUNT(*) FROM (
                       SELECT lower(hash_md5) AS md5 FROM images
                       WHERE deleted=0 AND COALESCE(hash_md5,'')<>''
                       GROUP BY lower(hash_md5) HAVING COUNT(*)>1
                   )"""
            ).fetchone()[0] or 0)
    except Exception:
        duplicates_after = 0
    return {
        "checked": checked,
        "updated": updated,
        "missing": missing,
        "errors": errors,
        "mismatched_filename": mismatched_filename,
        "duplicate_md5_groups_after": duplicates_after,
        "backup": backup,
    }

def filename_collision_stats(settings: dict) -> dict:
    """Read-only report for unsafe filename/path situations."""
    with db(settings, readonly=True) as con:
        duplicate_paths = int(con.execute(
            "SELECT COUNT(*) FROM (SELECT path FROM images WHERE deleted=0 GROUP BY path HAVING COUNT(*)>1)"
        ).fetchone()[0] or 0)
        same_name_diff_md5 = int(con.execute(
            """SELECT COUNT(*) FROM (
                   SELECT file_name FROM images WHERE deleted=0 AND COALESCE(file_name,'')<>''
                   GROUP BY file_name HAVING COUNT(DISTINCT lower(COALESCE(hash_md5,'')))>1
               )"""
        ).fetchone()[0] or 0)
        unsafe_content_names = int(con.execute(
            """SELECT COUNT(*) FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>''
               AND file_name NOT LIKE '%' || substr(lower(hash_md5),1,12) || '%'"""
        ).fetchone()[0] or 0)
        rows = con.execute("SELECT id,path,file_name,hash_md5 FROM images WHERE deleted=0 ORDER BY id").fetchall()
    missing = 0
    for row in rows:
        try:
            if not Path(str(row["path"] or "")).exists():
                missing += 1
        except Exception:
            missing += 1
    return {
        "duplicate_live_paths": duplicate_paths,
        "same_filename_different_md5": same_name_diff_md5,
        "unsafe_content_names": unsafe_content_names,
        "missing_live_paths": missing,
    }


def repair_missing_paths_by_md5(settings: dict, *, make_backup: bool = True, progress=None, cancel_check=None) -> dict:
    """Repair live DB rows whose path is missing by searching managed output by MD5.

    This never guesses by filename alone.  Only an exact byte-MD5 match may repair
    a path.  It is intended for old builds that registered ``48.jpg`` but later
    copied/renamed another collision variant.
    """
    backup = force_backup_database(settings, "repair_missing_paths_by_md5") if make_backup else ""
    if make_backup and db_path(settings).exists() and not backup:
        return {"checked": 0, "missing": 0, "repaired": 0, "unresolved": 0, "backup": "", "error": "Не удалось создать резервную копию базы. Ремонт отменён."}
    with db(settings, readonly=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id,path,file_name,hash_md5 FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>'' ORDER BY id"
        ).fetchall()]
    missing = [r for r in rows if not Path(str(r.get("path") or "")).exists()]
    wanted = {str(r.get("hash_md5") or "").lower(): r for r in missing if str(r.get("hash_md5") or "").strip()}
    found: dict[str, Path] = {}
    root = result_output_base(settings)
    checked_files = 0
    if wanted and root.exists():
        for candidate in root.rglob("*"):
            if cancel_check and cancel_check():
                break
            try:
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mkv", ".mov", ".avi"}:
                    continue
                checked_files += 1
                value = file_md5(candidate).lower()
                if value in wanted and value not in found:
                    found[value] = candidate
                    if len(found) >= len(wanted):
                        break
            except Exception:
                continue
            if progress and checked_files % 200 == 0:
                try:
                    progress(checked_files, len(wanted))
                except Exception:
                    pass
    repaired = unresolved = 0
    now = _now()
    with db(settings, write=True) as con:
        for row in missing:
            md5 = str(row.get("hash_md5") or "").lower()
            new_path = found.get(md5)
            if new_path and new_path.exists():
                con.execute("UPDATE images SET path=?, file_name=? WHERE id=?", (str(new_path), new_path.name, int(row["id"])))
                con.execute("UPDATE processed_files SET media_path=? WHERE media_path=?", (str(new_path), str(row.get("path") or "")))
                con.execute("UPDATE no_match_items SET media_path=? WHERE media_path=?", (str(new_path), str(row.get("path") or "")))
                repaired += 1
            else:
                con.execute(
                    "INSERT INTO filename_collision_audit(issue_type,image_id,path,file_name,hash_md5,details,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    ("missing_path_unresolved", int(row["id"]), str(row.get("path") or ""), str(row.get("file_name") or ""), md5, "No exact MD5 file found under managed output", "open", now),
                )
                unresolved += 1
    return {"checked": checked_files, "missing": len(missing), "repaired": repaired, "unresolved": unresolved, "backup": backup}


def normalize_live_content_filenames(settings: dict, *, make_backup: bool = True, progress=None, cancel_check=None) -> dict:
    """Rename live managed files to collision-proof ``original__md5.ext`` names.

    This fixes future ``Файл отсутствует`` issues caused by unrelated files with
    identical names.  It does not merge exact MD5 duplicates; run
    ``cleanup_live_exact_duplicates`` for that separate invariant.
    """
    backup = force_backup_database(settings, "normalize_content_filenames") if make_backup else ""
    if make_backup and db_path(settings).exists() and not backup:
        return {"checked": 0, "renamed": 0, "skipped": 0, "errors": 1, "backup": "", "error": "Не удалось создать резервную копию базы. Нормализация отменена."}
    with db(settings, readonly=True) as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id,path,file_name,hash_md5,COALESCE(original_file_name,file_name) AS original_file_name FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>'' ORDER BY id"
        ).fetchall()]
    checked = renamed = skipped = errors = db_path_collisions = 0
    for idx, row in enumerate(rows, 1):
        if cancel_check and cancel_check():
            break
        checked += 1
        try:
            cur = Path(str(row.get("path") or ""))
            md5 = str(row.get("hash_md5") or "").lower()
            if not cur.exists() or not is_managed_media_path(settings, cur):
                skipped += 1
                continue
            desired = content_addressed_path(cur, md5, original_name=str(row.get("original_file_name") or row.get("file_name") or cur.name))
            if desired.name == cur.name:
                skipped += 1
                continue
            with db(settings, readonly=True) as con:
                other = con.execute("SELECT id FROM images WHERE path=? AND id<>? LIMIT 1", (str(desired), int(row["id"]))).fetchone()
            if other:
                db_path_collisions += 1
                skipped += 1
                continue
            new_path = normalize_managed_content_name(settings, cur, md5, operation="normalize_live_content_filenames", original_name=str(row.get("original_file_name") or row.get("file_name") or cur.name))
            if str(new_path) != str(cur):
                with db(settings, write=True) as con:
                    con.execute("UPDATE images SET path=?, file_name=?, content_name_policy='md5_suffix' WHERE id=?", (str(new_path), new_path.name, int(row["id"])))
                    con.execute("UPDATE processed_files SET media_path=? WHERE media_path=?", (str(new_path), str(cur)))
                    con.execute("UPDATE no_match_items SET media_path=? WHERE media_path=?", (str(new_path), str(cur)))
                renamed += 1
            else:
                skipped += 1
        except Exception:
            errors += 1
        if progress and idx % 200 == 0:
            try:
                progress(idx, len(rows))
            except Exception:
                pass
    return {"checked": checked, "renamed": renamed, "skipped": skipped, "db_path_collisions": db_path_collisions, "errors": errors, "backup": backup}
