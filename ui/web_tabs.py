"""(v5.44.117) The program's second window — its pages as tabs, as a browser has them.

The owner (7.11.2026): «если нажал скм то тайтл открывается в отдельном окне как браузер с вкладками (можно будет сразу
несколько грузить одновременно) и так с каждой хуйней в проге … а закрытие такого окна не как не будет сказываться на
программе они по факту та же страница в проге но в другом окне».

A page of the program asks for a new window or tab — the middle button, Ctrl+click, ``target=_blank``, «Открыть ссылку в
новой вкладке» (``web_gallery_page._GalleryPage.createWindow`` → ``_NewWindowPage``): the program's own address opens
here (:func:`open_tab`) — the first such link opens this window, the next ones are new tabs in it, from the main window
and from this one; another site's — in the usual browser, as before.  A tab opened by the middle button or Ctrl+click
(a tab «in the background») does not take the place of the one shown, nor this window to the front once it is open:
several titles are asked at once.  The same web profile and cookies as the main window's
(``web_gallery_page.profile``); each tab its page's name and ×, the middle button on a tab closes it; closing a tab or
the window touches nothing else — the server, the background work, the main window's video and its full screen.  The
main window closing closes this one too (:func:`close_all`: the program ends with the main window).

What a page asks of the window (``/api/app/*`` → ``ui/web_bridge.py``): every request of a page here carries its tab's
mark — the header ``X-LB-Tab`` (:data:`MARK_HEADER`; the page's own request interceptor, :class:`_Mark`: the program's
addresses only) — so a dialog opens over this window and what follows it goes to this tab (:func:`tab_of`); ⛶ of a
page's own player and F11 — this window full screen.  The video the program plays itself (Qt Multimedia,
``webui/video_relay.RELAY``) is one for the whole program: from a page here it is refused in words, the main window's
goes on (``webui/pages/api.py``); a video the window's browser plays in the page plays here as anywhere.
"""
from __future__ import annotations

import itertools
import logging
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QTabBar, QTabWidget, QVBoxLayout, QWidget

_LOG = logging.getLogger("local_booru.window")

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:                  # PySide6-Addons is not installed: no window of tabs (the pages are in the browser)
    QWebEnginePage = QWebEngineUrlRequestInterceptor = QWebEngineView = None

MARK_HEADER = "X-LB-Tab"            # the header of every request of a page in a tab: its tab's mark
TITLE = "Local Booru"
SUFFIX = " — Local Booru"           # the pages' titles end so: a tab shows the name before it
SCREEN_CLAIM_MS = 4000              # a page loaded while the window is full screen for a player: it says so by then
_MARKS = itertools.count(1)
_WINDOW = None                      # the window of tabs (one for the program), None until a page asks for one
_DYING: list = []                   # a window closed, kept until it is destroyed (in the window's thread)
_TABS: dict = {}                    # a tab's mark → the tab (the bridge finds the page that asked by it)
_PLACE: dict = {}                   # the window's place and size as it was left (for this run of the program)


def tab_of(mark: str) -> "Tab | None":
    """The tab whose page carries ``mark`` (None — no such tab now: it was closed)."""
    return _TABS.get(str(mark or ""))


def window_of(page) -> "TabsWindow | None":
    """The window of tabs when ``page`` (a ``QWebEnginePage``) is one of its tabs' — a dialog it asks for opens there."""
    for tab in list(_TABS.values()):
        try:
            if tab.view.page() is page:
                return _WINDOW
        except RuntimeError:          # a tab being destroyed
            continue
    return None


def opened() -> "TabsWindow | None":
    return _WINDOW


def open_tab(url: QUrl, *, background: bool = False, settings_provider=None) -> bool:
    """The program's address ``url`` in a new tab of the window of tabs — the window made and shown when there is none;
    ``background`` (the middle button, Ctrl+click) — the tab shown is not changed and the window not brought to the front
    when it was open already.  False — no window of tabs here (no Qt WebEngine)."""
    global _WINDOW
    if QWebEngineView is None:
        return False
    from ui.web_gallery_page import profile
    if profile() is None:            # no page of the program yet: nothing to share the profile with
        return False
    window, first = _WINDOW, _WINDOW is None
    if first:
        window = TabsWindow(settings_provider)
        _WINDOW = window
    tab = window.add(url, front=first or not background)
    if first:
        window.show_first()
    elif not background:
        window.bring_up()
    elif not window.isActiveWindow():
        QApplication.alert(window)
    return tab is not None


def close_all() -> None:
    """The main window closes: the window of tabs with it (the program ends with the main window — and Qt quits only
    when no window is left)."""
    window = _WINDOW
    if window is not None:
        try:
            window.close()
        except RuntimeError as exc:
            _LOG.warning("the window of tabs not closed: %s", exc)
    _dispose()


