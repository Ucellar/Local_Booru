import sys
import os
import ctypes
import time
import threading
from pathlib import Path

# ── Early visible startup console ─────────────────────────────────────────────
# This runs before PySide6/Qt imports and before SQLite startup checks, so a
# double-clicked Windows launch no longer looks like a silent hang in Task
# Manager. Disable only with LOCAL_BOORU_STARTUP_CONSOLE=0.
_STARTUP_T0 = time.time()
_STARTUP_DONE = False
_STARTUP_LAST_STEP = "boot"

def _startup_log_path() -> Path:
    # Keep startup logs in Local_Booru_Archive/settings/output/logs instead of
    # the install/app directory.  Program Files and unpacked release folders may
    # be read-only, and logs should stay with the user's data.
    override = os.environ.get("LOCAL_BOORU_STARTUP_LOG", "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from core.paths import LOGS_DIR
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        return LOGS_DIR / "startup_console.log"
    except Exception:
        return Path(__file__).with_name("startup_console.log")


_STARTUP_LOG_FILE = _startup_log_path()


def _startup_log(message: str) -> None:
    global _STARTUP_LAST_STEP
    _STARTUP_LAST_STEP = str(message)
    stamp = time.strftime("%H:%M:%S")
    elapsed = time.time() - _STARTUP_T0
    line = f"[{stamp} +{elapsed:6.1f}s] STARTUP: {message}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with _STARTUP_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _ensure_startup_console() -> None:
    if os.environ.get("LOCAL_BOORU_STARTUP_CONSOLE", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        # Reuse parent console if the user launched from cmd/PowerShell; otherwise
        # allocate a new one for pythonw/PyInstaller windowed launches.
        if not kernel32.AttachConsole(-1):
            kernel32.AllocConsole()
        try:
            kernel32.SetConsoleTitleW("Local Booru — запуск и журнал")
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:
            pass
        # pythonw.exe starts with stdout/stderr detached. Rebind them to the
        # newly allocated console so print/logging becomes visible immediately.
        try:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        except Exception:
            pass
    except Exception:
        pass


def _startup_heartbeat() -> None:
    # While Qt has not shown the main window yet, print a small heartbeat so it is
    # obvious which startup stage is blocking: imports, SQLite check, migration,
    # preview cleanup, watcher, main window construction, etc.
    while not _STARTUP_DONE:
        time.sleep(5.0)
        if _STARTUP_DONE:
            break
        try:
            stamp = time.strftime("%H:%M:%S")
            elapsed = time.time() - _STARTUP_T0
            line = f"[{stamp} +{elapsed:6.1f}s] STARTUP: ещё запускается; текущий этап: {_STARTUP_LAST_STEP}"
            print(line, flush=True)
            with _STARTUP_LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def _enable_console_logging() -> None:
    """Mirror normal logging into the startup console when it exists."""
    try:
        import logging
        root = logging.getLogger()
        if any(getattr(h, "_local_booru_console", False) for h in root.handlers):
            return
        handler = logging.StreamHandler(sys.stdout)
        handler._local_booru_console = True
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(handler)
    except Exception:
        pass


_ensure_startup_console()
try:
    _STARTUP_LOG_FILE.write_text("", encoding="utf-8")
except Exception:
    pass
_startup_log("процесс создан; ранняя консоль включена")
try:
    threading.Thread(target=_startup_heartbeat, name="startup-heartbeat", daemon=True).start()
except Exception:
    pass

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

_startup_log("импорт PySide6 / Qt")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon, QPixmapCache
_startup_log("PySide6 импортирован")

from ui.main_window import MainWindow
from core.paths import ERROR_LOG_FILE
from core.redaction import sanitize_text

try:
    from core.image_safe import configure_pillow
    configure_pillow()
except Exception:
    pass

from core.settings import load_settings
from core.tagger import cleanup_preview_cache


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
    global _STARTUP_DONE
    _startup_log("main() начат")
    # ── Stability: logging + exception handler ──────────────────────────
    from core.stability import setup_logging, install_global_exception_handler
    setup_logging()
    _enable_console_logging()
    import logging
    _applog = logging.getLogger("local_booru")
    _applog.info("=== Local Booru starting ===")
    try:
        from core.paths import DATA_DIR, SETTINGS_FILE, WORKSPACE_POINTER_FILE, STABLE_WORKSPACE_POINTER_FILE
        _startup_log(f"workspace DATA_DIR: {DATA_DIR}")
        _startup_log(f"workspace SETTINGS_FILE: {SETTINGS_FILE}")
        _startup_log(f"workspace local pointer: {WORKSPACE_POINTER_FILE}")
        _startup_log(f"workspace global pointer: {STABLE_WORKSPACE_POINTER_FILE}")
        _applog.info("Workspace DATA_DIR: %s", DATA_DIR)
        _applog.info("Workspace SETTINGS_FILE: %s", SETTINGS_FILE)
    except Exception:
        pass

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

    _startup_log("создание QApplication")
    app = QApplication(sys.argv)
    _startup_log("QApplication создан")
    try:
        _startup_log("загрузка настроек для QPixmapCache")
        _settings_for_pixmap = load_settings()
        try:
            from core.language_bootstrap import apply_language_choice, should_show_language_dialog
            if should_show_language_dialog(_settings_for_pixmap) and os.environ.get("LOCAL_BOORU_SKIP_LANGUAGE_DIALOG", "").strip().lower() not in {"1", "true", "yes", "on"}:
                _startup_log("первичный выбор языка интерфейса")
                from ui.language_dialog import choose_startup_language
                _chosen_language = choose_startup_language(_settings_for_pixmap.get("language", "ru"))
                apply_language_choice(_settings_for_pixmap, _chosen_language)
                from core.settings import save_settings as _save_language_settings
                _save_language_settings(_settings_for_pixmap)
                _startup_log(f"язык интерфейса выбран: {_chosen_language}")
        except Exception as _language_dialog_error:
            try:
                _applog.warning("Startup language dialog failed: %s", _language_dialog_error)
            except Exception:
                pass
        _pix_mb = int(_settings_for_pixmap.get("pixmap_cache_mb", 128) or 128)
        _pix_mb = max(32, min(512, _pix_mb))
        QPixmapCache.setCacheLimit(_pix_mb * 1024)
    except Exception:
        try:
            QPixmapCache.setCacheLimit(128 * 1024)
        except Exception:
            pass
    app.setStyle("Fusion")
    app.setApplicationName("Local Booru")
    app.setOrganizationName("Local Booru")

    # Install global Qt + Python exception handler
    _startup_log("установка глобального обработчика ошибок")
    install_global_exception_handler(app)

    # Start thumbnail service (background QThreadPool, 3 workers)
    _startup_log("запуск thumbnail service")
    from core.thumb_service import ThumbnailService
    thumb_svc = ThumbnailService.instance(max_threads=3)
    try:
        from core.shutdown import register as _shutdown_register
        _shutdown_register("thumbnail service", thumb_svc.stop)
    except Exception:
        pass

    _db_startup_results = {}
    try:
        _startup_log("загрузка основных настроек")
        _startup_settings = load_settings()
        # Validate the existing working DB before any migration or maintenance writes.
        # A broken disposable gallery may be rebuilt, but it must not be changed
        # silently while the user is deciding what to do.
        from core.stability import run_startup_checks
        _startup_log("SQLite startup fast checks / миграции")
        _db_startup_results = run_startup_checks(_startup_settings, log=lambda m: (_startup_log(str(m)), _applog.info(m))[1])
        _startup_log("SQLite startup fast checks завершены")
        # v406: settings-level guard complements the .initialized marker near
        # the DB.  The marker protects the normal case; this flag also catches
        # "folder/marker disappeared" after the app has successfully used a DB
        # at least once.  It is written only here on the canonical settings dict
        # in the main thread, never from database.connect() worker/session copies.
        try:
            if not bool(_startup_settings.get("db_initialized_once", False)):
                _startup_settings["db_initialized_once"] = True
                from core.settings import save_settings as _save_settings
                _save_settings(_startup_settings)
                _startup_log("SQLite DB guard enabled: db_initialized_once=True")
        except Exception as _db_guard_save_error:
            _applog.warning("Could not persist db_initialized_once guard: %s", _db_guard_save_error)
        _sqlite_writes_wait_for_health = bool(_db_startup_results.get("write_deferred_until_health"))
        if _db_startup_results.get("write_blocked") and not _sqlite_writes_wait_for_health:
            raise RuntimeError("SQLite is in read-only safety mode: " + str(_db_startup_results.get("db_integrity", "integrity check failed")))
        # One-time transitions from pre-SQLite compatibility files. These are
        # audit-preserving migrations; original media is never touched.  After a
        # bad shutdown, skip startup DB writes until the background health check
        # has cleared the temporary read-only gate.
        if not _sqlite_writes_wait_for_health:
            try:
                _startup_log("миграция legacy deleted registry")
                from core.deleted_registry import migrate_legacy_registry
                _deleted_migration = migrate_legacy_registry(_startup_settings)
                if int(_deleted_migration.get("imported", 0) or 0):
                    _applog.info("Migrated %s legacy deleted-MD5 rule(s) into SQLite.", _deleted_migration.get("imported", 0))
            except Exception as _registry_error:
                _applog.warning("Deleted-MD5 registry migration failed: %s", _registry_error)
            try:
                _startup_log("миграция legacy NO_MATCH cache")
                from core.nomatch_db import migrate_legacy_nomatch_cache
                _nm_migration = migrate_legacy_nomatch_cache(_startup_settings)
                if int(_nm_migration.get("imported", 0) or 0):
                    _applog.info("Migrated %s legacy NO_MATCH item(s) into SQLite.", _nm_migration.get("imported", 0))
            except Exception as _nm_error:
                _applog.warning("NO_MATCH migration failed: %s", _nm_error)
        else:
            _startup_log("SQLite записи отложены до фоновой проверки; startup DB-миграции пропущены")
        thumb_svc.configure(
            max_threads=int(_startup_settings.get("thumb_threads", 3) or 3),
            memory_items=int(_startup_settings.get("thumb_memory_items", 400) or 400),
        )
        if not _sqlite_writes_wait_for_health:
            try:
                from core.stability import record_health_event
                record_health_event(_startup_settings, "ok", str(_db_startup_results.get("db_integrity", "quick_check ok")))
            except Exception as _health_event_error:
                _applog.warning("Could not record DB health event: %s", _health_event_error)
        _startup_log("очистка кэша превью")
        cleanup_preview_cache(_startup_settings)
        _startup_log("очистка кэша превью завершена")
        _startup_log("очистка .part файлов")
        from core.file_safety import cleanup_partial_files
        from core.paths import result_output_base
        _removed_parts = cleanup_partial_files(result_output_base(_startup_settings))
        if not _sqlite_writes_wait_for_health:
            try:
                _startup_log("проверка Inbox/Trash lifecycle")
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
        else:
            _startup_log("Inbox/Trash lifecycle отложен до успешной фоновой проверки SQLite")
        if _removed_parts:
            _applog.warning(
                "Removed %s unfinished .part file(s) left by an interrupted write; "
                "affected downloads remain eligible for retry.",
                _removed_parts,
            )
    except Exception as _cleanup_error:
        try:
            from core.database.connection import DatabaseMissingError
        except Exception:
            DatabaseMissingError = None
        if DatabaseMissingError is not None and isinstance(_cleanup_error, DatabaseMissingError):
            _applog.error("SQLite database is missing: %s", _cleanup_error)
            _startup_log(f"SQLite база не найдена: {_cleanup_error}")
            try:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(
                    None,
                    "SQLite база не найдена",
                    "Local Booru отказался создавать новую пустую SQLite вместо уже инициализированной базы.\n\n"
                    + str(_cleanup_error),
                )
            except Exception:
                pass
            _STARTUP_DONE = True
            return 2
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
        _startup_log("запуск filesystem watcher")
        from core.settings import load_settings as _ls
        from core.filesystem_watcher import LibraryWatcher
        from core.shutdown import register as _shutdown_register
        _settings_for_watch = _ls()
        watcher = LibraryWatcher(_settings_for_watch, log=lambda m: _applog.info(m))
        if watcher.start():
            _shutdown_register("library watcher", watcher.stop)
    except Exception as _we:
        _applog.warning("Watcher startup failed: %s", _we)

    _startup_log("создание главного окна")
    w = MainWindow()
    _startup_log("главное окно создано")
    if icon:
        w.setWindowIcon(icon)
    _startup_log("показ главного окна")
    w.show()

    # Browser extension companion API.  Starts after the UI is visible so a busy
    # or locked SQLite DB can never block the main window from appearing.
    browser_companion = None
    try:
        _startup_log("запуск Browser Companion API")
        from core.browser_companion_api import start_browser_companion_api
        from core.shutdown import register as _shutdown_register
        browser_companion = start_browser_companion_api(load_settings(), log_fn=lambda m: (_startup_log(str(m)), _applog.info(m))[1])
        if browser_companion is not None:
            _shutdown_register("browser companion API", browser_companion.stop)
    except Exception as _browser_companion_error:
        _applog.warning("Browser companion API startup failed: %s", _browser_companion_error)

    _startup_log("главное окно показано; запуск завершён")
    _STARTUP_DONE = True

    # v163: run slow SQLite health checks only after the UI is visible.
    # A 400+ MB database can spend minutes in PRAGMA quick_check after a bad
    # shutdown; doing it before w.show() makes startup look frozen.
    try:
        if _db_startup_results.get("deferred_health_check"):
            def _deferred_db_health() -> None:
                try:
                    from core.settings import load_settings as _ls
                    from core.stability import run_deferred_db_health_check
                    _startup_log("фоновая проверка SQLite начата")
                    _res = run_deferred_db_health_check(
                        _ls(),
                        log=lambda m: (_startup_log(str(m)), _applog.info(m))[1],
                    )
                    if _res.get("write_blocked"):
                        _startup_log("фоновая проверка SQLite нашла ошибку; запись заблокирована")
                    else:
                        _startup_log("фоновая проверка SQLite завершена")
                except Exception as _bg_db_error:
                    _applog.warning("Deferred DB health check failed: %s", _bg_db_error)
                    _startup_log(f"фоновая проверка SQLite ошибка: {_bg_db_error}")
            threading.Thread(target=_deferred_db_health, name="deferred-db-health", daemon=True).start()
    except Exception as _defer_error:
        _applog.warning("Could not start deferred DB health check: %s", _defer_error)

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

    # aboutToQuit happens while Qt is still closing.  Do not mark the previous
    # run as clean here; post-UI SQLite/backup maintenance still has to finish.
    # The clean flag is written at the very end of main().
    def _on_qt_about_to_quit():
        try:
            from core.shutdown import begin_shutdown
            begin_shutdown()
        except Exception:
            pass
    app.aboutToQuit.connect(_on_qt_about_to_quit)

    _startup_log("Qt event loop запущен")
    ret = app.exec()

    # Graceful shutdown: stop background pools, watcher and pooled DB handles.
    # This runs after the Qt window is already gone, so slow drivers/backups do
    # not make Windows mark the visible app as "not responding" on close.
    _STARTUP_DONE = True
    try:
        from core.shutdown import request_shutdown
        request_shutdown()
    except Exception:
        try:
            thumb_svc.stop()
        except Exception:
            pass

    # Post-UI maintenance that used to run in MainWindow.closeEvent().  Keep it
    # outside the GUI event handler: large thumbnail caches, external SSD
    # backups and TRUNCATE checkpoints are allowed to take time, but they must
    # not freeze the close button.
    try:
        _exit_settings = getattr(w, "settings", None) or load_settings()
    except Exception:
        _exit_settings = {}
    try:
        if bool((_exit_settings or {}).get("thumb_cleanup_on_exit", True)):
            from core.library_lifecycle import trim_thumbnail_cache
            trim_thumbnail_cache(_exit_settings)
    except Exception as _thumb_exit_error:
        _applog.warning("Exit thumbnail trim failed: %s", _thumb_exit_error)
    try:
        from core.light_backup import maybe_auto_backup, checkpoint_sqlite
        if bool((_exit_settings or {}).get("light_backup_on_exit", True)):
            result = maybe_auto_backup(_exit_settings, reason="app_exit")
            if result.get("created"):
                try:
                    from core.settings import save_settings as _save_settings
                    _save_settings(_exit_settings)
                except Exception:
                    pass
        if bool((_exit_settings or {}).get("sqlite_checkpoint_on_exit", True)):
            checkpoint_sqlite(_exit_settings, truncate=True, optimize=True)
    except Exception as _db_exit_error:
        # Shutdown must not be blocked by an external SSD being absent or a
        # transient SQLite lock.  The next manual/automatic backup will retry.
        _applog.warning("Exit backup/checkpoint failed: %s", _db_exit_error)

    try:
        from core.stability import on_clean_exit
        on_clean_exit(_exit_settings)
    except Exception:
        pass

    return ret


if __name__ == "__main__":
    sys.exit(main())
