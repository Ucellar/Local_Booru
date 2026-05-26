from pathlib import Path
import weakref
import shiboken6
from urllib.parse import urlparse
import json
import time
import webbrowser
import shutil

from PySide6.QtCore import QUrl, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QLabel,
    QTabWidget, QWidget
)
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from core.paths import BROWSER_PROFILE_DIR, BROWSER_COOKIES_DIR

_BR34_WINDOW = None
MAX_BR34_TABS = 4
LOAD_TIMEOUT_MS = 90000  # 90 sec — rule34.xxx can be slow
AUTO_REUSE_CURRENT_TAB = False  # each site gets its own tab


def _normalize_url(url: str, allow_blank: bool = False):
    url = (url or "").strip().strip('"\'')
    if not url:
        return "about:blank" if allow_blank else None
    if url.lower() == "about:blank":
        return "about:blank" if allow_blank else None
    if url.startswith(("http://", "https://", "file:///")):
        return url
    if url.startswith("//"):
        return "https:" + url
    if "." in url and not any(ch.isspace() for ch in url):
        return "https://" + url
    return None


def _safe_host(host: str) -> str:
    host = (host or "default").lower().replace("www.", "").strip()
    return host.replace(":", "_").replace("/", "_")


def _rootish_hosts(host: str):
    host = (host or "").lower().replace("www.", "").strip().lstrip(".")
    out = []
    if host:
        out.append(host)
        parts = host.split(".")
        if len(parts) >= 2:
            out.append(".".join(parts[-2:]))
    return list(dict.fromkeys(out))


def _qt_valid(obj) -> bool:
    """Safe wrapper: shiboken6.isValid itself can raise for already-deleted wrappers."""
    if obj is None:
        return False
    try:
        return bool(shiboken6.isValid(obj))
    except Exception:
        return False


def _cookie_to_dict(cookie):
    def b(x):
        try:
            return bytes(x).decode("utf-8", errors="ignore")
        except Exception:
            return str(x)

    expires = None
    try:
        if not cookie.expirationDate().isNull():
            expires = int(cookie.expirationDate().toSecsSinceEpoch())
    except Exception:
        expires = None

    return {
        "name": b(cookie.name()),
        "value": b(cookie.value()),
        "domain": cookie.domain(),
        "path": cookie.path(),
        "expires": expires,
        "secure": bool(cookie.isSecure()),
        "httpOnly": bool(cookie.isHttpOnly()),
    }


class BrowserView(QWebEngineView):
    """QWebEngineView with mouse back/forward support."""
    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.BackButton:
                self.back(); return
            if event.button() == Qt.ForwardButton:
                self.forward(); return
        except Exception:
            pass
        super().mousePressEvent(event)


class BrowserPage(QWebEnginePage):
    """Open target=_blank/window.open links in br34 tabs."""
    def __init__(self, profile, owner, parent=None):
        super().__init__(profile, parent)
        self.owner = owner

    def createWindow(self, _type):
        # Popups/ad windows were the biggest br34 RAM source.
        # Allow only a small number of real popup tabs; otherwise reuse current page.
        try:
            if self.owner.tabs.count() >= MAX_BR34_TABS:
                self.owner.log("BR34 POPUP BLOCKED: tab limit reached")
                return self.owner.current_tab().page if self.owner.current_tab() else None
        except Exception:
            pass
        tab = self.owner.add_tab("about:blank", switch=False, popup=True)
        return tab.page if tab else None


