"""(v5.44.73) The program window's browser for a site that lets in only browsers: before its pages it runs a check that
a browser opens them (com-x.life — every page, looked at from the owner's computer 5.11.2026; Cloudflare's and
DDoS-Guard's checks the same way).  The owner (5.11.2026), of com-x.life: «проверка кому не похуй куки есть или пусть
открывается в браузере проходишь проверку и качает».

So the page is opened by the program window's own Chromium out of sight (``ui/page_fetch.py`` — the same as for
«Описание с сайта» and nhentai: the gallery's profile, what the site sets there is kept for the next time; in a view
that is never put on the screen, so to its scripts it is an ordinary open tab); the site's check runs in it as in any
browser — nothing is faked or solved, and a check that waits for a person (a captcha) is not passed — said in words.  The program then asks the site with what that browser got: the cookies
the site set and the browser's User-Agent (a site ties its pass to them) — ``manhwa_sources.Net`` puts them on its
requests to that site; when the site still answers it with the check, its pages come from the browser itself.

:func:`open_page` → :class:`Page`; what a site gave is kept for the run (:func:`kept_for`), so the next task asking
it starts with it.  ``OPENER`` — a browser of the checks' own.

(v5.44.132) A site that lets in nothing but the browser itself — the program's requests refused even with the
browser's pass and agent (Cloudflare looks at more than those: Nude-Moon, 8.11.2026) — gets its pictures (and pages)
asked by the browser: :func:`fetch_bytes`; ``BYTES`` — the checks' own.  The owner's sign-in on such a site (his
cookies, not the pass of his own browser's check) is given to the window's browser before it asks the site
(:func:`share_cookies`; «Убрать» of «Сайты манги» takes it away); ``COOKIES`` — the checks' own.
"""
from __future__ import annotations

import html as html_text
import ipaddress
import json
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlparse

OPENER = None              # the checks: a function (url, still_check) -> Page instead of the window's browser
WAIT = 10.0                # seconds for the window to take the page
FINISH_WAIT = 80.0         # …and to open it with the site's check (``ui/page_fetch.TIMEOUT`` and some more)
# (v5.44.132) the browser's own request (:func:`fetch_bytes`): the checks — a function (url, referer) -> (status, bytes,
# type) instead of the window's browser; the wait for its answer (``ui/page_fetch.FETCH_TIMEOUT`` and some more)
BYTES = None
BYTES_WAIT = 75.0
COOKIES = None             # (v5.44.132) the checks: a function (origin, cookies, forget) instead of the window's browser
KEEP_FOR = 6 * 3600        # what a site gave is used this long (a site's pass lives hours; then the browser again)


class BrowserError(RuntimeError):
    """The window's browser did not open the page — the words are for the page."""


class BrowserCancelled(BrowserError):
    """The task no longer needs its turn at the hidden browser."""


@dataclass
class Page:
    html: str
    url: str
    cookies: list = field(default_factory=list)      # [{name, value, domain, path}] of the page's site
    agent: str = ""                                   # the browser's User-Agent


_LOCK = threading.Lock()
_KEPT: dict = {}           # the site («com-x.life») → (cookies, agent, when)
_PAGE_LOCK = threading.RLock()       # all tasks share one full page; nested Net/open_page calls share their turn
_MANUAL_CHECK = threading.Lock()
MANUAL_CHECK_SECONDS = 120
_MANUAL_CHECK_SITES = ("nudemoon", "mangadex", "mangabuff", "comx")
_QT_SITES: set = set()       # an explicit manual check keeps using the window's authenticated profile


def _camoufox_for(url: str) -> bool:
    return site_of(urlparse(url).hostname or "") not in _QT_SITES


@contextmanager
def page_turn(stop_check=None):
    """One full browser page in the program; waiting tasks can leave when stopped or past their deadline."""
    stopped = stop_check or (lambda: False)
    if stopped():
        raise BrowserCancelled("остановлено")
    while not _PAGE_LOCK.acquire(timeout=0.1):
        if stopped():
            raise BrowserCancelled("остановлено")
    try:
        if stopped():
            raise BrowserCancelled("остановлено")
        yield
    finally:
        _PAGE_LOCK.release()


def site_of(host: str) -> str:
    """The site of a host: its last two labels («img.com-x.life» → «com-x.life»), an address as it is."""
    host = str(host or "").lower().strip(".")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else host


def available() -> bool:
    """The window's browser is there (the program window is open, or the checks gave their own)."""
    if OPENER is not None:
        return True
    from core import site_camoufox
    if site_camoufox.available():
        return True
    from webui import service
    return service.bridge() is not None


def open_page(url: str, still_check=None, *, stop_check=None) -> Page:
    """The page as the window's browser opens it — out of sight, with the site's check run as any browser runs it
    (``still_check(html, url)`` — the page is still the check: the browser waits for the next one).  What the site
    gave is kept for the run.  :class:`BrowserError` with words."""
    with page_turn(stop_check):
        return _open_page(url, still_check, stop_check)


