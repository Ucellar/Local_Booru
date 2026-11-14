"""A web page opened by the program window's browser for «Описание с сайта» (``core/video_meta_import.py``) when the
site does not give it to a plain request: Cloudflare and DDoS-Guard first run a check in the visitor's browser and
then let it through — the window's Chromium runs that check as any browser does, with the gallery's own profile
(what the site set after the check is kept for the next time).

The ordinary fetch stays out of sight. It ends by itself (``TIMEOUT``) and is destroyed explicitly — a web page left
to the garbage collector can end the program (CLAUDE.md, rule 15).  The task waiting for it gets ``box["html"]`` and
``box["url"]``, or ``box["error"]``, and then ``done``.

(v5.44.73) «Скачать манхву» (``core/site_browser.py``) asks it too: ``guard(html, url)`` — the page is still the site's
check (com-x.life's is no Cloudflare's: it sends the browser to «/_c?t=…» first); ``cookies`` — the box also gets the
cookies of the page's site and the browser's User-Agent (``box["cookies"]``, ``box["agent"]``): the program's own
requests to that site go with them; ``visible`` — the page is in a view that is never put on the screen
(``WA_DontShowOnScreen``): a page with no view is «hidden» to its scripts, with a screen of 0×0 and no animation frames —
what a check for robots looks at (measured in Qt 6.11: «hidden», 0×0, no frames; in such a view — «visible», the
screen's size, 60 frames a second).

An explicit site check shows a dialog where the user can interact with the site. It uses the same profile,
restricts top-level navigation to the site's own addresses, and closes when the catalog is ready.

(v5.44.132) :class:`BytesFetch` — the browser's own request of a page or a picture, for a site whose check lets in
nothing but the browser itself (Nude-Moon: Cloudflare refused the program's own requests even with the browser's pass
and its User-Agent — measured from the owner's computer 8.11.2026): a page of the site's origin made in place
(``setHtml`` with the site's address as its base — nothing is loaded from the site for it), and ``fetch`` in it — the
browser's cookies, its connection, the Referer of the site; the answer comes back as base64 by polling
(``runJavaScript`` does not wait for a promise): ``box["status"]``, ``box["type"]``, ``box["data"]`` (bytes) or
``box["error"]``.  Each request has its own page, destroyed when it ends.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

_LOG = logging.getLogger("local_booru.video")

TIMEOUT = 45.0                 # seconds for the page and the site's check together
SETTLE = 1.5                   # seconds after the document is ready before it is read (scripts can still change it)
FETCH_TIMEOUT = 60.0           # (v5.44.132) seconds for the browser's own request of a page or a picture
FETCH_LIMIT = 64 * 1024 * 1024  # an answer bigger than this is not taken (a picture of a chapter is a few MB at most)
POLL_MS = 100                  # how often the page is asked whether the answer came
DOCUMENT_POLL_MS = 250         # a full document and its check need fewer polls than a bytes request


class _ManualPage(QWebEnginePage):
    def __init__(self, profile, parent, allowed):
        super().__init__(profile, parent)
        self._allowed = allowed

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if is_main_frame and not self._allowed(url.toString()):
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class PageFetch(QObject):
    def __init__(self, profile, url: str, box: dict, done, parent=None, guard=None, cookies: bool = False,
                 visible: bool = False, manual=None, owner=None, manual_label: str = ""):
        super().__init__(parent)
        self._url = url
        self._box = box
        self._done = done
        self._finished = False
        self._loaded_ok = False
        self._load_failed = False
        self._navigation = 0
        self._ready_since = None
        self._poll_pending = False
        self._guard = guard
        self._profile = profile
        self._host = (urlparse(url).hostname or "").lower()
        self._cookies: dict = {}
        self._store = profile.cookieStore() if cookies else None
        self._page = _ManualPage(profile, self, manual) if manual else QWebEnginePage(profile, self)
        self._page.setAudioMuted(True)           # (v5.44.145) a page nobody sees is not heard either (a site's video
        self._page.loadFinished.connect(self._loaded)      # or advert starting by itself)
        self._page.loadStarted.connect(self._loading)
        ready = QWebEngineScript()
        ready.setName("local-booru-document-ready")
        ready.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld.value)
        ready.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        ready.setRunsOnSubFrames(False)
        ready.setSourceCode("window.__lbDocumentReady = true;")
        self._page.scripts().insert(ready)
        self._view = None
        self._dialog = None
        if manual:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout
            from ui.web_gallery_page import _refuse_permission
            self._dialog = QDialog(owner)
            self._dialog.setWindowTitle(f"{manual_label} — проверка в браузере" if manual_label else "Проверка сайта")
            self._dialog.resize(920, 720)
            self._dialog.finished.connect(self._manual_closed)
            layout = QVBoxLayout(self._dialog)
            layout.addWidget(QLabel("Пройдите проверку сайта. Когда сайт откроется, это окно закроется."))
            self._view = QWebEngineView(self._dialog)
            self._view.setPage(self._page)
            layout.addWidget(self._view)
            if hasattr(self._page, "permissionRequested"):
                self._page.permissionRequested.connect(_refuse_permission)
        elif visible:
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
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._check_ready)

    def start(self) -> None:
        if self._dialog is not None:
            from core.site_browser import MANUAL_CHECK_SECONDS
            self._timeout.start(MANUAL_CHECK_SECONDS * 1000)
            self._dialog.show()
            self._dialog.raise_()
            self._dialog.activateWindow()
        else:
            self._timeout.start(int(TIMEOUT * 1000))
        if self._view is not None and self._dialog is None:
            self._view.show()                             # «shown» to Qt and to the page — not on the screen
        if self._store is not None:
            self._store.cookieAdded.connect(self._cookie)
            self._store.loadAllCookies()                 # what the site set before: told again, as «added»
        self._page.load(QUrl(self._url))
        self._poll.start(DOCUMENT_POLL_MS)

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

    def _loading(self) -> None:
        self._navigation += 1
        self._ready_since = None
        self._loaded_ok = self._load_failed = False
        self._settle.stop()

    def _check_ready(self) -> None:
        """DOM ready does not wait for pictures/advertising; the site's own check still has the final say."""
        if self._finished or self._poll_pending or self._load_failed:
            return
        self._poll_pending = True
        navigation = self._navigation
        self._page.runJavaScript(
            'window.__lbDocumentReady && document.body && document.readyState !== "loading" ? location.href : ""',
            QWebEngineScript.ScriptWorldId.ApplicationWorld.value,
            lambda url: self._document_ready(url, navigation))

    def _document_ready(self, url, navigation: int) -> None:
        self._poll_pending = False
        if self._finished or navigation != self._navigation or self._load_failed:
            return
        url = str(url or "")
        if urlparse(url).scheme not in ("http", "https") or url != self._page.url().toString():
            return
        if self._ready_since is None:
            self._ready_since = time.monotonic()
        if time.monotonic() - self._ready_since < SETTLE:
            return
        self._poll_pending = True
        # Qt's toHtml may still return its empty initial document until loadFinished. Read the live DOM instead.
        self._page.runJavaScript(
            'window.__lbDocumentReady && document.body ? document.documentElement.outerHTML : ""',
            QWebEngineScript.ScriptWorldId.ApplicationWorld.value,
            lambda html: self._got_ready(html, url, navigation))

    def _got_ready(self, html, url: str, navigation: int) -> None:
        self._poll_pending = False
        if (self._finished or navigation != self._navigation or self._load_failed
                or url != self._page.url().toString()):
            return
        html = str(html or "")
        if html and not self._still_check(html):
            self._finish(html=html, url=url)

    def _loaded(self, ok: bool) -> None:
        if self._finished:
            return
        self._loaded_ok = bool(ok)
        self._load_failed = not ok
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
                self._finish(error="Не удалось проверить, готова ли страница сайта")
                return True
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
        if self._dialog is not None:
            self._finish(error="Время проверки сайта вышло. Откройте её ещё раз")
            return
        self._page.toHtml(self._got_late)

    def _manual_closed(self, _result: int) -> None:
        if not self._finished:
            self._finish(error="Окно проверки закрыто")

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
        self._poll.stop()
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
            self._page.loadStarted.disconnect(self._loading)
            self._page.triggerAction(QWebEnginePage.WebAction.Stop)
        except (RuntimeError, TypeError) as exc:     # already gone
            _LOG.debug("page fetch: %s", exc)
        self._done.set()
        if self._view is not None:
            self._view.close()
            if self._dialog is None:
                self._view.deleteLater()
            self._view = None
        if self._dialog is not None:
            dialog, self._dialog = self._dialog, None
            dialog.hide()
            dialog.deleteLater()
        self._page.deleteLater()
        self.deleteLater()