class BrowserTab(QWidget):
    titleChanged = Signal(str)
    urlChanged = Signal(QUrl)
    loadStarted = Signal()
    loadProgress = Signal(int)
    loadFinished = Signal(bool)

    def __init__(self, profile, owner, url="about:blank", popup=False):
        super().__init__(owner)
        self.owner = owner
        self.popup = popup
        self.loading = False
        self.expected_url = _normalize_url(url, allow_blank=True) or "about:blank"
        self.view = BrowserView(self)
        self.page = BrowserPage(profile, owner, self.view)
        self.view.setPage(self.page)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.view.titleChanged.connect(self.titleChanged)
        self.view.urlChanged.connect(self.urlChanged)
        self.view.loadStarted.connect(self.loadStarted)
        self.view.loadProgress.connect(self.loadProgress)
        self.view.loadFinished.connect(self.loadFinished)
        # Direct load is intentionally used here. The delayed-load implementation
        # caused QtWebEngine to stay on about:blank on some machines.
        if url:
            self.load(url)

    def load(self, url: str):
        norm = _normalize_url(url, allow_blank=True)
        if not norm:
            return False
        self.expected_url = norm
        self.loading = True
        self.view.load(QUrl(norm))
        return True

    def stop(self):
        try:
            if _qt_valid(self.view):
                self.view.stop()
        except Exception:
            pass
        self.loading = False

    def safe_destroy(self):
        self.loading = False
        try:
            self.stop()
        except Exception:
            pass
        for signal_name in ("titleChanged", "urlChanged", "loadStarted", "loadProgress", "loadFinished"):
            try:
                getattr(self.view, signal_name).disconnect()
            except Exception:
                pass
        for signal_name in ("titleChanged", "urlChanged", "loadStarted", "loadProgress", "loadFinished"):
            try:
                getattr(self, signal_name).disconnect()
            except Exception:
                pass
        try:
            if _qt_valid(self.view):
                page = self.view.page()
                self.view.setPage(None)
                if page and _qt_valid(page):
                    page.deleteLater()
                self.view.deleteLater()
        except Exception:
            pass