def _open_page(url: str, still_check, stop_check=None) -> Page:
    if OPENER is not None:
        page = OPENER(url, still_check)
    else:
        from core import site_camoufox
        if _camoufox_for(url):
            try:
                result = site_camoufox.open_page(url, still_check, stop_check)
            except site_camoufox.Unavailable:
                result = None  # Optional runtime absent or failed to start: use the existing browser.
            except site_camoufox.Cancelled as exc:
                raise BrowserCancelled(str(exc)) from exc
            except site_camoufox.Failed as exc:
                raise BrowserError(str(exc)) from exc
            if result is not None:
                page = Page(html=result["html"], url=result["url"], cookies=result["cookies"], agent=result["agent"])
                remember(url, page.cookies, page.agent)
                return page
        from webui import service
        if service.bridge() is None:
            raise BrowserError("окна программы нет — её браузер не открыть")
        box: dict = {}
        state = service.call_window("fetch_page", {"url": url, "box": box, "guard": still_check, "cookies": True,
                                                   "visible": True}, wait=WAIT, finish_wait=FINISH_WAIT)
        if state != "done":
            raise BrowserError("окно программы не открыло страницу" + (" (не дождались)" if state == "late" else ""))
        if box.get("error"):
            raise BrowserError(str(box["error"]))
        page = Page(html=str(box.get("html") or ""), url=str(box.get("url") or url),
                    cookies=[dict(c) for c in box.get("cookies") or [] if isinstance(c, dict)],
                    agent=str(box.get("agent") or ""))
    remember(url, page.cookies, page.agent)
    return page


def manual_check_sites() -> dict:
    from core import manhwa_sources as S
    return {source.name: source.label for source in S.SOURCES if source.name in _MANUAL_CHECK_SITES}


def _manual_target(source) -> tuple:
    """The fixed first page and the exact HTTPS hosts it may navigate to, including challenge redirects."""
    if source.name == "nudemoon":
        return source.feed_url("new", 1), source.hosts
    if source.name == "mangadex":
        return source.api().rstrip("/") + "/manga?limit=1&contentRating[]=safe", (*source.hosts, "api.mangadex.org")
    if source.name == "mangabuff":
        return source.site().rstrip("/") + "/manga?sort=updated_at", source.hosts
    return source.site().rstrip("/") + "/", source.hosts


def _manual_ready(source, page: str, address: str) -> bool:
    from core import manhwa_sources as S
    parsed = urlparse(address)
    path = parsed.path.rstrip("/")
    if S.still_check(page, address):
        return False
    if source.name == "nudemoon":
        return path == "/all_manga" and bool(source.ROW.search(page) and source.ENTRY.search(page))
    if source.name == "mangabuff":
        return path == "/manga" and any(hit.names and hit.names[0] for hit in source._cards(page))
    if source.name == "mangadex":
        if parsed.hostname != "api.mangadex.org" or path != "/manga":
            return False
        pre = re.search(r"<pre\b[^>]*>(.*?)</pre>", page, re.S)
        try:
            data = json.loads(html_text.unescape(pre.group(1)) if pre else page)
        except (ValueError, RecursionError):
            return False
        if not isinstance(data, dict) or data.get("result") != "ok" or not isinstance(data.get("data"), list):
            return False
        return any(isinstance(item, dict) and item.get("type") == "manga"
                   and re.fullmatch(source.UUID, str(item.get("id") or ""))
                   and isinstance(item.get("attributes"), dict) for item in data["data"])
    from core import manhwa_comx as X
    return not path and 'id="dle-content"' in page and bool(X.hits_of(page))


def verify_site(site: str) -> bool:
    """Show a supported site to the user. The shared browser profile keeps the site's own session."""
    from core import manhwa_sources as S
    from webui import service
    source = S.by_name(site) if site in _MANUAL_CHECK_SITES else None
    if source is None:
        raise BrowserError("Ручная проверка для этого сайта не поддерживается")
    if service.bridge() is None:
        raise BrowserError("Откройте Local Booru со встроенным браузером и повторите проверку")
    url, hosts = _manual_target(source)

    def same_site(address: str) -> bool:
        try:
            parsed = urlparse(address)
            return (parsed.scheme == "https" and parsed.hostname in hosts and parsed.port in (None, 443)
                    and parsed.username is None and parsed.password is None)
        except ValueError:
            return False

    if not same_site(url) or (site == "mangadex" and urlparse(url).hostname != "api.mangadex.org"):
        raise BrowserError(f"Для проверки нужен официальный HTTPS-адрес {source.label} в настройках сайта")
    if not _MANUAL_CHECK.acquire(blocking=False):
        raise BrowserError("Окно проверки сайта уже открыто")

    def waiting(html: str, address: str) -> bool:
        return not same_site(address) or not _manual_ready(source, html, address)

    try:
        box: dict = {}
        payload = {"url": url, "box": box, "guard": waiting, "manual": same_site, "manual_label": source.label}
        state = service.call_window("fetch_page", payload, wait=WAIT, finish_wait=MANUAL_CHECK_SECONDS + WAIT)
        if state != "done":
            raise BrowserError(f"Окно программы не завершило проверку {source.label}")
        if box.get("error"):
            raise BrowserError(str(box["error"]))
        ready = not waiting(str(box.get("html") or ""), str(box.get("url") or ""))
        if ready:
            _QT_SITES.update(site_of(host) for host in hosts)
            # The user's manual check belongs to Qt's profile, not to the headless worker.
            remember(url, list(box.get("cookies") or []), str(box.get("agent") or ""))
            S.browser_session_changed(hosts)
        return ready
    finally:
        _MANUAL_CHECK.release()


