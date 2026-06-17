"""Stability layer for Local Booru.

Provides:
  1. Global exception handler — catches all uncaught exceptions, logs to file
  2. DB integrity check — verifies DB on startup, auto-repairs if possible
  3. DB auto-backup — daily backup of the SQLite file
  4. File maintenance — marks missing files as deleted in DB
  5. Network retry — exponential backoff for HTTP requests
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import time
import traceback
import json
import platform
from pathlib import Path
from typing import Callable, TypeVar
from core.redaction import sanitize_text, sanitize_log_directory

# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_logging(log_dir: Path | None = None) -> logging.Logger:
    """Configure root logger: file + stderr. Returns app logger."""
    try:
        from core.paths import LOGS_DIR
        _log_dir = log_dir or LOGS_DIR
    except Exception:
        _log_dir = Path.cwd() / "Local_Booru_Archive" / "settings" / "output" / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    # Privacy repair for historical logs created by older versions. No raw
    # secret-bearing backup is kept; this affects logs only, not media or DB.
    try:
        sanitize_log_directory(_log_dir)
    except Exception:
        pass

    log_file = _log_dir / "app.log"
    error_file = _log_dir / "errors.log"

    from core.redaction import RedactingFormatter
    fmt = RedactingFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # App log: INFO+. Size-based rotation prevents month-long parser runs from
    # turning the logs directory into another archive.
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)

    # Error log: ERROR+
    eh = logging.handlers.RotatingFileHandler(
        error_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    eh.setLevel(logging.ERROR)
    eh.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if not root.handlers:
        root.addHandler(fh)
        root.addHandler(eh)

    return logging.getLogger("local_booru")


# Lazy import for RotatingFileHandler
import logging.handlers

_log = logging.getLogger("local_booru.stability")


def _write_crash_snapshot(message: str) -> str:
    """Persist a small crash summary without reading or mutating media rows."""
    try:
        from core.paths import LOGS_DIR
        from core.settings import load_settings
        from core.database.connection import db_path
        settings = load_settings()
        database = db_path(settings)
        payload = {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "python": sys.version,
            "platform": platform.platform(),
            "database": str(database),
            "database_size_bytes": int(database.stat().st_size) if database.exists() else 0,
            "traceback": sanitize_text(message),
        }
        path = Path(LOGS_DIR) / "last_crash.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
    except Exception:
        return ""


# ── Global exception handler ──────────────────────────────────────────────────

def install_global_exception_handler(app=None) -> None:
    """Catch ALL uncaught exceptions, log them, show user-friendly dialog."""

    def _handle(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        msg = sanitize_text("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        _log.critical("Uncaught exception:\n%s", msg)
        _write_crash_snapshot(msg)
        # Also print to stderr so console shows it
        print(f"\n[CRASH] {exc_type.__name__}: {exc_value}", file=sys.stderr)
        # Show Qt dialog if app is running
        try:
            from PySide6.QtWidgets import QMessageBox, QApplication
            if QApplication.instance():
                box = QMessageBox()
                box.setWindowTitle("Ошибка — Local Booru")
                box.setIcon(QMessageBox.Icon.Critical)
                box.setText(f"Произошла ошибка: {exc_type.__name__}")
                box.setDetailedText(msg)
                box.setStandardButtons(QMessageBox.StandardButton.Ok)
                box.exec()
        except Exception:
            pass

    sys.excepthook = _handle

    # Also handle exceptions in Qt event loop
    if app is not None:
        try:
            def _qt_exception(exc_type, exc_value, exc_tb):
                _handle(exc_type, exc_value, exc_tb)
            app.setAttribute(
                __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.ApplicationAttribute.AA_DontUseNativeDialogs,
                False,
            )
        except Exception:
            pass


def safe_call(fn: Callable, *args, default=None, label: str = "", **kwargs):
    """Call fn safely, return default on any exception. Logs errors."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        _log.error("safe_call %s failed: %s", label or fn.__name__, e)
        return default


# ── DB integrity check ────────────────────────────────────────────────────────