class LoginBrowserDialog(QDialog):
    """br34: embedded persistent browser with tabs and cookie export."""

    def __init__(self, url: str = "https://example.com", parent=None, log_func=None):
        super().__init__(parent)
        self.log_func = log_func or (lambda m: None)
        self.setWindowTitle("br34 - Local Booru Browser")
        self._destroying = False
        # Clamp initial size to available screen
        try:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else None
            if avail:
                w = min(1360, avail.width() - 40)
                h = min(900,  avail.height() - 80)
                self.resize(w, h)
                # Center on screen
                self.move(
                    avail.x() + (avail.width()  - w) // 2,
                    avail.y() + (avail.height() - h) // 2,
                )
            else:
                self.resize(1360, 900)
        except Exception:
            self.resize(1360, 900)

        self.current_url = _normalize_url(url, allow_blank=True) or "about:blank"
        self.cookies_by_host = {}
        self.cf_cookie_seen = False

        BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        BROWSER_COOKIES_DIR.mkdir(parents=True, exist_ok=True)

        self.profile = QWebEngineProfile("LocalBooruLoginProfile", self)
        self.profile.setPersistentStoragePath(str(BROWSER_PROFILE_DIR))
        self.profile.setCachePath(str(BROWSER_PROFILE_DIR / "cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        try:
            # Memory cache prevents QtWebEngine dictionary/disk cache growth while keeping br34 functional.
            self.profile.setHttpCacheType(QWebEngineProfile.MemoryHttpCache)
        except Exception:
            pass
        try:
            self.profile.setHttpUserAgent(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0"
            )
        except Exception:
            pass
        try:
            settings = self.profile.settings()
            for attr in [
                "JavascriptEnabled", "LocalStorageEnabled", "JavascriptCanOpenWindows",
                "JavascriptCanAccessClipboard", "FullScreenSupportEnabled",
                "AllowRunningInsecureContent", "PluginsEnabled"
            ]:
                try:
                    settings.setAttribute(getattr(QWebEngineSettings, attr), True)
                except Exception:
                    pass
            # GPU/WebGL in embedded QtWebEngine is a common source of white pages,
            # renderer crashes and memory growth on some Windows systems.
            # Keep WebGL disabled to prevent renderer crashes on some systems
            for attr in ["WebGLEnabled", "Accelerated2dCanvasEnabled"]:
                try:
                    settings.setAttribute(getattr(QWebEngineSettings, attr), False)
                except Exception:
                    pass
            # Enable features that help Cloudflare/JS-heavy sites load
            for attr in ["DnsPrefetchEnabled", "LocalContentCanAccessRemoteUrls",
                         "ErrorPageEnabled", "ScrollAnimatorEnabled"]:
                try:
                    settings.setAttribute(getattr(QWebEngineSettings, attr), True)
                except Exception:
                    pass
        except Exception:
            pass

        self.store = self.profile.cookieStore()
        self.store.cookieAdded.connect(self.on_cookie_added)
        try:
            self.store.loadAllCookies()
        except Exception:
            pass

        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        self.back_btn = QPushButton("←")
        self.forward_btn = QPushButton("→")
        self.refresh_btn = QPushButton("⟳")
        self.stop_btn = QPushButton("Stop")
        self.new_tab_btn = QPushButton("+")
        self.close_tab_btn = QPushButton("×")
        self.url_edit = QLineEdit(self.current_url)
        self.go_btn = QPushButton("Go")
        self.save_btn = QPushButton("Save cookies")
        self.external_btn = QPushButton("Open external")
        self.close_btn = QPushButton("Close")
        for w in [self.back_btn, self.forward_btn, self.refresh_btn, self.stop_btn, self.new_tab_btn, self.close_tab_btn]:
            top.addWidget(w)
        top.addWidget(QLabel("URL:"))
        top.addWidget(self.url_edit, 1)
        for w in [self.go_btn, self.save_btn, self.external_btn, self.close_btn]:
            top.addWidget(w)
        lay.addLayout(top)

        self.tabs = QTabWidget(self)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        lay.addWidget(self.tabs, 1)

        self.status = QLabel("")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.status)

        self.back_btn.clicked.connect(lambda: self.current_view().back() if self.current_view() else None)
        self.forward_btn.clicked.connect(lambda: self.current_view().forward() if self.current_view() else None)
        self.refresh_btn.clicked.connect(lambda: self.current_view().reload() if self.current_view() else None)
        self.stop_btn.clicked.connect(lambda: self.current_tab().stop() if self.current_tab() else None)
        self.new_tab_btn.clicked.connect(lambda: self.add_tab("https://google.com", switch=True))
        self.close_tab_btn.clicked.connect(lambda: self.close_tab(self.tabs.currentIndex()))
        self.go_btn.clicked.connect(self.go)
        self.save_btn.clicked.connect(self.save_all_cookies)
        self.external_btn.clicked.connect(self.open_external)
        self.close_btn.clicked.connect(self.release_and_hide)
        self.url_edit.returnPressed.connect(self.go)
        self.tabs.currentChanged.connect(self.on_current_tab_changed)
        self.tabs.tabCloseRequested.connect(self.close_tab)

        self.add_tab(self.current_url, switch=True)

        self._trim_timer = QTimer(self)
        self._trim_timer.setInterval(90000)
        self._trim_timer.timeout.connect(self.trim_memory)
        self._trim_timer.start()

    def current_tab(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, BrowserTab) and _qt_valid(w) else None

    def current_view(self):
        tab = self.current_tab()
        return tab.view if tab and _qt_valid(tab.view) else None

    def add_tab(self, url: str = "https://google.com", switch: bool = True, popup: bool = False):
        norm = _normalize_url(url, allow_blank=popup)
        if not norm:
            self.log(f"BR34 SKIP EMPTY/BAD URL: {url!r}")
            return self.current_tab()

        # Reuse Google/Lens tabs to avoid a thousand Google windows/tabs.
        reusable = self.find_reusable_tab(norm)
        if reusable is not None and not popup:
            if switch:
                self.tabs.setCurrentIndex(self.tabs.indexOf(reusable))
            self.log(f"BR34 LOAD: {norm}")
            reusable.load(norm)
            return reusable

        self.close_blank_tabs(keep_current=True)
        while self.tabs.count() >= MAX_BR34_TABS:
            idx = 0 if self.tabs.currentIndex() != 0 else 1
            self.close_tab(idx)

        self.log(f"BR34 LOAD: {norm}")
        tab = BrowserTab(self.profile, self, norm, popup=popup)
        idx = self.tabs.addTab(tab, "Loading...")
        if switch:
            self.tabs.setCurrentIndex(idx)
        tab.titleChanged.connect(lambda title, t=tab: self.update_tab_title(t, title))
        tab.urlChanged.connect(lambda qurl, t=tab: self.on_tab_url_changed(t, qurl))
        tab.loadStarted.connect(lambda t=tab: self.on_tab_load_started(t))
        tab.loadProgress.connect(lambda p, t=tab: self.on_tab_load_progress(t, p))
        tab.loadFinished.connect(lambda ok, t=tab: self.on_tab_load_finished(t, ok))
        QTimer.singleShot(LOAD_TIMEOUT_MS, lambda t=weakref.ref(tab): self.on_tab_load_timeout(t()))
        if popup:
            QTimer.singleShot(4500, lambda t=weakref.ref(tab): self.close_if_blank_popup(t()))
        return tab

    def _host_of(self, url: str) -> str:
        try:
            return urlparse(url or "").netloc.lower().replace("www.", "")
        except Exception:
            return ""

    def find_reusable_tab(self, url: str):
        host = self._host_of(url)
        if not host:
            return None
        if host.endswith("google.com") or host.endswith("lens.google.com") or "google." in host:
            for i in range(self.tabs.count()):
                w = self.tabs.widget(i)
                if isinstance(w, BrowserTab) and _qt_valid(w):
                    try:
                        h = self._host_of(w.view.url().toString() or w.expected_url)
                    except Exception:
                        h = self._host_of(w.expected_url)
                    if h.endswith("google.com") or h.endswith("lens.google.com") or "google." in h:
                        return w
        return None

    def close_blank_tabs(self, keep_current=True):
        cur = self.tabs.currentWidget() if keep_current else None
        for i in reversed(range(self.tabs.count())):
            w = self.tabs.widget(i)
            if keep_current and w is cur:
                continue
            if isinstance(w, BrowserTab) and _qt_valid(w):
                try:
                    u = w.view.url().toString()
                except Exception:
                    u = ""
                tab_text = self.tabs.tabText(i) if i >= 0 else ""
                if (not u or u == "about:blank") and not w.loading and tab_text != "Loading...":
                    self.close_tab(i)

    def close_if_blank_popup(self, tab):
        if self._destroying or not _qt_valid(tab):
            return
        idx = self.tabs.indexOf(tab)
        if idx < 0:
            return
        try:
            u = tab.view.url().toString()
        except Exception:
            u = ""
        if not u or u == "about:blank":
            self.close_tab(idx)

    def trim_memory(self):
        try:
            self.close_blank_tabs(keep_current=True)
            while self.tabs.count() > MAX_BR34_TABS:
                idx = 0 if self.tabs.currentIndex() != 0 else 1
                self.close_tab(idx)
        except Exception:
            pass

    def close_tab(self, idx: int):
        if idx < 0:
            return
        if self.tabs.count() <= 1:
            tab = self.current_tab()
            if tab:
                tab.stop()
                tab.load("about:blank")
            return
        w = self.tabs.widget(idx)
        if w and not _qt_valid(w):
            return
        self.tabs.removeTab(idx)
        if w:
            try:
                if isinstance(w, BrowserTab):
                    w.safe_destroy()
            except Exception:
                pass
            try:
                w.deleteLater()
            except Exception:
                pass

    def update_tab_title(self, tab, title):
        if self._destroying or not _qt_valid(tab):
            return
        idx = self.tabs.indexOf(tab)
        if idx >= 0:
            title = (title or "New tab").strip()
            if len(title) > 28:
                title = title[:25] + "..."
            self.tabs.setTabText(idx, title or "Tab")

    def on_tab_url_changed(self, tab, qurl):
        if self._destroying or not _qt_valid(tab):
            return
        url = qurl.toString()
        if tab is self.current_tab():
            self.current_url = url
            self.url_edit.setText(url)

    def on_tab_load_started(self, tab):
        if self._destroying or not _qt_valid(tab):
            return
        tab.loading = True
        idx = self.tabs.indexOf(tab)
        if idx >= 0:
            self.tabs.setTabText(idx, "Loading...")

    def on_tab_load_progress(self, tab, progress):
        if self._destroying or not _qt_valid(tab):
            return
        if tab is self.current_tab():
            try:
                u = tab.expected_url or tab.view.url().toString()
            except Exception:
                u = "<deleted>"
            self.status.setText(f"Loading {progress}%: {u}")

    def on_tab_load_timeout(self, tab):
        if self._destroying or not _qt_valid(tab):
            return
        idx = self.tabs.indexOf(tab)
        if idx < 0:
            return
        # Only warn if still loading — don't stop, let it continue
        if not getattr(tab, "loading", False):
            return
        try:
            url = tab.expected_url or tab.view.url().toString()
        except Exception:
            url = "<deleted>"
        # Check if page actually loaded something (not about:blank)
        try:
            current_url = tab.view.url().toString()
            if current_url and current_url != "about:blank":
                # Page started loading, just slow — don't interrupt
                self.log(f"BR34 SLOW LOAD: {url}. Still loading, please wait...")
                # Give another 60s
                import weakref
                QTimer.singleShot(60000, lambda t=weakref.ref(tab): self._final_timeout(t()))
                return
        except Exception:
            pass
        # Truly stuck — update label but still don't stop
        if idx >= 0:
            self.tabs.setTabText(idx, "Slow...")
        self.log(f"BR34 LOAD SLOW: {url}. Use Refresh/Go or Open external browser.")

    def _final_timeout(self, tab):
        """Final timeout — stop if still loading after 150s total."""
        if self._destroying or not _qt_valid(tab):
            return
        idx = self.tabs.indexOf(tab)
        if idx < 0 or not getattr(tab, "loading", False):
            return
        try:
            url = tab.expected_url or tab.view.url().toString()
        except Exception:
            url = "<deleted>"
        try:
            tab.stop()
        except Exception:
            pass
        tab.loading = False
        if idx >= 0:
            self.tabs.setTabText(idx, "Timeout")
        self.log(f"BR34 LOAD TIMEOUT: {url}. Use Refresh or Open external.")

    def on_current_tab_changed(self, idx):
        tab = self.current_tab()
        if tab and _qt_valid(tab):
            try:
                self.current_url = tab.view.url().toString() or tab.expected_url
            except Exception:
                self.current_url = tab.expected_url or "about:blank"
            self.url_edit.setText(self.current_url)

    def log(self, msg):
        self.status.setText(msg)
        self.log_func(msg)

    def mousePressEvent(self, event):
        try:
            view = self.current_view()
            if event.button() == Qt.BackButton and view:
                view.back(); return
            if event.button() == Qt.ForwardButton and view:
                view.forward(); return
        except Exception:
            pass
        super().mousePressEvent(event)

    def go(self):
        url = _normalize_url(self.url_edit.text(), allow_blank=False)
        if not url:
            self.log("BR34 SKIP EMPTY/BAD URL FROM ADDRESS BAR")
            return
        tab = self.current_tab() or self.add_tab(url, switch=True)
        if tab:
            self.log(f"BR34 LOAD: {url}")
            tab.load(url)
            QTimer.singleShot(LOAD_TIMEOUT_MS, lambda t=weakref.ref(tab): self.on_tab_load_timeout(t()))

    def open_url_in_tab(self, url: str, switch=True):
        norm = _normalize_url(url, allow_blank=False)
        if not norm:
            self.log(f"BR34 SKIP EMPTY/BAD URL: {url!r}")
            return self.current_tab()
        return self.add_tab(norm, switch=switch)

    def load_current_or_new(self, url: str):
        norm = _normalize_url(url, allow_blank=False)
        if not norm:
            self.log(f"BR34 SKIP EMPTY/BAD URL: {url!r}")
            return None
        tab = self.current_tab()
        if tab is None:
            return self.add_tab(norm, switch=True)
        self.log(f"BR34 REUSE TAB LOAD: {norm}")
        tab.load(norm)
        QTimer.singleShot(LOAD_TIMEOUT_MS, lambda t=weakref.ref(tab): self.on_tab_load_timeout(t()))
        return tab

    def closeEvent(self, event):
        if not getattr(self, "_destroying", False):
            # X button = hide, not destroy. Keep window alive for reuse.
            event.ignore()
            self.release_and_hide()
            return
        try:
            self.trim_memory()
        except Exception:
            pass
        super().closeEvent(event)

    def hideEvent(self, event):
        try:
            self.trim_memory()
        except Exception:
            pass
        super().hideEvent(event)

    def _clamp_to_screen(self):
        """Ensure dialog stays fully within available screen area."""
        try:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.screenAt(self.geometry().center())
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            avail = screen.availableGeometry()
            geo  = self.frameGeometry()
            w = min(geo.width(),  avail.width()  - 20)
            h = min(geo.height(), avail.height() - 40)
            x = max(avail.x(), min(geo.x(), avail.right()  - w))
            y = max(avail.y(), min(geo.y(), avail.bottom() - h))
            if geo.width() != w or geo.height() != h:
                self.resize(w, h)
            if geo.x() != x or geo.y() != y:
                self.move(x, y)
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Prevent window from growing larger than screen
        try:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
            if screen:
                avail = screen.availableGeometry()
                if self.width() > avail.width() or self.height() > avail.height() - 40:
                    from PySide6.QtCore import QTimer
                    QTimer.singleShot(0, self._clamp_to_screen)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._clamp_to_screen)

    def release_and_hide(self):
        # Just hide the window — keep it alive for reuse.
        # This avoids the "next site creates new window" problem.
        try:
            self.save_all_cookies(silent=True)
        except Exception:
            pass
        self._clamp_to_screen()
        self.hide()
        try:
            self.log_func("br34 HIDDEN (reusable). Next login will reuse this window.")
        except Exception:
            pass

    def destroy_completely(self):
        # Called only on app exit.
        global _BR34_WINDOW
        if self._destroying:
            return
        self._destroying = True
        try:
            self.save_all_cookies(silent=True)
        except Exception:
            pass
        try:
            if hasattr(self, "store") and _qt_valid(self.store):
                try:
                    self.store.cookieAdded.disconnect(self.on_cookie_added)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            for i in reversed(range(self.tabs.count())):
                w = self.tabs.widget(i)
                self.tabs.removeTab(i)
                if isinstance(w, BrowserTab):
                    try:
                        w.safe_destroy()
                    except Exception:
                        pass
                if w:
                    try:
                        w.deleteLater()
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            if _qt_valid(self.profile):
                self.profile.clearHttpCache()
        except Exception:
            pass
        try:
            pass
        except Exception:
            pass
        _BR34_WINDOW = None
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        # Small delay before close to let Qt process pending events
        try:
            QTimer.singleShot(100, self.close)
        except Exception:
            self.close()

    def open_external(self):
        url = _normalize_url(self.url_edit.text() or self.current_url, allow_blank=False)
        if url:
            webbrowser.open(url)
            self.log("Opened in external browser. For Danbooru/Cloudflare, export cookies.txt from Chrome/Edge and put it in data/runtime/browser_cookies/danbooru.donmai.us.txt")
        else:
            self.log("BR34 SKIP EMPTY/BAD EXTERNAL URL")

    def on_tab_load_finished(self, tab, ok):
        if self._destroying or not _qt_valid(tab):
            return
        tab.loading = False
        try:
            if tab.view.url().toString() == "about:blank" and self.tabs.count() > 1:
                QTimer.singleShot(500, lambda t=weakref.ref(tab): self.close_if_blank_popup(t()))
        except Exception:
            pass
        if tab is not self.current_tab():
            QTimer.singleShot(1500, lambda s=weakref.ref(self): s() and s().save_all_cookies(silent=True))
            return
        try:
            url = tab.view.url().toString() or tab.expected_url
        except Exception:
            url = tab.expected_url or "about:blank"
        self.current_url = url
        self.url_edit.setText(url)
        host = urlparse(url).netloc.lower().replace("www.", "")
        msg = f"Loaded: {host or url} ({'OK' if ok else 'FAIL'}). br34 tabs: {self.tabs.count()}/{MAX_BR34_TABS}. Login/search here, then press Save cookies."
        if "donmai.us" in host and not self.cf_cookie_seen:
            msg += " Danbooru note: if Cloudflare loops, use Open external + import cookies.txt with cf_clearance."
        self.log(msg)
        QTimer.singleShot(1500, lambda s=weakref.ref(self): s() and s().save_all_cookies(silent=True))

    def on_cookie_added(self, cookie):
        if self._destroying:
            return
        try:
            if not _qt_valid(self.profile):
                return
        except Exception:
            return
        d = _cookie_to_dict(cookie)
        name = (d.get("name") or "")
        if name.lower() == "cf_clearance":
            self.cf_cookie_seen = True
            self.log("Cloudflare cookie captured: cf_clearance")

        cookie_domain = (d.get("domain") or "").lower().lstrip(".").replace("www.", "")
        cur = urlparse(self.current_url).netloc.lower().replace("www.", "")
        hosts = set(_rootish_hosts(cookie_domain)) | set(_rootish_hosts(cur))
        if not hosts:
            hosts = {"default"}
        for host in hosts:
            key = _safe_host(host)
            current = self.cookies_by_host.setdefault(key, {})
            current[name] = d
        self.save_all_cookies(silent=True)

    def save_all_cookies(self, silent=False):
        if self._destroying:
            return
        try:
            if not _qt_valid(self.profile):
                return
        except Exception:
            return
        try:
            if hasattr(self, "store") and _qt_valid(self.store):
                self.store.loadAllCookies()
        except Exception:
            pass
        saved_total = 0
        saved_files = []
        for host, cookie_map in self.cookies_by_host.items():
            cookies = list(cookie_map.values())
            if not cookies:
                continue
            out = BROWSER_COOKIES_DIR / f"{host}.json"
            data = {
                "cookies": cookies,
                "user_agent": self.profile.httpUserAgent() if _qt_valid(self.profile) else "",
                "saved_at": time.time(),
                "source": "qt_webengine",
            }
            out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            saved_total += len(cookies)
            saved_files.append(out.name)
        if not silent:
            host = urlparse(self.current_url).netloc.lower().replace("www.", "")
            names = sorted({name for m in self.cookies_by_host.values() for name in m})
            extra = ""
            if "donmai.us" in host and "cf_clearance" not in names:
                extra = " | WARNING: no cf_clearance, requests will probably get Cloudflare 403."
            self.log(f"Saved cookies: {saved_total} to {', '.join(saved_files) or 'none'}. Names: {', '.join(names[:30])}{extra}")
            # Always clamp after save
            self._clamp_to_screen()


