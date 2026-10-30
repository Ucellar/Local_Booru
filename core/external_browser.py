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
        pass
    return ""


def _family_from_hint(value: str) -> str:
    raw = str(value or "").strip().strip('"').lower()
    name = Path(raw).name.lower()
    haystack = f"{raw} {name}"
    if "firefox" in haystack:
        return "firefox"
    if "msedge" in haystack or "microsoftedge" in haystack or name.startswith("edge"):
        return "edge"
    if "brave" in haystack:
        return "brave"
    if "opera" in haystack or "opr" in haystack:
        return "opera"
    if "chrome" in haystack or "chromium" in haystack:
        return "chrome"
    return ""


def system_default_browser_family() -> str:
    """Best-effort Windows default HTTPS browser family.

    Failure is intentionally harmless: the resolver then binds the task to the
    unique URL fragment opened through ``webbrowser`` instead of guessing a
    browser family.
    """
    if os.name != "nt":
        return ""
    try:
        import winreg  # type: ignore

        key_path = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            prog_id = str(winreg.QueryValueEx(key, "ProgId")[0] or "")
        return _family_from_hint(prog_id)
    except Exception:
        return ""


def preferred_browser_family(settings: dict[str, Any] | None) -> str:
    """Family hint for the browser selected by the user.

    Custom mode derives it from the selected executable.  Default mode asks
    Windows which application owns HTTPS.  An empty result is not treated as
    permission for a random browser: the bridge uses a one-time launch marker.
    """
    if browser_mode(settings) == "custom":
        return _family_from_hint(str((settings or {}).get("external_browser_executable") or ""))
    return system_default_browser_family()

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
                pass

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
