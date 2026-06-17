"""Local/offline worker limits for CPU and disk bound jobs.

Network queues keep their own per-host rate limits.  This module only controls
local work that does not talk to the internet: hashing, pHash/preview work,
video frame extraction, cache preparation and local DB read helpers.
"""
from __future__ import annotations

import os
from typing import Any


def cpu_count(default: int = 8) -> int:
    try:
        return max(1, int(os.cpu_count() or default))
    except Exception:
        return max(1, int(default or 1))


def as_int(value: Any, default: int) -> int:
    try:
        if isinstance(value, str) and value.strip().lower() in {"", "auto", "default"}:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def total_local_workers(settings: dict | None, default: int | None = None) -> int:
    settings = settings or {}
    if default is None:
        default = min(12, max(4, cpu_count()))
    raw = settings.get("local_total_workers", settings.get("developer_thread_total_workers", default))
    value = as_int(raw, int(default))
    return max(1, min(value, 64))


def local_workers(settings: dict | None, key: str, default: int, *, minimum: int = 1, maximum: int | None = None, total_cap: bool = True) -> int:
    """Return a safe worker count for a named local/offline queue.

    ``total_cap`` intentionally caps per-queue worker counts at the global local
    pool ceiling.  It is not a hard sum across all queues; it prevents accidental
    values such as 999 from exploding a single service while still allowing
    independent services to run concurrently.
    """
    settings = settings or {}
    value = as_int(settings.get(key, default), int(default))
    cap = int(maximum or 64)
    if total_cap:
        cap = min(cap, total_local_workers(settings))
    return max(int(minimum), min(int(value), int(cap)))


def apply_legacy_thread_aliases(settings: dict | None) -> dict:
    """Fill old keys from the new thread menu without deleting user values."""
    s = settings if isinstance(settings, dict) else {}
    if "local_tagger_workers" in s and "tagger_parallel_workers" not in s:
        s["tagger_parallel_workers"] = s.get("local_tagger_workers")
    if "local_thumb_workers" in s and "thumb_threads" not in s:
        s["thumb_threads"] = s.get("local_thumb_workers")
    if "local_thumb_pregen_workers" in s and "thumb_pregen_workers" not in s:
        s["thumb_pregen_workers"] = s.get("local_thumb_pregen_workers")
    if "local_background_workers" in s and "task_max_workers" not in s:
        s["task_max_workers"] = s.get("local_background_workers")
    return s


def snapshot(settings: dict | None) -> dict[str, int]:
    """Human/debug summary of effective local worker limits."""
    s = settings or {}
    return {
        "total": total_local_workers(s),
        "scan": local_workers(s, "local_scan_workers", 2, maximum=16),
        "hash": local_workers(s, "local_hash_workers", 4, maximum=32),
        "image": local_workers(s, "local_image_workers", 4, maximum=32),
        "video": local_workers(s, "local_video_workers", 2, maximum=8),
        "db_read": local_workers(s, "local_db_read_workers", 2, maximum=16),
        "tagger": local_workers(s, "tagger_parallel_workers", int(s.get("local_tagger_workers", 1) or 1), maximum=16),
        "thumb_ui": local_workers(s, "thumb_threads", int(s.get("local_thumb_workers", 3) or 3), maximum=16),
        "thumb_pregen": local_workers(s, "thumb_pregen_workers", int(s.get("local_thumb_pregen_workers", 2) or 2), maximum=16),
        "background": local_workers(s, "task_max_workers", int(s.get("local_background_workers", 2) or 2), maximum=16),
        "visual_nomatch": local_workers(s, "visual_nomatch_workers", 2, maximum=8),
    }
