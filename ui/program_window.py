"""Which window the program opens (``app.py`` asks once, at start).

The new window (``ui/web_shell.py``: the web pages in the whole window), unless «Окно программы» on «Настройки»
(``ui_shell``) says "classic", or LOCAL_BOORU_UI_SHELL=web|classic says otherwise; and the old window with the
sidebar of Qt pages (``ui/main_window.py``) also when the new one cannot open:

- no Qt WebEngine in this installation, or the new window failed to build (said in the log and in the console);
- the new window did not show its first page last time — the program died on the way (Qt WebEngine can take the
  whole program down with it, and then the switch back to the old window, a page of that window, is out of
  reach).  The start of the new window leaves a mark (``RUNTIME_DIR/new_window_starting.txt``, with the number of
  the process) that its first page shown for a few seconds, or its closing, takes away; a mark left over by a
  process that is gone means that start never got there: this start opens the old window, remembers it in the
  settings and says so.  A mark of a process still running is another copy of the program starting just now — not
  a failure.  ``run_old_window.bat`` opens the old window too.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable

_LOG = logging.getLogger("local_booru")
SHELLS = ("web", "classic")
FELL_BACK = ("В прошлый раз программа закрылась, когда новое окно ещё не показало ни одной страницы (возможно, его "
             "встроенный браузер не работает на этом компьютере), поэтому сейчас открыто старое окно. Попробовать новое "
             "снова: «Галерея» → «Настройки» → «Окно программы» → «новое окно» (со следующего запуска).")


def choice(settings: dict) -> str:
    """"web" or "classic"."""
    forced = os.environ.get("LOCAL_BOORU_UI_SHELL", "").strip().lower()
    if forced in SHELLS:
        return forced
    return "classic" if str((settings or {}).get("ui_shell") or "web").strip().lower() == "classic" else "web"


def browser_flags(settings_file=None) -> str:
    """Chromium switches for the window's built-in browser: «Рисовать страницы — без видеокарты» (``webview_gpu``
    "off", or LOCAL_BOORU_WEBVIEW_GPU=off) gives "--disable-gpu" — some graphics drivers left half-drawn frames of
    big pictures on the screen.  Qt WebEngine reads its switches once, before the application object exists, so
    ``app.py`` asks this first — straight from the settings file: ``load_settings`` also tidies the workspace,
    which waits until the workspace is checked."""
    mode = os.environ.get("LOCAL_BOORU_WEBVIEW_GPU", "").strip().lower()
    if mode not in ("auto", "off"):
        mode = "auto"
        try:
            if settings_file is None:
                from core.paths import SETTINGS_FILE as settings_file
            import json
            with open(settings_file, "r", encoding="utf-8-sig") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                mode = str(raw.get("webview_gpu") or "auto").strip().lower()
        except (OSError, ValueError, ImportError) as exc:   # no settings yet, or a damaged file: the usual way
            _LOG.debug("webview_gpu not read: %s", exc)
    return "--disable-gpu" if mode == "off" else ""


def apply_browser_flags(settings_file=None) -> str:
    """Puts ``browser_flags`` into QTWEBENGINE_CHROMIUM_FLAGS (keeping switches set there by hand); returns them."""
    flags = browser_flags(settings_file)
    if flags:
        current = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        if flags not in current.split():
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (current + " " + flags).strip()
    return flags


def _mark() -> Path:
    from core.paths import RUNTIME_DIR
    return Path(RUNTIME_DIR) / "new_window_starting.txt"


def starting() -> None:
    """The new window is about to be built."""
    try:
        mark = _mark()
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(f"{os.getpid()} {time.strftime('%Y-%m-%d %H:%M:%S')}", encoding="utf-8")
    except OSError as exc:            # without the mark only the fallback after a crash is lost
        _LOG.warning("new window start mark not written: %s", exc)


def started() -> None:
    """The new window showed its first page (or was closed): it does start on this computer."""
    try:
        _mark().unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        _LOG.warning("new window start mark not removed: %s", exc)


def died_last_time() -> bool:
    """A start of the new window never got to its first page, and its process is gone."""
    try:
        text = _mark().read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    except OSError:
        return False
    first = (text.split() or ["0"])[0]
    pid = int(first) if first.isdigit() else 0
    return not (pid and pid != os.getpid() and _process_alive(pid))


def _process_alive(pid: int) -> bool:
    """A process with this number runs now (``os.kill(pid, 0)`` would end it on Windows)."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5                      # access denied: it exists
        try:
            code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def make(settings: dict, *, say: Callable[[str], None] | None = None):
    """The window to show; ``say`` — a line for the startup console.  Qt's own words (the menu over a page, the
    buttons of dialogs) are put in the program's language first (``ui/qt_language.py``)."""
    say = say or (lambda _message: None)
    try:
        from PySide6.QtWidgets import QApplication
        from ui import qt_language
        say(f"язык Qt: {qt_language.apply(QApplication.instance(), settings)}")
    except Exception as exc:  # the window opens anyway — Qt's words stay in English
        _LOG.warning("Qt's own translation not installed: %s", exc)
    if choice(settings) == "web":
        forced = os.environ.get("LOCAL_BOORU_UI_SHELL", "").strip().lower() == "web"
        if died_last_time() and not forced:
            say("новое окно в прошлый раз не показало ни одной страницы — открывается старое")
            _LOG.warning("The new window did not show its first page last time: the old window opens, and stays chosen")
            started()
            _choose_classic(settings)
            window = _old_window()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: _tell(window, FELL_BACK))
            return window
        try:
            from ui.web_shell import WebShell, can_show_web
            if can_show_web():
                starting()
                return WebShell()
            say("Qt WebEngine недоступен: открывается старое окно")
            _LOG.warning("Qt WebEngine is not available: the old window opens")
        except Exception as exc:  # whatever broke, the program still opens — in its old window
            started()                    # caught here: the next start may try the new window again
            from core.redaction import sanitize_text
            text = sanitize_text(f"{type(exc).__name__}: {exc}")
            say(f"новое окно не открылось ({text}); открывается старое")
            _LOG.error("The new window failed to open, the old one opens instead: %s", text, exc_info=True)
    return _old_window()


def _old_window():
    from ui.main_window import MainWindow
    return MainWindow()


def _choose_classic(settings: dict) -> None:
    from core.settings import load_settings, save_settings
    try:
        current = load_settings()
        current["ui_shell"] = "classic"
        save_settings(current)
        settings["ui_shell"] = "classic"
    except Exception as exc:  # the old window opens this time anyway
        _LOG.warning("the old window not remembered as chosen: %s", exc)


def _tell(window, text: str) -> None:
    from PySide6.QtWidgets import QMessageBox
    box = QMessageBox(QMessageBox.Information, "Окно программы", text, QMessageBox.Ok, window)
    box.setObjectName("lb_fell_back_box")
    box.show()
