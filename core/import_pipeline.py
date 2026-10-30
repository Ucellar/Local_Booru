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
import threading
import time

from core.work_timing import BASE, IMPORT_WAIT, STEPS

_LOG = logging.getLogger("local_booru.md5_invariant")

# One import at a time in this process.  The parser, the grabber, subscriptions and the
# browser extension all register through here; "is there already a card with these bytes?"
# and "create the card" must not interleave, or identical bytes end up as two cards.
IMPORT_LOCK = threading.RLock()

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
        return (found_media_path_by_md5(settings, md5, exclude_path=str(incoming))
                or _live_card_path(settings, md5, incoming))
    if bucket == "partial_match":
        return (
            found_media_path_by_md5(settings, md5, exclude_path=str(incoming))
            or media_path_by_md5(settings, md5, preferred_bucket="partial_match", require_bucket=True, exclude_path=str(incoming))
            or _live_card_path(settings, md5, incoming)
        )
    return found_media_path_by_md5(settings, md5, exclude_path=str(incoming)) or media_path_by_md5(settings, md5, preferred_bucket="no_match", require_bucket=True, exclude_path=str(incoming))


def _live_card_path(settings: dict, md5: str, incoming: Path) -> str:
    """Any other live card with these bytes except a Брак copy (a FOUND result promotes those instead).

    Grabber files without a "found" folder and subscription files live in other buckets
    ("downloaded", "original"); without this, identical bytes became a second card.
    """
    value = str(md5 or "").strip().lower()
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        rows = con.execute(       # «+deleted»: by the MD5's index (storage.media_path_by_md5)
            """SELECT path FROM images
               WHERE +deleted=0 AND hash_md5 IN (?,?) AND path<>?
                 AND bucket NOT IN ('no_match','downloaded_no_match')
               ORDER BY indexed_at ASC, id ASC""",
            (value, value.upper(), str(incoming)),
        ).fetchall()
    for row in rows:
        if Path(str(row["path"] or "")).exists():
            return str(row["path"])
    return ""


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




def _is_trash_row(row) -> bool:
    try:
        return int(row["deleted"] or 0) != 0 and str(row["lifecycle"] or "") == "trash"
    except (KeyError, IndexError, TypeError):
        return False


def _rehome_row(con, row, incoming: Path, status: str, lifecycle_kwargs: dict | None, md5: str) -> None:
    """Point an existing logical object (its tags, sources, rating) at a new real file."""
    from core.media_utils import safe_stat, image_size, bucket_for_path, VIDEO_EXTS
    import time
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
        life = str(row["lifecycle"] or "archive") if "lifecycle" in row.keys() else "archive"
        if life in ("trash", "missing"):
            life = "archive"
    con.execute(
        """UPDATE images SET
               path=?, file_name=?, bucket=?, size_bytes=?, width=?, height=?,
               hash_md5=COALESCE(NULLIF(?,''), hash_md5), mtime_ns=?,
               is_video=?, indexed_at=?, deleted=0,
               lifecycle=?, inbox_until=?, original_media_path='', trashed_at=0,
               import_origin=CASE WHEN ?<>'' THEN ? ELSE import_origin END
           WHERE id=?""",
        (
            str(incoming), incoming.name, bucket, int(size or 0), int(width or 0), int(height or 0),
            md5, int(mtime_ns or 0), int(incoming.suffix.lower() in VIDEO_EXTS), int(time.time()),
            str(life), int(inbox_until or 0), origin, origin, int(row["id"]),
        ),
    )


def _restore_stale_exact_row_to_path(settings: dict, md5: str, incoming: Path, status: str, lifecycle_kwargs: dict | None = None) -> dict:
    """Re-home a live row whose managed file went missing (or an old hidden row) onto the new copy.

    The re-parse of a surviving source file must give the existing logical object (tags,
    sources, rating) its new real file instead of creating a second card.

    Rows in Trash are never touched here: the user deleted them, and Trash holds them until an
    explicit restore or purge (see ``_hold_trashed``).  The caller skips this step when a live
    canonical copy exists — then the normal exact-MD5 merge handles the import (re-homing first
    would leave the re-homed row on a discarded copy).
    """
    result = {"restored": 0, "image_id": 0, "old_path": "", "new_path": ""}
    value = str(md5 or "").strip().lower()
    if not value:
        return result
    try:
        incoming = Path(incoming)
        if not incoming.exists() or not incoming.is_file():
            return result
    except OSError:
        return result
    import sqlite3
    from core.database.connection import db
    try:
        return _restore_stale_row(db, settings, value, incoming, status, lifecycle_kwargs, result)
    except sqlite3.Error as exc:
        _LOG.warning("restore stale exact row %s -> %s failed: %s", value, incoming, exc)
        return result


