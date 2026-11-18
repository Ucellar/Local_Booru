"""(v5.44.60) The sites «Скачать манхву» takes titles from: what a title is, its chapters, the pictures of a chapter.

The owner (4.11.2026): «mangabuff.ru сайт с мангой и манхвой» (where he reads), remanga.org first among the big ones;
«Платные главы — никогда».  Each site is a :class:`Source`; ``source_for(url)`` finds the one a link belongs to.

* Remanga — its open API, no sign-in (looked at from the owner's computer, 4.11.2026): ``/api/v2/titles/<dir>/`` — the
  title (``main_name``, ``secondary_name``, ``another_name`` « / »-separated, ``type``, ``status``, ``age_limit``,
  ``is_erotic``, ``issue_year``, ``genres``, ``categories``, ``description`` — HTML, ``cover`` — paths on the site,
  ``branches`` [{id, total_votes, count_chapters, publishers}]); ``/api/v2/titles/chapters/?branch_id=&ordering=index
  &page=&count=`` — the chapters of a branch, page by page (``next``): ``id``, ``index``, ``tome``, ``chapter`` (a
  string: «12.5»), ``name``, ``is_paid`` with ``pub_date`` — when a paid chapter becomes free, ``is_published``;
  ``/api/v2/titles/chapters/<id>/`` — its ``pages``: groups (lists) of {link, width, height}, the slices of the strip.
  A paid chapter is never asked for its pictures.
* MangaBuff — pages, not an API: the title's page (``h1``, ``.manga__name-alt``, ``.manga__middle-links`` — the type,
  the year, the status; ``.tags`` — the genres, ``.manga__description``, the poster in ``og:image``), its chapters —
  ``a.chapters__item`` (``…/<tome>/<chapter>``, ``data-chapter``, ``.chapters__name``, ``data-chapter-date``) and the
  rest of them from ``POST /chapters/load`` {manga_id} with the page's CSRF token (``{"content": "<html>"}``); a
  chapter's page — its pictures in ``.reader__item img`` (``src`` or ``data-src``), sizes unknown until downloaded.

The addresses of the sites are module values, so the checks put a site of their own in their place.  Requests go
straight, not through the system's proxy (a VPN's), and through it when straight does not open — as the sites of
voice-overs do (``core/video_dubs.direct``).

(v5.44.62) The same title on another site — the owner (4.11.2026): «стоило бы брать от туда если не доступно с
реманги».  Each site's search (``Source.search``: Remanga's and ReComics' ``/api/v2/search/?query=``, MangaLib's
``/api/manga?q=&site_id[]=1``, MangaBuff's ``/search?type=manga&q=`` — its ``a.cards__item``) is asked by the title's
names; an answer is the same title when a name of each is the same (``name_key``: letters and digits, one case, the
Cyrillic look-alikes of Latin letters read as Latin — Remanga writes «Cultivation of Hеavеn» with Russian «е»), and
the kind of comic and the year do not differ (``same_title``).

(v5.44.72) com-x.life — the owner (5.11.2026): «тоже должно быть доступно».  Every page of it (looked at from the
owner's computer, 5.11.2026) first answers with a check for robots: a page whose script looks whether a browser opens
it (``navigator.webdriver``, the screen…) and only then lets that browser in.  A site that answers with such a check
(Cloudflare, DDoS-Guard, its own) is told by :func:`robot_check` — :class:`Closed`, never asked again in a loop.  A
link of a site the program cannot get into is a title by the name in its address, looked for on the others
(``core/manhwa_link_names.find_by_words``).  A title Remanga marks as licensed (``is_licensed``, «Лицензировано») —
``licensed``: its paid chapters are sold by the holder of the licence and do not become free there.

(v5.44.73) The owner (5.11.2026): «проверка кому не похуй куки есть или пусть открывается в браузере проходишь проверку
и качает», «поставил ссылку на ремангу и мне выдал все главы которые есть на сайтах».  So such a site's page is opened
by the program window's own browser (``core/site_browser.py`` → ``ui/page_fetch.py``: its check runs there as in any
browser; nothing faked, nothing solved), and the program asks the site with the cookies and the User-Agent that browser
got (``Net._past_check``); a site that lets in only the browser itself gets its pages from it (``_through_browser``).
com-x.life (:class:`ComX`) is one of the sites again — last in the owner's order: its title page (``window.__DATA__``:
``news_id``, ``chapters`` [{id, title, date, posi}]), its reader ``/reader/<news_id>/<chapter id>`` (``__DATA__``
``images`` — paths on ``COMX_IMAGES``), its search (``/index.php?do=search``, DataLife Engine's own) — the reading
of its pages is ``core/manhwa_comx.py``.

(v5.44.77) ReadManga and Desu — the sites of the owner's friend's reader (Yomi 1.3), looked at from the owner's
computer (5.11.2026): :class:`ReadManga` (the Grouple network, now ``a.zazaza.me`` — its pages; its search's JSON),
:class:`Desu` (``desu.uno`` — its new API ``/api/manga/…``; the old ``/manga/api/`` answers 410).  Both after MangaBuff
in the order, before com-x.life; both in «Найти по названию» and among the sites the chapters a title's site has not
free are taken from.

(v5.44.78) «Адреса сайтов и вход» (``core/manhwa_access.py``, «Настройки → Манга и манхва»): a site that moved is asked at
the owner's own address (:func:`use_addresses` — ``ADDRESSES``; every :class:`Source` asks ``site()``/``api()``, never
the module values directly; a link of the new address is the site's too), and the owner's sign-in on a site — its
cookies (:func:`use_signins`: each session of a host gets the cookies of its domain) and MangaLib's token (its API
with ``Authorization: Bearer`` — the titles 18+ it shows only to the signed in).  Their values never leave this module
and the sessions: not into pages, journals or errors.

(v5.44.85) Хентай-тян (:class:`Hentaichan`) — the owner (5.11.2026): «https://hentaichan.live/ еще добавь в
поддерживаемые».  A site of hentai alone (``Source.adult_only``): asked only where hentai is shown, for another site's
title only when it is erotic.  A title a gate closes is neither opened nor taken (:class:`Excluded`).

(v5.44.131) Nude-Moon (:class:`NudeMoon`) — the owner (8.11.2026): «nude-moon.org надо добавить в поддержку»; its pages
are behind Cloudflare's check for browsers, and to «как его проходить?» — «Да, как Com-X и куки»: the window's browser
(as for com-x.life), or the owner's own browser — the extension 0.10.1 gives its cookies with its User-Agent, and the
program asks a site with that agent while it has the owner's pass there (:func:`signed_agent`: Cloudflare ties its
pass — ``cf_clearance`` — to the browser that got it).  (v5.44.132) Measured from the owner's computer the same day:
the window's browser passes the check by itself (8–20 s, once 45 s were not enough in a new profile), but Nude-Moon's
Cloudflare refuses the program's own requests even with that browser's pass and its very User-Agent (it looks at more
than those) — so its pages and pictures are the browser's own requests (``Net._browser_fetch`` →
``site_browser.fetch_bytes`` → ``ui/page_fetch.BytesFetch``), and the owner's cookies would not help there either.

(v5.44.168) The sites themselves are a module each — ``core/manhwa_sites/`` (this file was 3 700 lines: every site in
one; the review of 9.11.2026).  Here stays what they share: the addresses and the sign-ins, the errors, what a title,
a chapter and a picture are, the requests (:class:`Net`), the names of titles compared, :class:`Source` — and, at the
end, the list of the sites (``SOURCES``, ``source_for``), for which the sites' modules are imported.  Their classes are
names of this module as before (``manhwa_sources.Remanga``), and they ask this module's names as ``S.<name>`` when
they run: **a value a check or the owner's settings put into this module (``REMANGA_API``, ``PACE``…) is the one a
site uses** — a site's module that copied such a value at its import (``from core.manhwa_sources import REMANGA_API``)
would never see it.
"""
from __future__ import annotations

import copy
import html as _html
import json
import logging
import re
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

# the program's gates: what a site says of a title is handed to them (``core/gates.py``)
from core import download_telemetry
from core import gates as G

_LOG = logging.getLogger("local_booru.manhwa_get")

REMANGA_SITE = "https://remanga.org"
REMANGA_API = "https://api.remanga.org"
MANGABUFF_SITE = "https://mangabuff.ru"
RECOMICS_SITE = "https://recomics.org"           # (v5.44.61) Remanga's sister site — the same API
RECOMICS_API = "https://api.recomics.org"
MANGALIB_SITE = "https://mangalib.me"            # (v5.44.61) its API answers with «Site-Id: 1»
MANGALIB_API = "https://api.cdnlibs.org"
MANGALIB_IMAGES = "https://img2.imglib.info"     # its «main» picture server, unless /api/constants names another
HENTAILIB_SITE = "https://hentailib.me"          # (v5.44.96) the same API as MangaLib's, «Site-Id: 4»
HENTAILIB_API = "https://api.cdnlibs.org"        # …(its own name: a check puts a site of its own there)
HENTAILIB_IMAGES = "https://img2h.hentaicdn.org"  # its «main» picture server of site 4 (/api/constants, 6.11.2026)
COMX_SITE = "https://com-x.life"                 # (v5.44.72) a check for robots first: through the window's browser
COMX_IMAGES = "https://img.com-x.life/comix"     # (v5.44.73) where the pictures of its reader are
READMANGA_SITE = "https://a.zazaza.me"           # (v5.44.77) ReadManga moves from address to address (5.11.2026)
ALLHENTAI_SITE = "https://20.allhen.online"       # (v5.44.99) AllHentai: its number moves (20.allhen.online, 6.11.2026)
DESU_SITE = "https://desu.uno"                   # (v5.44.77) desu.me no longer opens: desu.uno (5.11.2026)
MANGADEX_SITE = "https://mangadex.org"           # (v5.44.79) every language of a title; its own check for browsers
MANGADEX_API = "https://api.mangadex.org"
HENTAICHAN_SITE = "https://hentaichan.live"      # (v5.44.85) its pages move from mirror to mirror (x17.h-chan.me…)
NUDEMOON_SITE = "https://nude-moon.org"          # (v5.44.131) behind Cloudflare's check for browsers (8.11.2026)
ONELOVE_SITE = "https://mangaonelove.space"      # (v5.44.143) Manga One Love — WordPress (Madara), open to programs
EHENTAI_SITE = "https://e-hentai.org"
EHENTAI_API = "https://api.e-hentai.org/api.php"
TIMEOUT = (10, 40)
PAGE_LIMIT = 8 * 1024 * 1024          # a page or an API answer — bigger is not one
PICTURE_LIMIT = 40 * 1024 * 1024      # a slice of a strip is a few hundred KB
PACE = 0.35                           # seconds between two requests to one site's pages or API (not its pictures)
TRIES = 3
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 "
      "Safari/537.36")


