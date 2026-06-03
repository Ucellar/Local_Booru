import sys
import os
import ctypes
from pathlib import Path

# Chromium/WebEngine flags — must be set before any PySide6.QtWebEngine import.
_chromium_flags = [
    "--lang=en-US",
    "--disable-blink-features=AutomationControlled",
    "--enable-features=NetworkService",
    "--disable-features=SameSiteByDefaultCookies,CookiesWithoutSameSiteMustBeSecure,"
    "CompressionDictionaryTransport,PersistentSharedDictionary",
    "--disable-gpu",
    "--disable-gpu-compositing",
    "--disable-gpu-shader-disk-cache",
    "--disable-software-rasterizer",
    "--disable-webgl",
    "--disable-3d-apis",
    "--disk-cache-size=67108864",
    "--media-cache-size=33554432",
    "--log-level=3",
    "--disable-logging",
]
_existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = ((_existing + " ") if _existing else "") + " ".join(_chromium_flags)
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QT_OPENGL", "software")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon, QPixmapCache

from ui.main_window import MainWindow
from core.paths import ERROR_LOG_FILE
from core.redaction import sanitize_text

try:
    from core.image_safe import configure_pillow
    configure_pillow()
except Exception:
    pass

from core.tagger import load_settings, cleanup_preview_cache


def _log_exception(exc_type, exc, tb):
    import traceback, time
    text = sanitize_text("".join(traceback.format_exception(exc_type, exc, tb)))
    text = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] " + text
    try:
        ERROR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass
    try:
        print(text, file=sys.stderr)
    except Exception:
        pass


