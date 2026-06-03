"""Create a privacy-conscious diagnostic ZIP for bug reports."""
from __future__ import annotations

import json
import platform
import sys
import time
import zipfile
from pathlib import Path

from core.paths import ERROR_LOG_FILE, LOGS_DIR, SETTINGS_FILE
from core.database.connection import db_path
from core.redaction import sanitize_object, sanitize_text

_SECRET_WORDS = ("api_key", "key", "cookie", "password", "login", "token", "user_id")


def _redact(value, key=""):
    if any(secret in str(key).lower() for secret in _SECRET_WORDS):
        return "<removed>" if value else value
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value]
    return value


def create_diagnostic_zip(settings: dict, destination: str | Path) -> str:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "platform": platform.platform(),
        "settings": _redact(dict(settings or {})),
    }
    try:
        from core.library_lifecycle import library_stats
        report["library_stats"] = library_stats(settings)
    except Exception as exc:
        report["library_stats_error"] = str(exc)
    try:
        from core.performance import recent_slow_operations
        report["slow_operations"] = recent_slow_operations(100)
    except Exception as exc:
        report["slow_operations_error"] = str(exc)
    dbfile = db_path(settings)
    report["database_exists"] = dbfile.exists()
    report["database_size"] = dbfile.stat().st_size if dbfile.exists() else 0
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("diagnostics.json", json.dumps(sanitize_object(report), ensure_ascii=False, indent=2))
        included = set()
        for pattern in ("*.log", "*.log.*", "*.json", "*.jsonl", "*.jsonl.*"):
            for logfile in sorted(Path(LOGS_DIR).glob(pattern)):
                try:
                    arcname = f"logs/{logfile.name}"
                    if arcname in included:
                        continue
                    included.add(arcname)
                    content = logfile.read_text(encoding="utf-8", errors="replace")
                    z.writestr(arcname, sanitize_text(content))
                except Exception:
                    pass
        if ERROR_LOG_FILE.exists() and ERROR_LOG_FILE.parent != Path(LOGS_DIR):
            try:
                z.writestr("logs/errors.log", sanitize_text(ERROR_LOG_FILE.read_text(encoding="utf-8", errors="replace")))
            except Exception:
                pass
    return str(destination)
