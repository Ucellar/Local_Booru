"""Library lifecycle helpers: Inbox, Trash, backups, metadata export and cache upkeep.

This keeps destructive file operations out of UI code. A file moved to trash
retains its database metadata so it can be restored. Only permanent deletion
removes related rows and cached thumbnails.
"""
from __future__ import annotations

import contextlib
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


# Automatic copies made before deletions.  Only these are reused and rotated: copies the user made
# (Settings «Backup SQLite», Diagnostics, VACUUM, bulk repairs) keep the old name and are never removed.
_AUTO_BACKUP_PREFIX = "local_booru_auto_"
_OPERATION_BACKUP_GLOB = _AUTO_BACKUP_PREFIX + "*.sqlite3"


def _recent_operation_backup(folder: Path, minutes: float) -> Path | None:
    """The newest automatic copy younger than ``minutes`` that is a whole base (a copy cut short — the program closed
    or the disk filled while it was written — is never taken for one)."""
    if minutes <= 0:
        return None
    copies = sorted(folder.glob(_OPERATION_BACKUP_GLOB), key=lambda f: f.stat().st_mtime, reverse=True)
    for copy in copies:
        if time.time() - copy.stat().st_mtime >= minutes * 60:
            break
        if _complete_sqlite_file(copy):
            return copy
    return None


def _complete_sqlite_file(path: Path) -> bool:
    """A whole SQLite file: its header names as many pages as the file holds (a copy cut short holds fewer).  A header
    whose page count is not to be trusted (an old writer) does not pass either."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            head = fh.read(100)
    except OSError:
        return False
    if len(head) < 100 or not head.startswith(b"SQLite format 3\x00"):
        return False
    page_size = int.from_bytes(head[16:18], "big")
    page_size = 65536 if page_size == 1 else page_size
    pages = int.from_bytes(head[28:32], "big")
    if page_size < 512 or pages <= 0 or head[24:28] != head[92:96]:
        return False
    return size >= pages * page_size


def _remove_stale_partials(folder: Path, older_than: float = 3600.0) -> None:
    """Copies cut short by a closed program (``*.part``): they are never used, only taking space."""
    for leftover in list(folder.glob("*.part")) + list(folder.glob("*.part-journal")):
        try:
            if time.time() - leftover.stat().st_mtime > older_than:
                leftover.unlink()
        except OSError:
            pass


def _rotate_operation_backups(folder: Path, keep: int) -> None:
    copies = sorted(folder.glob(_OPERATION_BACKUP_GLOB), key=lambda f: f.stat().st_mtime, reverse=True)
    for old in copies[max(1, keep):]:
        for part in (old, Path(str(old) + "-wal")):     # a fallback copy keeps its write-ahead log beside it
            try:
                part.unlink(missing_ok=True)
            except OSError as exc:
                import logging
                logging.getLogger("local_booru.backup").warning("old backup %s not removed: %s", part, exc)


def force_backup_database(settings: dict, reason: str = "operation", *, auto: bool = False,
                          reuse_recent: bool = False) -> str:
    """An independent SQLite copy before destructive work.

    ``auto`` — a routine copy before deleting cards or emptying Trash: only the newest
    ``backup_operation_keep`` such copies stay (they used to pile up forever).  With
    ``reuse_recent`` (moving cards to Trash one by one) a copy younger than
    ``backup_operation_min_interval_minutes`` is reused — Trash keeps the deleted cards anyway.
    Copies the user asked for and copies before bulk repairs are never reused or removed.
    """
    source = db_path(settings)
    if not source.exists():
        return ""
    folder = Path(BACKUPS_DIR) / "db"
    folder.mkdir(parents=True, exist_ok=True)
    if auto and reuse_recent:
        recent = _recent_operation_backup(folder, float(settings.get("backup_operation_min_interval_minutes", 10) or 0))
        if recent is not None:
            return str(recent)
    made = _write_operation_backup(settings, source, folder, reason, auto=auto)
    if made and auto:
        _rotate_operation_backups(folder, int(settings.get("backup_operation_keep", 5) or 5))
    return made


def backup_database_file(settings: dict, source: str | Path, reason: str) -> str:
    """A copy of a base file named by its own path (``force_backup_database`` copies the base of ``settings``):
    «Удалить всё» copies the base it has moved aside.  Kept like the copies the user asks for (never rotated);
    "" when it could not be made."""
    folder = Path(BACKUPS_DIR) / "db"
    folder.mkdir(parents=True, exist_ok=True)
    return _write_operation_backup(settings, Path(source), folder, reason, auto=False)


def _write_operation_backup(settings: dict, source: Path, folder: Path, reason: str, *, auto: bool) -> str:
    """A copy of ``source`` in ``folder``.  It is written under a temporary name and gets its own name only when whole:
    a copy cut short (the program closed, the disk filled) never looks like a copy — a timed purge reuses the copies of
    the last minutes."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(reason))[:40]
    prefix = _AUTO_BACKUP_PREFIX if auto else "local_booru_"
    target = folder / f"{prefix}{stamp}_{safe_reason or 'backup'}.sqlite3"
    partial = Path(str(target) + ".part")
    target_wal = Path(str(target) + "-wal")
    partial_wal = Path(str(target_wal) + ".part")
    _remove_stale_partials(folder)
    try:
        from core.preflight import ensure_space_for_write
        _ok, _msg = ensure_space_for_write(settings, target, int(source.stat().st_size if source.exists() else 0))
        if not _ok:
            return ""
    except Exception:
        pass
    try:
        src = sqlite3.connect(str(source), timeout=60)
        try:
            dst = sqlite3.connect(str(partial), timeout=60)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        os.replace(partial, target)
        return str(target)
    except Exception:
        _unlink_quietly(partial)
    # Fallback is still better than silently continuing without any copy.  The write-ahead log goes along:
    # the changes not yet written into the base file live there (the pair opens as one base); it is in place
    # before the base file gets its name.
    try:
        shutil.copy2(source, partial)
        wal = Path(str(source) + "-wal")
        if wal.exists():
            shutil.copy2(wal, partial_wal)
            os.replace(partial_wal, target_wal)
        os.replace(partial, target)
        return str(target)
    except Exception:
        for leftover in (partial, partial_wal, target_wal):
            _unlink_quietly(leftover)
        return ""


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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