def fetch_bytes(url: str, referer: str = "", limit: int = 0) -> tuple:
    """(v5.44.132) The answer to the window's browser's own request of ``url`` — ``fetch`` in a page of the site's
    origin, with the browser's cookies and its connection (``ui/page_fetch.BytesFetch``): (status, bytes, type).  For a
    site whose check for browsers lets in nothing but the browser itself — the program's own requests refused even with
    the browser's pass and its User-Agent (Nude-Moon behind Cloudflare, measured from the owner's computer 8.11.2026):
    its pictures (and pages) are asked by the browser.  :class:`BrowserError` with words."""
    if BYTES is not None:
        return BYTES(url, referer)
    from core import site_camoufox
    if _camoufox_for(referer or url):
        try:
            return site_camoufox.fetch_bytes(url, referer, limit)
        except site_camoufox.Unavailable:
            pass
        except site_camoufox.Failed as exc:
            raise BrowserError(str(exc)) from exc
    from webui import service
    if service.bridge() is None:
        raise BrowserError("окна программы нет — её браузер не открыть")
    box: dict = {}
    state = service.call_window("fetch_bytes", {"url": url, "referer": referer, "limit": int(limit or 0), "box": box},
                                wait=WAIT, finish_wait=BYTES_WAIT)
    if state != "done":
        raise BrowserError("окно программы не спросило сайт" + (" (не дождались)" if state == "late" else ""))
    if box.get("error"):
        raise BrowserError(str(box["error"]))
    return int(box.get("status") or 0), bytes(box.get("data") or b""), str(box.get("type") or "")


def share_cookies(origin: str, cookies: list, *, forget: bool = False) -> int:
    """(v5.44.132) The owner's sign-in on a site (its cookies of «Настройки → Сайты манги») given to the window's
    browser, or (``forget``) taken out of it: a site whose pages and pictures only the browser itself gets shows some
    of them only to the signed in (Nude-Moon: a third of its readers, 8.11.2026).  How many cookies; :class:`BrowserError`
    with words."""
    if COOKIES is not None:
        return COOKIES(origin, list(cookies or []), forget)
    from core import site_camoufox
    if _camoufox_for(origin):
        try:
            return site_camoufox.share_cookies(origin, list(cookies or []), forget=forget)
        except site_camoufox.Unavailable:
            pass
        except site_camoufox.Failed as exc:
            raise BrowserError(str(exc)) from exc
    from webui import service
    if service.bridge() is None:
        raise BrowserError("окна программы нет — её браузеру вход не передать")
    box: dict = {}
    state = service.call_window("share_cookies", {"origin": origin, "cookies": list(cookies or []), "forget": forget,
                                                  "box": box}, wait=WAIT, finish_wait=WAIT)
    if state != "done":
        raise BrowserError("окно программы не взяло вход")
    if box.get("error"):
        raise BrowserError(str(box["error"]))
    return int(box.get("count") or 0)


def remember(url: str, cookies: list, agent: str) -> None:
    site = site_of(urlparse(url).hostname or "")
    if site and (cookies or agent):
        with _LOCK:
            _KEPT[site] = (list(cookies), str(agent or ""), time.time())


def kept_for(host: str) -> tuple:
    """(cookies, agent) the window's browser got from the host's site this run — ([], "") when none or too old."""
    with _LOCK:
        entry = _KEPT.get(site_of(host))
    if entry is None or time.time() - entry[2] > KEEP_FOR:
        return [], ""
    return list(entry[0]), entry[1]


def forget(host: str = "") -> None:
    """The site's pass forgotten (all of them without ``host``): the next request goes through the browser again."""
    with _LOCK:
        if host:
            _KEPT.pop(site_of(host), None)
        else:
            _KEPT.clear()


def related(domain: str, host: str) -> bool:
    """A cookie of ``domain`` belongs to ``host``'s site (the pass of «com-x.life» is the pictures' of «img.com-x.life»)."""
    domain = str(domain or "").lower().lstrip(".")
    return bool(domain) and site_of(domain) == site_of(host)
