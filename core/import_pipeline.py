"""Single finishing pipeline for media arriving from parser/downloader/subscriptions.

All import sources should call :func:`register_media_import` after a media file
is safely present on disk. It keeps lifecycle, URL history, metadata, preview
creation and deleted-file policy consistent without exposing more UI controls.

Exact MD5 invariant:
    one live physical media file / one live image row / many source links.
A new identical copy is never kept as another live gallery card; its sources
and tags are merged into the canonical row automatically.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable
import logging

_LOG = logging.getLogger("local_booru.md5_invariant")

def _log_exact_md5_merge(canonical: Path, md5: str, added_sources, *, discarded_copy: bool = False, origin: str = "") -> None:
    """Log only meaningful exact-MD5 events instead of every metadata refresh."""
    added_sources = list(added_sources or [])
    try:
        if added_sources:
            _LOG.info(
                "EXACT MD5 MERGE: existing_media=%s md5=%s source_added=%s origin=%s discarded_transient_copy=%s no_physical_copy_created=1",
                str(canonical), str(md5 or ""), len(added_sources), str(origin or ""), int(bool(discarded_copy)),
            )
        elif discarded_copy:
            _LOG.info(
                "EXACT MD5 DEDUPE: existing_media=%s md5=%s origin=%s discarded_transient_copy=1 no_physical_copy_created=1",
                str(canonical), str(md5 or ""), str(origin or ""),
            )
    except Exception:
        pass

def _urls_from_sources(source_list) -> set[str]:
    urls: set[str] = set()
    for line in source_list or []:
        for part in str(line or "").split():
            if part.startswith(("http://", "https://")):
                urls.add(part.strip())
    return urls

def _linked_source_urls(settings: dict, media_path: Path) -> set[str]:
    try:
        from core.database.connection import db
        with db(settings, readonly=True) as con:
            rows = con.execute(
                """SELECT s.url FROM images i
                   JOIN image_sources x ON x.image_id=i.id
                   JOIN sources s ON s.id=x.source_id
                   WHERE i.path=? AND i.deleted=0""", (str(media_path),)
            ).fetchall()
        return {str(row["url"] or "") for row in rows if str(row["url"] or "")}
    except Exception:
        return set()


from core.media_utils import file_md5


def _deleted_action(settings: dict, md5: str) -> str:
    if not md5:
        return "allow"
    try:
        from core.deleted_registry import has_deleted_md5
        if has_deleted_md5(md5, settings=settings):
            return "return_inbox" if str(settings.get("deleted_reimport_policy", "skip")) == "return_inbox" else "skip"
    except Exception:
        pass
    return "allow"


def _desired_bucket(status: str) -> str:
    value = str(status or "").lower()
    if value in {"tagged", "found", "downloaded_found"}:
        return "found"
    if value in {"partial", "partial_match", "downloaded_partial_match"}:
        return "partial_match"
    if value in {"nomatch", "no_match", "downloaded_no_match"}:
        return "no_match"
    return ""


def _live_canonical_path(settings: dict, md5: str, status: str, incoming: Path) -> str:
    """Pick an already-live exact MD5 row that is safe to enrich.

    A successful FOUND import must never be folded into a previous NO_MATCH
    card; instead it creates/promotes the FOUND card and normal cleanup can
    discard the obsolete no-match copy. A no-match import may reuse an already
    known FOUND card because there is no reason to keep an identical reject.
    """
    if not md5:
        return ""
    from core.database.storage import media_path_by_md5, found_media_path_by_md5
    bucket = _desired_bucket(status)
    if bucket == "found":
        return found_media_path_by_md5(settings, md5, exclude_path=str(incoming))
    if bucket == "partial_match":
        return (
            found_media_path_by_md5(settings, md5, exclude_path=str(incoming))
            or media_path_by_md5(settings, md5, preferred_bucket="partial_match", require_bucket=True, exclude_path=str(incoming))
        )
    return found_media_path_by_md5(settings, md5, exclude_path=str(incoming)) or media_path_by_md5(settings, md5, preferred_bucket="no_match", require_bucket=True, exclude_path=str(incoming))


def _is_managed_output_file(settings: dict, path: Path) -> bool:
    try:
        from core.paths import result_output_base
        path.resolve().relative_to(result_output_base(settings).resolve())
        return True
    except Exception:
        return False


def _is_already_live_library_path(settings: dict, path: Path) -> bool:
    """Return True when this very path is already a visible library object."""
    try:
        from core.database.connection import db
        with db(settings, readonly=True) as con:
            row = con.execute("SELECT 1 FROM images WHERE path=? AND deleted=0 LIMIT 1", (str(path),)).fetchone()
        return row is not None
    except Exception:
        return False




def _restore_stale_exact_row_to_path(settings: dict, md5: str, incoming: Path, status: str, lifecycle_kwargs: dict | None = None) -> dict:
    """Revive/re-home an old deleted or missing-file row for this exact MD5.

    This covers the common retry path where a file was previously moved to
    Local Booru's Deleted/Trash state, or a live row was left pointing at a
    missing managed file, then the user re-parses a surviving no_match copy.
    The re-parse must promote the existing logical object to the new real
    managed file path instead of leaving the gallery row pointing at the old
    missing path.
    """
    result = {"restored": 0, "image_id": 0, "old_path": "", "new_path": ""}
    value = str(md5 or "").strip().lower()
    if not value:
        return result
    try:
        incoming = Path(incoming)
        if not incoming.exists() or not incoming.is_file():
            return result
    except Exception:
        return result
    try:
        from core.database.connection import db
        from core.media_utils import safe_stat, image_size, bucket_for_path, VIDEO_EXTS
        import time
        with db(settings, write=True) as con:
            target = str(incoming)
            target_row = con.execute("SELECT * FROM images WHERE path=?", (target,)).fetchone()
            # Prefer restoring the row already attached to the target path; then
            # any deleted/missing row with the same exact MD5.
            candidates = []
            if target_row is not None:
                candidates.append(target_row)
            rows = con.execute(
                """SELECT * FROM images
                   WHERE lower(COALESCE(hash_md5,''))=? AND path<>?
                   ORDER BY CASE
                       WHEN bucket IN ('found','downloaded_found') THEN 0
                       WHEN bucket IN ('partial_match','downloaded_partial_match') THEN 1
                       WHEN bucket IN ('no_match','downloaded_no_match') THEN 2
                       ELSE 3 END,
                       deleted DESC, indexed_at DESC, id DESC""",
                (value, target),
            ).fetchall()
            candidates.extend(rows)

            picked = None
            for row in candidates:
                try:
                    old_path = str(row["path"] or "")
                    old_exists = bool(old_path and Path(old_path).exists())
                    is_deleted = int(row["deleted"] or 0) != 0
                    if is_deleted or not old_exists:
                        picked = row
                        break
                except Exception:
                    picked = row
                    break
            if picked is None:
                return result
            picked_id = int(picked["id"])
            # If another different row already owns target, let the normal
            # upsert path handle that row rather than violating UNIQUE(path).
            if target_row is not None and int(target_row["id"]) != picked_id:
                return result
            size, mtime_ns = safe_stat(incoming)
            width, height = image_size(incoming) if incoming.exists() else (0, 0)
            bucket = bucket_for_path(incoming) or _desired_bucket(status)
            life = None
            inbox_until = 0
            origin = ""
            if lifecycle_kwargs:
                life = lifecycle_kwargs.get("lifecycle")
                inbox_until = int(lifecycle_kwargs.get("inbox_until", 0) or 0)
                origin = str(lifecycle_kwargs.get("import_origin", "") or "")
            if not life:
                life = str(picked["lifecycle"] or "archive") if "lifecycle" in picked.keys() else "archive"
            con.execute(
                """UPDATE images SET
                       path=?, file_name=?, bucket=?, size_bytes=?, width=?, height=?,
                       hash_md5=COALESCE(NULLIF(?,''), hash_md5), mtime_ns=?,
                       is_video=?, indexed_at=?, deleted=0,
                       lifecycle=?, inbox_until=?, original_media_path='', trashed_at=0,
                       import_origin=CASE WHEN ?<>'' THEN ? ELSE import_origin END
                   WHERE id=?""",
                (
                    target, incoming.name, bucket, int(size or 0), int(width or 0), int(height or 0),
                    value, int(mtime_ns or 0), int(incoming.suffix.lower() in VIDEO_EXTS), int(time.time()),
                    str(life), int(inbox_until or 0), origin, origin, picked_id,
                ),
            )
            try:
                con.execute(
                    """INSERT INTO processed_files(original_path, original_name, media_path, status, bucket, processed_at)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(media_path) DO UPDATE SET
                         original_path=excluded.original_path,
                         original_name=excluded.original_name,
                         status=excluded.status,
                         bucket=excluded.bucket,
                         processed_at=excluded.processed_at""",
                    ("", incoming.name, target, status or "", bucket, int(time.time())),
                )
            except Exception:
                pass
            result.update({"restored": 1, "image_id": picked_id, "old_path": str(picked["path"] or ""), "new_path": target})
            try:
                _LOG.info("RESTORE STALE EXACT MD5 ROW: md5=%s image_id=%s old_path=%s new_path=%s", value, picked_id, result["old_path"], target)
            except Exception:
                pass
            return result
    except Exception:
        return result


def _discard_transient_exact_copy(settings: dict, incoming: Path, canonical: Path) -> bool:
    """Remove a newly-created redundant output copy without polluting Trash.

    This is safe only inside Local Booru output: the canonical on-disk file has
    the identical MD5 and metadata has already been merged into its DB row.
    Source originals outside the application output are never touched.
    """
    try:
        if incoming.resolve() == canonical.resolve():
            return False
        if not _is_managed_output_file(settings, incoming):
            return False
        if incoming.exists() and incoming.is_file():
            from core.services.media_storage_service import unlink_managed
            return bool(unlink_managed(settings, incoming, operation="import_pipeline.discard_exact_copy"))
    except Exception:
        pass
    return False



def _cleanup_promoted_nomatch(settings: dict, md5: str, original_path: str, promoted_path: Path, *, origin: str = "") -> dict:
    """Deactivate/delete stale NO_MATCH copy after FOUND/PARTIAL promotion."""
    try:
        from core.nomatch_db import deactivate_promoted_exact_match
        result = deactivate_promoted_exact_match(
            settings=settings,
            md5=md5 or "",
            original_path=original_path or "",
            promoted_path=promoted_path,
        ) or {}
        try:
            changed = int(result.get("rows_deactivated", 0) or 0) + int(result.get("image_rows_removed", 0) or 0) + int(result.get("files_removed", 0) or 0)
            if changed:
                _LOG.info(
                    "NO_MATCH PROMOTE CLEANUP: md5=%s promoted=%s origin=%s rows_deactivated=%s image_rows_removed=%s files_removed=%s errors=%s",
                    str(md5 or ""), str(promoted_path), str(origin or ""),
                    int(result.get("rows_deactivated", 0) or 0),
                    int(result.get("image_rows_removed", 0) or 0),
                    int(result.get("files_removed", 0) or 0),
                    int(result.get("errors", 0) or 0),
                )
        except Exception:
            pass
        return dict(result)
    except Exception:
        return {"rows_deactivated": 0, "image_rows_removed": 0, "files_removed": 0, "errors": 1}

def register_media_import(
    settings: dict,
    media_path: str | Path,
    *,
    tags: Iterable[str] | None = None,
    groups: dict | None = None,
    sources: Iterable[str] | None = None,
    status: str = "tagged",
    original_path: str = "",
    hash_md5: str | None = None,
    raw: dict | None = None,
    post_url: str = "",
    file_url: str = "",
    site: str = "",
    source_tag_groups: list[dict] | None = None,
    origin: str = "import",
    merge_existing: bool = True,
    generate_thumbnail: bool = True,
) -> dict:
    """Store one incoming result and enforce the exact-MD5 single-file invariant.

    If a byte-identical live media row already exists, the incoming sources and
    tags are added to that canonical row and a redundant newly-created file in
    Local Booru output is discarded. It is *not* sent to Trash, because it was
    never a second library object; it was a transient copy of an existing one.
    """
    p = Path(media_path)
    md5 = str(hash_md5 or "").strip().lower()
    if not md5 and p.exists() and p.is_file():
        try:
            from core.file_hash_cache import get_or_compute_md5
            md5 = get_or_compute_md5(settings, p)[0]
        except Exception:
            try:
                md5 = file_md5(p).lower()
            except Exception:
                md5 = ""

    source_list = [str(x).strip() for x in (sources or []) if str(x).strip()]
    if post_url and post_url not in source_list:
        source_list.append(post_url)
    if file_url and file_url not in source_list:
        source_list.append(file_url)

    desired_bucket = _desired_bucket(status)
    restore_lifecycle_kwargs = None
    if desired_bucket in {"found", "partial_match"}:
        try:
            from core.library_lifecycle import import_lifecycle_kwargs as _lb_import_lifecycle_kwargs
            restore_lifecycle_kwargs = _lb_import_lifecycle_kwargs(settings, origin)
        except Exception:
            restore_lifecycle_kwargs = None
        _restore_stale_exact_row_to_path(settings, md5, p, status, restore_lifecycle_kwargs)

    # A live canonical exact copy wins over any obsolete deleted-MD5 marker.
    # This is the critical path for one image being found on multiple sources.
    canonical_path = _live_canonical_path(settings, md5, status, p)
    if canonical_path:
        from core.database.storage import upsert_media_metadata
        from core.library_lifecycle import update_url_history
        canonical = Path(canonical_path)
        sources_before = _linked_source_urls(settings, canonical)
        image_id = upsert_media_metadata(
            settings,
            canonical,
            tags=list(tags or []),
            groups=groups,
            source_text="\n".join(source_list),
            status=status,
            original_path=original_path,
            hash_md5=md5 or None,
            raw=raw,
            post_url=post_url,
            file_url=file_url,
            site=site,
            source_tag_groups=source_tag_groups,
            merge_existing=True,
            # Do not make an old archive item "new" again merely because a
            # second source was found for the same bytes.
            lifecycle=None,
        )
        try:
            from core.deleted_registry import forget_deleted_md5
            forget_deleted_md5(md5, settings=settings)
        except Exception:
            pass
        for url in source_list:
            try:
                update_url_history(settings, url, status="merged_exact_md5", image_id=image_id)
            except Exception:
                pass
        removed_copy = _discard_transient_exact_copy(settings, p, canonical)
        sources_after = _linked_source_urls(settings, canonical)
        added_sources = sorted(sources_after - sources_before)
        _log_exact_md5_merge(canonical, md5, added_sources, discarded_copy=removed_copy, origin=origin)
        if generate_thumbnail and canonical.exists():
            try:
                from core.image_safe import safe_thumbnail_path
                w = int(settings.get("thumb_cache_card_w", settings.get("thumb_cache_w", 256)) or 256)
                h = int(settings.get("thumb_cache_card_h", settings.get("thumb_cache_h", 256)) or 256)
                safe_thumbnail_path(str(canonical), max(256, w * 2), max(256, h * 2))
            except Exception:
                pass
        nomatch_cleanup = {}
        if desired_bucket in {"found", "partial_match"}:
            nomatch_cleanup = _cleanup_promoted_nomatch(settings, md5, original_path or str(p), canonical, origin=origin)
        return {
            "action": "merged_exact_md5",
            "image_id": int(image_id or 0),
            "md5": md5,
            "canonical_path": str(canonical),
            "source_added": len(added_sources),
            "discarded_transient_copy": bool(removed_copy),
            "nomatch_cleanup": nomatch_cleanup,
        }

    # Updating metadata on an already-live canonical row is never a re-import.
    # Old erroneous deleted-MD5 registry entries must not throw the live file
    # back into Trash when another source is attached to it.
    already_live_current = _is_already_live_library_path(settings, p)
    sources_before = _linked_source_urls(settings, p) if already_live_current else set()
    if already_live_current:
        deleted_action = "allow"
        try:
            from core.deleted_registry import forget_deleted_md5
            forget_deleted_md5(md5, settings=settings)
        except Exception:
            pass
    else:
        deleted_action = _deleted_action(settings, md5)
    if deleted_action == "skip":
        try:
            if _is_managed_output_file(settings, p) and p.exists():
                from core.library_lifecycle import trash_media_paths, update_url_history
                trash_media_paths(settings, [p], reason="reimport_deleted_rejected", make_backup=False)
                for url in source_list:
                    if url:
                        update_url_history(settings, url, status="skipped_deleted", error="exact MD5 previously deleted")
        except Exception:
            pass
        return {"action": "skip_deleted", "image_id": 0, "md5": md5}

    from core.database.storage import upsert_media_metadata
    from core.library_lifecycle import import_lifecycle_kwargs, update_url_history
    # Attaching another site to an existing object is enrichment, not a new
    # arrival; keep its archive/inbox state unchanged.
    lifecycle_kwargs = {} if already_live_current else import_lifecycle_kwargs(settings, origin)
    if deleted_action == "return_inbox" and not already_live_current:
        lifecycle_kwargs["lifecycle"] = "inbox"
    image_id = upsert_media_metadata(
        settings,
        p,
        tags=list(tags or []),
        groups=groups,
        source_text="\n".join(source_list),
        status=status,
        original_path=original_path,
        hash_md5=md5 or None,
        raw=raw,
        post_url=post_url,
        file_url=file_url,
        site=site,
        source_tag_groups=source_tag_groups,
        merge_existing=merge_existing,
        **lifecycle_kwargs,
    )
    for url in source_list:
        try:
            update_url_history(settings, url, status="downloaded", image_id=image_id)
        except Exception:
            pass
    added_sources = []
    if already_live_current:
        sources_after = _linked_source_urls(settings, p)
        added_sources = sorted(sources_after - sources_before)
        _log_exact_md5_merge(p, md5, added_sources, discarded_copy=False, origin=origin)
    nomatch_cleanup = {}
    if desired_bucket in {"found", "partial_match"}:
        nomatch_cleanup = _cleanup_promoted_nomatch(settings, md5, original_path or str(p), p, origin=origin)
    if generate_thumbnail and p.exists():
        try:
            from core.image_safe import safe_thumbnail_path
            w = int(settings.get("thumb_cache_card_w", settings.get("thumb_cache_w", 256)) or 256)
            h = int(settings.get("thumb_cache_card_h", settings.get("thumb_cache_h", 256)) or 256)
            safe_thumbnail_path(str(p), max(256, w * 2), max(256, h * 2))
        except Exception:
            pass
    return {"action": "imported", "image_id": int(image_id or 0), "md5": md5, "canonical_path": str(p), "source_added": len(added_sources), "nomatch_cleanup": nomatch_cleanup}
