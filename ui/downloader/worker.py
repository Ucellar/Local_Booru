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


DEFAULT_BLOCKLIST = (
    "obese, obesity, overweight, weight_gain, "
    "inflation, inflation_fetish, expansion, expansion_fetish, "
    "pregnant, pregnancy, mpreg, bloated, belly_inflation, "
    "nipple_expansion, huge_nipples, giant_nipples, "
    "cyst, cysts, cystitis, "
    "ai_generated, ai-assisted, ai_assisted, "
    "scat, coprophagia, poop, feces, "
    "necrophilia, corpse, guro, gore, vomit, fart, farting"
)



from ui.downloader.helpers import *

class DownloaderWorker(QThread):
    log = Signal(str)
    done = Signal()

    def __init__(self, owner, mode, payload):
        super().__init__(owner)
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
            if self._pause_event.wait(0.15):
                return

    def run(self):
        try:
            if self.mode == "tag":
                self.owner._download_tag_query_impl(
                    self.payload["site"],
                    self.payload["tags"],
                    self.payload["limit_total"],
                )
            elif self.mode == "post":
                self.owner._download_post_impl(self.payload["post_url"])
            elif self.mode == "cleanup":
                self.owner._cleanup_by_blocklist_impl()
            elif self.mode == "dedupe":
                self.owner._scan_and_clean_duplicates_impl()
        except Exception as e:
            self.log.emit(f"WORKER ERROR: {type(e).__name__}: {e}")
        finally:
            self.done.emit()
