"""«Брак → Повторный поиск»: the chosen no-match files go through the parser once more.

The parser takes a folder, so the files are staged in ``<settings>/cache/nomatch_retry_queue``
(hard links, or copies on another disk) with a manifest that tells the parser each file's original
path and its Брак copy (``_retry_nomatch_manifest.json``, read by ``ParserRunner.collect``).  The
user's source folder setting is never changed: the run gets its own copy of the settings.
Used by the web page and the old Qt «Брак» page alike.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

QUEUE_DIR = "nomatch_retry_queue"
MANIFEST = "_retry_nomatch_manifest.json"


def _active_media(settings: dict) -> Path:
    from core.tagger.results import result_output_base
    return result_output_base(settings) / "no_match" / "media"


def restore_missing_copies(settings: dict, items: list, log=None) -> int:
    """Put back the Брак copies of ``items`` that are not in the current archive (an old archive,
    a deleted copy): the retry and the gallery need them there.  Returns how many were made."""
    from core.nomatch_db import update_nomatch_media_path
    from core.tagger.results import copy_result_files
    active = _active_media(settings)
    restored = 0
    for item in items:
        original_raw = str(item.get("original_path", "") or "").strip()
        visible_raw = str(item.get("path", "") or "").strip()
        original = Path(original_raw) if original_raw else None
        visible = Path(visible_raw) if visible_raw else None
        stored = Path(item.get("media_path", "") or "") if item.get("media_path") else None
        source = original if original is not None and original.exists() else visible
        if source is None or not source.exists():
            continue
        if stored and stored.exists():
            try:
                if stored.resolve().is_relative_to(active.resolve()):
                    continue
            except (OSError, ValueError):
                if str(stored.resolve()).lower().startswith(str(active.resolve()).lower()):
                    continue
        rebuilt = copy_result_files(settings, source, "nomatch")
        if rebuilt and Path(rebuilt).exists():
            update_nomatch_media_path(original if original is not None else source, rebuilt, settings=settings)
            item.update(media_path=str(rebuilt), path=str(rebuilt), fallback_to_original=False, file_missing=False)
            restored += 1
    if restored and log:
        log(f"Восстановлено копий брака в текущем архиве: {restored}")
    return restored


def _readable_file(item: dict) -> Path | None:
    for raw in (item.get("path"), item.get("media_path"), item.get("original_path")):
        if not raw:
            continue
        p = Path(str(raw))
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def prepare_queue(settings: dict, items: list, *, label: str = "") -> tuple[Path | None, int, list]:
    """(queue folder, files staged, files skipped) for ``items`` — exactly these files."""
    from core.paths import suggested_settings_storage_dir
    chosen = list(items or [])
    if not chosen:
        return None, 0, []
    queue_root = suggested_settings_storage_dir(settings) / "cache" / QUEUE_DIR
    shutil.rmtree(queue_root, ignore_errors=True)
    queue_root.mkdir(parents=True, exist_ok=True)
    used, manifest, skipped = set(), [], []
    for item in chosen:
        src = _readable_file(item)
        if src is None:
            skipped.append(str(item.get("name") or item.get("path") or item.get("original_path") or "<?>"))
            continue
        name, stem, suffix = src.name or "file", src.stem or "file", src.suffix
        dest_name, n = name, 2
        while dest_name.lower() in used or (queue_root / dest_name).exists():
            dest_name = f"{stem}__retry{n}{suffix}"
            n += 1
        used.add(dest_name.lower())
        dst = queue_root / dest_name
        try:
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        except OSError as exc:
            skipped.append(f"{src}: {type(exc).__name__}: {exc}")
            continue
        manifest.append({
            "queue_file": str(dst), "source_file": str(src),
            "original_path": str(item.get("original_path") or ""), "media_path": str(item.get("media_path") or ""),
            "visual_status": str(item.get("visual_status") or ""),
            "visual_confidence": float(item.get("visual_confidence") or 0.0),
            "reason": str(item.get("reason") or ""),
        })
    (queue_root / MANIFEST).write_text(json.dumps({
        "created_at": int(time.time()), "filter": label, "count": len(manifest),
        "items": manifest, "skipped": skipped}, ensure_ascii=False, indent=2), encoding="utf-8")
    return queue_root, len(manifest), skipped


def retry_settings(settings: dict, queue_root: Path, *, label: str = "", count: int = 0) -> dict:
    """The settings of the retry run (a copy: the source folder setting stays as it was)."""
    out = dict(settings)
    out.update({
        "root": str(queue_root), "skip_existing": False, "tag_only_untagged": False, "retry_nomatch": True,
        "retry_nomatch_filter": label, "retry_nomatch_count": int(count), "retry_nomatch_queue_root": str(queue_root),
    })
    out.pop("_parser_explicit_paths", None)
    return out


def items_for_media(settings: dict, media_paths: list) -> list:
    """The active Брак rows whose copies are these files (the gallery's «Брак» cards)."""
    from core.nomatch_db import list_nomatches
    wanted = {os.path.normcase(str(Path(p))) for p in media_paths if p}
    out = []
    for item in list_nomatches(None, settings=settings):
        keys = {os.path.normcase(str(Path(x))) for x in (item.get("media_path"), item.get("path")) if x}
        if keys & wanted:
            out.append(item)
    return out
