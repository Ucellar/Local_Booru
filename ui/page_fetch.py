"""A web page opened by the program window's browser for «Описание с сайта» (``core/video_meta_import.py``) when the
site does not give it to a plain request: Cloudflare and DDoS-Guard first run a check in the visitor's browser and
then let it through — the window's Chromium runs that check as any browser does, with the gallery's own profile
(what the site set after the check is kept for the next time).

Out of sight: the page is never shown.  It ends by itself (``TIMEOUT``) and is destroyed explicitly — a web page left
to the garbage collector can end the program (CLAUDE.md, rule 15).  The task waiting for it gets ``box["html"]`` and
``box["url"]``, or ``box["error"]``, and then ``done``.

(v5.44.73) «Скачать манхву» (``core/site_browser.py``) asks it too: ``guard(html, url)`` — the page is still the site's
check (com-x.life's is no Cloudflare's: it sends the browser to «/_c?t=…» first); ``cookies`` — the box also gets the
cookies of the page's site and the browser's User-Agent (``box["cookies"]``, ``box["agent"]``): the program's own
requests to that site go with them; ``visible`` — the page is in a view that is never put on the screen
(``WA_DontShowOnScreen``): a page with no view is «hidden» to its scripts, with a screen of 0×0 and no animation frames —
what a check for robots looks at (measured in Qt 6.11: «hidden», 0×0, no frames; in such a view — «visible», the
screen's size, 60 frames a second).
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage

_LOG = logging.getLogger("local_booru.video")

TIMEOUT = 45.0                 # seconds for the page and the site's check together
SETTLE = 1.5                   # seconds after a load before the page is read (a check loads the page again)


class PageFetch(QObject):
    def __init__(self, profile, url: str, box: dict, done, parent=None, guard=None, cookies: bool = False,
                 visible: bool = False):
        super().__init__(parent)
        self._url = url
        self._box = box
        self._done = done
        self._finished = False
        self._loaded_ok = False
        self._guard = guard
        self._profile = profile
        self._host = (urlparse(url).hostname or "").lower()
        self._cookies: dict = {}
        self._store = profile.cookieStore() if cookies else None
        self._page = QWebEnginePage(profile, self)
        self._page.loadFinished.connect(self._loaded)
        self._view = None
        if visible:
            from PySide6.QtCore import Qt
            from PySide6.QtWebEngineWidgets import QWebEngineView
            self._view = QWebEngineView()                 # no parent: destroyed by _finish, never by the collector
            self._view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            self._view.setPage(self._page)
            self._view.resize(1280, 860)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._read)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._give_up)

    def start(self) -> None:
        self._timeout.start(int(TIMEOUT * 1000))
        if self._view is not None:
            self._view.show()                             # «shown» to Qt and to the page — not on the screen
        if self._store is not None:
            self._store.cookieAdded.connect(self._cookie)
            self._store.loadAllCookies()                 # what the site set before: told again, as «added»
        self._page.load(QUrl(self._url))

    def _cookie(self, cookie) -> None:
        """(v5.44.73) A cookie of the page's site (its own or its hosts' — «img.com-x.life» of «com-x.life»)."""
        from core.site_browser import related
        try:
            domain = str(cookie.domain() or "").lower() or self._host
            name = bytes(cookie.name()).decode("latin-1")
            value = bytes(cookie.value()).decode("latin-1")
            path = str(cookie.path() or "/")
        except (RuntimeError, TypeError, ValueError) as exc:
            _LOG.debug("page fetch: a cookie not read: %s", exc)
            return
        if name and related(domain, self._host):
            self._cookies[(domain, path, name)] = {"name": name, "value": value, "domain": domain, "path": path}

    def _loaded(self, ok: bool) -> None:
        if self._finished:
            return
        self._loaded_ok = bool(ok)
        self._settle.start(int(SETTLE * 1000))      # every load starts it again: a check that reloads is waited for

    def _read(self) -> None:
        if not self._finished:
            self._page.toHtml(self._got)

    def _still_check(self, html: str) -> bool:
        if self._guard is not None:
            try:
                return bool(self._guard(html, self._page.url().toString()))
            except Exception as exc:  # noqa: BLE001 - the asker's own test: its fault must not leave it waiting
                _LOG.warning("page fetch: the page not told from a check: %s", exc)
                return False
        from core.video_meta_import import guard_of
        return bool(guard_of(200, html))

    def _got(self, html) -> None:
        if self._finished:
            return
        html = str(html or "")
        if self._still_check(html):
            return                                  # still the check: its script loads the page again
        if not self._loaded_ok:
            self._finish(error="Браузер окна программы не открыл страницу (нет связи или неверный адрес)")
            return
        self._finish(html=html, url=self._page.url().toString())

    def _give_up(self) -> None:
        if self._finished:
            return
        self._page.toHtml(self._got_late)

    def _got_late(self, html) -> None:
        if self._finished:
            return
        html = str(html or "")
        if html:
            self._finish(html=html, url=self._page.url().toString())   # the importer says the check did not pass
        else:
            self._finish(error=f"Страница не открылась за {int(TIMEOUT)} секунд")

    def _finish(self, *, html: str = "", url: str = "", error: str = "") -> None:
        self._finished = True
        self._settle.stop()
        self._timeout.stop()
        if error:
            self._box["error"] = error
        else:
            self._box["html"] = html
            self._box["url"] = url or self._url
            if self._store is not None:
                self._box["cookies"] = list(self._cookies.values())
                self._box["agent"] = str(self._profile.httpUserAgent() or "")
        try:
            if self._store is not None:
                self._store.cookieAdded.disconnect(self._cookie)
            self._page.loadFinished.disconnect(self._loaded)
            self._page.triggerAction(QWebEnginePage.WebAction.Stop)
        except (RuntimeError, TypeError) as exc:     # already gone
            _LOG.debug("page fetch: %s", exc)
        self._done.set()
        if self._view is not None:
            self._view.close()
            self._view.deleteLater()
            self._view = None
        self._page.deleteLater()
        self.deleteLater()
