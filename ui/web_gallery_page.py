"""The program's web pages (``webui``) inside the program window, shown by Qt WebEngine.

Links to other sites (post sources) open in the usual browser; «Скачать оригинал» asks where to save.
Without Qt WebEngine — or where it does not work (``ui/program_window.py``) — the pages open in the usual browser, and
this part of the window says so and offers its buttons (``browser=True``).
(v5.44.117) A page's own address asked for a new window or tab (the middle button, Ctrl+click, ``target=_blank``) — a
tab of the program's second window (``ui/web_tabs.py``), not this view.
"""
from __future__ import annotations

import logging
import threading
import time
import weakref

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStackedLayout, QVBoxLayout, QWidget

_LOG = logging.getLogger("local_booru.webui")

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    _WEBENGINE_ERROR = ""
except ImportError as exc:          # PySide6-Addons is not installed
    QWebEnginePage = QWebEngineProfile = QWebEngineView = None
    _WEBENGINE_ERROR = str(exc)

_LOCAL_HOSTS = {"127.0.0.1", "localhost"}
SCREEN_CLAIM_MS = 4000      # a page loaded while the window was full screen for a player: it says it has one by then
_WEB_SCHEMES = {"http", "https"}


def _gallery_url(url: QUrl) -> bool:
    """A page of this gallery (the local server)."""
    return url.scheme() in _WEB_SCHEMES and url.host() in _LOCAL_HOSTS


def _local(url: QUrl) -> bool:
    return url.scheme() in {"data", "blob", "about"} or _gallery_url(url)


def _open_outside(url: QUrl, settings: dict | None = None) -> None:
    """Web addresses go to the usual browser (the one chosen in the settings).  Nothing else does:
    a stored source like «someapp:payload» would start whatever program Windows has registered
    for that scheme."""
    from core.external_browser import browser_mode, is_web_url, open_external_browser_url
    text = url.toString()
    if not is_web_url(text):
        _LOG.warning("link not opened (scheme %r is not a web address): %s", url.scheme(), text[:200])
    elif browser_mode(settings) == "custom":
        open_external_browser_url(text, settings)
    else:
        QDesktopServices.openUrl(url)


if QWebEnginePage is not None:
    class _GalleryPage(QWebEnginePage):
        """Keeps the gallery inside the window and sends every other site to the usual browser."""

        settings_provider = staticmethod(lambda: {})

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if is_main_frame and not _local(url):
                _open_outside(url, self.settings_provider())
                return False
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)

        def createWindow(self, window_type):
            # target="_blank", a middle click, Ctrl+click, «Открыть ссылку в новой вкладке»: a throwaway page that
            # sends the address on; (v5.44.117) the middle button and Ctrl+click — a tab «in the background»
            background = window_type == QWebEnginePage.WebWindowType.WebBrowserBackgroundTab
            return _NewWindowPage(self.profile(), self, background=background)

    class _NewWindowPage(QWebEnginePage):
        """A gallery page asked for a new window: (v5.44.117) the gallery address opens in a tab of the program's
        second window (``ui/web_tabs.py`` — the same profile and cookies; before, in the same view: a browser outside
        has no session cookie and would show «откройте из программы»), any other site in the usual browser."""

        def __init__(self, profile, opener, *, background: bool = False):
            super().__init__(profile, opener)
            self._background = bool(background)

        def acceptNavigationRequest(self, url, _nav_type, _is_main_frame):
            if url.isEmpty() or url.scheme() == "about":
                return False                    # the blank start of the new window; the real address follows
            opener = self.parent()
            settings = opener.settings_provider if isinstance(opener, _GalleryPage) else None
            if _gallery_url(url) and isinstance(opener, QWebEnginePage):
                if not _open_in_tabs(url, background=self._background, settings_provider=settings):
                    opener.setUrl(url)          # no window of tabs: here, as before
            else:
                _open_outside(url, settings() if settings is not None else None)
            self.deleteLater()
            return False