def _md5_for_trash(settings: dict, con, image_id: int, path: Path) -> str:
    """The bytes' MD5 of a card going to Trash: Trash holds content by MD5 (see deleted_registry.reimport_block)."""
    row = con.execute("SELECT hash_md5 FROM images WHERE id=?", (int(image_id),)).fetchone()
    md5 = str((row["hash_md5"] if row else "") or "").strip().lower()
    if md5 or not (path.exists() and path.is_file()):
        return md5
    try:
        from core.file_hash_cache import get_or_compute_md5
        return str(get_or_compute_md5(settings, path)[0] or "").lower()
    except OSError as exc:
        _log_trash_error("md5 before trash", path, exc)
        return ""


def move_to_trash(settings: dict, image_rows: Iterable[dict], *, reason: str = "delete", tag_or_source: str = "", make_backup: bool = True) -> dict:
    """Move cards to Trash: the file goes to output/trash/media, the row keeps all its metadata.

    Trash holds the content: while a card is there, new copies of the same bytes are not imported
    (import_pipeline._hold_trashed, parser pre-check).  The card's Брак entry is closed and comes
    back on restore.
    """
    rows = [dict(r) for r in (image_rows or [])]
    if not rows:
        return {"trashed_files": 0, "errors": 0, "trashed_records": 0, "backup": ""}
    backup = force_backup_database(settings, reason, auto=True, reuse_recent=True) if make_backup else ""
    if make_backup and db_path(settings).exists() and not backup:
        return {"trashed_files": 0, "deleted_files": 0, "errors": len(rows), "trashed_records": 0, "deleted_records": 0, "backup": "", "error": "Не удалось создать резервную копию базы. Удаление отменено."}
    trash = _trash_media_dir(settings)
    moved = errors = records = protected_source_skipped = changed_meanwhile = 0
    now = _now()
    with db(settings, write=True) as con:
        for row in rows:
            image_id = int(row.get("id") or 0)
            old = Path(str(row.get("path") or ""))
            if not image_id:
                continue
            if not require_managed_media_mutation(settings, old, "move_to_trash"):
                protected_source_skipped += 1
                continue
            # The rows were read before this transaction: a card trashed or moved since (the gallery, another
            # page) is left as it is now — writing the old path back would lose its file in the trash.
            now_row = con.execute("SELECT path, deleted FROM images WHERE id=?", (image_id,)).fetchone()
            if now_row is None or int(now_row["deleted"] or 0) or str(now_row["path"] or "") != str(row.get("path") or ""):
                changed_meanwhile += 1
                continue
            new_path = old
            file_moved = False
            con.execute("SAVEPOINT trash_row")
            try:
                md5 = _md5_for_trash(settings, con, image_id, old)
                if old.exists() and old.is_file():
                    new_path = _unique_target(trash, old, image_id)
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    if not move_managed(settings, old, new_path, operation="move_to_trash"):
                        protected_source_skipped += 1
                        con.execute("RELEASE trash_row")
                        continue
                    file_moved = True
                    moved += 1
                con.execute(
                    """UPDATE images SET path=?, file_name=?, deleted=1, lifecycle='trash',
                       hash_md5=COALESCE(NULLIF(?,''), hash_md5),
                       original_media_path=CASE WHEN original_media_path='' THEN ? ELSE original_media_path END,
                       trashed_at=?, inbox_until=0 WHERE id=?""",
                    (str(new_path), new_path.name, md5, str(old), now, image_id),
                )
                con.execute(
                    "INSERT INTO delete_log(path,file_name,reason,tag_or_source,deleted_at) VALUES(?,?,?,?,?)",
                    (str(old), old.name, reason, tag_or_source, now),
                )
                # The Брак list shows rejects that still need attention; a deleted card is not one.
                con.execute("UPDATE no_match_items SET active=0, updated_at=? WHERE active=1 AND media_path=?",
                            (now, str(old)))
                con.execute("RELEASE trash_row")
                records += 1
            except Exception as exc:  # one bad row must not roll back the rows whose files already moved
                errors += 1
                _log_trash_error("move_to_trash", old, exc)
                con.execute("ROLLBACK TO trash_row")
                con.execute("RELEASE trash_row")
                if file_moved:
                    # The row still points at the old place: put the file back there.
                    try:
                        move_managed(settings, new_path, old, operation="move_to_trash.undo")
                        moved -= 1
                    except Exception as undo_exc:
                        _log_trash_error("move_to_trash.undo", new_path, undo_exc)
    return {"trashed_files": moved, "deleted_files": moved, "errors": errors, "trashed_records": records, "deleted_records": records, "protected_source_skipped": protected_source_skipped, "changed_meanwhile": changed_meanwhile, "backup": backup}


