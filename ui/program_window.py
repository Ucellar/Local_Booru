"""How the program window opens (``app.py`` asks once, at start).

The window is ``ui/web_shell.py``: the program's web pages shown by Qt WebEngine in the whole window.  Where Qt WebEngine
does not work, the pages open in the usual browser and the window is a small one that keeps the program running and
does what a page asks of it (``WebShell(browser=True)``): when «Открывать» on «Настройки» says so (``ui_shell``
"browser"), or LOCAL_BOORU_UI_SHELL=browser — and by itself:

- no Qt WebEngine in this installation, or the window failed to build (said in the log, the console and the window);
- the window did not show its first page last time: the program died on the way (Qt WebEngine can take the whole
  program down with it).  The start of the window leaves a mark (``RUNTIME_DIR/new_window_starting.txt``: the number of
  the process, the time, whether it drew with the graphics card) that its first page shown for a few seconds, or its
  closing, takes away; a mark left over by a process that is gone means that start never got there.  It drew with the
  graphics card — this start draws without it (``apply_browser_flags``, before the application object; ``make``
  remembers «Рисовать страницы — без видеокарты» and says so); it drew without it already — this start opens the pages
  in the browser, remembers that and says so.  A mark of a process still running is another copy of the program
  starting just now — not a failure.  LOCAL_BOORU_UI_SHELL=web keeps the window whatever happened.

The window's own way back from the browser: «Открыть в окне программы» on the small window, or «Открывать» on
«Настройки».  (Until v5.40 a failure opened the old window with its sidebar of Qt pages; it is gone.)
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable

_LOG = logging.getLogger("local_booru")
SHELLS = ("web", "browser")
GPU_FLAG = "--disable-gpu"
GPU_OFF = ("В прошлый раз программа закрылась, когда окно ещё не показало ни одной страницы: похоже, встроенный "
           "браузер не справляется с видеокартой этого компьютера. Теперь страницы рисуются без видеокарты. Вернуть "
           "как было: «Настройки» → «Окно программы» → «Рисовать страницы» (со следующего запуска).")
FELL_BACK = ("Окно программы закрылось, не показав ни одной страницы, и без видеокарты тоже: встроенный браузер (Qt "
             "WebEngine) не работает на этом компьютере. Поэтому страницы программы открываются в обычном браузере.")
NO_WEBENGINE = ("В этой установке нет встроенного браузера (Qt WebEngine), поэтому страницы программы открываются в "
                "обычном браузере.")
FAILED = "Окно программы не открылось ({error}), поэтому страницы программы открываются в обычном браузере."
_AUTO_OFF: list = []        # this start draws without the graphics card: the last one died drawing with it


def _forced_shell() -> str:
    forced = os.environ.get("LOCAL_BOORU_UI_SHELL", "").strip().lower()
    return forced if forced in SHELLS else ""


def choice(settings: dict) -> str:
    """"web" (the window) or "browser" (the pages in the usual browser).  An old "classic" (the old window, gone in
    v5.40) is the window."""
    forced = _forced_shell()
    if forced:
        return forced
    return "browser" if str((settings or {}).get("ui_shell") or "web").strip().lower() == "browser" else "web"


def _raw_settings(settings_file=None) -> dict:
    """The settings file as it is (``load_settings`` also tidies the workspace, which waits until it is checked)."""
    try:
        if settings_file is None:
            from core.paths import SETTINGS_FILE as settings_file
        import json
        with open(settings_file, "r", encoding="utf-8-sig") as fh:
            raw = json.load(fh)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError, ImportError) as exc:   # no settings yet, or a damaged file: the usual way
        _LOG.debug("settings not read before the window: %s", exc)
        return {}


def browser_flags(settings_file=None) -> str:
    """Chromium switches for the window's built-in browser: «Рисовать страницы — без видеокарты» (``webview_gpu``
    "off", or LOCAL_BOORU_WEBVIEW_GPU=off) gives "--disable-gpu" — some graphics drivers left half-drawn frames of
    big pictures on the screen.  Qt WebEngine reads its switches once, before the application object exists, so
    ``app.py`` asks this first — straight from the settings file: ``load_settings`` also tidies the workspace,
    which waits until the workspace is checked."""
    mode = os.environ.get("LOCAL_BOORU_WEBVIEW_GPU", "").strip().lower()
    if mode not in ("auto", "off"):
        mode = str(_raw_settings(settings_file).get("webview_gpu") or "auto").strip().lower()
    return GPU_FLAG if mode == "off" else ""


def apply_browser_flags(settings_file=None) -> str:
    """Puts ``browser_flags`` into QTWEBENGINE_CHROMIUM_FLAGS (keeping switches set there by hand); returns them.  The
    window died before its first page last time while it drew with the graphics card: this start draws without it
    (``make`` remembers it) — unless LOCAL_BOORU_WEBVIEW_GPU=auto asks for the card."""
    flags = browser_flags(settings_file)
    _AUTO_OFF.clear()
    gpu_asked = os.environ.get("LOCAL_BOORU_WEBVIEW_GPU", "").strip().lower() == "auto"
    if (not flags and not gpu_asked and choice(_raw_settings(settings_file)) == "web" and died_last_time()
            and _mark_gpu() != "off" and GPU_FLAG not in os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()):
        flags = GPU_FLAG
        _AUTO_OFF.append(True)
        _LOG.warning("The window did not show its first page last time while drawing with the graphics card: "
                     "this start draws without it")
    if flags:
        current = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        if flags not in current.split():
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (current + " " + flags).strip()
    return flags


def gpu_turned_off() -> bool:
    """This start draws without the graphics card because the last one died drawing with it."""
    return bool(_AUTO_OFF)


def _mark() -> Path:
    from core.paths import RUNTIME_DIR
    return Path(RUNTIME_DIR) / "new_window_starting.txt"


def drawing_with_gpu() -> bool:
    """This run's built-in browser draws with the graphics card (no "--disable-gpu" among its switches)."""
    return GPU_FLAG not in os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()