# (v5.44.132) the browser's own request: ``fetch`` in the page of the site's origin; the answer kept in the page until
# it is taken (base64 — runJavaScript gives strings, not bytes)
_FETCH_JS = """(function(){var box = window.__lbFetch = window.__lbFetch || {}; var id = %s; box[id] = null;
fetch(%s, {credentials: "include", referrer: %s, cache: "no-store"}).then(function(r){
  return r.arrayBuffer().then(function(buf){
    if (buf.byteLength > %d) { box[id] = {status: r.status, error: "big"}; return; }
    var b = new Uint8Array(buf), s = "";
    for (var i = 0; i < b.length; i += 32768) s += String.fromCharCode.apply(null, b.subarray(i, i + 32768));
    box[id] = {status: r.status, type: r.headers.get("content-type") || "", data: btoa(s)};
  });
}).catch(function(e){ box[id] = {error: String(e)}; });
return true;})()"""
_TAKE_JS = """(function(){var box = window.__lbFetch; var v = box && box[%s]; if (!v) return "";
delete box[%s]; return JSON.stringify(v);})()"""


def share_cookies(profile, origin: str, cookies: list, *, forget: bool = False) -> int:
    """(v5.44.132) The owner's sign-in on a site (its cookies, as «Настройки → Сайты манги» keeps them: {name, value,
    domain, path, expires}) put into the window's browser for the site at ``origin`` — or (``forget``) taken out of it.
    A cookie of the site's own host is the host's alone (Chromium takes no «Domain» of an address), one of its domain
    («.nude-moon.org») — of the domain.  How many were given."""
    from PySide6.QtCore import QDateTime
    from PySide6.QtNetwork import QNetworkCookie
    store = profile.cookieStore()
    host = (urlparse(origin).hostname or "").lower()
    count = 0
    for c in cookies or []:
        name = str(c.get("name") or "")
        if not name:
            continue
        cookie = QNetworkCookie(name.encode("latin-1", "replace"), str(c.get("value") or "").encode("latin-1", "replace"))
        domain = str(c.get("domain") or "").lower()
        if domain and domain.lstrip(".") != host:
            cookie.setDomain(domain if domain.startswith(".") else "." + domain)
        cookie.setPath(str(c.get("path") or "/"))
        cookie.setHttpOnly(True)
        try:
            ends = float(c.get("expires") or 0)
        except (TypeError, ValueError):
            ends = 0.0
        if ends > 0:
            cookie.setExpirationDate(QDateTime.fromSecsSinceEpoch(int(ends)))
        if forget:
            store.deleteCookie(cookie, QUrl(origin))
        else:
            store.setCookie(cookie, QUrl(origin))
        count += 1
    return count


