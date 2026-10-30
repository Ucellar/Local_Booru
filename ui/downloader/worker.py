from pathlib import Path
import json
import re
import mimetypes
import time
import hashlib
from urllib.parse import urlparse, parse_qs, unquote, quote_plus

import requests
from bs4 import BeautifulSoup

from PySide6.QtCore import QThread, Signal
import threading
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
    QLineEdit, QPlainTextEdit, QSpinBox, QMessageBox, QDialog, QGridLayout, QScrollArea
)

from ui.login_browser import open_br34
from ui.memory_tools import bounded_append
from core.paths import BROWSER_COOKIES_DIR, ensure_output_base
from core.settings import save_settings
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt
try:
    from PIL import Image
    import imagehash
except Exception:
    Image = None
    imagehash = None





from ui.downloader.helpers import *

class DownloaderWorker(QThread):
    log = Signal(str)
    done = Signal()
    result = Signal(object)

    def __init__(self, owner, mode, payload):
        super().__init__(owner)
        try:
            from core.shutdown import register_qthread
            register_qthread(self, "DownloaderWorker")
        except Exception:
            pass
        self.owner = owner
        self.mode = mode
        self.payload = payload
        self.stop_requested = False
        self._pause_event = threading.Event()
        self._pause_event.set()

    def request_stop(self):
        self.stop_requested = True
        self._pause_event.set()

    def set_paused(self, paused: bool):
        if paused:
            self._pause_event.clear()
        else:
            self._pause_event.set()

    def wait_if_paused(self):
        while not self.stop_requested:
            try:
                from core.shutdown import is_shutting_down
                if is_shutting_down() or self.isInterruptionRequested():
                    self.stop_requested = True
                    return
            except Exception:
                pass
            if self._pause_event.wait(0.15):
                return

    def run(self):
        registry = None
        task_id = ""
        failed = None
        try:
            from core.background_tasks import get_background_task_registry
            registry = get_background_task_registry()
            task_id = registry.register(
                f"Downloader: {self.mode}",
                kind="downloader",
                state="running",
                detail=str(self.mode),
                progress_text="downloader выполняется",
                cancellable=True,
                cancel_callback=self.request_stop,
            )
            registry.start(task_id, detail=str(self.mode), progress_text="downloader выполняется")
        except Exception:
            registry = None
            task_id = ""

        try:
            bind_context = registry.bind(task_id) if registry is not None and task_id else __import__("contextlib").nullcontext()
            with bind_context:
                if self.mode == "tag":
                    self.owner._download_tag_query_impl(
                        self.payload["site"],
                        self.payload["tags"],
                        self.payload["limit_total"],
                    )
                elif self.mode == "tag_many":
                    self.owner._download_tag_many_impl(
                        self.payload.get("sites") or [],
                        self.payload.get("tags") or "",
                        int(self.payload.get("limit_total") or 0),
                    )
                elif self.mode == "post":
                    self.owner._download_post_impl(self.payload["post_url"])
                elif self.mode == "cleanup":
                    self.owner._cleanup_by_blocklist_impl()
                elif self.mode == "dedupe":
                    self.owner._scan_and_clean_duplicates_impl()
                elif self.mode == "preview":
                    self.result.emit(self.owner._preview_search_impl(
                        self.payload.get("sites") or [],
                        self.payload.get("tags") or "",
                        int(self.payload.get("limit_total") or 0),
                        bool(self.payload.get("hide_existing", True)),
                        bool(self.payload.get("append", False)),
                        self.payload.get("start_pages") or {},
                        progress_emit=self.result.emit,
                        request_token=self.payload.get("request_token"),
                    ) or [])
                elif self.mode == "preview_download":
                    candidate = self.payload.get("candidate") or {}
                    result = self.owner._download_preview_candidate_impl(candidate)
                    self.result.emit({"mode": "preview_download", "candidate": candidate, "result": str(result or "")})
                elif self.mode == "preview_download_many":
                    candidates = list(self.payload.get("candidates") or [])
                    for index, candidate in enumerate(candidates, start=1):
                        if self.stop_requested:
                            break
                        self.wait_if_paused()
                        if registry is not None and task_id:
                            registry.progress(
                                task_id,
                                text="скачивание выбранных файлов",
                                current=index,
                                total=len(candidates),
                                detail=str((candidate or {}).get("post_url") or (candidate or {}).get("file_url") or ""),
                            )
                        self.owner._download_preview_candidate_impl(candidate or {})
        except Exception as e:
            failed = e
            if registry is not None and task_id:
                registry.fail(task_id, e)
            self.log.emit(f"WORKER ERROR: {type(e).__name__}: {e}")
        finally:
            if registry is not None and task_id and failed is None:
                if self.stop_requested or self.isInterruptionRequested():
                    registry.cancel(task_id, detail="downloader stopped")
                else:
                    registry.finish(task_id)
            self.done.emit()
