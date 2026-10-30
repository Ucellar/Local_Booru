"""One web gallery server per program run, shared by the program window and any browser."""
from __future__ import annotations

import logging
import threading
from typing import Callable

_LOG = logging.getLogger("local_booru.webui")
_LOCK = threading.Lock()
_SERVER = None
_BRIDGE = None        # the program window's AppBridge (ui/web_bridge.py), None without a window


def ensure_started(settings_provider: Callable[[], dict]):
    """The running server; started on first use.  ``settings_provider`` returns the live settings."""
    global _SERVER
    with _LOCK:
        if _SERVER is None:
            from webui.server import WebUI
            settings = settings_provider() or {}
            port = int(settings.get("webui_port", 0) or 0)
            server = WebUI(settings_provider, port=port)
            try:
                server.start()
            except OSError as exc:            # the chosen port is taken: any free one will do
                if not port:
                    raise
                _LOG.warning("web gallery port %s is busy (%s), using a free one", port, exc)
                server = WebUI(settings_provider)
                server.start()
            _SERVER = server
        return _SERVER


def running():
    return _SERVER


def stop() -> None:
    global _SERVER
    with _LOCK:
        server, _SERVER = _SERVER, None
    if server is not None:
        server.stop()


def set_bridge(bridge) -> None:
    global _BRIDGE
    _BRIDGE = bridge


def bridge():
    return _BRIDGE


# ── what a page asks the program window and waits for ─────────────────────
# The window takes a call (``begin_call``) before it acts; a page that stops waiting first drops it —
# so a late window never does what the user was already told is not done.
def call_window(name: str, payload: dict, *, wait: float, finish_wait: float = 60.0) -> str:
    """Ask the window for ``name`` and wait.  "done" — it is done; "no_window"; "refused" — the window
    does not know the request; "dropped" — the window did not take it within ``wait`` seconds and never
    will; "late" — it took it but did not finish within ``finish_wait`` seconds more."""
    window = _BRIDGE
    if window is None:
        return "no_window"
    done = threading.Event()
    claim = {"state": "waiting", "lock": threading.Lock()}
    if not window.request(name, dict(payload, done=done, claim=claim)):
        return "refused"
    if done.wait(wait):
        return "done"
    with claim["lock"]:
        if claim["state"] == "waiting":
            claim["state"] = "dropped"
            return "dropped"
    return "done" if done.wait(finish_wait) else "late"


def begin_call(payload) -> bool:
    """The window's side of ``call_window``: True when it may act, False when the page stopped waiting."""
    claim = payload.get("claim") if isinstance(payload, dict) else None
    if not isinstance(claim, dict) or "lock" not in claim:
        return True
    with claim["lock"]:
        if claim.get("state") == "dropped":
            return False
        claim["state"] = "running"
        return True