def _check_existing_database(settings: dict, pragma: str) -> tuple[bool, str]:
    """Validate an existing SQLite file without initialising or migrating it."""
    import sqlite3
    from core.database.connection import db_path
    path = db_path(settings)
    if not path.exists():
        return True, "NEW_DATABASE"
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            rows = con.execute(pragma).fetchall()
        finally:
            con.close()
        results = [str(r[0]) for r in rows]
        if results == ["ok"]:
            return True, "OK"
        return False, "; ".join(results[:5]) or "unknown integrity failure"
    except Exception as exc:
        return False, str(exc)


def check_db_integrity(settings: dict) -> tuple[bool, str]:
    """Run read-only PRAGMA integrity_check without changing the SQLite file."""
    ok, msg = _check_existing_database(settings, "PRAGMA integrity_check(10)")
    if ok:
        _log.info("DB integrity: %s", msg)
    else:
        _log.error("DB integrity FAILED: %s", msg)
    return ok, msg


def check_db_quick(settings: dict) -> bool:
    """Read-only PRAGMA quick_check. Can still be slow on large DBs."""
    return _check_existing_database(settings, "PRAGMA quick_check(5)")[0]


def check_db_smoke(settings: dict) -> tuple[bool, str]:
    """Very cheap startup check: open DB read-only and read schema metadata only.

    This is intentionally NOT PRAGMA quick_check. quick_check can take minutes on
    a 400+ MB WAL/SQLite library after a crash, and doing it before the main
    window makes startup look frozen. Corruption detection is moved to a
    background health check after the window is visible.
    """
    import sqlite3
    from core.database.connection import db_path
    path = db_path(settings)
    if not path.exists():
        return True, "NEW_DATABASE"
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            con.execute("PRAGMA query_only=ON")
            con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            con.execute("PRAGMA user_version").fetchone()
        finally:
            con.close()
        return True, "SMOKE_OK"
    except Exception as exc:
        return False, str(exc)


# ── DB auto-backup ────────────────────────────────────────────────────────────

def maybe_backup_db(settings: dict, max_backups: int = 5) -> str | None:
    """Backup DB if last backup is >24h old. Returns backup path or None."""
    try:
        from core.database.connection import db_path
        from core.paths import BACKUPS_DIR
        db_file = db_path(settings)
        if not db_file.exists():
            return None

        backup_dir = Path(BACKUPS_DIR) / "db"
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Check last backup time
        existing = sorted(backup_dir.glob("*.sqlite3.bak"), reverse=True)
        if existing:
            last_mtime = existing[0].stat().st_mtime
            if time.time() - last_mtime < 86400:  # 24h
                return None  # too recent

        # Create backup
        ts = time.strftime("%Y%m%d_%H%M%S")
        dst = backup_dir / f"local_booru_{ts}.sqlite3.bak"

        # Use SQLite backup API for consistency (safe even during writes)
        import sqlite3
        src_con = sqlite3.connect(str(db_file))
        dst_con = sqlite3.connect(str(dst))
        src_con.backup(dst_con, pages=100)
        dst_con.close()
        src_con.close()

        _log.info("DB backup created: %s (%.1f MB)", dst.name, dst.stat().st_size / 1e6)

        # Remove old backups beyond max_backups
        existing = sorted(backup_dir.glob("*.sqlite3.bak"), reverse=True)
        for old in existing[max_backups:]:
            try:
                old.unlink()
                _log.info("Removed old backup: %s", old.name)
            except Exception:
                pass

        return str(dst)
    except Exception as e:
        _log.error("DB backup failed: %s", e)
        return None


# ── File maintenance ──────────────────────────────────────────────────────────