def _restore_stale_row(db, settings: dict, value: str, incoming: Path, status: str, lifecycle_kwargs, result: dict) -> dict:
    with db(settings, write=True) as con:
        target = str(incoming)
        target_row = con.execute("SELECT * FROM images WHERE path=?", (target,)).fetchone()
        if target_row is not None and _is_trash_row(target_row):
            return result
        rows = con.execute(
            """SELECT * FROM images
               WHERE hash_md5 IN (?,?) AND path<>?
               ORDER BY CASE
                   WHEN bucket IN ('found','downloaded_found') THEN 0
                   WHEN bucket IN ('partial_match','downloaded_partial_match') THEN 1
                   WHEN bucket IN ('no_match','downloaded_no_match') THEN 2
                   ELSE 3 END,
                   deleted DESC, indexed_at DESC, id DESC""",
            (value, value.upper(), target),
        ).fetchall()
        candidates = ([target_row] if target_row is not None else []) + [r for r in rows if not _is_trash_row(r)]
        picked = None
        for row in candidates:
            old_path = str(row["path"] or "")
            if int(row["deleted"] or 0) != 0 or not (old_path and Path(old_path).exists()):
                picked = row
                break
        if picked is None:
            return result
        # If another different row already owns target, let the normal
        # upsert path handle that row rather than violating UNIQUE(path).
        if target_row is not None and int(target_row["id"]) != int(picked["id"]):
            return result
        _rehome_row(con, picked, incoming, status, lifecycle_kwargs, value)
        result.update({"restored": 1, "image_id": int(picked["id"]), "old_path": str(picked["path"] or ""), "new_path": target})
    _LOG.info("RESTORE STALE EXACT MD5 ROW: md5=%s image_id=%s old_path=%s new_path=%s",
              value, result["image_id"], result["old_path"], target)
    return result


def trash_hold(settings: dict, md5: str, incoming: str | Path, *, status: str, origin: str, sources=()) -> dict | None:
    """Public entry of the Trash rule for writers that register a copy themselves (NO_MATCH copies).

    Returns None when the copy may be used (nothing in Trash, or the trashed card was brought back
    onto it by the policy «Вернуть в «Новые»»), else the result of the held import (the copy is removed).
    """
    p = Path(incoming)
    with IMPORT_LOCK:
        if not md5 or _is_already_live_library_path(settings, p):
            return None
        return _hold_trashed(settings, str(md5).lower(), p, [str(x) for x in sources or [] if str(x).strip()],
                             status=status, origin=origin)


def _path_has_row(settings: dict, path: Path) -> bool:
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        return con.execute("SELECT 1 FROM images WHERE path=? LIMIT 1", (str(path),)).fetchone() is not None


