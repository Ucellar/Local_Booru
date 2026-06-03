"""Read-only performance audit for the growing Local Booru SQLite library."""
from __future__ import annotations

from pathlib import Path
from time import perf_counter
import sqlite3
from typing import Callable, Any

from core.database.connection import db_path


def _query(con: sqlite3.Connection, name: str, sql: str, params=()) -> dict[str, Any]:
    started = perf_counter()
    error = ""
    rows = 0
    sample = None
    try:
        cur = con.execute(sql, tuple(params))
        values = cur.fetchall()
        rows = len(values)
        sample = dict(values[0]) if values and hasattr(values[0], "keys") else (values[0] if values else None)
    except Exception as exc:
        error = str(exc)
    elapsed = (perf_counter() - started) * 1000.0
    return {"name": name, "duration_ms": round(elapsed, 2), "rows": rows, "sample": sample, "error": error}


def audit_query_performance(settings: dict, *, progress: Callable[[str], None] | None = None, stop_check: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Measure key read-only queries without building caches or modifying rows."""
    progress = progress or (lambda _msg: None)
    stop_check = stop_check or (lambda: False)
    path = db_path(settings)
    result: dict[str, Any] = {"database": str(path), "exists": path.exists(), "read_only": True, "queries": [], "cancelled": False}
    if not path.exists():
        result["error"] = "SQLite-база не найдена"
        return result
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    tests = [
        ("Количество живых файлов", "SELECT COUNT(*) AS value FROM images WHERE deleted=0", ()),
        ("Источники галереи", "SELECT s.host, COUNT(DISTINCT xs.image_id) AS files FROM image_sources xs JOIN sources s ON s.id=xs.source_id JOIN images i ON i.id=xs.image_id WHERE i.deleted=0 GROUP BY s.host ORDER BY files DESC", ()),
        ("Группы тегов", "SELECT t.category, COUNT(DISTINCT it.image_id) AS files FROM tags t JOIN image_tags it ON it.tag_id=t.id JOIN images i ON i.id=it.image_id WHERE i.deleted=0 GROUP BY t.category ORDER BY files DESC", ()),
        ("Популярные теги", "SELECT t.name, COUNT(DISTINCT it.image_id) AS files FROM tags t JOIN image_tags it ON it.tag_id=t.id JOIN images i ON i.id=it.image_id WHERE i.deleted=0 GROUP BY t.id ORDER BY files DESC LIMIT 500", ()),
        ("Первая страница галереи", "SELECT id,path,file_name,hash_md5 FROM images WHERE deleted=0 ORDER BY id DESC LIMIT 64", ()),
        ("Точные MD5-дубли", "SELECT lower(hash_md5) AS md5, COUNT(*) AS files FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>'' GROUP BY lower(hash_md5) HAVING COUNT(*)>1 LIMIT 100", ()),
        ("Очередь SauceNAO", "SELECT service, COUNT(*) AS files FROM reverse_retry_queue GROUP BY service", ()),
        ("Фоновая раскладка", "SELECT status, COUNT(*) AS files FROM tag_enrichment_queue GROUP BY status", ()),
    ]
    try:
        for name, sql, params in tests:
            if stop_check():
                result["cancelled"] = True
                break
            progress(f"Профилирование: {name}…")
            result["queries"].append(_query(con, name, sql, params))
    finally:
        con.close()
    durations = [float(x.get("duration_ms", 0.0)) for x in result["queries"] if not x.get("error")]
    result["total_ms"] = round(sum(durations), 2)
    result["slowest"] = max(result["queries"], key=lambda x: float(x.get("duration_ms", 0.0)), default={})
    result["warning_queries"] = [x for x in result["queries"] if float(x.get("duration_ms", 0.0)) >= float(settings.get("performance_slow_ms", 100) or 100)]
    return result
