"""A web page opened by the program window's browser for «Описание с сайта» (``core/video_meta_import.py``) when the
site does not give it to a plain request: Cloudflare and DDoS-Guard first run a check in the visitor's browser and
then let it through — the window's Chromium runs that check as any browser does, with the gallery's own profile
(what the site set after the check is kept for the next time).

Out of sight: the page is never shown.  It ends by itself (``TIMEOUT``) and is destroyed explicitly — a web page left
to the garbage collector can end the program (CLAUDE.md, rule 15).  The task waiting for it gets ``box["html"]`` and
``box["url"]``, or ``box["error"]``, and then ``done``.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage

_LOG = logging.getLogger("local_booru.video")

TIMEOUT = 45.0                 # seconds for the page and the site's check together
SETTLE = 1.5                   # seconds after a load before the page is read (a check loads the page again)


class PageFetch(QObject):
    def __init__(self, profile, url: str, box: dict, done, parent=None):
        super().__init__(parent)
        self._url = url
        self._box = box
        self._done = done
        self._finished = False
        self._loaded_ok = False
        self._page = QWebEnginePage(profile, self)
        self._page.loadFinished.connect(self._loaded)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._read)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._give_up)

    def start(self) -> None:
        self._timeout.start(int(TIMEOUT * 1000))
        self._page.load(QUrl(self._url))

    def _loaded(self, ok: bool) -> None:
        if self._finished:
            return
        self._loaded_ok = bool(ok)
        self._settle.start(int(SETTLE * 1000))      # every load starts it again: a check that reloads is waited for

    def _read(self) -> None:
        if not self._finished:
            self._page.toHtml(self._got)

    def _got(self, html) -> None:
        if self._finished:
            return
        from core.video_meta_import import guard_of
        html = str(html or "")
        if guard_of(200, html):
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
        try:
            self._page.loadFinished.disconnect(self._loaded)
            self._page.triggerAction(QWebEnginePage.WebAction.Stop)
        except (RuntimeError, TypeError) as exc:     # already gone
            _LOG.debug("page fetch: %s", exc)
        self._done.set()
        self._page.deleteLater()
        self.deleteLater()