def main() -> int:
    # ── Stability: logging + exception handler ──────────────────────────
    from core.stability import setup_logging, install_global_exception_handler
    setup_logging()
    import logging
    _applog = logging.getLogger("local_booru")
    _applog.info("=== Local Booru starting ===")

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LocalBooru.App")
    except Exception:
        pass

    # ── HiDPI / multi-resolution support ──────────────────────────────
    try:
        from PySide6.QtCore import Qt
        # Enable automatic HiDPI scaling (handles 4K, 1080p, 1440p etc.)
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(sys.argv)
    try:
        QPixmapCache.setCacheLimit(128 * 1024)  # 128 MB
    except Exception:
        pass
    app.setStyle("Fusion")
    app.setApplicationName("Local Booru")
    app.setOrganizationName("Local Booru")

    # Install global Qt + Python exception handler
    install_global_exception_handler(app)

    # Start thumbnail service (background QThreadPool, 3 workers)
    from core.thumb_service import ThumbnailService
    thumb_svc = ThumbnailService.instance(max_threads=3)
    try:
        from core.shutdown import register as _shutdown_register
        _shutdown_register("thumbnail service", thumb_svc.stop)
    except Exception:
        pass

    _db_startup_results = {}
    try:
        _startup_settings = load_settings()
        # Validate the existing working DB before any migration or maintenance writes.
        # A broken disposable gallery may be rebuilt, but it must not be changed
        # silently while the user is deciding what to do.
        from core.stability import run_startup_checks
        _db_startup_results = run_startup_checks(_startup_settings, log=lambda m: _applog.info(m))
        if _db_startup_results.get("write_blocked"):
            raise RuntimeError("SQLite is in read-only safety mode: " + str(_db_startup_results.get("db_integrity", "integrity check failed")))
        # One-time transitions from pre-SQLite compatibility files. These are
        # audit-preserving migrations; original media is never touched.
        try:
            from core.deleted_registry import migrate_legacy_registry
            _deleted_migration = migrate_legacy_registry(_startup_settings)
            if int(_deleted_migration.get("imported", 0) or 0):
                _applog.info("Migrated %s legacy deleted-MD5 rule(s) into SQLite.", _deleted_migration.get("imported", 0))
        except Exception as _registry_error:
            _applog.warning("Deleted-MD5 registry migration failed: %s", _registry_error)
        try:
            from core.nomatch_db import migrate_legacy_nomatch_cache
            _nm_migration = migrate_legacy_nomatch_cache(_startup_settings)
            if int(_nm_migration.get("imported", 0) or 0):
                _applog.info("Migrated %s legacy NO_MATCH item(s) into SQLite.", _nm_migration.get("imported", 0))
        except Exception as _nm_error:
            _applog.warning("NO_MATCH migration failed: %s", _nm_error)
        thumb_svc.configure(
            max_threads=int(_startup_settings.get("thumb_threads", 3) or 3),
            memory_items=int(_startup_settings.get("thumb_memory_items", 400) or 400),
        )
        try:
            from core.stability import record_health_event
            record_health_event(_startup_settings, "ok", str(_db_startup_results.get("db_integrity", "quick_check ok")))
        except Exception as _health_event_error:
            _applog.warning("Could not record DB health event: %s", _health_event_error)
        cleanup_preview_cache(_startup_settings)
        from core.file_safety import cleanup_partial_files
        from core.paths import result_output_base
        _removed_parts = cleanup_partial_files(result_output_base(_startup_settings))
        try:
            from core.library_lifecycle import archive_expired_inbox
            _archived = archive_expired_inbox(_startup_settings)
            if _archived:
                _applog.info("Auto-archived %s Inbox file(s) after their review window.", _archived)
            try:
                from core.library_lifecycle import purge_expired_trash
                _purged = purge_expired_trash(_startup_settings)
                if int(_purged.get("removed_records", 0) or 0):
                    _applog.info("Auto-purged %s expired Trash file(s).", _purged.get("removed_records", 0))
            except Exception as _trash_error:
                _applog.warning("Trash expiration cleanup failed: %s", _trash_error)
        except Exception as _inbox_error:
            _applog.warning("Inbox expiration check failed: %s", _inbox_error)
        if _removed_parts:
            _applog.warning(
                "Removed %s unfinished .part file(s) left by an interrupted write; "
                "affected downloads remain eligible for retry.",
                _removed_parts,
            )
    except Exception as _cleanup_error:
        _applog.warning("Startup cache/.part cleanup failed: %s", _cleanup_error)

    icon_path = Path(__file__).parent / "assets" / "app_icon.ico"
    icon = None
    if icon_path.exists():
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)

    # Clear oversized saved window dimensions before creating window
    try:
        from core.app_context import AppContext
        _ctx = AppContext()
        _s = _ctx.settings
        _saved_w = int(_s.get("window_w", 0))
        _saved_h = int(_s.get("window_h", 0))
        if _saved_w > 0 or _saved_h > 0:
            from PySide6.QtGui import QGuiApplication
            _screen = QGuiApplication.primaryScreen()
            if _screen:
                _avail = _screen.availableGeometry()
                if _saved_w > _avail.width() or _saved_h > _avail.height():
                    _s.pop("window_w", None)
                    _s.pop("window_h", None)
                    _ctx.save_settings()
    except Exception:
        pass

    # ── Optional filesystem watcher / incremental indexing ─────────────
    watcher = None
    try:
        from core.settings import load_settings as _ls
        from core.filesystem_watcher import LibraryWatcher
        from core.shutdown import register as _shutdown_register
        _settings_for_watch = _ls()
        watcher = LibraryWatcher(_settings_for_watch, log=lambda m: _applog.info(m))
        if watcher.start():
            _shutdown_register("library watcher", watcher.stop)
    except Exception as _we:
        _applog.warning("Watcher startup failed: %s", _we)

    w = MainWindow()
    if icon:
        w.setWindowIcon(icon)
    w.show()
    if _db_startup_results.get("write_blocked"):
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                w,
                "SQLite: безопасный режим",
                "Проверка рабочей базы не пройдена. Запись в SQLite заблокирована.\n\n"
                "Основной архив не затронут. Открой «Диагностика» и либо создай копию базы, "
                "либо удали/пересобери рабочую библиотеку.\n\n"
                + str(_db_startup_results.get("db_integrity", "")),
            )
        except Exception:
            pass

    # Register clean exit handler (Hydrus pattern: marks shutdown as OK)
    def _on_clean_exit():
        try:
            from core.settings import load_settings as _ls
            from core.stability import on_clean_exit
            on_clean_exit(_ls())
        except Exception:
            pass
    app.aboutToQuit.connect(_on_clean_exit)

    ret = app.exec()

    # Graceful shutdown: stop background pools, watcher and pooled DB handles.
    try:
        from core.shutdown import request_shutdown
        request_shutdown()
    except Exception:
        try:
            thumb_svc.stop()
        except Exception:
            pass

    return ret


if __name__ == "__main__":
    sys.exit(main())