def run_file_maintenance(settings: dict, log: Callable | None = None,
                         max_check: int = 5000) -> dict:
    """Check that indexed files still exist on disk.

    Files that no longer exist are marked deleted=1 in DB.
    Returns {"checked": N, "missing": N, "errors": N}
    """
    log = log or _log.info
    stats = {"checked": 0, "missing": 0, "errors": 0}

    try:
        from core.database.connection import db
        with db(settings) as con:
            rows = con.execute(
                "SELECT id, path FROM images WHERE deleted=0 ORDER BY id LIMIT ?",
                (max_check,)
            ).fetchall()

            missing_ids = []
            for row in rows:
                stats["checked"] += 1
                try:
                    if not Path(row["path"]).exists():
                        missing_ids.append(int(row["id"]))
                        stats["missing"] += 1
                except Exception:
                    stats["errors"] += 1

            if missing_ids:
                ph = ",".join("?" * len(missing_ids))
                con.execute(
                    f"UPDATE images SET deleted=1 WHERE id IN ({ph})",
                    missing_ids,
                )
                con.commit()
                log(f"FILE MAINTENANCE: {stats['missing']} missing files marked deleted "
                    f"(out of {stats['checked']} checked)")
            else:
                log(f"FILE MAINTENANCE: all {stats['checked']} files OK")
    except Exception as e:
        _log.error("File maintenance error: %s", e)
        stats["errors"] += 1

    return stats


