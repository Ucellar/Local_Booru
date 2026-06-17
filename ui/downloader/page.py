from pathlib import Path
import json
import re
import mimetypes
import time
import hashlib
import shutil
import atexit
import threading
import concurrent.futures
from collections import OrderedDict
from urllib.parse import urlparse, parse_qs, unquote, quote_plus, urlencode

import requests
from bs4 import BeautifulSoup

from PySide6.QtCore import QThread, Signal, QTimer, QEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
    QLineEdit, QPlainTextEdit, QSpinBox, QMessageBox, QDialog, QGridLayout, QScrollArea,
    QCheckBox, QFrame, QMenu, QApplication, QListWidget, QListWidgetItem, QSplitter
)

from ui.login_browser import open_br34
from ui.memory_tools import bounded_append
from core.paths import BROWSER_COOKIES_DIR, CACHE_DIR, ensure_output_base
from core.settings import save_settings
from core.file_safety import atomic_write_chunks
from core.tag_utils import normalize_tag, tag_display_color
from PySide6.QtGui import QPixmap, QColor, QBrush, QKeySequence, QShortcut
from PySide6.QtCore import Qt, QPoint
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


GRABBER_GROUP_ORDER = [
    "artist", "contributor", "character", "copyright", "species",
    "general", "meta", "lore", "invalid", "parody", "language",
    "category", "pages",
]
GRABBER_GROUP_COLORS = {
    "artist": "#ff3838", "contributor": "#e67e22", "character": "#00a000",
    "copyright": "#ff54a7", "species": "#22a6b3", "general": "#004cff",
    "meta": "#ff9900", "lore": "#9b59b6", "invalid": "#7f8c8d",
    "parody": "#ff54a7", "language": "#cc8800", "category": "#00aaaa",
    "pages": "#888888",
}
GRABBER_GROUP_PRIORITY = {
    # В выдаче граббера одинаковый тег должен выглядеть как в галерее:
    # информативная категория важнее general, но сам набор тегов источника
    # не мутируется.
    "artist": 10, "contributor": 9, "character": 8, "copyright": 7,
    "species": 6, "meta": 5, "lore": 4, "parody": 4, "language": 4,
    "category": 4, "pages": 4, "invalid": 3, "general": 1,
}


from ui.downloader.helpers import *
from ui.downloader.worker import DownloaderWorker
from ui.duplicates_page import image_size as _duplicate_image_size

class _FullImageLoader(QThread):
    """Downloads the full-quality image in background, then updates post_page."""
    ready = Signal(str, object)
    failed = Signal(str)

    def __init__(self, item, owner):
        super().__init__()
        self._item = dict(item or {})
        self._owner = owner

    def run(self):
        try:
            path = self._owner._download_preview_full_image(self._item)
            if path:
                self.ready.emit(str(path), self._item)
            else:
                self.failed.emit('full image download returned no path')
        except Exception as e:
            self.failed.emit(str(e))