def starting() -> None:
    """The window is about to be built."""
    try:
        mark = _mark()
        mark.parent.mkdir(parents=True, exist_ok=True)
        gpu = "on" if drawing_with_gpu() else "off"
        mark.write_text(f"{os.getpid()} {time.strftime('%Y-%m-%d %H:%M:%S')} gpu={gpu}", encoding="utf-8")
    except OSError as exc:            # without the mark only the fallback after a crash is lost
        _LOG.warning("window start mark not written: %s", exc)


def started() -> None:
    """The window showed its first page (or was closed): it does start on this computer."""
    try:
        _mark().unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        _LOG.warning("window start mark not removed: %s", exc)


def _mark_text() -> str | None:
    """The mark's words; None — no mark (or it cannot be read: no failure is assumed)."""
    try:
        return _mark().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _mark_gpu() -> str:
    """"on" / "off": how the start that left the mark drew ("on" for a mark of v5.39 and before, which did not say)."""
    for word in (_mark_text() or "").split():
        if word.startswith("gpu="):
            return "off" if word[4:].strip().lower() == "off" else "on"
    return "on"


def died_last_time() -> bool:
    """A start of the window never got to its first page, and its process is gone."""
    text = _mark_text()
    if text is None:
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
    from ui.web_shell import WebShell, can_show_web
    why = ""
    if choice(settings) == "web":
        tell = ""
        if died_last_time():
            if _mark_gpu() == "on" and not drawing_with_gpu():
                # it drew with the graphics card, this start draws without it: the window once more
                if gpu_turned_off():
                    say("окно в прошлый раз не показало ни одной страницы — страницы рисуются без видеокарты")
                    _remember(settings, webview_gpu="off")
                    tell = GPU_OFF
            elif _forced_shell() != "web":
                say("окно в прошлый раз не показало ни одной страницы и без видеокарты — страницы открываются в браузере")
                _LOG.warning("The window did not show its first page last time, without the graphics card too: "
                             "the pages open in the browser, and this stays chosen")
                started()
                _remember(settings, ui_shell="browser")
                why = FELL_BACK
        if not why:
            try:
                if can_show_web():
                    starting()
                    window = WebShell()
                    if tell:
                        from PySide6.QtCore import QTimer
                        QTimer.singleShot(0, lambda: _tell(window, tell))
                    return window
                why = NO_WEBENGINE
                say("Qt WebEngine недоступен: страницы открываются в браузере")
                _LOG.warning("Qt WebEngine is not available: the pages open in the browser")
            except Exception as exc:  # whatever broke, the program still opens — its pages in the browser
                started()                    # caught here: the next start may try the window again
                from core.redaction import sanitize_text
                text = sanitize_text(f"{type(exc).__name__}: {exc}")
                say(f"окно не открылось ({text}); страницы открываются в браузере")
                _LOG.error("The window failed to open, the pages open in the browser: %s", text, exc_info=True)
                why = FAILED.format(error=text)
    return WebShell(browser=True, why=why)


def _remember(settings: dict, **changes) -> None:
    """A choice made at the start, kept for the next ones (and in ``settings`` of this one)."""
    from core.settings import load_settings, save_settings
    try:
        current = load_settings()
        current.update(changes)
        save_settings(current)
        settings.update(changes)
    except Exception as exc:  # the window opens this time anyway
        _LOG.warning("the choice made at the start not remembered (%s): %s", ", ".join(changes), exc)


def _tell(window, text: str) -> None:
    from PySide6.QtWidgets import QMessageBox
    box = QMessageBox(QMessageBox.Information, "Окно программы", text, QMessageBox.Ok, window)
    box.setObjectName("lb_fell_back_box")
    box.show()