def _hold_trashed(settings: dict, md5: str, incoming: Path, source_list: list, *, status: str, origin: str) -> dict | None:
    """Bytes the user put into Trash stay there until an explicit restore or purge.

    Policy «Не скачивать обратно» (default): the new copy is not imported; a transient copy made
    in the library folder is removed (Trash already holds the same bytes).
    Policy «Вернуть в «Новые»»: the trashed card itself comes back onto the new copy, and its
    file in Trash is removed — one card, nothing left behind in trash/media.
    Returns the final result of the import, or None when the import should continue.
    """
    from core.deleted_registry import trashed_rows
    rows = trashed_rows(md5, settings=settings)
    if not rows:
        return None
    from core.library_lifecycle import update_url_history
    newest = rows[0]
    if str(settings.get("deleted_reimport_policy", "skip")) == "return_inbox":
        trash_file = Path(str(newest.get("path") or ""))
        try:
            same_bytes = not trash_file.exists() or file_md5(trash_file).lower() == str(md5).lower()
        except OSError:
            same_bytes = False
        if not same_bytes:
            return None                             # the Trash file is something else: import as usual
        from core.database.connection import db
        from core.library_lifecycle import import_lifecycle_kwargs
        lifecycle = import_lifecycle_kwargs(settings, origin)
        lifecycle["lifecycle"] = "inbox"
        with db(settings, write=True) as con:
            row = con.execute("SELECT * FROM images WHERE id=?", (int(newest["id"]),)).fetchone()
            if row is None or not _is_trash_row(row):
                return None
            if con.execute("SELECT 1 FROM images WHERE path=? AND id<>?", (str(incoming), int(row["id"]))).fetchone():
                return None
            _rehome_row(con, row, incoming, status, lifecycle, str(md5).lower())
        if trash_file.exists() and trash_file != incoming:
            from core.services.media_storage_service import unlink_managed
            unlink_managed(settings, trash_file, operation="import_pipeline.return_from_trash")
        try:
            from core.deleted_registry import forget_deleted_md5
            forget_deleted_md5(md5, settings=settings)
        except Exception as exc:
            _LOG.warning("return from trash: could not lift the delete block of %s: %s", md5, exc)
        _LOG.info("RETURN FROM TRASH: md5=%s image_id=%s trash_file=%s new_path=%s origin=%s",
                  md5, newest["id"], trash_file, incoming, origin)
        return None
    removed = False
    if (_is_managed_output_file(settings, incoming) and incoming.exists() and incoming.is_file()
            and not _path_has_row(settings, incoming)):
        # Only a transient copy made for this import is removed — never a file some card owns
        # (e.g. another Trash card's file that a caller tried to register again).
        from core.services.media_storage_service import unlink_managed
        removed = bool(unlink_managed(settings, incoming, operation="import_pipeline.trash_hold"))
    for url in source_list:
        try:
            update_url_history(settings, url, status="skipped_trashed", error="exact MD5 is in Trash")
        except Exception as exc:
            _LOG.warning("url history: %s", exc)
    _LOG.info("TRASH HOLD: md5=%s trashed_image_id=%s incoming=%s discarded_copy=%s origin=%s",
              md5, newest["id"], incoming, int(removed), origin)
    return {"action": "skip_trashed", "image_id": 0, "md5": md5, "trashed_image_id": int(newest["id"]),
            "discarded_transient_copy": removed}


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

def _bytes_md5(settings: dict, p: Path) -> str:
    """MD5 of the file's bytes ("" when it cannot be read)."""
    try:
        if not (p.exists() and p.is_file()):
            return ""
    except OSError:
        return ""
    try:
        from core.file_hash_cache import get_or_compute_md5
        return str(get_or_compute_md5(settings, p)[0] or "").lower()
    except Exception as exc:  # the hash cache is optional: read the bytes directly
        _LOG.warning("hash cache unavailable for %s: %s", p, exc)
        try:
            return file_md5(p).lower()
        except OSError:
            return ""


