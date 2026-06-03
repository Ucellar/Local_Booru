"""Low-risk performance telemetry for Local Booru.

Only slow operations are persisted as JSON lines.  Telemetry is intentionally
small and independent of SQLite so measuring a database bottleneck cannot create
another database bottleneck or mutate the library.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from core.paths import LOGS_DIR

PERFORMANCE_LOG_FILE = Path(LOGS_DIR) / "performance.jsonl"
_LOCK = threading.RLock()
_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 5


def _threshold_ms(settings: dict | None, default: float = 100.0) -> float:
    try:
        return max(1.0, float((settings or {}).get("performance_slow_ms", default) or default))
    except Exception:
        return default


def _rotate_locked() -> None:
    try:
        if not PERFORMANCE_LOG_FILE.exists() or PERFORMANCE_LOG_FILE.stat().st_size < _MAX_BYTES:
            return
        for index in range(_BACKUPS - 1, 0, -1):
            older = PERFORMANCE_LOG_FILE.with_suffix(f".jsonl.{index}")
            newer = PERFORMANCE_LOG_FILE.with_suffix(f".jsonl.{index + 1}")
            if older.exists():
                older.replace(newer)
        PERFORMANCE_LOG_FILE.replace(PERFORMANCE_LOG_FILE.with_suffix(".jsonl.1"))
    except Exception:
        pass


def record_slow_operation(name: str, duration_ms: float, *, settings: dict | None = None, detail: dict[str, Any] | None = None, force: bool = False) -> bool:
    """Append one slow operation to the bounded log; returns whether written."""
    threshold = _threshold_ms(settings)
    if not force and float(duration_ms) < threshold:
        return False
    item = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "operation": str(name),
        "duration_ms": round(float(duration_ms), 2),
        "detail": detail or {},
    }
    try:
        with _LOCK:
            PERFORMANCE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            _rotate_locked()
            with PERFORMANCE_LOG_FILE.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


@contextmanager
def timed(name: str, settings: dict | None = None, **detail: Any) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        record_slow_operation(name, (time.perf_counter() - started) * 1000.0, settings=settings, detail=detail)


def recent_slow_operations(limit: int = 100) -> list[dict[str, Any]]:
    """Read recent entries without raising or touching application data."""
    items: list[dict[str, Any]] = []
    paths = [PERFORMANCE_LOG_FILE] + [PERFORMANCE_LOG_FILE.with_suffix(f".jsonl.{i}") for i in range(1, _BACKUPS + 1)]
    for path in paths:
        try:
            if not path.exists():
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        items.append(obj)
                        if len(items) >= max(1, int(limit)):
                            return items
                except Exception:
                    continue
        except Exception:
            continue
    return items
