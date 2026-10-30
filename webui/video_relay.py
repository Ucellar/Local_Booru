"""A card's video played inside the page of the program window.

The window's browser (Qt WebEngine) cannot decode H.264, so the program plays the video itself (Qt Multimedia,
ui/video_inline.py) and hands its frames here as JPEG pictures; the page shows them in place of the video —
``/video/live/<token>`` is an endless multipart answer that replaces the picture with every new frame
(webui/pages/video.py) — and its buttons ask the program to pause, seek, change the volume.  The sound plays in
the program.  So the video plays where the page shows it, scrolls with the page, and menus open over it.

This module knows nothing of Qt: one play is one ``token``; the program publishes frames and its state for it,
the server's answers wait for them.  A new play ends the one before (one video at a time).
"""
from __future__ import annotations

import threading
import time

MAX_SIDE = 1920            # the frames are made no bigger than this (and than the place on the page)


class FrameRelay:
    def __init__(self):
        self._cond = threading.Condition()
        self._token = ""            # the play going on ("" — none)
        self._seq = 0               # number of the newest frame of this play
        self._jpeg = b""
        self._state: dict = {}
        self._releasers: list = []

    # ── the program's side ─────────────────────────────────────────────────
    def begin(self, token: str) -> None:
        """A new play: the one before ends (its streams stop)."""
        with self._cond:
            self._token, self._seq, self._jpeg = str(token), 0, b""
            self._state = {"token": str(token), "playing": False, "position": 0.0, "duration": 0.0,
                           "volume": 50, "muted": False, "error": "", "ready": False, "at": time.time()}
            self._cond.notify_all()

    def end(self, token: str = "") -> None:
        """The play ``token`` (any, when empty) is over: its streams end."""
        with self._cond:
            if not token or token == self._token:
                self._token, self._jpeg = "", b""
                self._cond.notify_all()

    def publish(self, token: str, jpeg: bytes) -> None:
        with self._cond:
            if token and token == self._token and jpeg:
                self._jpeg = bytes(jpeg)
                self._seq += 1
                self._cond.notify_all()

    def set_state(self, token: str, **state) -> None:
        with self._cond:
            if token and token == self._token:
                self._state.update(state, token=token, at=time.time())

    # ── the server's side ──────────────────────────────────────────────────
    def active(self, token: str) -> bool:
        with self._cond:
            return bool(token) and token == self._token

    def next_frame(self, token: str, after: int, timeout: float = 2.0) -> tuple[int, bytes] | None:
        """The newest frame after number ``after``: (number, JPEG); None when none came within ``timeout`` or the
        play is over (``active`` tells which)."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while True:
                if token != self._token:
                    return None
                if self._seq > after and self._jpeg:
                    return self._seq, self._jpeg
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cond.wait(left)

    def state(self, token: str) -> dict:
        with self._cond:
            if not token or token != self._token:
                return {"token": token, "over": True}
            return dict(self._state)

    # ── files that are about to move (core: trash, restore) ────────────────
    def add_releaser(self, fn) -> None:
        """``fn(key or None, wait)``: let go of a file the player holds (the program's player registers itself)."""
        with self._cond:
            if fn not in self._releasers:
                self._releasers.append(fn)

    def release(self, key=None, wait: float = 2.0) -> None:
        with self._cond:
            releasers = list(self._releasers)
        for fn in releasers:
            try:
                fn(key, wait)
            except Exception:  # a player that fails to let go must not stop the move; the move reports its error
                pass


RELAY = FrameRelay()


def fit_size(width, height, box_w, box_h, *, grow: bool = False) -> tuple[int, int]:
    """The size of the frames: the video's proportions within the place on the page (and ``MAX_SIDE``); never larger
    than the video itself unless ``grow`` (words drawn on a small video: sharp at the size the page shows it at)."""
    try:
        width, height = max(1, int(width)), max(1, int(height))
        box_w = max(16, min(MAX_SIDE, int(box_w or 0) or 960))
        box_h = max(16, min(MAX_SIDE, int(box_h or 0) or 540))
    except (TypeError, ValueError):
        return 960, 540
    scale = min(box_w / width, box_h / height) if grow else min(box_w / width, box_h / height, 1.0)
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)