def new_page(profile, parent, settings_provider):
    """A page of the program for a web view (``_GalleryPage``: its own addresses there, other sites in the usual
    browser, (v5.44.117) a new window — a tab of the window of tabs) with what every page of the window has: a
    request for a permission refused at once, a video started without a press — the main window's and (v5.44.117) a
    tab's (``ui/web_tabs.py``)."""
    page = _GalleryPage(profile, parent)
    page.settings_provider = settings_provider
    if hasattr(page, "permissionRequested"):                   # Qt 6.8 and later
        page.permissionRequested.connect(_refuse_permission)
    try:            # (v5.44) the program's own pages: an episode asked to play plays (as the program's own player)
        from PySide6.QtWebEngineCore import QWebEngineSettings
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    except (ImportError, AttributeError) as exc:
        _LOG.debug("autoplay of the window's pages not allowed: %s", exc)
    return page


def _open_in_tabs(url, *, background: bool, settings_provider) -> bool:
    """(v5.44.117) The program's address in a tab of its second window (``ui/web_tabs.open_tab``); False — not there."""
    try:
        from ui import web_tabs
        return web_tabs.open_tab(url, background=background, settings_provider=settings_provider)
    except Exception as exc:  # noqa: BLE001 - a window that could not be made: the page opens where it was asked
        _LOG.error("the window of tabs did not open: %s", exc, exc_info=True)
        return False


_PROFILE = None            # the gallery's one web profile for the program run
_DOWNLOADS = None          # weak reference to the gallery page that asks where a download goes


def _shared_profile(page: "WebGalleryPage"):
    """The gallery's web profile: one per program run, belonging to the application — it must outlive its pages
    (Qt warns and may lose cookies otherwise), and two profiles on one storage folder would fight over it (a window
    that failed to build leaves its profile behind).  «Скачать оригинал» is asked by the page made last."""
    global _PROFILE, _DOWNLOADS
    if _PROFILE is None:
        from core.paths import CACHE_DIR
        from PySide6.QtWidgets import QApplication
        storage = CACHE_DIR / "webview"
        storage.mkdir(parents=True, exist_ok=True)
        profile = QWebEngineProfile("LocalBooruGallery", QApplication.instance())
        profile.setPersistentStoragePath(str(storage))
        profile.setCachePath(str(storage / "cache"))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        profile.downloadRequested.connect(_download_requested)
        _PROFILE = profile
    _DOWNLOADS = weakref.ref(page)
    return _PROFILE


def profile():
    """(v5.44.117) The gallery's web profile of this run (None before the first page): the window of tabs
    (``ui/web_tabs.py``) shares it — the same cookies, the same session."""
    return _PROFILE


def _refuse_permission(permission) -> None:
    """A gallery page asked for a permission (the clipboard, notifications, the camera…): its pages need none.  Left
    unanswered, the request was never settled and the page waited for ever — ``navigator.clipboard.writeText`` of
    «Скопировать» did nothing until v5.23.1; refused, the page goes its other way at once (the pages copy through
    ``document.execCommand("copy")``, which the window allows: webui/static/booru.js ``copyText``)."""
    try:
        permission.deny()
    except Exception as exc:  # an old Qt or a request already gone: nothing waits on it then
        _LOG.debug("permission request not answered: %s", exc)


def _full_player(on: bool) -> None:
    """The full-size player opened or left: work in the background that waits while a video plays knows it
    (``RELAY.playing``)."""
    try:
        from webui.video_relay import RELAY
    except ImportError:
        return
    RELAY.set_full(on)


def _download_requested(download) -> None:
    page = _DOWNLOADS() if _DOWNLOADS is not None else None
    if page is None:
        download.cancel()
        return
    owner = None
    try:                       # (v5.44.117) asked by a page of the window of tabs: the question over that window
        from ui import web_tabs
        owner = web_tabs.window_of(download.page())
    except (AttributeError, RuntimeError, ImportError) as exc:
        _LOG.debug("the page of a download not known: %s", exc)
    page._on_download(download, owner)


