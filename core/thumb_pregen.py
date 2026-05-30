from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Callable

_log = logging.getLogger("local_booru.thumbs")

def pregen_thumbnails(paths: Iterable[Path], settings: dict, *, progress: Callable[[int, int], None] | None = None, stop_check=None) -> dict:
    """Generate persistent thumbnails in data/cache/thumbs without touching QPixmap."""
    items = [Path(p) for p in paths]
    total = len(items)
    if not total:
        return {"total": 0, "done": 0, "errors": 0}
    w = int((settings or {}).get("thumb_cache_w", 256) or 256)
    h = int((settings or {}).get("thumb_cache_h", 256) or 256)
    cw = int((settings or {}).get("thumb_cache_card_w", 240) or 240)
    ch = int((settings or {}).get("thumb_cache_card_h", 220) or 220)
    workers = max(1, min(int((settings or {}).get("thumb_pregen_workers", 2) or 2), 4))
    done = errors = 0
    def one(path: Path):
        from core.image_safe import safe_thumbnail_path
        safe_thumbnail_path(path, w, h)
        if (cw, ch) != (w, h):
            safe_thumbnail_path(path, cw, ch)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="thumb-pregen") as ex:
        futs = [ex.submit(one, x) for x in items]
        for fut in as_completed(futs):
            if stop_check and stop_check():
                break
            try:
                fut.result(); done += 1
            except Exception as e:
                errors += 1; _log.debug("thumb pregen failed: %s", e)
            if progress and (done + errors) % 25 == 0:
                progress(done + errors, total)
    if progress:
        progress(done + errors, total)
    return {"total": total, "done": done, "errors": errors}
