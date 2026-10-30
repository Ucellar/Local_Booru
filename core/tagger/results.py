"""Managed-output helpers shared by the parser, NO_MATCH page and importers.

Moved here from the old 12k-line core/tagger/engine.py without behaviour changes.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from core.paths import CACHE_DIR, ERROR_LOG_FILE, ensure_output_base
from core.redaction import sanitize_text
from core.services.media_storage_service import copy_into_managed, delete_bucket_artifacts, unlink_managed
from core.tagger.hashing import file_md5
from core.tagger.tag_groups import groups_to_tags, unique_keep_order

COPY_SUFFIX_RE = re.compile(r"\s*\((\d+)\)$")


def has_copy_suffix(path) -> bool:
    """'name (1).jpg' — Windows duplicate copies, skipped by the parser."""
    try:
        return bool(COPY_SUFFIX_RE.search(Path(path).stem))
    except (TypeError, ValueError):
        return False


def result_output_base(settings):
    return ensure_output_base(settings.get("output_dir"), settings.get("root"))


def result_bucket_name(status):
    if status in ("tagged", "found"):
        return "found"
    if status in ("partial", "partial_match"):
        return "partial_match"
    return "no_match"


def result_paths_for(settings, img, status):
    img = Path(img)
    base_out = result_output_base(settings)
    bucket_dir = base_out / result_bucket_name(status)
    session_sub = settings.get("session_folder", "")
    media_dir = bucket_dir / "media" / session_sub if session_sub else bucket_dir / "media"
    return {
        "base": base_out,
        "bucket": bucket_dir,
        "media": media_dir,
        "tags": bucket_dir / "tags",
        "source": bucket_dir / "source",
        "searched": bucket_dir / "searched",
        "cache": bucket_dir / "cache",
        "media_file": media_dir / img.name,
        "searched_file": bucket_dir / "searched" / (img.stem + ".searched.json"),
    }


def output_processed_status(settings, img):
    """Processing status from SQLite (the only source of truth)."""
    from core.database.storage import processed_status
    return processed_status(settings, img)


def valid_archived_media_path(path) -> bool:
    if not path:
        return False
    p = Path(path)
    return (p.exists() and p.is_file()) or not p.is_absolute()


def copy_result_files(settings, img, status):
    """Copy the source file into the managed output, one live copy per exact MD5.

    A FOUND result reuses the already-live canonical copy of identical bytes.
    NO_MATCH copies are registered here because nothing else writes them.
    """
    img = Path(img)
    paths = result_paths_for(settings, img, status)
    bucket = result_bucket_name(status)
    from core.file_hash_cache import get_or_compute_md5
    md5 = get_or_compute_md5(settings, img)[0] if img.exists() else ""
    if bucket == "found" and md5:
        from core.database.storage import found_media_path_by_md5
        existing = found_media_path_by_md5(settings, md5)
        if existing and Path(existing).exists():
            return Path(existing)
    for d in (paths["media"], paths["cache"]):
        d.mkdir(parents=True, exist_ok=True)
    from core.preflight import ensure_space_for_write
    ok, message = ensure_space_for_write(settings, paths["media_file"], img.stat().st_size if img.exists() else 0)
    if not ok:
        append_error_log("COPY STOP NO DISK SPACE: " + message)
        return None
    target = copy_into_managed(settings, img, paths["media_file"], operation="tagger.copy_result", hash_md5=md5 or None)
    if bucket == "no_match":
        # A file the user put into Trash stays there (the copy is removed again), unless the
        # policy «Вернуть в «Новые»» brings the trashed card back onto this copy.
        from core.import_pipeline import trash_hold
        if trash_hold(settings, md5, target, status=bucket, origin="tagger") is not None:
            return None
        from core.database.storage import ensure_image
        ensure_image(settings, target, status=bucket, original_path=str(img), hash_md5=md5 or None)
    return target


def cleanup_archived_result(settings, img, statuses=("nomatch",)):
    """Remove the older output copy of *this* file (e.g. from NO_MATCH) after it was found.

    Old builds named output copies by the plain file name, so another picture may
    sit under the same name: only the file itself or byte-identical copies go.
    """
    from core.file_hash_cache import get_or_compute_md5
    img = Path(img)
    wanted = get_or_compute_md5(settings, img)[0] if img.exists() else ""
    for st in statuses:
        candidate = result_paths_for(settings, img, st)["media"] / img.name
        if not candidate.exists():
            continue
        same_file = candidate.resolve() == img.resolve()
        if not same_file and (not wanted or get_or_compute_md5(settings, candidate)[0] != wanted):
            continue
        unlink_managed(settings, candidate, operation="tagger.cleanup_archived_result")
        delete_bucket_artifacts(settings, candidate, operation="tagger.cleanup_legacy_output_artifacts")


def save_found_metadata(settings, img, tags, source_url="", groups=None, status="tagged", *,
                        archived_media_path=None, hash_md5=None, source_tag_groups=None):
    from core.import_pipeline import register_media_import
    img = Path(img)
    paths = result_paths_for(settings, img, status)
    media_path = Path(archived_media_path) if archived_media_path else paths["media_file"]
    if not groups or not groups_to_tags(groups):
        groups = {"artist": [], "character": [], "copyright": [], "general": list(tags or []), "meta": []}
    return register_media_import(
        settings, media_path, tags=tags or [], groups=groups,
        sources=[source_url] if source_url else [], status=result_bucket_name(status),
        original_path=str(img), hash_md5=hash_md5, origin="tagger",
        source_tag_groups=source_tag_groups, merge_existing=True,
    )


def promote_manual_match(settings, img, tags, source_url="", groups=None):
    """NO_MATCH file + tags from a URL the user pasted -> FOUND."""
    from core.nomatch_db import remove_nomatch
    img = Path(img)
    tags = unique_keep_order(tags)
    archived = copy_result_files(settings, img, "tagged")
    if not valid_archived_media_path(archived):
        append_error_log(f"PROMOTE MANUAL MATCH FAILED: managed copy missing for {img}")
        return False
    source_groups = [{"url": source_url, "groups": groups or {"general": list(tags)}, "method": "manual_url"}] if source_url else []
    save_found_metadata(settings, img, tags, source_url, groups, status="tagged",
                        archived_media_path=archived, source_tag_groups=source_groups)
    remove_nomatch(img, settings=settings)
    cleanup_archived_result(settings, img, ("nomatch", "partial"))
    return True


def append_error_log(msg):
    """Append to the rotating error log (10 MB x 5)."""
    try:
        ERROR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if ERROR_LOG_FILE.exists() and ERROR_LOG_FILE.stat().st_size >= 10 * 1024 * 1024:
            for idx in range(4, 0, -1):
                src = ERROR_LOG_FILE.with_name(ERROR_LOG_FILE.name + f".{idx}")
                if src.exists():
                    src.replace(ERROR_LOG_FILE.with_name(ERROR_LOG_FILE.name + f".{idx + 1}"))
            ERROR_LOG_FILE.replace(ERROR_LOG_FILE.with_name(ERROR_LOG_FILE.name + ".1"))
        with ERROR_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {sanitize_text(msg)}\n")
    except OSError:
        pass  # the error log itself must never raise into callers


def cleanup_preview_cache(settings=None):
    """Keep generated preview/frame files bounded by age and count."""
    settings = settings or {}
    max_files = int(settings.get("max_preview_cache_files", settings.get("max_thumb_cache_files", 20000)) or 20000)
    max_age_days = int(settings.get("preview_cache_max_age_days", settings.get("thumb_cache_max_age_days", 90)) or 90)
    now = time.time()
    files = []
    for root in (CACHE_DIR / "preview_cache", CACHE_DIR / "thumbs"):
        if not root.exists():
            continue
        for p in root.rglob("*"):
            try:
                if p.is_file():
                    st = p.stat()
                    if max_age_days > 0 and now - st.st_mtime > max_age_days * 86400:
                        p.unlink(missing_ok=True)
                    else:
                        files.append((p, st.st_mtime))
            except OSError:
                continue
    if max_files > 0 and len(files) > max_files:
        for p, _mtime in sorted(files, key=lambda x: x[1])[: len(files) - max_files]:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                continue


def file_md5_lower(path) -> str:
    return file_md5(path).lower()
