"""Thumbnail service — QImage in worker thread, QPixmap created in UI thread.

This is the correct Qt pattern: QPixmap is NOT thread-safe and must only be
created/used in the main (GUI) thread. Workers produce QImage (thread-safe),
signal carries QImage bytes, UI thread converts to QPixmap.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, QMutex, QMutexLocker
from PySide6.QtGui import QPixmap, QImage


# ── LRU QPixmap cache — UI thread only ───────────────────────────────────────

class _PixmapCache:
    def __init__(self, maxsize: int = 400):
        self._d: OrderedDict[str, QPixmap] = OrderedDict()
        self._max = maxsize
        self._lock = QMutex()

    def get(self, key: str) -> QPixmap | None:
        with QMutexLocker(self._lock):
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def put(self, key: str, pix: QPixmap) -> None:
        with QMutexLocker(self._lock):
            self._d[key] = pix
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with QMutexLocker(self._lock):
            self._d.clear()

    def set_maxsize(self, maxsize: int) -> None:
        with QMutexLocker(self._lock):
            self._max = max(50, min(int(maxsize or 400), 2000))
            while len(self._d) > self._max:
                self._d.popitem(last=False)

    def size(self) -> int:
        with QMutexLocker(self._lock):
            return len(self._d)


_PIX_CACHE = _PixmapCache(400)


# ── Worker signals ────────────────────────────────────────────────────────────

class _Signals(QObject):
    # Emit disk_path (str) so QPixmap is created in UI thread, not worker
    done = Signal(str, int, int, str)   # src_path, w, h, disk_cache_path


# ── Worker — only PIL/disk work, no QPixmap ───────────────────────────────────

class _ThumbWorker(QRunnable):
    def __init__(self, path: str, w: int, h: int, signals: _Signals):
        super().__init__()
        self.setAutoDelete(True)
        self.path = path
        self.w = w
        self.h = h
        self.signals = signals

    def run(self) -> None:
        try:
            from core.image_safe import safe_thumbnail_path
            disk = safe_thumbnail_path(self.path, self.w, self.h) or ""
        except Exception:
            disk = ""
        # Emit disk path — QPixmap will be created in UI thread by the service
        self.signals.done.emit(self.path, self.w, self.h, disk)


# ── Service (singleton) ───────────────────────────────────────────────────────

class ThumbnailService(QObject):
    """Background thumbnail generator.

    Workers produce thumbnails on disk (PIL, thread-safe).
    QPixmap is created HERE in the UI thread inside _on_done().
    """
    _instance: "ThumbnailService | None" = None

    thumbnail_ready = Signal(str, int, int, QPixmap)

    def __init__(self, max_threads: int = 3, parent: QObject | None = None):
        super().__init__(parent)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max(1, min(max_threads, 6)))
        self._pending: set[tuple] = set()
        self._callbacks: dict[tuple, list[Callable]] = {}
        self._lock = QMutex()
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)
        self._reported_failures: set[str] = set()

    @classmethod
    def instance(cls, max_threads: int = 3) -> "ThumbnailService":
        if cls._instance is None:
            cls._instance = cls(max_threads=max_threads)
        return cls._instance

    def request(
        self,
        path: str,
        width: int,
        height: int,
        callback: Callable[[str, QPixmap], None] | None = None,
    ) -> QPixmap | None:
        """Return cached QPixmap immediately or enqueue background generation."""
        cache_key = f"{path}|{width}x{height}"
        cached = _PIX_CACHE.get(cache_key)
        if cached is not None:
            return cached

        key = (path, width, height)
        with QMutexLocker(self._lock):
            if callback:
                self._callbacks.setdefault(key, []).append(callback)
            if key in self._pending:
                return None
            self._pending.add(key)

        self._pool.start(_ThumbWorker(path, width, height, self._signals))
        return None

    def _on_done(self, path: str, width: int, height: int, disk_path: str) -> None:
        """Called in UI thread — safe to create QPixmap here."""
        key = (path, width, height)
        cache_key = f"{path}|{width}x{height}"

        # Create QPixmap in UI thread ✓
        if disk_path:
            pix = QPixmap(disk_path)
        else:
            pix = QPixmap()

        if not pix.isNull():
            _PIX_CACHE.put(cache_key, pix)
        elif path and path not in self._reported_failures:
            self._reported_failures.add(path)
            try:
                import logging
                logging.getLogger("local_booru").warning("THUMBNAIL FAILED: %s", path)
            except Exception:
                pass

        with QMutexLocker(self._lock):
            self._pending.discard(key)
            cbs = self._callbacks.pop(key, [])

        self.thumbnail_ready.emit(path, width, height, pix)
        for cb in cbs:
            try:
                cb(path, pix)
            except Exception:
                pass

    def invalidate(self, path: str) -> None:
        _PIX_CACHE.clear()  # simple approach

    def stop(self) -> None:
        self._pool.waitForDone(3000)

    def clear_memory_cache(self) -> None:
        _PIX_CACHE.clear()

    def configure(self, *, max_threads: int | None = None, memory_items: int | None = None) -> None:
        """Apply lightweight thumbnail settings without rebuilding the service."""
        if max_threads is not None:
            self._pool.setMaxThreadCount(max(1, min(int(max_threads or 3), 6)))
        if memory_items is not None:
            _PIX_CACHE.set_maxsize(int(memory_items or 400))

    def memory_cache_items(self) -> int:
        return _PIX_CACHE.size()
