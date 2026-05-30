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
from pathlib import Path
from typing import Callable, TypeVar

# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_logging(log_dir: Path | None = None) -> logging.Logger:
    """Configure root logger: file + stderr. Returns app logger."""
    try:
        from core.paths import LOGS_DIR
        _log_dir = log_dir or LOGS_DIR
    except Exception:
        _log_dir = Path.home() / "Documents" / "Local_Booru" / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)

    log_file = _log_dir / "app.log"
    error_file = _log_dir / "errors.log"

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # App log: INFO+
    fh = logging.handlers.TimedRotatingFileHandler(
        log_file, when="midnight", interval=1, backupCount=7, encoding="utf-8"
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)

    # Error log: ERROR+
    eh = logging.handlers.TimedRotatingFileHandler(
        error_file, when="midnight", interval=1, backupCount=7, encoding="utf-8"
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


# ── Global exception handler ──────────────────────────────────────────────────

def install_global_exception_handler(app=None) -> None:
    """Catch ALL uncaught exceptions, log them, show user-friendly dialog."""

    def _handle(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _log.critical("Uncaught exception:\n%s", msg)
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

def check_db_integrity(settings: dict) -> tuple[bool, str]:
    """Run PRAGMA integrity_check. Returns (ok, message)."""
    try:
        from core.database.connection import db
        with db(settings, readonly=True) as con:
            rows = con.execute("PRAGMA integrity_check(10)").fetchall()
            results = [r[0] for r in rows]
            if results == ["ok"]:
                _log.info("DB integrity: OK")
                return True, "OK"
            else:
                msg = "; ".join(results[:5])
                _log.error("DB integrity FAILED: %s", msg)
                return False, msg
    except Exception as e:
        _log.error("DB integrity check error: %s", e)
        return False, str(e)


def check_db_quick(settings: dict) -> bool:
    """Quick check: PRAGMA quick_check. Faster than full integrity_check."""
    try:
        from core.database.connection import db
        with db(settings, readonly=True) as con:
            r = con.execute("PRAGMA quick_check(5)").fetchone()
            return r and r[0] == "ok"
    except Exception:
        return False


# ── DB auto-backup ────────────────────────────────────────────────────────────

def maybe_backup_db(settings: dict, max_backups: int = 5) -> str | None:
    """Backup DB if last backup is >24h old. Returns backup path or None."""
    try:
        from core.database.connection import db_path
        from core.paths import DATA_DIR
        db_file = db_path(settings)
        if not db_file.exists():
            return None

        backup_dir = DATA_DIR / "db_backups"
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
    """Run all startup stability checks. Returns status dict."""
    log = log or print
    results = {}

    # 0. Check last shutdown (Hydrus pattern)
    bad_shutdown = was_last_shutdown_bad(settings)
    if bad_shutdown:
        log("[Stability] WARNING: Last shutdown was bad (crash). Running full DB check...")
        results["bad_shutdown"] = True
    write_shutdown_flag(settings, bad=True)  # assume bad until clean exit

    # 1. DB quick check (full if last shutdown was bad)
    log("[Stability] Quick DB check...")
    ok = check_db_quick(settings)
    results["db_ok"] = ok
    if not ok or bad_shutdown:
        log("[Stability] Running full integrity check...")
        ok2, msg = check_db_integrity(settings)
        results["db_integrity"] = msg
        if not ok2:
            log(f"[Stability] DB INTEGRITY ERROR: {msg}")
    else:
        log("[Stability] DB OK")

    # 2. Auto-backup
    log("[Stability] Checking DB backup...")
    backup = maybe_backup_db(settings)
    if backup:
        log(f"[Stability] DB backed up: {Path(backup).name}")
    results["backup"] = backup

    return results


def on_clean_exit(settings: dict) -> None:
    """Call on clean shutdown to mark it as OK."""
    write_shutdown_flag(settings, bad=False)