def _dispose() -> None:
    """The windows of tabs closed destroyed here, in the window's thread, now — never left to the garbage collector (it
    may run in another thread, and a web view destroyed there ends the program: Chromium's check)."""
    try:
        import shiboken6
    except ImportError:              # a stand-in Qt (the checks without PySide6)
        _DYING.clear()
        return
    while _DYING:
        window = _DYING.pop()
        try:
            if shiboken6.isValid(window):
                shiboken6.delete(window)
        except RuntimeError as exc:
            _LOG.warning("the window of tabs not destroyed: %s", exc)


def _clean_title(title: str) -> str:
    """A page's title as a tab shows it: never its address (it carries the session key); without « — Local Booru»."""
    text = str(title or "").strip()
    if not text or "127.0.0.1" in text or "localhost" in text or "://" in text:
        return ""
    return text[:-len(SUFFIX)] if text.endswith(SUFFIX) and len(text) > len(SUFFIX) else text


if QWebEngineUrlRequestInterceptor is not None:
    class _Mark(QWebEngineUrlRequestInterceptor):
        """Every request of the page of a tab — to the program's own server only — carries its tab's mark."""

        def __init__(self, mark: str, parent=None):
            super().__init__(parent)
            self.mark = mark.encode("ascii")

        def interceptRequest(self, info) -> None:  # noqa: N802 - Qt's name
            if info.requestUrl().host() in ("127.0.0.1", "localhost"):
                info.setHttpHeader(MARK_HEADER.encode("ascii"), self.mark)


class _TabBar(QTabBar):
    """The middle button on a tab closes it, as in a browser."""

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt's name
        if event.button() == Qt.MouseButton.MiddleButton:
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self.tabCloseRequested.emit(index)
                return
        super().mouseReleaseEvent(event)


class Tab(QWidget):
    """A tab: its page — the program's, in the same web profile as the main window's — and what the window does for a
    page (``ui/web_bridge.py`` asks it as it asks the main window's page: ``open_path``, ``current_path``,
    ``toggle_fullscreen``, ``video_screen``)."""

    def __init__(self, window: "TabsWindow", mark: str):
        super().__init__(window)     # a parent from the first line: never left to the garbage collector
        from ui.qt_language import hide_dead_actions
        from ui.web_gallery_page import new_page, profile
        self.mark = mark
        self.owner = window
        self._profile = profile()
        self.view = QWebEngineView(self)
        page = new_page(self._profile, self.view, window.settings_provider)      # as the main window's pages
        self._mark = _Mark(mark, page)           # the page's own (it goes with the page; the page does not own it)
        page.setUrlRequestInterceptor(self._mark)
        self.view.setPage(page)
        hide_dead_actions(page)
        page.renderProcessTerminated.connect(self._render_died)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self._screen = False          # ⛶ of the page's player: this window full screen for it
        self._screen_was = None
        self._screen_since = 0.0
        self._screen_claimed = False
        self._died_at = 0.0
        self.view.loadStarted.connect(self._load_started)
        self.view.loadFinished.connect(self._load_finished)

    # ── what the bridge asks of a page ──────────────────────────────────────────────────────────────────────────────
    def open_path(self, path: str) -> None:
        from webui import service
        server = service.running()
        if server is not None:
            self.view.load(QUrl(server.login_url(path)))

    def current_path(self) -> str:
        return self.view.url().path()

    def toggle_fullscreen(self) -> None:
        self.owner.toggle_fullscreen()

    def video_screen(self, on: bool) -> None:
        """⛶ of the page's player: this window full screen for it, and back to the state it had."""
        if not on:
            self.leave_screen()
            return
        self._screen_claimed = True
        if self._screen:
            return
        window = self.owner
        self._screen = True
        self._screen_since = time.monotonic()
        window.screen_keys(True)
        if window.isFullScreen():
            self._screen_was = None
        else:
            self._screen_was = window.windowState()
            window.showFullScreen()

    def leave_screen(self) -> None:
        if not self._screen:
            return
        self._screen = False
        window = self.owner
        window.screen_keys(False)
        was, self._screen_was = self._screen_was, None
        if was is not None and window.isFullScreen():
            if was & Qt.WindowState.WindowMaximized:
                window.showMaximized()
            else:
                window.showNormal()

    def screen_on(self) -> bool:
        return self._screen

    def screen_off_by_window(self) -> None:
        """Esc on the whole screen, the window left full screen by itself, another tab shown: the player goes back."""
        if not self._screen:
            return
        self.leave_screen()
        self.view.page().runJavaScript(
            "window.LocalBooru && LocalBooru.nativeVideo && LocalBooru.nativeVideo.screen(false)")

    def _load_started(self) -> None:
        self._screen_claimed = False

    def _load_finished(self, _ok: bool) -> None:
        if self._screen:
            QTimer.singleShot(SCREEN_CLAIM_MS, self._screen_check)

    def _screen_check(self) -> None:
        if self._screen and not self._screen_claimed:
            self.leave_screen()

    def _render_died(self, status, code) -> None:
        """The page's renderer ended: the page loads again (once in a while — not round and round)."""
        if status == QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus:
            return
        _LOG.warning("a tab's page renderer ended (%s, exit code %s)", getattr(status, "name", status), code)
        now = time.monotonic()
        if now - self._died_at > 60:
            self._died_at = now
            QTimer.singleShot(500, self.view.reload)