def check_recent_media_after_crash(settings: dict, log: Callable | None = None, limit: int = 250) -> dict:
    """Verify recently written media after an unclean shutdown.

    The full archive may contain tens of thousands of files; after a crash we
    inspect only the most recently indexed live rows and persist detected
    damage as integrity issues for the existing repair/inspection tools.
    """
    log = log or _log.info
    result = {"checked": 0, "damaged": 0, "missing": 0}
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
    video_exts = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
    try:
        from core.database.connection import db
        with db(settings, readonly=True) as con:
            rows = con.execute(
                "SELECT id,path,is_video FROM images WHERE deleted=0 ORDER BY mtime_ns DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        issues = []
        for row in rows:
            result["checked"] += 1
            path = Path(str(row["path"] or ""))
            issue = ""
            if not path.exists() or not path.is_file():
                result["missing"] += 1
                issue = "missing_after_crash"
            else:
                try:
                    if path.stat().st_size <= 0:
                        issue = "zero_size_after_crash"
                    elif path.suffix.lower() in image_exts:
                        from PIL import Image
                        with Image.open(path) as im:
                            im.verify()
                    elif bool(row["is_video"]) or path.suffix.lower() in video_exts:
                        # Full decoding would be expensive. A zero/truncated file
                        # is caught here; normal playback remains the final test.
                        if path.stat().st_size < 64:
                            issue = "truncated_video_after_crash"
                except Exception as exc:
                    issue = f"unreadable_media_after_crash: {type(exc).__name__}"
            if issue:
                result["damaged"] += 1
                issues.append((str(issue), int(row["id"]), str(path)))
        if issues:
            now = int(time.time())
            with db(settings, write=True) as con:
                for issue, image_id, path in issues:
                    con.execute(
                        "INSERT INTO integrity_issues(issue_type,severity,image_id,path,details,status,created_at) VALUES(?,?,?,?,?,'open',?)",
                        ("media_after_crash", "warning", image_id, path, issue, now),
                    )
            log(f"[Stability] Crash media check: found {len(issues)} suspect recent file(s).")
        else:
            log(f"[Stability] Crash media check: {result['checked']} recent file(s) look OK.")
    except Exception as exc:
        _log.error("Crash media check failed: %s", exc)
    return result


def sample_live_media_paths(settings: dict, log: Callable | None = None, limit: int = 1000) -> dict:
    """Sample live DB paths at startup without walking the whole archive.

    This detects path/root mistakes and orphan rows cheaply. Missing rows are
    logged and recorded as integrity issues, but not automatically deleted.
    """
    log = log or _log.info
    result = {"checked": 0, "missing": 0, "errors": 0}
    try:
        from core.database.connection import db, writes_blocked
        with db(settings, readonly=True) as con:
            rows = con.execute(
                "SELECT id,path FROM images WHERE deleted=0 ORDER BY RANDOM() LIMIT ?",
                (max(1, int(limit or 1000)),),
            ).fetchall()
        issues = []
        for row in rows:
            result["checked"] += 1
            try:
                path = Path(str(row["path"] or ""))
                if not path.exists():
                    result["missing"] += 1
                    issues.append((int(row["id"]), str(path)))
            except Exception:
                result["errors"] += 1
        if issues and not writes_blocked():
            now = int(time.time())
            with db(settings, write=True) as con:
                for image_id, path in issues[:1000]:
                    con.execute(
                        "INSERT INTO integrity_issues(issue_type,severity,image_id,path,details,status,created_at) VALUES(?,?,?,?,?,'open',?)",
                        ("sample_missing_file", "warning", image_id, path, "startup sampled path missing", now),
                    )
        if result["missing"]:
            log(f"[Stability] Sample path check: missing={result['missing']} / checked={result['checked']}")
        else:
            log(f"[Stability] Sample path check: {result['checked']} sampled file path(s) OK")
    except Exception as exc:
        result["errors"] += 1
        _log.error("Sample path check failed: %s", exc)
    return result


# ── Network retry ─────────────────────────────────────────────────────────────

T = TypeVar("T")

def with_retry(
    fn: Callable[[], T],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    label: str = "",
) -> T:
    """Call fn() with exponential backoff retry on failure.

    Raises the last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < max_attempts - 1:
                delay = base_delay * (2 ** attempt)
                _log.warning(
                    "Retry %s [%d/%d] after %.1fs: %s",
                    label or fn.__name__, attempt + 1, max_attempts, delay, e
                )
                time.sleep(delay)
    raise last_exc  # type: ignore


def retry_request(session, method: str, url: str, max_attempts: int = 3,
                  **kwargs):
    """HTTP request with automatic retry on connection/timeout errors."""
    import requests as _requests

    def _do():
        resp = getattr(session, method.lower())(url, **kwargs)
        # Retry on 429 (rate limit) and 5xx
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 5))
            _log.warning("Rate limited by %s, waiting %ds", url, retry_after)
            time.sleep(retry_after)
            raise _requests.RequestException(f"429 Rate Limited: {url}")
        if resp.status_code >= 500:
            raise _requests.RequestException(f"HTTP {resp.status_code}: {url}")
        return resp

    return with_retry(_do, max_attempts=max_attempts, label=f"{method} {url}")


# ── Startup checks ────────────────────────────────────────────────────────────

def write_shutdown_flag(settings: dict, bad: bool = False) -> None:
    """Write a flag indicating shutdown state. bad=True means crash/bad shutdown."""
    try:
        from core.paths import DATA_DIR
        flag = DATA_DIR / "last_shutdown.txt"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("bad" if bad else "ok", encoding="utf-8")
    except Exception:
        pass


def was_last_shutdown_bad(settings: dict) -> bool:
    """Check if last shutdown was bad (crash). Hydrus pattern."""
    try:
        from core.paths import DATA_DIR
        flag = DATA_DIR / "last_shutdown.txt"
        return flag.exists() and flag.read_text(encoding="utf-8").strip() == "bad"
    except Exception:
        return False


def run_startup_checks(settings: dict, log: Callable | None = None) -> dict:
    """Run startup checks without making the UI wait for a full DB scan.

    v163 change: startup uses a cheap read-only smoke check by default.
    PRAGMA quick_check/integrity_check is allowed to run after the window is
    visible via run_deferred_db_health_check(). If the user explicitly wants the
    old blocking behavior, set startup_db_check_mode to "quick" or "full".
    """
    log = log or print
    results = {}

    bad_shutdown = was_last_shutdown_bad(settings)
    if bad_shutdown:
        log("[Stability] WARNING: Last shutdown was bad (crash). Heavy DB check will run after the window is visible.")
        results["bad_shutdown"] = True
    write_shutdown_flag(settings, bad=True)  # assume bad until clean exit

    mode = str(settings.get("startup_db_check_mode", "fast") or "fast").strip().lower()
    if mode in {"quick", "full", "blocking"}:
        log("[Stability] Blocking DB quick_check requested by settings...")
        ok = check_db_quick(settings)
        results["db_ok"] = ok
        if not ok or mode == "full" or bad_shutdown:
            log("[Stability] Running blocking integrity check...")
            ok2, msg = check_db_integrity(settings)
            results["db_integrity"] = msg
            if not ok2:
                from core.database.connection import set_writes_blocked
                set_writes_blocked(msg)
                results["write_blocked"] = True
                log(f"[Stability] DB INTEGRITY ERROR: {msg}")
                log("[Stability] SAFE MODE: SQLite writes are blocked until the working DB is rebuilt or restored manually.")
            else:
                from core.database.connection import set_writes_blocked
                set_writes_blocked("")
        else:
            from core.database.connection import set_writes_blocked
            set_writes_blocked("")
            log("[Stability] DB OK")
    else:
        log("[Stability] Fast DB smoke check...")
        ok, msg = check_db_smoke(settings)
        results["db_ok"] = ok
        results["db_integrity"] = msg
        results["deferred_health_check"] = True
        if ok:
            from core.database.connection import set_writes_blocked
            if bad_shutdown and not bool(settings.get("startup_allow_writes_before_health", False)):
                # The DB can be opened and the UI may be shown, but after an
                # unclean shutdown we should not run migrations/cleanup writes
                # until the background quick_check/integrity_check has passed.
                set_writes_blocked("ожидание фоновой проверки SQLite после аварийного завершения")
                results["write_deferred_until_health"] = True
                log("[Stability] DB smoke check OK; SQLite writes are temporarily paused until the background health check finishes.")
            else:
                set_writes_blocked("")
                log("[Stability] DB smoke check OK; quick_check deferred to background.")
        else:
            from core.database.connection import set_writes_blocked
            set_writes_blocked(msg)
            results["write_blocked"] = True
            log(f"[Stability] DB OPEN ERROR: {msg}")
            log("[Stability] SAFE MODE: SQLite writes are blocked because the DB cannot be opened read-only.")

    # Cheap live-path sample only if DB opened. Keep it small so startup remains fast.
    if results.get("db_ok"):
        try:
            limit = int(settings.get("startup_sample_paths", 100) or 100)
            limit = max(0, min(250, limit))
            if limit:
                results["sample_paths"] = sample_live_media_paths(settings, log=log, limit=limit)
        except Exception as exc:
            results["sample_paths"] = {"errors": 1, "error": str(exc)}

    # Backup may still take time on huge DBs, so it is not forced during early
    # startup. It remains available from normal backup/diagnostic paths.
    if bool(settings.get("startup_auto_backup", False)):
        log("[Stability] Checking DB backup...")
        backup = maybe_backup_db(settings)
        if backup:
            log(f"[Stability] DB backed up: {Path(backup).name}")
        results["backup"] = backup
    else:
        results["backup"] = None

    return results


def run_deferred_db_health_check(settings: dict, log: Callable | None = None) -> dict:
    """Run slow DB checks after the UI is already visible."""
    log = log or print
    results = {}
    bad_shutdown = was_last_shutdown_bad(settings)
    try:
        log("[Stability] Background DB quick_check started...")
        ok = check_db_quick(settings)
        results["db_ok"] = ok
        if not ok or bad_shutdown:
            log("[Stability] Background integrity_check started...")
            ok2, msg = check_db_integrity(settings)
            results["db_integrity"] = msg
            if not ok2:
                from core.database.connection import set_writes_blocked
                set_writes_blocked(msg)
                results["write_blocked"] = True
                log(f"[Stability] DB INTEGRITY ERROR: {msg}")
                return results
        from core.database.connection import set_writes_blocked
        set_writes_blocked("")
        try:
            record_health_event(settings, "ok", "background quick_check ok", check_type="deferred_quick_check")
        except Exception:
            pass
        log("[Stability] Background DB health check OK")
        return results
    except Exception as exc:
        results["error"] = str(exc)
        log(f"[Stability] Background DB health check failed: {exc}")
        return results


def record_health_event(settings: dict, status: str, details: str = "", check_type: str = "startup_quick_check") -> None:
    """Persist successful/acknowledged health results after schema initialisation."""
    try:
        from core.database.connection import db, db_path, writes_blocked
        if writes_blocked():
            return
        path = db_path(settings)
        with db(settings, write=True) as con:
            con.execute(
                "INSERT INTO database_health_events(check_type,status,details,db_size_bytes,created_at) VALUES(?,?,?,?,?)",
                (str(check_type), str(status), str(details or ""), int(path.stat().st_size) if path.exists() else 0, int(time.time())),
            )
    except Exception as exc:
        _log.warning("Could not persist database health event: %s", exc)


def on_clean_exit(settings: dict) -> None:
    """Call on clean shutdown to mark it as OK."""
    write_shutdown_flag(settings, bad=False)
