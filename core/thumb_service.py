"""Priority/cancellation-aware thumbnail service.

Workers create disk thumbnails off the GUI thread. QPixmap creation and all
callbacks remain on the GUI thread. Waiting work is owned by a pure-Python
priority queue, so stale gallery generations can be removed before Qt receives
a QRunnable.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Hashable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, QMutex, QMutexLocker
from PySide6.QtGui import QPixmap

from core.thumb_queue import (
    ThumbnailWorkQueue,
    PRIORITY_VISIBLE,
    PRIORITY_CURRENT_PAGE,
    PRIORITY_PREFETCH,
    PRIORITY_BACKGROUND,
)


class _PixmapCache:
    def __init__(self, maxsize: int = 400):
        self._d: OrderedDict[str, QPixmap] = OrderedDict()
        self._max = maxsize
        self._lock = QMutex()

    def get(self, key: str) -> QPixmap | None:
        with QMutexLocker(self._lock):
            value = self._d.get(key)
            if value is not None:
                self._d.move_to_end(key)
            return value

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


class _Signals(QObject):
    done = Signal(str, int, int, str)  # src_path, w, h, disk_cache_path


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
        self.signals.done.emit(self.path, self.w, self.h, disk)


class ThumbnailService(QObject):
    """Background thumbnail generator with owner/generation cancellation."""

    _instance: "ThumbnailService | None" = None
    thumbnail_ready = Signal(str, int, int, QPixmap)

    def __init__(self, max_threads: int = 3, parent: QObject | None = None):
        super().__init__(parent)
        self._pool = QThreadPool()
        self._max_threads = max(1, min(int(max_threads or 3), 16))
        self._pool.setMaxThreadCount(self._max_threads)
        self._queue = ThumbnailWorkQueue(max_active=self._max_threads, max_pending=256)
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
        *,
        owner: str | None = None,
        generation: Hashable | None = None,
        priority: int = PRIORITY_BACKGROUND,
    ) -> QPixmap | None:
        path = str(path or "")
        width = max(1, int(width or 1))
        height = max(1, int(height or 1))
        cached = _PIX_CACHE.get(f"{path}|{width}x{height}")
        if cached is not None:
            return cached

        with QMutexLocker(self._lock):
            accepted, starts = self._queue.add(
                (path, width, height), owner=owner, generation=generation,
                priority=priority, callback=callback,
            )
        if accepted:
            self._start_keys(starts)
        return None

    def cancel(
        self,
        path: str,
        width: int,
        height: int,
        *,
        owner: str | None = None,
        generation: Hashable | None = None,
    ) -> int:
        key = (str(path or ""), max(1, int(width or 1)), max(1, int(height or 1)))
        with QMutexLocker(self._lock):
            removed, starts = self._queue.cancel_key(
                key, owner=owner, generation=generation,
            )
        self._start_keys(starts)
        return removed

    def cancel_owner(self, owner: str, generation: Hashable | None = None) -> int:
        with QMutexLocker(self._lock):
            removed, starts = self._queue.cancel_owner(owner, generation)
        self._start_keys(starts)
        return removed

    def _start_keys(self, keys: list[tuple[str, int, int]]) -> None:
        for path, width, height in keys:
            worker = _ThumbWorker(path, width, height, self._signals)
            try:
                self._pool.start(worker)
            except Exception:
                try:
                    self._on_done(path, width, height, "")
                except Exception:
                    pass

    def _on_done(self, path: str, width: int, height: int, disk_path: str) -> None:
        cache_key = f"{path}|{width}x{height}"
        pix = QPixmap(disk_path) if disk_path else QPixmap()
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
            callbacks, should_emit, starts = self._queue.complete((path, width, height))
        # A cancelled running generation may still warm disk/memory cache, but
        # it must never repaint a newer page or invoke stale callbacks.
        if should_emit:
            self.thumbnail_ready.emit(path, width, height, pix)
            for callback in callbacks:
                try:
                    callback(path, pix)
                except Exception:
                    pass
        self._start_keys(starts)

    def invalidate(self, path: str) -> None:
        _PIX_CACHE.clear()

    def stop(self) -> None:
        with QMutexLocker(self._lock):
            self._queue.stop_queued()
        self._pool.waitForDone(3000)

    def clear_memory_cache(self) -> None:
        _PIX_CACHE.clear()

    def pending_count(self) -> int:
        with QMutexLocker(self._lock):
            return self._queue.pending_count()

    def queued_count(self) -> int:
        with QMutexLocker(self._lock):
            return self._queue.queued_count()

    def active_count(self) -> int:
        with QMutexLocker(self._lock):
            return self._queue.active_count()

    def configure(
        self,
        *,
        max_threads: int | None = None,
        memory_items: int | None = None,
        max_pending: int | None = None,
    ) -> None:
        with QMutexLocker(self._lock):
            if max_threads is not None:
                self._max_threads = max(1, min(int(max_threads or 3), 16))
                self._pool.setMaxThreadCount(self._max_threads)
            starts = self._queue.configure(
                max_active=self._max_threads if max_threads is not None else None,
                max_pending=max(16, min(int(max_pending or 256), 2000)) if max_pending is not None else None,
            )
        if memory_items is not None:
            _PIX_CACHE.set_maxsize(int(memory_items or 400))
        self._start_keys(starts)

    def memory_cache_items(self) -> int:
        return _PIX_CACHE.size()