class TabsWindow(QMainWindow):
    """The window of tabs (one for the program — :func:`open_tab`)."""

    def __init__(self, settings_provider=None):
        super().__init__()
        self.settings_provider = settings_provider or (lambda: {})
        self.setWindowTitle(TITLE)
        self.setMinimumSize(640, 420)
        from core.paths import APP_ICON_FILE
        if APP_ICON_FILE.exists():
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(APP_ICON_FILE)))
        self.tabs = QTabWidget(self)
        self.tabs.setTabBar(_TabBar(self.tabs))
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._current_changed)
        self.tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._tab_menu)
        self.setCentralWidget(self.tabs)
        self._was_maximized = False
        for keys, slot in (("Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex())),
                           ("Ctrl+F4", lambda: self.close_tab(self.tabs.currentIndex())),
                           ("Ctrl+Tab", lambda: self._step(1)), ("Ctrl+PgDown", lambda: self._step(1)),
                           ("Ctrl+Shift+Tab", lambda: self._step(-1)), ("Ctrl+PgUp", lambda: self._step(-1)),
                           ("F5", lambda: self._each(lambda t: t.view.reload())),
                           ("Ctrl+R", lambda: self._each(lambda t: t.view.reload())),
                           ("Alt+Left", lambda: self._each(lambda t: t.view.back())),
                           ("Alt+Right", lambda: self._each(lambda t: t.view.forward())),
                           ("F11", self.toggle_fullscreen)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)
        # Esc of a player on the whole screen: the window's own key, only meanwhile (the pages have their own Esc)
        self._esc = QShortcut(QKeySequence("Esc"), self)
        self._esc.activated.connect(lambda: self._each(lambda t: t.screen_off_by_window()))
        self._esc.setEnabled(False)
        self._place()

    # ── the tabs ────────────────────────────────────────────────────────────────────────────────────────────────────
    def add(self, url: QUrl, *, front: bool = True) -> Tab:
        mark = f"t{next(_MARKS)}"
        tab = Tab(self, mark)
        _TABS[mark] = tab
        index = self.tabs.addTab(tab, "Загрузка…")
        self.tabs.setTabToolTip(index, "Загрузка…")
        tab.view.titleChanged.connect(lambda title, tab=tab: self._titled(tab, title))
        tab.view.iconChanged.connect(lambda icon, tab=tab: self._iconed(tab, icon))
        tab.view.load(url)
        if front:
            self.tabs.setCurrentWidget(tab)
            tab.view.setFocus()
        return tab

    def close_tab(self, index: int) -> None:
        """A tab closed: its page goes (its own player with it); the last one — the window closes."""
        tab = self.tabs.widget(index)
        if tab is None:
            return
        if isinstance(tab, Tab):
            tab.leave_screen()
            _TABS.pop(tab.mark, None)
        self.tabs.removeTab(index)
        tab.deleteLater()            # (its parent keeps it until then: never the garbage collector's)
        if self.tabs.count() == 0:
            self.close()

    def _titled(self, tab: Tab, title: str) -> None:
        index = self.tabs.indexOf(tab)
        if index < 0:
            return
        name = _clean_title(title)
        self.tabs.setTabText(index, (name or TITLE).replace("&", "&&"))
        self.tabs.setTabToolTip(index, name or TITLE)
        if self.tabs.currentWidget() is tab:
            self._show_title(tab)

    def _iconed(self, tab: Tab, icon) -> None:
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self.tabs.setTabIcon(index, icon)

    def _show_title(self, tab) -> None:
        name = _clean_title(tab.view.title()) if isinstance(tab, Tab) else ""
        self.setWindowTitle(f"{name}{SUFFIX}" if name else TITLE)

    def _current_changed(self, _index: int) -> None:
        current = self.tabs.currentWidget()
        for tab in list(_TABS.values()):
            if tab is not current and tab.screen_on():
                tab.screen_off_by_window()           # another tab shown: the player of the hidden one goes back
        if current is not None:
            self._show_title(current)

    def _step(self, step: int) -> None:
        count = self.tabs.count()
        if count:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % count)

    def _each(self, fn) -> None:
        """``fn`` of the tab shown."""
        current = self.tabs.currentWidget()
        if isinstance(current, Tab):
            fn(current)

    def _tab_menu(self, point) -> None:
        index = self.tabs.tabBar().tabAt(point)
        tab = self.tabs.widget(index) if index >= 0 else None
        if not isinstance(tab, Tab):
            return
        menu = QMenu(self)
        main = menu.addAction("Открыть в главном окне")
        main.setToolTip("Эта страница — в главном окне программы (там играет видео программы)")
        close = menu.addAction("Закрыть вкладку")
        others = menu.addAction("Закрыть другие вкладки")
        others.setEnabled(self.tabs.count() > 1)
        chosen = menu.exec(self.tabs.tabBar().mapToGlobal(point))
        menu.deleteLater()
        if chosen is main:
            self.to_main(tab)
        elif chosen is close:
            self.close_tab(self.tabs.indexOf(tab))
        elif chosen is others:
            for other in [self.tabs.widget(n) for n in range(self.tabs.count())]:
                if other is not tab:
                    self.close_tab(self.tabs.indexOf(other))

    def to_main(self, tab: Tab) -> None:
        """«Открыть в главном окне»: the tab's page in the main window (the video of the program plays there), the main
        window in front; the tab closes."""
        from webui import service
        bridge = service.bridge()
        page = getattr(bridge, "page", None)
        if page is None or not hasattr(page, "open_path"):
            return
        url = tab.view.url()
        path = url.path() + (f"?{url.query()}" if url.query() else "") + (f"#{url.fragment()}" if url.fragment() else "")
        page.open_path(path or "/posts")
        main = page.window()
        if main.isMinimized():
            main.setWindowState(main.windowState() & ~Qt.WindowState.WindowMinimized)
        main.show()
        main.raise_()
        main.activateWindow()
        self.close_tab(self.tabs.indexOf(tab))

    # ── the window ──────────────────────────────────────────────────────────────────────────────────────────────────
    def _place(self) -> None:
        """Where it was left this run; the first time — most of the screen, in the middle of it."""
        geometry = _PLACE.get("geometry")
        if geometry is not None and self.restoreGeometry(geometry):
            return
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1200, 800)
            return
        avail = screen.availableGeometry()
        width, height = max(800, int(avail.width() * 0.8)), max(500, int(avail.height() * 0.85))
        width, height = min(width, avail.width()), min(height, avail.height())
        self.resize(width, height)
        self.move(avail.left() + (avail.width() - width) // 2, avail.top() + (avail.height() - height) // 2)

    def show_first(self) -> None:
        if _PLACE.get("maximized"):
            self.showMaximized()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def bring_up(self) -> None:
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def screen_keys(self, on: bool) -> None:
        """A player of a tab on the whole screen: Esc is the window's meanwhile (the pages have their own Esc)."""
        self._esc.setEnabled(on or any(t.screen_on() for t in _TABS.values()))

    def toggle_fullscreen(self) -> None:
        """F11: the whole window on the full screen and back (maximized when it was)."""
        if self.isFullScreen():
            if self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()

    def changeEvent(self, event):  # noqa: N802 - Qt's name
        super().changeEvent(event)
        from PySide6.QtCore import QEvent
        if event.type() != QEvent.Type.WindowStateChange:
            return
        full = self.isFullScreen()
        self.tabs.tabBar().setVisible(not full)
        if not full:
            for tab in list(_TABS.values()):
                if tab.screen_on() and time.monotonic() - tab._screen_since > 1.0:
                    tab._screen_was = None           # nothing to give back: it is not full screen any more
                    tab.screen_off_by_window()

    def closeEvent(self, event):  # noqa: N802 - Qt's name
        """The window closes: its tabs' pages go — nothing else (the program, its server and the main window stay)."""
        global _WINDOW
        if not self.isFullScreen():
            _PLACE["geometry"] = self.saveGeometry()
            _PLACE["maximized"] = self.isMaximized()
        for index in reversed(range(self.tabs.count())):
            tab = self.tabs.widget(index)
            if isinstance(tab, Tab):
                _TABS.pop(tab.mark, None)
            self.tabs.removeTab(index)
            if tab is not None:
                tab.deleteLater()
        event.accept()
        if _WINDOW is self:
            _WINDOW = None                       # the next link opens a new one
        if self not in _DYING:
            _DYING.append(self)
            QTimer.singleShot(0, _dispose)       # destroyed once this event is over, in the window's thread