def _log_trash_error(operation: str, path: Path, exc: Exception) -> None:
    import logging
    logging.getLogger("local_booru.trash").warning("%s %s: %s: %s", operation, path, type(exc).__name__, exc)


def trash_rows(settings: dict) -> list[dict]:
    """Return trash entries together with the last recorded reason.

    A visible reason is critical because some download/dedup workflows may put
    recoverable files into Trash automatically.  Previously the UI showed only
    a count, leaving the user unable to distinguish manual deletion from an
    automatic duplicate cleanup.
    """
    from core.trash_tools import REASON_SQL, TARGET_SQL     # the reason as the new window's «Корзина» reads it
    with db(settings, readonly=True) as con:
        rows = con.execute(
            f"""
            SELECT i.id,i.path,i.file_name,i.bucket,i.size_bytes,i.width,i.height,i.hash_md5,i.is_video,
                   i.original_media_path,i.trashed_at,
                   {REASON_SQL} AS delete_reason,
                   {TARGET_SQL} AS delete_target
            FROM images i
            WHERE i.deleted=1 AND i.lifecycle IN ('trash','missing')
            ORDER BY i.trashed_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def _merge_image_metadata(con, keep_id: int, source_id: int) -> None:
    """Preserve all discovered metadata before hiding an exact byte duplicate (one set per site)."""
    from core.database.storage import merge_card_metadata
    merge_card_metadata(con, keep_id, source_id)


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
    """Restore recoverable files without creating a second live MD5-identical copy.

    Restoring is the user's explicit «I want this back»: the MD5 delete block is lifted and a
    Брак entry closed by the trash move comes back with the new file path.
    """
    ids = [int(x) for x in image_ids if x is not None]
    restored = errors = skipped_existing = merged_existing = unblocked_md5 = protected_source_skipped = 0
    unblock_after_commit = set()
    now = _now()
    with db(settings, write=True) as con:
        for image_id in ids:
            row = con.execute("SELECT * FROM images WHERE id=? AND lifecycle IN ('trash','missing')", (image_id,)).fetchone()
            if not row:
                continue
            con.execute("SAVEPOINT restore_row")
            file_back = None
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
                    con.execute("RELEASE restore_row")
                    continue
                before_trash = str(row["original_media_path"] or "")
                desired = Path(before_trash or row["path"])
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
                if cur.exists() and str(row["lifecycle"] or "") == "missing" and is_managed_media_path(settings, cur):
                    dest = cur                          # the lost file is back in its place: nothing to move
                elif cur.exists():
                    if not require_managed_media_mutation(settings, cur, "restore_from_trash_source"):
                        protected_source_skipped += 1
                        con.execute("RELEASE restore_row")
                        continue
                    if not move_managed(settings, cur, dest, operation="restore_from_trash"):
                        protected_source_skipped += 1
                        con.execute("RELEASE restore_row")
                        continue
                    file_back = (dest, cur)
                elif not dest.exists():
                    errors += 1
                    con.execute("RELEASE restore_row")
                    continue
                con.execute(
                    "UPDATE images SET path=?,file_name=?,deleted=0,lifecycle='archive',original_media_path='',trashed_at=0 WHERE id=?",
                    (str(dest), dest.name, image_id),
                )
                if before_trash:
                    # Only the entry closed by the trash move (updated at or after it) comes back;
                    # one the user had removed from Брак before deleting the card stays closed.
                    con.execute(
                        """UPDATE no_match_items SET active=1, media_path=?, updated_at=?
                           WHERE active=0 AND media_path=? AND updated_at>=?""",
                        (str(dest), now, before_trash, int(row["trashed_at"] or 0)),
                    )
                if md5:
                    unblock_after_commit.add(md5)
                con.execute("RELEASE restore_row")
                restored += 1
            except Exception as exc:  # one bad row must not roll back the rows whose files already moved
                errors += 1
                _log_trash_error("restore_from_trash", Path(str(row["path"] or "")), exc)
                con.execute("ROLLBACK TO restore_row")
                con.execute("RELEASE restore_row")
                if file_back:
                    try:
                        move_managed(settings, file_back[0], file_back[1], operation="restore_from_trash.undo")
                    except Exception as undo_exc:
                        _log_trash_error("restore_from_trash.undo", file_back[0], undo_exc)
    if unblock_after_commit:
        from core.deleted_registry import forget_deleted_md5
        for md5 in unblock_after_commit:
            unblocked_md5 += 1 if forget_deleted_md5(md5, settings=settings) else 0
    return {"restored": restored, "skipped_existing": skipped_existing, "merged_existing": merged_existing, "unblocked_md5": unblocked_md5, "protected_source_skipped": protected_source_skipped, "errors": errors}


_GROUP_ROWS_SQL = """SELECT * FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>''
               AND lower(hash_md5) IN (SELECT lower(hash_md5) FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>''
                                       GROUP BY lower(hash_md5) HAVING COUNT(*)>1)
               ORDER BY lower(hash_md5), indexed_at ASC, id ASC"""


def _merge_log():
    import logging
    return logging.getLogger("local_booru.trash")


def _report_progress(progress, stage: str, done: int, total: int) -> None:
    if progress is None:
        return
    try:
        progress(stage, done, total)
    except Exception as exc:          # a page that went away must not stop the repair
        _merge_log().warning("exact-copy merge: progress not shown: %s: %s", type(exc).__name__, exc)


def _real_md5(settings: dict, path: str) -> str:
    """MD5 of the file's bytes, "" when it cannot be read (the hash cache answers for unchanged files)."""
    p = Path(path)
    try:
        from core.file_hash_cache import get_or_compute_md5
        return str(get_or_compute_md5(settings, p)[0] or "").lower()
    except OSError:
        return ""
    except Exception as exc:          # the cache is optional: read the bytes directly
        _merge_log().warning("hash cache unavailable for %s: %s: %s", p, type(exc).__name__, exc)
        try:
            return file_md5(p).lower()
        except OSError:
            return ""


def _file_identity(path: str) -> tuple:
    """Which physical file ``path`` names: two spellings of one file (a case difference on Windows,
    a link) are one identity."""
    try:
        st = os.stat(path)
        if st.st_ino:
            return ("inode", st.st_dev, st.st_ino)
    except OSError:
        pass
    return ("path", os.path.normcase(os.path.realpath(path)))


def cleanup_live_exact_duplicates(settings: dict, *, make_backup: bool = True, progress=None, cancel_check=None,
                                  hold=None) -> dict:
    """Normalize the live library to one physical row per exact MD5.

    All tags, all source links, favourite/rating state are merged into the
    canonical row before redundant files are moved to Trash. Older database
    rows may not yet carry ``hash_md5``; this explicit repair operation computes
    their hashes first, with optional UI progress/cancellation. It never uses
    pHash and never removes the final physical copy of any content.

    The MD5 in the base is not trusted: older versions wrote a site's MD5 (or the MD5 of a similar,
    different picture) into cards.  Only cards whose file bytes really have the group's MD5 are merged;
    the others are left as they are (``skipped_bytes_differ``).  Two cards naming one physical file
    (another spelling of the path) are not "copies": moving that file would leave the kept card without
    it — they are left as they are too (``skipped_same_file``).

    The groups are read, merged and moved under ``import_pipeline.IMPORT_LOCK``: a copy of the same
    bytes coming in meanwhile (parser, subscription, extension) joins a card of its MD5 — without the
    lock it could join the one about to go to the Trash, and its tags went there with it.  The bytes are
    read before the lock (only files changed since are read again under it).
    ``hold(paths)`` — a context manager around the move of those files (the web server's
    ``FILE_STREAMS.holding``: on Windows a file being sent cannot be moved).
    ``progress(stage, done, total)`` — stage "hash" (cards without an MD5), "verify" (bytes of the copies).
    """
    from core.media_utils import safe_stat
    # a bulk repair: its copy is kept (not rotated away with the routine copies before deletions)
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
    stopped = {"groups": 0, "trashed_files": 0, "trashed_records": 0, "merged_existing": 0, "cancelled": True,
               "backup": backup}
    for pos, row in enumerate(missing, start=1):
        if cancel_check is not None and cancel_check():
            return dict(stopped, hashed_missing=hashed_missing, errors=hash_errors)
        path = Path(str(row.get("path") or ""))
        try:
            if path.exists() and path.is_file():
                value = file_md5(path).lower()
                with db(settings, write=True) as con:
                    con.execute("UPDATE images SET hash_md5=? WHERE id=?", (value, int(row["id"])))
                hashed_missing += 1
        except Exception as exc:
            hash_errors += 1
            _merge_log().warning("exact-copy merge: MD5 of %s not computed: %s: %s", path, type(exc).__name__, exc)
        _report_progress(progress, "hash", pos, total_missing)

    with db(settings, readonly=True) as con:
        members = [str(r["path"] or "") for r in con.execute(_GROUP_ROWS_SQL).fetchall()]
    verified: dict = {}
    for pos, path in enumerate(members, start=1):
        if cancel_check is not None and cancel_check():
            return dict(stopped, hashed_missing=hashed_missing, errors=hash_errors)
        verified[path] = (safe_stat(Path(path)), _real_md5(settings, path))
        _report_progress(progress, "verify", pos, len(members))

    from core.import_pipeline import IMPORT_LOCK
    with IMPORT_LOCK:
        return _merge_exact_groups_locked(settings, hold, verified, backup=backup, hashed_missing=hashed_missing,
                                          hash_errors=hash_errors)


def _merge_exact_groups_locked(settings: dict, hold, verified: dict, *, backup: str, hashed_missing: int,
                               hash_errors: int) -> dict:
    """The part of :func:`cleanup_live_exact_duplicates` under the import lock: read the groups,
    keep only the cards whose bytes have the group's MD5, merge each group into its keeper, move the
    extra copies to the Trash, lift the MD5 blocks."""
    from core.media_utils import safe_stat

    def real_md5(path: str) -> str:
        known = verified.get(path)
        if known is not None and known[0] == safe_stat(Path(path)):
            return known[1]
        return _real_md5(settings, path)              # changed (or new) since the bytes were read

    with db(settings, readonly=True) as con:
        rows = [dict(r) for r in con.execute(_GROUP_ROWS_SQL).fetchall()]
    by_md5 = {}
    bytes_differ: list = []
    for row in rows:
        path = str(row.get("path") or "")
        try:
            if not Path(path).exists():
                continue
        except Exception:
            continue
        key = str(row.get("hash_md5") or "").lower()
        if real_md5(path) != key:
            bytes_differ.append(path)
            continue
        by_md5.setdefault(key, []).append(row)
    groups = 0
    extras = []
    merge_pairs = []
    duplicate_md5s = []
    same_file: list = []
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
        seen = {_file_identity(str(keep["path"]))}
        duplicate_items = []
        for item in ordered[1:]:
            identity = _file_identity(str(item["path"]))
            if identity in seen:               # the same file under another spelling: not a copy
                same_file.append(str(item["path"]))
                continue
            seen.add(identity)
            duplicate_items.append(item)
        if not duplicate_items:
            continue
        groups += 1
        duplicate_md5s.append(_md5)
        for extra in duplicate_items:
            extras.append({"id": int(extra["id"]), "path": str(extra["path"]), "file_name": str(extra["file_name"]), "bucket": str(extra.get("bucket") or ""), "size_bytes": int(extra.get("size_bytes") or 0)})
            merge_pairs.append((int(keep["id"]), int(extra["id"])))
    for path in bytes_differ:
        _merge_log().warning("exact-copy merge: %s left alone — its bytes are not the MD5 the base has for it", path)
    for path in same_file:
        _merge_log().warning("exact-copy merge: %s left alone — the same file as the kept card", path)
    skipped = {"skipped_bytes_differ": len(bytes_differ), "skipped_same_file": len(same_file)}
    if not extras:
        return {"groups": 0, "trashed_files": 0, "trashed_records": 0, "merged_existing": 0, "unblocked_md5": 0,
                "hashed_missing": hashed_missing, "errors": hash_errors, "backup": backup, **skipped}
    with (hold([e["path"] for e in extras]) if hold is not None else contextlib.nullcontext()):
        with db(settings, write=True) as con:
            for keep_id, source_id in merge_pairs:
                _merge_image_metadata(con, keep_id, source_id)
        result = move_to_trash(
            settings, extras, reason="exact_md5_auto_normalized",
            tag_or_source="точный MD5; источники и теги склеены в одну запись", make_backup=False,
        )
    unblocked_md5 = 0
    try:            # the content stays in the library: the blocks the moves just made must not hold its copies
        from core.deleted_registry import forget_deleted_md5
        for value in set(duplicate_md5s):
            unblocked_md5 += int(forget_deleted_md5(value, settings=settings) or 0)
    except Exception as exc:
        _merge_log().warning("exact-copy merge: MD5 blocks not lifted: %s: %s", type(exc).__name__, exc)
    result.update({"groups": groups, "merged_existing": len(merge_pairs), "unblocked_md5": unblocked_md5,
                   "hashed_missing": hashed_missing, "errors": int(result.get("errors", 0)) + hash_errors,
                   "backup": backup, **skipped})
    return result


def _delete_thumb_files_for_path(path: str) -> int:
    """Delete only thumbnails mapped to one purged media path (see :func:`_delete_thumb_files_for_paths`)."""
    return _delete_thumb_files_for_paths([path])


def _delete_thumb_files_for_paths(paths: Iterable[str]) -> int:
    """Delete only thumbnails mapped to purged media paths — one pass over the cache for all of them (a pass per
    card made a purge of hundreds of cards take minutes on a big cache).

    New thumbnails carry small ``.src`` marker files. Old unmapped cache entries
    are harmless and will disappear during normal shutdown trimming.
    """
    root = Path(CACHE_DIR) / "thumbs"
    wanted = set()
    for path in paths:
        if not path:
            continue
        try:
            wanted.add(str(Path(path).resolve()))
        except Exception:
            wanted.add(str(path))
    removed = 0
    if not wanted or not root.exists():
        return 0
    for marker in root.glob("*.jpg.src"):
        try:
            if marker.read_text(encoding="utf-8", errors="ignore").strip() not in wanted:
                continue
            image = Path(str(marker)[:-4])
            if image.exists():
                image.unlink(missing_ok=True); removed += 1
            marker.unlink(missing_ok=True)
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


def _same_file_back(path: Path, md5: str) -> bool:
    """The file of a «файл пропал» card is at its place again (same bytes when the MD5 is known)."""
    try:
        if not (path.exists() and path.is_file()):
            return False
        return not md5 or file_md5(path).lower() == md5.lower()
    except OSError:
        return False


def purge_trash(settings: dict, image_ids: Iterable[int] | None = None, *, make_backup: bool = True,
                reuse_backup: bool = False, stop_check=None) -> dict:
    """Delete trashed cards for good (``image_ids`` — those, None — the whole trash).  ``reuse_backup`` — a copy of
    the base made minutes ago will do (the timed purge); ``stop_check()`` true — stop between cards: the cards done
    so far are gone, the rest stay in the trash (``stopped``).  A card taken out of the trash meanwhile stays.
    Never while «Вернуть всё» goes (``core.trash_tools.bulk``): it would take the cards not brought back yet."""
    from core.trash_tools import TrashBusy, bulk
    try:
        with bulk("очистка корзины"):
            return _purge_trash(settings, image_ids, make_backup=make_backup, reuse_backup=reuse_backup,
                                stop_check=stop_check)
    except TrashBusy as exc:
        return {"removed_files": 0, "removed_records": 0, "errors": 0, "backup": "", "stopped": True,
                "error": str(exc)}


def _purge_trash(settings: dict, image_ids, *, make_backup: bool, reuse_backup: bool, stop_check) -> dict:
    ids = [int(x) for x in (image_ids or []) if x is not None]
    backup = force_backup_database(settings, "empty_trash", auto=True, reuse_recent=reuse_backup) if make_backup else ""
    if make_backup and db_path(settings).exists() and not backup:
        return {"removed_files": 0, "removed_records": 0, "errors": 1, "backup": "", "error": "Не удалось создать резервную копию базы. Очистка корзины отменена."}
    reason_sql = """COALESCE((SELECT d.reason FROM delete_log d WHERE d.path=CASE WHEN COALESCE(i.original_media_path,'')<>'' THEN i.original_media_path ELSE i.path END ORDER BY d.deleted_at DESC,d.id DESC LIMIT 1), 'unknown') AS last_delete_reason"""
    with db(settings, readonly=True) as con:
        if ids:
            ph = ",".join("?" for _ in ids)
            rows = con.execute(f"SELECT i.*, {reason_sql} FROM images i WHERE i.id IN ({ph}) AND i.lifecycle IN ('trash','missing')", ids).fetchall()
        else:
            rows = con.execute(f"SELECT i.*, {reason_sql} FROM images i WHERE i.lifecycle IN ('trash','missing') AND i.deleted=1").fetchall()
    removed = errors = protected_source_skipped = revived = kept_files = 0
    hard_ids = []
    from core.deleted_registry import mark_deleted
    reimport_blocking_reasons = {"delete", "gallery_context_delete", "post_context_delete", "delete_by_tag", "delete_by_source"}
    trash_dir = _trash_media_dir(settings)
    thumbs_of = []
    stopped = False
    for row in rows:
        if stop_check is not None and stop_check():
            stopped = True
            break
        p = Path(row["path"])
        if str(row["lifecycle"] or "") == "missing":
            # «Файл пропал» cards point at their place in the library, not into Trash.  Purging
            # never deletes a file for them: it removes the row, or, if the very same file is back,
            # the card is simply alive again.
            if _same_file_back(p, str(row["hash_md5"] or "")):
                with db(settings, write=True) as con:
                    con.execute("UPDATE images SET deleted=0, lifecycle='archive', trashed_at=0, original_media_path='' "
                                "WHERE id=? AND lifecycle='missing'", (int(row["id"]),))
                revived += 1
            else:
                hard_ids.append(int(row["id"]))
            continue
        with db(settings, readonly=True) as con:
            now_row = con.execute("SELECT path, lifecycle FROM images WHERE id=?", (int(row["id"]),)).fetchone()
        if (now_row is None or str(now_row["lifecycle"] or "") != "trash"
                or str(now_row["path"] or "") != str(row["path"] or "")):
            continue        # restored, moved or deleted meanwhile (the gallery, while a long purge ran): not ours now
        if not require_managed_media_mutation(settings, p, "purge_trash"):
            protected_source_skipped += 1
            continue
        delete_file = p.exists() and p.is_file()
        if delete_file and not _path_is_under(str(p), trash_dir):
            # The row kept a library path (its file was missing when it went to Trash).  A file
            # there now is deleted only if it is exactly the trashed picture.
            try:
                delete_file = bool(row["hash_md5"]) and file_md5(p).lower() == str(row["hash_md5"]).lower()
            except OSError:
                delete_file = False
            kept_files += 0 if delete_file else 1
        try:
            try:
                last_reason = str(row["last_delete_reason"] or "") if "last_delete_reason" in row.keys() else ""
                # Only an intentional content removal blocks a future import.
                # Automatic/duplicate cleanup must not poison the MD5 registry.
                if last_reason in reimport_blocking_reasons:
                    mark_deleted(p, reason=last_reason, md5=str(row["hash_md5"] or ""), size=int(row["size_bytes"] or 0), settings=settings, manual_delete=True)
            except Exception:
                pass
            if delete_file:
                if unlink_managed(settings, p, operation="purge_trash"):
                    removed += 1
                else:
                    protected_source_skipped += 1
                    continue
            thumbs_of.append(str(p))
            old_media = str(row["original_media_path"] or "") if "original_media_path" in row.keys() else ""
            if old_media:
                thumbs_of.append(old_media)
            hard_ids.append(int(row["id"]))
        except Exception:
            errors += 1
    if thumbs_of:
        _delete_thumb_files_for_paths(thumbs_of)
    removed_records = 0
    if hard_ids:
        ph = ",".join("?" for _ in hard_ids)
        with db(settings, write=True) as con:
            # only cards still in the trash: one restored meanwhile (the gallery, while a long purge ran) keeps its
            # card — its file was moved out before this purge came to it, so the purge did not delete it
            cur = con.execute(f"DELETE FROM images WHERE id IN ({ph}) AND lifecycle IN ('trash','missing')", hard_ids)
            removed_records = int(cur.rowcount or 0)
            from core.database.repository import cleanup_orphans
            cleanup_orphans(con)
    return {"removed_files": removed, "removed_records": removed_records, "protected_source_skipped": protected_source_skipped,
            "revived_missing": revived, "kept_library_files": kept_files, "errors": errors, "backup": backup,
            "stopped": stopped}


def purge_expired_trash(settings: dict, *, hold=None, stop_check=None) -> dict:
    """Permanently remove trashed files only when the user enabled timed cleanup.  ``hold(paths)`` — a context
    in which nothing sends those files (on Windows a file being sent cannot be deleted); ``stop_check`` — as for
    :func:`purge_trash`.

    It runs every few minutes, so it does nothing — no copy of the base either — when nothing it may delete has
    expired: a card whose file lies outside the library stays in the trash (the program never deletes it), and
    used to cost a full copy of the base at every run; a copy of the last minutes is reused."""
    days = max(0, int(settings.get("trash_auto_purge_days", 0) or 0))
    if days <= 0:
        return {"removed_files": 0, "removed_records": 0, "errors": 0, "backup": ""}
    cutoff = _now() - days * 86400
    with db(settings, readonly=True) as con:
        rows = con.execute(
            "SELECT id, path, lifecycle FROM images WHERE lifecycle IN ('trash','missing') AND deleted=1 AND trashed_at>0 AND trashed_at<=?",
            (cutoff,),
        ).fetchall()
    rows = [r for r in rows if str(r["lifecycle"] or "") == "missing" or is_managed_media_path(settings, str(r["path"]))]
    if not rows:
        return {"removed_files": 0, "removed_records": 0, "errors": 0, "backup": ""}
    with (hold([str(r["path"]) for r in rows]) if hold is not None else contextlib.nullcontext()):
        return purge_trash(settings, [int(r["id"]) for r in rows], make_backup=True, reuse_backup=True,
                           stop_check=stop_check)


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


_EXPORT_CHUNK = 500        # card numbers per query (SQLite takes a limited number of parameters)


def export_metadata(settings: dict, destination: str | Path, fmt: str = "json", image_ids: Iterable[int] | None = None) -> int:
    """Write tags and sources of cards to JSON or CSV; returns how many cards were written.

    ``image_ids=None``: the whole library (live cards).  A list: exactly those cards, trashed ones
    too (an export from the Trash section); an empty list writes nothing and returns 0.
    """
    destination = Path(destination)
    ids = None if image_ids is None else sorted({int(x) for x in image_ids if x is not None})
    if ids is not None and not ids:
        return 0
    with db(settings, readonly=True) as con:
        if ids is not None:
            images = []
            for start in range(0, len(ids), _EXPORT_CHUNK):
                part = ids[start:start + _EXPORT_CHUNK]
                ph = ",".join("?" for _ in part)
                images += con.execute(f"SELECT * FROM images WHERE id IN ({ph})", part).fetchall()
            images.sort(key=lambda r: str(r["path"] or "").casefold())
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


def clear_thumbnail_cache(settings: dict) -> dict:
    """Remove every cached thumbnail (each is made again when it is shown).  A file held open right now
    (on Windows: the gallery sending it) stays — counted in ``busy``, not an error."""
    root = Path(CACHE_DIR) / "thumbs"
    removed = freed = busy = 0
    for p in (root.rglob("*") if root.exists() else ()):
        try:
            if not p.is_file():
                continue
            size = p.stat().st_size
            p.unlink()
        except OSError:
            busy += 1
            continue
        removed += 1
        freed += size
    return {"removed": removed, "bytes": freed, "busy": busy}


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
            "trash": q("SELECT COUNT(*) FROM images WHERE deleted=1 AND lifecycle IN ('trash','missing')"),
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


def existing_output_base(selected: str | Path) -> Path | None:
    """The output/ branch a chosen folder names — the same rules as ``paths.ensure_output_base`` (the
    output folder itself, an archive root, a folder holding Local_Booru_Archive) — when it exists; None
    otherwise.  Nothing is created."""
    from core.paths import LEGACY_OUTPUT_FOLDER_NAME, OUTPUT_FOLDER_NAME
    raw = str(selected or "").strip()
    if not raw:
        return None
    base = Path(raw).expanduser()
    try:
        base = base.resolve()
    except OSError:
        base = base.absolute()
    name = base.name.lower()
    if name in (LEGACY_OUTPUT_FOLDER_NAME.lower(), "output"):
        candidate = base
    elif (base / "output").is_dir() or (base / "settings").is_dir() or name == OUTPUT_FOLDER_NAME.lower():
        candidate = base / "output"
    else:
        candidate = base / OUTPUT_FOLDER_NAME / "output"
    return candidate if candidate.is_dir() else None


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

    It creates nothing: a folder that is not an existing library (its output/ branch) is refused.  Creating
    an empty output/ where the configured one was missing turned every card «offline» into «missing» —
    the index and the integrity repair then deleted or trashed them all.
    """
    new_base = existing_output_base(new_output)
    if new_base is None:
        return {"old_base": "", "old_roots": {}, "new_base": str(new_output), "found": 0, "updated": 0, "already_ok": 0,
                "missing_target": 0, "no_relative": 0, "backup": "", "table_updates": {}, "matches": [],
                "error": f"Здесь нет рабочей библиотеки (папки output): {new_output} — ничего не создано и не изменено"}
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
    merged_here = 0
    with db(settings, readonly=True) as con:          # cards already made of the files here (to be joined)
        duplicates = sum(1 for _image_id, _old, new in matches
                         if con.execute("SELECT 1 FROM images WHERE path=? LIMIT 1", (new,)).fetchone())
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
        from core.database.storage import merge_card_metadata
        mapping = {old: new for _image_id, old, new in matches}
        now = _now()
        with db(settings, write=True) as con:
            for image_id, old, new in matches:
                # a card the index made of the file here before the move was known (older versions did):
                # its tags join the real card, which then takes the place (a second row there is refused)
                for dup in con.execute("SELECT id FROM images WHERE path=? AND id<>?", (new, image_id)).fetchall():
                    merge_card_metadata(con, image_id, int(dup["id"]))
                    con.execute("DELETE FROM images WHERE id=?", (int(dup["id"]),))
                    merged_here += 1
                con.execute("UPDATE images SET path=?, file_name=? WHERE id=?", (new, Path(new).name, image_id))
            # Keep auxiliary managed-media references in sync. Do not rewrite
            # original_path/site_scan_status: those usually point to the read-only
            # source archive, not to Local_Booru_Archive/output.
            table_updates["processed_files.media_path"] = _update_path_column_by_mapping(con, "processed_files", "media_path", mapping)
            table_updates["source_originals.media_path"] = _update_path_column_by_mapping(con, "source_originals", "media_path", mapping)
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
        "duplicates_here": duplicates,
        "merged_duplicates": merged_here,
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
    from core.source_protection import is_inside, source_root
    source = source_root(settings)
    users_inside = source is not None and is_inside(source, root)     # the user's pictures kept in the output
    if wanted and root.exists():
        for candidate in root.rglob("*"):
            if cancel_check and cancel_check():
                break
            try:
                if not candidate.is_file():
                    continue
                if users_inside and not is_managed_media_path(settings, candidate):
                    continue                    # a card never points at the user's own file
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
                con.execute("UPDATE source_originals SET media_path=? WHERE media_path=?", (str(new_path), str(row.get("path") or "")))
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
                    con.execute("UPDATE source_originals SET media_path=? WHERE media_path=?", (str(new_path), str(cur)))
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
