"""Thresholded low-impact WAL checkpoint for non-parser writers.

The parser already performs a PASSIVE checkpoint every configured number of
site checks. This module supplements it after Browser Import, Downloader,
integrity/recovery and ordinary UI writes. It never uses RESTART/TRUNCATE and
never treats a busy/incomplete PASSIVE result as an error.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.database.connection import db_path
from core.light_backup import passive_checkpoint_sqlite
from core.maintenance_coordinator import get_maintenance_coordinator

_LOG = logging.getLogger("local_booru")


def _threshold_bytes(settings: dict) -> int:
    try:
        mb = int((settings or {}).get("sqlite_idle_checkpoint_threshold_mb", 64) or 64)
    except Exception:
        mb = 64
    return max(1, min(4096, mb)) * 1024 * 1024


def wal_checkpoint_probe(settings: dict) -> dict[str, Any]:
    """Cheap sidecar-size check; does not open SQLite or create the WAL."""
    path = Path(db_path(settings))
    wal = Path(str(path) + "-wal")
    threshold = _threshold_bytes(settings)
    try:
        size = int(wal.stat().st_size) if wal.is_file() else 0
    except OSError as exc:
        return {
            "needed": False,
            "reason": "wal_stat_error",
            "error": str(exc),
            "db": str(path),
            "wal": str(wal),
            "wal_bytes": 0,
            "threshold_bytes": threshold,
        }
    return {
        "needed": bool(size >= threshold),
        "reason": "threshold_reached" if size >= threshold else "below_threshold",
        "db": str(path),
        "wal": str(wal),
        "wal_bytes": size,
        "threshold_bytes": threshold,
    }


def run_idle_passive_checkpoint(
    settings: dict,
    *,
    progress=None,
    stop_check=None,
) -> dict[str, Any]:
    """Run one fail-soft PASSIVE checkpoint only when idle and above threshold."""
    settings = dict(settings or {})
    result = wal_checkpoint_probe(settings)
    result.update({
        "attempted": False,
        "ok": True,
        "busy": 0,
        "log_pages": 0,
        "checkpointed_pages": 0,
        "wal_bytes_after": int(result.get("wal_bytes") or 0),
    })
    if not bool(settings.get("sqlite_idle_checkpoint_enabled", True)):
        result["reason"] = "disabled"
        return result
    if not result.get("needed"):
        return result
    if stop_check and stop_check():
        result["reason"] = "cancelled"
        return result

    coordinator = get_maintenance_coordinator()
    min_idle = max(0.0, float(settings.get("sqlite_idle_checkpoint_min_idle_seconds", 15) or 15))
    decision = coordinator.can_run(settings, mode="idle", min_idle_seconds=min_idle)
    if not decision.allowed:
        result["reason"] = str(decision.reason or "not_idle")
        return result

    with coordinator.maintenance_job(
        "wal_checkpoint",
        stop_check=stop_check,
        progress=progress,
    ):
        # Permission and threshold are rechecked after acquiring the maintenance
        # lane. A parser/user action may have started while this task waited.
        decision = coordinator.can_run(settings, mode="idle", min_idle_seconds=min_idle)
        if not decision.allowed:
            result["reason"] = str(decision.reason or "not_idle")
            return result
        fresh = wal_checkpoint_probe(settings)
        result.update(fresh)
        if not fresh.get("needed"):
            return result
        if stop_check and stop_check():
            result["reason"] = "cancelled"
            return result
        if progress:
            progress("SQLite: PASSIVE checkpoint WAL…")
        checkpoint = passive_checkpoint_sqlite(settings, optimize=False)
        result["attempted"] = True
        result["ok"] = bool(checkpoint.get("ok", False))
        row = tuple(checkpoint.get("checkpoint") or ())
        padded = tuple(row) + (0, 0, 0)
        result["busy"] = int(padded[0] or 0)
        result["log_pages"] = int(padded[1] or 0)
        result["checkpointed_pages"] = int(padded[2] or 0)
        if not result["ok"]:
            result["reason"] = "checkpoint_failed"
        else:
            result["reason"] = "passive_busy" if result["busy"] else "checkpointed"
        after = wal_checkpoint_probe(settings)
        result["wal_bytes_after"] = int(after.get("wal_bytes") or 0)
        if checkpoint.get("reason"):
            result["checkpoint_reason"] = str(checkpoint.get("reason"))

    # busy!=0 is expected when a reader holds an older end mark. PASSIVE did as
    # much as it safely could and will be retried by the next idle timer tick.
    _LOG.info(
        "SQLite WAL checkpoint PASSIVE idle: before=%s after=%s busy=%s log_pages=%s checkpointed_pages=%s ok=%s",
        result.get("wal_bytes"), result.get("wal_bytes_after"), result.get("busy"),
        result.get("log_pages"), result.get("checkpointed_pages"), result.get("ok"),
    )
    return result