class WebGalleryPage(QWidget):
    """The program window's content; ``open_path("/posts?tags=...")`` shows any address of its pages."""

    def __init__(self, main_window, *, browser: bool = False, why: str = ""):
        # the window is its parent from the start: a page left without one would be destroyed by the garbage
        # collector, in whatever thread it runs — and a web view destroyed outside the window's thread ends the program
        super().__init__(main_window if isinstance(main_window, QWidget) else None)
        self.mw = main_window
        self._view = None
        self._loaded = False
        self._player = None
        self._inline = None              # the video played inside the page (ui/video_inline.py), made when first asked
        self._screen = False
        self._screen_was = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if browser or QWebEngineView is None:
            self._browser_panel(lay, why)
            if QWebEngineView is None:
                _LOG.warning("Qt WebEngine is not available: %s", _WEBENGINE_ERROR)
            return
        self._profile = _shared_profile(self)
        self._view = QWebEngineView(self)
        page = new_page(self._profile, self._view, lambda: getattr(self.mw, "settings", {}) or {})
        self._view.setPage(page)
        from ui.qt_language import hide_dead_actions
        hide_dead_actions(page)                 # the menu over a page: only what works in this window
        self._view.urlChanged.connect(self._on_url_changed)
        # The web view and, while a video plays, the native player take turns in the same place
        # (a native video window over a web view misbehaves, so they never overlap).
        holder = QWidget(self)
        self._layers = QStackedLayout(holder)
        self._layers.addWidget(self._view)
        # ⛶ of the page's player: the window is full screen for it (booru.js ``setFull`` puts the player over the page)
        self._screen_since = 0.0
        self._screen_was = None          # the window's state before (None — it was full screen already: F11)
        self._f11_was_maximized = False  # F11: the window was maximized before (back so)
        self._screen_claimed = False     # the page shown now said it has the player on the whole screen
        self._view.loadStarted.connect(self._load_started)
        self._view.loadFinished.connect(self._load_finished)
        lay.addWidget(holder)
        # Browser keys the embedded view lacks (the mouse back/forward buttons it handles itself).
        for keys, slot in (("F5", self._view.reload), ("Ctrl+R", self._view.reload),
                           ("Alt+Left", self._view.back), ("Alt+Right", self._view.forward),
                           ("F11", self.toggle_fullscreen)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        # Esc of the player on the whole screen: the window's own key (the page may not get it); only meanwhile —
        # the pages have their own Esc otherwise (dialogs, the reader, the viewer of the parser)
        self._screen_esc = QShortcut(QKeySequence("Esc"), self)
        self._screen_esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._screen_esc.activated.connect(self._screen_off_by_window)
        self._screen_esc.setEnabled(False)

    def _browser_panel(self, lay, why: str) -> None:
        """The pages are in the usual browser: what this window is for now, and its buttons."""
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        title = QLabel("Local Booru работает в браузере")
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        font.setBold(True)
        title.setFont(font)
        lay.addWidget(title)
        text = (why + "\n\n" if why else "") + (
            "Это окно держит программу запущенной и отвечает страницам (выбор папок, вопросы); закроете его — "
            "программа закончит работу.")
        note = QLabel(text)
        note.setObjectName("lb_browser_note")
        note.setWordWrap(True)
        lay.addWidget(note)
        buttons = QHBoxLayout()
        go = QPushButton("Открыть Local Booru в браузере")
        go.setObjectName("lb_open_browser")
        go.setDefault(True)
        go.clicked.connect(lambda: self.open_in_browser("/posts"))
        buttons.addWidget(go)
        if QWebEngineView is not None:
            window = QPushButton("Открыть в окне программы")
            window.setObjectName("lb_back_to_window")
            window.setToolTip("Программа начнёт заново и попробует показать страницы в своём окне")
            window.clicked.connect(self._back_to_window)
            buttons.addWidget(window)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        lay.addStretch(1)

    def _back_to_window(self) -> None:
        """«Открыть в окне программы»: the window again from now on, the program starts again at once."""
        settings = getattr(self.mw, "settings", None)
        if isinstance(settings, dict) and settings.get("ui_shell") == "browser":
            settings["ui_shell"] = "web"
            try:
                self.mw.save_settings()
            except OSError as exc:
                _LOG.warning("the window not chosen again: %s", exc)
                return
        from core import app_restart
        app_restart.request()                    # app.py starts the program again once this window has closed
        self.mw.close()

    # ── called by the program window ─────────────────────────────────────────
    @property
    def view(self):
        """The web view (None without Qt WebEngine)."""
        return self._view

    def start(self) -> None:
        """The server, the bridge and the first page now (the pages ask the window for dialogs, and «Подписки» look
        for the bridge, before the window is first shown).  The pages in the browser: it opens them once the program
        is up."""
        self._server()
        if self._view is None:
            QTimer.singleShot(0, lambda: self.open_in_browser("/posts"))
        self._ensure_loaded()

    def showEvent(self, event):
        self._ensure_loaded()
        super().showEvent(event)
        if self._view is not None:
            self._view.setFocus()       # arrow keys and typing go to the gallery at once

    def hideEvent(self, event):
        # The window minimized or closing: the video stops (it would play on unseen and keep its file open).
        if self.player_visible():
            self._show_page()
        self._stop_inline()
        if not event.spontaneous():            # closing; a minimized window keeps its full screen as it was
            self._screen_leave()
        super().hideEvent(event)

    def release_media(self) -> None:
        """Before the program moves or deletes files: the players let go of their video and the
        gallery server stops sending files (on Windows an open file cannot be moved)."""
        if self._player is not None:
            self._player.stop(now=True)          # the files are moved right after this call
            self._show_page()
        self._stop_inline(now=True)
        from webui.server import FILE_STREAMS
        FILE_STREAMS.cut()

    def open_path(self, path: str) -> None:
        server = self._server()
        if self._view is None:
            self.open_in_browser(path)
            return
        self._loaded = True
        self._view.load(QUrl(server.login_url(path)))

    def open_in_browser(self, path: str = "/posts") -> None:
        QDesktopServices.openUrl(QUrl(self._server().login_url(path)))

    def toggle_fullscreen(self) -> None:
        """F11: the whole window on the full screen and back — back maximized when it was (it came back in its own
        size, smaller than before).  Not for the small window of the pages in the browser."""
        if self._view is None:
            return
        window = self.window()
        if window.isFullScreen():
            was = self._screen_was if self._screen and self._screen_was is not None else None
            maximized = bool(was & Qt.WindowState.WindowMaximized) if was is not None else self._f11_was_maximized
            if maximized:                    # (the page's player had the screen: back as the window was before it)
                window.showMaximized()
            else:
                window.showNormal()
        else:
            self._f11_was_maximized = window.isMaximized()
            window.showFullScreen()

    # ── ⛶: the page's own player over the whole screen ─────────────────────────
    def video_screen(self, on: bool) -> None:
        """The page put its player over itself (``on``) or took it back: the window goes full screen for it — and
        back to the state it had (maximized or not; full screen by F11 stays so)."""
        if not on:
            self._screen_leave()
            return
        if self._view is None:                   # the pages in the browser: the browser has its own full screen
            return
        self._screen_claimed = True
        if self._screen:
            return
        window = self.window()
        self._screen = True
        self._screen_since = time.monotonic()
        self._screen_esc.setEnabled(True)
        if window.isFullScreen():
            self._screen_was = None
        else:
            self._screen_was = window.windowState()
            window.showFullScreen()

    def screen_on(self) -> bool:
        return self._screen

    def _screen_leave(self) -> None:
        if not self._screen:
            return
        self._screen = False
        self._screen_esc.setEnabled(False)
        was, self._screen_was = self._screen_was, None
        window = self.window()
        if was is not None and window.isFullScreen():
            if was & Qt.WindowState.WindowMaximized:
                window.showMaximized()
            else:
                window.showNormal()

    def _screen_off_by_window(self) -> None:
        """Esc on the whole screen, or the window left full screen by itself (F11): the page's player is put back."""
        if not self._screen:
            return
        self._screen_leave()
        if self._view is not None:
            self._view.page().runJavaScript(
                "window.LocalBooru && LocalBooru.nativeVideo && LocalBooru.nativeVideo.screen(false)")

    def window_state_changed(self) -> None:
        """(the window, its state changed) full screen left by other means than the page: the page is told.  Right
        after going full screen a window manager may pass through other states: looked at again a moment later."""
        if not self._screen or self.window().isFullScreen():
            return
        if time.monotonic() - self._screen_since < 1.0:
            QTimer.singleShot(1100, self.window_state_changed)
            return
        self._screen_was = None                     # nothing to give back: it is not full screen any more
        self._screen_off_by_window()

    def _load_started(self) -> None:
        self._screen_claimed = False

    def _load_finished(self, _ok: bool) -> None:
        """A page loaded while the window was full screen for a player: it goes on so (the next episode says so at
        once — booru.js) — or, with no player saying so, the window gets its usual size back."""
        if self._screen:
            QTimer.singleShot(SCREEN_CLAIM_MS, self._screen_check)

    def _screen_check(self) -> None:
        if self._screen and not self._screen_claimed:
            self._screen_leave()

    # ── the video player ─────────────────────────────────────────────────────
    def play_video(self, path: str, title: str = "", image_id: int | None = None, *, prefer: dict | None = None,
                   series: str = "", top: str = "", episode: str = "", start_at: float = 0.0, paused: bool = False,
                   resume: float = 0.0, loop: bool = True, memory: str = "") -> None:
        """Play a video in the native player in place of the page (Esc goes back to the page — and its own player
        goes on from the moment this one was left: ``_player_left``).
        ``image_id``: the post that asked; if another page is shown by now, the request is stale.  An episode of
        the tab «Видео»: ``prefer`` — the voice-over and subtitles chosen for its ``series`` (or on the page just now),
        a new choice is kept; ``top`` — the series' folder (its folders of subtitles); ``episode`` — what was watched
        of it is kept, it is played once.  ``start_at``, ``paused``, ``resume`` — the page's player's moment, whether
        it was paused, the furthest place reached («Продолжить просмотр»).  (v5.44.121) ``loop`` False — a long video
        of the library: played once, its place kept under ``memory`` (its key in the one memory)."""
        if self._view is None:
            return
        if image_id is not None and self.current_path() != f"/post/{int(image_id)}":
            _LOG.debug("video of post %s not played: the page is %s now", image_id, self.current_path())
            return
        self._stop_inline()                     # the full-size player instead of the one in the page
        if self._player is None:
            from ui.video_player import VideoPlayer
            self._player = VideoPlayer(self, self.mw.settings, getattr(self.mw, "save_settings", None))
            self._player.left.connect(self._player_left)
            self._player.navigate.connect(self._player_navigate)
            self._layers.addWidget(self._player)
        self._layers.setCurrentWidget(self._player)
        _full_player(True)
        on_choice = on_progress = None
        settings = self.mw.settings
        if series:                                  # (v5.44.121) the one memory of the series (its folder and site)
            def on_choice(kind: str, value, series=series) -> None:
                from core import video_library     # a JSON file written: not in the window's thread
                threading.Thread(target=video_library.choose_tracks, args=(settings, series, kind, value),
                                 name="video-tracks", daemon=True).start()
            if episode:
                def on_progress(position: float, length: float, ended: bool, series=series, episode=episode) -> None:
                    from core import video_library
                    threading.Thread(target=video_library.record_ids,
                                     args=(settings, series, episode, position, length),
                                     kwargs={"ended": ended}, name="video-progress", daemon=True).start()
        elif memory and not loop:                   # a long video of the library: its place
            def on_progress(position: float, length: float, ended: bool, memory=memory) -> None:
                from core import video_library
                threading.Thread(target=video_library.PROGRESS.record, args=(memory, "1", position, length),
                                 kwargs={"ended": ended}, name="video-progress", daemon=True).start()
        self._player_episode = bool(episode)
        self._player.play(path, title, prefer=prefer, on_choice=on_choice, top=top or None, start_at=start_at,
                          paused=paused, resume=resume, on_progress=on_progress, loop=loop and not episode)

    def player_visible(self) -> bool:
        return self._player is not None and self._layers.currentWidget() is self._player

    def inline_video(self, payload: dict) -> None:
        """The video of a post played inside the page: «start» (the page of that post is still shown) or a command
        of the page's buttons.  Without Qt Multimedia the page hears why and offers the other ways."""
        from webui.video_relay import RELAY
        token = str(payload.get("token") or "")
        if payload.get("cmd") == "start":
            image_id = payload.get("id")
            if payload.get("episode") or payload.get("stream"):
                # an episode of the tab «Видео» — (v5.44.121) of the folder or of its site: the one page of its series
                # is still shown (``/videos/s/<id>``, or a title of a site — ``/videos/watch``)
                gone = not self.current_path().startswith(("/videos/s/", "/videos/watch"))
            else:
                gone = image_id is not None and self.current_path() != f"/post/{int(image_id)}"
            if self._view is None or gone:
                RELAY.end(token)                   # the page is gone by now
                return
            if self.player_visible():
                self._show_page()
        if self._inline is None:
            try:
                from ui.video_inline import InlineVideo, available
                if not available():
                    raise RuntimeError("в этой установке нет Qt Multimedia")
                self._inline = InlineVideo(self, self.mw.settings, getattr(self.mw, "save_settings", None))
            except Exception as exc:  # no Qt Multimedia, a broken backend: the page offers the other players
                _LOG.warning("inline video player not made: %s", exc)
                RELAY.set_state(token, error=f"Встроенный проигрыватель не запустился: {exc}")
                return
        self._inline.handle(payload)

    def _stop_inline(self, now: bool = False) -> None:
        if self._inline is not None:
            self._inline.stop(now=now)

    def _show_page(self) -> None:
        if self._player is not None:
            self._player.stop()
        _full_player(False)
        self._layers.setCurrentWidget(self._view)
        self._view.setFocus()

    def _player_left(self, position: float, length: float, playing: bool, ended: bool) -> None:
        """The full-size player was left (Esc, ✕, an episode's end): the page's own player goes on from that moment —
        playing if it was playing; an episode played to its end — the page goes on to the next one (full size)."""
        self._show_page()
        if self._view is None:
            return
        self._view.page().runJavaScript(
            "window.LocalBooru && LocalBooru.nativeVideo && LocalBooru.nativeVideo.resume("
            f"{max(0.0, float(position)):.2f}, {max(0.0, float(length)):.2f}, {'true' if playing else 'false'}, "
            f"{'true' if ended else 'false'})")

    def _player_navigate(self, step: int) -> None:
        """← / → in the player: the previous / next post of the same search (a video plays again); of an episode —
        the previous / next episode, full size again."""
        self._show_page()
        key = "ArrowRight" if step > 0 else "ArrowLeft"
        full = "u.searchParams.set('full', '1'); " if getattr(self, "_player_episode", False) else ""
        self._view.page().runJavaScript(
            f"(() => {{ const a = document.querySelector('a[data-key=\"{key}\"]'); if (!a) return; "
            f"const u = new URL(a.href); {full}location.href = u.href; }})()")

    # ── internals ────────────────────────────────────────────────────────────
    def _server(self):
        from webui import service
        server = service.ensure_started(lambda: self.mw.settings)
        if service.bridge() is None:             # (the pages in the browser too: settings are saved by the window)
            from ui.web_bridge import AppBridge
            service.set_bridge(AppBridge(self.mw, self))
        return server

    def current_path(self) -> str:
        """The path of the page shown now ("/posts", "/manga/g/…"), "" before the first load."""
        return self._view.url().path() if self._view is not None else ""

    def _ensure_loaded(self) -> None:
        if self._loaded or self._view is None:
            return
        self.open_path("/posts")

    def _on_url_changed(self, _url) -> None:
        """Another page: the video of the old one stops."""
        if self.player_visible():
            self._show_page()
        self._stop_inline()

    def _on_download(self, download, owner=None) -> None:
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog
        suggested = Path(download.downloadDirectory()) / download.downloadFileName()
        target, _ = QFileDialog.getSaveFileName(owner or self, "Сохранить оригинал", str(suggested))
        if not target:
            download.cancel()
            return
        download.setDownloadDirectory(str(Path(target).parent))
        download.setDownloadFileName(Path(target).name)
        download.accept()
