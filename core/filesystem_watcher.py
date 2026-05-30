from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

_log = logging.getLogger("local_booru.watcher")

class LibraryWatcher:
    """Debounced incremental indexing watcher.

    Uses watchdog when installed.  Falls back to a cheap polling loop that only
    looks for new/modified media paths and then runs index_library(force=False).
    """
    def __init__(self, settings: dict, log: Callable[[str], None] | None = None):
        self.settings = dict(settings or {})
        self.log = log or (lambda m: _log.info(m))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._observer = None
        self._timer: threading.Timer | None = None
        self._indexing = threading.Lock()

    def start(self) -> bool:
        if not bool(self.settings.get("watch_filesystem", False)):
            return False
        try:
            from core.media_utils import scan_roots
            roots = [Path(r) for r in scan_roots(self.settings) if Path(r).exists()]
        except Exception:
            roots = []
        if not roots:
            return False
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
            watcher = self
            class Handler(FileSystemEventHandler):
                def on_created(self, event): watcher._schedule()
                def on_modified(self, event): watcher._schedule()
                def on_moved(self, event): watcher._schedule()
                def on_deleted(self, event): watcher._schedule()
            obs = Observer()
            handler = Handler()
            for r in roots:
                obs.schedule(handler, str(r), recursive=True)
            obs.start()
            self._observer = obs
            self.log(f"WATCHER: watchdog enabled for {len(roots)} root(s)")
            return True
        except Exception as e:
            self.log(f"WATCHER: watchdog unavailable, polling fallback ({e})")
            self._thread = threading.Thread(target=self._poll_loop, name="library-watcher", daemon=True)
            self._thread.start()
            return True

    def _schedule(self, delay: float = 2.5) -> None:
        if self._stop.is_set():
            return
        try:
            if self._timer:
                self._timer.cancel()
        except Exception:
            pass
        self._timer = threading.Timer(delay, self._run_incremental_index)
        self._timer.daemon = True
        self._timer.start()

    def _run_incremental_index(self) -> None:
        if self._stop.is_set() or not self._indexing.acquire(blocking=False):
            return
        try:
            from core.database.indexer import index_library
            self.log("WATCHER: incremental index start")
            index_library(self.settings, force=False, stop_check=self._stop.is_set)
            self.log("WATCHER: incremental index done")
        except Exception as e:
            self.log(f"WATCHER ERROR: {e}")
        finally:
            self._indexing.release()

    def _poll_loop(self) -> None:
        known: dict[str, tuple[int, int]] = {}
        while not self._stop.wait(float(self.settings.get("watch_poll_seconds", 15) or 15)):
            changed = False
            try:
                from core.media_utils import scan_roots, iter_media_files, safe_stat
                for p in iter_media_files(scan_roots(self.settings), stop_check=self._stop.is_set):
                    st = safe_stat(p)
                    key = str(p)
                    if known.get(key) != st:
                        known[key] = st
                        changed = True
            except Exception as e:
                self.log(f"WATCHER POLL ERROR: {e}")
            if changed:
                self._schedule(1.0)

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._timer:
                self._timer.cancel()
        except Exception:
            pass
        try:
            if self._observer:
                self._observer.stop(); self._observer.join(3000)
        except Exception:
            pass
        try:
            if self._thread and self._thread.is_alive():
                self._thread.join(3)
        except Exception:
            pass
