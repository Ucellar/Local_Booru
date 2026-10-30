"""The program closes and starts again (v5.36: «Перезапустить и закончить перенос» of «Настройки → Папки»): the window
asks for it (:func:`request`) and closes; app.py starts a new copy at the very end of its exit (:func:`requested`),
after the base and the files are closed — the new copy finds them free."""
from __future__ import annotations

import threading

_LOCK = threading.Lock()
_ASKED = [False]


def request() -> None:
    with _LOCK:
        _ASKED[0] = True


def requested() -> bool:
    with _LOCK:
        return _ASKED[0]
