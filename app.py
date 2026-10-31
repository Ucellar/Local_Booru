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

_startup_log("импорт PySide6 / Qt")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
_startup_log("PySide6 импортирован")

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


def _workspace_available_or_restart() -> bool:
    """The chosen archive is unreachable (external drive not connected): ask before anything is written.

    Starting anyway used to open an empty library in Documents and the first save deleted
    the pointer to the archive, so after connecting the drive the archive was "lost".
    Returns False when this process must exit (the user quit, or a restarted copy takes over).
    """
    from core.paths import WORKSPACE_UNAVAILABLE, remove_workspace_pointer, unavailable_workspace_target
    if not WORKSPACE_UNAVAILABLE:
        return True
    from PySide6.QtWidgets import QMessageBox
    _startup_log(f"архив недоступен: {WORKSPACE_UNAVAILABLE}")
    while True:
        box = QMessageBox()
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Архив не найден")
        box.setText(f"Не найден архив Local Booru:\n{WORKSPACE_UNAVAILABLE}\n\n"
                    "Скорее всего, не подключён диск. Подключи его и нажми «Повторить».")
        box.setInformativeText("Пока архив недоступен, программа ничего не сохраняет, "
                               "поэтому твои настройки и библиотека не пострадают.")
        retry = box.addButton("Повторить", QMessageBox.AcceptRole)
        forget = box.addButton("Забыть этот архив…", QMessageBox.DestructiveRole)
        box.addButton("Выход", QMessageBox.RejectRole)
        box.setDefaultButton(retry)
        box.exec()
        clicked = box.clickedButton()
        if clicked is retry:
            if unavailable_workspace_target() is None:
                _restart_self()
                return False
            continue
        if clicked is forget:
            sure = QMessageBox.question(
                None, "Забыть архив?",
                "Программа откроется с новой пустой библиотекой в «Документах».\n"
                "Сам архив на диске не удаляется, его можно подключить снова в Настройках.\n\nПродолжить?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if sure == QMessageBox.Yes:
                remove_workspace_pointer()
                _restart_self()
            return False
        return False


def _ask_pending_reset(request: dict) -> str:
    """«Удалить всё» ordered on the page is done at this start only if the person says so now: the order can
    be days old.  "yes" / "cancel" / "later" (also without a person: LOCAL_BOORU_SKIP_RESET_DIALOG)."""
    if os.environ.get("LOCAL_BOORU_SKIP_RESET_DIALOG", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "later"
    from PySide6.QtWidgets import QMessageBox
    from core.library_reset import describe_request
    box = QMessageBox()
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Полный сброс библиотеки")
    box.setText(describe_request(request))
    go = box.addButton("Удалить всё", QMessageBox.DestructiveRole)
    drop = box.addButton("Отменить сброс", QMessageBox.RejectRole)
    later = box.addButton("Не сейчас", QMessageBox.NoRole)
    box.setDefaultButton(later)
    box.setEscapeButton(later)
    box.exec()
    clicked = box.clickedButton()
    return "yes" if clicked is go else ("cancel" if clicked is drop else "later")


def _finish_archive_move(settings: dict) -> bool:
    """«Перенести архив на другой диск» (core/archive_move.py): the files were copied while the program ran; here,
    before anything opens the base, the rest is done after one more question.  True — the program starts again on the
    moved archive (this process ends)."""
    from core import archive_move
    archive_move.recover()                  # (a start that ended while the base had its other name for a moment)
    order = archive_move.pending()
    if order is None or order.get("phase") != archive_move.COPIED:
        return False
    if os.environ.get("LOCAL_BOORU_SKIP_MOVE_DIALOG", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox, QProgressDialog
    box = QMessageBox()
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("Перенос архива")
    box.setText(archive_move.describe(order))
    if order.get("error"):
        box.setInformativeText(f"В прошлый раз перенос не закончился: {order['error']}")
    go = box.addButton("Перенести сейчас", QMessageBox.AcceptRole)
    drop = box.addButton("Отменить перенос", QMessageBox.DestructiveRole)
    later = box.addButton("Не сейчас", QMessageBox.RejectRole)
    box.setDefaultButton(go)
    box.setEscapeButton(later)
    box.exec()
    clicked = box.clickedButton()
    said = archive_move.answer(order, "yes" if clicked is go else ("cancel" if clicked is drop else "later"))
    if said == "cancel":
        QMessageBox.information(None, "Перенос архива", "Перенос отменён. Сделанная копия осталась в\n"
                                f"{order.get('target')}\n— её можно удалить вручную.")
        return False
    if said != "yes":
        return False
    dialog = QProgressDialog("Переношу архив…", "", 0, 1000)
    dialog.setWindowTitle("Перенос архива")
    dialog.setCancelButton(None)
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.show()
    QApplication.processEvents()

    def progress(text: str, done: int = 0, total: int = 0) -> None:
        dialog.setLabelText(text)
        dialog.setValue(int(1000 * done / total) if total else 0)
        QApplication.processEvents()
    result = archive_move.finish(order, progress=progress, log=_startup_log)
    dialog.close()
    if result.get("done"):
        QMessageBox.information(None, "Перенос архива", f"Архив перенесён:\n{result['target']}\n\n"
                                "Программа сейчас откроется с ним заново.")
        _restart_self()
        return True
    QMessageBox.warning(None, "Перенос архива", f"Перенос не закончен: {result.get('error') or 'неизвестно почему'}\n\n"
                        "Программа откроется со старым архивом, в нём ничего не изменилось. При следующем запуске "
                        "программа спросит о переносе снова; отменить его можно в «Настройках» → «Папки».")
    return False


def _finish_archive_create(settings: dict) -> bool:
    """«Создать архив» (core/archive_create.py): ordered on «Настройки» while the base and the settings were in
    «Документах»; here, before anything opens the base, it is made after one more question.  True — the program starts
    again on the new archive (this process ends)."""
    from core import archive_create
    order = archive_create.pending()
    if order is None:
        return False
    if os.environ.get("LOCAL_BOORU_SKIP_CREATE_DIALOG", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox, QProgressDialog
    box = QMessageBox()
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("Создание архива")
    box.setText(archive_create.describe(order))
    if order.get("error"):
        box.setInformativeText(f"В прошлый раз архив не создан: {order['error']}")
    go = box.addButton("Создать сейчас", QMessageBox.AcceptRole)
    drop = box.addButton("Отменить", QMessageBox.DestructiveRole)
    later = box.addButton("Не сейчас", QMessageBox.RejectRole)
    box.setDefaultButton(go)
    box.setEscapeButton(later)
    box.exec()
    clicked = box.clickedButton()
    said = archive_create.answer(order, "yes" if clicked is go else ("cancel" if clicked is drop else "later"))
    if said == "cancel":
        QMessageBox.information(None, "Создание архива", "Архив не создаётся: программа работает с папкой в "
                                "«Документах», как раньше. Создать его можно в «Настройках» → «Папки».")
        return False
    if said != "yes":
        return False
    dialog = QProgressDialog("Создаю архив…", "", 0, 1000)
    dialog.setWindowTitle("Создание архива")
    dialog.setCancelButton(None)
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.show()
    QApplication.processEvents()

    def progress(text: str, done: int = 0, total: int = 0) -> None:
        dialog.setLabelText(text)
        dialog.setValue(int(1000 * done / total) if total else 0)
        QApplication.processEvents()
    result = archive_create.finish(order, settings, progress=progress, log=_startup_log)
    dialog.close()
    if result.get("done"):
        QMessageBox.information(None, "Создание архива", f"Архив создан:\n{result['archive']}\n\n"
                                "Программа сейчас откроется с ним заново. Папка в «Документах» осталась как была.")
        _restart_self()
        return True
    QMessageBox.warning(None, "Создание архива", f"Архив не создан: {result.get('error') or 'неизвестно почему'}\n\n"
                        "Программа откроется как раньше, с папкой в «Документах», в ней ничего не изменилось. При "
                        "следующем запуске программа спросит снова; отменить можно в «Настройках» → «Папки».")
    return False


def _restart_self() -> None:
    """Start a fresh copy of the program (paths are resolved once per process)."""
    import subprocess
    args = [sys.executable] + ([] if getattr(sys, "frozen", False) else [str(Path(__file__).resolve())]) + sys.argv[1:]
    subprocess.Popen(args, cwd=str(Path(__file__).resolve().parent))


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

    # «Рисовать страницы — без видеокарты»: Qt WebEngine takes its switches before the application object (and after a
    # start whose window died before its first page, this one draws without the graphics card: ui/program_window.py).
    try:
        from ui.program_window import apply_browser_flags, gpu_turned_off
        if apply_browser_flags():
            _startup_log("встроенный браузер рисует без видеокарты ("
                         + ("в прошлый раз окно не показало ни одной страницы)" if gpu_turned_off()
                            else "«Настройки» → «Окно программы»)"))
            _applog.info("Built-in browser: drawing without the graphics card (webview_gpu=off)")
    except Exception as _flags_error:
        _startup_log(f"режим отрисовки встроенного браузера не прочитан: {_flags_error}")

    # Qt WebEngine (the new gallery is shown by it) has to be loaded before the application object.
    try:
        from PySide6 import QtWebEngineWidgets as _webengine_preload  # noqa: F401
    except ImportError as _webengine_error:
        _startup_log(f"Qt WebEngine недоступен ({_webengine_error}): страницы программы откроются в браузере")

    _startup_log("создание QApplication")
    app = QApplication(sys.argv)
    _startup_log("QApplication создан")
    if not _workspace_available_or_restart():
        _STARTUP_DONE = True
        return 3
    try:
        _startup_log("загрузка настроек для первых вопросов")
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
        # v458: one process-wide media-download gate. An environment override
        # is useful for headless/tests; otherwise the user may choose on every
        # launch without changing the persistent default.
        try:
            from core.download_policy import configure_session
            _download_env = os.environ.get("LOCAL_BOORU_MEDIA_DOWNLOADS", "").strip().lower()
            if _download_env in {"0", "false", "no", "off"}:
                _downloads_enabled = False
                _download_reason = "environment_override"
            elif _download_env in {"1", "true", "yes", "on"}:
                _downloads_enabled = True
                _download_reason = "environment_override"
            elif bool(_settings_for_pixmap.get("session_media_downloads_prompt", False)) and os.environ.get("LOCAL_BOORU_SKIP_DOWNLOAD_DIALOG", "").strip().lower() not in {"1", "true", "yes", "on"}:
                _startup_log("выбор политики скачивания медиа на текущую сессию")
                from ui.download_session_dialog import choose_session_media_downloads
                _downloads_enabled = choose_session_media_downloads(bool(_settings_for_pixmap.get("session_media_downloads_default", True)))
                _download_reason = "startup_dialog"
            else:
                _downloads_enabled = bool(_settings_for_pixmap.get("session_media_downloads_default", True))
                _download_reason = "settings_default"
            configure_session(_downloads_enabled, reason=_download_reason)
            _startup_log("медиа-загрузки в этой сессии: " + ("разрешены" if _downloads_enabled else "ЗАПРЕЩЕНЫ"))
        except Exception as _download_policy_error:
            try:
                _applog.warning("Startup download-policy dialog failed: %s", _download_policy_error)
            except Exception:
                pass
    except Exception as _first_settings_error:    # the window reads the settings again and says what is wrong
        _applog.warning("Settings not read for the questions at the start: %s", _first_settings_error)
    app.setStyle("Fusion")
    app.setApplicationName("Local Booru")
    app.setOrganizationName("Local Booru")

    # Install global Qt + Python exception handler
    _startup_log("установка глобального обработчика ошибок")
    install_global_exception_handler(app)

    _db_startup_results = {}
    _sqlite_writes_wait_for_health = False
    _db_schema_state = {"ready": False, "error": ""}
    try:
        _startup_log("загрузка основных настроек")
        _startup_settings = load_settings()
        # «Удалить всё» of the web page «Удаление и сброс» is done here, before anything opens the base
        # or the library's files (core/library_reset.py), after one more question.  It clears
        # «db_initialized_once» in these very settings (and saves them), so the checks below make the new
        # base instead of reporting it missing.  A failure of it never stops the start.
        # «Перенести архив на другой диск»: the files were copied while the program ran; the base and the rest are
        # moved here, before anything opens them — then the program starts again on the moved archive.
        try:
            if _finish_archive_move(_startup_settings):
                _STARTUP_DONE = True
                return 5
        except Exception as _move_error:
            _applog.error("Archive move at start failed: %s", _move_error)
        # «Создать архив» (the base and the settings still in «Документах»): copied into the archive the same way
        try:
            if _finish_archive_create(_startup_settings):
                _STARTUP_DONE = True
                return 5
        except Exception as _create_error:
            _applog.error("Archive creation at start failed: %s", _create_error)
        _reset_result = None
        try:
            from core.library_reset import perform_pending_reset
            _reset_result = perform_pending_reset(_startup_settings, confirm=_ask_pending_reset,
                                                  log=lambda m: (_startup_log(str(m)), _applog.info(m))[1])
        except Exception as _reset_error:
            _applog.error("Library reset at start failed: %s", _reset_error)
        if isinstance(_reset_result, dict) and _reset_result.get("busy"):
            # another copy of the program is resetting the library right now: this one must not open it
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(None, "Сброс библиотеки",
                                    "Другая копия программы сейчас выполняет сброс библиотеки.\n"
                                    "Дождитесь, пока откроется её окно.")
            _STARTUP_DONE = True
            return 4
        # Validate the existing working DB before any migration or maintenance writes.
        # A broken disposable gallery may be rebuilt, but it must not be changed
        # silently while the user is deciding what to do.
        from core.stability import run_startup_checks
        _startup_log("SQLite startup fast checks")
        _db_startup_results = run_startup_checks(_startup_settings, log=lambda m: (_startup_log(str(m)), _applog.info(m))[1])
        _startup_log("SQLite startup fast checks завершены")
        if _db_startup_results.get("db_missing"):
            from core.database.connection import DatabaseMissingError
            raise DatabaseMissingError(str(_db_startup_results["db_missing"]))
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
        if _sqlite_writes_wait_for_health:
            # (v5.44.5) the base is older than the program and its update waits for the background check: the pages
            # say so and wait (they need the new tables) instead of «no such table»
            try:
                from core.database.connection import schema_behind, set_pages_wait
                if schema_behind(_startup_settings):
                    set_pages_wait("Программа в прошлый раз закрылась аварийно, поэтому сначала проверяет базу, а потом "
                                   "обновит её до новой версии. Страница откроется сама, когда это закончится.")
                    _startup_log("страницы ждут обновления базы (после фоновой проверки)")
            except Exception as _behind_error:   # not knowing it changes nothing: the pages open as before
                _applog.warning("Schema version not read at start: %s", _behind_error)
        if not _sqlite_writes_wait_for_health:
            _startup_log("SQLite writable schema migrations")
            from core.database.connection import ensure_database_schema
            _schema_status = ensure_database_schema(_startup_settings)
            _db_schema_state["ready"] = True
            _startup_log(
                f"SQLite schema ready: v{int(_schema_status.get('current', 0) or 0)}/"
                f"v{int(_schema_status.get('target', 0) or 0)}"
            )
            try:
                from core.quality_upgrade_service import recover_incomplete_quality_upgrades
                _upgrade_recovery = recover_incomplete_quality_upgrades(
                    _startup_settings, trace=lambda m: _applog.info(str(m))
                )
                if _upgrade_recovery:
                    _startup_log(f"quality-upgrade recovery: {len(_upgrade_recovery)} revision(s)")
            except Exception as _upgrade_recovery_error:
                _applog.error("Quality-upgrade startup recovery failed: %s", _upgrade_recovery_error)
                raise
        else:
            _startup_log("SQLite schema migrations отложены до фоновой проверки")
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
        _removed_parts = cleanup_partial_files(result_output_base(_startup_settings), settings=_startup_settings)
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
    try:
        _window_settings = load_settings()
    except Exception as _window_settings_error:   # the window loads them again and says what is wrong
        _applog.warning("Settings not read for the choice of window: %s", _window_settings_error)
        _window_settings = {}
    from ui.program_window import make as _make_window
    w = _make_window(_window_settings, say=_startup_log)
    _startup_log("главное окно создано")
    if icon:
        w.setWindowIcon(icon)
    _startup_log("показ главного окна")
    (getattr(w, "show_as_saved", None) or w.show)()     # the new window: maximized, as the person left it

    # Browser extension companion API.  It may write immediately after a click,
    # so it starts only after a successful writable schema/migration pass.
    _browser_companion_lock = threading.Lock()
    _browser_companion_state = {"handle": None}

    def _start_browser_companion_when_schema_ready():
        with _browser_companion_lock:
            if _browser_companion_state.get("handle") is not None:
                return _browser_companion_state.get("handle")
            if not bool(_db_schema_state.get("ready")):
                _startup_log("Browser Companion API отложен: SQLite schema ещё не готова")
                return None
            try:
                from core.shutdown import is_shutting_down
                if is_shutting_down():
                    return None
            except Exception:
                pass
            _startup_log("запуск Browser Companion API")
            from core.browser_companion_api import start_browser_companion_api
            from core.shutdown import register as _shutdown_register
            handle = start_browser_companion_api(
                load_settings(),
                log_fn=lambda m: (_startup_log(str(m)), _applog.info(m))[1],
            )
            if handle is not None:
                _browser_companion_state["handle"] = handle
                _shutdown_register("browser companion API", handle.stop)
            return handle

    try:
        _start_browser_companion_when_schema_ready()
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
                        from core.database.connection import pages_wait, set_pages_wait
                        if pages_wait():
                            set_pages_wait("Проверка базы нашла ошибку, поэтому обновить её до новой версии нельзя. "
                                           "Подробности — в журнале программы (окно «запуск и журнал»).", failed=True)
                    else:
                        _startup_log("фоновая проверка SQLite завершена")
                        try:
                            from core.database.connection import ensure_database_schema
                            _startup_log("SQLite writable schema migrations после фоновой проверки")
                            _schema_status = ensure_database_schema(_ls(), force=True)
                            _db_schema_state["ready"] = True
                            from core.database.connection import set_pages_wait
                            set_pages_wait("")
                            _startup_log(
                                f"SQLite schema ready: v{int(_schema_status.get('current', 0) or 0)}/"
                                f"v{int(_schema_status.get('target', 0) or 0)}"
                            )
                            from core.quality_upgrade_service import recover_incomplete_quality_upgrades
                            _upgrade_recovery = recover_incomplete_quality_upgrades(
                                _ls(), trace=lambda m: _applog.info(str(m))
                            )
                            if _upgrade_recovery:
                                _startup_log(f"quality-upgrade recovery: {len(_upgrade_recovery)} revision(s)")
                            _start_browser_companion_when_schema_ready()
                        except Exception as _schema_after_health_error:
                            _db_schema_state["ready"] = False
                            _db_schema_state["error"] = str(_schema_after_health_error)
                            from core.database.connection import pages_wait, set_pages_wait
                            if pages_wait():
                                set_pages_wait(f"Базу не удалось обновить до новой версии: {_schema_after_health_error}",
                                               failed=True)
                            _applog.warning(
                                "Deferred SQLite schema migration failed: %s",
                                _schema_after_health_error,
                            )
                            _startup_log(
                                f"SQLite schema migration error: {_schema_after_health_error}"
                            )
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
            from webui import service as _webui_service
            _webui_service.stop()
        except Exception as _webui_stop_error:  # closing must go on; the server threads are daemons
            _applog.warning("Web gallery stop failed: %s", _webui_stop_error)
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
    _shutdown_join_ok = True
    _shutdown_join_alive = []
    try:
        from core.shutdown import request_shutdown
        _shutdown_result = request_shutdown(timeout_hint=15.0, qt_roots=[w])
        _shutdown_join_ok = bool((_shutdown_result or {}).get("ok", True))
        _shutdown_join_alive = list((_shutdown_result or {}).get("alive", []) or [])
    except Exception as _shutdown_error:
        _shutdown_join_ok = False
        _shutdown_join_alive = ["shutdown manager error"]
        _applog.warning("Shutdown manager failed: %s", _shutdown_error)

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
        if not _shutdown_join_ok:
            _applog.warning(
                "Skipping exit backup/checkpoint because worker threads did not stop: %s",
                ", ".join(map(str, _shutdown_join_alive)) or "unknown",
            )
            try:
                from core.stability import write_shutdown_flag
                write_shutdown_flag(_exit_settings, bad=True)
            except Exception:
                pass
        else:
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
        if _shutdown_join_ok:
            from core.stability import on_clean_exit
            on_clean_exit(_exit_settings)
    except Exception:
        pass

    # «Перезапустить» of a page (core/app_restart.py): a new copy, now that the base and the files are closed
    try:
        from core import app_restart
        if app_restart.requested():
            _restart_self()
    except Exception as _restart_error:
        _applog.warning("Restart after exit failed: %s", _restart_error)

    return ret


def _selfcheck() -> int:
    """``Local Booru.exe --selfcheck``: every module of the program imports in this build, and Qt WebEngine with them
    (the release build checks its folder so).  No window, no library: the answer — to the file named in
    LOCAL_BOORU_SELFCHECK_OUT, else to the console; the exit code is 0 only when nothing failed."""
    import importlib
    import pkgutil
    names, failed = [], []
    for package in ("core", "ui", "webui"):
        try:
            module = importlib.import_module(package)
            names += [info.name for info in pkgutil.walk_packages(module.__path__, package + ".")]
        except Exception as exc:
            failed.append(f"{package}: {type(exc).__name__}: {exc}")
    for name in sorted(set(names)):
        try:
            importlib.import_module(name)
        except Exception as exc:
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
    try:
        from PySide6 import QtWebEngineWidgets  # noqa: F401
    except Exception as exc:
        failed.append(f"PySide6.QtWebEngineWidgets: {type(exc).__name__}: {exc}")
    try:                                     # the voice-overs into one file (core/video_merge): PyAV and its coders
        import av
        av.codec.Codec("aac", "w")
    except Exception as exc:
        failed.append(f"av (PyAV): {type(exc).__name__}: {exc}")
    text = f"modules: {len(set(names))}, failed: {len(failed)}\n" + "\n".join(failed)
    out = os.environ.get("LOCAL_BOORU_SELFCHECK_OUT", "").strip()
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 1 if failed or not names else 0


if __name__ == "__main__":
    if "--selfcheck" in sys.argv[1:]:
        sys.exit(_selfcheck())
    sys.exit(main())
