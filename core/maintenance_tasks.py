"""Small background-friendly maintenance jobs for the local library."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from core.database.connection import db


def _live_paths(settings: dict) -> list[Path]:
    with db(settings, readonly=True) as con:
        return [Path(r["path"]) for r in con.execute("SELECT path FROM images WHERE deleted=0 ORDER BY indexed_at DESC").fetchall()]


def repair_missing_thumbnails(settings: dict, progress: Callable[[str], None] | None = None, stop_check=None) -> dict:
    from core.image_safe import safe_thumbnail_path
    paths = _live_paths(settings)
    created = errors = skipped = 0
    w = max(256, int(settings.get("thumb_cache_card_w", 240) or 240) * 2)
    h = max(256, int(settings.get("thumb_cache_card_h", 220) or 220) * 2)
    for idx, p in enumerate(paths, 1):
        if stop_check and stop_check():
            break
        if not p.exists():
            skipped += 1
            continue
        try:
            safe_thumbnail_path(str(p), w, h)
            created += 1
        except Exception:
            errors += 1
        if progress and idx % 100 == 0:
            progress(f"Превью: {idx}/{len(paths)}")
    return {"checked": len(paths), "created": created, "missing": skipped, "errors": errors}


def validate_recent_media(settings: dict, limit: int = 1000, progress: Callable[[str], None] | None = None) -> dict:
    from core.stability import check_recent_media_after_crash
    return check_recent_media_after_crash(settings, log=progress, limit=int(limit))
