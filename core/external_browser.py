"""Open URLs in the user's normal browser without owning a browser engine."""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any
from core.quiet import passed as let_pass

_LAUNCH_LOCK = threading.Lock()
_LAST_LAUNCH: dict[str, float] = {}


def is_web_url(url) -> bool:
    """Only http(s) addresses are handed to Windows.  Windows opens any scheme with the program
    registered for it (``ms-msdt:``, ``search-ms:``, ``steam:``…); a source stored in the library
    (an imported sidecar file could store anything) must never start one by a click."""
    return str(url or "").strip().lower().startswith(("http://", "https://"))


def browser_mode(settings: dict[str, Any] | None) -> str:
    mode = str((settings or {}).get("external_browser_mode") or "default").strip().lower()
    return mode if mode in {"default", "custom"} else "default"


def custom_browser_executable(settings: dict[str, Any] | None) -> str:
    raw = str((settings or {}).get("external_browser_executable") or "").strip().strip('"')
    if not raw:
        return ""
    try:
        path = Path(os.path.expandvars(os.path.expanduser(raw)))
        if path.is_file():
            return str(path)
    except Exception:
        let_pass("core.external_browser.custom_browser_executable")
    return ""


def browser_family_matches(preferred: str, actual: str) -> bool:
    wanted = str(preferred or "").strip().lower()
    if not wanted:
        return True
    got = str(actual or "").strip().lower()
    aliases = {
        "chrome": ("chrome", "chromium"),
        "edge": ("edge", "edg"),
        "firefox": ("firefox",),
        "brave": ("brave",),
        "opera": ("opera", "opr"),
    }
    return any(token in got for token in aliases.get(wanted, (wanted,)))


def _custom_args(settings: dict[str, Any] | None) -> list[str]:
    raw = str((settings or {}).get("external_browser_arguments") or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw, posix=False)
    except Exception:
        return []


def open_external_browser_url(
    url: str,
    settings: dict[str, Any] | None = None,
    *,
    dedupe_seconds: float = 3.0,
    log_fn=None,
) -> bool:
    target = str(url or "").strip()
    if not is_web_url(target):
        return False

    now = time.monotonic()
    with _LAUNCH_LOCK:
        previous = float(_LAST_LAUNCH.get(target) or 0.0)
        if now - previous < max(0.0, float(dedupe_seconds or 0.0)):
            return True
        _LAST_LAUNCH[target] = now

    def _log(message: str) -> None:
        if callable(log_fn):
            try:
                log_fn(message)
            except Exception:
                let_pass("core.external_browser.open_external_browser_url._log")

    if browser_mode(settings) == "custom":
        exe = custom_browser_executable(settings)
        if exe:
            try:
                creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) or 0) if os.name == "nt" else 0
                subprocess.Popen(
                    [exe, *_custom_args(settings), target],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=(os.name != "nt"),
                    creationflags=creationflags,
                )
                _log(f"EXTERNAL BROWSER: opened with selected browser: {Path(exe).name}")
                return True
            except Exception as exc:
                _log(f"EXTERNAL BROWSER: selected browser failed: {type(exc).__name__}: {str(exc)[:160]}")
        else:
            _log("EXTERNAL BROWSER: selected executable is missing; using system default")

    try:
        ok = bool(webbrowser.open(target, new=2, autoraise=True))
        _log("EXTERNAL BROWSER: opened with system default browser" if ok else "EXTERNAL BROWSER: browser returned no confirmation")
        return ok
    except Exception as exc:
        _log(f"EXTERNAL BROWSER: system browser failed: {type(exc).__name__}: {str(exc)[:160]}")
        return False
