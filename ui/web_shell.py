"""The program window: the web pages (``webui``) fill it, and the window gives them what a browser cannot — dialogs,
the clipboard, the video player, dragging files out (``ui/web_bridge.py``, through ``WebGalleryPage``) — and runs what
the program does by itself (``core/background_duties.py``: the archive of «Новые», the trash's days, the automatic check
of subscriptions, the idle checkpoint of the base).

Where Qt WebEngine does not work (``ui/program_window.py`` decides: «Открывать» on «Настройки», key ``ui_shell``, or a
failure), the window is small (``browser=True``): the pages open in the usual browser, and the window keeps the program
running, asks its questions and opens its dialogs for them; closing it ends the program.
The program's exit work (backup, checkpoint, thumbnails) stays in ``app.py``: it needs only ``.settings``.
(v5.44.117) A page's link opened in a new window or tab (the middle button…) — the second window, of tabs
(``ui/web_tabs.py``); it closes with this one, its own closing touches nothing here.
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

_LOG = logging.getLogger("local_booru.window")
TITLE = "Local Booru"
RENDER_RETRY_SECONDS = 120          # the page's renderer dying twice within this — the window offers another way
STARTED_AFTER_MS = 3000             # a page in and the window shown this long: the new window works here


def can_show_web() -> bool:
    """Qt WebEngine is installed (the new window shows nothing without it)."""
    from ui.web_gallery_page import QWebEngineView
    return QWebEngineView is not None


class WebShell(QMainWindow):
    def __init__(self, *, browser: bool = False, why: str = ""):
        """``browser`` — the pages in the usual browser, this window small (``why`` — what it says about it)."""
        super().__init__()
        self.browser = bool(browser)
        self._why = str(why or "")
        self._closing = False
        self._plan_box = None
        self._plan_key = None
        self._asking = None
        self._render_deaths: list = []
        self._offered = False
        self._page_in = False
        self._confirmed = False
        self.web_gallery_page = None
        try:
            self._build()
        except BaseException:
            self._abandon()
            _delete_now(self)          # never left to the garbage collector: it may run in another thread, and a
            raise                      # web view destroyed there takes the whole program down (Chromium's check)

    def _build(self) -> None:
        from core.app_context import AppContext
        from core.paths import APP_ICON_FILE
        self.context = AppContext()
        self.settings = self.context.settings            # the one live settings dict of this run
        from core.maintenance_coordinator import install_qt_activity_filter
        install_qt_activity_filter()                      # the idle checkpoint waits for a pause of the person
        if self.browser:
            self.setWindowTitle(TITLE + " — в браузере")
            self.resize(620, 360)
        else:
            self.setWindowTitle(TITLE)
            self.setMinimumSize(800, 500)
            self.resize(*self._saved_size())
        if APP_ICON_FILE.exists():
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(APP_ICON_FILE)))
        from ui.web_gallery_page import WebGalleryPage
        self.web_gallery_page = WebGalleryPage(self, browser=self.browser, why=self._why)
        self.setCentralWidget(self.web_gallery_page)
        view = self.web_gallery_page.view
        if view is not None:
            view.titleChanged.connect(self._title_changed)
            view.loadFinished.connect(self._page_loaded)
            view.page().renderProcessTerminated.connect(self._render_died)
        self.web_gallery_page.start()                     # the server and the bridge now, not at the first show
        self._asking = QTimer(self)
        self._asking.setInterval(1000)
        self._asking.timeout.connect(self._ask_large_download)
        self._asking.start()
        from core import background_duties
        from webui import data
        from webui.server import FILE_STREAMS
        try:
            background_duties.start(lambda: self.settings, hold=lambda paths: FILE_STREAMS.holding(*paths),
                                    changed=data.TAG_COUNTS.invalidate)
        except Exception as exc:  # the program opens all the same; what it does by itself waits for the next start
            _LOG.error("background duties did not start: %s", exc, exc_info=True)

    def _abandon(self) -> None:
        """The window failed to build: what it started is stopped, so the window in the browser starts clean (its own
        settings for the web server, its own bridge)."""
        from core import background_duties
        from webui import service
        if self._asking is not None:
            self._asking.stop()
        background_duties.stop()
        bridge = service.bridge()
        if bridge is not None and getattr(bridge, "mw", None) is self:
            service.set_bridge(None)
        server, mine = service.running(), getattr(self, "settings", None)
        if server is not None and mine is not None:
            try:
                reads_mine = server.settings_provider() is mine
            except Exception as exc:  # a provider that fails is not this window's to keep
                _LOG.warning("web server settings not read while undoing the window: %s", exc)
                reads_mine = True
            if reads_mine:
                service.stop()                  # its pages would read this window's settings

    # ── what other parts ask of a window ────────────────────────────────────
    def save_settings(self) -> None:
        self.context.save_settings()

    def release_open_media_handles(self) -> None:
        """Before the program moves or deletes files: the player and the web server let go of them."""
        self.web_gallery_page.release_media()

    # ── the window ─────────────────────────────────────────────────────────
    def _saved_size(self) -> tuple[int, int]:
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        max_w, max_h = (avail.width(), avail.height()) if avail is not None else (1920, 1080)
        try:
            width, height = int(self.settings.get("window_w", 1440)), int(self.settings.get("window_h", 880))
        except (TypeError, ValueError):
            width, height = 1440, 880
        return max(800, min(width, max_w)), max(500, min(height, max_h))

    def _title_changed(self, title: str) -> None:
        """The page's title; a page without one gives its address — never shown (it carries the session key)."""
        text = str(title or "")
        if not text or "127.0.0.1" in text or "localhost" in text or "://" in text:
            text = TITLE
        self.setWindowTitle(text)

    def _page_loaded(self, ok: bool) -> None:
        """A page is in.  The first one, shown for a few seconds, says that Qt WebEngine works on this computer
        (``program_window`` stops waiting for it): it can still take the program down at the first paint."""
        if not ok or self._page_in:
            return
        self._page_in = True
        QTimer.singleShot(STARTED_AFTER_MS, self._confirm_started)

    def _confirm_started(self) -> None:
        if self._confirmed or self._closing:
            return
        if not self.isVisible():
            QTimer.singleShot(STARTED_AFTER_MS, self._confirm_started)    # not shown yet: once it has been
            return
        self._confirmed = True
        from ui import program_window
        program_window.started()

    def _render_died(self, status, code) -> None:
        """The page's renderer process ended: the page is blank.  Once — the page loads again; twice within
        ``RENDER_RETRY_SECONDS`` — the window offers another way (the settings are out of reach in a blank page)."""
        from PySide6.QtWebEngineCore import QWebEnginePage
        if status == QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus or self._closing:
            return
        now = time.monotonic()
        self._render_deaths = [t for t in self._render_deaths if now - t < RENDER_RETRY_SECONDS] + [now]
        _LOG.warning("the page's render process ended (%s, exit code %s)", getattr(status, "name", status), code)
        if len(self._render_deaths) < 2:
            QTimer.singleShot(500, self.web_gallery_page.view.reload)
            return
        self._offer_other_way()

    def _offer_other_way(self) -> None:
        """The pages keep failing: the program starts again drawing them without the graphics card — or, when it draws
        without it already, with its pages in the usual browser (``ui/program_window.py``)."""
        if self._offered:
            return
        self._offered = True
        from ui import program_window
        if program_window.drawing_with_gpu():
            text = ("Страницы программы не показываются: встроенный браузер (Qt WebEngine) падает. Часто дело в "
                    "видеокарте — программа может начать заново и рисовать страницы без неё. Вернуть как было: "
                    "«Настройки → Внешний вид → Окно программы → Рисовать страницы».")
            label, change = "Перезапустить без видеокарты", {"webview_gpu": "off"}
        else:
            text = ("Страницы программы не показываются: встроенный браузер (Qt WebEngine) падает и без видеокарты. "
                    "Программа может начать заново и открывать свои страницы в обычном браузере. Вернуть окно: "
                    "«Настройки → Внешний вид → Окно программы → Открывать».")
            label, change = "Перезапустить и открывать в браузере", {"ui_shell": "browser"}
        box = QMessageBox(QMessageBox.Warning, "Окно программы", text, QMessageBox.NoButton, self)
        other = box.addButton(label, QMessageBox.ButtonRole.AcceptRole)
        stay = box.addButton("Остаться", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(stay)                # a stray Enter keeps the window as it is
        box.setEscapeButton(stay)
        box.setAttribute(Qt.WA_DeleteOnClose)

        def chosen(_result) -> None:
            if box.clickedButton() is not other:
                self._offered = False                 # a third failure asks again
                self._render_deaths.clear()
                self.web_gallery_page.view.reload()
                return
            self.settings.update(change)
            try:
                self.save_settings()
            except OSError as exc:
                _LOG.warning("the choice of another way not saved: %s", exc)
                return
            from core import app_restart
            app_restart.request()                 # app.py starts the program again once it has closed
            self.close()
        box.finished.connect(chosen)
        box.show()

    def show_as_saved(self) -> None:
        """Shown as the person left it: maximized — the default, and when its own size takes the whole screen (a
        window «stretched» to the screen had its title bar and bottom beyond it) — or in its own size, whole on the
        screen (``_clamp_to_screen``).  The small window of the pages in the browser — in its own size."""
        if self.browser:
            self.show()
        elif self._wants_maximized():
            self.showMaximized()
        else:
            self.show()

    def _wants_maximized(self) -> bool:
        if self.settings.get("window_maximized", True):
            return True
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return False
        avail = screen.availableGeometry()
        return self.width() >= 0.95 * avail.width() and self.height() >= 0.9 * avail.height()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._clamp_to_screen)
        QTimer.singleShot(400, self._clamp_to_screen)     # Windows tells the frame (the title bar) a moment later

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and not self.browser:
            if self.web_gallery_page is not None:
                self.web_gallery_page.window_state_changed()   # a player on the whole screen goes back to its page
            if not self.isMinimized() and not self.isFullScreen():
                self.settings["window_maximized"] = self.isMaximized()     # the next start opens the same way
            if not self.isMinimized():
                QTimer.singleShot(150, self._clamp_to_screen)

    def _clamp_to_screen(self) -> None:
        """The whole window on the screen it is on, its frame too (a size saved on a bigger screen; a size of the
        whole screen with the title bar over it).  ``resize`` takes the inner size: the frame is taken off."""
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        if screen is None or self.isMaximized() or self.isFullScreen() or self.isMinimized():
            return
        avail = screen.availableGeometry()
        inner, frame = self.geometry(), self.frameGeometry()
        margin_w, margin_h = frame.width() - inner.width(), frame.height() - inner.height()
        width, height = min(frame.width(), avail.width()), min(frame.height(), avail.height())
        x = max(avail.left(), min(frame.x(), avail.left() + avail.width() - width))
        y = max(avail.top(), min(frame.y(), avail.top() + avail.height() - height))
        if (frame.x(), frame.y()) != (x, y):
            self.move(x, y)
        if (frame.width(), frame.height()) != (width, height):
            self.resize(max(1, width - margin_w), max(1, height - margin_h))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.browser or self.isMaximized() or self.isMinimized() or self.isFullScreen():
            return                     # (the small window of the pages in the browser keeps no size)
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            if self.width() <= avail.width() and self.height() <= avail.height():
                self.settings["window_w"], self.settings["window_h"] = self.width(), self.height()

    def closeEvent(self, event):
        """Close at once: the work of the exit (jobs stopping, backup, checkpoint) goes on in ``app.py`` after
        the window is gone, so Windows never calls it «not responding»."""
        if self._closing:
            event.accept()
            return
        self._closing = True
        if self._asking is not None:
            self._asking.stop()
        self._close_plan_box()
        self.hide()
        from ui import web_tabs
        web_tabs.close_all()                  # (v5.44.117) the window of tabs ends with the program's window
        from core import background_duties
        from core.shutdown import begin_shutdown
        from ui import program_window
        background_duties.stop()
        begin_shutdown()
        program_window.started()              # closed by the person: this window did start
        try:
            self.save_settings()
        except OSError as exc:              # the size of the window is not worth a stuck exit
            _LOG.warning("settings not saved at close: %s", exc)
        event.accept()

    # ── «Большая загрузка» wherever the person is ──────────────────────────
    def _ask_large_download(self) -> None:
        """A check of subscriptions waits for «download this much?»: the page «Подписки» asks it itself; on any
        other page (and for the automatic check, which no page started) the window asks.  An answer given on
        the page closes the window's question; so does a new question (the old one is gone)."""
        from core import subscription_jobs
        job = subscription_jobs.current()
        if job is None or job.status != "waiting":
            self._close_plan_box()
            return
        plan = job.snapshot(after=10 ** 12, limit=1)["plan"]      # the question only, no journal lines
        key = (job.id, plan.get("seq"))
        if not plan or key == self._plan_key:
            return
        self._close_plan_box()                   # the box of an earlier question, if one is still open
        if self.web_gallery_page.current_path().startswith("/subs"):
            return                               # «Подписки» asks it; leaving it unanswered, the window asks
        self._plan_key = key
        self._show_plan(job, plan)

    def _show_plan(self, job, plan: dict) -> None:
        """The question in a box of its own that does not take the keyboard: typing in a page goes on, and no
        Enter or letter meant for the page answers it; the only button the keyboard could reach says «no»."""
        from core.preflight import format_bytes
        name = str(plan.get("name") or "")
        text = (f"Найдено файлов для загрузки: {int(plan.get('files', 0) or 0)}{' — ' + name if name else ''}\n"
                f"Известный размер: {format_bytes(plan.get('known_bytes', 0))} для "
                f"{int(plan.get('known_files', 0) or 0)} файл(ов)\n"
                f"Свободно на диске: {format_bytes(plan.get('disk_free', 0))}\n\n"
                "Загрузка может занять много времени из-за ограничений сайтов. "
                "При временных ошибках программа будет ждать и продолжать позже.")
        if plan.get("not_enough_space"):
            text += "\n\nВНИМАНИЕ: известный размер уже превышает свободное место с резервом."
        box = QMessageBox(QMessageBox.Question, "Большая загрузка", text + "\n\nСкачать?", QMessageBox.NoButton, self)
        yes = box.addButton("Скачать", QMessageBox.ButtonRole.YesRole)
        no = box.addButton("Не скачивать", QMessageBox.ButtonRole.NoRole)
        box.setDefaultButton(no)
        box.setEscapeButton(no)                    # × is «no», as before
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.setAttribute(Qt.WA_ShowWithoutActivating)
        box.setWindowModality(Qt.WindowModality.NonModal)     # the pages stay usable while it waits

        def closed(_result) -> None:
            if self._plan_box is box:
                self._plan_box = None
            if box.property("lb_answered_elsewhere"):
                return
            job.decide(box.clickedButton() is yes, seq=plan.get("seq"))
        box.finished.connect(closed)
        self._plan_box = box
        box.show()
        QApplication.alert(self)                   # the taskbar button asks for a look when the window is behind

    def _close_plan_box(self) -> None:
        if self._plan_box is not None:
            box, self._plan_box = self._plan_box, None
            box.setProperty("lb_answered_elsewhere", True)
            box.close()


def _delete_now(widget) -> None:
    """Destroy a Qt object here, in the window's thread, now."""
    try:
        import shiboken6
    except ImportError:              # a stand-in Qt (the checks without PySide6)
        return
    try:
        if shiboken6.isValid(widget):
            shiboken6.delete(widget)
    except RuntimeError as exc:
        _LOG.warning("the window that failed to build was not destroyed: %s", exc)