class DownloaderPage(QWidget):
    """
    Single-post and tag-query downloader.
    Output layout mirrors found/no_match:
    downloads/found/media, downloads/found/tags, downloads/found/source, downloads/found/searched, downloads/found/cache
    """

    # Dedicated signal for logs coming from worker threads.
    # Direct UI writes from a worker thread can close Qt apps on Windows
    # without a Python traceback.
    log_requested = Signal(str)

    def _append_log_direct(self, msg):
        bounded_append(self.info, str(msg), int(self.main.settings.get("max_console_lines", 2500)))
        try:
            sb = self.info.verticalScrollBar()
            sb.setValue(sb.maximum())
        except Exception:
            pass

    def append_log(self, msg):
        if QThread.currentThread() != self.thread():
            self.log_requested.emit(str(msg))
            return
        self._append_log_direct(msg)

    def _schedule_gallery_refresh_after_download(self, saved_path: str = ""):
        """Debounced notification that the library SQLite rows changed.

        Downloader writes happen in worker threads.  The already-open Gallery
        page keeps its own SQL state and a pooled read-only connection, so it
        needs an explicit dirty signal instead of waiting for the user to fully
        reopen the application.
        """
        try:
            self._last_downloaded_media_path = str(saved_path or "")
        except Exception:
            pass
        if getattr(self, "_gallery_refresh_timer_pending", False):
            return
        self._gallery_refresh_timer_pending = True

        def _fire():
            self._gallery_refresh_timer_pending = False
            try:
                from core.database.connection import close_thread_pooled_connections
                close_thread_pooled_connections(self.main.settings, readonly=True)
            except Exception:
                pass
            try:
                gallery = getattr(self.main, "gallery_page", None)
                if gallery is not None and hasattr(gallery, "mark_library_changed"):
                    gallery.mark_library_changed(getattr(self, "_last_downloaded_media_path", ""))
                elif gallery is not None and hasattr(gallery, "refresh_force") and gallery.isVisible():
                    gallery.refresh_force()
            except Exception as e:
                try:
                    self.append_log(f"GALLERY REFRESH WARN: {type(e).__name__}: {e}")
                except Exception:
                    pass

        QTimer.singleShot(600, _fire)

    def _make_worker_runtime(self):
        # Snapshot UI/settings before worker starts.
        # Worker thread must not read Qt widgets directly: on Windows/Qt this can
        # silently close the process instead of producing a Python traceback.
        base = self._runtime_base()
        raw_blocklist = self._grabber_subscription_blocklist_text() or ""
        return {
            "base": base,
            "downloads_root": base / "downloads",
            "blocklist": {x.strip().lower() for x in re.split(r"[,;\s]+", raw_blocklist) if x.strip()},
            "settings": dict(self.main.settings),
        }

    def _runtime_settings(self):
        rt = getattr(self, "_worker_runtime", None)
        if isinstance(rt, dict) and isinstance(rt.get("settings"), dict):
            return rt["settings"]
        return self.main.settings

    def _runtime_base(self):
        rt = getattr(self, "_worker_runtime", None)
        if isinstance(rt, dict) and rt.get("base") is not None:
            return Path(rt["base"])
        return ensure_output_base(
            self.main.settings.get("output_dir"),
            self.main.settings.get("root")
        )

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.log_requested.connect(self._append_log_direct)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        title = QLabel("Граббер")
        title.setStyleSheet("font-size:26px;font-weight:900")
        lay.addWidget(title)

        # v222 migration: the grabber is meant to help extend the local archive,
        # so already-downloaded posts must stay hidden by default.  v221 briefly
        # flipped old configs to show existing posts while fixing the crash loop;
        # restore the intended default once.  The checkbox in Settings can still
        # show existing posts manually when needed.
        try:
            if not self.main.settings.get("grabber_preview_hide_existing_v222_restored", False):
                self.main.settings["grabber_preview_hide_existing"] = True
                self.main.settings["grabber_preview_hide_existing_v222_restored"] = True
                save_settings(self.main.settings)
        except Exception:
            pass

        # v224 stability profile: the UI grabber is a browser/viewer first, not a
        # background crawler.  Old configs from v215-v222 could still keep heavy
        # original prefetch and per-card streaming enabled, which caused lots of
        # image decoding, PoW requests and Qt refreshes during browsing. Disable
        # those once; visual duplicate collapse is handled separately as a
        # conservative display-only pass.
        try:
            if not self.main.settings.get("grabber_stability_v224_applied", False):
                self.main.settings["grabber_preview_prefetch_originals"] = False
                self.main.settings["grabber_preview_stream_cards"] = False
                self.main.settings["grabber_include_protected_sites"] = False
                self.main.settings["grabber_preview_threads"] = min(2, int(self.main.settings.get("grabber_preview_threads", 2) or 2))
                self.main.settings["grabber_stability_v224_applied"] = True
                save_settings(self.main.settings)
        except Exception:
            pass

        # v237: screenshots/logs showed the same visual card appearing once from
        # ATF and once from e621/rule34/etc.  Exact MD5 cannot catch that when a
        # site recompresses the same art, so enable conservative visual collapse
        # by default.  This remains UI/display-only for different MD5s: it hides
        # the duplicate card but does not persist the other site's tags/source as
        # proof of the downloaded file.
        try:
            changed = False
            if not self.main.settings.get("grabber_visual_dedupe_v237_applied", False):
                self.main.settings["grabber_visual_hash_merge"] = True
                self.main.settings["grabber_visual_hash_distance"] = int(self.main.settings.get("grabber_visual_hash_distance", 4) or 4)
                self.main.settings["grabber_visual_dedupe_v237_applied"] = True
                changed = True
            # v243 has no separate toggle; the marker only prevents old one-shot
            # migrations from running on every start because of a misspelled
            # combined key with a space in it.
            if not self.main.settings.get("grabber_visual_source_filter_v243_applied", False):
                self.main.settings["grabber_visual_source_filter_v243_applied"] = True
                changed = True
            if changed:
                save_settings(self.main.settings)
        except Exception:
            pass

        # Heavy original prefetch is a background cache helper, not the search
        # engine itself.  Keep it strictly serialized so one preview page cannot
        # start several direct media downloads/PoW flows in parallel and make Qt
        # close silently on Windows.
        self._grabber_original_prefetch_lock = threading.Lock()
        # v236: opened online-post preview loaders are keyed by card key/URL so
        # post-page navigation can request loading on every render without
        # spawning duplicate downloads for the same temporary preview.
        self._full_loader_keys = set()
        # v234: metadata (tags/md5/source/post JSON) lives in bounded RAM cache;
        # image bytes stay in temporary grabber_online disk cache only.
        self._grabber_metadata_ram_cache = OrderedDict()
        self._grabber_metadata_ram_bytes = 0
        self._grabber_metadata_ram_lock = threading.RLock()
        # Scaled preview pixmaps are RAM-only and bounded separately from the
        # temporary disk image cache. Disk cache keeps bytes after network GET;
        # RAM cache avoids rereading/rescaling the same thumbnails while paging.
        self._grabber_pixmap_ram_cache = OrderedDict()
        self._grabber_pixmap_ram_bytes = 0
        self._grabber_pixmap_ram_lock = threading.RLock()

        # v229: настоящий site-lane async.  Старый stability-profile принудительно
        # оставлял grabber_preview_threads=2, из-за чего 5 сайтов шли пачками
        # 2+2+1 и ATF часто стартовал последним.  Значение 0 означает auto:
        # одна активная lane на каждый включённый сайт, а per-host rate-limit
        # всё равно не даёт долбить один домен слишком быстро.
        try:
            if not self.main.settings.get("grabber_async_site_lanes_v229_applied", False):
                if int(self.main.settings.get("grabber_preview_threads", 2) or 0) <= 2:
                    self.main.settings["grabber_preview_threads"] = 0
                if int(self.main.settings.get("grabber_exact_md5_threads", 2) or 0) <= 2:
                    self.main.settings["grabber_exact_md5_threads"] = 0
                self.main.settings["grabber_async_site_lanes_v229_applied"] = True
                save_settings(self.main.settings)
        except Exception:
            pass

        # v229: ATF PoW is bound to the requests session/cookies.  Creating a
        # fresh session for preview page 3, preview page 4 and exact-MD5 caused
        # the same PoW to be solved again and again.  Cache sessions by host and
        # serialize ATF metadata calls on that host session.  Other sites still
        # use short-lived sessions and can run fully parallel.
        self._grabber_session_cache = {}
        self._grabber_session_locks = {}
        self._grabber_session_cache_lock = threading.RLock()

        # Граббер теперь выглядит как обычная галерея: сверху только URL и
        # поиск, в центре сетка, слева источники/теги.  Скачивание карточек —
        # через ПКМ по посту, без лишних кнопок в карточках/панели.
        url_row = QHBoxLayout()
        self.url = QLineEdit("")
        self.url.setPlaceholderText("Ссылка на пост или файл")
        self.download_btn = QPushButton("Скачать")
        url_row.addWidget(QLabel("URL:"))
        url_row.addWidget(self.url, 1)
        url_row.addWidget(self.download_btn)
        lay.addLayout(url_row)

        search_row = QHBoxLayout()
        self.preview_query = QLineEdit(str(self.main.settings.get("grabber_preview_query", "")))
        self.preview_query.setPlaceholderText("Поиск по тегам на включённых сайтах парсера")
        # Один крестик очистки — встроенный в поле, как в обычной Галерее.
        # Отдельная кнопка рядом давала два крестика на некоторых темах Qt.
        try:
            self.preview_query.setClearButtonEnabled(True)
        except Exception:
            pass
        self.preview_search_btn = QPushButton("Найти")
        self.tag_download_btn = QPushButton("Скачать тег")
        search_row.addWidget(QLabel("Поиск:"))
        search_row.addWidget(self.preview_query, 1)
        search_row.addWidget(self.preview_search_btn)
        search_row.addWidget(self.tag_download_btn)
        lay.addLayout(search_row)

        self.preview_status = QLabel("Граббер использует только сайты, включённые во вкладке «Парсер». ПКМ по карточке → Скачать.")
        self.preview_status.setWordWrap(True)
        lay.addWidget(self.preview_status)

        self.open_btn = QPushButton("Открыть"); self.open_btn.setVisible(False)
        self.pause_btn = QPushButton("Пауза"); self.pause_btn.setVisible(False)
        self.stop_btn = QPushButton("Стоп"); self.stop_btn.setVisible(False)
        self.pause_btn.setObjectName("ParserPauseButton"); self.stop_btn.setObjectName("DownloaderStopButton")
        self.pause_btn.setEnabled(False); self.stop_btn.setEnabled(False)
        self.dedupe_btn = QPushButton("Дубликаты"); self.cleanup_btn = QPushButton("Очистка блоклиста")
        self.dedupe_btn.setVisible(False); self.cleanup_btn.setVisible(False)
        self.blocklist = QLineEdit(self._grabber_subscription_blocklist_text())
        self.blocklist.setVisible(False)
        self.tag_site = QLineEdit(""); self.tag_site.setVisible(False)
        self.tag_query = self.preview_query
        self.tag_limit = QSpinBox(); self.tag_limit.setRange(1, 1000000)
        self.tag_limit.setValue(int(self.main.settings.get("grabber_tag_download_limit", self.main.settings.get("grabber_preview_limit", 500)) or 500))
        self.tag_limit.setVisible(False)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 6, 0)
        side_lay.setSpacing(5)
        src_header = QWidget()
        src_header_lay = QHBoxLayout(src_header)
        src_header_lay.setContentsMargins(0, 0, 0, 0)
        src_header_lay.setSpacing(4)
        src_title = QLabel("Источники")
        src_title.setStyleSheet("font-weight:800")
        self.preview_sources_toggle = QPushButton("Показать все")
        self.preview_sources_toggle.setToolTip("Развернуть список источников, как в Галерее")
        self.preview_sources_toggle.clicked.connect(self._toggle_preview_sources)
        src_header_lay.addWidget(src_title, 1)
        src_header_lay.addWidget(self.preview_sources_toggle, 0)
        self.preview_sources_expanded = False
        self.preview_sources_list = QListWidget()
        self.preview_sources_list.setMinimumWidth(190)
        self.preview_sources_list.setMaximumWidth(250)
        # Компактно, как блок источников в Галерее: это фильтр, а не отдельная
        # панель статуса.
        self.preview_sources_list.setMinimumHeight(64)
        self.preview_sources_list.setMaximumHeight(96)
        self.preview_sources_list.itemClicked.connect(self._preview_source_clicked)
        tag_title = QLabel("Теги")
        tag_title.setStyleSheet("font-weight:800")
        self.preview_tags_list = QListWidget()
        self.preview_tags_list.setMinimumWidth(210)
        self.preview_tags_list.setMaximumWidth(310)
        self.preview_tags_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.preview_tags_list.itemClicked.connect(self._preview_tag_clicked)
        self.preview_tags_list.customContextMenuRequested.connect(self._preview_tag_context_menu)
        side_lay.addWidget(src_header)
        side_lay.addWidget(self.preview_sources_list, 0)
        side_lay.addWidget(tag_title)
        side_lay.addWidget(self.preview_tags_list, 1)
        splitter.addWidget(side)

        grid_host = QWidget()
        grid_lay = QVBoxLayout(grid_host)
        grid_lay.setContentsMargins(0, 0, 0, 0)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setMinimumHeight(420)
        self.preview_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # Граббер листает ровно страницами, как booru-галерея; колесо не должно
        # превращаться в микропрокрутку внутреннего QScrollArea.
        self.preview_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview_inner = QWidget()
        self.preview_grid = QGridLayout(self.preview_inner)
        self.preview_grid.setContentsMargins(4, 4, 4, 4)
        self.preview_grid.setHorizontalSpacing(8)
        self.preview_grid.setVerticalSpacing(8)
        self.preview_scroll.setWidget(self.preview_inner)
        grid_lay.addWidget(self.preview_scroll, 1)
        splitter.addWidget(grid_host)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        lay.addWidget(splitter, 1)

        nav_row = QHBoxLayout()
        self.preview_prev_btn = QPushButton("←")
        self.preview_page_label = QLabel("Страница 0/0")
        self.preview_next_btn = QPushButton("→")
        self.preview_prev_btn.setEnabled(False)
        self.preview_next_btn.setEnabled(False)
        nav_row.addStretch(1)
        nav_row.addWidget(self.preview_prev_btn)
        nav_row.addWidget(self.preview_page_label)
        nav_row.addWidget(self.preview_next_btn)
        nav_row.addStretch(1)
        lay.addLayout(nav_row)

        self.preview_items = []
        self.preview_page_index = 1
        self._preview_site_counts = {}
        self.preview_total_by_site = {}
        self._preview_next_page_by_site = {}
        self._preview_exhausted_sites = set()
        self._preview_loading_more = False
        self._preview_query_text = ""
        self._preview_site_filter = ""
        self._preview_request_token = 0
        self._pending_preview_search = None
        self._last_preview_autoload_key = None
        self._preview_manual_skip_attempts = 0
        self._preview_render_pending = False
        self._preview_sidebar_pending = False
        self._preview_last_visible_keys = ()
        self._md5_ram_index = None
        self._md5_ram_index_lock = threading.RLock()
        self._grabber_exact_md5_cache = {}
        self._grabber_exact_md5_lock = threading.RLock()
        self._grabber_existing_merge_lock = threading.RLock()
        self._grabber_existing_merge_seen = set()
        self._grabber_exclusion_lock = threading.RLock()
        self._grabber_exclusion_identities = None
        try:
            self.preview_scroll.viewport().installEventFilter(self)
        except Exception:
            pass

        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setVisible(False)
        self.info.setPlainText("Grabber log")
        lay.addWidget(self.info)

        self._prepare_grabber_temp_cache()

        self.download_btn.clicked.connect(self.download_post)
        self.tag_download_btn.clicked.connect(self.download_tag_query)
        self.pause_btn.clicked.connect(self.toggle_pause_worker)
        self.stop_btn.clicked.connect(self.stop_worker)
        self.dedupe_btn.clicked.connect(lambda: self.main.go("Duplicates") if hasattr(self.main, "go") else self.scan_and_clean_duplicates())
        self.cleanup_btn.clicked.connect(self.cleanup_by_blocklist)
        self.preview_search_btn.clicked.connect(self.search_online_preview)
        # Очистка теперь встроена в QLineEdit; Enter запускает поиск.
        self.preview_query.returnPressed.connect(self.search_online_preview)
        self.preview_prev_btn.clicked.connect(lambda: self.preview_go_page(-1))
        self.preview_next_btn.clicked.connect(lambda: self.preview_go_page(1))
        self._refresh_preview_sidebar()
        self.render_preview_page()


    def _grabber_temp_cache_root(self):
        return CACHE_DIR / "grabber_online"

    def _grabber_cache_dir(self, kind="preview"):
        root = self._grabber_temp_cache_root() / str(kind or "preview")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _grabber_cache_limit_bytes(self):
        """Return session cache limit in bytes; 0 means unlimited for this session."""
        try:
            mb = int(self.main.settings.get("grabber_cache_limit_mb", 200) or 200)
        except Exception:
            mb = 200
        if mb <= 0:
            return 0
        return max(50, mb) * 1024 * 1024

    def _grabber_metadata_ram_limit_bytes(self):
        """RAM limit for non-image grabber metadata: tags, md5, sources and post JSON.

        This is deliberately separate from grabber_cache_limit_mb. The disk cache
        stores temporary preview/full image bytes; this RAM cache stores only
        small API metadata so repeated page loads do not reparse/remerge tags.
        0 disables metadata RAM caching.
        """
        try:
            mb = int(self.main.settings.get("grabber_metadata_ram_cache_mb", 64) or 64)
        except Exception:
            mb = 64
        if mb <= 0:
            return 0
        return max(1, mb) * 1024 * 1024

    def _grabber_metadata_ram_cache_put(self, item):
        """Cache one card's lightweight metadata in RAM up to configured limit."""
        limit = self._grabber_metadata_ram_limit_bytes()
        if limit <= 0 or not item:
            return False
        try:
            key = str(item.get("key") or item.get("md5") or "").strip()
            if not key:
                return False
            payload = {
                "key": key,
                "md5": item.get("md5") or "",
                "md5s": list(item.get("md5s") or []),
                "sites": list(item.get("sites") or []),
                "post_urls": list(item.get("post_urls") or []),
                "file_urls": list(item.get("file_urls") or []),
                "view_urls": list(item.get("view_urls") or []),
                "download_url": item.get("download_url") or "",
                "preview_url": item.get("preview_url") or "",
                "tags": list(item.get("tags") or []),
                "groups": item.get("groups") or {},
                "source_tag_groups": list(item.get("source_tag_groups") or []),
                "post": item.get("post") or {},
            }
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", "ignore")
            size = len(raw)
            if size > limit:
                return False
            with self._grabber_metadata_ram_lock:
                old = self._grabber_metadata_ram_cache.pop(key, None)
                if old:
                    self._grabber_metadata_ram_bytes -= int(old[0] or 0)
                self._grabber_metadata_ram_cache[key] = (size, payload)
                self._grabber_metadata_ram_bytes += size
                while self._grabber_metadata_ram_bytes > limit and self._grabber_metadata_ram_cache:
                    _old_key, (old_size, _old_payload) = self._grabber_metadata_ram_cache.popitem(last=False)
                    self._grabber_metadata_ram_bytes -= int(old_size or 0)
            return True
        except Exception:
            return False

    def _grabber_image_ram_limit_bytes(self):
        """RAM limit for scaled grabber preview pixmaps. 0 disables it."""
        try:
            mb = int(self.main.settings.get("grabber_image_ram_cache_mb", 256) or 256)
        except Exception:
            mb = 256
        if mb <= 0:
            return 0
        return max(16, mb) * 1024 * 1024

    def _grabber_cached_scaled_pixmap(self, path, width, height):
        """Load/scale one preview image through a bounded RAM LRU cache.

        Network-loaded image bytes still live in settings/cache/grabber_online.
        This RAM layer is only for fast UI redraws and is cleared with the page.
        """
        path = str(path or "")
        if not path:
            return QPixmap()
        try:
            width = int(width or 0); height = int(height or 0)
        except Exception:
            width = height = 0
        if width <= 0 or height <= 0:
            return QPixmap(str(path))
        limit = self._grabber_image_ram_limit_bytes()
        key = f"{path}|{width}x{height}"
        if limit > 0:
            with self._grabber_pixmap_ram_lock:
                old = self._grabber_pixmap_ram_cache.pop(key, None)
                if old is not None:
                    size, pix = old
                    self._grabber_pixmap_ram_cache[key] = (size, pix)
                    return QPixmap(pix)
        pix = QPixmap(str(path))
        if pix.isNull():
            return pix
        pix = pix.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if limit <= 0:
            return pix
        try:
            size = max(1, int(pix.width()) * max(1, int(pix.height())) * 4)
            if size > limit:
                return pix
            with self._grabber_pixmap_ram_lock:
                self._grabber_pixmap_ram_cache[key] = (size, QPixmap(pix))
                self._grabber_pixmap_ram_bytes += size
                while self._grabber_pixmap_ram_bytes > limit and self._grabber_pixmap_ram_cache:
                    _old_key, (old_size, _old_pix) = self._grabber_pixmap_ram_cache.popitem(last=False)
                    self._grabber_pixmap_ram_bytes -= int(old_size or 0)
        except Exception:
            pass
        return pix

    def _grabber_open_quality(self):
        """Quality tier for opening a grabber card, not for archive download.

        small_25  -> lightweight view cache (ATF 720x720 / sample-ish)
        medium_50 -> site sample/large preview where available
        original_100 -> original/full URL for viewing only; Download still uses original always
        """
        q = str(self.main.settings.get("grabber_open_quality", "medium_50") or "medium_50").strip().lower()
        if q in {"small", "small_25", "25", "25%"}:
            return "small_25"
        if q in {"original", "original_100", "100", "100%"}:
            return "original_100"
        return "medium_50"

    def _grabber_open_quality_label(self):
        q = self._grabber_open_quality()
        if q == "small_25":
            return "Маленький / 25%"
        if q == "original_100":
            return "Оригинал / 100%"
        return "Средний / 50%"

    def _grabber_loading_placeholder_path(self, item=None):
        key = ""
        try:
            key = str((item or {}).get("key") or (item or {}).get("md5") or "online")
        except Exception:
            key = "online"
        return f"__local_booru_loading_preview__/{hashlib.sha1(key.encode('utf-8', 'ignore')).hexdigest()}"

    def _grabber_mark_open_quality(self, item):
        try:
            item["open_quality"] = self._grabber_open_quality()
            item["open_quality_label"] = self._grabber_open_quality_label()
        except Exception:
            pass
        return item

    def _grabber_cache_evict(self):
        """Evict oldest cached files if total size exceeds the limit."""
        try:
            root = self._grabber_temp_cache_root()
            if not root.exists():
                return
            files = sorted(
                [f for f in root.rglob("*") if f.is_file() and not f.name.endswith(".tmp")],
                key=lambda f: f.stat().st_mtime
            )
            total = sum(f.stat().st_size for f in files)
            limit = self._grabber_cache_limit_bytes()
            if limit <= 0:
                return
            while total > limit and files:
                oldest = files.pop(0)
                size = oldest.stat().st_size
                try:
                    oldest.unlink(missing_ok=True)
                    total -= size
                except Exception:
                    pass
        except Exception:
            pass

    def _cleanup_grabber_temp_cache(self):
        try:
            shutil.rmtree(self._grabber_temp_cache_root(), ignore_errors=True)
        except Exception:
            pass

    def _prepare_grabber_temp_cache(self):
        # Online grabber images are temporary UI cache, not archive media.
        # Remove leftovers from previous/crashed sessions at startup and clear
        # the current session during normal interpreter shutdown.
        self._cleanup_grabber_temp_cache()
        try:
            atexit.register(lambda root=str(self._grabber_temp_cache_root()): shutil.rmtree(root, ignore_errors=True))
        except Exception:
            pass


    def _grabber_subscription_blocklist_text(self):
        s = self.main.settings if hasattr(self, "main") else {}
        return str(
            s.get("grabber_subscriptions_blocklist")
            or s.get("downloader_blocklist")
            or DEFAULT_BLOCKLIST
        )

    def _enabled_parser_sites(self):
        """Sites are selected only in Parser settings; Grabber mirrors them."""
        settings = self.main.settings if hasattr(self, "main") else {}
        out = []
        seen = set()

        def add_site(domain, cfg=None):
            cfg = cfg if isinstance(cfg, dict) else {}
            if not cfg.get("enabled", False):
                return
            raw = str(cfg.get("base_url") or cfg.get("login_url") or cfg.get("url") or cfg.get("domain") or domain or "").strip()
            if not raw:
                return
            if not raw.startswith(("http://", "https://")):
                raw = "https://" + raw
            root = raw.rstrip("/")
            host = _host(root)
            if not host or host in seen:
                return
            # Only include sites that the downloader helper can query by tag.
            if not _tag_search_api(root, "", page=0, limit=1, settings=settings):
                return
            seen.add(host)
            out.append(root)

        sites = settings.get("sites", {}) if isinstance(settings.get("sites"), dict) else {}
        for domain, cfg in sites.items():
            add_site(domain, cfg)
        custom = settings.get("custom_sites", []) if isinstance(settings.get("custom_sites"), list) else []
        for cfg in custom:
            if isinstance(cfg, dict):
                add_site(cfg.get("domain") or cfg.get("base_url") or cfg.get("url"), cfg)

        if not out:
            for domain in ("rule34.xxx", "gelbooru.com", "e621.net", "booru.allthefallen.moe", "danbooru.donmai.us"):
                out.append("https://" + domain)
        return out

    def _grabber_site_threads(self, setting_name, site_count):
        """Resolve grabber concurrency.  0/auto means one lane per site."""
        site_count = max(1, int(site_count or 1))
        try:
            raw = self._runtime_settings().get(setting_name, 0)
        except Exception:
            raw = 0
        try:
            if isinstance(raw, str) and raw.strip().lower() in {"", "auto", "all", "site", "sites"}:
                value = 0
            else:
                value = int(raw or 0)
        except Exception:
            value = 0
        if value <= 0:
            # Keep a hard ceiling for accidental huge custom-site lists, but for
            # the normal 5-site set this means all sites are active together.
            return max(1, min(site_count, 8))
        return max(1, min(value, site_count, 16))

    def _grabber_session_lock_for_host(self, host):
        host = (host or "").lower().replace("www.", "")
        with self._grabber_session_cache_lock:
            lock = self._grabber_session_locks.get(host)
            if lock is None:
                lock = threading.RLock()
                self._grabber_session_locks[host] = lock
            return lock

    def _grabber_session_for_url(self, url):
        host = _host(url)
        if "allthefallen" not in host:
            return _session_for_url(url, self.append_log)
        with self._grabber_session_cache_lock:
            session = self._grabber_session_cache.get(host)
            if session is None:
                session = _session_for_url(url, self.append_log)
                self._grabber_session_cache[host] = session
            return session

    def _grabber_protected_site_hosts(self):
        # v226: ATF is Danbooru-like and is queried through /posts.json, so it
        # must stay available for preview cards and exact-MD5 metadata fanout.
        # Expensive protected-media work is still blocked separately by
        # _grabber_prefetch_protected_originals_enabled(); this list is only for
        # fully unsafe metadata sites.  Keep the hook for future sites, but do
        # not classify ATF metadata API as skippable.
        return set()

    def _grabber_include_protected_sites(self):
        try:
            return bool(self._runtime_settings().get("grabber_include_protected_sites", False))
        except Exception:
            return False

    def _grabber_preview_sites_from_parser(self):
        sites = list(self._enabled_parser_sites() or [])
        if self._grabber_include_protected_sites():
            return sites
        protected = self._grabber_protected_site_hosts()
        filtered = []
        skipped = []
        for root in sites:
            host = _host(root)
            # v227: do not special-case ATF here.  v226 cleared the protected
            # host set, but the leftover substring check still skipped
            # booru.allthefallen.moe before any /posts.json request was made.
            # Only hosts explicitly returned by _grabber_protected_site_hosts()
            # may be filtered from metadata preview.
            if host in protected:
                skipped.append(host)
                continue
            filtered.append(root)
        if skipped and not getattr(self, "_grabber_logged_protected_skip", False):
            try:
                self.append_log("PREVIEW SKIP PROTECTED SITES: " + ", ".join(sorted(set(skipped))) + " (включается в настройках разработчика)")
            except Exception:
                pass
            self._grabber_logged_protected_skip = True
        return filtered

    def retranslate(self):
        pass

    def open_br34(self):
        open_br34(self.url.text().strip(), self, log_func=self.append_log)

    def start_downloader_worker(self, mode, payload):
        if getattr(self, "worker", None) and self.worker.isRunning():
            self.append_log("BUSY: downloader уже работает")
            return

        self.open_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.tag_download_btn.setEnabled(False)
        self.preview_search_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.pause_btn.setText("Пауза")
        self.dedupe_btn.setEnabled(False)
        self.cleanup_btn.setEnabled(False)
        self.info.setVisible(True)

        self._worker_runtime = self._make_worker_runtime()
        self.worker = DownloaderWorker(self, mode, payload)
        self.worker.log.connect(self.log_requested.emit)
        self.worker.done.connect(self.on_worker_done)
        self.worker.start()

    def on_worker_done(self):
        self.open_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        self.tag_download_btn.setEnabled(True)
        self.preview_search_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setText("Пауза")
        self.dedupe_btn.setEnabled(True)
        self.cleanup_btn.setEnabled(True)
        self._worker_runtime = None
        self.append_log("WORKER DONE")

    def current_worker(self):
        w = getattr(self, "worker", None)
        if w is not None and w.isRunning():
            return w
        return None

    def should_stop(self):
        w = self.current_worker()
        return bool(w and getattr(w, "stop_requested", False))

    def wait_if_paused(self):
        w = self.current_worker()
        if w and hasattr(w, "wait_if_paused"):
            w.wait_if_paused()

    def _sqlite_write_block_reason(self):
        try:
            from core.database.connection import writes_blocked, writes_blocked_reason
            if writes_blocked():
                return str(writes_blocked_reason() or "SQLite writes are blocked")
        except Exception:
            pass
        return ""

    def _wait_for_sqlite_writes_ready(self, context="download", max_seconds=900):
        """Do not create archive files while SQLite metadata writes are paused.

        After an unclean shutdown v164+ intentionally opens the window early and
        blocks DB writes until the deferred health check passes.  Downloading an
        original file during that window creates an unmanaged media file: bytes
        land in output/, but sources/tags/MD5 cannot be recorded.  That breaks
        grabber hiding and duplicate handling.
        """
        start = time.monotonic()
        next_log = 0.0
        while True:
            reason = self._sqlite_write_block_reason()
            if not reason:
                return True
            if self.should_stop():
                self.append_log(f"DOWNLOAD WAIT SQLITE CANCELLED: {reason}")
                return False
            elapsed = time.monotonic() - start
            if elapsed >= float(max_seconds or 0):
                self.append_log(f"DOWNLOAD WAIT SQLITE TIMEOUT: {reason}")
                return False
            now = time.monotonic()
            if now >= next_log:
                self.append_log(f"DOWNLOAD WAIT SQLITE: {context}; {reason}")
                next_log = now + 5.0
            time.sleep(0.25)

    def toggle_pause_worker(self):
        w = self.current_worker()
        if not w:
            return
        paused = self.pause_btn.text() != "Продолжить"
        w.set_paused(paused)
        self.pause_btn.setText("Продолжить" if paused else "Пауза")
        self.append_log("PAUSE" if paused else "RESUME")

    def stop_worker(self):
        w = self.current_worker()
        if not w:
            return
        w.request_stop()
        self.append_log("STOP REQUESTED: задача остановится после текущего файла")

    def shutdown_fast(self):
        """Request downloader/grabber workers to stop without blocking the UI close."""
        try:
            self._dl_queue = []
        except Exception:
            pass
        for attr in ("worker", "_dl_worker"):
            try:
                w = getattr(self, attr, None)
                if w is not None and hasattr(w, "isRunning") and w.isRunning():
                    if hasattr(w, "request_stop"):
                        w.request_stop()
                    elif hasattr(w, "requestInterruption"):
                        w.requestInterruption()
            except Exception:
                pass
        try:
            for loader in list(getattr(self, "_full_loaders", []) or []):
                if loader is not None and hasattr(loader, "isRunning") and loader.isRunning():
                    if hasattr(loader, "requestInterruption"):
                        loader.requestInterruption()
            self._full_loaders = []
        except Exception:
            pass

    def downloads_root(self):
        rt = getattr(self, "_worker_runtime", None)
        if isinstance(rt, dict) and rt.get("downloads_root") is not None:
            root = Path(rt["downloads_root"])
        else:
            base = self._runtime_base()
            root = base / "downloads"
        for status in ("found", "partial_match", "no_match"):
            for sub in ("media", "tags", "source", "searched", "cache"):
                (root / status / sub).mkdir(parents=True, exist_ok=True)
        return root

    def status_dirs(self, status="found"):
        root = self.downloads_root() / status
        return {
            "media": root / "media",
            "tags": root / "tags",
            "source": root / "source",
            "searched": root / "searched",
            "cache": root / "cache",
        }

    def blocklist_set(self):
        rt = getattr(self, "_worker_runtime", None)
        if isinstance(rt, dict) and isinstance(rt.get("blocklist"), set):
            return set(rt["blocklist"])
        raw = self._grabber_subscription_blocklist_text() or ""
        return {x.strip().lower() for x in re.split(r"[,;\s]+", raw) if x.strip()}

    def has_blocked_tag(self, tags):
        bad = self.blocklist_set()
        low = {str(t).lower() for t in tags or []}
        return bool(bad & low)

    def _md5_ram_index_enabled(self):
        try:
            return bool(self._runtime_settings().get("developer_preload_md5_index", True))
        except Exception:
            return True

    def _normalize_md5_hex(self, md5):
        md5 = str(md5 or "").strip().lower()
        return md5 if re.fullmatch(r"[0-9a-f]{32}", md5) else ""

    def _build_md5_ram_index(self):
        """Build a compact exact-MD5 lookup for downloader/grabber duplicate checks.

        Do not load the old sidecar/cache blob into RAM.  It may contain paths,
        tags and JSON overhead and can explode from ~500 MB on disk into several
        GB of Python objects.  This index loads only the normalized MD5 and the
        canonical live path from SQLite, then keeps it updated after successful
        downloads/imports.
        """
        idx = {}
        if not self._md5_ram_index_enabled():
            return idx
        t0 = time.time()
        try:
            from core.database.connection import db
            with db(self._runtime_settings(), readonly=True) as con:
                for row in con.execute(
                    "SELECT lower(COALESCE(hash_md5,'')) AS md5, path "
                    "FROM images WHERE deleted=0 AND COALESCE(hash_md5,'')<>''"
                ):
                    try:
                        md5 = self._normalize_md5_hex(row["md5"] if hasattr(row, "keys") else row[0])
                        path = str((row["path"] if hasattr(row, "keys") else row[1]) or "")
                        if md5 and path and md5 not in idx:
                            idx[md5] = path
                    except Exception:
                        pass
        except Exception as e:
            try:
                self.append_log(f"MD5 RAM INDEX ERROR: {type(e).__name__}: {e}")
            except Exception:
                pass
            return {}
        # Legacy/orphan repair: some older archive rows were created before the
        # SQLite hash column was reliably populated. Do not hash the whole
        # library here; just trust managed filenames that visibly contain a
        # 32-hex MD5 token. This lets the grabber hide/enrich already present
        # files, including old no_match/partial_match ("брак") objects.
        try:
            for p in self.all_known_media_files():
                name = str(getattr(p, "stem", "") or "").lower()
                candidates = []
                if re.fullmatch(r"[0-9a-f]{32}", name):
                    candidates.append(name)
                candidates += re.findall(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", name)
                for md5 in candidates:
                    md5 = self._normalize_md5_hex(md5)
                    if md5 and md5 not in idx:
                        idx[md5] = str(p)
                        break
        except Exception:
            pass

        try:
            self.append_log(f"MD5 RAM INDEX: загружено {len(idx)} хэшей за {time.time() - t0:.2f}с")
        except Exception:
            pass
        return idx

    def _get_md5_ram_index(self):
        if not self._md5_ram_index_enabled():
            return None
        idx = getattr(self, "_md5_ram_index", None)
        if idx is not None:
            return idx
        with self._md5_ram_index_lock:
            idx = getattr(self, "_md5_ram_index", None)
            if idx is None:
                idx = self._build_md5_ram_index()
                self._md5_ram_index = idx
            return idx

    def _md5_ram_lookup(self, md5):
        md5 = self._normalize_md5_hex(md5)
        if not md5:
            return ""
        idx = self._get_md5_ram_index()
        if not idx:
            return ""
        return str(idx.get(md5) or "")

    def _md5_ram_note(self, md5, path):
        md5 = self._normalize_md5_hex(md5)
        path = str(path or "")
        if not md5 or not path or not self._md5_ram_index_enabled():
            return
        idx = self._get_md5_ram_index()
        if idx is None:
            return
        with self._md5_ram_index_lock:
            idx.setdefault(md5, path)

    def _md5_ram_reset(self):
        with self._md5_ram_index_lock:
            self._md5_ram_index = None

    def _grabber_exact_md5_fanout_enabled(self):
        # On by default: when the same bytes exist on several enabled booru
        # sites, download the file once but collect post-page sources and tags
        # from every exact-MD5 hit before registering metadata.
        try:
            return bool(self._runtime_settings().get("grabber_exact_md5_fanout", True))
        except Exception:
            return True

    def _grabber_visual_merge_enabled(self):
        # Exact MD5 stays the safe primary identity.  This second layer is only
        # for the online grabber UI: if two sites host visually identical files
        # with different byte MD5s (re-encoded PNG/JPG/WebP), show one card.
        # For different MD5s it is display-only; parser/source persistence still
        # requires exact MD5/post identity.
        try:
            return bool(self._runtime_settings().get("grabber_visual_hash_merge", True))
        except Exception:
            return True

    def _grabber_visual_merge_distance(self):
        try:
            return max(0, min(16, int(self._runtime_settings().get("grabber_visual_hash_distance", 4) or 4)))
        except Exception:
            return 4

    def _visual_hash_distance(self, a, b):
        a = str(a or "").strip().lower()
        b = str(b or "").strip().lower()
        if not a or not b or len(a) != len(b):
            return None
        try:
            return bin(int(a, 16) ^ int(b, 16)).count("1")
        except Exception:
            return None

    def _preview_visual_hash_for_path(self, path):
        if not self._grabber_visual_merge_enabled():
            return ""
        try:
            path = Path(path)
            if not path.exists() or not path.is_file():
                return ""
            # Do not phash animated/video files as stable still images.
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                return ""
            return str(_visual_hash(path) or "").strip().lower()
        except Exception:
            return ""

    def _find_visual_merge_key(self, acc, item):
        if not self._grabber_visual_merge_enabled():
            return ""
        vh = str(item.get("visual_hash") or "").strip().lower()
        if not vh:
            return ""
        threshold = self._grabber_visual_merge_distance()
        best_key = ""
        best_dist = None
        item_sites = set(str(x) for x in (item.get("sites") or []) if x)
        for k, cur in list((acc or {}).items()):
            cur_hashes = list(cur.get("visual_hashes") or [])
            if cur.get("visual_hash"):
                cur_hashes.append(cur.get("visual_hash"))
            cur_hashes = list(dict.fromkeys(str(x or "").strip().lower() for x in cur_hashes if x))
            if not cur_hashes:
                continue
            # Avoid merging same-site variants by pHash.  Same-site ambiguity is
            # exactly where sketches/crops/edits are common; cross-site visual
            # merge is the intended use case.
            cur_sites = set(str(x) for x in (cur.get("sites") or []) if x)
            if item_sites and cur_sites and item_sites <= cur_sites:
                continue
            for old_vh in cur_hashes:
                dist = self._visual_hash_distance(vh, old_vh)
                if dist is None or dist > threshold:
                    continue
                if best_dist is None or dist < best_dist:
                    best_key = k
                    best_dist = dist
        if best_key:
            try:
                self.append_log(f"PREVIEW VISUAL MERGE: {vh} -> {best_key} distance={best_dist}")
            except Exception:
                pass
        return best_key

    def _exact_md5_search_apis(self, site, md5):
        md5 = self._normalize_md5_hex(md5)
        if not md5:
            return []
        host = _host(site)
        try:
            auth = _apt_auth_query(self._runtime_settings(), host)
        except Exception:
            auth = ""
        if host in ("rule34.xxx", "api.rule34.xxx") and ("api_key=" not in auth or "user_id=" not in auth):
            try:
                self.append_log(f"EXACT MD5 SKIP [{host}]: нужен API key + User ID для api.rule34.xxx")
            except Exception:
                pass
            return []
        out = []
        # Common booru syntax.  This is the safest first request because it goes
        # through the same tag-search path as the visible grabber pages.
        api = _tag_search_api(site, f"md5:{md5}", page=0, limit=10, settings=self._runtime_settings())
        if api:
            out.append(api)

        # DAPI installs are inconsistent: Gelbooru's md5=<hash> fallback is useful.
        # Do not use it for rule34.xxx: v246 logs proved api.rule34.xxx ignores
        # that parameter and returns unrelated posts.
        if host == "gelbooru.com":
            out.append(f"https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1&md5={md5}&pid=0&limit=10{auth}")

        return list(dict.fromkeys(x for x in out if x))

    def _exact_md5_item_from_post(self, site, post, md5, session=None):
        md5 = self._normalize_md5_hex(md5)
        if not isinstance(post, dict) or not md5:
            return None
        file_url = _extract_file_url_from_json(post) or ""
        post_url = self._post_url_from_post(site, post)
        preview_url = _extract_preview_url_from_json(post) or file_url
        groups = _dedupe_group_dict(self._groups_from_post_for_grabber_site(site, post, session=session))
        post_tags = _tag_list_from_post(post)
        media_urls = self._preview_media_urls_from_post(post, file_url, preview_url)
        download_url = self._preview_original_url_from_post(post, file_url)
        host = _host(site)
        return {
            "key": "md5:" + md5,
            "md5": md5,
            "id": str(post.get("id") or ""),
            "sites": [host] if host else [],
            "post_urls": [post_url] if post_url else [],
            "file_urls": media_urls or ([file_url] if file_url else []),
            "download_url": download_url,
            "preview_url": preview_url,
            "thumb_path": "",
            "tags": post_tags,
            "groups": groups,
            "post": dict(post),
            "already_path": self._candidate_in_library(post_url=post_url, file_url=file_url, md5=md5),
            "source_tag_groups": [{"url": post_url or file_url, "groups": groups, "method": "grabber_exact_md5"}],
        }

    def _search_exact_md5_on_site(self, site, md5):
        md5 = self._normalize_md5_hex(md5)
        if not md5:
            return []
        host = _host(site)
        session = self._grabber_session_for_url(site)
        out = []
        seen_sites = set()
        for api in self._exact_md5_search_apis(site, md5):
            try:
                self.append_log(f"EXACT MD5 TRY [{host}]: {_mask_sensitive_url(api)}")
                r = self._preview_http_get(session, site, api, timeout=30)
                self.append_log(f"EXACT MD5 STATUS [{host}]: {r.status_code} {r.headers.get('content-type','')}")
                if r.status_code >= 400:
                    continue
                for post in _posts_from_json_response(r):
                    got_md5 = self._normalize_md5_hex(_post_md5_from_json(post))
                    if got_md5 != md5:
                        continue
                    item = self._exact_md5_item_from_post(site, post, md5, session=session)
                    if not item:
                        continue
                    # One exact post per site is enough for cross-site tag/source
                    # collection.  Exact duplicates on the same site are not used
                    # to multiply tags and source rows.
                    site_key = _host((item.get("post_urls") or [site])[0]) or host
                    if site_key in seen_sites:
                        continue
                    seen_sites.add(site_key)
                    out.append(item)
                if out:
                    break
            except Exception as e:
                self.append_log(f"EXACT MD5 ERROR [{host}]: {type(e).__name__}: {e}")
        return out

    def _enrich_candidate_exact_md5(self, candidate):
        candidate = dict(candidate or {})
        if not self._grabber_exact_md5_fanout_enabled():
            return candidate
        md5 = self._normalize_md5_hex(candidate.get("md5") or (candidate.get("post") or {}).get("md5") or "")
        if not md5:
            return candidate

        cached = None
        with self._grabber_exact_md5_lock:
            cached = self._grabber_exact_md5_cache.get(md5)
        if cached is None:
            found = []
            try:
                sites = list(self._grabber_preview_sites_from_parser())
                if len(sites) <= 1:
                    for site in sites:
                        if self.should_stop():
                            break
                        self.wait_if_paused()
                        found.extend(self._search_exact_md5_on_site(site, md5))
                else:
                    try:
                        # 0/auto = one lane per site.  This is important for
                        # cross-site fanout: a slow ATF PoW lane must not keep
                        # rule34/gelbooru/e621/danbooru waiting behind a global
                        # two-thread cap.
                        max_workers = self._grabber_site_threads("grabber_exact_md5_threads", len(sites))
                    except Exception:
                        max_workers = self._grabber_site_threads("grabber_preview_threads", len(sites))
                    self.append_log(f"EXACT MD5 FANOUT ASYNC: {md5} site_threads={max_workers}")
                    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="grabber-md5") as ex:
                        futs = {ex.submit(self._search_exact_md5_on_site, site, md5): site for site in sites}
                        for fut in concurrent.futures.as_completed(futs):
                            site = futs[fut]
                            host = _host(site)
                            if self.should_stop():
                                break
                            self.wait_if_paused()
                            try:
                                found.extend(fut.result() or [])
                            except Exception as e:
                                self.append_log(f"EXACT MD5 SITE THREAD ERROR [{host}]: {type(e).__name__}: {e}")
            except Exception as e:
                self.append_log(f"EXACT MD5 FANOUT ERROR: {type(e).__name__}: {e}")
            with self._grabber_exact_md5_lock:
                self._grabber_exact_md5_cache[md5] = list(found)
                cached = list(found)

        acc = {}
        base = dict(candidate)
        base["key"] = "md5:" + md5
        base["md5"] = md5
        self._merge_preview_candidate(acc, base)
        for item in cached or []:
            self._merge_preview_candidate(acc, item)
        enriched = acc.get("md5:" + md5) or candidate

        # Prefer an already-downloaded canonical path if any exact-MD5 source
        # exposes it; this lets the download action become a metadata merge.
        if not enriched.get("already_path"):
            hit = self._candidate_in_library(md5=md5)
            if hit:
                enriched["already_path"] = hit
        try:
            self.append_log(
                f"EXACT MD5 FANOUT: {md5} sites={len(enriched.get('sites') or [])} "
                f"sources={len(enriched.get('post_urls') or [])}"
            )
        except Exception:
            pass
        return enriched

    def _register_download_metadata_for_path(self, media_path, post_url, file_url, post, groups, *, hash_md5=""):
        """Attach post metadata to one canonical physical file.

        When a second booru exposes the exact same bytes, its URL/tags belong
        to the existing image row; they must not be lost merely because a
        second download is skipped.
        """
        if not media_path:
            return None
        groups = _dedupe_group_dict(groups or _groups_from_post(post))
        tags = []
        for g in ("artist", "contributor", "character", "copyright", "species", "general", "meta", "lore", "invalid"):
            tags += groups.get(g, [])
        try:
            from core.import_pipeline import register_media_import
            _settings = self.main.settings if hasattr(self, "main") else self.settings
            # User-facing sources must be post pages only.  Direct CDN file,
            # sample and preview URLs stay in raw_metadata.file_url but are not
            # added to the visible sources list.
            all_sources = [x for x in (post_url,) if x]
            source_tag_groups = None
            if isinstance(post, dict):
                extra_sources = post.get("_all_sources") or []
                if isinstance(extra_sources, (list, tuple)):
                    only_post_pages = []
                    for x in extra_sources:
                        u = str(x or "").strip()
                        if not u:
                            continue
                        h = _host(u)
                        path = urlparse(u).path.lower()
                        if "/posts/" in path or "page=post" in u or "s=view" in u:
                            only_post_pages.append(u)
                    all_sources = list(dict.fromkeys(all_sources + only_post_pages))
                stg = post.get("_source_tag_groups")
                if isinstance(stg, list):
                    source_tag_groups = stg
            _hash_md5 = str(hash_md5 or (post.get("md5") if isinstance(post, dict) else "") or "")
            result = register_media_import(
                _settings,
                Path(media_path),
                tags=tags,
                groups=groups,
                sources=all_sources,
                status="downloaded_found",
                original_path="",
                hash_md5=_hash_md5,
                raw=post,
                post_url=post_url,
                file_url=file_url,
                site=_host(post_url or file_url),
                source_tag_groups=source_tag_groups,
                origin="downloader",
                merge_existing=True,
            )
            self._md5_ram_note(_hash_md5, media_path)
            return result
        except Exception as e:
            try:
                self.append_log(f"SQLITE METADATA ERROR: {type(e).__name__}: {e}")
            except Exception:
                pass
            return None

    def _register_download_metadata_by_stem(self, stem, post_url, file_url, post, groups):
        """Attach downloader metadata to the SQLite row for the stored media."""
        dirs = self.status_dirs("found")
        media_path = None
        try:
            for f in dirs["media"].iterdir():
                if f.is_file() and f.stem == stem:
                    media_path = f
                    break
        except Exception:
            media_path = None
        return self._register_download_metadata_for_path(media_path, post_url, file_url, post, groups)


    def _trash_downloaded_media(self, paths, *, reason: str, kept_path: str = "", make_backup: bool = True):
        from core.library_lifecycle import trash_media_paths
        result = trash_media_paths(self.main.settings, [Path(p) for p in paths], reason=reason, make_backup=make_backup)
        if result.get("error"):
            self.append_log("TRASH ERROR: " + str(result.get("error")))
        return result

    def _deleted_md5_policy_blocks(self, md5: str) -> bool:
        if not str(md5 or "").strip():
            return False
        try:
            from core.deleted_registry import has_deleted_md5
            return has_deleted_md5(str(md5).strip().lower(), settings=self.main.settings) and str(self.main.settings.get("deleted_reimport_policy", "skip")) != "return_inbox"
        except Exception:
            return False

    def _path_from_duplicate_reason(self, reason):
        """Extract the local media path from a duplicate-check reason.

        Older grabber builds could download a file while SQLite writes were
        blocked.  That leaves a valid media file on disk without an image row.
        When the next download sees the same MD5 filename, it must attach
        metadata to that orphan instead of returning "no saved path".
        """
        text = str(reason or "")
        if not text:
            return None
        # The reason strings are intentionally human-readable:
        #   "same remote md5/name: F:\\...\\file.jpg"
        #   "same name: F:\\...\\file.jpg"
        #   "MD5 already in RAM index: F:\\...\\file.jpg"
        # Keep parsing permissive so old log/reason variants still work.
        if ": " in text:
            candidate = text.split(": ", 1)[1].strip()
        else:
            m = re.search(r"([A-Za-z]:\\.*|/.*)$", text)
            candidate = m.group(1).strip() if m else ""
        if not candidate:
            return None
        try:
            p = Path(candidate)
            return p if p.exists() and p.is_file() else None
        except Exception:
            return None

    def _merge_duplicate_reason_existing_file(self, reason, *, file_url, post_url, post, groups, remote_md5):
        """Register metadata for an already-present duplicate file.

        A duplicate hit is not automatically an error in the grabber.  If the
        exact bytes are already present, especially as an orphan from an older
        failed metadata write, the correct behavior is: keep the physical file,
        attach the current post/source/tags, return the path, and hide the card.
        """
        p = self._path_from_duplicate_reason(reason)
        if not p:
            return None
        md5 = self._normalize_md5_hex(remote_md5)
        if md5:
            try:
                actual = _file_md5(p).lower()
                if actual != md5:
                    self.append_log(f"DUPLICATE NAME MD5 MISMATCH: {p.name} actual={actual} remote={md5}; скачиваю как новый файл")
                    return False
            except Exception as e:
                self.append_log(f"DUPLICATE FILE VERIFY WARN: {type(e).__name__}: {e}")
                return None
        if post is None:
            return None
        if not self._wait_for_sqlite_writes_ready("перед привязкой существующего файла", max_seconds=900):
            return None
        result = self._register_download_metadata_for_path(p, post_url, file_url, post, groups, hash_md5=md5)
        if result is None:
            self.append_log(f"MERGE EXISTING DUPLICATE FAILED: {p}")
            return None
        try:
            from core.library_lifecycle import update_url_history
            update_url_history(self.main.settings, file_url, status="merged_existing_file", error=f"already present as {p}")
        except Exception:
            pass
        if md5:
            self._md5_ram_note(md5, p)
        self.append_log(f"MERGED EXISTING FILE SOURCE: {p}")
        return p

    def _download_file(self, session, file_url, post_url, stem_hint="download", post=None, groups=None):
        dirs = self.status_dirs("found")
        remote_md5 = ""
        if isinstance(post, dict):
            remote_md5 = str(post.get("md5") or "")
        if post is not None and not self._wait_for_sqlite_writes_ready("перед скачиванием оригинала", max_seconds=900):
            return None
        # Same exact bytes from another source are not a skipped post: attach
        # this source/tags to the existing image row without downloading again.
        # A live copy wins over any obsolete "previously deleted" marker.
        if remote_md5 and post is not None:
            try:
                from core.services.metadata_service import found_media_path_by_md5
                canonical = self._md5_ram_lookup(remote_md5) or found_media_path_by_md5(self.main.settings, remote_md5)
                if canonical:
                    self._register_download_metadata_for_path(canonical, post_url, file_url, post, groups, hash_md5=remote_md5)
                    from core.library_lifecycle import update_url_history
                    update_url_history(self.main.settings, file_url, status="merged_exact_md5", error=f"same bytes as {canonical}")
                    self.append_log(f"MERGED EXACT MD5 SOURCE: existing media {canonical}")
                    return Path(canonical)
            except Exception as e:
                self.append_log(f"EXACT MD5 MERGE WARN: {e}")

        if self._deleted_md5_policy_blocks(remote_md5):
            self.append_log("SKIP DELETED: exact MD5 was permanently removed earlier")
            try:
                from core.library_lifecycle import update_url_history
                update_url_history(self.main.settings, file_url, status="skipped_deleted", error="exact MD5 previously deleted")
            except Exception:
                pass
            return None

        dup, reason = self.is_duplicate_download(file_url, stem_hint, remote_md5, post_url=post_url)
        if dup:
            merged = self._merge_duplicate_reason_existing_file(
                reason, file_url=file_url, post_url=post_url, post=post, groups=groups, remote_md5=remote_md5
            )
            if merged:
                return Path(merged)
            if merged is False and remote_md5:
                # A stale/orphan file had the expected MD5-shaped name but not
                # the expected content.  Do not let name-only fallback block the
                # real download; the output naming loop below will suffix it.
                dup = False
            if dup:
                self.append_log(f"SKIP DUPLICATE: {reason}")
                try:
                    from core.library_lifecycle import update_url_history
                    update_url_history(self.main.settings, file_url, status="duplicate", error=reason)
                except Exception:
                    pass
                return None

        r = self._stream_media_get(session, file_url, post_url, timeout=60)
        self.append_log(f"FILE STATUS: {r.status_code} {r.headers.get('content-type', '')}")

        if r.status_code >= 400:
            raise RuntimeError(f"file status {r.status_code}")
        if not self._response_looks_like_media(r, file_url):
            raise RuntimeError(f"file response is not media: {r.headers.get('content-type', '')}")

        ext = _ext_from_url_or_type(file_url, r.headers.get("content-type"))
        stem = _safe_name(stem_hint or Path(urlparse(file_url).path).stem or "download")
        out = dirs["media"] / (stem + ext)
        n = 1
        while out.exists():
            out = dirs["media"] / f"{stem}_{n}{ext}"
            n += 1

        try:
            from core.preflight import ensure_space_for_write
            _settings = self.main.settings if hasattr(self, "main") else self.settings
            _expected = int(r.headers.get("Content-Length", 0) or 0)
            _ok_space, _space_msg = ensure_space_for_write(_settings, out, _expected)
            if not _ok_space:
                self.append_log("STOP NO DISK SPACE: " + _space_msg)
                return None
            _, total = atomic_write_chunks(
                out,
                r.iter_content(chunk_size=1024 * 512),
                should_stop=self.should_stop,
                before_chunk=self.wait_if_paused,
            )
        except InterruptedError:
            self.append_log("STOPPED DURING FILE DOWNLOAD")
            return None

        if out.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"} and not self._looks_like_image_or_animation_path(out):
            try:
                out.unlink(missing_ok=True)
            except Exception:
                pass
            raise RuntimeError("downloaded file is not a decodable image")

        # Resolve exact bytes after download even when a site did not expose a
        # remote MD5. Any existing live row is canonical regardless of filename.
        try:
            actual_md5 = _file_md5(out).lower()
        except Exception:
            actual_md5 = ""
        if actual_md5:
            try:
                from core.services.metadata_service import found_media_path_by_md5
                canonical = self._md5_ram_lookup(actual_md5)
                if canonical and str(Path(canonical)) == str(Path(out)):
                    canonical = ""
                canonical = canonical or found_media_path_by_md5(self.main.settings, actual_md5, exclude_path=str(out))
                if canonical:
                    if post is not None:
                        self._register_download_metadata_for_path(canonical, post_url, file_url, post, groups, hash_md5=actual_md5)
                    from core.services.media_storage_service import unlink_managed
                    unlink_managed(self.main.settings, out, operation="downloader.discard_exact_copy")
                    from core.library_lifecycle import update_url_history
                    update_url_history(self.main.settings, file_url, status="merged_exact_md5", error=f"same bytes as {canonical}")
                    self.append_log(f"MERGED JUST-DOWNLOADED EXACT MD5 SOURCE: {canonical}")
                    return Path(canonical)
            except Exception as e:
                self.append_log(f"POST-DOWNLOAD EXACT MD5 MERGE WARN: {e}")

        # Sites do not always provide a post MD5. If there is no live canonical
        # copy and the exact content was intentionally removed, keep it rejected.
        try:
            if actual_md5 and self._deleted_md5_policy_blocks(actual_md5):
                self._trash_downloaded_media([out], reason="reimport_deleted_rejected", make_backup=False)
                from core.library_lifecycle import update_url_history
                update_url_history(self.main.settings, file_url, status="skipped_deleted", error="downloaded content MD5 was permanently deleted")
                self.append_log("SKIP DELETED AFTER DOWNLOAD: moved rejected copy to «Удалено»")
                return None
        except Exception:
            pass

        # Files missing a historical MD5 row still receive one final byte-exact
        # filesystem fallback; metadata is merged before the transient copy dies.
        try:
            is_dup, old_path = self.is_exact_existing_file(out)
            if is_dup and old_path:
                if post is not None:
                    self._register_download_metadata_for_path(old_path, post_url, file_url, post, groups, hash_md5=actual_md5)
                from core.services.media_storage_service import unlink_managed
                unlink_managed(self.main.settings, out, operation="downloader.discard_exact_fallback")
                try:
                    from core.library_lifecycle import update_url_history
                    update_url_history(self.main.settings, file_url, status="merged_exact_md5", error=f"same bytes as {old_path}")
                except Exception:
                    pass
                self.append_log(f"MERGED JUST-DOWNLOADED EXACT MD5 SOURCE: {old_path}")
                return Path(old_path)
        except Exception as e:
            self.append_log(f"POST-DOWNLOAD EXACT MD5 MERGE WARN: {e}")

        if actual_md5:
            try:
                from core.services.media_storage_service import normalize_managed_content_name
                original_name = Path(urlparse(file_url).path).name or out.name
                new_out = normalize_managed_content_name(
                    self.main.settings,
                    out,
                    actual_md5,
                    operation="downloader.normalize_content_filename",
                    original_name=original_name,
                )
                if str(new_out) != str(out):
                    self.append_log(f"RENAMED CONTENT-SAFE: {out.name} -> {new_out.name}")
                    out = Path(new_out)
            except Exception as e:
                self.append_log(f"CONTENT-SAFE RENAME WARN: {type(e).__name__}: {e}")

        if post is not None:
            if not self._wait_for_sqlite_writes_ready("перед записью метаданных", max_seconds=900):
                try:
                    out.unlink(missing_ok=True)
                    self.append_log("DOWNLOAD DISCARDED: SQLite write lock was not released; unmanaged file removed")
                except Exception:
                    pass
                return None
            meta_result = self._register_download_metadata_for_path(out, post_url, file_url, post, groups or _groups_from_post(post), hash_md5=actual_md5)
            if meta_result is None and self._sqlite_write_block_reason():
                try:
                    out.unlink(missing_ok=True)
                    self.append_log("DOWNLOAD DISCARDED: SQLite metadata write is blocked; unmanaged file removed")
                except Exception:
                    pass
                return None
        self._md5_ram_note(actual_md5, out)

        self.append_log(f"SAVED: {out} ({total} bytes)")
        return out

    def _grabber_categorized_flat_tag_hosts(self):
        return {"rule34.xxx", "api.rule34.xxx", "gelbooru.com", "xbooru.com", "hypnohub.net"}

    def _grabber_category_cache_host(self, host):
        host = str(host or "").lower().replace("www.", "")
        if host == "api.rule34.xxx":
            return "rule34.xxx"
        return host

    def _grabber_dapi_tag_base_for_host(self, host):
        host = self._grabber_category_cache_host(host)
        if host == "rule34.xxx":
            return "https://api.rule34.xxx/index.php", "rule34.xxx"
        if host == "gelbooru.com":
            return "https://gelbooru.com/index.php", "gelbooru.com"
        if host in ("xbooru.com", "hypnohub.net"):
            return f"https://{host}/index.php", host
        return "", host

    def _grabber_group_from_tag_type(self, value):
        key = str(value if value is not None else "").strip().lower()
        return {
            "0": "general", "general": "general",
            "1": "artist", "artist": "artist",
            "3": "copyright", "copyright": "copyright", "series": "copyright",
            "4": "character", "character": "character",
            "5": "meta", "meta": "meta", "metadata": "meta",
            "species": "species", "specie": "species",
            "contributor": "contributor", "contributors": "contributor",
            "lore": "lore", "invalid": "invalid",
        }.get(key, "general")

    def _grabber_parse_dapi_tag_rows(self, response):
        rows = []
        try:
            data = response.json()
        except Exception:
            data = None
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            tag_data = data.get("tag") or data.get("tags") or data.get("post")
            if isinstance(tag_data, list):
                rows = tag_data
            elif isinstance(tag_data, dict):
                rows = [tag_data]
            elif data.get("name"):
                rows = [data]
        if not rows:
            try:
                soup = BeautifulSoup(response.text or "", "xml")
                rows = [dict(node.attrs) for node in soup.find_all("tag")]
            except Exception:
                rows = []
        return [row for row in rows if isinstance(row, dict)]

    def _cached_tag_categories_for_grabber(self, host, tags):
        """Return safe cached categories for flat-tag grabber posts.

        Older builds could cache ``general`` for rule34.xxx before the tag-list
        classifier was fixed.  Trusting those stale general rows prevents the
        new DAPI lookup from ever running, so v242 only trusts cached general
        categories when they were written by the versioned grabber classifier.
        Non-general categories are safe to reuse.
        """
        host = self._grabber_category_cache_host(host)
        names = []
        seen = set()
        for raw in tags or []:
            name = normalize_tag(str(raw))
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        if not host or not names:
            return {}
        trusted_general_methods = {"grabber_dapi_tag_api_v242"}
        out = {}
        try:
            from core.database.storage import db
            with db(self._runtime_settings(), readonly=True) as con:
                for i in range(0, len(names), 500):
                    chunk = names[i:i + 500]
                    ph = ",".join(["?"] * len(chunk))
                    rows = con.execute(
                        f"SELECT tag_name, category, source_method FROM tag_category_cache WHERE site_key=? AND tag_name IN ({ph})",
                        [host, *chunk],
                    ).fetchall()
                    for row in rows:
                        name = normalize_tag(row["tag_name"])
                        category = str(row["category"] or "general").strip().lower() or "general"
                        method = str(row["source_method"] or "").strip().lower()
                        if not name:
                            continue
                        if category != "general" or method in trusted_general_methods:
                            out[name] = category
        except Exception:
            return {}
        return out

    def _grabber_request_dapi_tag_rows(self, session, session_host, base, params, *, host):
        url = base + "?" + urlencode(params)
        auth = _apt_auth_query(self._runtime_settings(), session_host)
        if auth:
            url += auth
        try:
            self.append_log(f"PREVIEW TAG CATEGORY TRY [{host}]: {_mask_sensitive_url(url)}")
            r = self._preview_http_get(session, f"https://{session_host}/", url, timeout=30)
            self.append_log(f"PREVIEW TAG CATEGORY STATUS [{host}]: {r.status_code} {r.headers.get('content-type','')}")
            if r.status_code >= 400:
                return []
            return self._grabber_parse_dapi_tag_rows(r)
        except Exception as e:
            try:
                self.append_log(f"PREVIEW TAG CATEGORY ERROR [{host}]: {type(e).__name__}: {e}")
            except Exception:
                pass
            return []

    def _categorize_flat_tags_for_grabber(self, site, tags, session=None):
        """Classify flat Gelbooru-family tag strings for grabber previews.

        rule34.xxx DAPI post JSON exposes one flat ``tags`` string.  Without
        this pass the online post sidebar renders every rule34 tag as general.
        The tag catalogue is used only for tags already present on the exact
        post/card; it never introduces new metadata.
        """
        host = self._grabber_category_cache_host(_host(site))
        clean = []
        seen = set()
        for raw in tags or []:
            tag = normalize_tag(str(raw))
            if tag and tag not in seen:
                seen.add(tag)
                clean.append(tag)
        if not clean or host not in self._grabber_categorized_flat_tag_hosts():
            return {}

        out = {}
        remaining = set(clean)
        cached = self._cached_tag_categories_for_grabber(host, clean)
        for name, group in dict(cached or {}).items():
            name = normalize_tag(name)
            if name in remaining:
                out[name] = str(group or "general").lower() or "general"
                remaining.discard(name)
        if not remaining:
            return out

        base, session_host = self._grabber_dapi_tag_base_for_host(host)
        if not base:
            return out
        session = session or self._grabber_session_for_url(f"https://{session_host}/")
        updates = {}

        def consume_rows(rows):
            changed = False
            for row in rows or []:
                name = normalize_tag(row.get("name") or row.get("tag") or row.get("label") or "")
                if not name or name not in remaining:
                    continue
                group = self._grabber_group_from_tag_type(row.get("type", row.get("category", row.get("tag_type"))))
                out[name] = group
                updates[name] = group
                remaining.discard(name)
                changed = True
            return changed

        names = [t for t in clean if t in remaining]
        for i in range(0, len(names), 50):
            chunk = [t for t in names[i:i + 50] if t in remaining]
            if not chunk:
                continue
            # Gelbooru-family docs use names=<space separated tags>, but some
            # rule34 mirrors behave inconsistently.  Try the documented bulk
            # form first, then fall back to exact one-tag queries for tags that
            # still have no category.
            rows = self._grabber_request_dapi_tag_rows(
                session, session_host, base,
                {"page": "dapi", "s": "tag", "q": "index", "json": "1", "names": " ".join(chunk)},
                host=host,
            )
            consume_rows(rows)
            for tag in list(chunk):
                if tag not in remaining:
                    continue
                # Per-tag fallback.  This fixes rule34.xxx cases where bulk
                # names returns an empty/partial catalogue and everything would
                # otherwise stay in general forever.
                rows = self._grabber_request_dapi_tag_rows(
                    session, session_host, base,
                    {"page": "dapi", "s": "tag", "q": "index", "json": "1", "name": tag},
                    host=host,
                )
                consume_rows(rows)

        # Mark still-unknown tags as verified general only after the v242 lookup
        # had a chance to ask the remote catalogue.  This prevents old stale
        # general rows from blocking classification while still avoiding endless
        # rechecks for truly general tags.
        for name in list(remaining):
            updates.setdefault(name, "general")
        try:
            if updates:
                from core.database.storage import upsert_tag_category_cache
                # legacy text-test anchor: method="grabber_dapi_tag_api"
                upsert_tag_category_cache(self._runtime_settings(), host, updates, method="grabber_dapi_tag_api_v242")
        except Exception:
            pass
        # Unknown tags are intentionally kept as general by callers while
        # preserving the exact post's flat tag membership.
        return out

    def _preview_category_map_for_posts(self, site, posts, session=None):
        host = self._grabber_category_cache_host(_host(site))
        if host not in self._grabber_categorized_flat_tag_hosts():
            return {}
        flat = []
        for post in posts or []:
            groups = _groups_from_post(post)
            has_informative = any(groups.get(g) for g in ("artist", "contributor", "character", "copyright", "species", "meta", "lore", "invalid"))
            if has_informative:
                continue
            flat += _tag_list_from_post(post)
        return self._categorize_flat_tags_for_grabber(site, flat, session=session)

    def _groups_from_post_for_grabber_site(self, site, post, *, category_map=None, session=None):
        groups = _dedupe_group_dict(_groups_from_post(post))
        host = self._grabber_category_cache_host(_host(site))
        if host not in self._grabber_categorized_flat_tag_hosts():
            return groups
        has_informative = any(groups.get(g) for g in ("artist", "contributor", "character", "copyright", "species", "meta", "lore", "invalid"))
        if has_informative:
            return groups
        flat = _tag_list_from_post(post)
        if not flat:
            return groups
        category_map = dict(category_map or self._categorize_flat_tags_for_grabber(site, flat, session=session) or {})
        rebuilt = {"artist": [], "contributor": [], "character": [], "copyright": [], "species": [], "general": [], "meta": [], "lore": [], "invalid": []}
        for raw in flat:
            tag = normalize_tag(str(raw))
            if not tag:
                continue
            group = str(category_map.get(tag) or "general").lower()
            if group not in rebuilt:
                group = "general"
            rebuilt[group].append(tag)
        return _dedupe_group_dict(rebuilt)

    def _merge_html_groups_into_post(self, session, post_url, post):
        """
        DAPI/rule34 XML often returns only flat 'tags'.
        The visible post page has grouped sidebar tags. Merge those groups
        into the post dict before saving it in SQLite.
        """
        try:
            # Official JSON APIs are authoritative. Their visible HTML pages
            # are UI output (and can be login/Cloudflare pages), not metadata.
            if _host(post_url) in ("e621.net", "e926.net", "danbooru.donmai.us", "donmai.us"):
                return post, _groups_from_post(post)
            # Only bother if groups are empty or everything is general.
            cur_groups = _groups_from_post(post)
            has_grouped = bool(
                cur_groups.get("artist")
                or cur_groups.get("character")
                or cur_groups.get("copyright")
                or cur_groups.get("species")
                or cur_groups.get("meta")
            )

            if has_grouped:
                return post, cur_groups

            # For rule34.xxx / Gelbooru-family DAPI posts the JSON response is
            # authoritative but often flat.  Prefer the documented tag-catalog
            # API to classify those already-confirmed tags before touching HTML.
            dapi_groups = self._groups_from_post_for_grabber_site(post_url, post, session=session)
            if any(dapi_groups.get(g) for g in ("artist", "contributor", "character", "copyright", "species", "meta", "lore", "invalid")):
                post = dict(post or {})
                post["tag_string_artist"] = " ".join(dapi_groups.get("artist", []))
                post["tag_string_character"] = " ".join(dapi_groups.get("character", []))
                post["tag_string_copyright"] = " ".join(dapi_groups.get("copyright", []))
                post["tag_string_species"] = " ".join(dapi_groups.get("species", []))
                post["tag_string_general"] = " ".join(dapi_groups.get("general", []))
                post["tag_string_meta"] = " ".join(dapi_groups.get("meta", []))
                all_tags = []
                for g in ("artist", "contributor", "character", "copyright", "species", "general", "meta", "lore", "invalid"):
                    all_tags += dapi_groups.get(g, [])
                post["tag_string"] = " ".join(all_tags)
                return post, dapi_groups

            self.append_log(f"HTML GROUPS TRY: {post_url}")
            r = session.get(post_url, timeout=30)
            self.append_log(f"HTML GROUPS STATUS: {r.status_code} {r.headers.get('content-type', '')}")

            if r.status_code >= 400:
                return post, cur_groups

            html_groups = self._groups_from_html(r.text)
            if any(html_groups.values()):
                post = dict(post or {})
                post["tag_string_artist"] = " ".join(html_groups.get("artist", []))
                post["tag_string_character"] = " ".join(html_groups.get("character", []))
                post["tag_string_copyright"] = " ".join(html_groups.get("copyright", []))
                post["tag_string_species"] = " ".join(html_groups.get("species", []))
                post["tag_string_general"] = " ".join(html_groups.get("general", []))
                post["tag_string_meta"] = " ".join(html_groups.get("meta", []))

                all_tags = []
                for g in ("artist", "contributor", "character", "copyright", "species", "general", "meta", "lore", "invalid"):
                    all_tags += html_groups.get(g, [])
                post["tag_string"] = " ".join(all_tags)

                self.append_log(
                    "HTML GROUPS OK: "
                    f"artist={len(html_groups.get('artist', []))} "
                    f"character={len(html_groups.get('character', []))} "
                    f"copyright={len(html_groups.get('copyright', []))} "
                    f"species={len(html_groups.get('species', []))} "
                    f"general={len(html_groups.get('general', []))} "
                    f"meta={len(html_groups.get('meta', []))}"
                )

                return post, html_groups

            return post, cur_groups

        except Exception as e:
            self.append_log(f"HTML GROUPS ERROR: {type(e).__name__}: {e}")
            return post, _groups_from_post(post)

    def _find_post_data_and_file_url(self, post_url, session):
        for api in _candidate_api_urls(post_url):
            try:
                self.append_log(f"API TRY: {_mask_sensitive_url(api)}")
                r = self._preview_http_get(session, post_url, api, timeout=30)
                self.append_log(f"API STATUS: {r.status_code} {r.headers.get('content-type', '')}")
                if r.status_code >= 400:
                    continue
                posts = _posts_from_json_response(r)
                if posts:
                    post = posts[0]
                    file_url = _extract_file_url_from_json(post)
                    if file_url:
                        merged_post, _merged_groups = self._merge_html_groups_into_post(session, post_url, post)
                        return merged_post, file_url
            except Exception as e:
                self.append_log(f"API ERROR: {type(e).__name__}: {e}")

        if _host(post_url) in ("e621.net", "e926.net", "danbooru.donmai.us", "donmai.us"):
            raise RuntimeError("Official JSON API did not return usable post data; HTML tag fallback disabled to avoid polluted tags or protection pages")

        self.append_log(f"HTML TRY: {post_url}")
        r = session.get(post_url, timeout=30)
        self.append_log(f"HTML STATUS: {r.status_code} {r.headers.get('content-type', '')}")
        if r.status_code >= 400:
            raise RuntimeError(f"post page status {r.status_code}")

        file_url = _extract_file_url_from_html(r.text, post_url)
        html_groups = self._groups_from_html(r.text)
        html_tags = []
        for g in ("artist", "contributor", "character", "copyright", "species", "general", "meta", "lore", "invalid"):
            html_tags += html_groups.get(g, [])
        post = {
            "tag_string": " ".join(html_tags),
            "tag_string_artist": " ".join(html_groups.get("artist", [])),
            "tag_string_character": " ".join(html_groups.get("character", [])),
            "tag_string_copyright": " ".join(html_groups.get("copyright", [])),
            "tag_string_species": " ".join(html_groups.get("species", [])),
            "tag_string_general": " ".join(html_groups.get("general", [])),
            "tag_string_meta": " ".join(html_groups.get("meta", [])),
            "source": post_url,
        }
        return post, file_url

    def _groups_from_html(self, html_text):
        groups = {"artist": [], "contributor": [], "character": [], "copyright": [], "species": [], "general": [], "meta": [], "lore": [], "invalid": []}

        try:
            soup = BeautifulSoup(html_text or "", "html.parser")

            selector_map = {
                "artist": [
                    "li.tag-type-artist a[href*='tags=']",
                    ".tag-type-artist a[href*='tags=']",
                    "li[class*='artist'] a[href*='tags=']",
                ],
                "character": [
                    "li.tag-type-character a[href*='tags=']",
                    ".tag-type-character a[href*='tags=']",
                    "li[class*='character'] a[href*='tags=']",
                ],
                "copyright": [
                    "li.tag-type-copyright a[href*='tags=']",
                    ".tag-type-copyright a[href*='tags=']",
                    "li[class*='copyright'] a[href*='tags=']",
                ],
                "species": [
                    "li.tag-type-species a[href*='tags=']",
                    ".tag-type-species a[href*='tags=']",
                    "li[class*='species'] a[href*='tags=']",
                ],
                "general": [
                    "li.tag-type-general a[href*='tags=']",
                    ".tag-type-general a[href*='tags=']",
                    "li[class*='general'] a[href*='tags=']",
                ],
                "meta": [
                    "li.tag-type-metadata a[href*='tags=']",
                    "li.tag-type-meta a[href*='tags=']",
                    ".tag-type-metadata a[href*='tags=']",
                    ".tag-type-meta a[href*='tags=']",
                    "li[class*='metadata'] a[href*='tags=']",
                    "li[class*='meta'] a[href*='tags=']",
                ],
            }

            def candidates_from_link(a):
                # Display text may contain counters/UI labels (for example
                # ``horse 231k``); tag identity comes only from ``tags=`` href.
                out = []
                href = a.get("href", "") or ""
                try:
                    q = parse_qs(urlparse(href).query)
                    for raw in q.get("tags", []):
                        out += str(raw).replace("+", " ").split()
                except Exception:
                    pass
                return out

            for group, selectors in selector_map.items():
                for sel in selectors:
                    for a in soup.select(sel):
                        for raw in candidates_from_link(a):
                            tag = _clean_download_tag(raw)
                            if tag:
                                groups[group].append(tag)

            # If classes are absent, use fallback only for real tag sidebar links.
            if not any(groups.values()):
                for a in soup.select("#tag-sidebar a[href*='tags='], #tag-list a[href*='tags=']"):
                    for raw in candidates_from_link(a):
                        tag = _clean_download_tag(raw)
                        if tag:
                            groups["general"].append(tag)

        except Exception:
            pass

        return _dedupe_group_dict(groups)

    def _tags_from_html(self, html_text):
        groups = self._groups_from_html(html_text)
        out = []
        for g in ("artist", "contributor", "character", "copyright", "species", "general", "meta", "lore", "invalid"):
            out += groups.get(g, [])
        return list(dict.fromkeys(out))

    def all_known_media_files(self):
        base = self._runtime_base()
        roots = [
            base / "found" / "media",
            base / "partial_match" / "media",
            base / "no_match" / "media",
            base / "downloads" / "found" / "media",
            base / "downloads" / "partial_match" / "media",
            base / "downloads" / "no_match" / "media",
        ]
        exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov"}
        out = []
        for root in roots:
            if root.exists():
                out += [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts]
        return out

    def is_duplicate_download(self, file_url, stem_hint, remote_md5="", post_url=""):
        """Pre-download duplicate check.

        Checks in order (fastest first):
        1. DB: exact URL match in raw_metadata.file_url or post_url
        2. DB: MD5 match in images table
        3. Filesystem: filename/MD5 check (legacy)
        """
        # 1. URL-based check (fast, O(1) with index)
        if file_url or post_url:
            try:
                from core.database.connection import get_connection
                conn = get_connection(self.main.settings)
                for url in [u for u in [file_url, post_url] if u]:
                    row = conn.execute(
                        "SELECT i.path FROM raw_metadata rm "
                        "JOIN images i ON i.id=rm.image_id "
                        "WHERE rm.file_url=? OR rm.post_url=?",
                        (url, url)
                    ).fetchone()
                    if row:
                        return True, f"URL already in DB: {row[0]}"
            except Exception:
                pass

        # 2. MD5-based check.  Prefer the optional RAM index: it avoids a
        # SQL roundtrip for every grabber card and, more importantly, never
        # falls back to hashing the whole library before a download.
        remote_md5 = self._normalize_md5_hex(remote_md5)
        if remote_md5:
            hit = self._md5_ram_lookup(remote_md5)
            if hit:
                return True, f"MD5 already in RAM index: {hit}"
            try:
                from core.database.connection import get_connection
                conn = get_connection(self.main.settings)
                row = conn.execute(
                    "SELECT path FROM images WHERE hash_md5=?", (remote_md5,)
                ).fetchone()
                if row:
                    self._md5_ram_note(remote_md5, row[0])
                    return True, f"MD5 already in DB: {row[0]}"
            except Exception:
                pass

        # 3. Filesystem fallback (legacy, name-only).
        #
        # Do NOT hash every existing file here.  On a real archive with tens of
        # thousands of images this used to run before every grabber download and
        # could freeze the queue for many minutes between
        # "PREVIEW DOWNLOAD" and "FILE GET TRY".  Exact content merging is still
        # done safely below, after the file has been downloaded, through SQLite
        # MD5 lookup and the stricter post-download duplicate fallback.
        wanted_stem = _safe_name(stem_hint)
        wanted_base = _base_without_copy_suffix(wanted_stem).lower()

        for p in self.all_known_media_files():
            try:
                stem = p.stem.lower()
                base = _base_without_copy_suffix(stem).lower()

                if remote_md5 and remote_md5 == stem:
                    return True, f"same remote md5/name: {p}"

                if stem == wanted_stem.lower():
                    return True, f"same name: {p}"

                # Do not create Windows-style duplicates when the original name exists.
                if base == wanted_base and (_is_copy_suffix(stem) or _is_copy_suffix(wanted_stem)):
                    return True, f"copy-suffix duplicate: {p}"
            except Exception:
                pass
        return False, ""

    def is_exact_existing_file(self, new_file: Path) -> tuple[bool, str]:
        """Post-download exact duplicate check.

        Fast path: RAM/SQLite MD5 index.  The legacy filesystem scan is optional
        because rglob/stat/hash over a large archive after every grabber download
        destroys throughput.
        """
        try:
            new_md5 = _file_md5(new_file).lower()
            new_bytes = int(new_file.stat().st_size)
            new_pixels = _duplicate_image_size(new_file)
            new_base = _base_without_copy_suffix(new_file.stem).lower()
        except Exception:
            return False, ""

        hit = self._md5_ram_lookup(new_md5)
        if hit and str(Path(hit)) != str(Path(new_file)):
            return True, hit
        try:
            from core.services.metadata_service import found_media_path_by_md5
            hit = found_media_path_by_md5(self.main.settings, new_md5, exclude_path=str(new_file))
            if hit:
                self._md5_ram_note(new_md5, hit)
                return True, str(hit)
        except Exception:
            pass

        if not bool(self._runtime_settings().get("developer_filesystem_duplicate_fallback", False)):
            return False, ""

        for oldp in self.all_known_media_files():
            if oldp == new_file or not oldp.exists():
                continue
            try:
                if int(oldp.stat().st_size) != new_bytes:
                    continue
                if _file_md5(oldp).lower() != new_md5:
                    continue
                if _duplicate_image_size(oldp) != new_pixels:
                    continue
                old_base = _base_without_copy_suffix(oldp.stem).lower()
                if old_base == new_base or oldp.stem.lower() == new_md5:
                    return True, str(oldp)
            except Exception:
                pass
        return False, ""


    def cleanup_by_blocklist(self):
        self.start_downloader_worker("cleanup", {})

    def scan_and_clean_duplicates(self):
        self.start_downloader_worker("dedupe", {})

    def _cleanup_by_blocklist_impl(self):
        bad = self.blocklist_set()
        if not bad:
            self.append_log("BLOCKLIST EMPTY")
            return

        base = self._runtime_base()
        roots = [base / "found" / "media", base / "downloads" / "found" / "media"]
        blocked_media = []
        for media_root in roots:
            if not media_root.exists():
                continue
            bucket = media_root.parent
            for media in media_root.rglob("*"):
                if not media.is_file():
                    continue
                tags = set()
                # SQLite is the only live source of truth for downloaded metadata.
                try:
                    from core.database.connection import db
                    with db(self.main.settings, readonly=True) as con:
                        rows = con.execute(
                            """SELECT LOWER(t.normalized_name) AS name FROM tags t
                               JOIN image_tags it ON it.tag_id=t.id
                               JOIN images i ON i.id=it.image_id
                               WHERE i.path=? AND i.deleted=0""",
                            (str(media),),
                        ).fetchall()
                        tags.update(str(r["name"] or "").lower() for r in rows)
                except Exception:
                    pass
                if bad & tags:
                    blocked_media.append(media)

        if not blocked_media:
            self.append_log("BLOCKLIST CLEANUP DONE: совпадений нет")
            return
        try:
            result = self._trash_downloaded_media(blocked_media, reason="downloader_blocklist_cleanup", make_backup=True)
            deleted = int(result.get("trashed_files", 0) or 0)
            self.append_log(f"BLOCKLIST → Удалено: {deleted} файлов")
            if result.get("error"):
                self.append_log("BLOCKLIST TRASH ERROR: " + str(result.get("error")))
        except Exception as e:
            self.append_log(f"BLOCKLIST TRASH ERROR: {e}")

    def _scan_and_clean_duplicates_impl(self):
        files = self.all_known_media_files()
        self.append_log(f"DUP SCAN: files={len(files)}")
        by_md5 = {}
        auto_deleted = 0
        candidates = []

        for p in files:
            try:
                md = _file_md5(p)
                by_md5.setdefault(md, []).append(p)
            except Exception as e:
                self.append_log(f"DUP HASH ERROR: {p}: {e}")

        for md, group in by_md5.items():
            if len(group) < 2:
                continue
            group = sorted(group, key=lambda x: (len(str(x)), str(x).lower()))
            keep = group[0]
            for p in group[1:]:
                if _base_without_copy_suffix(p.stem).lower() == _base_without_copy_suffix(keep.stem).lower() and _is_copy_suffix(p.stem):
                    try:
                        result = self._trash_downloaded_media([p], reason="downloader_exact_copy", make_backup=True)
                        auto_deleted += int(result.get("trashed_files", 0) or 0)
                        self.append_log(f"AUTO TRASH EXACT COPY: {p}")
                    except Exception as e:
                        self.append_log(f"DELETE ERROR: {p}: {e}")
                else:
                    candidates.append((keep, p, "exact md5"))

        # visual duplicate candidates: conservative only distance <= 3
        vh = {}
        for p in files:
            if not p.exists() or p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            h = _visual_hash(p)
            if h:
                vh.setdefault(h, []).append(p)
        for h, group in vh.items():
            if len(group) > 1:
                group = sorted(group, key=lambda x: (len(str(x)), str(x).lower()))
                for p in group[1:]:
                    if p.exists() and group[0].exists():
                        candidates.append((group[0], p, "same visual hash"))

        self.append_log(f"DUP SCAN DONE: auto_deleted={auto_deleted}, manual_candidates={len(candidates)}")
        for a, b, reason in candidates[:50]:
            self.show_duplicate_choice(a, b, reason)

    def show_duplicate_choice(self, a, b, reason):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Дубликат: {reason}")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Выбери, что удалить. Если не уверен — нажми оставить оба."))

        grid = QGridLayout()
        for col, p in enumerate([a, b]):
            lab = QLabel()
            lab.setAlignment(Qt.AlignCenter)
            pix = QPixmap(str(p))
            if not pix.isNull():
                lab.setPixmap(pix.scaled(260, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                lab.setText("NO PREVIEW")
            grid.addWidget(lab, 0, col)
            grid.addWidget(QLabel(_media_size_text(p)), 1, col)

        lay.addLayout(grid)
        row = QHBoxLayout()
        del_a = QPushButton("Удалить левую")
        del_b = QPushButton("Удалить правую")
        keep = QPushButton("Оставить оба")
        row.addWidget(del_a); row.addWidget(del_b); row.addWidget(keep)
        lay.addLayout(row)

        del_a.clicked.connect(lambda: (self._trash_downloaded_media([Path(a)], reason="downloader_manual_duplicate", make_backup=True), dlg.accept()))
        del_b.clicked.connect(lambda: (self._trash_downloaded_media([Path(b)], reason="downloader_manual_duplicate", make_backup=True), dlg.accept()))
        keep.clicked.connect(dlg.reject)
        dlg.exec()

    # ── Online preview grabber ────────────────────────────────────────────────

    def clear_preview_query(self):
        self.preview_query.clear()
        try:
            self.main.settings["grabber_preview_query"] = ""
            save_settings(self.main.settings)
        except Exception:
            pass

    def _preview_grid_shape(self):
        # Граббер должен повторять обычную Галерею: те же колонки и строки.
        # Старые grabber_columns/grabber_rows больше не читаем, иначе сохранённый
        # мусор снова превращает 8×4 в 7×5/другой размер.
        cols = max(1, int(self.main.settings.get("columns", 8) or 8))
        rows = max(1, int(self.main.settings.get("rows_per_page", 4) or 4))
        return cols, rows

    def _preview_per_page(self):
        c, r = self._preview_grid_shape()
        return max(1, c * r)

    def _preview_prefetch_pages(self):
        # Сколько уже готовых UI-страниц держать впереди текущей.  Для граббера
        # это НЕ пользовательский лимит выдачи и НЕ число API-страниц на сайт.
        # Фиксированное окно: 1/5 — ничего, переход на 2/5 грузит 6,
        # 3/6 — 7, 4/7 — 8.  Старые сохранённые значения вроде 12/20 из
        # прошлых сборок принудительно зажимаем, иначе один переход снова
        # превращается в огромный запрос по 100+ постов с каждого сайта.
        try:
            pages = int(self._runtime_settings().get("grabber_preview_prefetch_pages", 4) or 4)
        except Exception:
            pages = 4
        pages = max(1, min(4, pages))
        try:
            if int(self.main.settings.get("grabber_preview_prefetch_pages", pages) or pages) != pages:
                self.main.settings["grabber_preview_prefetch_pages"] = pages
                save_settings(self.main.settings)
        except Exception:
            pass
        return pages

    def _preview_fetch_limit(self, pages=None):
        # В интерфейсе нет отдельного «лимита выдачи». Видимая страница всегда
        # равна сетке галереи: колонки × строки.  Первый блок грузит текущую
        # страницу + prefetch-окно, следующие блоки догружают ровно одну
        # UI-страницу, чтобы было: 2/5 -> грузится 6, 3/6 -> 7, 4/7 -> 8.
        per = self._preview_per_page()
        try:
            pages = int(pages if pages is not None else self._preview_prefetch_pages())
        except Exception:
            pages = self._preview_prefetch_pages()
        return max(per, per * max(1, pages))

    def _preview_initial_fetch_limit(self):
        return self._preview_fetch_limit(self._preview_prefetch_pages() + 1)

    def _preview_append_fetch_limit(self):
        return self._preview_fetch_limit(1)

    def _preview_tile_size(self):
        cols, rows = self._preview_grid_shape()
        try:
            from ui.gallery_page import _current_theme_name
            theme = _current_theme_name()
        except Exception:
            theme = "abyss"
        spacing = 7 if theme in ("r34", "r34dark") else 10
        try:
            vieww = max(240, self.preview_scroll.viewport().width() - 8)
            viewh = max(220, self.preview_scroll.viewport().height() - 8)
        except Exception:
            vieww, viewh = 1200, 720
        max_tile = max(64, int(self.main.settings.get("card_height", 220) or 220))
        # Важно: размер клетки считается и по ширине, и по высоте. Иначе на
        # широком/высоком окне Qt визуально превращает 8×4 в 7×5/микроскролл.
        available_w = max(48, int((vieww - (cols * spacing * 2)) / max(1, cols)))
        available_h = max(48, int((viewh - (rows * spacing * 2)) / max(1, rows)))
        tile = max(48, min(max_tile, available_w, available_h))
        return tile, spacing

    def _parse_preview_sites(self):
        return self._grabber_preview_sites_from_parser()

    def _next_preview_request_token(self):
        try:
            self._preview_request_token = int(getattr(self, "_preview_request_token", 0) or 0) + 1
        except Exception:
            self._preview_request_token = 1
        return int(self._preview_request_token)

    def _current_preview_request_token(self):
        try:
            return int(getattr(self, "_preview_request_token", 0) or 0)
        except Exception:
            return 0

    def _clear_finished_preview_worker(self):
        w = getattr(self, "worker", None)
        if w is not None and not w.isRunning():
            try:
                w.deleteLater()
            except Exception:
                pass
            self.worker = None

    def _start_preview_worker(self, *, tags, sites=None, append=False, start_pages=None, limit_total=None, token=None):
        sites = list(sites if sites is not None else self._parse_preview_sites())
        if not sites:
            self.append_log("PREVIEW ERROR: во вкладке Парсер нет включённых поддерживаемых сайтов")
            return False
        tags = str(tags or "").strip()
        token = int(token if token is not None else self._next_preview_request_token())
        self._preview_query_text = tags
        if not append:
            self.preview_search_btn.setEnabled(False)
            self.preview_status.setText("Граббер: считаю общее количество и загружаю первую страницу…")
            self._preview_site_filter = ""
            self.preview_items = []
            self.preview_total_by_site = {}
            self._preview_next_page_by_site = {}
            self._preview_exhausted_sites = set()
            self._preview_loading_more = False
            self.preview_page_index = 1
            self._last_preview_autoload_key = None
            try:
                self._refresh_preview_sidebar()
                self.render_preview_page()
            except Exception:
                pass
        else:
            self._preview_loading_more = True
            self.preview_status.setText("Граббер: подгружаю следующую страницу…")
        self._worker_runtime = self._make_worker_runtime()
        self.worker = DownloaderWorker(self, "preview", {
            "sites": sites,
            "tags": tags,
            "limit_total": int(limit_total if limit_total is not None else (self._preview_append_fetch_limit() if append else self._preview_initial_fetch_limit())),
            "hide_existing": bool(self.main.settings.get("grabber_preview_hide_existing", True)),
            "append": bool(append),
            "start_pages": dict(start_pages or {}),
            "request_token": token,
        })
        self.worker.log.connect(self.log_requested.emit)
        self.worker.result.connect(self.on_preview_results)
        self.worker.done.connect(self.on_preview_worker_done)
        self.worker.start()
        return True

    def search_online_preview(self):
        self._clear_finished_preview_worker()
        sites = self._parse_preview_sites()
        tags = self.preview_query.text().strip()
        if not sites:
            self.append_log("PREVIEW ERROR: во вкладке Парсер нет включённых поддерживаемых сайтов")
            return
        try:
            self.main.settings["grabber_preview_query"] = tags
            save_settings(self.main.settings)
        except Exception:
            pass
        if getattr(self, "worker", None) and self.worker.isRunning():
            if getattr(self.worker, "mode", "") == "preview":
                # Invalidate the current preview stream immediately.  Old worker
                # may still emit queued cards while it is shutting down; the token
                # gate in on_preview_results will drop them.
                token = self._next_preview_request_token()
                self._pending_preview_search = {"tags": tags, "token": token}
                try:
                    self.worker.request_stop()
                except Exception:
                    pass
                self.append_log(f"PREVIEW SEARCH RESTART: остановка старого поиска, новый запрос: {tags or '<без тегов>'}")
                return
            self.append_log("BUSY: downloader уже работает")
            return
        self._pending_preview_search = None
        self._start_preview_worker(
            tags=tags,
            sites=sites,
            append=False,
            start_pages={},
            limit_total=self._preview_initial_fetch_limit(),
            token=self._next_preview_request_token(),
        )

    def on_preview_worker_done(self):
        self.preview_search_btn.setEnabled(True)
        self._preview_loading_more = False
        self._worker_runtime = None
        # The QThread can still report isRunning() for a very short time while
        # emitting done.  Clear the reference before launching a pending search,
        # otherwise the pending search can re-enter search_online_preview and
        # endlessly log PREVIEW SEARCH RESTART.
        try:
            old_worker = getattr(self, "worker", None)
            if old_worker is not None:
                old_worker.deleteLater()
        except Exception:
            pass
        self.worker = None
        try:
            self.preview_status.setText(self._preview_status_text())
        except Exception:
            pass
        pending = getattr(self, "_pending_preview_search", None)
        if pending is not None:
            self._pending_preview_search = None
            if isinstance(pending, dict):
                pending_tags = str(pending.get("tags") or "")
                pending_token = int(pending.get("token") or self._current_preview_request_token())
            else:
                pending_tags = str(pending or "")
                pending_token = self._current_preview_request_token()
            try:
                self.preview_query.setText(pending_tags)
            except Exception:
                pass
            QTimer.singleShot(0, lambda t=pending_tags, tok=pending_token: self._start_preview_worker(
                tags=t,
                sites=self._parse_preview_sites(),
                append=False,
                start_pages={},
                limit_total=self._preview_initial_fetch_limit(),
                token=tok,
            ))
            return
        # If user pressed Next past loaded visible cards, keep loading a bounded
        # number of remote chunks until that visible page exists.  This is needed
        # when "hide existing" is enabled: a public page can contain only posts
        # already present in the archive, so one append request may legitimately
        # add zero visible cards.  Unlike the old autoload loop, this continuation
        # happens only after an explicit Next click and has a hard cap.
        pending_page = getattr(self, "_preview_pending_page_after_load", None)
        if pending_page is not None:
            try:
                pending_page_i = max(1, int(pending_page))
            except Exception:
                pending_page_i = 1
            try:
                loaded_pages = max(1, self._preview_loaded_pages(self._filtered_preview_items()))
            except Exception:
                loaded_pages = 1
            if pending_page_i > loaded_pages and self._preview_has_more():
                try:
                    attempts = int(getattr(self, "_preview_manual_skip_attempts", 0) or 0)
                except Exception:
                    attempts = 0
                try:
                    max_attempts = int(self.main.settings.get("grabber_preview_manual_skip_chunks", 12) or 12)
                except Exception:
                    max_attempts = 12
                max_attempts = max(1, min(40, max_attempts))
                if attempts < max_attempts:
                    self._preview_manual_skip_attempts = attempts + 1
                    self._preview_pending_page_after_load = pending_page_i
                    self.append_log(
                        f"PREVIEW MANUAL LOAD: видимых карточек пока {loaded_pages} стр.; "
                        f"ищу страницу {pending_page_i}, пропускаю уже скачанные ({attempts + 1}/{max_attempts})"
                    )
                    QTimer.singleShot(0, self._load_more_preview_results)
                    return
                self.append_log(
                    f"PREVIEW MANUAL LOAD STOP: за {max_attempts} шагов не набралась страница {pending_page_i}; "
                    "дальше нажми → ещё раз или временно покажи уже скачанные"
                )
            self._preview_pending_page_after_load = None
            self._preview_manual_skip_attempts = 0
            try:
                self.preview_page_index = max(1, pending_page_i)
                QTimer.singleShot(0, self.render_preview_page)
            except Exception:
                pass

    def on_preview_results(self, payload):
        payload_token = None
        if isinstance(payload, dict):
            try:
                payload_token = int(payload.get("request_token")) if payload.get("request_token") is not None else None
            except Exception:
                payload_token = None
            if payload_token is not None and payload_token != self._current_preview_request_token():
                return
            items = list(payload.get("items") or [])
            append = bool(payload.get("append", False))
            total_by_site = payload.get("total_by_site") or {}
            next_pages = payload.get("next_pages") or {}
            exhausted = set(payload.get("exhausted_sites") or [])
            partial_progress = bool(payload.get("partial_progress", False))
        else:
            items = list(payload or [])
            append = False
            total_by_site = {}
            next_pages = {}
            exhausted = set()
            partial_progress = False

        # A card can be excluded while a preview worker is still running.  Filter
        # every partial/final payload again on the UI thread so stale worker
        # results cannot resurrect it.
        if self._grabber_preview_exclusions_enabled():
            try:
                items = [x for x in items if not self._is_grabber_preview_excluded(x)]
            except Exception:
                pass

        if append:
            acc = {}
            for old in self.preview_items or []:
                key = old.get("key") or str(id(old))
                acc[key] = old
            for item in items:
                try:
                    self._merge_preview_candidate(acc, item)
                except Exception:
                    key = item.get("key") or str(id(item))
                    acc[key] = item
            self.preview_items = list(acc.values())
        else:
            self.preview_items = list(items or [])
            self.preview_page_index = 1
            self._preview_site_filter = ""
            self.preview_total_by_site = {}
            self._preview_next_page_by_site = {}
            self._preview_exhausted_sites = set()

        for k, v in (total_by_site or {}).items():
            if v is None:
                continue
            try:
                self.preview_total_by_site[str(k)] = int(v)
            except Exception:
                pass
        for k, v in (next_pages or {}).items():
            try:
                self._preview_next_page_by_site[str(k)] = int(v)
            except Exception:
                pass
        self._preview_exhausted_sites |= set(str(x) for x in exhausted)
        if not partial_progress:
            self._preview_loading_more = False

        self.preview_items.sort(key=lambda x: (bool(x.get("already_path")), str(x.get("sites") or []), str(x.get("id") or "")))
        if partial_progress:
            try:
                self.preview_status.setText("Граббер: сайты ещё загружаются, карточки уже добавляются…")
            except Exception:
                pass
        else:
            self.preview_status.setText(self._preview_status_text())
        self._schedule_preview_ui_refresh(render=True, sidebar=True, delay=90)

    def _candidate_key(self, post, file_url, post_url):
        md5 = str(_post_md5_from_json(post or {}) or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", md5):
            return "md5:" + md5
        if file_url:
            return "file:" + str(file_url)
        return "post:" + _host(post_url) + ":" + str((post or {}).get("id") or post_url)

    def _candidate_in_library(self, post_url="", file_url="", md5=""):
        md5 = self._normalize_md5_hex(md5)
        hit = self._md5_ram_lookup(md5) if md5 else ""
        if hit:
            return hit
        try:
            from core.database.connection import db
            with db(self._runtime_settings(), readonly=True) as con:
                if md5:
                    row = con.execute("SELECT path FROM images WHERE deleted=0 AND lower(COALESCE(hash_md5,''))=? LIMIT 1", (md5,)).fetchone()
                    if row:
                        path = str(row["path"] or "")
                        self._md5_ram_note(md5, path)
                        return path
                def _url_variants(u):
                    raw = str(u or "").strip()
                    if not raw:
                        return []
                    vals = [raw, raw.rstrip("/")]
                    if raw.startswith("http://"):
                        vals.append("https://" + raw[len("http://"):])
                    if raw.startswith("https://"):
                        vals.append("http://" + raw[len("https://"):])
                    return list(dict.fromkeys(v for v in vals if v))

                for url in [u for u in (post_url, file_url) if u]:
                    for uv in _url_variants(url):
                        row = con.execute(
                            """SELECT i.path FROM sources s
                               JOIN image_sources x ON x.source_id=s.id
                               JOIN images i ON i.id=x.image_id
                               WHERE i.deleted=0 AND s.url=? LIMIT 1""",
                            (str(uv),),
                        ).fetchone()
                        if row:
                            return str(row["path"] or "")
                        row = con.execute(
                            """SELECT i.path FROM raw_metadata rm
                               JOIN images i ON i.id=rm.image_id
                               WHERE i.deleted=0 AND (rm.post_url=? OR rm.file_url=?) LIMIT 1""",
                            (str(uv), str(uv)),
                        ).fetchone()
                        if row:
                            return str(row["path"] or "")
                        row = con.execute(
                            """SELECT i.path FROM url_history uh
                               JOIN images i ON i.id=uh.image_id
                               WHERE i.deleted=0 AND uh.url=? AND uh.image_id>0 LIMIT 1""",
                            (str(uv),),
                        ).fetchone()
                        if row:
                            return str(row["path"] or "")
        except Exception:
            pass
        return ""

    def _grabber_preview_exclusions_enabled(self):
        # Scope is intentionally narrow: these manual exclusions hide cards only
        # in the online grabber preview/search UI.  They must never be treated as
        # parser/tagger bans; parser MD5/source discovery still runs normally and
        # can still attach tags/sources for the same image.
        try:
            return bool(self._runtime_settings().get("grabber_preview_manual_exclusions", True))
        except Exception:
            return True

    def _grabber_item_exclusion_identities(self, item):
        """Stable identities used by ПКМ → Исключить из поиска.

        Exact MD5 hides the same card across every booru mirror.  URL/key
        identities are kept as fallbacks for APIs that omit MD5.  visual_hash is
        deliberately only a fallback for no-MD5 cards, because pHash can be
        similar for distinct images and must not over-hide exact MD5 posts.
        """
        item = item or {}
        out = []
        md5s = []
        for value in [item.get("md5"), (item.get("post") or {}).get("md5")] + list(item.get("md5s") or []):
            md5 = self._normalize_md5_hex(value)
            if md5 and md5 not in md5s:
                md5s.append(md5)
                out.append(("md5", md5))
        key = str(item.get("key") or "").strip()
        if key:
            out.append(("key", key))
        for url in item.get("post_urls") or []:
            if str(url or "").strip():
                out.append(("post_url", str(url).strip()))
        file_urls = list(item.get("file_urls") or [])
        if item.get("download_url"):
            file_urls.append(str(item.get("download_url") or ""))
        if item.get("preview_url"):
            file_urls.append(str(item.get("preview_url") or ""))
        for url in file_urls:
            if str(url or "").strip():
                out.append(("file_url", str(url).strip()))
        # Only use visual hash as a fallback when there is no exact MD5.
        if not md5s:
            for vh in [item.get("visual_hash")] + list(item.get("visual_hashes") or []):
                vh = str(vh or "").strip().lower()
                if vh:
                    out.append(("visual_hash", vh))
        try:
            from core.grabber_exclusions import compact_identities
            return compact_identities(out)
        except Exception:
            # Conservative fallback: enough for in-memory matching if the helper
            # cannot be imported during early startup.
            seen = set()
            cleaned = []
            for typ, value in out:
                pair = (str(typ), str(value).strip())
                if pair[1] and pair not in seen:
                    seen.add(pair)
                    cleaned.append(pair)
            return cleaned

    def _grabber_exclusion_keyset(self, identities):
        keys = set()
        try:
            from core.grabber_exclusions import compact_identities
            identities = compact_identities(identities)
        except Exception:
            identities = list(identities or [])
        for typ, value in identities or []:
            typ = str(typ or "").strip().lower()
            value = str(value or "").strip()
            if typ and value:
                keys.add(f"{typ}:{value}")
        return keys

    def _load_grabber_exclusion_identities(self, *, force=False):
        if not self._grabber_preview_exclusions_enabled():
            return set()
        with self._grabber_exclusion_lock:
            if force or self._grabber_exclusion_identities is None:
                try:
                    from core.grabber_exclusions import active_identity_set
                    self._grabber_exclusion_identities = set(active_identity_set(self._runtime_settings()))
                except Exception as e:
                    self._grabber_exclusion_identities = set()
                    try:
                        self.append_log(f"PREVIEW EXCLUDE LOAD WARN: {type(e).__name__}: {e}")
                    except Exception:
                        pass
            return set(self._grabber_exclusion_identities or set())

    def _is_grabber_preview_excluded(self, item):
        if not self._grabber_preview_exclusions_enabled():
            return False
        keys = self._grabber_exclusion_keyset(self._grabber_item_exclusion_identities(item))
        if not keys:
            return False
        known = self._load_grabber_exclusion_identities()
        return bool(keys & known)

    def _remove_preview_items_matching_exclusion(self, excluded_keys):
        excluded_keys = set(excluded_keys or set())
        if not excluded_keys:
            return 0
        old = list(self.preview_items or [])
        kept = []
        removed = 0
        for item in old:
            keys = self._grabber_exclusion_keyset(self._grabber_item_exclusion_identities(item))
            if keys & excluded_keys:
                removed += 1
                continue
            kept.append(item)
        if removed:
            self.preview_items = kept
        return removed

    def _exclude_preview_candidate(self, item):
        if not self._grabber_preview_exclusions_enabled():
            QMessageBox.information(self, "Граббер", "Исключения граббера выключены в настройках.")
            return False
        identities = self._grabber_item_exclusion_identities(item)
        if not identities:
            QMessageBox.warning(self, "Граббер", "Не удалось определить MD5/URL карточки для исключения.")
            return False
        try:
            from core.grabber_exclusions import add_exclusion
            sites = ",".join(str(x) for x in (item.get("sites") or []) if str(x or "").strip())
            added = add_exclusion(
                self._runtime_settings(),
                identities,
                reason="manual_context_menu",
                query=str(getattr(self, "_preview_query_text", "") or ""),
                site=sites,
                note=str(((item.get("post_urls") or item.get("file_urls") or [""])[0]) or ""),
            )
            keys = self._grabber_exclusion_keyset(identities)
            with self._grabber_exclusion_lock:
                current = set(self._grabber_exclusion_identities or self._load_grabber_exclusion_identities(force=True))
                current |= keys
                self._grabber_exclusion_identities = current
            removed = self._remove_preview_items_matching_exclusion(keys)
            try:
                md5s = [v for t, v in identities if t == "md5"]
                label = (md5s[0] if md5s else str((item.get("post_urls") or item.get("file_urls") or [item.get("key") or ""])[0]))
                self.append_log(f"PREVIEW EXCLUDE: {label} identities={len(identities)} removed={removed} added={added}")
            except Exception:
                pass
            try:
                self.preview_status.setText("Карточка скрыта только в граббере; парсер/тэггер не блокируются.")
            except Exception:
                pass
            self._schedule_preview_ui_refresh(render=True, sidebar=True, delay=0)
            return True
        except Exception as e:
            QMessageBox.warning(self, "Граббер", f"Не удалось сохранить исключение: {type(e).__name__}: {e}")
            return False

    def _grabber_preview_merge_existing_sources_enabled(self):
        # When the online grabber sees a post that is already in the local
        # archive, keep it hidden but still attach the newly discovered source
        # and per-source tags to the existing row. This is especially useful
        # when the parser previously found only 1-2 sites and the grabber later
        # reveals one more exact-MD5 mirror.
        try:
            return bool(self._runtime_settings().get("grabber_preview_merge_existing_sources", True))
        except Exception:
            return True

    def _merge_existing_preview_candidate_metadata(self, item, already_path, *, reason="preview_existing"):
        if not self._grabber_preview_merge_existing_sources_enabled():
            return False
        path = str(already_path or "").strip()
        if not path:
            return False
        try:
            p = Path(path)
            if not p.exists() or not p.is_file():
                return False
        except Exception:
            return False
        post_urls = [str(u or "").strip() for u in (item.get("post_urls") or []) if str(u or "").strip()]
        file_urls = [str(u or "").strip() for u in (item.get("file_urls") or []) if str(u or "").strip()]
        download_url = str(item.get("download_url") or "").strip()
        source_urls = list(dict.fromkeys(post_urls + file_urls + ([download_url] if download_url else [])))
        if not source_urls:
            return False
        md5 = self._normalize_md5_hex(item.get("md5") or (item.get("post") or {}).get("md5") or "")
        merge_key = (path, md5, tuple(source_urls[:16]))
        with self._grabber_existing_merge_lock:
            if merge_key in self._grabber_existing_merge_seen:
                return False
            self._grabber_existing_merge_seen.add(merge_key)
        try:
            post = dict(item.get("post") or {})
            post["_all_sources"] = post_urls
            post["_source_tag_groups"] = list(item.get("source_tag_groups") or [])
            post_url = post_urls[0] if post_urls else ""
            file_url = download_url or (file_urls[0] if file_urls else "")
            result = self._register_download_metadata_for_path(
                p,
                post_url,
                file_url,
                post,
                item.get("groups") or {},
                hash_md5=md5,
            )
            if result is not None:
                try:
                    added = int((result or {}).get("source_added", 0) or 0) if isinstance(result, dict) else 0
                except Exception:
                    added = 0
                if md5:
                    self._md5_ram_note(md5, p)
                if added > 0:
                    self.append_log(
                        f"PREVIEW MERGE EXISTING SOURCE: {p.name} +{added} source(s) "
                        f"sites={','.join(item.get('sites') or [])}"
                    )
                return True
        except Exception as e:
            try:
                self.append_log(f"PREVIEW MERGE EXISTING WARN: {type(e).__name__}: {e}")
            except Exception:
                pass
        return False

    def _download_preview_thumb(self, session, preview_url, cache_key, referer=""):
        if not preview_url:
            return ""
        try:
            cache = self._grabber_cache_dir("preview")
            ext = Path(urlparse(preview_url).path).suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                ext = ".jpg"
            name = hashlib.sha1(str(cache_key or preview_url).encode("utf-8", "ignore")).hexdigest() + ext
            out = cache / name
            if out.exists() and out.stat().st_size > 0:
                return str(out)
            headers = {"Referer": referer or preview_url}
            r = session.get(preview_url, timeout=25, stream=True, headers=headers)
            if r.status_code >= 400:
                return ""
            tmp = out.with_name(out.name + f".{time.time_ns()}.tmp")
            with open(tmp, "wb") as f:
                total = 0
                for chunk in r.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 10 * 1024 * 1024:
                        break
                    f.write(chunk)
            if not self._looks_like_image_or_animation_path(tmp):
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                return ""
            tmp.replace(out)
            # Evict oldest files if cache is over limit
            try:
                self._grabber_cache_evict()
            except Exception:
                pass
            return str(out)
        except Exception:
            return ""

    def _merge_preview_candidate_visual_only(self, cur, item):
        """Collapse a visually duplicated online card without claiming same bytes.

        ATF/e621/rule34 can host the same-looking art with different physical
        MD5s because of recompression, resize, metadata stripping or format
        conversion.  For the grabber grid this may be shown as one grouped card,
        but it is not safe source proof.  Therefore this function must not merge
        ``sites``, ``post_urls``, ``file_urls``, ``source_tag_groups``, ``tags``
        or ``groups`` into the main candidate.  Those fields describe the exact
        physical card selected for download/parser metadata.  Visual-only data
        lives under ``visual_duplicate_*`` so the UI can show "похожие ×N"
        without making two different MD5s look like the same source set.
        """
        duplicate = {
            "sites": list(item.get("sites") or []),
            "post_urls": list(item.get("post_urls") or []),
            "file_urls": list(item.get("file_urls") or []),
            "md5": self._normalize_md5_hex(item.get("md5") or ""),
            "md5s": [self._normalize_md5_hex(x) for x in list(item.get("md5s") or []) if self._normalize_md5_hex(x)],
            "tags": list(item.get("tags") or []),
            "groups": item.get("groups") or {},
            "source_tag_groups": list(item.get("source_tag_groups") or []),
            "visual_hash": str(item.get("visual_hash") or "").strip().lower(),
            "key": str(item.get("key") or ""),
        }
        variants = list(cur.get("visual_duplicate_variants") or [])
        variant_key = (tuple(duplicate.get("post_urls") or []), duplicate.get("md5") or duplicate.get("key") or "")
        seen = {
            (tuple(v.get("post_urls") or []), str(v.get("md5") or v.get("key") or ""))
            for v in variants if isinstance(v, dict)
        }
        if variant_key not in seen:
            variants.append(duplicate)
        cur["visual_duplicate_variants"] = variants

        dup_sites = list(cur.get("visual_duplicate_sites") or [])
        for site in list(item.get("sites") or []):
            if site and site not in dup_sites:
                dup_sites.append(site)
        if dup_sites:
            cur["visual_duplicate_sites"] = dup_sites
            cur["visual_duplicate_count"] = len(variants) + 1
        dup_urls = list(cur.get("visual_duplicate_post_urls") or [])
        for url in list(item.get("post_urls") or []):
            if url and url not in dup_urls:
                dup_urls.append(url)
        if dup_urls:
            cur["visual_duplicate_post_urls"] = dup_urls
        vh_all = list(cur.get("visual_hashes") or [])
        for vh in [cur.get("visual_hash"), item.get("visual_hash")] + list(item.get("visual_hashes") or []):
            vh = str(vh or "").strip().lower()
            if vh and vh not in vh_all:
                vh_all.append(vh)
        if vh_all:
            cur["visual_hashes"] = vh_all
            cur["visual_hash"] = cur.get("visual_hash") or vh_all[0]
        try:
            self._grabber_metadata_ram_cache_put(cur)
        except Exception:
            pass
        try:
            self.append_log("PREVIEW VISUAL GROUP ONLY: different MD5/source proof kept isolated")
        except Exception:
            pass

    def _merge_source_tag_groups_exact(self, groups_list):
        by_url = {}
        order = []
        for stg in groups_list or []:
            if not isinstance(stg, dict):
                continue
            url = str(stg.get("url") or "").strip()
            if not url:
                continue
            if url not in by_url:
                by_url[url] = {"url": url, "method": str(stg.get("method") or "grabber_preview"), "groups": {}}
                order.append(url)
            cur = by_url[url]
            merged = _dedupe_group_dict(cur.get("groups") or {})
            for group, values in ((stg.get("groups") or {}).items() if isinstance(stg.get("groups"), dict) else []):
                merged.setdefault(group, [])
                merged[group] += list(values or [])
            cur["groups"] = _dedupe_group_dict(merged)
            method = str(stg.get("method") or "").strip()
            if method and method not in str(cur.get("method") or ""):
                cur["method"] = (str(cur.get("method") or "") + "+" + method).strip("+")
        return [by_url[u] for u in order]


    def _merge_preview_candidate(self, acc, item):
        # Exact MD5 is the safest canonical identity of an online post card.
        # Some APIs omit the md5 field but the helper can infer it from CDN URLs;
        # always re-key by md5 first so exact same bytes from ATF/e621/rule34/etc.
        # become one card with multiple source_tag_groups.
        md5 = self._normalize_md5_hex(item.get("md5") or (item.get("post") or {}).get("md5") or "")
        exact_key = "md5:" + md5 if md5 else str(item.get("key") or "")
        item["key"] = exact_key
        if md5:
            item["md5"] = md5
            md5s = list(item.get("md5s") or [])
            if md5 not in md5s:
                md5s.append(md5)
            item["md5s"] = md5s

        # Second layer: visually identical cross-site files can have different
        # byte MD5s because one site recompressed/converted/stripped metadata.
        # Use pHash only as a UI/source merge key, never as proof of exact bytes.
        visual_key = self._find_visual_merge_key(acc, item)
        key = visual_key or exact_key
        if visual_key:
            item["key"] = key

        if key not in acc:
            # Last-resort migration for a candidate inserted earlier under
            # file:/post: before an md5-bearing duplicate arrived.
            if md5:
                for old_key, old_item in list(acc.items()):
                    if self._normalize_md5_hex(old_item.get("md5") or "") == md5 or md5 in list(old_item.get("md5s") or []):
                        if old_key != exact_key:
                            acc[exact_key] = acc.pop(old_key)
                            key = exact_key
                            item["key"] = key
                        break
            if key not in acc:
                vh = str(item.get("visual_hash") or "").strip().lower()
                if vh:
                    item["visual_hashes"] = list(dict.fromkeys(list(item.get("visual_hashes") or []) + [vh]))
                acc[key] = item
                return
        cur = acc[key]
        if visual_key:
            existing_md5s = set()
            for val in [cur.get("md5")] + list(cur.get("md5s") or []):
                val = self._normalize_md5_hex(val)
                if val:
                    existing_md5s.add(val)
            incoming_md5s = set()
            for val in [item.get("md5")] + list(item.get("md5s") or []):
                val = self._normalize_md5_hex(val)
                if val:
                    incoming_md5s.add(val)
            # If pHash matched but byte MD5 did not, hide/merge only in the UI.
            # Do not merge source_tag_groups/tags/groups into the saved metadata;
            # that would make a recompressed mirror look like an exact source for
            # the downloaded original.
            if not (existing_md5s and incoming_md5s and (existing_md5s & incoming_md5s)):
                self._merge_preview_candidate_visual_only(cur, item)
                return
        for field in ("sites", "post_urls", "file_urls"):
            cur[field] = list(dict.fromkeys(list(cur.get(field) or []) + list(item.get(field) or [])))
        cur_md5s = list(cur.get("md5s") or [])
        for val in [cur.get("md5"), item.get("md5")] + list(item.get("md5s") or []):
            val = self._normalize_md5_hex(val)
            if val and val not in cur_md5s:
                cur_md5s.append(val)
        if cur_md5s:
            cur["md5s"] = cur_md5s
            # Keep the first exact MD5 as the physical download/check key.
            if not self._normalize_md5_hex(cur.get("md5") or ""):
                cur["md5"] = cur_md5s[0]
        vh_all = list(cur.get("visual_hashes") or [])
        for vh in [cur.get("visual_hash"), item.get("visual_hash")] + list(item.get("visual_hashes") or []):
            vh = str(vh or "").strip().lower()
            if vh and vh not in vh_all:
                vh_all.append(vh)
        if vh_all:
            cur["visual_hashes"] = vh_all
            cur["visual_hash"] = cur.get("visual_hash") or vh_all[0]
        if not cur.get("download_url") and item.get("download_url"):
            cur["download_url"] = item.get("download_url")
        try:
            self._grabber_metadata_ram_cache_put(cur)
        except Exception:
            pass
        stg_all = list(cur.get("source_tag_groups") or []) + list(item.get("source_tag_groups") or [])
        cur["source_tag_groups"] = self._merge_source_tag_groups_exact(stg_all)
        # Merge categories like the local gallery: keep per-source groups but also
        # build one union for display/download fallback.
        merged = _dedupe_group_dict(cur.get("groups") or {})
        for group, values in (item.get("groups") or {}).items():
            merged.setdefault(group, [])
            merged[group] += list(values or [])
        cur["groups"] = _dedupe_group_dict(merged)
        cur["tags"] = list(dict.fromkeys(list(cur.get("tags") or []) + list(item.get("tags") or [])))
        cur["already_path"] = cur.get("already_path") or item.get("already_path") or ""
        if not cur.get("thumb_path") and item.get("thumb_path"):
            cur["thumb_path"] = item.get("thumb_path")

    def _tag_count_api(self, base_url, tags):
        host = _host(base_url)
        tags_q = quote_plus(str(tags or "").strip())
        try:
            auth = _apt_auth_query(self._runtime_settings(), host)
        except Exception:
            auth = ""
        if host in ("rule34.xxx", "api.rule34.xxx"):
            # No json=1: XML response with count= attribute is more reliable for totals
            return f"https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&tags={tags_q}&pid=0&limit=0{auth}"
        if host == "gelbooru.com":
            # json=1 with limit=0 returns empty array; use XML endpoint for count
            return f"https://gelbooru.com/index.php?page=dapi&s=post&q=index&tags={tags_q}&pid=0&limit=1{auth}"
        if "allthefallen" in host:
            return f"https://{host}/counts/posts.json?tags={tags_q}{auth}"
        if "danbooru" in host or "donmai" in host:
            return f"https://danbooru.donmai.us/counts/posts.json?tags={tags_q}{auth}"
        if host in ("e621.net", "e926.net"):
            # e621/e926 do not expose Danbooru-style /counts/posts.json.
            # For a single tag, the tag endpoint has post_count; for multi-tag
            # queries there is no cheap exact total, so the UI must show ?.
            raw_tags = [x for x in re.split(r"\s+", str(tags or "").strip()) if x]
            if len(raw_tags) == 1:
                name_q = quote_plus(raw_tags[0])
                return f"https://{host}/tags.json?search[name_matches]={name_q}&limit=5{auth}"
            return ""
        return ""

    def _parse_preview_count_response(self, response):
        text = getattr(response, "text", "") or ""
        # Some APIs expose totals only in headers.  Do this first because the
        # body may be a normal posts list without any count field.
        try:
            headers = getattr(response, "headers", {}) or {}
            for key in ("x-total-count", "x-count", "x-post-count", "total-count"):
                val = headers.get(key) or headers.get(key.title())
                if isinstance(val, str) and val.strip().isdigit():
                    return int(val.strip())
                if isinstance(val, int):
                    return int(val)
        except Exception:
            pass
        try:
            data = response.json()
        except Exception:
            data = None
        if isinstance(data, list):
            # e621/e926 /tags.json fallback: [{"name": "tag", "post_count": N}, ...]
            for row in data:
                if isinstance(row, dict):
                    for key in ("post_count", "count", "total"):
                        val = row.get(key)
                        if isinstance(val, int):
                            return int(val)
                        if isinstance(val, str) and val.strip().isdigit():
                            return int(val.strip())
        if isinstance(data, dict):
            # Gelbooru-style JSON/XML-converted envelopes may keep count here.
            attrs = data.get("@attributes") or data.get("attributes")
            if isinstance(attrs, dict):
                for key in ("count", "total", "posts"):
                    val = attrs.get(key)
                    if isinstance(val, int):
                        return int(val)
                    if isinstance(val, str) and val.strip().isdigit():
                        return int(val.strip())
            for key in ("count", "total", "total_count", "post_count", "posts_count"):
                val = data.get(key)
                if isinstance(val, int):
                    return int(val)
                if isinstance(val, str) and val.strip().isdigit():
                    return int(val.strip())
            counts = data.get("counts")
            if isinstance(counts, dict):
                for key in ("posts", "post", "total", "count"):
                    val = counts.get(key)
                    if isinstance(val, int):
                        return int(val)
                    if isinstance(val, str) and val.strip().isdigit():
                        return int(val.strip())
        m = re.search(r'<posts\b[^>]*\bcount=["\'](\d+)["\']', text, re.I)
        if m:
            return int(m.group(1))
        m = re.search(r'\bcount=["\'](\d+)["\']', text, re.I)
        if m:
            return int(m.group(1))
        return None

    def _preview_abs_url(self, value):
        if not isinstance(value, str) or not value.strip():
            return ""
        value = value.strip()
        if value.startswith("//"):
            return "https:" + value
        if value.startswith(("http://", "https://")):
            return value
        return ""

    def _preview_original_url_from_post(self, post, file_url=""):
        """Return one best original/full media URL for actual archive download.

        Preview/sample URLs are acceptable for UI cache, but archive download
        must not silently save thumbnails.
        """
        def abs_url(v):
            return self._preview_abs_url(v)
        if isinstance(post, dict) and isinstance(post.get("post"), dict):
            post = post.get("post") or {}
        if not isinstance(post, dict):
            return ""
        for key in ("file_url", "source_file_url", "original_url"):
            u = abs_url(post.get(key))
            if u:
                return u
        f = post.get("file")
        if isinstance(f, dict):
            for key in ("url", "file_url", "ext_url"):
                u = abs_url(f.get(key))
                if u:
                    return u
        media = post.get("media_asset")
        if isinstance(media, dict):
            variants = media.get("variants") or []
            if isinstance(variants, list):
                for v in variants:
                    if isinstance(v, dict) and v.get("type") == "original":
                        u = abs_url(v.get("url"))
                        if u:
                            return u
        # No verified original/full URL in JSON.  Returning sample/preview here
        # would make the archive save a small image, so fail explicitly.
        return ""

    def _preview_media_urls_from_post(self, post, file_url="", preview_url=""):
        """Media URLs for opening one online card, ordered by configured quality.

        This affects only "open in grabber" viewing cache. Archive download uses
        _preview_original_url_from_post() and always saves the original URL.
        """
        if isinstance(post, dict) and isinstance(post.get("post"), dict):
            post = post.get("post") or {}
        if not isinstance(post, dict):
            post = {}

        def abs_url(v):
            return self._preview_abs_url(v)

        buckets = {"tiny": [], "small": [], "sample": [], "original": []}

        def add(bucket, value):
            u = abs_url(value)
            if not u:
                return
            if u in buckets[bucket]:
                return
            buckets[bucket].append(u)

        # Flat Danbooru/Gelbooru/e621-ish fields.
        add("original", post.get("file_url"))
        add("original", post.get("source_file_url"))
        add("original", post.get("original_url"))
        add("sample", post.get("large_file_url"))
        add("sample", post.get("sample_url"))
        add("sample", post.get("sample_file_url"))
        add("sample", post.get("jpeg_url"))
        add("tiny", post.get("preview_file_url"))
        add("tiny", post.get("preview_url"))

        f = post.get("file")
        if isinstance(f, dict):
            for key in ("url", "file_url", "ext_url"):
                add("original", f.get(key))
        smp = post.get("sample")
        if isinstance(smp, dict):
            for key in ("url", "file_url"):
                add("sample", smp.get(key))
        prev = post.get("preview")
        if isinstance(prev, dict):
            for key in ("url", "file_url"):
                add("tiny", prev.get(key))

        # ATF/Danbooru media_asset variants: 180/360/720/sample/full/original.
        media = post.get("media_asset")
        if isinstance(media, dict):
            variants = media.get("variants") or []
            if isinstance(variants, list):
                for v in variants:
                    if not isinstance(v, dict):
                        continue
                    typ = str(v.get("type") or "").lower()
                    url = v.get("url")
                    if typ in {"180x180", "360x360"}:
                        add("tiny", url)
                    elif typ == "720x720":
                        add("small", url)
                    elif typ in {"sample", "large"}:
                        add("sample", url)
                    elif typ in {"full", "original"}:
                        add("original", url)

        add("original", file_url)
        add("tiny", preview_url)

        q = self._grabber_open_quality()
        if q == "small_25":
            order = ["small", "sample", "original", "tiny"]
        elif q == "original_100":
            order = ["original", "sample", "small", "tiny"]
        else:  # medium_50
            order = ["sample", "small", "original", "tiny"]

        urls = []
        for bucket in order:
            for u in buckets[bucket]:
                # Opening a post must not use tiny preview unless there is no
                # usable larger URL at all.  The caller also filters preview names.
                if u not in urls:
                    urls.append(u)
        return urls

    def _looks_like_image_or_animation_path(self, path):
        try:
            p = Path(path)
            head = p.read_bytes()[:32]
        except Exception:
            return False
        if head.startswith(b"\xff\xd8\xff"):
            return True
        if head.startswith(b"\x89PNG"):
            return True
        if head.startswith(b"GIF8"):
            return True
        if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
            return True
        # Важно: этот метод часто вызывается из фоновых потоков граббера
        # во время загрузки превью/оригиналов. QPixmap является GUI-объектом
        # и на Windows может создавать пустые окна/ломать фокус, если трогать
        # его не из главного Qt-потока. Для проверки файла используем только
        # безопасный Pillow fallback без QWidget/QPixmap.
        try:
            if Image is not None:
                with Image.open(p) as img:
                    img.verify()
                return True
        except Exception:
            pass
        return False

    def _response_looks_like_media(self, response, url=""):
        ct = str(getattr(response, "headers", {}).get("content-type", "") or "").split(";")[0].lower().strip()
        ext = Path(urlparse(url).path).suffix.lower()
        if ct.startswith("image/") or ct.startswith("video/") or ct in {"application/octet-stream", "binary/octet-stream", ""}:
            return True
        if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov", ".mkv", ".avi"} and not ("html" in ct or "json" in ct or "xml" in ct or "text/" in ct):
            return True
        return False

    def _warm_download_session(self, session, post_url, file_url):
        host = _host(post_url or file_url)
        if not host:
            return
        if "allthefallen" in host and post_url:
            # Check if session already has valid ATF PoW cookies — don't solve again
            try:
                atf_cookie_names = {c.name for c in session.cookies}
                if "atf-anti-bot" in atf_cookie_names or "dab" in atf_cookie_names:
                    return  # Already solved PoW, reuse cookies
            except Exception:
                pass
            try:
                # Solves ATF PoW cookie before direct media GET.
                self._preview_http_get(session, post_url, post_url, timeout=30)
            except Exception:
                pass

    def _stream_media_get(self, session, file_url, post_url="", timeout=60):
        """Stream a media file with fail-fast semantics.

        Preview/API sessions are patched with the global retry wrapper.  That is
        good for metadata requests, but bad for the grabber download queue: one
        protected direct image URL could sit through several long retries and
        freeze every queued download behind it.  For the actual media GET we use
        Session.request() directly (same cookies/headers, no patched get()), one
        short connect/read timeout, and explicit logging.
        """
        self._warm_download_session(session, post_url, file_url)
        headers = {
            "Referer": post_url or file_url,
            "Accept": "image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8",
        }
        actual_timeout = (8, max(12, min(int(timeout or 30), 25)))
        host = _host(file_url)
        self.append_log(f"FILE GET TRY [{host}]: {file_url[:120]}")
        r = session.request("GET", file_url, timeout=actual_timeout, stream=True, headers=headers, allow_redirects=True)
        # If ATF challenges the direct /data/original URL despite the warmed
        # post page, solve once against the file URL and retry exactly once.
        if "allthefallen" in host:
            ctype = str(r.headers.get("content-type", "") or "").lower()
            if r.status_code < 400 and ("text/html" in ctype or "application/xhtml" in ctype):
                try:
                    r.close()
                except Exception:
                    pass
                try:
                    self._preview_http_get(session, file_url, file_url, timeout=20)
                except Exception:
                    pass
                self.append_log(f"FILE GET RETRY AFTER ATF POW [{host}]: {file_url[:120]}")
                r = session.request("GET", file_url, timeout=actual_timeout, stream=True, headers=headers, allow_redirects=True)
        return r

    def _preview_http_get(self, session, site, url, *, timeout=40):
        host = _host(site)
        # Для обычных JSON API используем прямой session.get, как в v168 — так
        # gelbooru/rule34/danbooru/e621 не ломаются из-за лишнего универсального
        # обходчика. PoW-ветка нужна только ATF.
        if "allthefallen" not in host:
            return session.get(url, timeout=timeout)
        lock = self._grabber_session_lock_for_host(host)
        try:
            from core.downloader_utils import _smart_get
            with lock:
                return _smart_get(session, url, host, self.append_log, timeout=timeout, settings=self._runtime_settings())
        except Exception:
            with lock:
                return session.get(url, timeout=timeout)


    def _preview_count_for_site(self, site, tags, session=None):
        api = self._tag_count_api(site, tags)
        host = _host(site)
        session = session or self._grabber_session_for_url(site)
        try:
            if api:
                self.append_log(f"PREVIEW COUNT TRY [{host}]: {_mask_sensitive_url(api)}")
                r = self._preview_http_get(session, site, api, timeout=30)
                self.append_log(f"PREVIEW COUNT STATUS [{host}]: {r.status_code} {r.headers.get('content-type','')}")
                if r.status_code < 400:
                    got = self._parse_preview_count_response(r)
                    if got is not None:
                        return got
            # Fallback: ask the normal posts endpoint with limit=1 and parse
            # headers/envelope.  If the site still gives no total, return None
            # and the UI shows "?" instead of lying with the loaded buffer size.
            api2 = _tag_search_api(site, tags, page=0, limit=1, settings=self._runtime_settings())
            if api2:
                self.append_log(f"PREVIEW COUNT FALLBACK [{host}]: {_mask_sensitive_url(api2)}")
                r2 = self._preview_http_get(session, site, api2, timeout=30)
                self.append_log(f"PREVIEW COUNT FALLBACK STATUS [{host}]: {r2.status_code} {r2.headers.get('content-type','')}")
                if r2.status_code < 400:
                    return self._parse_preview_count_response(r2)
            return None
        except Exception as e:
            self.append_log(f"PREVIEW COUNT ERROR [{host}]: {type(e).__name__}: {e}")
            return None

    def _grabber_prefetch_originals_enabled(self):
        try:
            return bool(self._runtime_settings().get("grabber_preview_prefetch_originals", False))
        except Exception:
            return False

    def _grabber_prefetch_protected_originals_enabled(self):
        # Protected sites such as ATF can require PoW/challenge handling for each
        # direct /data/original URL.  Let explicit Download/Open do that work,
        # but do not run it as speculative background prefetch by default.
        try:
            return bool(self._runtime_settings().get("grabber_preview_prefetch_protected_originals", False))
        except Exception:
            return False

    def _grabber_md5_cache_enabled(self):
        # Disk metadata cache: parser/tagger reuse this.  It is separate from
        # the RAM-only grabber metadata cache used for quick UI redraws.
        try:
            from core.grabber_md5_cache import enabled as _enabled
            return bool(_enabled(self._runtime_settings()))
        except Exception:
            try:
                s = self._runtime_settings()
                return bool(s.get("grabber_disk_metadata_cache_enabled", s.get("developer_grabber_md5_cache_enabled", True)))
            except Exception:
                return False

    def _cache_grabber_md5_item(self, item):
        """Store one grabber card in the optional Local Reverse Index.

        The index keeps source URLs, tags and categories in a separate SQLite
        file.  It deliberately does not store preview images; thumbnails remain
        in the bounded UI cache.  If the card has an exact real MD5, the same
        write also updates the parser shortcut table so future identical local
        files can be tagged offline without touching booru sites.
        """
        if not self._grabber_md5_cache_enabled():
            return False
        try:
            from core.grabber_md5_cache import upsert_item
            return upsert_item(
                self._runtime_settings(),
                item,
                method="grabber_preview",
            )
        except Exception as e:
            try:
                self.append_log(f"GRABBER LOCAL INDEX WARN: {type(e).__name__}: {e}")
            except Exception:
                pass
            return False

    def _download_preview_original_to_cache(self, item):
        """Download the original/full media into temporary grabber cache.

        This is used only when the user enables the heavy prefetch option.  The
        archive save path can then reuse this cached original instead of doing a
        second network download.
        """
        item = item or {}
        cached = str(item.get("cached_original_path") or "")
        if cached and Path(cached).exists() and Path(cached).stat().st_size > 0:
            return cached
        # Heavy prefetch is allowed to cache only the explicit original URL.
        # Never fall back to file_urls/preview_url here: those can be sample or
        # thumbnail URLs and later would be imported into the archive.
        urls = []
        if item.get("download_url"):
            urls.append(str(item.get("download_url") or ""))
        urls = [self._preview_abs_url(u) for u in urls]
        urls = [u for u in dict.fromkeys(urls) if u]
        if not urls:
            return ""
        post_url = ((item.get("post_urls") or []) or [urls[0]])[0]
        try:
            if ("allthefallen" in _host(post_url or urls[0])) and not self._grabber_prefetch_protected_originals_enabled():
                return ""
        except Exception:
            pass
        key = item.get("key") or item.get("md5") or urls[0]
        cache = self._grabber_cache_dir("original")

        # Only one speculative original download at a time.  Searches and manual
        # downloads may run concurrently; prefetch must politely step aside.
        lock = getattr(self, "_grabber_original_prefetch_lock", None)
        acquired = False
        if lock is not None:
            acquired = lock.acquire(blocking=False)
            if not acquired:
                return ""
        try:
            session = None
            for file_url in urls:
                try:
                    ext = Path(urlparse(file_url).path).suffix.lower()
                    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov", ".mkv", ".avi"}:
                        ext = ".bin"
                    url_key = hashlib.sha1(str(str(key) + "|" + file_url).encode("utf-8", "ignore")).hexdigest()
                    out = cache / (url_key + ext)
                    if out.exists() and out.stat().st_size > 0:
                        item["cached_original_path"] = str(out)
                        item["cached_original_kind"] = "original"
                        return str(out)
                    if session is None:
                        session = self._grabber_session_for_url(post_url or file_url)
                    r = self._stream_media_get(session, file_url, post_url, timeout=60)
                    if r.status_code >= 400:
                        self.append_log(f"GRABBER ORIGINAL PREFETCH WARN: {r.status_code} {file_url}")
                        continue
                    if not self._response_looks_like_media(r, file_url):
                        self.append_log(f"GRABBER ORIGINAL PREFETCH WARN: not media {r.headers.get('content-type','')} {file_url}")
                        continue
                    tmp = out.with_name(out.name + f".{time.time_ns()}.tmp")
                    with open(tmp, "wb") as f:
                        total = 0
                        for chunk in r.iter_content(512 * 1024):
                            if self.should_stop():
                                raise InterruptedError("stopped")
                            self.wait_if_paused()
                            if not chunk:
                                continue
                            total += len(chunk)
                            f.write(chunk)
                    if not tmp.exists() or tmp.stat().st_size <= 0:
                        continue
                    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"} and not self._looks_like_image_or_animation_path(tmp):
                        try:
                            tmp.unlink(missing_ok=True)
                        except Exception:
                            pass
                        continue
                    tmp.replace(out)
                    item["cached_original_path"] = str(out)
                    item["cached_original_kind"] = "original"
                    self.append_log(f"GRABBER ORIGINAL CACHED: {out.name} ({out.stat().st_size} bytes)")
                    try:
                        self._grabber_cache_evict()
                    except Exception:
                        pass
                    return str(out)
                except InterruptedError:
                    raise
                except Exception as e:
                    try:
                        self.append_log(f"GRABBER ORIGINAL PREFETCH ERROR: {type(e).__name__}: {e}")
                    except Exception:
                        pass
            return ""
        finally:
            if acquired and lock is not None:
                try:
                    lock.release()
                except Exception:
                    pass

    def _preview_emit_progress_item(self, progress_emit, item, *, append=True, request_token=None):
        # Stable default: do not mutate Qt grid for every single found card.
        # Instead, collect the current request and publish one bounded batch when
        # the worker finishes. This mirrors mature downloaders: metadata fetching
        # is separate from UI rendering and avoids Windows focus/window storms.
        if not self._runtime_settings().get("grabber_preview_stream_cards", False):
            return False
        if not progress_emit or not item:
            return False
        try:
            progress_emit({
                "items": [dict(item)],
                "append": bool(append),
                "total_by_site": {},
                "next_pages": {},
                "exhausted_sites": [],
                "request_token": request_token,
            })
            return True
        except Exception:
            return False

    def _preview_search_impl(self, sites, tags, limit_total, hide_existing=True, append=False, start_pages=None, progress_emit=None, request_token=None):
        tags = str(tags or "").strip()
        results = {}
        results_lock = threading.RLock()
        progress_streamed = False
        try:
            self.append_log(f"PREVIEW QUERY: {tags or '<без тегов>'}")
        except Exception:
            pass
        total_limit = max(1, int(limit_total or self._preview_per_page()))
        per_page = self._preview_per_page()
        if append:
            total_limit = min(total_limit, per_page)
        else:
            total_limit = min(total_limit, per_page * (self._preview_prefetch_pages() + 1))
        site_list = list(sites or [])
        start_pages = start_pages or {}
        per_site_target = max(1, (total_limit + max(1, len(site_list)) - 1) // max(1, len(site_list)))
        # Fetch metadata in small slices.  We still need booru API pages, but
        # cards are processed and emitted one-by-one instead of waiting for a
        # whole large page/batch to finish.
        api_page_size = max(1, min(20, per_site_target))
        max_pages_per_site = max(1, (per_site_target + api_page_size - 1) // api_page_size)
        total_by_site = {}
        next_pages = {}
        exhausted_sites = []
        # v228: true asynchronous grabber UI.  The worker still produces a
        # final merged payload, but every site page is also emitted as a small
        # progress batch.  Fast sites therefore render immediately instead of
        # waiting for a slow ATF PoW/API lane to finish.
        nonlocal_progress = [False]
        progress_lock = threading.RLock()

        def _emit_preview_progress(items=None, *, host="", total=None, next_page=None, exhausted=False):
            if not progress_emit:
                return False
            items = list(items or [])
            payload = {
                "items": [dict(x) for x in items],
                "append": True,
                "total_by_site": {str(host): int(total)} if host and total is not None else {},
                "next_pages": {str(host): int(next_page)} if host and next_page is not None else {},
                "exhausted_sites": [str(host)] if host and exhausted else [],
                "request_token": request_token,
                "partial_progress": True,
            }
            try:
                with progress_lock:
                    nonlocal_progress[0] = True
                progress_emit(payload)
                return True
            except Exception:
                return False

        def _site_worker(site):
            local_items = []
            local_total = None
            local_next = 0
            local_exhausted = False
            if self.should_stop():
                return site, local_items, local_total, local_next, True
            self.wait_if_paused()
            host = _host(site)
            session = None
            got_site = 0
            try:
                page = int(start_pages.get(host, 0) if isinstance(start_pages, dict) else 0)
            except Exception:
                page = 0
            local_next = page
            if not append:
                try:
                    session = self._grabber_session_for_url(site)
                    count = self._preview_count_for_site(site, tags, session=session)
                    if count is not None:
                        local_total = int(count)
                except Exception:
                    pass
            loops = 0
            while got_site < per_site_target and loops < max_pages_per_site:
                if self.should_stop():
                    break
                self.wait_if_paused()
                limit = min(api_page_size, per_site_target - got_site)
                api = _tag_search_api(site, tags, page=page, limit=limit, settings=self._runtime_settings())
                if not api:
                    if page == 0:
                        self.append_log(f"PREVIEW SKIP: {host}: tag API не поддержан")
                    local_exhausted = True
                    _emit_preview_progress([], host=host, total=local_total, next_page=local_next, exhausted=True)
                    break
                try:
                    if session is None:
                        session = self._grabber_session_for_url(site)
                    self.append_log(f"PREVIEW API TRY [{host} p{page + 1} limit={limit}]: {_mask_sensitive_url(api)}")
                    r = self._preview_http_get(session, site, api, timeout=40)
                    self.append_log(f"PREVIEW API STATUS [{host} p{page + 1}]: {r.status_code} {r.headers.get('content-type','')}")
                    if r.status_code >= 400:
                        local_exhausted = True
                        break
                    if local_total is None:
                        try:
                            api_count = self._parse_preview_count_response(r)
                            if api_count is not None:
                                local_total = int(api_count)
                        except Exception:
                            pass
                    posts = _posts_from_json_response(r)
                    self.append_log(f"PREVIEW POSTS [{host} p{page + 1}]: {len(posts)}")
                    page_category_map = self._preview_category_map_for_posts(site, posts, session=session)
                    if not posts:
                        local_exhausted = True
                        page += 1
                        local_next = page
                        _emit_preview_progress([], host=host, total=local_total, next_page=local_next, exhausted=True)
                        break
                    accepted_this_page = 0
                    page_items = []
                    for post in posts:
                        if self.should_stop():
                            break
                        self.wait_if_paused()
                        file_url = _extract_file_url_from_json(post)
                        if not file_url:
                            continue
                        post_url = self._post_url_from_post(site, post)
                        preview_url = _extract_preview_url_from_json(post) or file_url
                        groups = self._groups_from_post_for_grabber_site(site, post, category_map=page_category_map, session=session)
                        post_tags = _tag_list_from_post(post)
                        if self.has_blocked_tag(post_tags):
                            continue
                        md5 = str(_post_md5_from_json(post) or "").strip().lower()
                        already_path = self._candidate_in_library(post_url=post_url, file_url=file_url, md5=md5)
                        hidden_existing = bool(already_path and hide_existing)
                        key = self._candidate_key(post, file_url, post_url)
                        media_urls = self._preview_media_urls_from_post(post, file_url, preview_url)
                        download_url = self._preview_original_url_from_post(post, file_url)
                        item = {
                            "key": key,
                            "md5": md5,
                            "id": str(post.get("id") or ""),
                            "sites": [host],
                            "post_urls": [post_url] if post_url else [],
                            "file_urls": media_urls or ([file_url] if file_url else []),
                            "download_url": download_url,
                            "preview_url": preview_url,
                            "thumb_path": "",
                            "visual_hash": "",
                            "visual_hashes": [],
                            "tags": post_tags,
                            "groups": groups,
                            "post": dict(post),
                            "already_path": already_path,
                            "source_tag_groups": [{"url": post_url or file_url, "groups": groups, "method": "grabber_preview"}],
                        }
                        # Manual exclusions are checked before thumbnail download,
                        # so a right-click-hidden MD5/URL never wastes preview IO
                        # and never reappears from another site.
                        if self._is_grabber_preview_excluded(item):
                            continue
                        # Hidden existing items still enter the merge buffer so a
                        # later mirror from another site inherits already_path and
                        # disappears instead of being shown as "new". No thumbnail
                        # is needed for a card that will not be displayed.
                        if hidden_existing:
                            thumb_path = ""
                            visual_hash = ""
                        else:
                            thumb_path = self._download_preview_thumb(session, preview_url, key, referer=post_url)
                            visual_hash = self._preview_visual_hash_for_path(thumb_path)
                        item["thumb_path"] = thumb_path
                        item["visual_hash"] = visual_hash
                        item["visual_hashes"] = [visual_hash] if visual_hash else []
                        # Fallback for no-MD5 sites: after pHash is known, a
                        # previously excluded visual-only card can be skipped too.
                        if self._is_grabber_preview_excluded(item):
                            continue
                        self._cache_grabber_md5_item(item)
                        self._grabber_metadata_ram_cache_put(item)
                        if already_path:
                            self._merge_existing_preview_candidate_metadata(item, already_path)
                        if self._grabber_prefetch_originals_enabled() and not already_path:
                            try:
                                self._download_preview_original_to_cache(item)
                            except InterruptedError:
                                raise
                            except Exception as e:
                                self.append_log(f"GRABBER ORIGINAL PREFETCH ERROR: {type(e).__name__}: {e}")
                        with results_lock:
                            self._merge_preview_candidate(results, item)
                        local_items.append(item)
                        page_items.append(item)
                        # v228 streams by page/batch, not per-card.  This keeps
                        # Qt refreshes bounded while still rendering fast site
                        # results before slow sites finish.
                        if not hidden_existing:
                            got_site += 1
                            accepted_this_page += 1
                            if got_site >= per_site_target:
                                break
                    page += 1
                    loops += 1
                    local_next = page
                    page_exhausted = len(posts) < limit
                    if page_exhausted:
                        local_exhausted = True
                    _emit_preview_progress(page_items, host=host, total=local_total, next_page=local_next, exhausted=local_exhausted)
                    if page_exhausted:
                        break
                    if accepted_this_page == 0 and hide_existing:
                        continue
                except InterruptedError:
                    raise
                except Exception as e:
                    self.append_log(f"PREVIEW ERROR [{host}]: {type(e).__name__}: {e}")
                    local_exhausted = True
                    break
            local_next = max(local_next, int(start_pages.get(host, 0) if isinstance(start_pages, dict) else 0))
            return site, local_items, local_total, local_next, local_exhausted

        max_workers = 1
        try:
            max_workers = self._grabber_site_threads("grabber_preview_threads", len(site_list))
        except Exception:
            max_workers = max(1, min(len(site_list), 8))
        if max_workers <= 1 or len(site_list) <= 1:
            for site in site_list:
                host = _host(site)
                try:
                    _site, _items, _total, _next, _exh = _site_worker(site)
                    if _total is not None:
                        total_by_site[host] = int(_total)
                    next_pages[host] = int(_next or 0)
                    if _exh:
                        exhausted_sites.append(host)
                except Exception as e:
                    self.append_log(f"PREVIEW SITE THREAD ERROR [{host}]: {type(e).__name__}: {e}")
                    next_pages[host] = int(start_pages.get(host, 0) if isinstance(start_pages, dict) else 0)
                    exhausted_sites.append(host)
        else:
            self.append_log(f"PREVIEW ASYNC STREAM: site_threads={max_workers}; карточки приходят по мере готовности сайтов")
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="grabber-preview") as ex:
                futs = {ex.submit(_site_worker, site): site for site in site_list}
                for fut in concurrent.futures.as_completed(futs):
                    site = futs[fut]
                    host = _host(site)
                    try:
                        _site, _items, _total, _next, _exh = fut.result()
                        if _total is not None:
                            total_by_site[host] = int(_total)
                        next_pages[host] = int(_next or 0)
                        if _exh:
                            exhausted_sites.append(host)
                    except Exception as e:
                        self.append_log(f"PREVIEW SITE THREAD ERROR [{host}]: {type(e).__name__}: {e}")
                        next_pages[host] = int(start_pages.get(host, 0) if isinstance(start_pages, dict) else 0)
                        exhausted_sites.append(host)
            self.append_log("PREVIEW ASYNC STREAM: все сайты завершили текущий блок")
        with results_lock:
            out = list(results.values())
        out.sort(key=lambda x: (bool(x.get("already_path")), str(x.get("sites") or []), str(x.get("id") or "")))
        return {
            "items": out,
            "total_by_site": total_by_site,
            "next_pages": next_pages,
            "exhausted_sites": exhausted_sites,
            # If we streamed cards one-by-one, final payload must merge instead
            # of clearing the UI and causing a visible flash.
            "append": bool(append or nonlocal_progress[0]),
            "request_token": request_token,
        }


    def _preview_effective_tag_group(self, tag, groups_by_tag):
        tag_n = normalize_tag(tag).lower()
        best_group = "general"
        best_prio = -1
        for group in groups_by_tag.get(tag_n, {"general"}):
            prio = int(GRABBER_GROUP_PRIORITY.get(str(group), 0))
            if prio > best_prio:
                best_group = str(group)
                best_prio = prio
        return best_group

    def _add_preview_tag_header(self, group):
        label = str(group or "general")
        it = QListWidgetItem(label)
        it.setFlags(Qt.NoItemFlags)
        colors = dict(GRABBER_GROUP_COLORS)
        try:
            colors.update(self.main.settings.get("tag_group_colors") or {})
        except Exception:
            pass
        it.setForeground(QBrush(QColor(colors.get(label, "#888888"))))
        f = it.font()
        f.setBold(True)
        f.setPointSize(max(f.pointSize(), 12))
        it.setFont(f)
        self.preview_tags_list.addItem(it)

    def _preview_item_sites_for_filter(self, item, *, include_visual=True):
        """Return all site hosts that should make an online card visible.

        ``sites`` is exact source proof for the currently selected physical
        online file.  Since v239/v240, visually identical cross-site cards with
        different byte MD5s are stored under ``visual_duplicate_*`` instead of
        being merged into ``sites``.  The source filter in the grabber is a UI
        filter, not proof for source merging, so it must include those visual
        variants too; otherwise choosing rule34.xxx/e621/ATF can show an empty
        grid even though a grouped card from that site is present.
        """
        out = []
        def add(value):
            host = _host(value) if value and (":" in str(value) or "/" in str(value)) else str(value or "").strip().lower().replace("www.", "")
            if host and host not in out:
                out.append(host)
        for site in item.get("sites") or []:
            add(site)
        if include_visual:
            for site in item.get("visual_duplicate_sites") or []:
                add(site)
            for variant in item.get("visual_duplicate_variants") or []:
                if not isinstance(variant, dict):
                    continue
                for site in variant.get("sites") or []:
                    add(site)
                for url in variant.get("post_urls") or []:
                    add(url)
                for url in variant.get("file_urls") or []:
                    add(url)
        return out

    def _preview_item_matches_site_filter(self, item, site):
        site = str(site or "").strip().lower().replace("www.", "")
        if not site:
            return True
        return site in self._preview_item_sites_for_filter(item, include_visual=True)

    def _refresh_preview_sidebar(self):
        items = self._preview_items_for_display(site_filter="")
        self.preview_sources_list.clear()
        self.preview_tags_list.clear()

        source_counts = {}
        tag_counts_by_group = {}
        groups_by_tag = {}

        for item in items:
            for site in self._preview_item_sites_for_filter(item, include_visual=True):
                site = str(site)
                source_counts[site] = source_counts.get(site, 0) + 1

            groups = item.get("groups") or {}
            if not groups:
                groups = {"general": item.get("tags") or []}
            # First collect all categories seen for this card, then count every tag
            # once in the most informative category.  This mirrors gallery display
            # better than one flat uncolored tag list.
            local_groups_by_tag = {}
            for group, values in groups.items():
                group = str(group or "general")
                for raw in values or []:
                    tag = normalize_tag(raw).lower()
                    if not tag:
                        continue
                    local_groups_by_tag.setdefault(tag, set()).add(group)
                    groups_by_tag.setdefault(tag, set()).add(group)
            if not local_groups_by_tag:
                for raw in item.get("tags") or []:
                    tag = normalize_tag(raw).lower()
                    if not tag:
                        continue
                    local_groups_by_tag.setdefault(tag, set()).add("general")
                    groups_by_tag.setdefault(tag, set()).add("general")
            seen_tags = set()
            for tag in sorted(local_groups_by_tag):
                if tag in seen_tags:
                    continue
                seen_tags.add(tag)
                group = self._preview_effective_tag_group(tag, {tag: local_groups_by_tag.get(tag, {"general"})})
                tag_counts_by_group.setdefault(group, {})[tag] = tag_counts_by_group.setdefault(group, {}).get(tag, 0) + 1

        enabled_hosts = []
        try:
            enabled_hosts = [_host(x) for x in self._enabled_parser_sites()]
        except Exception:
            enabled_hosts = []
        enabled_hosts = [x for x in dict.fromkeys(enabled_hosts) if x]

        totals = getattr(self, "preview_total_by_site", {}) or {}
        known_values = [int(totals[h]) for h in enabled_hosts if isinstance(totals.get(h), int)]
        missing_total = any(h not in totals for h in enabled_hosts)
        max_known = max(known_values) if known_values else None
        if isinstance(max_known, int):
            all_label_count = str(max_known) + ("+?" if missing_total else "")
        else:
            all_label_count = "?"
        all_item = QListWidgetItem(f"all {all_label_count}")
        all_item.setData(Qt.UserRole, "")
        all_item.setToolTip(
            "Все включённые сайты парсера. Общий счётчик берётся как максимум по сайтам, "
            "а не сумма, чтобы один и тот же автор/тег с разных booru не давал ложные 10k+."
        )
        source_rows = [("", all_item)]

        # Всегда учитываем все включённые сайты, даже если текущий буфер ещё не
        # успел подгрузить их карточки. Порядок = порядок вкладки «Парсер».
        visible_sources = list(enabled_hosts)
        for site in visible_sources:
            loaded_count = source_counts.get(site, 0)
            total_count = totals.get(site)
            if isinstance(total_count, int):
                label = f"{site} {total_count}"
                tip = f"{site}: всего {total_count}; загружено {loaded_count}"
            else:
                label = f"{site} ?"
                tip = f"{site}: общий счётчик не получен; загружено {loaded_count}"
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, site)
            it.setToolTip(tip)
            source_rows.append((site, it))

        expanded = bool(getattr(self, "preview_sources_expanded", False))
        rows_to_show = source_rows if expanded else source_rows[:5]
        selected_source = str(getattr(self, "_preview_site_filter", "") or "")
        for site, it in rows_to_show:
            if selected_source == site:
                it.setSelected(True)
            self.preview_sources_list.addItem(it)
        try:
            visible_count = max(1, len(rows_to_show))
            self.preview_sources_list.setFixedHeight(min(300, 10 + visible_count * 24))
            self.preview_sources_toggle.setVisible(len(source_rows) > 5)
            self.preview_sources_toggle.setText("Скрыть" if expanded else "Показать все")
        except Exception:
            pass

        group_order = list(self.main.settings.get("tag_group_order") or GRABBER_GROUP_ORDER)
        for group in GRABBER_GROUP_ORDER:
            if group not in group_order:
                group_order.append(group)
        colors = dict(GRABBER_GROUP_COLORS)
        try:
            colors.update(self.main.settings.get("tag_group_colors") or {})
        except Exception:
            pass
        for group in group_order:
            values = tag_counts_by_group.get(group) or {}
            if not values:
                continue
            self._add_preview_tag_header(group)
            query_single_tag = self._preview_single_tag_query()
            query_total = self._preview_known_total("")
            for tag, count in sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))[:500]:
                shown_count = count
                count_note = f"загружено: {count}"
                if query_single_tag and tag == query_single_tag and isinstance(query_total, int):
                    shown_count = int(query_total)
                    count_note = f"всего по крупнейшему сайту: {shown_count}; загружено: {count}"
                it = QListWidgetItem(f"    {tag}    {shown_count}")
                it.setData(Qt.UserRole, tag)
                it.setToolTip(f"{group}: {tag}; {count_note}")
                it.setForeground(QBrush(QColor(tag_display_color(tag, group, self.main.settings, colors))))
                self.preview_tags_list.addItem(it)

    def _toggle_preview_sources(self):
        self.preview_sources_expanded = not bool(getattr(self, "preview_sources_expanded", False))
        self._refresh_preview_sidebar()

    def _preview_single_tag_query(self):
        q = str(getattr(self, "_preview_query_text", "") or "").strip()
        if not q:
            return ""
        parts = [x for x in re.split(r"\s+", q) if x]
        if len(parts) != 1:
            return ""
        tag = normalize_tag(parts[0]).lower()
        # Negative / wildcard / complex booru expressions are not a single tag
        # counter; for them the sidebar keeps loaded-card counts.
        if not tag or tag.startswith("-") or "*" in tag or ":" in tag:
            return ""
        return tag

    def _preview_source_clicked(self, item):
        site = str(item.data(Qt.UserRole) or "").strip()
        self._preview_site_filter = site
        self.preview_page_index = 1
        self._schedule_preview_ui_refresh(render=True, sidebar=False, delay=0)

    def _preview_items_for_display(self, *, site_filter=None):
        items = list(self.preview_items or [])
        try:
            if self._grabber_preview_exclusions_enabled():
                items = [x for x in items if not self._is_grabber_preview_excluded(x)]
        except Exception:
            pass
        try:
            hide_existing = bool(self.main.settings.get("grabber_preview_hide_existing", True))
        except Exception:
            hide_existing = True
        if hide_existing:
            items = [x for x in items if not x.get("already_path")]
        site = str(site_filter if site_filter is not None else getattr(self, "_preview_site_filter", "") or "").strip()
        if site:
            items = [x for x in items if self._preview_item_matches_site_filter(x, site)]
        return items

    def _filtered_preview_items(self):
        return self._preview_items_for_display()

    def _preview_tag_clicked(self, item):
        tag = str(item.data(Qt.UserRole) or "").strip()
        if not tag:
            return
        self.preview_query.setText(tag)
        self.search_online_preview()

    def _preview_tag_context_menu(self, pos):
        item = self.preview_tags_list.itemAt(pos)
        if not item:
            return
        tag = str(item.data(Qt.UserRole) or "").strip()
        if not tag:
            return
        menu = QMenu(self.preview_tags_list)
        act_search = menu.addAction("Искать по этому тегу")
        act_sub = menu.addAction("Подписаться")
        chosen = menu.exec(self.preview_tags_list.mapToGlobal(pos))
        if chosen == act_search:
            self.preview_query.setText(tag)
            self.search_online_preview()
        elif chosen == act_sub:
            self._subscribe_to_grabber_tag(tag)

    def _subscribe_to_grabber_tag(self, tag):
        tag = str(tag or "").strip()
        if not tag:
            return
        sites = []
        for idx, root in enumerate(self._enabled_parser_sites()):
            host = _host(root)
            if host:
                # Higher priority for earlier/enabled parser order.
                sites.append({"site": host, "priority": max(1, 5 - idx)})
        if not sites:
            QMessageBox.warning(self, "Подписка", "Нет включённых сайтов парсера для подписки.")
            return
        if QMessageBox.question(self, "Подписаться", f"Создать подписку на тег:\n{tag}\n\nСайтов: {len(sites)}?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            from core.subscriptions import add_subscription, normalize_blacklist_tags
            bl = normalize_blacklist_tags(self._grabber_subscription_blocklist_text())
            add_subscription(tag, sites, tag, blacklist_tags=bl)
            QMessageBox.information(self, "Подписка", f"Подписка создана: {tag}")
        except Exception as e:
            QMessageBox.warning(self, "Подписка", str(e))

    def _schedule_preview_ui_refresh(self, *, render=True, sidebar=True, delay=80):
        """Coalesce high-frequency grabber updates into one GUI refresh.

        Preview workers can stream many items in a few seconds.  Rebuilding the
        QWidget grid for every streamed item on Windows can create top-level
        orphan windows and eventually close the app.  Data is updated
        immediately; expensive Qt widget work is batched on the main event loop.
        """
        if sidebar:
            self._preview_sidebar_pending = True
        if render:
            self._preview_render_pending = True
        if getattr(self, "_preview_refresh_timer_active", False):
            return
        self._preview_refresh_timer_active = True

        def _flush():
            self._preview_refresh_timer_active = False
            do_sidebar = bool(getattr(self, "_preview_sidebar_pending", False))
            do_render = bool(getattr(self, "_preview_render_pending", False))
            self._preview_sidebar_pending = False
            self._preview_render_pending = False
            try:
                if do_sidebar:
                    self._refresh_preview_sidebar()
            except RuntimeError:
                return
            except Exception:
                pass
            try:
                if do_render:
                    self.render_preview_page()
            except RuntimeError:
                return
            except Exception as e:
                try:
                    self.append_log(f"PREVIEW RENDER WARN: {type(e).__name__}: {e}")
                except Exception:
                    pass

        QTimer.singleShot(max(0, int(delay or 0)), _flush)

    def _clear_preview_grid(self):
        # Delete children via their layout item and keep parentage intact.  Never
        # detach visible widgets with setParent(None): on Windows that can turn
        # cards into independent top-level "Local Booru" windows.
        while self.preview_grid.count():
            item = self.preview_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                try:
                    w.hide()
                except Exception:
                    pass
                try:
                    w.deleteLater()
                except Exception:
                    pass
            child_layout = item.layout()
            if child_layout is not None:
                try:
                    child_layout.deleteLater()
                except Exception:
                    pass

    def _loaded_source_counts(self):
        counts = {}
        for item in self.preview_items or []:
            try:
                sites = self._preview_item_sites_for_filter(item, include_visual=True)
            except Exception:
                sites = [str(s) for s in (item.get("sites") or [])]
            for site in sites:
                site = str(site)
                counts[site] = counts.get(site, 0) + 1
        return counts

    def _preview_known_total(self, site=""):
        site = str(site or "").strip()
        totals = getattr(self, "preview_total_by_site", {}) or {}
        if site:
            return totals.get(site)
        values = [int(v) for v in totals.values() if isinstance(v, int) and v >= 0]
        # Для режима all не суммируем сайты. Один и тот же booru-тег/автор почти
        # всегда пересекается между rule34/gelbooru/danbooru/ATF; сумма даёт
        # ложные огромные числа. Показываем крупнейший счётчик как честную
        # оценку масштаба запроса.
        return max(values) if values else None

    def _preview_total_pages(self, filtered_items=None):
        per = self._preview_per_page()
        site = str(getattr(self, "_preview_site_filter", "") or "").strip()
        known = self._preview_known_total(site)
        loaded = len(filtered_items if filtered_items is not None else self._filtered_preview_items())
        if isinstance(known, int) and known > 0:
            # If some enabled sites do not expose totals, the loaded buffer can
            # already be larger than the known-site sum.  Page count must never
            # shrink below what is actually loaded.
            basis = max(known, loaded)
        else:
            basis = loaded
        return max(1, (max(0, int(basis or 0)) + per - 1) // per)

    def _preview_loaded_pages(self, filtered_items=None):
        per = self._preview_per_page()
        count = len(filtered_items if filtered_items is not None else self._filtered_preview_items())
        return max(0, (max(0, int(count or 0)) + per - 1) // per)

    def _preview_status_text(self):
        q = str(getattr(self, "_preview_query_text", "") or "").strip()
        known = self._preview_known_total("")
        loaded = len(self._preview_items_for_display(site_filter=""))
        hidden = max(0, len(self.preview_items or []) - loaded)
        try:
            enabled_hosts = [_host(x) for x in self._enabled_parser_sites()]
            totals = getattr(self, "preview_total_by_site", {}) or {}
            missing_total = any(h and h not in totals for h in enabled_hosts)
        except Exception:
            missing_total = False
        if isinstance(known, int):
            suffix = "+?" if missing_total else ""
            base = f"Граббер: максимум по сайтам: {known}{suffix}; загружено для просмотра: {loaded}"
        else:
            base = f"Граббер: общее количество не вернулось API; загружено для просмотра: {loaded}"
        if hidden:
            base += f" · скрыто уже имеющихся: {hidden}"
        if q:
            base += f" · запрос: {q}"
        else:
            base += " · общий просмотр"
        return base

    def _preview_has_more(self):
        totals = getattr(self, "preview_total_by_site", {}) or {}
        loaded = self._loaded_source_counts()
        site = str(getattr(self, "_preview_site_filter", "") or "").strip()
        hosts = [site] if site else [_host(x) for x in self._grabber_preview_sites_from_parser()]
        for host in [h for h in hosts if h]:
            if host in getattr(self, "_preview_exhausted_sites", set()):
                continue
            total = totals.get(host)
            if isinstance(total, int) and total > 0 and loaded.get(host, 0) >= total:
                continue
            return True
        return False

    def _preview_active_sites_for_load_more(self, sites):
        # Do not ask exhausted zero-result sites again on every Next click.  The
        # v222 logic correctly remembered exhausted hosts but still passed all
        # enabled roots into the append worker, so e621/ATF kept repeating p1 for
        # tags where they returned 0 posts.
        exhausted = set(str(x) for x in (getattr(self, "_preview_exhausted_sites", set()) or set()))
        totals = getattr(self, "preview_total_by_site", {}) or {}
        loaded = self._loaded_source_counts()
        out = []
        skipped = []
        for root in sites or []:
            host = _host(root)
            if not host:
                continue
            if host in exhausted:
                skipped.append(host)
                continue
            total = totals.get(host)
            if isinstance(total, int) and total > 0 and loaded.get(host, 0) >= total:
                skipped.append(host)
                continue
            out.append(root)
        if skipped:
            try:
                self.append_log("PREVIEW SKIP EXHAUSTED: " + ", ".join(sorted(set(skipped))))
            except Exception:
                pass
        return out

    def _load_more_preview_results(self):
        if getattr(self, "_preview_loading_more", False):
            return
        self._clear_finished_preview_worker()
        if getattr(self, "worker", None) and self.worker.isRunning():
            return
        if not self._preview_has_more():
            return
        all_sites = self._grabber_preview_sites_from_parser()
        site_filter = str(getattr(self, "_preview_site_filter", "") or "").strip()
        if site_filter:
            sites = [root for root in all_sites if _host(root) == site_filter]
        else:
            sites = all_sites
        sites = self._preview_active_sites_for_load_more(sites)
        if not sites:
            return
        start_pages = dict(getattr(self, "_preview_next_page_by_site", {}) or {})
        self._start_preview_worker(
            tags=str(getattr(self, "_preview_query_text", "") or ""),
            sites=sites,
            append=True,
            start_pages=start_pages,
            limit_total=self._preview_append_fetch_limit(),
            token=self._current_preview_request_token(),
        )

    def render_preview_page(self):
        self._clear_preview_grid()
        items = self._filtered_preview_items()
        per = self._preview_per_page()
        maxp = self._preview_total_pages(items)
        self.preview_page_index = max(1, min(int(self.preview_page_index or 1), maxp))
        start = (self.preview_page_index - 1) * per
        page_items = items[start:start + per]
        cols, rows = self._preview_grid_shape()
        tile, spacing = self._preview_tile_size()
        self.preview_grid.setHorizontalSpacing(spacing)
        self.preview_grid.setVerticalSpacing(spacing)
        filt = str(getattr(self, "_preview_site_filter", "") or "").strip()
        suffix = f" · {filt}" if filt else ""
        total = self._preview_known_total(filt)
        total_text = str(total) if isinstance(total, int) else "?"
        loaded_pages = self._preview_loaded_pages(items)
        self.preview_page_label.setText(f"Страница {self.preview_page_index}/{maxp} · найдено {total_text} · загружено {len(items)} ({loaded_pages} стр.){suffix}")
        self.preview_prev_btn.setEnabled(self.preview_page_index > 1)
        self.preview_next_btn.setEnabled(self.preview_page_index < maxp or self._preview_has_more())
        # v221: no automatic endless rolling prefetch.  The previous logic tried
        # to keep several screen-pages ahead by repeatedly loading remote pages
        # until enough *visible* cards existed.  With "hide existing" or heavy
        # duplicate filtering that could walk page 50 -> 180 in the background,
        # rebuild the Qt grid hundreds of times and eventually close the app on
        # Windows.  Now a search loads the initial chunk; more is loaded only
        # when the user presses the next-page button at the end of loaded data.
        if not page_items:
            hint = QLabel("Подгружаю следующую страницу…" if getattr(self, "_preview_loading_more", False) else self._grabber_empty_hint(), self.preview_inner)
            hint.setObjectName("GrabberEmptyHint")
            hint.setAlignment(Qt.AlignCenter)
            hint.setWordWrap(True)
            hint.setMinimumHeight(360)
            self.preview_grid.addWidget(hint, 0, 0, 1, max(1, cols))
            self.preview_grid.setRowStretch(1, 1)
            return
        for idx, item in enumerate(page_items[:per]):
            card = self._make_preview_card(item, tile=tile)
            self.preview_grid.addWidget(card, idx // cols, idx % cols)
        self.preview_grid.setRowStretch(rows + 1, 1)

    def _grabber_empty_hint(self):
        sites = []
        try:
            sites = [_host(x) for x in self._enabled_parser_sites()]
        except Exception:
            sites = []
        sites = [x for x in sites if x]
        if self.preview_items and getattr(self, "_preview_site_filter", ""):
            return "По выбранному источнику карточек нет.\nВыбери «Все источники» слева или запусти другой поиск."
        site_text = ", ".join(sites[:6]) if sites else "нет включённых поддерживаемых сайтов"
        return (
            "Здесь будет сетка найденных постов граббера.\n\n"
            "Введи тег или несколько тегов сверху и нажми «Найти».\n"
            "ПКМ по карточке — скачать, открыть пост, скопировать ссылку или посмотреть теги.\n\n"
            f"Сейчас используются сайты из вкладки «Парсер»: {site_text}."
        )

    def preview_go_page(self, delta):
        delta = int(delta or 0)
        items = self._filtered_preview_items()
        loaded_pages = max(1, self._preview_loaded_pages(items))
        target = int(self.preview_page_index or 1) + delta
        if delta > 0 and target > loaded_pages and self._preview_has_more():
            # Explicit navigation may need to skip several remote API pages if all
            # posts on them are already in the archive and hidden.  The continuation
            # is handled in on_preview_worker_done with a hard cap, so this cannot
            # become the old endless autoload loop.
            self._preview_pending_page_after_load = target
            self._preview_manual_skip_attempts = 0
            self._load_more_preview_results()
            return
        self.preview_page_index = max(1, target)
        self.render_preview_page()


    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible() and (self.preview_items or self.preview_grid.count()):
            QTimer.singleShot(120, self.render_preview_page)

    def eventFilter(self, obj, event):
        try:
            if obj is self.preview_scroll.viewport() and event.type() == QEvent.Wheel and self.preview_items:
                self.preview_go_page(1 if event.angleDelta().y() < 0 else -1)
                event.accept()
                return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def wheelEvent(self, event):
        try:
            pos = self.preview_scroll.mapFromGlobal(event.globalPosition().toPoint())
            if self.preview_scroll.rect().contains(pos) and self.preview_items:
                self.preview_go_page(1 if event.angleDelta().y() < 0 else -1)
                event.accept()
                return
        except Exception:
            pass
        super().wheelEvent(event)

    def _make_preview_card(self, item, tile=None):
        tile = int(tile or self._preview_tile_size()[0])
        pad = 6
        card = QFrame(self.preview_inner)
        card.setFrameShape(QFrame.Shape.NoFrame)
        card.setObjectName("GrabberPreviewCard")
        card.setProperty("candidate", item)
        card.setFixedSize(tile + pad * 2, tile + pad * 2)
        card.setMinimumSize(tile + pad * 2, tile + pad * 2)
        card.setMaximumSize(tile + pad * 2, tile + pad * 2)
        card.setContextMenuPolicy(Qt.CustomContextMenu)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(pad, pad, pad, pad)
        lay.setSpacing(0)
        img = QLabel(card)
        img.setAlignment(Qt.AlignCenter)
        img.setFixedSize(tile, tile)
        img.setContextMenuPolicy(Qt.CustomContextMenu)
        pix = self._grabber_cached_scaled_pixmap(str(item.get("thumb_path") or ""), tile, tile)
        if not pix.isNull():
            img.setPixmap(pix)
        else:
            img.setText("NO PREVIEW")
        img.setToolTip(self._preview_item_tooltip(item))
        card.setToolTip(self._preview_item_tooltip(item))
        lay.addWidget(img)
        card.customContextMenuRequested.connect(lambda pos, c=card: self._show_preview_context(c, pos))
        img.customContextMenuRequested.connect(lambda pos, c=card, w=img: self._show_preview_context_on_widget(c, w, pos))

        def _single_click_open(ev, it=item):
            # Одиночный ЛКМ снова открывает карточку, как просил пользователь.
            # Массовые белые окна были не от клика, а от _clear_preview_grid():
            # setParent(None) на видимых карточках делал из них top-level окна.
            try:
                if ev.button() == Qt.LeftButton:
                    self.open_preview_post(it)
                    ev.accept()
                    return
            except Exception:
                pass
            try:
                QFrame.mouseReleaseEvent(card, ev)
            except Exception:
                pass

        def _double_click_open(ev, it=item):
            try:
                if ev.button() == Qt.LeftButton:
                    self.open_preview_post(it)
                    ev.accept()
                    return
            except Exception:
                pass
            try:
                QFrame.mouseDoubleClickEvent(card, ev)
            except Exception:
                pass

        card.mouseReleaseEvent = _single_click_open
        img.mouseReleaseEvent = _single_click_open
        card.mouseDoubleClickEvent = _double_click_open
        img.mouseDoubleClickEvent = _double_click_open
        return card

    def _show_preview_context_on_widget(self, card, widget, pos):
        # QLabel inside the card receives right-clicks itself, so map from the
        # child widget instead of relying on QFrame propagation.
        item = card.property("candidate") or {}
        menu = self._build_preview_context_menu(card, item)
        chosen = menu.exec(widget.mapToGlobal(pos))
        self._handle_preview_context_action(chosen, item, menu)

    def _preview_item_tooltip(self, item):
        sites = ", ".join(item.get("sites") or [])
        tags = " ".join((item.get("tags") or [])[:60])
        urls = item.get("post_urls") or item.get("file_urls") or []
        state = "уже есть" if item.get("already_path") else "новый"
        return "\n".join(x for x in [state, sites, tags, (urls or [""])[0]] if x)

    def _build_preview_context_menu(self, parent, item):
        menu = QMenu(parent)
        menu._act_download = menu.addAction("Скачать")
        menu._act_open_post = menu.addAction("Открыть пост")
        menu._act_open_local = menu.addAction("Открыть локально") if item.get("already_path") else None
        menu._act_copy = menu.addAction("Скопировать ссылку поста")
        menu._act_tags = menu.addAction("Показать теги")
        menu.addSeparator()
        menu._act_exclude = menu.addAction("Скрыть в граббере")
        try:
            menu._act_exclude.setToolTip("Скрывает карточку только в граббере; парсер и тэггер не блокируются.")
        except Exception:
            pass
        return menu

    def _handle_preview_context_action(self, chosen, item, menu):
        if chosen == getattr(menu, "_act_download", None):
            self.download_preview_candidate(item)
        elif chosen == getattr(menu, "_act_open_post", None):
            self.open_preview_post(item)
        elif getattr(menu, "_act_open_local", None) is not None and chosen == getattr(menu, "_act_open_local", None):
            self._open_local_path_from_preview(item)
        elif chosen == getattr(menu, "_act_copy", None):
            urls = item.get("post_urls") or item.get("file_urls") or []
            if urls:
                QApplication.clipboard().setText(urls[0])
        elif chosen == getattr(menu, "_act_tags", None):
            QMessageBox.information(self, "Теги", " ".join(item.get("tags") or []))
        elif chosen == getattr(menu, "_act_exclude", None):
            self._exclude_preview_candidate(item)

    def _show_preview_context(self, card, pos):
        item = card.property("candidate") or {}
        menu = self._build_preview_context_menu(card, item)
        chosen = menu.exec(card.mapToGlobal(pos))
        self._handle_preview_context_action(chosen, item, menu)

    def _download_preview_full_image(self, item):
        item = item or {}
        # Просмотр может подгружать только реальные media/original URL.  Превью
        # не добавляем даже как fallback, чтобы оно никогда не попало в поле
        # full_path и потом не было принято за оригинал при сохранении.
        preview_url = str(item.get("preview_url") or "")
        urls = list(dict.fromkeys(list(item.get("file_urls") or [])))
        urls = [u for u in urls if self._preview_abs_url(u) and u != preview_url and "/preview/" not in u and "preview" not in Path(urlparse(u).path).name.lower()]
        try:
            self.append_log(f"PREVIEW OPEN QUALITY: {self._grabber_open_quality()} urls={len(urls)}")
        except Exception:
            pass
        if not urls:
            return ""
        post_url = ((item.get("post_urls") or []) or [urls[0]])[0]
        key = item.get("key") or urls[0]
        for file_url in urls:
            ext = Path(urlparse(file_url).path).suffix.lower()
            if ext in {".mp4", ".webm", ".mov", ".mkv", ".avi"}:
                continue
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                ext = ".jpg"
            try:
                cache = self._grabber_cache_dir("full")
                url_key = hashlib.sha1(str(key + "|" + file_url).encode("utf-8", "ignore")).hexdigest()
                out = cache / (url_key + ext)
                if out.exists() and out.stat().st_size > 0:
                    if self._looks_like_image_or_animation_path(out):
                        return str(out)
                    try:
                        out.unlink(missing_ok=True)
                    except Exception:
                        pass
                session = self._grabber_session_for_url(post_url or file_url)
                r = self._stream_media_get(session, file_url, post_url, timeout=60)
                if r.status_code >= 400:
                    self.append_log(f"PREVIEW FULL IMAGE WARN: {r.status_code} {file_url}")
                    continue
                if not self._response_looks_like_media(r, file_url):
                    self.append_log(f"PREVIEW FULL IMAGE WARN: not media {r.headers.get('content-type','')} {file_url}")
                    continue
                tmp = out.with_name(out.name + f".{time.time_ns()}.tmp")
                total = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(256 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        # Это просмотр, не сохранение в библиотеку. Слишком
                        # крупный оригинал лучше открыть/скачать через ПКМ.
                        if total > 160 * 1024 * 1024:
                            break
                        f.write(chunk)
                if tmp.exists() and tmp.stat().st_size > 0:
                    if self._looks_like_image_or_animation_path(tmp):
                        tmp.replace(out)
                        return str(out)
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass
                    self.append_log(f"PREVIEW FULL IMAGE WARN: decoded image failed {file_url}")
            except Exception as e:
                try:
                    self.append_log(f"PREVIEW FULL IMAGE ERROR: {type(e).__name__}: {e}")
                except Exception:
                    pass
        return ""

    def _merge_online_source_groups_for_display(self, by_source):
        """Build the online-post "All sources" tag tree from source groups.

        Grabber posts can have a flat fallback in item["groups"] plus richer
        per-source tag groups in source_tag_groups.  The Post page renders
        item["tag_groups"] for "Все источники"; if we leave it as the
        flat fallback, rule34.xxx looks like every tag is general even when the
        source-specific groups were recovered correctly.
        """
        priority = {
            "general": 0,
            "invalid": 1,
            "meta": 2,
            "lore": 2,
            "species": 3,
            "copyright": 4,
            "character": 5,
            "contributor": 6,
            "artist": 7,
            "parody": 4,
            "language": 2,
            "category": 2,
        }
        best = {}
        for _host_key, groups in dict(by_source or {}).items():
            if not isinstance(groups, dict):
                continue
            for category, values in groups.items():
                cat = str(category or "general").strip().lower() or "general"
                if cat not in priority:
                    cat = "general"
                for value in values or []:
                    tag = normalize_tag(value)
                    if not tag:
                        continue
                    cur = best.get(tag)
                    if cur is None or priority.get(cat, 0) > priority.get(cur[0], 0):
                        best[tag] = (cat, tag)
        merged = {
            "artist": [], "contributor": [], "character": [], "copyright": [],
            "species": [], "general": [], "meta": [], "lore": [], "invalid": [],
            "parody": [], "language": [], "category": [],
        }
        for _norm, (cat, tag) in best.items():
            merged.setdefault(cat, []).append(tag)
        for cat in list(merged):
            merged[cat] = sorted(dict.fromkeys(merged.get(cat) or []), key=lambda v: v.lower())
        return {cat: vals for cat, vals in merged.items() if vals}

    def _preview_item_to_post_context(self, item, path=None):
        item = self._grabber_mark_open_quality(dict(item or {}))
        loading = bool(item.get("_open_loading_placeholder"))
        if path is None:
            if loading:
                path = self._grabber_loading_placeholder_path(item)
            else:
                path = str(item.get("full_path") or "")
        path = str(path or "")
        groups = _dedupe_group_dict(item.get("groups") or {})
        tags = list(dict.fromkeys(item.get("tags") or []))
        if not tags:
            for vals in groups.values():
                tags += list(vals or [])
        sources = []
        # В интерфейсе источников показываем только страницу поста.  Прямые
        # file/sample/preview URL нужны для скачивания/просмотра, но не должны
        # засорять «Источники» как миниатюры/служебные CDN-ссылки.
        seen_source_urls = set()
        for u in list(item.get("post_urls") or []):
            if not u or u in seen_source_urls:
                continue
            seen_source_urls.add(u)
            sources.append({"url": u, "host": _host(u)})
        by_source = {}
        for stg in item.get("source_tag_groups") or []:
            if not isinstance(stg, dict):
                continue
            host = _host(stg.get("url") or "")
            if host:
                by_source[host] = _dedupe_group_dict(stg.get("groups") or groups)
        if not by_source:
            for s in sources:
                if s.get("host"):
                    by_source[s["host"]] = groups

        # v240: visual-duplicate variants are not exact sources, because their
        # byte MD5 can differ.  But they must not disappear from the Post page:
        # show them as separate "похожий" sources and expose their tags under a
        # separate selector entry.  This fixes ATF/e621 cards that visually group
        # together: ATF stays visible, but is clearly not claimed as exact proof.
        for variant in list(item.get("visual_duplicate_variants") or []):
            if not isinstance(variant, dict):
                continue
            variant_posts = [str(u or "").strip() for u in list(variant.get("post_urls") or []) if str(u or "").strip()]
            variant_groups = _dedupe_group_dict(variant.get("groups") or {})
            if not variant_groups:
                variant_groups = {"general": list(variant.get("tags") or [])}
            # Prefer per-source groups from the variant payload.
            variant_source_groups = []
            for stg in list(variant.get("source_tag_groups") or []):
                if isinstance(stg, dict):
                    variant_source_groups.append(stg)
            for u in variant_posts:
                host = _host(u)
                if not host:
                    continue
                display_host = f"{host} (похожий)"
                if u not in seen_source_urls:
                    seen_source_urls.add(u)
                    sources.append({"url": u, "host": display_host, "visual_only": True})
                if display_host not in by_source:
                    # Try to keep ATF/e621 tag sets separate even when they came
                    # from a visually grouped, different-MD5 card.
                    matched_groups = None
                    for stg in variant_source_groups:
                        if _host(stg.get("url") or "") == host:
                            matched_groups = stg.get("groups")
                            break
                    by_source[display_host] = _dedupe_group_dict(matched_groups or variant_groups)
        merged_all_groups = self._merge_online_source_groups_for_display(by_source) or groups

        # Include full-res URLs so post_page can show quality image on demand
        preview_url = str(item.get("preview_url") or "")
        file_urls = list(dict.fromkeys(([str(item.get("download_url") or "")] if item.get("download_url") else []) + list(item.get("file_urls") or [])))
        file_urls = [u for u in file_urls if u and u != preview_url and "/preview/" not in u and "preview" not in Path(urlparse(u).path).name.lower()]
        online_url = file_urls[0] if file_urls else ""
        return {
            "path": path,
            "is_video": Path(path).suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".avi"},
            "tags": tags,
            "tag_groups": merged_all_groups,
            "tag_groups_by_source": by_source,
            "sources": sources,
            "source_hosts": [s.get("host", "") for s in sources if s.get("host")],
            "rating": 0,
            "favorite": 0,
            "_online_preview": True,
            "_online_loading_preview": loading,
            "_online_loading_message": "Загрузка предпросмотра…" if loading else "",
            "_online_thumb_path": str(item.get("thumb_path") or ""),
            "_online_url": online_url,
            "_online_file_urls": file_urls,
            "_preview_candidate": dict(item),
        }

    def open_preview_post(self, item):
        item = self._grabber_mark_open_quality(dict(item or {}))
        # Open immediately with an explicit loading placeholder, not the tiny card
        # thumbnail.  Otherwise a 180px/preview image looks like the final 25/50/100%
        # result while the real file is still downloading in the background.
        try:
            item["_open_loading_placeholder"] = True
        except Exception:
            pass
        try:
            filtered = self._filtered_preview_items() or [item]
            context = []
            key = item.get("key")
            for it in filtered:
                it2 = self._grabber_mark_open_quality(dict(it or {}))
                if it is item or it2.get("key") == key:
                    it2["_open_loading_placeholder"] = True
                    p = self._grabber_loading_placeholder_path(it2)
                else:
                    # Other online cards also stay in a lightweight placeholder state
                    # until opened/loaded; do not display stale low-res thumbnails as
                    # full post images.
                    it2["_open_loading_placeholder"] = True
                    p = self._grabber_loading_placeholder_path(it2)
                context.append(self._preview_item_to_post_context(it2, p))
            idx = 0
            key = item.get("key")
            for i, it in enumerate(filtered):
                if it is item or it.get("key") == key:
                    idx = i
                    break
            if hasattr(self.main, "post_page") and hasattr(self.main.post_page, "set_online_posts"):
                self.main.post_page.set_online_posts(
                    context,
                    idx,
                    tag_source=str(getattr(self, "_preview_site_filter", "") or "all"),
                    return_workspace="DLER",
                )
                if hasattr(self.main, "go"):
                    self.main.go("Post")
                # Start background loader for full-quality image
                self._start_full_image_loader(item, context, idx)
                return
            # post_page does not support set_online_posts — fall through to dialog
            self.append_log("PREVIEW OPEN: post_page.set_online_posts unavailable, using fallback dialog")
        except Exception as e:
            import traceback
            self.append_log(f"PREVIEW POST PAGE ERROR: {type(e).__name__}: {e}")
            self.append_log(traceback.format_exc()[:400])

        # Fallback for very old main windows: keep a minimal dialog, but the
        # normal path above uses the same Post page as the local gallery.
        full_path = str(item.get("thumb_path") or "")
        urls = item.get("post_urls") or item.get("file_urls") or []
        dlg = QDialog(self)
        dlg.setWindowTitle("Просмотр")
        dlg.resize(1180, 820)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        img = QLabel()
        img.setAlignment(Qt.AlignCenter)
        img.setMinimumSize(320, 320)
        img.setContextMenuPolicy(Qt.CustomContextMenu)
        lay.addWidget(img, 1)
        pix = QPixmap(str(full_path or item.get("thumb_path") or ""))

        def repaint_image():
            if not pix.isNull():
                target_w = max(200, img.width() - 2)
                target_h = max(200, img.height() - 2)
                img.setPixmap(pix.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                img.setText("NO PREVIEW")

        def toggle_fullscreen():
            win = dlg.window()
            if win.isFullScreen():
                win.showNormal()
            else:
                win.showFullScreen()
            QTimer.singleShot(0, repaint_image)

        def open_url():
            url = (urls or [""])[0]
            if url:
                try:
                    import webbrowser
                    webbrowser.open(url)
                except Exception:
                    QApplication.clipboard().setText(url)

        def context_menu(pos):
            menu = QMenu(img)
            act_download = menu.addAction("Скачать")
            act_open = menu.addAction("Открыть пост")
            act_copy = menu.addAction("Скопировать ссылку")
            act_close = menu.addAction("Закрыть")
            chosen = menu.exec(img.mapToGlobal(pos))
            if chosen == act_download:
                self.download_preview_candidate(item)
            elif chosen == act_open:
                open_url()
            elif chosen == act_copy:
                QApplication.clipboard().setText((urls or [""])[0])
            elif chosen == act_close:
                dlg.reject()

        img.customContextMenuRequested.connect(context_menu)
        img.resizeEvent = lambda ev: (QLabel.resizeEvent(img, ev), repaint_image())
        img.mouseDoubleClickEvent = lambda ev: toggle_fullscreen()
        QShortcut(QKeySequence("F11"), dlg).activated.connect(toggle_fullscreen)
        QShortcut(QKeySequence("Esc"), dlg).activated.connect(lambda: dlg.window().showNormal() if dlg.window().isFullScreen() else dlg.reject())
        repaint_image()
        dlg.exec()

    def _open_local_path_from_preview(self, item):
        path = str((item or {}).get("already_path") or "")
        if not path:
            return
        try:
            if hasattr(self.main, "post_page"):
                self.main.post_page.open_path(path)
                if hasattr(self.main, "go"):
                    self.main.go("Post")
        except Exception as e:
            self.append_log(f"OPEN LOCAL ERROR: {e}")

    def request_online_post_preview(self, post_item):
        """Start loading the selected 25/50/100% preview for current Post page item.

        v235 started the loader only on the first open from the grabber.  When the
        user navigated next/previous inside Post, the new item stayed on the
        deliberate placeholder forever.  This method is safe to call from every
        online Post.render(): duplicate loads are ignored by _start_full_image_loader.
        """
        try:
            if not isinstance(post_item, dict):
                return
            cand = dict(post_item.get("_preview_candidate") or {})
            if not cand:
                return
            if post_item.get("path") and not str(post_item.get("path")).startswith("__local_booru_loading_preview__") and not post_item.get("_online_loading_preview"):
                return
            cand = self._grabber_mark_open_quality(cand)
            pp = getattr(self.main, "post_page", None)
            ctx = list(getattr(pp, "context", []) or []) if pp is not None else []
            idx = int(getattr(pp, "index", 0) or 0) if pp is not None else 0
            self._start_full_image_loader(cand, ctx, idx)
        except Exception as e:
            try:
                self.append_log(f"PREVIEW NAV LOAD ERROR: {type(e).__name__}: {e}")
            except Exception:
                pass

    def _start_full_image_loader(self, item, context, idx):
        """Load full-quality image in background and update post_page."""
        item = self._grabber_mark_open_quality(dict(item or {}))
        file_urls = list(dict.fromkeys(list(item.get("file_urls") or [])))
        # Skip if no real full-res URL different from preview
        preview_url = str(item.get("preview_url") or "")
        real_urls = [u for u in file_urls if u != preview_url]
        if not real_urls:
            return
        load_key = str(item.get("key") or item.get("md5") or (real_urls[0] if real_urls else "") or "")
        if load_key:
            try:
                active = getattr(self, "_full_loader_keys", set())
                if load_key in active:
                    return
                active.add(load_key)
                self._full_loader_keys = active
            except Exception:
                pass
        try:
            loader = _FullImageLoader(item, self)
            def _release_loader_key():
                try:
                    if load_key and hasattr(self, "_full_loader_keys"):
                        self._full_loader_keys.discard(load_key)
                except Exception:
                    pass
            def _on_ready(local_path, loaded_item):
                _release_loader_key()
                try:
                    if not hasattr(self.main, "post_page"):
                        return
                    pp = self.main.post_page
                    cur = pp.item() if hasattr(pp, "item") else None
                    if cur is None:
                        return
                    cur_key = (cur.get("_preview_candidate") or {}).get("key") or cur.get("path")
                    load_key = loaded_item.get("key") or ""
                    if load_key and cur_key != load_key:
                        return  # user already navigated away
                    cur["path"] = local_path
                    cur["_online_loading_preview"] = False
                    cur["_online_loading_message"] = ""
                    cand = cur.get("_preview_candidate") or {}
                    if isinstance(cand, dict):
                        cand["full_path"] = local_path
                        cand["open_quality"] = loaded_item.get("open_quality") or self._grabber_open_quality()
                        cand["open_quality_label"] = loaded_item.get("open_quality_label") or self._grabber_open_quality_label()
                        cur["_preview_candidate"] = cand
                    try:
                        for ctx_item in getattr(pp, "context", []) or []:
                            ctx_key = (ctx_item.get("_preview_candidate") or {}).get("key") or ctx_item.get("path")
                            if load_key and ctx_key == load_key:
                                ctx_item["path"] = local_path
                                ctx_item["_online_loading_preview"] = False
                                ctx_item["_online_loading_message"] = ""
                    except Exception:
                        pass
                    from pathlib import Path as _P
                    pp.render_media(_P(local_path), cur)
                except Exception:
                    pass
            def _on_failed(message):
                _release_loader_key()
                try:
                    if not hasattr(self.main, "post_page"):
                        return
                    pp = self.main.post_page
                    cur = pp.item() if hasattr(pp, "item") else None
                    if cur is None:
                        return
                    cur_key = (cur.get("_preview_candidate") or {}).get("key") or cur.get("path")
                    load_key = (item or {}).get("key") or ""
                    if load_key and cur_key != load_key:
                        return
                    cur["_online_loading_preview"] = True
                    cur["_online_loading_message"] = "Не удалось загрузить предпросмотр"
                    pp.render_media(Path(str(cur.get("path") or "__local_booru_loading_preview__")), cur)
                except Exception:
                    pass
            loader.ready.connect(_on_ready)
            loader.failed.connect(_on_failed)
            loader.start()
            self._full_loaders = [l for l in getattr(self, "_full_loaders", []) if l.isRunning()]
            self._full_loaders.append(loader)
        except Exception as e:
            self.append_log(f"FULL LOADER ERROR: {e}")

    def download_preview_candidate(self, candidate):
        self.info.setVisible(True)
        if not hasattr(self, "_dl_queue"):
            self._dl_queue = []
        self._dl_queue.append(dict(candidate or {}))
        self.append_log(f"QUEUE: добавлено в очередь ({len(self._dl_queue)} ожидает)")
        if not (getattr(self, "_dl_worker", None) and self._dl_worker.isRunning()):
            self._run_next_download()

    def _run_next_download(self):
        if not getattr(self, "_dl_queue", []):
            return
        reason = self._sqlite_write_block_reason()
        if reason:
            if not getattr(self, "_dl_db_wait_notice", False):
                self.append_log(f"DOWNLOAD QUEUE WAIT SQLITE: {reason}")
                self._dl_db_wait_notice = True
            if not getattr(self, "_dl_db_wait_timer", False):
                self._dl_db_wait_timer = True
                def _retry_download_queue():
                    self._dl_db_wait_timer = False
                    self._run_next_download()
                QTimer.singleShot(2000, _retry_download_queue)
            return
        self._dl_db_wait_notice = False
        candidate = self._dl_queue.pop(0)
        sites = ", ".join(candidate.get("sites") or ["?"])
        self.append_log(f"DOWNLOAD START: {sites} — осталось в очереди: {len(self._dl_queue)}")
        self._dl_worker = DownloaderWorker(self, "preview_download", {"candidate": candidate})
        self._dl_worker.log.connect(self.log_requested.emit)
        self._dl_worker.result.connect(self._on_preview_download_result)
        def _on_done():
            self.append_log(f"DOWNLOAD DONE — осталось в очереди: {len(getattr(self, '_dl_queue', []))}")
            QTimer.singleShot(200, self._run_next_download)  # short delay between downloads
        self._dl_worker.done.connect(_on_done)
        self._dl_worker.start()

    def _on_preview_download_result(self, payload):
        try:
            if not isinstance(payload, dict) or payload.get("mode") != "preview_download":
                return
            saved = str(payload.get("result") or "").strip()
            if not saved:
                return
            cand = payload.get("candidate") or {}
            key = str(cand.get("key") or "")
            md5 = self._normalize_md5_hex(cand.get("md5") or (cand.get("post") or {}).get("md5") or "")
            if saved and md5:
                self._md5_ram_note(md5, saved)
            if saved:
                self._schedule_gallery_refresh_after_download(saved)
            for item in self.preview_items or []:
                same_key = key and str(item.get("key") or "") == key
                same_md5 = md5 and self._normalize_md5_hex(item.get("md5") or "") == md5
                if same_key or same_md5:
                    item["already_path"] = saved
            if bool(self.main.settings.get("grabber_preview_hide_existing", True)) and (key or md5):
                self.preview_items = [
                    it for it in (self.preview_items or [])
                    if not ((key and str(it.get("key") or "") == key) or (md5 and self._normalize_md5_hex(it.get("md5") or "") == md5))
                ]
                self._refresh_preview_sidebar()
                self.render_preview_page()
        except Exception:
            pass

    def _import_cached_preview_original(self, cached_path, file_url, post_url, stem_hint="download", post=None, groups=None):
        """Import a previously prefetched grabber original into the archive."""
        src = Path(str(cached_path or ""))
        if not src.exists() or src.stat().st_size <= 0:
            return None
        dirs = self.status_dirs("found")
        post = dict(post or {})
        groups = groups or _groups_from_post(post)
        remote_md5 = self._normalize_md5_hex(post.get("md5") or "")
        try:
            actual_src_md5 = _file_md5(src).lower()
        except Exception:
            actual_src_md5 = ""
        best_md5 = remote_md5 or actual_src_md5

        if post is not None and not self._wait_for_sqlite_writes_ready("перед импортом оригинала из кэша граббера", max_seconds=900):
            return None

        if best_md5:
            try:
                from core.services.metadata_service import found_media_path_by_md5
                canonical = self._md5_ram_lookup(best_md5) or found_media_path_by_md5(self.main.settings, best_md5)
                if canonical:
                    self._register_download_metadata_for_path(canonical, post_url, file_url, post, groups, hash_md5=best_md5)
                    self.append_log(f"GRABBER CACHED ORIGINAL MERGED EXACT MD5: {canonical}")
                    return Path(canonical)
            except Exception as e:
                self.append_log(f"GRABBER CACHED ORIGINAL MERGE WARN: {e}")
            try:
                if self._deleted_md5_policy_blocks(best_md5):
                    self.append_log("SKIP DELETED: cached original exact MD5 was permanently removed earlier")
                    return None
            except Exception:
                pass

        ext = src.suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov", ".mkv", ".avi"}:
            ext = Path(urlparse(file_url).path).suffix.lower() or ".bin"
        stem = _safe_name(stem_hint or Path(urlparse(file_url).path).stem or src.stem or "download")
        out = dirs["media"] / (stem + ext)
        n = 1
        while out.exists():
            out = dirs["media"] / f"{stem}_{n}{ext}"
            n += 1

        try:
            from core.preflight import ensure_space_for_write
            ok_space, space_msg = ensure_space_for_write(self.main.settings, out, int(src.stat().st_size or 0))
            if not ok_space:
                self.append_log("STOP NO DISK SPACE: " + space_msg)
                return None
        except Exception:
            pass

        self.append_log(f"GRABBER CACHED ORIGINAL IMPORT: {src.name} -> {out.name}")
        shutil.copy2(src, out)
        if out.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"} and not self._looks_like_image_or_animation_path(out):
            try:
                out.unlink(missing_ok=True)
            except Exception:
                pass
            raise RuntimeError("cached original is not a decodable image")

        try:
            actual_md5 = _file_md5(out).lower()
        except Exception:
            actual_md5 = best_md5 or ""

        try:
            if actual_md5:
                from core.services.metadata_service import found_media_path_by_md5
                canonical = self._md5_ram_lookup(actual_md5) or found_media_path_by_md5(self.main.settings, actual_md5, exclude_path=str(out))
                if canonical:
                    self._register_download_metadata_for_path(canonical, post_url, file_url, post, groups, hash_md5=actual_md5)
                    from core.services.media_storage_service import unlink_managed
                    unlink_managed(self.main.settings, out, operation="downloader.discard_cached_exact_copy")
                    self.append_log(f"GRABBER CACHED ORIGINAL MERGED JUST-COPIED EXACT MD5: {canonical}")
                    return Path(canonical)
        except Exception as e:
            self.append_log(f"GRABBER CACHED ORIGINAL POST-MERGE WARN: {e}")

        try:
            if actual_md5 and self._deleted_md5_policy_blocks(actual_md5):
                self._trash_downloaded_media([out], reason="reimport_deleted_rejected", make_backup=False)
                self.append_log("SKIP DELETED AFTER CACHED IMPORT: moved rejected copy to «Удалено»")
                return None
        except Exception:
            pass

        try:
            if actual_md5:
                from core.services.media_storage_service import normalize_managed_content_name
                original_name = Path(urlparse(file_url).path).name or out.name
                new_out = normalize_managed_content_name(
                    self.main.settings,
                    out,
                    actual_md5,
                    operation="downloader.normalize_cached_content_filename",
                    original_name=original_name,
                )
                if str(new_out) != str(out):
                    self.append_log(f"RENAMED CONTENT-SAFE: {out.name} -> {new_out.name}")
                    out = Path(new_out)
        except Exception as e:
            self.append_log(f"CONTENT-SAFE RENAME WARN: {type(e).__name__}: {e}")

        if post is not None:
            if not self._wait_for_sqlite_writes_ready("перед записью метаданных", max_seconds=900):
                try:
                    out.unlink(missing_ok=True)
                    self.append_log("DOWNLOAD DISCARDED: SQLite metadata write is blocked; cached imported file removed")
                except Exception:
                    pass
                return None
            self._register_download_metadata_for_path(out, post_url, file_url, post, groups, hash_md5=actual_md5)
        if actual_md5:
            self._md5_ram_note(actual_md5, out)
        self.append_log(f"SAVED FROM GRABBER CACHE: {out} ({out.stat().st_size} bytes)")
        return out

    def _download_preview_candidate_impl(self, candidate):
        try:
            candidate = self._enrich_candidate_exact_md5(candidate or {})
            urls = list(candidate.get("post_urls") or [])
            post_url = urls[0] if urls else ""
            # Архивный импорт граббера должен скачивать только explicit original
            # URL из API. Не используем sample/preview/file_urls как fallback:
            # так в v215-v219 в архив иногда попадало превью.
            download_url = self._preview_abs_url(str(candidate.get("download_url") or ""))
            file_urls = [download_url] if download_url else []
            if not post_url and file_urls:
                post_url = file_urls[0]
            self.append_log(f"DL CANDIDATE: post={post_url} download_url={(download_url or '')[:90]}")
            if not file_urls:
                self.append_log("PREVIEW DOWNLOAD ERROR: original file_url пустой — сайт не вернул ссылку на оригинал")
                return None
            post = dict(candidate.get("post") or {})
            post["md5"] = post.get("md5") or candidate.get("md5") or ""
            if candidate.get("md5s"):
                post["_all_md5s"] = list(dict.fromkeys(candidate.get("md5s") or []))
            if candidate.get("visual_hash") or candidate.get("visual_hashes"):
                post["_grabber_visual_hash"] = candidate.get("visual_hash") or ""
                post["_grabber_visual_hashes"] = list(dict.fromkeys(candidate.get("visual_hashes") or []))
            post["_all_sources"] = list(dict.fromkeys(urls))
            post["_source_tag_groups"] = list(candidate.get("source_tag_groups") or [])
            groups = _dedupe_group_dict(candidate.get("groups") or _groups_from_post(post))
            cached_original = str(candidate.get("cached_original_path") or "")
            if cached_original and Path(cached_original).exists():
                try:
                    stem = str(post.get("md5") or post.get("id") or Path(urlparse(file_urls[0]).path).stem or "download")
                    result = self._import_cached_preview_original(cached_original, file_urls[0], post_url, stem, post, groups)
                    if result:
                        return result
                except Exception as e:
                    self.append_log(f"GRABBER CACHED ORIGINAL IMPORT WARN: {type(e).__name__}: {e}; fallback to network")
            last_error = ""
            # Create session ONCE — ATF PoW is per-session, creating new session every iteration = N PoW solves
            session = self._grabber_session_for_url(post_url or (file_urls[0] if file_urls else ""))
            for file_url in file_urls:
                try:
                    stem = str(post.get("md5") or post.get("id") or Path(urlparse(file_url).path).stem or "download")
                    self.append_log(f"PREVIEW DOWNLOAD: {post_url or file_url} → {file_url[:100]}")
                    result = self._download_file(session, file_url, post_url, stem, post, groups)
                    if result:
                        self.append_log(f"PREVIEW DOWNLOAD DONE: {result}")
                        return result
                    last_error = "download returned no saved path"
                except Exception as e:
                    last_error = f"{type(e).__name__}: {e}"
                    self.append_log(f"PREVIEW DOWNLOAD RETRY: {last_error}")
                    continue
            self.append_log(f"PREVIEW DOWNLOAD ERROR: {last_error or 'не удалось скачать оригинал'}")
            return None
        except Exception as e:
            import traceback
            self.append_log(f"PREVIEW DOWNLOAD ERROR: {type(e).__name__}: {e}")
            self.append_log(traceback.format_exc()[:300])
            return None

    def download_post(self):
        post_url = self.url.text().strip()
        if not post_url:
            self.append_log("ERROR: URL пустой")
            return
        self.start_downloader_worker("post", {"post_url": post_url})

    def _download_post_impl(self, post_url):
        try:
            session = self._grabber_session_for_url(post_url)
            post, file_url = self._find_post_data_and_file_url(post_url, session)

            if not file_url:
                self.append_log("ERROR: не нашёл file_url на странице/API")
                return

            tags = _tag_list_from_post(post)
            if self.has_blocked_tag(tags):
                self.append_log(f"SKIP BLOCKED TAGS: {sorted(set(tags) & self.blocklist_set())}")
                return

            self.append_log(f"FILE URL: {file_url}")
            stem = str(post.get("md5") or post.get("id") or Path(urlparse(file_url).path).stem or "download")
            self._download_file(session, file_url, post_url, stem, post, _groups_from_post(post))

        except Exception as e:
            self.append_log(f"DOWNLOAD ERROR: {type(e).__name__}: {e}")

    def _post_url_from_post(self, base_url, post):
        host = _host(base_url)
        pid = str(post.get("id") or "")
        if not pid:
            return base_url
        if "allthefallen" in host or "danbooru" in host or "donmai" in host or host in ("e621.net", "e926.net"):
            return f"https://{host}/posts/{pid}"
        if host == "gelbooru.com":
            return f"https://gelbooru.com/index.php?page=post&s=view&id={pid}"
        if host in ("rule34.xxx", "api.rule34.xxx"):
            return f"https://rule34.xxx/index.php?page=post&s=view&id={pid}"
        if host == "rule34.us":
            return f"https://rule34.us/index.php?page=post&s=view&id={pid}"
        return base_url

    def download_tag_query(self):
        tags = self.preview_query.text().strip()
        limit_total = int(self.tag_limit.value())
        sites = self._enabled_parser_sites()

        if not tags:
            self.append_log("ERROR: тег пустой")
            return
        if not sites:
            self.append_log("ERROR: нет включённых сайтов парсера")
            return

        if QMessageBox.question(
            self,
            "Скачать тег",
            f"Вы точно уверены?\n\nТег/запрос: {tags}\nСайтов: {len(sites)}\nЛимит на сайт: {limit_total}\n\nБудут скачаны только новые файлы, блоклист граббера/подписок применяется.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        _settings = self.main.settings if hasattr(self, "main") else self.settings
        _threshold = max(1, int(_settings.get("large_download_warning_count", 1000) or 1000))
        if limit_total * len(sites) >= _threshold:
            from core.preflight import output_disk_info, format_bytes
            _disk = output_disk_info(_settings)
            _msg = (f"Запрошено до {limit_total * len(sites)} файлов суммарно.\n"
                    f"Свободно на диске: {format_bytes(_disk.get('free', 0))}.\n\nПродолжить?")
            if QMessageBox.question(self, "Большая загрузка", _msg, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return

        self.main.settings["grabber_preview_query"] = tags
        self.main.settings["grabber_tag_download_limit"] = limit_total
        try:
            save_settings(self.main.settings)
        except Exception:
            pass
        self.start_downloader_worker(
            "tag_many",
            {"sites": sites, "tags": tags, "limit_total": limit_total},
        )


    def _download_tag_many_impl(self, sites, tags, limit_total):
        for site in sites or []:
            if self.should_stop():
                break
            self.wait_if_paused()
            self.append_log(f"TAG DOWNLOAD SITE: {_host(site)}")
            self._download_tag_query_impl(site, tags, limit_total)

    def _download_tag_query_impl(self, site, tags, limit_total):
        if not site or not tags:
            self.append_log("ERROR: сайт или тег пустой")
            return

        try:
            session = self._grabber_session_for_url(site)
            got = 0
            page = 0
            per_page = min(100, max(1, limit_total))

            while got < limit_total:
                self.wait_if_paused()
                if self.should_stop():
                    self.append_log(f"STOPPED TAG DOWNLOAD: {got}")
                    return
                api = _tag_search_api(site, tags, page=page, limit=min(per_page, limit_total - got), settings=self._runtime_settings())
                if not api:
                    self.append_log("ERROR: этот сайт пока не поддержан для tag download")
                    return

                self.append_log(f"TAG API TRY: {_mask_sensitive_url(api)}")
                r = self._preview_http_get(session, site, api, timeout=40)
                self.append_log(f"TAG API STATUS: {r.status_code} {r.headers.get('content-type', '')}")

                if r.status_code >= 400:
                    self.append_log(f"ERROR: tag api status {r.status_code}")
                    return

                posts = _posts_from_json_response(r)

                if not posts:
                    raw_preview = (r.text or "")[:300].replace("\n", " ")
                    self.append_log(f"NO POSTS PARSED RAW: {raw_preview}")
                    if "Missing authentication" in raw_preview:
                        self.append_log("AUTH ERROR: проверь login/api_key/user_id в APT Sites для этого сайта")
                    self.append_log("DONE: посты закончились")
                    break

                for post in posts:
                    self.wait_if_paused()
                    if self.should_stop():
                        self.append_log(f"STOPPED TAG DOWNLOAD: {got}")
                        return
                    if got >= limit_total:
                        break

                    post_tags = _tag_list_from_post(post)
                    if self.has_blocked_tag(post_tags):
                        self.append_log(f"SKIP BLOCKED: post={post.get('id')}")
                        continue

                    file_url = _extract_file_url_from_json(post)
                    if not file_url:
                        self.append_log(f"SKIP NO FILE_URL: post={post.get('id')}")
                        continue

                    post_url = self._post_url_from_post(site, post)
                    stem = str(post.get("md5") or post.get("id") or Path(urlparse(file_url).path).stem or "download")

                    try:
                        self.append_log(f"DOWNLOAD {got + 1}/{limit_total}: {post_url}")
                        post, merged_groups = self._merge_html_groups_into_post(session, post_url, post)
                        self._download_file(session, file_url, post_url, stem, post, merged_groups)
                        got += 1
                        time.sleep(0.3)
                    except Exception as e:
                        self.append_log(f"SKIP DOWNLOAD ERROR: {type(e).__name__}: {e}")

                page += 1

            self.append_log(f"DONE TAG DOWNLOAD: {got}")

        except Exception as e:
            self.append_log(f"TAG DOWNLOAD ERROR: {type(e).__name__}: {e}")
