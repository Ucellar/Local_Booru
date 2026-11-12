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


PAGE = "page "               # a page of the program that opened slowly: «page /videos/s/…» (``webui/server.py``)
PAGES_READ = 2               # of the log's files looked through for them (the newest: 4 MB at most)


def slow_pages() -> list[dict[str, Any]]:
    """(v5.44.151) The pages of the program that took long to open, by the page: [{"page", "times", "longest_ms",
    "last"}], the longest first — from the log of slow operations (its newest files; only the lines of pages are
    parsed).  The owner: «некоторые страницы открываются по десять лет» — which ones, on his library and his disks,
    is seen here («Диагностика → Проверка»)."""
    found: dict = {}
    paths = [PERFORMANCE_LOG_FILE] + [PERFORMANCE_LOG_FILE.with_suffix(f".jsonl.{i}") for i in range(1, PAGES_READ)]
    mark = '"operation": "' + PAGE
    for path in paths:
        try:
            if not path.exists():
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for line in lines:
            if mark not in line:
                continue
            try:
                item = json.loads(line)
                name = str(item["operation"])[len(PAGE):]
                took = float(item.get("duration_ms") or 0)
            except Exception:
                continue
            row = found.setdefault(name, {"page": name, "times": 0, "longest_ms": 0.0, "last": ""})
            row["times"] += 1
            row["longest_ms"] = max(row["longest_ms"], took)
            row["last"] = max(row["last"], str(item.get("at") or ""))
    return sorted(found.values(), key=lambda r: -r["longest_ms"])


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