def _br34_is_alive() -> bool:
    """Return True if BR34 window exists and is usable (visible or hidden but not destroyed)."""
    global _BR34_WINDOW
    if _BR34_WINDOW is None:
        return False
    try:
        if not _qt_valid(_BR34_WINDOW):
            _BR34_WINDOW = None
            return False
        if getattr(_BR34_WINDOW, "_destroying", False):
            _BR34_WINDOW = None
            return False
        # Window can be hidden (after save cookies) — still reusable
        return True
    except Exception:
        _BR34_WINDOW = None
        return False


def open_br34(url: str = "https://google.com", parent=None, log_func=None, switch=True):
    """Open/reuse the single br34 window and add URL as a tab."""
    global _BR34_WINDOW
    norm = _normalize_url(url, allow_blank=False)
    if not norm:
        if log_func:
            log_func(f"BR34 SKIP EMPTY/BAD URL: {url!r}")
        return _BR34_WINDOW

    log = log_func or (lambda m: None)

    try:
        if _br34_is_alive():
            # Window exists (visible or hidden) — show it and add tab
            _BR34_WINDOW.log_func = log_func or _BR34_WINDOW.log_func
            _BR34_WINDOW.open_url_in_tab(norm, switch=switch)
            if not _BR34_WINDOW.isVisible():
                _BR34_WINDOW.show()
            _BR34_WINDOW.raise_()
            _BR34_WINDOW.activateWindow()
            # Don't log here - open_url_in_tab already logs BR34 LOAD
            return _BR34_WINDOW
        else:
            _BR34_WINDOW = None
            win = LoginBrowserDialog(norm, parent, log_func=log_func)
            _BR34_WINDOW = win
            win.show()
            win.raise_()
            win.activateWindow()
            log(f"BR34 WINDOW CREATED: {norm}")
            return win
    except Exception:
        _BR34_WINDOW = None
        try:
            win = LoginBrowserDialog(norm, parent, log_func=log_func)
            _BR34_WINDOW = win
            win.show()
            log(f"BR34 WINDOW CREATED (retry): {norm}")
            return win
        except Exception as e:
            log(f"BR34 FATAL: {e}")
            return None


def open_br34_multi(urls: list, parent=None, log_func=None):
    """Open multiple URLs in a single br34 window as tabs.
    
    First URL creates/reuses the window, rest are added as tabs immediately.
    This avoids the race condition where separate open_br34 calls each see
    the window as not-yet-visible and create separate windows.
    """
    global _BR34_WINDOW
    if not urls:
        return None
    
    log = log_func or (lambda m: None)
    normed = [u for u in (_normalize_url(u, allow_blank=False) for u in urls) if u]
    if not normed:
        log("BR34 MULTI: no valid URLs")
        return None

    # Ensure single window exists
    win = open_br34(normed[0], parent=parent, log_func=log_func, switch=True)
    if win is None:
        return None

    # Add remaining URLs as tabs WITHOUT creating new windows
    for url in normed[1:]:
        try:
            log(f"BR34 ADD TAB: {url}")
            win.open_url_in_tab(url, switch=False)
            log("br34 OPENED / TAB ADDED")
        except Exception as e:
            log(f"BR34 TAB ERROR: {e}")
    
    return win
