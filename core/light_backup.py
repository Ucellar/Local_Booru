"""Lightweight Local Booru backups.

This module intentionally backs up only the small, hard-to-rebuild state:
SQLite, configuration and optional cookies.  Managed media and thumbnail/cache
files are never copied here.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any

from core.database.connection import db, db_path, close_thread_pooled_connections
from core.database.storage import _safe_ident
from core.paths import SETTINGS_FILE, RUNTIME_DIR, DATA_DIR


def _now() -> int:
    return int(time.time())


def _safe_reason(value: str) -> str:
    text = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(value or "backup"))
    return text[:48] or "backup"


def _backup_root(settings: dict) -> Path:
    explicit = str((settings or {}).get("light_backup_dir", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    # Fallback stays inside the portable settings branch; it is still a useful
    # local checkpoint, but users should choose an external SSD for real safety.
    return DATA_DIR / "output" / "backups" / "light"


def _count_table(con, table: str) -> int:
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {_safe_ident(table)}").fetchone()[0] or 0)
    except Exception:
        return 0


def _snapshot_db(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source), timeout=120)
    dst = sqlite3.connect(str(target), timeout=120)
    try:
        src.execute("PRAGMA busy_timeout=120000")
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def checkpoint_sqlite(settings: dict, *, truncate: bool = True, optimize: bool = True) -> dict[str, Any]:
    """Flush WAL pages and optionally run PRAGMA optimize.

    This is safe to call on shutdown after workers are stopped.  During active
    parser work callers should avoid truncate checkpoints.
    """
    path = db_path(settings)
    if not path.exists():
        return {"ok": False, "reason": "database missing", "db": str(path)}
    # Closing pooled connections gives SQLite a chance to release stale readers
    # before a TRUNCATE checkpoint. During active parser work PASSIVE must not
    # destroy warm reader/writer handles.
    if truncate:
        close_thread_pooled_connections(settings)
    con = sqlite3.connect(str(path), timeout=120)
    try:
        con.execute("PRAGMA busy_timeout=120000")
        mode = "TRUNCATE" if truncate else "PASSIVE"
        row = con.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        if optimize:
            con.execute("PRAGMA optimize")
        con.commit()
        return {"ok": True, "db": str(path), "checkpoint": tuple(row) if row else ()}
    finally:
        con.close()



def passive_checkpoint_sqlite(settings: dict, *, optimize: bool = False) -> dict[str, Any]:
    """Low-impact checkpoint for active long parser runs.

    It never closes pooled connections and never truncates the WAL. Use the
    shutdown path for the heavier TRUNCATE checkpoint.
    """
    return checkpoint_sqlite(settings, truncate=False, optimize=optimize)


def create_light_backup(settings: dict, *, reason: str = "manual", force: bool = True) -> dict[str, Any]:
    """Create a timestamped ZIP containing SQLite + config + manifest.

    Returns a dictionary suitable for UI messages and logs.  If force is False,
    the configured interval is respected.
    """
    settings = settings or {}
    now = _now()
    if not force:
        try:
            hours = max(1, int(settings.get("light_backup_interval_hours", 24) or 24))
            last = int(settings.get("light_backup_last_at", 0) or 0)
            if last and now - last < hours * 3600:
                return {"created": False, "skipped": True, "reason": "interval", "next_after": last + hours * 3600}
        except Exception:
            pass

    source_db = db_path(settings)
    if not source_db.exists():
        return {"created": False, "error": "SQLite database not found", "db": str(source_db)}

    root = _backup_root(settings)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {"created": False, "error": f"backup directory unavailable: {exc}", "destination": str(root)}

    stamp = time.strftime("%Y%m%d_%H%M%S")
    reason_slug = _safe_reason(reason)
    zip_path = root / f"Local_Booru_light_backup_{stamp}_{reason_slug}.zip"
    try:
        from core.preflight import ensure_space_for_write
        expected = int(source_db.stat().st_size if source_db.exists() else 0) + int(SETTINGS_FILE.stat().st_size if SETTINGS_FILE.exists() else 0) + 1024 * 1024
        ok_space, space_msg = ensure_space_for_write(settings, zip_path, expected)
        if not ok_space:
            return {"created": False, "error": space_msg, "destination": str(root)}
    except Exception:
        pass
    tmp_dir = root / f".tmp_light_backup_{stamp}_{reason_slug}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        db_copy = tmp_dir / "db" / "local_booru_index.sqlite3"
        _snapshot_db(source_db, db_copy)

        config_dir = tmp_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        if SETTINGS_FILE.exists():
            shutil.copy2(SETTINGS_FILE, config_dir / "app_settings.json")

        if bool(settings.get("light_backup_include_cookies", False)):
            cookie_src = RUNTIME_DIR / "browser_cookies"
            if cookie_src.exists():
                shutil.copytree(cookie_src, tmp_dir / "runtime" / "browser_cookies", dirs_exist_ok=True)

        with sqlite3.connect(str(db_copy)) as con:
            manifest = {
                "format": "local-booru-light-backup-v1",
                "created_at": now,
                "created_at_text": time.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": str(reason or "manual"),
                "archive_settings_dir": str(DATA_DIR),
                "source_db": str(source_db),
                "app_settings": str(SETTINGS_FILE),
                "counts": {
                    "images": _count_table(con, "images"),
                    "live_images": int(con.execute("SELECT COUNT(*) FROM images WHERE deleted=0").fetchone()[0] or 0),
                    "tags": _count_table(con, "tags"),
                    "sources": _count_table(con, "sources"),
                    "no_match_active": int(con.execute("SELECT COUNT(*) FROM no_match_items WHERE active=1").fetchone()[0] or 0),
                },
                "includes_media": False,
                "includes_cache": False,
                "includes_cookies": bool(settings.get("light_backup_include_cookies", False)),
            }
        (tmp_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for item in sorted(tmp_dir.rglob("*")):
                if item.is_file():
                    z.write(item, item.relative_to(tmp_dir).as_posix())
    except Exception as exc:
        return {"created": False, "error": str(exc), "destination": str(root)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    _rotate_backups(root, keep_last=int(settings.get("light_backup_keep_last", 10) or 10))
    settings["light_backup_last_at"] = now
    return {"created": True, "path": str(zip_path), "bytes": int(zip_path.stat().st_size), "destination": str(root)}


def _rotate_backups(root: Path, *, keep_last: int) -> None:
    keep_last = max(1, int(keep_last or 1))
    backups = sorted(root.glob("Local_Booru_light_backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep_last:]:
        try:
            old.unlink()
        except Exception:
            pass


def maybe_auto_backup(settings: dict, *, reason: str) -> dict[str, Any]:
    if not bool((settings or {}).get("light_backup_enabled", False)):
        return {"created": False, "skipped": True, "reason": "disabled"}
    return create_light_backup(settings, reason=reason, force=False)