class BytesFetch(QObject):
    """(v5.44.132) The answer to the browser's own request of ``url`` (see the module's text): a page of the site's
    origin made in place, ``fetch`` in it, the answer taken by polling.  Ends by itself (:data:`FETCH_TIMEOUT`); its
    page is destroyed explicitly, as :class:`PageFetch`'s."""

    def __init__(self, profile, url: str, box: dict, done, parent=None, referer: str = "", limit: int = 0):
        super().__init__(parent)
        parsed = urlparse(url)
        self._origin = f"{parsed.scheme}://{parsed.netloc}"
        mine = urlparse(referer or "")
        self._referer = referer if f"{mine.scheme}://{mine.netloc}" == self._origin else self._origin + "/"
        self._url = url
        self._box = box
        self._done = done
        self._limit = int(limit or 0) if 0 < int(limit or 0) <= FETCH_LIMIT else FETCH_LIMIT
        self._finished = False
        self._asked = False
        self._key = json.dumps(f"q{id(self)}")
        self._page = QWebEnginePage(profile, self)
        self._page.setAudioMuted(True)           # (v5.44.145) as the page above: never heard
        self._page.loadFinished.connect(self._ready)
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._ask)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._give_up)

    def start(self) -> None:
        if urlparse(self._url).scheme not in ("http", "https"):
            self._finish(error="Это не адрес сайта")
            return
        self._timeout.start(int(FETCH_TIMEOUT * 1000))
        self._page.setHtml("<!doctype html><html><head><title></title></head><body></body></html>",
                           QUrl(self._origin + "/"))

    def _ready(self, ok: bool) -> None:
        if self._finished or self._asked:
            return
        if not ok:
            self._finish(error="Браузер окна программы не открыл страницу сайта для запроса")
            return
        self._asked = True
        script = _FETCH_JS % (self._key, json.dumps(self._url), json.dumps(self._referer), self._limit)
        self._page.runJavaScript(script, 0, self._started)

    def _started(self, _value) -> None:
        if not self._finished:
            self._poll.start()

    def _ask(self) -> None:
        if not self._finished:
            self._page.runJavaScript(_TAKE_JS % (self._key, self._key), 0, self._got)

    def _got(self, raw) -> None:
        if self._finished or not raw:
            return
        try:
            answer = json.loads(str(raw))
        except ValueError:
            self._finish(error="Браузер окна программы дал непонятный ответ")
            return
        if answer.get("error"):
            words = "ответ сайта слишком большой" if answer["error"] == "big" else str(answer["error"])[:200]
            self._finish(error=f"Браузер окна программы не получил ответ сайта: {words}")
            return
        try:
            data = base64.b64decode(str(answer.get("data") or ""))
        except ValueError:
            self._finish(error="Браузер окна программы дал непонятный ответ")
            return
        self._finish(status=int(answer.get("status") or 0), ctype=str(answer.get("type") or ""), data=data)

    def _give_up(self) -> None:
        if not self._finished:
            self._finish(error=f"Браузер окна программы не получил ответ сайта за {int(FETCH_TIMEOUT)} секунд")

    def _finish(self, *, status: int = 0, ctype: str = "", data: bytes = b"", error: str = "") -> None:
        self._finished = True
        self._poll.stop()
        self._timeout.stop()
        if error:
            self._box["error"] = error
        else:
            self._box.update({"status": status, "type": ctype, "data": data})
        try:
            self._page.loadFinished.disconnect(self._ready)
            self._page.triggerAction(QWebEnginePage.WebAction.Stop)
        except (RuntimeError, TypeError) as exc:     # already gone
            _LOG.debug("browser fetch: %s", exc)
        self._done.set()
        self._page.deleteLater()
        self.deleteLater()