# (v5.44.78) the owner's addresses of the sites (a site that moved) and his sign-in on them — ``core/manhwa_access.py``
ADDRESSES: dict = {}                  # the site's name (or «<name>_api») → «https://its.address»
_SIGNINS: dict = {}                   # the site's name → {"cookies": [{domain, name, value, path}], "token": "…",
#                                       (v5.44.131) "agent": the User-Agent of the browser the cookies came from}
# (the sites whose API is an address of its own — ``API_OF`` — are told by the sites themselves: at this file's end)
# (v5.44.79) the language of the chapters of a site that has many (MangaDex): "ru", "en" or "original" — «Настройки →
# Сайты манги» (``core/manhwa_access.py``); the card of a title picks another
LANGUAGES = ("ru", "en", "original")
LANGUAGE = ["ru"]


def plain_address(text) -> str:
    """«https://new.site/» or «new.site» → «https://new.site»; "" — not an address of a site (a path, another scheme)."""
    text = str(text or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path not in ("", "/") or \
            parsed.query or parsed.fragment or parsed.username or parsed.password:
        return ""
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def use_addresses(mapping) -> dict:
    """The owner's addresses in place (``{name: address}``, the rest — the sites' own); what is in place now."""
    names = {s.name for s in SOURCES} | {f"{n}_api" for n in API_OF}
    fresh = {}
    for name, value in dict(mapping or {}).items():
        address = plain_address(value)
        if name in names and address:
            fresh[name] = address
    ADDRESSES.clear()
    ADDRESSES.update(fresh)
    return dict(ADDRESSES)


def address(name: str) -> str:
    return ADDRESSES.get(name, "")


def default_address(name: str) -> str:
    """(v5.44.78) The site's own address (the module's value — the checks put theirs there); «<site>_api» — its
    API's.  (v5.44.169) Which value is a site's — the site says (``Source.address_value``, ``api_value``)."""
    for source in SOURCES:
        if name == source.name:
            return str(globals().get(source.address_value) or "")
        if name == source.name + "_api":
            return str(globals().get(source.api_value) or "")
    return ""


def use_language(value) -> str:
    """(v5.44.79) The language of the chapters of a site of many languages: "ru", "en", "original"."""
    LANGUAGE[0] = str(value) if str(value or "") in LANGUAGES else "ru"
    return LANGUAGE[0]


def address_hosts() -> tuple:
    """(v5.44.78) The hosts of the owner's addresses (their covers are the sites' covers)."""
    return tuple(sorted({(urlparse(a).hostname or "").lower() for a in ADDRESSES.values()} - {""}))


def _api_of(site: str) -> str:
    """Remanga's way: the API of «https://remanga.org» is «https://api.remanga.org»."""
    parsed = urlparse(site or "")
    if not parsed.hostname:
        return ""
    host = parsed.netloc.lower().removeprefix("www.")
    return f"{parsed.scheme}://api.{host}"


def use_signins(mapping) -> None:
    """(v5.44.78) The owner's sign-in in place: {site: {"cookies": [...], "token": "...", (v5.44.131) "agent": "..."}}
    (``core/manhwa_access``)."""
    fresh = {}
    for name, value in dict(mapping or {}).items():
        if isinstance(value, dict):
            cookies = [c for c in value.get("cookies") or [] if isinstance(c, dict) and c.get("name")]
            fresh[str(name)] = {"cookies": cookies, "token": str(value.get("token") or ""),
                                "agent": plain_agent(value.get("agent"))}
    _SIGNINS.clear()
    _SIGNINS.update(fresh)
    _ACCOUNT_MARKS.clear()                           # (v5.44.107) another sign-in — another account's mark


# (v5.44.107) The Grouple sites (AllHentai, ReadManga) show a signed-in reader a chapter's pictures only at the address
# their own script makes of its link: «?d=<the account's mark>» — ``window.user_hash`` of their pages (looked at from the
# owner's computer with his sign-in, 7.11.2026: the chapter's page without it — every picture ``/static/deleted1.png``,
# with it — the pictures).  The mark is learnt from the site's page and kept in memory only: never written, never said.
_ACCOUNT_MARKS: dict = {}                            # site name -> the mark its pages named
_MARK_RE = re.compile(r"window\.user_hash\s*=\s*['\"]([A-Za-z0-9+/=_-]{1,200})['\"]")


def _mark_in(page: str) -> str:
    m = _MARK_RE.search(page or "")
    return m.group(1) if m else ""


def token_of(name: str) -> str:
    return str((_SIGNINS.get(name) or {}).get("token") or "")


def _cookie_fits(domain: str, host: str) -> bool:
    domain = str(domain or "").lower().lstrip(".")
    host = str(host or "").lower()
    return bool(domain) and (host == domain or host.endswith("." + domain))


def signed_cookies(host: str) -> list:
    """The owner's cookies a session of ``host`` carries (of every site: a cookie goes where its domain is)."""
    return [c for site in _SIGNINS.values() for c in site.get("cookies") or []
            if _cookie_fits(c.get("domain"), host)]


# (v5.44.131) the cookies of a pass through a check for browsers — Cloudflare's «cf_clearance», DDoS-Guard's «__ddg…»:
# the service lets in with one only the browser that got it — the same User-Agent (enough where the service looks at
# the cookie and the agent alone; Nude-Moon's Cloudflare looks at more — v5.44.132: its pages through the browser)
AGENT_BOUND = ("cf_clearance", "__ddg")


def plain_agent(text) -> str:
    """(v5.44.131) A browser's User-Agent as the extension gives it — one line of printable ASCII («Mozilla/5.0 …»);
    "" — not one."""
    text = str(text or "").strip()
    return text if re.fullmatch(r"Mozilla/[\x20-\x7e]{10,500}", text) else ""


def signed_agent(host: str) -> str:
    """(v5.44.131) The User-Agent a session of ``host`` asks with: the owner's browser's, when the owner's cookies of
    this host hold its pass through a check for browsers (:data:`AGENT_BOUND`) — the pass is good only with it; ""
    — the program's own."""
    for site in _SIGNINS.values():
        agent = str(site.get("agent") or "")
        if agent and any(_cookie_fits(c.get("domain"), host) and str(c.get("name") or "").startswith(AGENT_BOUND)
                         for c in site.get("cookies") or []):
            return agent
    return ""


class SourceError(RuntimeError):
    """A site did not give what was asked — the words are for the page."""


class Missing(SourceError):
    """(v5.44.62) The site has no such thing (it answered 404): a title another site's search led to that is gone."""


class Refused(SourceError):
    """(v5.44.72) The site would not give it (401, 403, 451) — not a check for robots."""


class Closed(SourceError):
    """(v5.44.72) The site lets in only browsers: it first checks that a browser opens the page (a check for robots);
    (v5.44.73) the program window's browser was not there, or did not get in either."""


class Paid(SourceError):
    """(v5.44.74) The chapter is paid (or closed) on the site: asking again gives nothing — never bought, never got
    around; another site's free copy is the only way."""


class Withheld(SourceError):
    """(v5.44.136) The site gives the program none of its pictures: its picture server answers a stand-in instead of
    each one (ReadManga, asked from the owner's computer 9.11.2026: status 300 and a bare link to someone else's photo —
    for every page, whatever the request; his screenshot: «ReadManga прислал не картинку вместо страницы 1» and three
    retries).  The site's protection against programs — not got around (as a captcha is not solved): asking again gives
    nothing, another site's copy is the only way (``manhwa_run._attempt`` — as :class:`Paid`)."""


class Excluded(SourceError):
    """(v5.44.85) A title the program neither opens nor takes, whatever its site says: a gate closed it
    (``gates.title`` — the words are the gate's)."""


class Silent(SourceError):
    """(v5.44.98) The site did not answer at all — every try ran out of time or the line broke (Хентай-тян 6.11.2026:
    its pages hung for everyone — the program, a browser, the system's proxy, another computer).  Its pages are not
    asked again for :data:`SILENT_FOR` seconds (:func:`silent_left`): the next page of the program says so at once."""


_QUIET_WORDS = re.compile(r"не ответил|ответил (?:429|5\d\d|ничего)")


def transient(exc) -> bool:
    """(v5.44.169) The site said nothing this time and may the next: it was silent (:class:`Silent`), the line broke,
    it was busy (429, 5xx) — asked again later.  Not: it has no such thing (:class:`Missing`), it will not give it
    (:class:`Refused`, :class:`Closed`, :class:`Paid`, :class:`Withheld` — asking again gives the same), a gate of the
    program closed it (:class:`Excluded`), the check was stopped."""
    if isinstance(exc, (Missing, Refused, Closed, Paid, Withheld, Excluded)):
        return False
    return isinstance(exc, Silent) or bool(_QUIET_WORDS.search(str(exc)))


_ASKED_BY = re.compile(r"\?[^\s'\"()<>]+")


def library_words(exc) -> str:
    """(v5.44.169) What the library says of a request that got no answer — for a person's page and the journal: the
    address it names without what was asked by it («…/api/search?…»: the words of a search are long and say nothing
    of why the site was silent — the path does), credentials removed (``core.redaction``), 160 characters."""
    from core.redaction import sanitize_text
    return sanitize_text(_ASKED_BY.sub("?…", str(exc)))[:160]


SILENT_FOR = 3 * 60                 # (v5.44.98) seconds a silent site's pages are not asked again
_SILENT: dict = {}                  # host → (until, the words, (v5.44.162) its name was not found), monotonic
_SILENT_LOCK = threading.Lock()


def _place(url_or_host: str) -> str:
    """The host of an address with its port («hentaichan.live», a check's «127.0.0.1:50123»)."""
    text = str(url_or_host or "")
    return (urlparse(text).netloc if "://" in text else text).lower()


def silent_left(url_or_host: str) -> float:
    """(v5.44.98) Seconds the site of this address stays «silent» (0 — it is asked as usual)."""
    with _SILENT_LOCK:
        until = _SILENT.get(_place(url_or_host), (0.0, ""))[0]
    return max(0.0, until - time.monotonic())


def forget_silent(url_or_host: str = "") -> None:
    """(v5.44.98) Ask a silent site again (the person asked: «Спросить ещё раз», «Найти» once more); "" — every one."""
    place = _place(url_or_host)
    with _SILENT_LOCK:
        if place:
            _SILENT.pop(place, None)
        else:
            _SILENT.clear()


def _nameless(url_or_host: str) -> bool:
    """(v5.44.162) The silent site of this address is one whose name the computer did not find."""
    with _SILENT_LOCK:
        held = _SILENT.get(_place(url_or_host))
    return bool(held and len(held) > 2 and held[2])


def silent_words(what: str, left: float, nameless: bool = False) -> str:
    """The words about a site that gave no answer; (v5.44.162) ``nameless`` — the computer did not find its name
    (and a public resolver gave none either, ``core/name_lookup``): that is not the site's silence, and what helps
    is another thing."""
    minutes = max(1, int(round(left / 60.0)))
    if nameless:
        from core import name_lookup
        return f"{what}: {name_lookup.WORDS}; программа спросит снова через {minutes} мин"
    return f"{what} сейчас не отвечает — сайт молчит; программа спросит его снова через {minutes} мин"


CLOSED = "{site} пускает только браузеры: сначала проверяет, что страницу открывает браузер (проверка на роботов{guard})"
# (the service, the marks of its check page — a tuple: all of them, the marks its own pages may carry too — they tell
# only a refusal)
_CHECKS = (("Cloudflare", ("cf-browser-verification", "cf_chl_", "Just a moment...", "Attention Required! | Cloudflare"),
            ("challenge-platform", "cf-turnstile")),
           ("DDoS-Guard", ("DDoS-Guard</title>",), ("ddos-guard", "DDoS-Guard", "__ddg")),
           # (v5.44.79) MangaDex's own (5.11.2026: its API redirects to mangadex.org/edge/challenge?src=…)
           ("MangaDex", ("MangaDex - Browser verification",), ()),
           # com-x.life's own (5.11.2026): the words of its <noscript> and its script that looks at the browser
           ("", (("необходимо включить JavaScript", "webdriver"),), ()))
# (v5.44.131) Cloudflare's challenge itself — whatever the status and the heading: the options of its script and the
# address of its challenge are on no other page (a page of a protected site carries «challenge-platform/scripts/jsd»
# only).  As the window's browser has it, its script has put the site's name in an <h1> already («Nude-Moon», looked at
# from the owner's computer 8.11.2026: the browser's page was not told from the site's — «не открыл страницу»).
_CF_CHALLENGE = ("_cf_chl_opt", "/orchestrate/chl_page/", "/orchestrate/managed/", "/orchestrate/jsch/")


def robot_check(status: int, page: bytes, url: str = "") -> "str | None":
    """(v5.44.72) The answer is a site's check for robots, not its page: the service's name ("" — the site's own);
    None — it is not one.  A check is a page without a heading that carries the marks of one, or a refusal (403, 429,
    503) with them or with the marks a protected site's pages carry anyway (Cloudflare's script, its Turnstile) — a
    page of the site with those in it is not (MangaBuff's, behind DDoS-Guard, carry none: 5.11.2026)."""
    text = bytes(page[:256 * 1024]).decode("utf-8", "replace")
    if "/_c?t=" in str(url or ""):
        return ""                                          # com-x.life sends every page there first
    if "/edge/challenge" in str(url or ""):
        return "MangaDex"                                  # (v5.44.79) MangaDex sends its API there first
    refused = status in (403, 429, 503)
    if any(mark in text for mark in _CF_CHALLENGE):
        return "Cloudflare"

    def carries(mark) -> bool:
        return all(part in text for part in mark) if isinstance(mark, tuple) else mark in text
    for name, own, anyway in _CHECKS:
        if any(carries(mark) for mark in own) and (refused or "<h1" not in text.lower()):
            return name
        if refused and any(carries(mark) for mark in anyway):
            return name
    return None


def still_check(html: str, url: str = "") -> bool:
    """(v5.44.73) The browser's page is still the site's check (``ui/page_fetch`` waits for the next one)."""
    return robot_check(200, str(html or "").encode("utf-8", "replace"), url) is not None


def closed_words(site: str, guard: str = "") -> str:
    return CLOSED.format(site=site, guard=f" {guard}" if guard else "")


# ── what a site says ─────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Branch:
    id: str
    name: str = ""                     # the translators
    votes: int = 0
    count: int = 0                     # its chapters


@dataclass
class ChapterRef:
    id: str                            # the site's
    tome: int = 0
    number: str = ""                   # «12», «12.5»
    name: str = ""
    free: bool = True
    free_at: str = ""                  # a paid chapter: when it becomes free (ISO), if the site says
    date: str = ""                     # when it came out (ISO or the site's own)
    url: str = ""                      # its page on the site
    index: int = 0                     # its place among the chapters

    @property
    def label(self) -> str:
        return (f"Том {self.tome} " if self.tome else "") + f"Глава {self.number or '?'}"


@dataclass
class Picture:
    url: str
    width: int = 0
    height: int = 0
    fallbacks: list = field(default_factory=list)     # (v5.44.82) the same picture on the site's other servers
    source: str = ""                  # a viewer page resolved lazily by this source, only when this picture is used
    direct_only: bool = False          # a source that reads public HTTP only never asks the window's browser


def fetch_picture(net: "Net", pic: Picture, *, what: str, headers: dict) -> tuple:
    """(v5.44.82) A chapter's picture: from its address, else from the site's other servers (MangaLib's — as the
    friend's reader Yomi does: «Перебирает зеркала картинок»).  (data, type) — or the first server's :class:`SourceError`.
    (v5.44.95) A server that is not the last one is asked once and shortly (:data:`MIRROR_TIMEOUT`: Хентай-тян's hung
    40 s three times for every picture); the picture hosts of a site of mirrors (:func:`picture_links`) — the one that
    gave a picture last first.  (v5.44.134) A server that refused a picture (401/403/451) or did not answer while
    another server of the site gave it — asked after the others :data:`SERVER_REST` seconds (said once): MangaLib's
    «main» refuses the program's every picture (403 of DDoS-Guard), its «secondary» gives them — asked from the owner's
    computer 8.11.2026, his journal had a line for every picture; a picture missing on a server (404) says nothing of
    the server."""
    if pic.source:
        source = by_name(pic.source)
        if source is None:
            raise SourceError("Источник страницы больше не поддерживается")
        pic = source.resolve_picture(net, pic)
    first = None
    host = (urlparse(str(pic.url or "")).hostname or "").lower()
    links = list(dict.fromkeys(link for one in [pic.url, *pic.fallbacks] for link in picture_links(one)))
    if len(links) > 1:
        now = time.monotonic()
        with _RESTING_LOCK:
            resting = {host for host, until in _RESTING.items() if until > now}
        if resting:                                      # stable: the others first, in the site's order
            links.sort(key=lambda link: urlparse(link).netloc.lower() in resting)
    failed: list = []
    for n, link in enumerate(links):
        last = n == len(links) - 1
        try:
            fetch = net._fetch if pic.direct_only and isinstance(net, Net) else net.fetch
            got = fetch(link, paced=False, limit=PICTURE_LIMIT, what=what, headers=headers,
                            **({} if last else {"timeout": MIRROR_TIMEOUT, "tries": 1}))
            if stand_in(got[0]):                         # (v5.44.136) not a picture: the site's answer to programs
                withhold(host, what)
                raise Withheld(WITHHELD.format(site=what))
            gave_picture(link)
            _rest(failed, what)
            return got
        except Closed:
            raise
        except SourceError as exc:
            first = first or exc
            if not isinstance(exc, Missing):
                failed.append((urlparse(link).netloc.lower(), exc))
            _LOG.info("%s: a picture not given by %s: %s", what, urlparse(link).netloc, exc)
    raise first or SourceError(f"{what}: картинка не пришла")


# (v5.44.136) a site whose picture server gives the program a stand-in instead of every picture (:class:`Withheld`)
WITHHELD = ("{site} не отдаёт программе картинки: вместо страницы его сервер присылает ссылку на чужую картинку — "
            "это его защита от программ, её программа не обходит. Главу можно взять только с другого сайта")
WITHHELD_FOR = 6 * 3600            # seconds such a site's chapters are asked of another site first (``withheld``)
STAND_IN_MAX = 2048                # bytes: a stand-in is a bare link, a picture is never that short
_WITHHELD: dict = {}               # a site's label → until when (``time.monotonic``)
_WITHHELD_LOCK = threading.Lock()


def stand_in(data: bytes) -> bool:
    """(v5.44.136) A picture server's stand-in instead of the picture: a short answer that is one link and nothing else
    (ReadManga's «https://upload.wikimedia.org/…Pumkins.JPG», 77 bytes, «image/jpeg»)."""
    if not data or len(data) > STAND_IN_MAX:
        return False
    try:
        text = bytes(data).decode("utf-8").strip()
    except UnicodeDecodeError:
        return False
    return bool(re.fullmatch(r"https?://[^\s<>\"']+", text))


def withhold(host: str, what: str) -> None:
    """The site ``what`` gave a stand-in instead of a picture: said once while it lasts; its chapters are asked of
    another site first (``manhwa_run._attempt``) :data:`WITHHELD_FOR` seconds."""
    now = time.monotonic()
    with _WITHHELD_LOCK:
        new = _WITHHELD.get(what, 0.0) <= now
        _WITHHELD[what] = now + WITHHELD_FOR
    if new:
        _LOG.info("%s: its picture server (%s) gives the program a stand-in instead of the pictures — its chapters are "
                  "taken from another site first for %d h", what, host, WITHHELD_FOR // 3600)


def withheld(what: str) -> bool:
    """(v5.44.136) The site ``what`` (its label) gave a stand-in instead of a picture lately."""
    with _WITHHELD_LOCK:
        return _WITHHELD.get(str(what or ""), 0.0) > time.monotonic()


# (v5.44.134) picture servers asked after the others a while (``fetch_picture``)
SERVER_REST = 10 * 60
_RESTING: dict = {}                  # a picture server (its host[:port]) → until when (``time.monotonic``) asked last
_RESTING_LOCK = threading.Lock()


def _rest(failed: list, what: str) -> None:
    """The servers that did not give a picture another server of the site gave (``failed``: (host, error)) — asked
    after the others :data:`SERVER_REST` seconds; said once while it lasts."""
    if not failed:
        return
    now = time.monotonic()
    with _RESTING_LOCK:
        for host, exc in failed:
            if _RESTING.get(host, 0.0) <= now:
                _LOG.info("%s: picture server %s did not give a picture another server gave (%s) — asked after the "
                          "others for %d min", what, host, exc, SERVER_REST // 60)
            _RESTING[host] = now + SERVER_REST


# (v5.44.95) Хентай-тян's pictures: its pages name img4, mimg4, imgcover, megaimg.imgschan.xyz — on 6.11.2026 they all
# hung (asked from the owner's computer), while img2 and img3.imgschan.xyz gave the same paths in a second
PICTURE_MIRRORS = {"imgschan.xyz": ("img2.imgschan.xyz", "img3.imgschan.xyz")}
MIRROR_TIMEOUT = (6, 20)             # a picture's server that is not the last one to ask: (connect, read), once
_GOOD_HOSTS: dict = {}               # a site of mirrors → its host that gave a picture last


def _mirror_domain(host: str) -> str:
    host = str(host or "").lower()
    return next((d for d, mirrors in PICTURE_MIRRORS.items()
                 if host == d or host.endswith("." + d) or host in mirrors), "")


def picture_links(url: str) -> list:
    """(v5.44.95) The same picture on the hosts of a site of mirrors (:data:`PICTURE_MIRRORS`): the one that gave a
    picture last, the address's own, the mirrors; another address — itself alone."""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return [url]
    domain = _mirror_domain(parsed.hostname or "")
    if not domain or not parsed.path:
        return [url]
    hosts = dict.fromkeys(h for h in (_GOOD_HOSTS.get(domain), parsed.hostname, *PICTURE_MIRRORS[domain]) if h)
    return [parsed._replace(netloc=host if not parsed.port else f"{host}:{parsed.port}").geturl() for host in hosts]


def gave_picture(url: str) -> None:
    """The host of ``url`` gave a picture: the next pictures of its site ask it first."""
    host = (urlparse(str(url or "")).hostname or "").lower()
    domain = _mirror_domain(host)
    if domain:
        _GOOD_HOSTS[domain] = host


@dataclass
class Hit:
    """(v5.44.62) A title a site's search gave — enough to tell whether to ask for it; (v5.44.75) and to show it as a
    tile of «Найти по названию» (``core/manhwa_search.py``): its page, cover, chapters, rating."""
    site: str
    key: str
    names: list = field(default_factory=list)
    type: str = ""
    year: int = 0
    url: str = ""                      # (v5.44.75) the title's page on the site
    cover: str = ""                    # its cover (a small one)
    chapters: int = 0                  # chapters the site counts (0 — it does not say)
    rating: float = 0.0
    votes: int = 0
    adult: bool = False                # the site marks it as erotica


@dataclass
class TitleRef:
    site: str                          # Source.name
    key: str                           # the site's own name of the title (Remanga's dir, MangaBuff's slug)
    url: str
    title: str
    alt_titles: list = field(default_factory=list)
    type: str = ""                     # «Манхва», «Манга», «Маньхуа»…
    status: str = ""
    year: int = 0
    age: str = ""
    is_erotic: bool = False
    genres: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    description: str = ""
    cover_url: str = ""
    authors: list = field(default_factory=list)
    people: list = field(default_factory=list)         # (v5.44.149) [{id, name}] — its authors and artists as the site
    #                                                    numbers them (the Lib API: «Лента» lists a person's titles)
    branches: list = field(default_factory=list)       # Branch; one with no id — a site without branches
    site_id: str = ""                  # MangaBuff's number of the title (for /chapters/load)
    licensed: bool = False             # (v5.44.72) the site sells it under a licence: its paid chapters stay paid
    language: str = ""                 # (v5.44.79) the chapters' language ("" — the site's own, Russian; MangaDex's —
    original: str = ""                 # its branch's), the language the title was written in (MangaDex)
    prefer: str = ""                   # (v5.44.79) the branch the owner's language picks (MangaDex)

    def best_branch(self) -> "Branch | None":
        """The translators with the most votes (the owner: «с бóльшим числом оценок»), then with more chapters;
        (v5.44.79) on a site of many languages — the owner's language (``prefer``) when the title has it."""
        if self.prefer:
            chosen = next((b for b in self.branches if b.id == self.prefer), None)
            if chosen is not None:
                return chosen
        return max(self.branches, key=lambda b: (b.votes, b.count), default=None)

    def info(self) -> dict:
        """What ``info.json`` of the title's folder keeps (``core/manhwa_library.py`` reads it)."""
        return {"title": self.title, "alt_titles": self.alt_titles, "type": self.type, "status": self.status,
                "year": self.year or None, "age": self.age, "is_erotic": self.is_erotic, "genres": self.genres,
                "tags": self.tags, "description": self.description, "authors": self.authors,
                "source_url": self.url, "site": self.site}


def checked(ref: TitleRef) -> TitleRef:
    """(v5.44.143) A title a site has just described, past the program's gate (``gates.title``): what the site says of
    it — its names, genres, tags and whether it marks it erotic (``is_erotic``: MangaLib's «18+ (RX)», Remanga's
    ``is_erotic``, MangaDex's «erotica» and «pornographic», the genres «Хентай», «Эротика»…) — is handed to the gate;
    :class:`Excluded` with the gate's words when it closes the title.  Every site's ``title`` ends with it: opened by
    a link, as another site's copy, by the night's round, by «Читать»."""
    why = G.title(ref.site, names=[ref.title, *ref.alt_titles], genres=ref.genres, tags=ref.tags, adult=ref.is_erotic)
    if why:
        raise Excluded(why)
    return ref


def checked_hits(hits: list) -> list:
    """(v5.44.143) The tiles of a site's search or feed, past the program's gate: a title it closes by what the tile
    says (its names, the site's mark of erotica) is not a tile (its page is not opened either — :func:`checked`)."""
    return [h for h in hits or [] if not G.title(h.site, names=h.names, adult=h.adult)]


# ── the way to a site ────────────────────────────────────────────────────────────────────────────────────────────
class Net:
    """One session per site; straight first, then through the system's proxy; a pace between a site's requests;
    (v5.44.73) a site's check for robots — through the program window's browser (``core/site_browser.py``)."""

    timeout = TIMEOUT                    # (v5.44.89) a feed's page asks shorter (``manhwa_feed.FeedNet``)
    tries = TRIES

    def __init__(self, stop_check=None):
        self._sessions: dict = {}
        self._last: dict = {}
        self.kept: dict = {}                 # what a site keeps between its questions of one look (MangaLib's chapters)
        self._lock = threading.Lock()
        self.stop_check = stop_check or (lambda: False)
        self._passes: dict = {}                  # the site → (cookies, agent) the browser got there
        self._browser_only: set = set()          # hosts whose pages only the browser itself gets
        self._bytes_only: set = set()            # (v5.44.132) …and their pictures too (the browser's own request)
        self._no_fetch: set = set()              # (v5.44.132) hosts the browser's own request did not work for

    def asked_until(self, stop_check) -> "Net":
        """(v5.44.117) This net — its sessions (one per host; several threads may share them: ``requests`` sessions
        take requests from many threads, and each thread here asks its own site), its pace, the browser's passes, what
        a look keeps (``kept``) — with another stop: the other sites of a title asked at once
        (``manhwa_download.look``) stop at their common deadline, or when the one asking stops."""
        twin = copy.copy(self)                   # a shallow copy: the same dictionaries and locks
        twin.stop_check = stop_check
        return twin

    def session(self, host: str):
        import requests
        with self._lock:
            session = self._sessions.get(host)
            if session is None:
                session = requests.Session()
                session.headers.update({"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
                session.trust_env = False                    # straight; ``_through`` turns to the proxy
                session.lb_route = "direct"
                if host in _PROXIED and _system_proxy(f"https://{host}/"):
                    session.trust_env = True                 # (v5.44.85) it did not open straight in this run
                    session.lb_route = "system"
                for c in signed_cookies(host):               # (v5.44.78) the owner's sign-in on this site
                    session.cookies.set(str(c["name"]), str(c.get("value") or ""), domain=str(c.get("domain") or host),
                                        path=str(c.get("path") or "/"))
                agent = signed_agent(host)                   # (v5.44.131) his pass through a check — with his agent
                if agent:
                    session.headers["User-Agent"] = agent
                self._sessions[host] = session
                self._dress(host, session)
            return session

    # (v5.44.73) the window's browser for a site that lets in only browsers
    def _pass_of(self, host: str) -> tuple:
        from core import site_browser
        site = site_browser.site_of(host)
        if site not in self._passes:
            cookies, agent = site_browser.kept_for(host)     # what the browser got there earlier in this run
            if cookies or agent:
                self._passes[site] = (cookies, agent)
        return self._passes.get(site, ([], ""))

    def _dress(self, host: str, session) -> None:
        """The site's pass on a session of one of its hosts: the browser's cookies and its User-Agent."""
        from core import site_browser
        cookies, agent = self._pass_of(host)
        mine = [c for c in cookies if site_browser.related(str(c.get("domain") or host), host)]
        # (v5.44.131) the window's pass instead of the owner's one (of his browser, with his agent): never both
        bound = {str(c.get("name") or "") for c in mine if str(c.get("name") or "").startswith(AGENT_BOUND)}
        for old in [c for c in session.cookies if c.name in bound]:
            session.cookies.clear(old.domain, old.path, old.name)
        for c in mine:
            session.cookies.set(str(c.get("name") or ""), str(c.get("value") or ""),
                                domain=str(c.get("domain") or host), path=str(c.get("path") or "/"))
        if agent and (mine or not cookies):
            session.headers["User-Agent"] = agent

    def _take(self, host: str, page) -> None:
        from core import site_browser
        with self._lock:
            self._passes[site_browser.site_of(host)] = (list(page.cookies), page.agent)
            for other, session in self._sessions.items():
                if site_browser.site_of(other) == site_browser.site_of(host):
                    self._dress(other, session)

    @staticmethod
    def _page_like(method: str, headers: dict | None) -> bool:
        accept = str((headers or {}).get("Accept") or "")
        return method == "GET" and not accept.startswith("image/") and "json" not in accept

    @contextmanager
    def _browser_turn(self):
        """The program's shared browser turn, only while this task still needs its answer."""
        from core import site_browser
        try:
            with site_browser.page_turn(self.stop_check):
                yield
        except site_browser.BrowserCancelled as exc:
            raise SourceError(str(exc)) from exc

    def _open(self, url: str, closed: "Closed", what: str):
        """The page as the window's browser opens it — :class:`Closed` with words when it does not get in either."""
        from core import site_browser
        share_signin(url)                                # (v5.44.132) the owner's sign-in there — the browser's too
        try:
            page = site_browser.open_page(url, still_check, stop_check=self.stop_check)
        except site_browser.BrowserCancelled as exc:
            raise SourceError(str(exc)) from exc
        except site_browser.BrowserError as exc:
            raise Closed(f"{closed} — браузер программы её не прошёл: {exc}") from exc
        if still_check(page.html, page.url):
            raise Closed(f"{closed} — браузер окна программы её не прошёл: она ждёт человека (капча), а такое "
                         "программа не решает")
        _LOG.info("%s: let in through the window's browser (%d cookies)", what, len(page.cookies))
        return page

    def _past_check(self, url: str, closed: "Closed", options: dict) -> tuple:
        """A check for robots instead of the answer: the window's browser opens the page (a picture — the page it is
        for), the request is asked again with what the browser got; a site that lets in only the browser itself —
        its page as the browser has it (and its next pages through it)."""
        from core import site_browser
        if not site_browser.available():
            raise Closed(f"{closed} — а браузера окна программы сейчас нет")
        host = (urlparse(url).hostname or "").lower()
        what = options.get("what") or host
        headers = options.get("headers") or {}
        page_like = self._page_like(options.get("method", "GET"), headers)
        if host in self._browser_only and not page_like:
            if options.get("method", "GET") != "GET":
                raise Closed(f"{closed} — пускает только сам браузер окна программы, а он такое не отправляет")
            with self._lock:                             # (v5.44.132) the browser's own request — and so the next ones
                self._bytes_only.add(host)
            return self._browser_bytes(url, closed, options)
        target = url if page_like else str(headers.get("Referer") or f"{urlparse(url).scheme}://{host}/")
        with self._browser_turn():
            try:
                return self._fetch(url, **options)        # another worker of this site got in meanwhile
            except Closed:
                pass
            page = self._open(target, closed, what)
            self._take(host, page)
        try:
            return self._fetch(url, **options)
        except Closed:
            if not page_like:
                if options.get("method", "GET") != "GET":
                    raise
                with self._lock:                         # (v5.44.132) its pictures — the browser's own request
                    self._browser_only.add(host)
                    self._bytes_only.add(host)
                _LOG.info("%s: its pictures only through the window's browser", what)
                return self._browser_bytes(url, closed, options)
        with self._lock:
            self._browser_only.add(host)
        _LOG.info("%s: its pages only through the window's browser", what)
        return page.html.encode("utf-8"), "text/html; charset=utf-8"

    def _through_browser(self, url: str, what: str) -> tuple:
        from core import site_browser
        if self.stop_check():
            raise SourceError("остановлено")
        closed = Closed(closed_words(what))
        if not site_browser.available():
            raise Closed(f"{closed} — а браузера окна программы сейчас нет")
        host = (urlparse(url).hostname or "").lower()
        if host not in self._no_fetch:                  # (v5.44.132) the browser's own request first: the page as the
            got = self._browser_fetch(url, what, f"{urlparse(url).scheme}://{host}/", PAGE_LIMIT)   # site sent it,
            if got is not None:                         # without opening it (quicker)
                return got
        with self._browser_turn():
            page = self._open(url, closed, what)
            self._take(host, page)
        return page.html.encode("utf-8"), "text/html; charset=utf-8"

    def _browser_fetch(self, url: str, what: str, referer: str, limit: int) -> "tuple | None":
        """(v5.44.132) The window's browser's own request (``site_browser.fetch_bytes``): (the answer's bytes, its type);
        None — the answer is the site's check again (the browser's pass ended), or the browser cannot ask so (it is
        not asked so again by this net); :class:`Missing`, :class:`Refused`, :class:`SourceError` — as the site said."""
        from core import site_browser
        host = (urlparse(url).hostname or "").lower()
        share_signin(url)                                # the owner's sign-in there — the browser's too
        for attempt in (1, 2):
            if self.stop_check():
                raise SourceError("остановлено")
            try:
                with download_telemetry.browser_read(picture=limit == PICTURE_LIMIT):
                    status, data, ctype = site_browser.fetch_bytes(url, referer, limit)
            except site_browser.BrowserError as exc:
                _LOG.info("%s: the browser's own request did not work: %s", what, exc)
                with self._lock:
                    self._no_fetch.add(host)
                return None
            ctype = str(ctype or "").lower()
            if "html" in ctype and status in (200, 403, 404, 429, 503) and robot_check(status, data, url) is not None:
                return None                              # the check again: the browser's pass ended
            if status in (429, 500, 502, 503, 504, 520, 521, 522, 524) and attempt == 1:
                time.sleep(2.0)
                continue
            if status in (404, 410):
                raise Missing(f"{what} ответил {status}")
            if status in (401, 403, 451):
                raise Refused(f"{what} ответил {status}")
            if status >= 400 or status <= 0:
                raise SourceError(f"{what} ответил {status or 'ничего'}")
            if len(data) > limit:
                raise SourceError(f"{what} прислал слишком большой ответ")
            return bytes(data), ctype
        raise SourceError(f"{what} не ответил")

    def _browser_bytes(self, url: str, closed: "Closed", options: dict) -> tuple:
        """(v5.44.132) A picture (or another answer that is no page) of a site that lets in nothing but the browser
        itself: the window's browser's own request; the browser's pass ended — the browser passes the check again (on
        the page the request is for — its Referer), and asks once more."""
        from core import site_browser
        if not site_browser.available():
            raise Closed(f"{closed} — а браузера окна программы сейчас нет")
        host = (urlparse(url).hostname or "").lower()
        what = options.get("what") or host
        headers = options.get("headers") or {}
        referer = str(headers.get("Referer") or f"{urlparse(url).scheme}://{host}/")
        limit = int(options.get("limit") or PAGE_LIMIT)
        for attempt in (1, 2):
            got = self._browser_fetch(url, what, referer, limit)
            if got is not None:
                return got
            if attempt == 2 or host in self._no_fetch:
                break
            with self._browser_turn():
                page = self._open(referer, closed, what)
                self._take(host, page)
        raise Closed(f"{closed} — пускает только сам браузер окна программы, а он это не получил")

    def _wait_turn(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            at = max(now, self._last.get(host, 0.0) + PACE)
            self._last[host] = at
        if at > now:
            time.sleep(at - now)

    def fetch(self, url: str, *, method: str = "GET", headers: dict | None = None, data=None, limit: int = PAGE_LIMIT,
              paced: bool = True, what: str = "", timeout=None, tries: "int | None" = None,
              keep_missing: bool = False) -> tuple[bytes, str]:
        """(the answer's bytes, its content type); :class:`SourceError` with words when there is none; (v5.44.73) a
        check for robots — through the window's browser (:meth:`_past_check`); (v5.44.93) ``timeout``, ``tries`` —
        of this request alone (a title's cover: short — it is not worth minutes)."""
        host = (urlparse(url).hostname or "").lower()
        options = {"method": method, "headers": headers, "data": data, "limit": limit, "paced": paced, "what": what,
                   "timeout": timeout, "tries": tries, "keep_missing": keep_missing}
        if host in self._browser_only and self._page_like(method, headers):
            return self._through_browser(url, what or host)
        if host in self._bytes_only and method == "GET":         # (v5.44.132) not asked again to be refused
            return self._browser_bytes(url, Closed(closed_words(what or host)), options)
        try:
            return self._fetch(url, **options)
        except Closed as closed:
            return self._past_check(url, closed, options)

    def _fetch(self, url: str, *, method: str = "GET", headers: dict | None = None, data=None, limit: int = PAGE_LIMIT,
               paced: bool = True, what: str = "", timeout=None, tries: "int | None" = None,
               keep_missing: bool = False) -> tuple[bytes, str]:
        import requests
        host = (urlparse(url).hostname or "").lower()
        what = what or host
        place = _place(url)
        # (v5.44.98) its pages and API, not pictures: a cover that hangs says nothing of the site's pages
        watched = paced and not str((headers or {}).get("Accept") or "").startswith("image/")
        if watched and silent_left(place):           # it did not answer a moment ago: said at once
            raise Silent(silent_words(what, silent_left(place), nameless=_nameless(place)))
        session = self.session(host)
        if getattr(session, "lb_route", "") == "direct":
            from core import name_lookup          # (v5.44.162) a name the system did not find lately: asked ahead
            name_lookup.expect(url)
        last = ""
        silent = False
        nameless = False                 # (v5.44.162) the computer did not find the site's name
        looked = False                   # …and its name was asked of a public resolver (core/name_lookup), once
        timeout = timeout or self.timeout
        attempt = 0
        while attempt < (tries or self.tries):
            attempt += 1
            if self.stop_check():
                raise SourceError("остановлено")
            if paced:
                self._wait_turn(host)
            try:
                with session.request(method, url, headers=headers or {}, data=data, timeout=timeout,
                                     stream=True) as answer:
                    silent = False
                    if watched and place in _SILENT:
                        forget_silent(place)             # it answers again
                    status = answer.status_code
                    ctype = str(answer.headers.get("Content-Type") or "").lower()
                    body = b""
                    if "html" in ctype and status in (200, 403, 404, 429, 503):
                        # (v5.44.72) a check for robots instead of the page: said, never asked again
                        whole = status == 200 or keep_missing and status in (404, 410)
                        body = self._read(answer, limit if whole else 256 * 1024, what, cut=not whole)
                        guard = robot_check(status, body, str(answer.url or url))
                        if guard is not None:
                            raise Closed(closed_words(what, guard))
                    if status in (429, 500, 502, 503, 504, 520, 521, 522, 524):
                        last = f"{what} ответил {status}"
                        wait = float(answer.headers.get("Retry-After") or 0) if str(
                            answer.headers.get("Retry-After") or "").isdigit() else 0
                        if attempt < (tries or self.tries):      # (v5.44.97) the last try is not waited after
                            time.sleep(min(30.0, max(wait, 2.0 * attempt)))
                        continue
                    if status in (404, 410) and keep_missing and "html" in ctype and body:
                        return bytes(body), ctype                 # (v5.44.99) the page all the same
                    if status in (404, 410):
                        raise Missing(f"{what} ответил {status}")
                    if status in (401, 403, 451):
                        raise Refused(f"{what} ответил {status}")
                    if status >= 400:
                        raise SourceError(f"{what} ответил {status}")
                    if status == 200 and "html" in ctype:
                        return bytes(body), ctype
                    return self._read(answer, limit, what, picture=limit == PICTURE_LIMIT), ctype
            except (requests.ConnectionError, requests.Timeout) as exc:
                last = f"{what} не ответил: {library_words(exc)}"
                silent = True
                from core import name_lookup
                nameless = name_lookup.not_found(exc)
                if getattr(session, "lb_route", "") == "direct" and _system_proxy(url):
                    _LOG.info("%s: not reached straight — through the system's proxy from now on", host)
                    session.trust_env = True
                    session.lb_route = "system"
                    _PROXIED.add(host)
                    # (v5.44.162) with one try allowed (a feed's page of nhentai) the proxy was never asked: the try
                    # went on the straight way, and the site was «silent» for three minutes before it was asked there
                    if attempt >= (tries or self.tries):
                        attempt -= 1
                    continue
                if nameless and not looked:
                    # (v5.44.162) the computer does not find the site's name and there is no other way to it: the
                    # name is asked of a public resolver; given — the same request once more, by the address
                    looked = True
                    if name_lookup.learn(host):
                        attempt -= 1
                        continue
                if attempt < (tries or self.tries):
                    time.sleep(1.5 * attempt)
            except requests.RequestException as exc:
                raise SourceError(f"{what} не ответил: {library_words(exc)}") from exc
        if silent and watched:                        # (v5.44.98) no answer at all: not asked again a while
            with _SILENT_LOCK:
                _SILENT[place] = (time.monotonic() + SILENT_FOR, last, nameless)
            _LOG.info("%s: silent — its pages not asked for %d s (%s)", what, SILENT_FOR, last)
            raise Silent(silent_words(what, SILENT_FOR, nameless=nameless))     # words for a person, not the library's
        raise SourceError(last or f"{what} не ответил")

    def _read(self, answer, limit: int, what: str, cut: bool = False, *, picture: bool = False) -> bytes:
        """The body of an answer up to ``limit`` bytes: more — :class:`SourceError` (``cut`` — only that much)."""
        body = bytearray()
        for chunk in answer.iter_content(128 * 1024):
            if picture:
                download_telemetry.record(net=len(chunk))
            body += chunk
            if len(body) > limit:
                if cut:
                    return bytes(body[:limit])
                raise SourceError(f"{what} прислал слишком большой ответ")
            if self.stop_check():
                raise SourceError("остановлено")
        return bytes(body)

    def json(self, url: str, **options):
        body, _ctype = self.fetch(url, **options)
        try:
            return json.loads(body.decode("utf-8", "replace"))
        except ValueError as exc:
            raise SourceError(f"{options.get('what') or urlparse(url).hostname} прислал не то, что ждали") from exc

    def text(self, url: str, **options) -> str:
        body, _ctype = self.fetch(url, **options)
        return body.decode("utf-8", "replace")


# (v5.44.132) the owner's sign-in given to the window's browser (``share_signin``): its origin → the cookies given
_SHARED: dict = {}
_SHARED_LOCK = threading.Lock()


def browser_session_changed(hosts) -> None:
    """A manual check switched profiles; send the app's own sign-in to the selected browser again."""
    from core import site_browser
    sites = {site_browser.site_of(host) for host in hosts}
    with _SHARED_LOCK:
        for origin in list(_SHARED):
            if site_browser.site_of(urlparse(origin).hostname or "") in sites:
                _SHARED.pop(origin, None)


def _signin_key(c: dict) -> tuple:
    return str(c.get("domain") or ""), str(c.get("path") or "/"), str(c.get("name") or "")


def share_signin(url: str) -> None:
    """(v5.44.132) The owner's sign-in on the site of ``url`` — his cookies of it, not the pass of his own browser's
    check (:data:`AGENT_BOUND`: the window's browser has its own) — given to the window's browser before it asks the
    site, once while they are the same; the ones he no longer gives are taken back out of it.  A site whose pages and
    pictures only the browser itself gets shows some of them only to the signed in (a third of Nude-Moon's readers)."""
    from core import site_browser
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower()
    if not host or parsed.scheme not in ("http", "https"):
        return
    origin = f"{parsed.scheme}://{parsed.netloc.lower()}"
    mine = [dict(c) for c in signed_cookies(host) if not str(c.get("name") or "").startswith(AGENT_BOUND)]
    with _SHARED_LOCK:
        before = list(_SHARED.get(origin) or [])
    if sorted((_signin_key(c), str(c.get("value") or "")) for c in mine) == \
            sorted((_signin_key(c), str(c.get("value") or "")) for c in before):
        return
    gone = [c for c in before if _signin_key(c) not in {_signin_key(x) for x in mine}]
    try:
        if gone:
            site_browser.share_cookies(origin, gone, forget=True)
        if mine:
            site_browser.share_cookies(origin, mine)
    except site_browser.BrowserError as exc:
        _LOG.info("%s: the sign-in not given to the window's browser: %s", host, exc)
        return
    with _SHARED_LOCK:
        _SHARED[origin] = mine
    _LOG.info("%s: the owner's sign-in given to the window's browser (%d cookies, %d taken back)", host, len(mine),
              len(gone))


def forget_shared(name: str = "", cookies=None) -> None:
    """(v5.44.132) «Убрать» of «Сайты манги»: the owner's sign-in of the site ``name`` (every site without it) taken
    back out of the window's browser — what was given to it in this run and ``cookies`` (the ones the program kept of
    that site: given in an earlier run, they are in the browser still)."""
    from core import site_browser
    source = by_name(name) if name else None
    with _SHARED_LOCK:
        taken = {o: list(c) for o, c in _SHARED.items()
                 if source is None or _host_of(o + "/", source.hosts, source.site())}
        for origin in taken:
            _SHARED.pop(origin, None)
    for c in cookies or []:
        domain = str(c.get("domain") or "").lower().lstrip(".")
        if domain and not str(c.get("name") or "").startswith(AGENT_BOUND):
            mine = taken.setdefault(f"https://{domain}", [])
            if _signin_key(c) not in {_signin_key(x) for x in mine}:
                mine.append(dict(c))
    for origin, given in taken.items():
        try:
            if given:
                site_browser.share_cookies(origin, given, forget=True)
        except site_browser.BrowserError as exc:
            _LOG.info("%s: the sign-in not taken back from the window's browser: %s", origin, exc)


# (v5.44.85) the hosts that did not open straight in this run: the next looks go through the system's proxy at once
# (a site that hangs without the owner's VPN — Хентай-тян — waited 40 seconds each time before)
_PROXIED: set = set()


def _system_proxy(url: str) -> bool:
    import requests
    try:
        return bool(requests.utils.get_environ_proxies(url))
    except (OSError, ValueError):
        return False


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────────────────────
def plain(text) -> str:
    """HTML of a description → its text: paragraphs kept, tags gone, entities read."""
    text = str(text or "")
    text = re.sub(r"(?i)<\s*br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</\s*p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text).replace("\r", "")
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text).strip()


def _names(items) -> list:
    return [str(x.get("name") or "").strip() for x in items or [] if isinstance(x, dict) and x.get("name")]


def _num(value) -> str:
    """A chapter's number as a string: «12», «12.5», «0.5» (no zeros in front of it or at the end of its fraction;
    (v5.44.62) «.5» and «0.5» are one number — «0.5» had become «.5» before)."""
    text = str(value if value is not None else "").strip().replace(",", ".")
    m = re.fullmatch(r"(\d*)(?:\.(\d*))?", text)
    if m and text and text != ".":
        whole = m.group(1).lstrip("0") or "0"
        part = (m.group(2) or "").rstrip("0")
        return f"{whole}.{part}" if part else whole
    return text


def _ours(url: str, site: str, hosts: tuple) -> bool:
    """The link is of this site: one of its hosts (with or without «www.»), or the very address the site is at now
    (the checks' own site on this machine)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return host in hosts or bool(parsed.netloc) and parsed.netloc.lower() == urlparse(site).netloc.lower()


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _year(text) -> int:
    """The year in «2021», «2021 г.» or (v5.44.63) «05 августа 2021 г.» (MangaLib's search says it so)."""
    m = re.search(r"(?<!\d)((?:19|20)\d\d)(?!\d)", str(text or ""))
    return int(m.group(1)) if m else 0


def _text(fragment: str) -> str:
    """A piece of HTML as one line of text."""
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def _block(page: str, cls: str) -> str:
    """The inside of the first element with the class ``cls`` (to its closing tag at the same depth)."""
    return _inside(page, re.search(r'<(\w+)[^>]*class="[^"]*\b' + re.escape(cls) + r'\b[^"]*"[^>]*>', page))


def _by_id(page: str, ident: str) -> str:
    """(v5.44.85) The inside of the element with the id ``ident``."""
    return _inside(page, re.search(r'<(\w+)\b[^>]*\bid=["\']' + re.escape(ident) + r'["\'][^>]*>', page))


def _inside(page: str, m) -> str:
    """The inside of the element whose opening tag ``m`` found (its tag's name — the group 1)."""
    if not m:
        return ""
    tag, pos, depth = m.group(1), m.end(), 1
    opener, closer = re.compile(r"<" + tag + r"\b", re.I), re.compile(r"</" + tag + r"\s*>", re.I)
    while depth and pos < len(page):
        o, c = opener.search(page, pos), closer.search(page, pos)
        if c is None:
            break
        if o is not None and o.start() < c.start():
            depth, pos = depth + 1, o.end()
        else:
            depth, pos = depth - 1, c.end()
            if depth == 0:
                return page[m.end():c.start()]
    return page[m.end():m.end() + 4000]


def _host_of(url: str, hosts: tuple, site: str) -> bool:
    """(v5.44.77) The link is of this site: one of its domains or a subdomain of one («a.zazaza.me»), or the very
    address the site is at now."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return any(host == h or host.endswith("." + h) for h in hosts) or \
        bool(parsed.netloc) and parsed.netloc.lower() == urlparse(site).netloc.lower()


def _date(stamp) -> str:
    """(v5.44.77) A unix time → «2016-06-01T09:05:38» (UTC); "" — none."""
    try:
        value = float(stamp)
    except (TypeError, ValueError):
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(value)) if value > 0 else ""


# ── (v5.44.62) one title on two sites ────────────────────────────────────────────────────────────────────────────
_LOOKALIKE = str.maketrans("авекмнорстухёіјѕ", "abekmhopctyxeijs")
_TO_LATIN = str.maketrans({"А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
                           "Т": "T", "Х": "X", "а": "a", "е": "e", "к": "k", "о": "o", "р": "p", "с": "c", "у": "y",
                           "х": "x", "і": "i", "ј": "j", "ѕ": "s"})
_KINDS = {"манхва": "manhwa", "маньхуа": "manhua", "манга": "manga", "oel-манга": "manga", "комикс": "comic",
          "западный комикс": "comic", "комикс западный": "comic", "рукомикс": "rucomic", "руманга": "rucomic",
          "индонезийский комикс": "indo"}
SEARCHES = 3                 # names a site's search is asked by: the main one and two in Latin letters
LOOKS = 3                    # titles of its answers asked for


def name_key(text) -> str:
    """A name as two sites agree on it: letters and digits alone, one case, «ё» as «е», the Cyrillic letters that look
    like Latin ones read as those (so «Cultivation of Hеavеn» with Russian «е» is «Cultivation of Heaven»)."""
    text = unicodedata.normalize("NFKC", str(text or "")).casefold().translate(_LOOKALIKE)
    return "".join(ch for ch in text if ch.isalnum())


def _cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x3040 <= o <= 0x30FF or 0x3400 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF or 0xF900 <= o <= 0xFAFF


def names_of(*names) -> set:
    """The names that tell a title: four letters at least (two of Chinese, Japanese or Korean script)."""
    out = set()
    for name in names:
        key = name_key(name)
        if len(key) >= 4 or len(key) >= 2 and any(_cjk(ch) for ch in key):
            out.add(key)
    return out


def _fits(kind_a: str, year_a: int, kind_b: str, year_b: int) -> bool:
    """Not two different things: the kind of comic the same when both sites say it, the year a year apart at most."""
    a, b = _KINDS.get(str(kind_a or "").strip().casefold(), ""), _KINDS.get(str(kind_b or "").strip().casefold(), "")
    if a and b and a != b:
        return False
    return not (year_a and year_b and abs(int(year_a) - int(year_b)) > 1)


def same_title(a: "TitleRef", b: "TitleRef") -> bool:
    """Two sites' titles are one: a name of each the same (``name_key``), the kind and the year not different."""
    return bool(names_of(a.title, *a.alt_titles) & names_of(b.title, *b.alt_titles)) and \
        _fits(a.type, a.year, b.type, b.year)


def _latin(name: str) -> str:
    """The name, if it is written in Latin letters, with the Cyrillic look-alikes in it put right; else ""."""
    letters = [ch for ch in str(name or "") if ch.isalpha()]
    if not letters or sum(1 for ch in letters if "a" <= ch.lower() <= "z") < 0.6 * len(letters):
        return ""
    return str(name).translate(_TO_LATIN).strip()


def queries_of(title: "TitleRef") -> list:
    """What another site's search is asked: the title's main name, then its names in Latin letters (a site may know
    it by another English name: Remanga's «Huashan reborn» is «Return of the Mount Hua Sect» elsewhere)."""
    out = [title.title.strip()] if title.title.strip() else []
    for name in title.alt_titles:
        latin = _latin(name)
        if latin and latin.casefold() not in {x.casefold() for x in out}:
            out.append(latin)
    return out[:SEARCHES]


def find_same(net: "Net", source: "Source", title: "TitleRef") -> "TitleRef | None":
    """The same title on ``source``: its search asked by the title's names; the answers that agree in a name (and in
    the kind and the year) asked for and checked by every name they have.  When none agrees, the search's first answer
    is asked for too if it carries one name only (MangaBuff's carry only the Russian name, and a site's Russian name of
    a title may be its own).  None — not there.  (v5.44.146) A site none of whose searches answered (it was silent,
    answered an error, would not let in) raises that error: «не ответил» is not «тайтла там нет» — a site that was
    down when a title was found used to be kept as searched, and was not asked of that title again for a week
    (``manhwa_download._helpers_of``).  A search that answers 404 has answered: nothing there."""
    wanted = names_of(title.title, *title.alt_titles)
    asked: set = set()
    answered, unanswered = False, None
    for query in queries_of(title):
        try:
            hits = source.search(net, query)
        except Closed:
            raise                                  # (v5.44.72) said by the one asking: the site lets no program in
        except SourceError as exc:
            _LOG.info("%s: the search for %r: %s", source.label, query, exc)
            if isinstance(exc, Missing):
                answered = True
            else:
                unanswered = exc
            continue
        answered = True
        near = [h for h in hits if names_of(*h.names) & wanted and _fits(h.type, h.year, title.type, title.year)]
        if not near:
            near = [h for h in hits[:1] if len(h.names) <= 1 and _fits(h.type, h.year, title.type, title.year)]
        for hit in near:
            if hit.key in asked or len(asked) >= LOOKS:
                continue
            asked.add(hit.key)
            try:
                other = source.title(net, hit.key)
            except SourceError as exc:
                _LOG.info("%s: %s not asked for: %s", source.label, hit.key, exc)
                continue
            if same_title(title, other):
                return other
    if not answered and unanswered is not None:
        raise unanswered
    return None


# ── the sites ────────────────────────────────────────────────────────────────────────────────────────────────────
class Source:
    name = ""
    label = ""
    hosts: tuple = ()                  # the site's domains (a link of one is the site's)
    paid_never = True                  # the owner's rule — a site's paid chapter is not taken
    readable = True                    # (v5.44.72) False — a site the program never asks (it lets no program in)
    languages = False                  # (v5.44.79) its branches are languages (MangaDex), not translators
    # (v5.44.85) everything there is hentai (Хентай-тян): asked only where hentai is shown («SFW» — never), and for the
    # chapters of another site's title only when that title is erotic
    adult_only = False
    SIGNED_ONLY = False                # (v5.44.96) a chapter's pages to a sign-in alone (HentaiLib, (v5.44.99) AllHentai)
    # (v5.44.169) What the rest of the program asks and says of a site is told here, with the site — the lists of
    # sites elsewhere are made of it (``sites_by_name``, ``cover_hosts``, ``labels_said``, ``default_address``,
    # ``API_OF``; ``manhwa_search.ASKED`` / ``COVER_HOSTS``, the hints of «Скачать»): a site added is added once.
    address_value = ""                 # the name of this module's value with its address («REMANGA_SITE»)
    api_value = ""                     # …with its API's («REMANGA_API»); "" — its pages are all it has
    by_name = True                     # «Найти по названию» asks it (False — its search goes through the window's
    #                                    browser, or it has none: its titles come by a link, or from «Лента»)
    cover_hosts: tuple = ()            # the hosts its covers come from: the covers' proxy lets these in
    shown = ""                         # its address as a hint names it («readmanga (a.zazaza.me)»); "" — hosts[0]
    feed_order = 0                     # its place in the simple feed; 0 — not offered there
    catalog_kinds: tuple = ()          # the combined feed's tabs whose requests this site can serve
    catalog_order = 0                  # its place in those tabs (independent of chapter-download priority)
    catalog_filters = False            # its combined catalog accepts filters
    legacy_search_order = 0            # historical name searches before 5.44.73; never inferred from by_name

    def site(self) -> str:
        """(v5.44.78) The site's address now: the owner's (``ADDRESSES``), else its own."""
        return address(self.name)

    def shown_as(self) -> str:
        """(v5.44.169) The site's address as a hint names it."""
        return self.shown or (self.hosts[0] if self.hosts else self.name)

    def signed_in(self) -> bool:
        """(v5.44.99) The program has the owner's sign-in there (a site of :data:`SIGNED_ONLY` reads chapters with it
        alone): a token (MangaLib), the site's cookies (AllHentai)."""
        return True

    def sign_in_words(self) -> str:
        """(v5.44.99) Where to sign in, said when a chapter of a sign-in alone is asked without one."""
        return SIGN_IN_FIRST.format(site=self.label)

    def key_of(self, url: str) -> str:
        raise NotImplementedError

    def identity_key(self, key: str) -> str:
        """Stable title identity for downloaded-card matching; the download key stays unchanged."""
        return str(key or "")

    @staticmethod
    def words(key: str) -> list:
        """(v5.44.72) The words of the title's name in its address: «9504-latna-saga-survival…» → latna, saga,
        survival…; MangaLib's «89959--yeokdagp-…», Remanga's «<29.04.2026>omniscient-reader_» — without the number
        and the date."""
        text = re.sub(r"<[^>]*>", "", unquote(str(key or ""))).lower()
        text = re.sub(r"^\d+-+", "", text)
        return [w for w in re.split(r"[-_.\s]+", text) if w]

    def title(self, net: Net, key: str) -> TitleRef:
        raise NotImplementedError

    def chapters(self, net: Net, title: TitleRef, branch: str = "") -> list:
        raise NotImplementedError

    def pictures(self, net: Net, title: TitleRef, chapter: ChapterRef) -> list:
        raise NotImplementedError

    def resolve_picture(self, net: Net, picture: Picture) -> Picture:
        """A viewer page resolved when its image is needed; ordinary image addresses are already ready."""
        return picture

    def search(self, net: Net, query: str) -> list:
        """(v5.44.62) The site's titles its search finds by ``query``: :class:`Hit`."""
        return []

    def feed(self, net: Net, how: str, page: int) -> tuple:
        """(v5.44.83) «Лента» of the tab «Манхва»: the site's titles updated lately (``how`` "new") or the most popular
        ("popular"), page ``page`` (from 1) — ([:class:`Hit`], whether there is a next page).  The ways the friend's
        reader Yomi 1.7 asks them (its ``popular``/``latest``)."""
        return [], False

    def referer(self, title: TitleRef) -> str:
        return title.url


SIGN_IN_FIRST = ("{site} даёт читать и скачивать главы только со входом: «Настройки → Манга и манхва» → вход MangaLib (он "
                 "общий для сайтов Lib)")


# ── what several sites share: the number and the name of a work's part in an entry's name (Хентай-тян,
# Nude-Moon — v5.44.85, v5.44.132), a page's text in the charset it names ─────────────────────────────────────────
_PART_AT = re.compile(r"(?i)(?<![^\W\d_])(?:глава|главы|часть|части|chapter|chap|ch|part|эпизод|episode|ep)\.?\s*[№#]?"
                      r"\s*(\d+(?:[.,]\d+)?)")
# (v5.44.132) «… №9-11» — a range of parts (Nude-Moon's entries of several chapters at once): its first number
_TRAILING_NUMBER = re.compile(r"[\s\-–—:,.#№]+(\d+(?:[.,]\d+)?)(?:\s*[-–—]\s*\d+(?:[.,]\d+)?)?\s*$")


def _decoded(body: bytes, ctype: str = "") -> str:
    """(v5.44.85) A page's text in the charset it names (its header, else its ``<meta>``) — an old site's
    windows-1251 too; UTF-8 when it names none."""
    m = re.search(r"charset=[\"']?([\w-]+)", str(ctype or ""), re.I)
    charset = m.group(1) if m else ""
    if not charset:
        meta = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", bytes(body[:4096]), re.I)
        charset = meta.group(1).decode("ascii", "replace") if meta else "utf-8"
    try:
        return bytes(body).decode(charset, "replace")
    except LookupError:
        return bytes(body).decode("utf-8", "replace")


def _cyrillic(text: str) -> bool:
    return bool(re.search(r"[а-яё]", str(text or ""), re.I))


def _shown_name(main: str, others: list) -> tuple:
    """(the title's name, its other names) of an entry: its Russian name when the site's own is not Russian — not of a
    part («… - глава 18 (Полная деградация)»: the Russian name there may be the part's own)."""
    russian = next((x for x in others if _cyrillic(x)), "")
    if russian and not _cyrillic(main) and not _part_number(main):
        return russian, [main, *(x for x in others if x != russian)]
    return main, list(others)


def _part_number(name: str) -> str:
    """The number of a work's part in its name: «… глава 3» → "3", «… часть 2. Имя» → "2", «… 4» → "4"; "" — none."""
    m = _PART_AT.search(str(name or ""))
    if m:
        return _num(m.group(1))
    m = _TRAILING_NUMBER.search(str(name or ""))
    return _num(m.group(1)) if m and str(name)[:m.start()].strip() else ""


def _without_part(name: str) -> str:
    """A part's name without its number and what follows it: «Сосед — глава 3» → «Сосед», «Сосед - часть 5. Вторая
    жена» → «Сосед»."""
    text = str(name or "")
    m = _PART_AT.search(text)
    if m and text[:m.start()].strip(" -–—:,.([#№"):
        text = text[:m.start()]
    elif m:
        text = text[m.end():]
    else:
        text = _TRAILING_NUMBER.sub("", text)
    return " ".join(text.split()).strip(" -–—:,.([#№")


def _part_name(name: str) -> str:
    """A part's own name after its number: «Сосед - часть 5. Вторая жена» → «Вторая жена»; "" — none."""
    m = _PART_AT.search(str(name or ""))
    return " ".join(str(name)[m.end():].split()).strip(" -–—:,.)") if m and str(name)[:m.start()].strip() else ""


# ── (v5.44.168) the sites: a module each (``core/manhwa_sites/``) — imported here, after everything they use;
# their classes stay names of this module (``manhwa_sources.Remanga``, ``source_for``, ``SOURCES`` below)
from core.manhwa_sites.remanga import ReComics, Remanga  # noqa: E402
from core.manhwa_sites.mangabuff import MangaBuff  # noqa: E402
from core.manhwa_sites.mangalib import HentaiLib, MangaLib  # noqa: E402
from core.manhwa_sites.comx import ComX  # noqa: E402
from core.manhwa_sites.readmanga import AllHentai, ReadManga  # noqa: E402
from core.manhwa_sites.desu import Desu  # noqa: E402
from core.manhwa_sites.onelove import MangaOneLove  # noqa: E402
from core.manhwa_sites.mangadex import MangaDex  # noqa: E402
from core.manhwa_sites.hentaichan import Hentaichan  # noqa: E402
from core.manhwa_sites.nudemoon import NudeMoon  # noqa: E402
from core.manhwa_sites.ehentai import EHentai  # noqa: E402


KNOWN_TYPES = ("Манхва", "Манга", "Маньхуа", "Комикс", "Комикс западный", "Западный комикс", "Рукомикс",
               "Индонезийский комикс", "OEL-манга", "Другое")
KNOWN_STATUSES = ("Продолжается", "Завершен", "Завершён", "Заморожен", "Анонс", "Приостановлен", "Выпуск прекращён",
                  "Нет переводчика")


def _number_key(number: str) -> float:
    try:
        return float(number)
    except (TypeError, ValueError):
        return float("inf")


# (v5.44.73) com-x.life — through the browser, last; (v5.44.77) ReadManga and Desu after MangaBuff; (v5.44.79)
# MangaDex after all (its Russian chapters for the others' titles); (v5.44.85) Хентай-тян, a site of hentai alone — the
# very last; (v5.44.131) Nude-Moon after it (behind Cloudflare's check: its window's browser is slow to ask)
SOURCES = (Remanga(), ReComics(), MangaLib(), MangaBuff(), ReadManga(), Desu(), ComX(), MangaOneLove(),  # (v5.44.143)
           MangaDex(), HentaiLib(), AllHentai(), Hentaichan(), NudeMoon(), EHentai())


def readable() -> list:
    """(v5.44.72) The sites the program asks, in the owner's order."""
    return [s for s in SOURCES if s.readable]


def source_for(url: str) -> tuple:
    """(the site, the title's key on it) of a link; (None, "") — no site the program knows."""
    url = str(url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    for source in SOURCES:
        key = source.key_of(url)
        if key:
            return source, key
    return None, ""


def by_name(name: str) -> "Source | None":
    return next((s for s in SOURCES if s.name == name), None)


def title_identity(site: str, key: str) -> tuple:
    """A site's stable title key, shared by the catalog and browser companion."""
    source = by_name(str(site or ""))
    return str(site or ""), source.identity_key(key) if source is not None else str(key or "")


# ── (v5.44.169) the lists the rest of the program keeps of the sites — made of what the sites say of themselves ─────
API_OF = tuple(s.name for s in SOURCES if s.api_value)    # the sites whose API is an address of its own


def sites_by_name() -> tuple:
    """The names of the sites «Найти по названию» asks, in the owner's order (``manhwa_search.ASKED``)."""
    return tuple(s.name for s in SOURCES if s.by_name)


def feed_sites() -> tuple:
    """The simple feed's sites, in their stated order; chapter-only sources do not join it by inheritance."""
    return tuple(s.name for s in sorted(SOURCES, key=lambda s: s.feed_order) if s.feed_order)


def legacy_searched_sites() -> tuple:
    """The sites a saved title had already searched before 5.44.73 (a migration, not today's search list)."""
    return tuple(s.name for s in sorted(SOURCES, key=lambda s: s.legacy_search_order) if s.legacy_search_order)


def cover_hosts() -> tuple:
    """The hosts the covers of the sites come from, each once (``manhwa_search.COVER_HOSTS`` begins with them)."""
    return tuple(dict.fromkeys(host for s in SOURCES for host in s.cover_hosts))


def labels_said(sites=None) -> str:
    """The sites named in a sentence: «Remanga, ReComics, … или Nude-Moon» (every site; or ``sites``)."""
    labels = [s.label for s in (SOURCES if sites is None else sites)]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " или " + labels[-1] if labels else ""
