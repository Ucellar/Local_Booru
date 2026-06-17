"""Parallel local preflight cache warm-up for parser input files.

This is deliberately offline-only: it computes/caches file MD5 and, when safe,
pHash/video-preview signatures so network lanes can start without serially
waiting on disk/CPU work for every file.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from typing import Iterable, Callable

from core.local_parallel import local_workers

_log = logging.getLogger("local_booru.local_preflight")


def start_parser_local_preflight(
    files: Iterable[Path],
    settings: dict | None,
    *,
    log: Callable[[str], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> threading.Thread | None:
    """Start a daemon thread that warms parser hash caches in parallel.

    It returns immediately.  The parser conveyor may still process files while
    the warm-up is running; cache hits will appear as soon as workers finish.
    """
    settings = settings or {}
    if not bool(settings.get("local_preflight_enabled", True)):
        return None
    items = [Path(p) for p in files]
    if not items:
        return None
    total_workers = local_workers(settings, "local_hash_workers", 4, maximum=32)
    image_workers = local_workers(settings, "local_image_workers", 4, maximum=32)
    max_workers = max(1, min(total_workers, image_workers, 32))
    compute_phash = bool(settings.get("local_preflight_phash", True))
    # Avoid flooding the executor with 500k Future objects at once.
    backlog = max_workers * 4

    def emit(msg: str) -> None:
        try:
            if log:
                log(str(msg))
        except Exception:
            pass

    def should_stop() -> bool:
        try:
            return bool(stop_check and stop_check())
        except Exception:
            return False

    def one(path: Path) -> tuple[bool, bool]:
        md5_done = phash_done = False
        try:
            from core.file_hash_cache import get_or_compute_md5
            get_or_compute_md5(settings, path)
            md5_done = True
        except Exception as e:
            _log.debug("local preflight md5 failed: %s: %s", path, e)
        if compute_phash:
            try:
                from core.file_hash_cache import get_or_compute_phash
                from core.tagger.engine import file_phash, video_frame_image
                search_path = video_frame_image(path)
                get_or_compute_phash(settings, search_path, file_phash)
                phash_done = True
            except Exception as e:
                _log.debug("local preflight phash failed: %s: %s", path, e)
        return md5_done, phash_done

    def runner() -> None:
        done = errors = 0
        emit(f"LOCAL PREFLIGHT: files={len(items)} workers={max_workers} phash={'on' if compute_phash else 'off'}")
        pending = {}
        it = iter(items)
        try:
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="local-preflight") as ex:
                def submit_more() -> bool:
                    if should_stop():
                        return False
                    try:
                        path = next(it)
                    except StopIteration:
                        return False
                    pending[ex.submit(one, path)] = path
                    return True

                while len(pending) < backlog and submit_more():
                    pass
                while pending and not should_stop():
                    ready, _ = wait(list(pending.keys()), timeout=0.5, return_when=FIRST_COMPLETED)
                    if not ready:
                        continue
                    for fut in ready:
                        path = pending.pop(fut, None)
                        try:
                            fut.result()
                            done += 1
                        except Exception as e:
                            errors += 1
                            _log.debug("local preflight failed: %s: %s", path, e)
                        while len(pending) < backlog and submit_more():
                            pass
                        if (done + errors) % 500 == 0:
                            emit(f"LOCAL PREFLIGHT: cached={done} errors={errors}/{len(items)}")
        finally:
            emit(f"LOCAL PREFLIGHT DONE: cached={done} errors={errors}/{len(items)}")

    th = threading.Thread(target=runner, daemon=True, name="local-preflight")
    th.start()
    return th