def _bind_site_md5(settings: dict, canonical: str, site_md5: str, *, site: str, source_url: str) -> None:
    """A site published another MD5 for this picture: keep it as an alias, never as the card's MD5."""
    try:
        from core.database.storage import bind_site_md5_alias
        bound = bind_site_md5_alias(settings, canonical, site_md5, site=site, source_url=source_url)
        if not bound.get("ok"):
            _LOG.info("SITE MD5 ALIAS not bound: site_md5=%s canonical=%s reason=%s",
                      site_md5, canonical, bound.get("reason"))
    except Exception as exc:
        _LOG.warning("SITE MD5 ALIAS %s -> %s failed: %s", site_md5, canonical, exc)


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
    site_posts: list | None = None,
) -> dict:
    """Store one incoming result and enforce the exact-MD5 single-file invariant.

    If a byte-identical live media row already exists, the incoming sources and
    tags are added to that canonical row and a redundant newly-created file in
    Local Booru output is discarded. It is *not* sent to Trash, because it was
    never a second library object; it was a transient copy of an existing one.

    The card's MD5 is always the MD5 of the file's bytes.  ``hash_md5`` from the caller
    (often the MD5 a site published for the post) is only a hint: when it differs from the
    bytes, it is stored as a site-MD5 alias of the card.

    ``site_posts``: what the sites said about the card's posts — parent, uploader, source links
    (``core.site_posts``); kept with the card the file ends up in.
    """
    p = Path(media_path)
    claimed = str(hash_md5 or "").strip().lower()
    md5 = _bytes_md5(settings, p) or claimed
    site_md5 = claimed if claimed and claimed != md5 else ""
    asked = time.perf_counter()
    with IMPORT_LOCK:
        taken = time.perf_counter()
        STEPS.add(IMPORT_WAIT, taken - asked)       # another writer (or «Обслуживание») had the library
        result = _register_locked(
            settings, p, md5, tags=tags, groups=groups, sources=sources, status=status,
            original_path=original_path, raw=raw, post_url=post_url, file_url=file_url, site=site,
            source_tag_groups=source_tag_groups, origin=origin, merge_existing=merge_existing,
            site_posts=site_posts,
        )
        canonical = str(result.get("canonical_path") or "")
        if site_md5 and canonical:
            _bind_site_md5(settings, canonical, site_md5, site=site, source_url=post_url or file_url)
        STEPS.add(BASE, time.perf_counter() - taken)
    if generate_thumbnail:
        make_card_preview(settings, canonical)
    return result


def make_card_preview(settings: dict, canonical) -> None:
    """The card's preview in the gallery's size (made at import so the gallery shows it at once).  A caller that
    holds a lock others wait for (the parser's writes) makes it after letting go: a big picture or a video takes
    a second or more."""
    if not canonical or not Path(canonical).exists():
        return
    try:
        from core.image_safe import card_thumbnail_size, safe_thumbnail_path
        safe_thumbnail_path(str(canonical), *card_thumbnail_size(settings))
    except Exception as exc:  # a preview is a convenience; the import itself is done
        _LOG.warning("thumbnail for %s failed: %s", canonical, exc)


def _register_locked(
    settings: dict,
    p: Path,
    md5: str,
    *,
    tags: Iterable[str] | None,
    groups: dict | None,
    sources: Iterable[str] | None,
    status: str,
    original_path: str,
    raw: dict | None,
    post_url: str,
    file_url: str,
    site: str,
    source_tag_groups: list[dict] | None,
    origin: str,
    merge_existing: bool,
    site_posts: list | None = None,
) -> dict:
    source_list = [str(x).strip() for x in (sources or []) if str(x).strip()]
    if post_url and post_url not in source_list:
        source_list.append(post_url)
    if file_url and file_url not in source_list:
        source_list.append(file_url)

    desired_bucket = _desired_bucket(status)
    restore_lifecycle_kwargs = None
    if desired_bucket in {"found", "partial_match"} and not _live_canonical_path(settings, md5, status, p):
        try:
            from core.library_lifecycle import import_lifecycle_kwargs as _lb_import_lifecycle_kwargs
            restore_lifecycle_kwargs = _lb_import_lifecycle_kwargs(settings, origin)
        except Exception:
            restore_lifecycle_kwargs = None
        _restore_stale_exact_row_to_path(settings, md5, p, status, restore_lifecycle_kwargs)

    # A live canonical exact copy wins over any obsolete deleted-MD5 marker.
    # This is the critical path for one image being found on multiple sources.
    canonical_path = _live_canonical_path(settings, md5, status, p)
    if not canonical_path and md5 and not _is_already_live_library_path(settings, p):
        held = _hold_trashed(settings, md5, p, source_list, status=status, origin=origin)
        if held is not None:
            return held
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
            site_posts=site_posts,
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

    if not already_live_current and not p.is_file():
        # Never a card without a file (subscriptions used to register a download that had just
        # been moved to Trash as a visual duplicate: a «ghost» card of size 0).
        _LOG.warning("IMPORT SKIPPED: no file at %s (origin=%s)", p, origin)
        return {"action": "missing_file", "image_id": 0, "md5": md5}
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
        site_posts=site_posts,
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
    return {"action": "imported", "image_id": int(image_id or 0), "md5": md5, "canonical_path": str(p), "source_added": len(added_sources), "nomatch_cleanup": nomatch_cleanup}
