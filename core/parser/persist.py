"""Write parser results into the library through the existing storage layer.

The parser's own lock serializes "copy into output + register" between parser threads (two input
files with identical bytes go to the same output path).  The registration itself is serialized with
every other writer (grabber, subscriptions, extension) by import_pipeline.IMPORT_LOCK; the file copy,
which can take long for big videos, stays outside that shared lock.  The card's preview is made after
the parser's lock is let go (v5.28): a big picture or a video takes a second or more, and every other
file's write waited for it.  Every step is timed (core/work_timing.py: the run says where its time goes).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from core.work_timing import BRAK, COPY, PREVIEW, STEPS, VIDEO, WAIT

from core.parser.models import Match, Post
from core.parser.util import host_of
from core.tagger.tag_groups import (
    add_tags_to_groups, empty_tag_groups, filter_numeric_tags, groups_to_tags, merge_tag_groups,
)

_WRITE_LOCK = threading.Lock()
FOUND_STATUSES = {"found", "tagged", "partial", "partial_match", "downloaded_found"}


def processed_status(settings: dict, original_path: str) -> str:
    from core.database.storage import processed_status as _status
    return str(_status(settings, original_path) or "").lower()


def library_bundle_for_md5(settings: dict, md5: str) -> dict | None:
    """Tags already stored for identical bytes in the gallery (no network needed)."""
    from core.database.connection import db
    from core.database.storage import found_media_path_by_md5
    from core.tagger.local_phash_match import load_local_match_bundle
    canonical = found_media_path_by_md5(settings, md5)
    if not canonical or not Path(canonical).exists():
        return None
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT id FROM images WHERE path=? AND deleted=0 LIMIT 1", (str(canonical),)).fetchone()
    if not row:
        return None
    bundle = load_local_match_bundle(settings, int(row["id"]))
    return bundle if bundle.get("tags") else None


def matches_from_bundle(bundle: dict, method: str) -> list:
    out = []
    for entry in bundle.get("source_tag_groups") or []:
        url = str(entry.get("url") or "")
        groups = empty_tag_groups()
        for key, values in (entry.get("groups") or {}).items():
            add_tags_to_groups(groups, key, values)
        if url and groups_to_tags(groups):
            out.append(Match(Post(site=host_of(url) or "library", label=host_of(url) or "библиотека",
                                  post_id="", url=url, groups=groups), method=method, verified="library"))
    if not out:
        groups = empty_tag_groups()
        for key, values in (bundle.get("groups") or {}).items():
            add_tags_to_groups(groups, key, values)
        if not groups_to_tags(groups):
            add_tags_to_groups(groups, "general", bundle.get("tags") or [])
        sources = [u for u in bundle.get("sources") or [] if str(u).startswith("http")]
        out.append(Match(Post(site="library", label="библиотека", post_id="", url=sources[0] if sources else "",
                              groups=groups), method=method, verified="library"))
    return out


def save_found(settings: dict, path: Path, matches: list, *, md5: str, original_path: str,
               still: Callable[[], bool] | None = None) -> dict:
    """The file's tags from ``matches`` into the library.  ``still()`` — checked under the lock of the parser's
    writes, right before the write: False — the file was settled meanwhile (another writer found it), nothing is
    written ({"ok": False, "gone": True})."""
    from core.import_pipeline import make_card_preview, register_media_import
    from core.nomatch_db import remove_nomatch
    from core.tagger.results import copy_result_files, valid_archived_media_path

    # The library rebuilds the visible tags from the per-site sets, so the numeric
    # filter must be applied to every site's set, not only to the merged one.
    cleaned = [(m, _filtered_groups(settings, m.post.groups)) for m in matches]
    groups = merge_tag_groups([g for _m, g in cleaned])
    tags = groups_to_tags(groups)
    if not tags:
        return {"ok": False, "reason": "после фильтров не осталось тегов"}
    lines, source_groups, seen = [], [], set()
    for m, g in cleaned:
        if not m.post.url or m.post.url in seen:
            continue
        seen.add(m.post.url)
        lines.append(f"{m.method} {m.post.label} {m.post.url}")
        if groups_to_tags(g):
            source_groups.append({"url": m.post.url, "groups": g, "method": m.method})
    from core.site_posts import from_post
    site_posts = [row for row in (from_post(m.post) for m in matches) if row]
    asked = time.perf_counter()
    with _WRITE_LOCK:
        STEPS.add(WAIT, time.perf_counter() - asked)
        if still is not None and not still():
            return {"ok": False, "gone": True, "reason": "файл тем временем уже записан другим путём"}
        with STEPS.timed(COPY):
            archived = copy_result_files(settings, path, "tagged")
        if not valid_archived_media_path(archived):
            return {"ok": False, "reason": "не удалось скопировать файл в рабочую библиотеку"}
        _video_length(archived)
        result = register_media_import(
            settings, archived, tags=tags, groups=groups, sources=lines, status="found",
            original_path=str(original_path), hash_md5=md5 or None, origin="tagger",
            source_tag_groups=source_groups, merge_existing=True, site_posts=site_posts,
            generate_thumbnail=False,
        )
        # register_media_import already removed the stale NO_MATCH copy by MD5;
        # here only the exact rows of this input are closed.
        for key in {str(original_path), str(path)}:
            remove_nomatch(key, settings=settings)
    with STEPS.timed(PREVIEW):              # after the lock: the other files' writes do not wait for it
        make_card_preview(settings, str((result or {}).get("canonical_path") or archived))
    return {"ok": True, "tags": len(tags), "result": result}


def _video_length(path) -> None:
    """A video's length and size, read before the shared import lock is taken (the base keeps them; read there, every
    other writer waited for it)."""
    from core.video_probe import is_video_name, probe
    if not is_video_name(path):
        return
    with STEPS.timed(VIDEO):
        try:
            probe(path)
        except Exception as exc:  # the base reads it again (and says what is wrong): only the order changed here
            import logging
            logging.getLogger("local_booru.parser").warning("video length of %s not read: %s", path, exc)


def _filtered_groups(settings: dict, groups: dict) -> dict:
    out = {key: list(values or []) for key, values in (groups or {}).items()}
    if settings.get("ignore_numeric_tags"):
        for key in out:
            out[key] = filter_numeric_tags(out[key], True)
    return out


def save_nomatch(settings: dict, path: Path, *, original_path: str, existing_media: str = "",
                 source_only: dict | None = None, log=None) -> dict:
    from core.nomatch_db import upsert_nomatch
    from core.tagger.results import copy_result_files

    visual = {}
    try:
        from core.visual_status import classify_nomatch_if_enabled
        info = classify_nomatch_if_enabled(existing_media or path, settings)
        if info and not info.get("error"):
            visual = {
                "visual_status": str(info.get("visual_status") or ""),
                "visual_confidence": float(info.get("visual_confidence") or 0.0),
                "visual_model": str(info.get("visual_model") or ""),
                "visual_checked_at": int(info.get("visual_checked_at") or 0),
            }
    except Exception as exc:  # the classifier is optional (torch may be missing)
        if log:
            log(f"  классификатор «фото/рисунок» недоступен: {type(exc).__name__}: {exc}")
    asked = time.perf_counter()
    with _WRITE_LOCK:
        STEPS.add(WAIT, time.perf_counter() - asked)
        with STEPS.timed(BRAK):
            if processed_status(settings, original_path) in FOUND_STATUSES:
                return {"ok": False, "status": "skip", "reason": "файл уже найден другим путём"}
            media = existing_media if existing_media and Path(existing_media).exists() else copy_result_files(settings, path, "nomatch")
            if not media:
                return {"ok": False, "status": "error",
                        "reason": "копия в Брак не создана (нет места на диске или файл в корзине)"}
            kwargs = dict(visual)
            if source_only and (source_only.get("url") or source_only.get("label")):
                kwargs.update(reason="source_only", source_url=str(source_only.get("url") or ""),
                              source_label=str(source_only.get("label") or ""),
                              source_similarity=float(source_only.get("similarity") or 0.0))
            upsert_nomatch(original_path, settings=settings, media_path=media, **kwargs)
    return {"ok": True}
