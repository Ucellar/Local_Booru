from __future__ import annotations

import logging
import threading
from typing import Callable

_log = logging.getLogger("local_booru.shutdown")
_callbacks: list[tuple[str, Callable[[], None]]] = []
_lock = threading.Lock()
_shutting_down = False

def register(name: str, callback: Callable[[], None]) -> None:
    with _lock:
        _callbacks.append((str(name), callback))

def is_shutting_down() -> bool:
    return _shutting_down

def request_shutdown(timeout_hint: float = 5.0) -> None:
    global _shutting_down
    _shutting_down = True
    with _lock:
        callbacks = list(reversed(_callbacks))
        _callbacks.clear()
    for name, cb in callbacks:
        try:
            _log.info("Stopping %s", name)
            cb()
        except Exception as e:
            _log.warning("Shutdown callback %s failed: %s", name, e)
    try:
        from core.database.connection import close_pooled_connections
        close_pooled_connections()
    except Exception:
        pass
