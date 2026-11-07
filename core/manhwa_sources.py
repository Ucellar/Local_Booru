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

(v5.44.78) «Адреса сайтов и вход» (``core/manhwa_access.py``, «Настройки → Сайты манги»): a site that moved is asked at
the owner's own address (:func:`use_addresses` — ``ADDRESSES``; every :class:`Source` asks ``site()``/``api()``, never
the module values directly; a link of the new address is the site's too), and the owner's sign-in on a site — its
cookies (:func:`use_signins`: each session of a host gets the cookies of its domain) and MangaLib's token (its API
with ``Authorization: Bearer`` — the titles 18+ it shows only to the signed in).  Their values never leave this module
and the sessions: not into pages, journals or errors.

(v5.44.85) Хентай-тян (:class:`Hentaichan`) — the owner (5.11.2026): «https://hentaichan.live/ еще добавь в
поддерживаемые».  A site of hentai alone (``Source.adult_only``): asked only where hentai is shown, for another site's
title only when it is erotic.  A title that depicts minors is neither opened nor taken (:class:`Excluded`).
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
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlparse, urlsplit, urlunsplit

# (v5.44.101) the program's rule about minors — in ``core/filter.py``; its names here for the code that uses them
from core.filter import depicts_minors  # noqa: F401 - used here and as S.depicts_minors elsewhere
from core.filter import HENTAILIB_NEVER_GENRES as F_HENTAILIB_NEVER_GENRES
from core.filter import HENTAILIB_NEVER_TAGS as F_HENTAILIB_NEVER_TAGS

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
TIMEOUT = (10, 40)
PAGE_LIMIT = 8 * 1024 * 1024          # a page or an API answer — bigger is not one
PICTURE_LIMIT = 40 * 1024 * 1024      # a slice of a strip is a few hundred KB
PACE = 0.35                           # seconds between two requests to one site's pages or API (not its pictures)
TRIES = 3
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 "
      "Safari/537.36")


# (v5.44.78) the owner's addresses of the sites (a site that moved) and his sign-in on them — ``core/manhwa_access.py``
ADDRESSES: dict = {}                  # the site's name (or «<name>_api») → «https://its.address»
_SIGNINS: dict = {}                   # the site's name → {"cookies": [{domain, name, value, path}], "token": "…"}
API_OF = ("remanga", "recomics", "mangalib", "mangadex", "hentailib")   # the sites whose API is an address of its own
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
    """(v5.44.78) The site's own address (the module's value — the checks put theirs there)."""
    return {"remanga": REMANGA_SITE, "remanga_api": REMANGA_API, "recomics": RECOMICS_SITE,
            "recomics_api": RECOMICS_API, "mangalib": MANGALIB_SITE, "mangalib_api": MANGALIB_API,
            "mangabuff": MANGABUFF_SITE, "comx": COMX_SITE, "readmanga": READMANGA_SITE, "desu": DESU_SITE,
            "mangadex": MANGADEX_SITE, "mangadex_api": MANGADEX_API, "hentaichan": HENTAICHAN_SITE,
            "hentailib": HENTAILIB_SITE, "hentailib_api": HENTAILIB_API,
            "allhentai": ALLHENTAI_SITE}.get(name, "")


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
    """(v5.44.78) The owner's sign-in in place: {site: {"cookies": [...], "token": "..."}} (``core/manhwa_access``)."""
    fresh = {}
    for name, value in dict(mapping or {}).items():
        if isinstance(value, dict):
            cookies = [c for c in value.get("cookies") or [] if isinstance(c, dict) and c.get("name")]
            fresh[str(name)] = {"cookies": cookies, "token": str(value.get("token") or "")}
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


class Excluded(SourceError):
    """(v5.44.85) A title the program neither opens nor takes, whatever its site says: it depicts minors."""


class Silent(SourceError):
    """(v5.44.98) The site did not answer at all — every try ran out of time or the line broke (Хентай-тян 6.11.2026:
    its pages hung for everyone — the program, a browser, the system's proxy, another computer).  Its pages are not
    asked again for :data:`SILENT_FOR` seconds (:func:`silent_left`): the next page of the program says so at once."""


SILENT_FOR = 3 * 60                 # (v5.44.98) seconds a silent site's pages are not asked again
_SILENT: dict = {}                  # host → (until, the words), monotonic
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


def silent_words(what: str, left: float) -> str:
    minutes = max(1, int(round(left / 60.0)))
    return f"{what} сейчас не отвечает — сайт молчит; программа спросит его снова через {minutes} мин"


EXCLUDED = "Этот тайтл программа не открывает и не скачивает: в нём изображены несовершеннолетние"


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


def fetch_picture(net: "Net", pic: Picture, *, what: str, headers: dict) -> tuple:
    """(v5.44.82) A chapter's picture: from its address, else from the site's other servers (MangaLib's — as the
    friend's reader Yomi does: «Перебирает зеркала картинок»).  (data, type) — or the first server's :class:`SourceError`.
    (v5.44.95) A server that is not the last one is asked once and shortly (:data:`MIRROR_TIMEOUT`: Хентай-тян's hung
    40 s three times for every picture); the picture hosts of a site of mirrors (:func:`picture_links`) — the one that
    gave a picture last first."""
    first = None
    links = list(dict.fromkeys(link for one in [pic.url, *pic.fallbacks] for link in picture_links(one)))
    for n, link in enumerate(links):
        last = n == len(links) - 1
        try:
            got = net.fetch(link, paced=False, limit=PICTURE_LIMIT, what=what, headers=headers,
                            **({} if last else {"timeout": MIRROR_TIMEOUT, "tries": 1}))
            gave_picture(link)
            return got
        except Closed:
            raise
        except SourceError as exc:
            first = first or exc
            _LOG.info("%s: a picture not given by %s: %s", what, urlparse(link).netloc, exc)
    raise first or SourceError(f"{what}: картинка не пришла")


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
        self._browser_lock = threading.Lock()    # (v5.44.73) one page of the browser at a time
        self._passes: dict = {}                  # the site → (cookies, agent) the browser got there
        self._browser_only: set = set()          # hosts whose pages only the browser itself gets

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

    def _open(self, url: str, closed: "Closed", what: str):
        """The page as the window's browser opens it — :class:`Closed` with words when it does not get in either."""
        from core import site_browser
        try:
            page = site_browser.open_page(url, still_check)
        except site_browser.BrowserError as exc:
            raise Closed(f"{closed} — браузер окна программы её не прошёл: {exc}") from exc
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
            raise Closed(f"{closed} — пускает только сам браузер окна программы, а он такое не скачивает")
        target = url if page_like else str(headers.get("Referer") or f"{urlparse(url).scheme}://{host}/")
        with self._browser_lock:
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
                raise
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
        with self._browser_lock:
            page = self._open(url, closed, what)
            self._take((urlparse(url).hostname or "").lower(), page)
        return page.html.encode("utf-8"), "text/html; charset=utf-8"

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
        try:
            return self._fetch(url, **options)
        except Closed as closed:
            return self._past_check(url, closed, options)

    def _fetch(self, url: str, *, method: str = "GET", headers: dict | None = None, data=None, limit: int = PAGE_LIMIT,
               paced: bool = True, what: str = "", timeout=None, tries: "int | None" = None,
               keep_missing: bool = False) -> tuple[bytes, str]:
        import requests
        from core.redaction import sanitize_text
        host = (urlparse(url).hostname or "").lower()
        what = what or host
        place = _place(url)
        # (v5.44.98) its pages and API, not pictures: a cover that hangs says nothing of the site's pages
        watched = paced and not str((headers or {}).get("Accept") or "").startswith("image/")
        if watched and silent_left(place):           # it did not answer a moment ago: said at once
            raise Silent(silent_words(what, silent_left(place)))
        session = self.session(host)
        last = ""
        silent = False
        timeout = timeout or self.timeout
        for attempt in range(1, (tries or self.tries) + 1):
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
                    return self._read(answer, limit, what), ctype
            except (requests.ConnectionError, requests.Timeout) as exc:
                last = f"{what} не ответил: {sanitize_text(str(exc))[:160]}"
                silent = True
                if getattr(session, "lb_route", "") == "direct" and _system_proxy(url):
                    _LOG.info("%s: not reached straight — through the system's proxy from now on", host)
                    session.trust_env = True
                    session.lb_route = "system"
                    _PROXIED.add(host)
                    continue
                if attempt < (tries or self.tries):
                    time.sleep(1.5 * attempt)
            except requests.RequestException as exc:
                raise SourceError(f"{what} не ответил: {sanitize_text(str(exc))[:160]}") from exc
        if silent and watched:                        # (v5.44.98) no answer at all: not asked again a while
            with _SILENT_LOCK:
                _SILENT[place] = (time.monotonic() + SILENT_FOR, last)
            _LOG.info("%s: silent — its pages not asked for %d s (%s)", what, SILENT_FOR, last)
            raise Silent(silent_words(what, SILENT_FOR))      # words for a person, not the library's
        raise SourceError(last or f"{what} не ответил")

    def _read(self, answer, limit: int, what: str, cut: bool = False) -> bytes:
        """The body of an answer up to ``limit`` bytes: more — :class:`SourceError` (``cut`` — only that much)."""
        body = bytearray()
        for chunk in answer.iter_content(128 * 1024):
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
    a title may be its own).  None — not there."""
    wanted = names_of(title.title, *title.alt_titles)
    asked: set = set()
    for query in queries_of(title):
        try:
            hits = source.search(net, query)
        except Closed:
            raise                                  # (v5.44.72) said by the one asking: the site lets no program in
        except SourceError as exc:
            _LOG.info("%s: the search for %r: %s", source.label, query, exc)
            continue
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

    def site(self) -> str:
        """(v5.44.78) The site's address now: the owner's (``ADDRESSES``), else its own."""
        return address(self.name)

    def signed_in(self) -> bool:
        """(v5.44.99) The program has the owner's sign-in there (a site of :data:`SIGNED_ONLY` reads chapters with it
        alone): a token (MangaLib), the site's cookies (AllHentai)."""
        return True

    def sign_in_words(self) -> str:
        """(v5.44.99) Where to sign in, said when a chapter of a sign-in alone is asked without one."""
        return SIGN_IN_FIRST.format(site=self.label)

    def key_of(self, url: str) -> str:
        raise NotImplementedError

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


class Remanga(Source):
    name = "remanga"
    label = "Remanga"
    hosts = ("remanga.org",)

    def site(self) -> str:
        return address(self.name) or REMANGA_SITE

    def api(self) -> str:
        return address(self.name + "_api") or _api_of(address(self.name)) or REMANGA_API

    def key_of(self, url: str) -> str:
        if not _ours(url, self.site(), self.hosts):
            return ""
        m = re.match(r"/manga/([^/?#]+)", urlparse(url).path)
        return m.group(1) if m else ""

    def title(self, net: Net, key: str) -> TitleRef:
        # (v5.44.62) a dir may hold «<>» (Remanga's «<29.04.2026>omniscient-reader_»): in the address it is quoted
        data = net.json(f"{self.api()}/api/v2/titles/{quote(unquote(key), safe='')}/", what=self.label,
                        headers={"Accept": "application/json", "Referer": self.site() + "/"})
        c = data.get("content", data) if isinstance(data, dict) else {}
        if not isinstance(c, dict) or not (c.get("main_name") or c.get("secondary_name")):
            raise Missing(f"{self.label} не знает такого тайтла")
        others = [x.strip() for x in str(c.get("another_name") or "").split(" / ") if x.strip()]
        alt = [x for x in [str(c.get("secondary_name") or "").strip(), *others] if x and x != c.get("main_name")]
        cover = c.get("cover") or {}
        cover_path = (cover.get("high") or cover.get("mid") or cover.get("low") or "") if isinstance(cover, dict) else ""
        branches = []
        for b in c.get("branches") or []:
            if isinstance(b, dict) and b.get("id") is not None:
                branches.append(Branch(id=str(b["id"]), name=", ".join(_names(b.get("publishers"))),
                                       votes=_int(b.get("total_votes")), count=_int(b.get("count_chapters"))))
        age = c.get("age_limit") if isinstance(c.get("age_limit"), dict) else {}
        dir_ = str(c.get("dir") or unquote(key))
        status = str((c.get("status") or {}).get("name") or "") if isinstance(c.get("status"), dict) else ""
        return TitleRef(
            site=self.name, key=dir_, url=f"{self.site()}/manga/{quote(dir_, safe='')}",
            title=str(c.get("main_name") or c.get("secondary_name")).strip(), alt_titles=alt,
            type=str((c.get("type") or {}).get("name") or "") if isinstance(c.get("type"), dict) else "",
            status=status, licensed=bool(c.get("is_licensed")) or status.casefold().startswith("лицензир"),
            year=_int(c.get("issue_year")), age=str(age.get("name") or ""), is_erotic=bool(c.get("is_erotic")),
            genres=_names(c.get("genres")), tags=_names(c.get("categories")), description=plain(c.get("description")),
            cover_url=urljoin(self.site() + "/", cover_path) if cover_path else "",
            authors=[x["name"] for x in c.get("creators") or [] if isinstance(x, dict) and x.get("name")
                     and x.get("type") == 1],
            branches=branches)

    def chapters(self, net: Net, title: TitleRef, branch: str = "") -> list:
        branch = branch or (title.best_branch().id if title.best_branch() else "")
        if not branch:
            return []
        out, page = [], 1
        while page and page <= 200:
            data = net.json(f"{self.api()}/api/v2/titles/chapters/?branch_id={branch}&ordering=index&page={page}"
                            f"&count=40", what=self.label, headers={"Accept": "application/json",
                                                                   "Referer": self.site() + "/"})
            items = data.get("results") if isinstance(data, dict) else None
            if items is None and isinstance(data, dict):
                items = data.get("content")
            for x in items or []:
                if not isinstance(x, dict) or x.get("id") is None:
                    continue
                if x.get("is_published") is False:
                    continue                                   # announced, not out yet
                paid = bool(x.get("is_paid")) and not bool(x.get("is_bought"))
                out.append(ChapterRef(id=str(x["id"]), tome=_int(x.get("tome")), number=_num(x.get("chapter")),
                                      name=str(x.get("name") or "").strip(), free=not paid,
                                      free_at=str(x.get("pub_date") or "") if paid else "",
                                      date=str(x.get("upload_date") or x.get("pub_date") or ""),
                                      url=f"{title.url}/{x['id']}", index=_int(x.get("index"))))
            page = page + 1 if isinstance(data, dict) and data.get("next") else 0
        out.sort(key=lambda ch: (ch.index, ch.tome, _number_key(ch.number)))
        return out

    def pictures(self, net: Net, title: TitleRef, chapter: ChapterRef) -> list:
        if not chapter.free:
            raise Paid("глава платная — её программа не скачивает")
        data = net.json(f"{self.api()}/api/v2/titles/chapters/{chapter.id}/", what=self.label,
                        headers={"Accept": "application/json", "Referer": self.site() + "/"})
        c = data.get("content", data) if isinstance(data, dict) else {}
        if not isinstance(c, dict):
            raise SourceError(f"{self.label} не дал главу")
        if c.get("is_paid") and not c.get("is_bought"):
            raise Paid("глава платная — её программа не скачивает")
        out = []
        for group in c.get("pages") or []:
            for p in (group if isinstance(group, list) else [group]):
                if isinstance(p, dict) and p.get("link"):
                    out.append(Picture(url=str(p["link"]), width=_int(p.get("width")), height=_int(p.get("height"))))
        if not out:
            raise SourceError(f"в главе на {self.label} нет страниц")
        return out

    def search(self, net: Net, query: str) -> list:
        data = net.json(f"{self.api()}/api/v2/search/?query={quote(query)}", what=self.label,
                        headers={"Accept": "application/json", "Referer": self.site() + "/"})
        return self._hits(data)

    FEED_ORDER = {"new": "-chapter_date", "popular": "-rating"}
    FEED_COUNT = 30

    def feed(self, net: Net, how: str, page: int) -> tuple:
        """(v5.44.83) Its catalog — ``/api/v2/search/catalog/`` (the old ``/api/search/catalog/`` when it is not)."""
        query = f"ordering={self.FEED_ORDER.get(how, '-chapter_date')}&count={self.FEED_COUNT}&page={max(1, page)}"
        headers = {"Accept": "application/json", "Referer": self.site() + "/"}
        try:
            data = net.json(f"{self.api()}/api/v2/search/catalog/?{query}", what=self.label, headers=headers)
        except Missing:
            data = net.json(f"{self.api()}/api/search/catalog/?{query}", what=self.label, headers=headers)
        hits = self._hits(data)
        if isinstance(data, dict) and "next" in data:
            return hits, bool(data.get("next"))
        props = data.get("props") if isinstance(data, dict) and isinstance(data.get("props"), dict) else {}
        if props.get("total_pages"):
            return hits, _int(props.get("page") or page) < _int(props.get("total_pages"))
        return hits, len(hits) >= self.FEED_COUNT

    def _hits(self, data) -> list:
        items = data.get("results") if isinstance(data, dict) else None
        if items is None and isinstance(data, dict):
            items = data.get("content")
        out = []
        for x in items or []:
            if not isinstance(x, dict) or not x.get("dir"):
                continue
            names = [x.get("main_name"), x.get("secondary_name"), *str(x.get("another_name") or "").split(" / ")]
            cover = x.get("cover") if isinstance(x.get("cover"), dict) else {}
            cover_path = str(cover.get("mid") or cover.get("low") or cover.get("high") or "")
            dir_ = str(x["dir"])
            out.append(Hit(site=self.name, key=dir_, names=[str(n).strip() for n in names if n and str(n).strip()],
                           type=str((x.get("type") or {}).get("name") or "") if isinstance(x.get("type"), dict) else "",
                           year=_int(x.get("issue_year")), url=f"{self.site()}/manga/{quote(dir_, safe='')}",
                           cover=urljoin(self.site() + "/", cover_path) if cover_path else "",
                           chapters=_int(x.get("count_chapters")), rating=_float(x.get("avg_rating")),
                           votes=_int(x.get("total_votes")), adult=bool(x.get("is_erotic"))))
        return out

    def referer(self, title: TitleRef) -> str:
        return self.site() + "/"


class MangaBuff(Source):
    name = "mangabuff"
    label = "MangaBuff"
    hosts = ("mangabuff.ru",)

    def site(self) -> str:
        return address(self.name) or MANGABUFF_SITE

    def key_of(self, url: str) -> str:
        if not _ours(url, self.site(), self.hosts):
            return ""
        m = re.match(r"/manga/([^/?#]+)", urlparse(url).path)
        return m.group(1) if m else ""

    @staticmethod
    def _text(fragment: str) -> str:
        return _text(fragment)

    def _block(self, page: str, cls: str) -> str:
        """The inside of the first element with the class ``cls`` (to its closing tag at the same depth)."""
        return _block(page, cls)

    def title(self, net: Net, key: str) -> TitleRef:
        url = f"{self.site()}/manga/{key}"
        page = net.text(url, what="MangaBuff")
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
        if not h1:
            raise SourceError("на странице MangaBuff нет тайтла")
        name = self._text(h1.group(1))
        alt_block = self._block(page, "manga__name-alt")
        alt = [self._text(x) for x in re.findall(r"<span[^>]*>(.*?)</span>", alt_block, re.S)] or \
            [x.strip() for x in self._text(alt_block).split(" / ") if x.strip()]
        middle = self._block(page, "manga__middle-links")
        links = [self._text(x) for x in re.findall(r"<a[^>]*>(.*?)</a>", middle, re.S)]
        kind = next((x for x in links if x in KNOWN_TYPES), "")
        year = next((int(x) for x in links if re.fullmatch(r"(19|20)\d\d", x)), 0)
        status = next((x for x in links if x in KNOWN_STATUSES), "")
        tags_block = self._block(page, "tags")
        genres = [self._text(x) for x in re.findall(r"<a[^>]*>(.*?)</a>", tags_block, re.S)]
        genres = [g for g in genres if g and len(g) < 40]
        about = plain(self._block(page, "manga__description"))
        poster = re.search(r'<meta property="og:image" content="([^"]+)"', page)
        site_id = re.search(r'class="manga[\s"][^>]*data-id="(\d+)"', page) or re.search(r'data-manga-id="(\d+)"', page)
        erotic = any(g.lower() in ("хентай", "эротика", "hentai") for g in genres) or \
            bool(re.search(r'class="[^"]*\bmanga__age[^"]*"[^>]*>\s*18\+', page))
        return TitleRef(site=self.name, key=key, url=url, title=name, alt_titles=[a for a in alt if a and a != name],
                        type=kind, status=status, year=year, is_erotic=erotic, genres=genres, description=about,
                        cover_url=_html.unescape(poster.group(1)) if poster else "",
                        branches=[Branch(id="", name="MangaBuff")], site_id=site_id.group(1) if site_id else "")

    _ITEM = re.compile(r'<a[^>]+href="([^"]+/manga/[^"/]+/(\d+)/([\d.]+))"[^>]*class="chapters__item[^"]*"[^>]*>(.*?)</a>'
                       r'|<a[^>]+class="chapters__item[^"]*"[^>]*href="([^"]+/manga/[^"/]+/(\d+)/([\d.]+))"[^>]*>(.*?)</a>',
                       re.S)

    def _items(self, page: str) -> list:
        out = []
        for m in self._ITEM.finditer(page):
            href, tome, number, inner = (m.group(1), m.group(2), m.group(3), m.group(4)) if m.group(1) else \
                (m.group(5), m.group(6), m.group(7), m.group(8))
            tag = m.group(0)[:m.group(0).find(">") + 1]
            date = re.search(r'data-chapter-date="([^"]*)"', tag)
            own = re.search(r'class="chapters__name"[^>]*>(.*?)</div>', inner or "", re.S)
            out.append(ChapterRef(id=f"{tome}/{number}", tome=_int(tome), number=_num(number),
                                  name=self._text(own.group(1)) if own else "", date=date.group(1) if date else "",
                                  url=urljoin(self.site() + "/", _html.unescape(href))))
        return out

    def chapters(self, net: Net, title: TitleRef, branch: str = "") -> list:
        page = net.text(title.url, what="MangaBuff")
        found = {ch.id: ch for ch in self._items(page)}
        if "load-chapters-trigger" in page or "chapters/load" in page or len(found) >= 100:
            token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
            manga_id = title.site_id or (re.search(r'data-manga-id="(\d+)"', page) or [None, ""])[1]
            if token and manga_id:
                body, _ctype = net.fetch(f"{self.site()}/chapters/load", method="POST", data={"manga_id": manga_id},
                                         headers={"X-CSRF-TOKEN": token.group(1), "X-Requested-With": "XMLHttpRequest",
                                                  "Accept": "application/json", "Referer": title.url},
                                         what="MangaBuff")
                try:
                    more = json.loads(body.decode("utf-8", "replace")).get("content") or ""
                except (ValueError, AttributeError):
                    more = ""
                for ch in self._items(str(more)):
                    found.setdefault(ch.id, ch)
        out = sorted(found.values(), key=lambda ch: (ch.tome, _number_key(ch.number)))
        for n, ch in enumerate(out, 1):
            ch.index = n
        return out

    def pictures(self, net: Net, title: TitleRef, chapter: ChapterRef) -> list:
        page = net.text(chapter.url, what="MangaBuff", headers={"Referer": title.url})
        out = []
        for item in re.findall(r'<div[^>]*class="reader__item[^"]*"[^>]*>(.*?)</div>', page, re.S):
            img = re.search(r'<img[^>]*?(?:data-src|src)="([^"]+)"', item)
            if img and not img.group(1).startswith("data:"):
                out.append(Picture(url=urljoin(chapter.url, _html.unescape(img.group(1)))))
        if not out:
            if re.search(r"(?i)купить|платн|доступ[а-я]* по подписке", self._text(self._block(page, "reader"))):
                raise Paid("глава закрыта на MangaBuff — программа такие не скачивает")
            raise SourceError("на странице главы MangaBuff нет картинок")
        return out

    _CARD = re.compile(r'<a\b([^>]*\bclass="[^"]*\bcards__item\b[^"]*"[^>]*)>(.*?)</a>', re.S)

    def search(self, net: Net, query: str) -> list:
        page = net.text(f"{self.site()}/search?type=manga&q={quote(query)}", what="MangaBuff")
        return self._cards(page)

    FEED_ORDER = {"new": "updated_at", "popular": "real_views"}

    def feed(self, net: Net, how: str, page: int) -> tuple:
        """Its catalog page ``/manga?sort=…&page=N`` — the cards as its search has them."""
        text = net.text(f"{self.site()}/manga?sort={self.FEED_ORDER.get(how, 'updated_at')}"
                        + (f"&page={page}" if page > 1 else ""), what="MangaBuff")
        hits = self._cards(text)
        more = bool(re.search(r'class="pagination__button[^"]*"[^>]*>\s*<a[^>]*>\s*Вперёд', text)) or \
            bool(re.search(rf'[?&]page={page + 1}\b', text))
        return hits, more

    def _cards(self, page: str) -> list:
        out = []
        for m in self._CARD.finditer(page):
            href = re.search(r'href="([^"]+)"', m.group(1))
            slug = re.search(r"/manga/([^/?#]+)", _html.unescape(href.group(1))) if href else None
            if not slug:
                continue
            name = re.search(r'class="cards__name"[^>]*>(.*?)</div>', m.group(2), re.S)
            info = re.search(r'class="cards__info"[^>]*>(.*?)</div>', m.group(2), re.S)
            kind = self._text(info.group(1)).split(",")[0].strip() if info else ""
            # (v5.44.75) the poster in the card's style: «background-image: url('/x180/img/manga/posters/….jpg?…')»
            poster = re.search(r"background-image:\s*url\(\s*['\"]?([^'\")]+)", _html.unescape(m.group(2)))
            rating = re.search(r'class="cards__rating[^"]*"[^>]*>\s*([\d.,]+)\s*<', m.group(2))
            out.append(Hit(site=self.name, key=slug.group(1), names=[self._text(name.group(1))] if name else [],
                           type=kind if kind in KNOWN_TYPES else "", url=f"{self.site()}/manga/{slug.group(1)}",
                           cover=urljoin(self.site() + "/", poster.group(1).strip()) if poster else "",
                           rating=_float(rating.group(1)) if rating else 0.0))
        return out

    def referer(self, title: TitleRef) -> str:
        return self.site() + "/"


class ReComics(Remanga):
    """(v5.44.61) recomics.org — Remanga's sister site (Russian comics, «Рукомикс»): the same API v2 at its own address;
    its title comes without the «content» wrapper (read either way)."""
    name = "recomics"
    label = "ReComics"
    hosts = ("recomics.org",)

    def site(self) -> str:
        return address(self.name) or RECOMICS_SITE

    def api(self) -> str:
        return address(self.name + "_api") or _api_of(address(self.name)) or RECOMICS_API


class MangaLib(Source):
    """(v5.44.61) mangalib.me — its API (``Site-Id: 1``; looked at from the owner's computer, 4.11.2026):
    ``/api/manga/<id--slug>?fields[]=…`` — the title (``rus_name``, ``eng_name``, ``otherNames``, ``type``, ``status``,
    ``ageRestriction``, ``releaseDate``, ``summary`` — a document of paragraphs, ``genres``, ``tags``, ``authors``,
    ``cover``); ``/api/manga/<id--slug>/chapters`` — every chapter with its translations (``branches``: ``branch_id``,
    its ``teams``; a paid one — ``restricted_view`` {is_open: false, expired_at — when it opens}); ``/api/manga/<id--slug>
    /chapter?number=&volume=[&branch_id=]`` — its ``pages`` [{url «//manga/…», width, height}] on the picture server
    (``/api/constants?fields[]=imageServers``: «main» of site 1).  It has no votes: the translators with the most
    chapters are taken."""
    name = "mangalib"
    label = "MangaLib"
    hosts = ("mangalib.me", "mangalib.org", "test-front.mangalib.me")
    FIELDS = ("summary", "genres", "tags", "authors", "otherNames", "releaseDate")
    SITE_ID = 1                                 # (v5.44.96) its site of the Lib sites' API (HentaiLib — 4)

    def site(self) -> str:
        return address(self.name) or MANGALIB_SITE

    def api(self) -> str:
        return address(self.name + "_api") or MANGALIB_API

    def token(self) -> str:
        return token_of(self.name)

    def images(self) -> str:
        return MANGALIB_IMAGES

    def headers(self) -> dict:
        out = {"Accept": "application/json", "Site-Id": str(self.SITE_ID), "Referer": self.site() + "/",
               "Origin": self.site()}
        token = self.token()
        if token:                               # (v5.44.78) the owner's sign-in: the titles 18+ it shows only so
            out["Authorization"] = f"Bearer {token}"
        return out

    def _never(self) -> str:
        """(v5.44.96) What its lists leave out always — the program's rule about minors (HentaiLib's)."""
        return ""

    def key_of(self, url: str) -> str:
        if not _ours(url, self.site(), self.hosts):
            return ""
        path = urlparse(url).path
        # (v5.44.63) a link may name the title by its slug alone — «/ru/manga/yeokdagp-yeongji-sulgyesa» (the owner's,
        # 4.11.2026); the API takes either and answers with its «slug_url» («89959--…»), the key from then on
        for pattern in (r"/(?:[a-z]{2}/)?manga/([^/?#]+)",              # /ru/manga/<id--slug or slug>
                        r"/(?:[a-z]{2}/)?(\d+--[^/?#]+)",                # /ru/<id--slug>/read/…
                        r"/(?:[a-z]{2}/)?([a-z0-9][\w-]*)/read(?:/|$)",  # /ru/<slug>/read/…
                        r"/([a-z0-9][\w-]*-[\w-]+)(?:/v[\d.]+/c[\d.]+)?/?$"):  # the old /<slug>, /<slug>/v1/c1
            m = re.match(pattern, path)
            if m:
                return m.group(1)
        return ""

    @staticmethod
    def _summary(doc) -> str:
        """The description: a document of paragraphs (or plain text, or HTML)."""
        if isinstance(doc, str):
            return plain(doc)
        out = []

        def walk(node, line):
            if isinstance(node, dict):
                kind = node.get("type")
                if kind == "text":
                    line.append(str(node.get("text") or ""))
                elif kind == "hardBreak":
                    line.append("\n")
                for child in node.get("content") or []:
                    walk(child, line)
            elif isinstance(node, list):
                for child in node:
                    walk(child, line)
        for block in (doc or {}).get("content") or [] if isinstance(doc, dict) else []:
            line: list = []
            walk(block, line)
            text = "".join(line).strip()
            if text:
                out.append(text)
        return "\n\n".join(out)

    def _chapters_raw(self, net: Net, key: str) -> list:
        """Its chapters as the API gave them — asked once for a look (the title's translators and its chapters)."""
        if (self.name, key) not in net.kept:
            data = net.json(f"{self.api()}/api/manga/{key}/chapters", what=self.label, headers=self.headers())
            net.kept[(self.name, key)] = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"),
                                                                                                   list) else []
        return net.kept[(self.name, key)]

    def title(self, net: Net, key: str) -> TitleRef:
        fields = "&".join(f"fields[]={f}" for f in self.FIELDS)
        data = net.json(f"{self.api()}/api/manga/{key}?{fields}", what=self.label, headers=self.headers())
        c = data.get("data") if isinstance(data, dict) else None
        if not isinstance(c, dict) or not (c.get("rus_name") or c.get("name")):
            raise Missing(f"{self.label} не знает такого тайтла")
        name = str(c.get("rus_name") or c.get("name") or "").strip()
        alt = []
        for x in [c.get("eng_name"), c.get("name"), *(c.get("otherNames") or [])]:
            x = str(x or "").strip()
            if x and x != name and x not in alt:
                alt.append(x)
        if self.adult_only and depicts_minors([name, *alt, *_names(c.get("tags")), *_names(c.get("genres"))]):
            raise Excluded(EXCLUDED)                 # (v5.44.96) the program's rule — HentaiLib's titles
        year = _year(c.get("releaseDate") or c.get("releaseDateString"))
        label = lambda v: str(v.get("label") or v.get("name") or "") if isinstance(v, dict) else ""   # noqa: E731
        branches: dict = {}
        for ch in self._chapters_raw(net, str(c.get("slug_url") or key)):
            for b in ch.get("branches") or []:
                bid = str(b.get("branch_id") if b.get("branch_id") is not None else "0")
                entry = branches.setdefault(bid, Branch(id=bid, name=", ".join(_names(b.get("teams")))))
                entry.count += 1
        cover = c.get("cover") if isinstance(c.get("cover"), dict) else {}
        return TitleRef(site=self.name, key=str(c.get("slug_url") or key),
                        url=f"{self.site()}/ru/manga/{c.get('slug_url') or key}", title=name, alt_titles=alt,
                        type=label(c.get("type")), status=label(c.get("status")), year=year,
                        age=label(c.get("ageRestriction")), is_erotic=self.adult_only, genres=_names(c.get("genres")),
                        tags=_names(c.get("tags")), description=self._summary(c.get("summary")),
                        cover_url=str(cover.get("default") or cover.get("md") or ""),
                        authors=_names(c.get("authors")), branches=list(branches.values()))

    def chapters(self, net: Net, title: TitleRef, branch: str = "") -> list:
        branch = branch or (title.best_branch().id if title.best_branch() else "0")
        out = []
        for ch in self._chapters_raw(net, title.key):
            for b in ch.get("branches") or []:
                bid = str(b.get("branch_id") if b.get("branch_id") is not None else "0")
                if bid != branch:
                    continue
                locked = b.get("restricted_view") if isinstance(b.get("restricted_view"), dict) else None
                paid = bool(locked) and not locked.get("is_open")
                volume, raw = str(ch.get("volume") or ""), str(ch.get("number") or "")
                number = _num(raw)
                out.append(ChapterRef(id=f"{volume}/{raw}/{bid}", tome=_int(volume), number=number,
                                      name=str(ch.get("name") or "").strip(), free=not paid,
                                      free_at=str(locked.get("expired_at") or "") if paid else "",
                                      date=str(b.get("created_at") or ""), index=_int(ch.get("index")),
                                      url=f"{self.site()}/ru/{title.key}/read/v{volume}/c{number}"))
        out.sort(key=lambda c: (c.index, c.tome, _number_key(c.number)))
        return out

    SERVERS = ("main", "secondary", "compress")      # (v5.44.82) the order its picture servers are asked in

    def _image_servers(self, net: Net) -> list:
        """(v5.44.82) Its picture servers for its site, «main» first, then «secondary», «compress» and the others (the
        friend's reader Yomi tries them in turn); none named — :data:`MANGALIB_IMAGES` (HentaiLib's —
        :data:`HENTAILIB_IMAGES`)."""
        if (self.name, "servers") not in net.kept:
            found = []
            try:
                data = net.json(f"{self.api()}/api/constants?fields[]=imageServers", what=self.label,
                                headers=self.headers())
                servers = ((data.get("data") or {}).get("imageServers") or []) if isinstance(data, dict) else []
                mine = [s for s in servers if isinstance(s, dict) and s.get("url") and
                        self.SITE_ID in [_int(x) for x in (s.get("site_ids") or [])]]
                mine.sort(key=lambda s: self.SERVERS.index(s.get("id")) if s.get("id") in self.SERVERS else 9)
                for server in mine:
                    link = str(server["url"]).rstrip("/")
                    if link not in found:
                        found.append(link)
            except SourceError as exc:
                _LOG.info("%s's picture servers not asked: %s", self.label, exc)
            net.kept[(self.name, "servers")] = found or [self.images()]
        return net.kept[(self.name, "servers")]

    def pictures(self, net: Net, title: TitleRef, chapter: ChapterRef) -> list:
        if not chapter.free:
            raise Paid("глава платная — её программа не скачивает")
        volume, number, bid = (chapter.id.split("/") + ["", "", "0"])[:3]
        query = f"number={number}&volume={volume}" + (f"&branch_id={bid}" if bid and bid != "0" else "")
        try:
            data = net.json(f"{self.api()}/api/manga/{title.key}/chapter?{query}", what=self.label,
                            headers=self.headers())
        except Missing as exc:
            if self.SIGNED_ONLY and not self.signed_in():   # (v5.44.96) HentaiLib gives pages to a sign-in alone
                raise Closed(self.sign_in_words()) from exc
            raise
        c = data.get("data") if isinstance(data, dict) else None
        if not isinstance(c, dict):
            raise SourceError(f"{self.label} не дал главу")
        locked = c.get("restricted_view") if isinstance(c.get("restricted_view"), dict) else None
        if locked and not locked.get("is_open"):
            raise Paid("глава платная — её программа не скачивает")
        servers = self._image_servers(net)
        out = []
        for p in c.get("pages") or []:
            if isinstance(p, dict) and p.get("url"):
                link = str(p["url"])
                if link.startswith("http"):
                    links = [link]
                else:                                       # «//manga/…»: one slash; (v5.44.82) every server of it
                    links = [server + "/" + link.lstrip("/") for server in servers]
                out.append(Picture(url=links[0], width=_int(p.get("width")), height=_int(p.get("height")),
                                   fallbacks=links[1:]))
        if not out:
            raise SourceError(f"в главе на {self.label} нет страниц")
        return out

    SIGNED_ONLY = False                         # (v5.44.96) a chapter's pages to a sign-in alone (HentaiLib)

    def signed_in(self) -> bool:                # (v5.44.99) its token (MangaLib's for HentaiLib — one account)
        return bool(self.token())

    def search(self, net: Net, query: str) -> list:
        data = net.json(f"{self.api()}/api/manga?q={quote(query)}&site_id[]={self.SITE_ID}{self._never()}",
                        what=self.label, headers=self.headers())
        return self._hits(data)

    FEED_ORDER = {"new": "last_chapter_at", "popular": "views"}

    def feed(self, net: Net, how: str, page: int) -> tuple:
        data = net.json(f"{self.api()}/api/manga?site_id[]={self.SITE_ID}&page={max(1, page)}&sort_by="
                        f"{self.FEED_ORDER.get(how, 'last_chapter_at')}&sort_type=desc{self._never()}", what=self.label,
                        headers=self.headers())
        meta = data.get("meta") if isinstance(data, dict) and isinstance(data.get("meta"), dict) else {}
        hits = self._hits(data)
        return hits, bool(meta.get("has_next_page")) if "has_next_page" in meta else len(hits) >= 20

    def _hits(self, data) -> list:
        out = []
        for x in (data.get("data") if isinstance(data, dict) else None) or []:
            if not isinstance(x, dict) or not x.get("slug_url"):
                continue
            year = _year(x.get("releaseDate") or x.get("releaseDateString"))
            names = [str(n).strip() for n in (x.get("rus_name"), x.get("eng_name"), x.get("name")) if n and str(n).strip()]
            if self.adult_only and depicts_minors(names):
                continue                             # (v5.44.96) the program's rule — by the name too (HentaiLib)
            cover = x.get("cover") if isinstance(x.get("cover"), dict) else {}
            rating = x.get("rating") if isinstance(x.get("rating"), dict) else {}
            out.append(Hit(site=self.name, key=str(x["slug_url"]), names=names,
                           type=str((x.get("type") or {}).get("label") or "") if isinstance(x.get("type"), dict) else "",
                           year=year, url=f"{self.site()}/ru/manga/{x['slug_url']}",
                           cover=str(cover.get("thumbnail") or cover.get("default") or cover.get("md") or ""),
                           rating=_float(rating.get("average")), votes=_int(rating.get("votes")),
                           adult=self.adult_only))
        return out

    def referer(self, title: TitleRef) -> str:
        return self.site() + "/"


class HentaiLib(MangaLib):
    """(v5.44.96) HentaiLib (hentailib.me) — the owner (6.11.2026), asked whether to take it through his sign-in of
    MangaLib: «Ну дак делай».  The API of MangaLib (``api.cdnlibs.org``) with ``Site-Id: 4`` (asked from the owner's
    computer 6.11.2026): its list (60 a page), a title and its chapters — without a sign-in; a chapter's pages — 404
    without one: :data:`SIGNED_ONLY` — its own sign-in, else MangaLib's (one account of the Lib sites); its pictures —
    its own servers of ``/api/constants`` (site 4: img2h/img3h.hentaicdn.org).  Every title is «18+ (RX)»: a site of
    hentai alone.  The program's rule about minors: its tags «Лоликон», «Сетакон», «Юные», «Девочки-волшебницы» (and
    MangaLib's «Лоли» — its titles carry MangaLib's tags too), the genres «Школа», «Кодомо» — always left out of its
    lists (:data:`NEVER_TAGS`, :data:`NEVER_GENRES`); a title whose
    tags, genres or name say so (``depicts_minors``: «Школа Каста» had neither of them) — not a hit, and
    :class:`Excluded` when opened."""
    name = "hentailib"
    label = "HentaiLib"
    hosts = ("hentailib.me", "hentailib.org")
    adult_only = True
    SITE_ID = 4
    SIGNED_ONLY = True
    NEVER_TAGS = F_HENTAILIB_NEVER_TAGS        # «Лоликон», «Сетакон», «Юные», «Девочки-волшебницы», «Лоли» (core/filter.py)
    NEVER_GENRES = F_HENTAILIB_NEVER_GENRES    # «Школа», «Кодомо»

    def site(self) -> str:
        return address(self.name) or HENTAILIB_SITE

    def api(self) -> str:                      # the owner's address of MangaLib's API serves it too
        return address(self.name + "_api") or address("mangalib_api") or HENTAILIB_API

    def images(self) -> str:
        return HENTAILIB_IMAGES

    def token(self) -> str:                    # its own sign-in, else MangaLib's: one account of the Lib sites
        return token_of(self.name) or token_of("mangalib")

    def _never(self) -> str:
        return "".join(f"&tags_exclude[]={n}" for n in self.NEVER_TAGS) + \
            "".join(f"&genres_exclude[]={n}" for n in self.NEVER_GENRES)


SIGN_IN_FIRST = ("{site} даёт читать и скачивать главы только со входом: «Настройки → Сайты манги» → вход MangaLib (он "
                 "общий для сайтов Lib)")


class ComX(Source):
    """(v5.44.72) com-x.life — every page of it is a check for robots first (looked at from the owner's computer,
    5.11.2026); (v5.44.73) the program window's browser gets in (``Net`` — ``core/site_browser.py``), and its pages are
    read by ``core/manhwa_comx.py``: the title's page — its names, description, poster, facts and ``window.__DATA__``
    chapters; the reader — the pictures; the search of DataLife Engine.  Its chapters are all free; it has no
    translators to choose (one branch)."""
    name = "comx"
    label = "com-x.life"
    hosts = ("com-x.life",)

    def site(self) -> str:
        return address(self.name) or COMX_SITE

    def key_of(self, url: str) -> str:
        if not _ours(url, self.site(), self.hosts):
            return ""
        m = re.fullmatch(r"/(\d+)-([a-z0-9][a-z0-9-]*)\.html?", urlparse(url).path, re.I)
        return f"{m.group(1)}-{m.group(2).lower()}" if m else ""

    def _page(self, net: Net, url: str, referer: str = "") -> str:
        return net.text(url, what=self.label, headers={"Referer": referer or self.site() + "/",
                                                        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})

    def title(self, net: Net, key: str) -> TitleRef:
        from core import manhwa_comx as X
        url = f"{self.site()}/{key}.html"
        page = self._page(net, url)
        kept = X.keep("title", page)
        data = X.data_of(page)
        names = X.names_of(page)
        if not names:
            raise SourceError("на странице com-x.life нет тайтла" + (f" (она сохранена: {kept})" if kept else ""))
        news_id = str(data.get("news_id") or key.split("-", 1)[0])
        raw = data.get("chapters") if isinstance(data.get("chapters"), list) else X.chapters_in_html(page, news_id)
        net.kept[("comx", key)] = (news_id, raw)
        facts = X.facts_of(page)
        cover = X.meta(page, "og:image")
        genres = facts["genres"]
        return TitleRef(site=self.name, key=key, url=url, title=names[0], alt_titles=names[1:], type=facts["type"],
                        status=facts["status"], year=facts["year"], genres=genres,
                        is_erotic=any(g.casefold() in ("хентай", "эротика", "hentai") for g in genres),
                        description=X.description_of(page), cover_url=urljoin(url, cover) if cover else "",
                        branches=[Branch(id="", name="com-x.life")], site_id=news_id)

    def chapters(self, net: Net, title: TitleRef, branch: str = "") -> list:
        from core import manhwa_comx as X
        news_id, raw = net.kept.get(("comx", title.key)) or (title.site_id, None)
        if raw is None:
            page = self._page(net, title.url)
            data = X.data_of(page)
            news_id = str(data.get("news_id") or news_id or title.key.split("-", 1)[0])
            raw = data.get("chapters") if isinstance(data.get("chapters"), list) else X.chapters_in_html(page, news_id)
        found: dict = {}
        for x in raw:
            if not isinstance(x, dict) or x.get("id") is None:
                continue
            tome, number, own = X.numbers_of(x.get("title") or x.get("name") or "", x.get("posi"))
            ch = ChapterRef(id=str(x["id"]), tome=tome, number=number, name=own, date=str(x.get("date") or ""),
                            url=f"{self.site()}/reader/{news_id}/{x['id']}")
            old = found.get((tome, number))                # one chapter uploaded twice: the later one
            if old is None or _int(ch.id) > _int(old.id):
                found[(tome, number)] = ch
        out = sorted(found.values(), key=lambda c: (c.tome, _number_key(c.number)))
        for n, ch in enumerate(out, 1):
            ch.index = n
        return out

    def pictures(self, net: Net, title: TitleRef, chapter: ChapterRef) -> list:
        from core import manhwa_comx as X
        page = self._page(net, chapter.url, referer=title.url)
        urls = X.picture_urls(page, X.data_of(page), chapter.url, COMX_IMAGES)
        if not urls:
            kept = X.keep("reader", page)
            if X.CLOSED_WORDS.search(X.text_of(page)):
                raise Paid("глава закрыта на com-x.life — программа такие не скачивает")
            raise SourceError("на странице главы com-x.life нет картинок" + (f" (она сохранена: {kept})" if kept else ""))
        return [Picture(url=u) for u in urls]

    def search(self, net: Net, query: str) -> list:
        """DataLife Engine's search: its form (POST), else the same by the address (GET)."""
        from core import manhwa_comx as X
        form = {"do": "search", "subaction": "search", "story": query, "search_start": "0", "full_search": "0",
                "result_from": "1"}
        try:
            body, _ctype = net.fetch(f"{self.site()}/index.php?do=search", method="POST", data=form, what=self.label,
                                     headers={"Referer": self.site() + "/"})
            page = body.decode("utf-8", "replace")
        except (Closed, Refused, Missing):
            page = self._page(net, f"{self.site()}/index.php?do=search&subaction=search&story={quote(query)}")
        X.keep("search", page)
        return [Hit(site=self.name, key=key, names=names) for key, names in X.hits_of(page)]

    def referer(self, title: TitleRef) -> str:
        return self.site() + "/"


class ReadManga(Source):
    """(v5.44.77) ReadManga (the Grouple network; it moves from address to address — now ``a.zazaza.me``, looked at from
    the owner's computer, 5.11.2026) — pages, not an API.  The title's page: ``h1.cr-hero-names__main``, the other
    names (``.cr-hero-names__alt`` spans, ``alternativeHeadline``), the description (``.cr-description__content``), the
    kind and the year (``.cr-hero-short-details``: ``/list/category/…``, ``/list/year/…``), the genres and the tags
    (``/list/genre/…``, ``/list/tag/…`` — «# Тег»), the authors (``.cr-main-person-item__name``), the status
    (``data-production-status``), the poster (``.cr-hero-overlay__bg`` ``data-bg``); the chapters — rows ``tr.item-row``
    (``data-num`` — the number ×10; ``a.chapter-link`` — ``/<title>/vol<N>/<M>``, its text «3 - 235 Имя»;
    ``data-date-raw``).  A chapter's pictures — ``/<title>/vol<N>/<M>?mtr=true`` (the site's own «мне есть 18»):
    ``rm_h.readerInit(chapterInfo, [[server, '', path, width, height, ''], …])``; a chapter taken down (a licence) —
    404 and ``/static/deleted…`` pictures: :class:`Paid` (another site's copy is the only way).  Its search —
    ``/search/suggestion?query=`` (JSON; only this site's titles: a link of its own, ``type`` MANGA).  One translation
    to a chapter; all free."""
    name = "readmanga"
    label = "ReadManga"
    hosts = ("zazaza.me", "readmanga.live", "readmanga.io", "readmanga.me", "readmanga.ru", "readmanga.org")
    RESERVED = {"list", "search", "internal", "user", "users", "static", "news", "forum", "login", "register", "auth",
                "rss", "api", "help", "about", "top", "catalog", "random", "private", "ajax", "app", "uploads"}

    def site(self) -> str:
        return address(self.name) or READMANGA_SITE

    def key_of(self, url: str) -> str:
        if not _host_of(url, self.hosts, self.site()):
            return ""
        m = re.match(r"/([a-z0-9][a-z0-9_]*)(?:/|$)", urlparse(url).path, re.I)
        if not m or m.group(1).lower() in self.RESERVED or not re.search(r"[a-z]", m.group(1), re.I):
            return ""
        return m.group(1)

    def _page(self, net: Net, url: str, referer: str = "") -> str:
        return net.text(url, what=self.label, headers={"Referer": referer or self.site() + "/",
                                                        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})

    @staticmethod
    def _links(page: str, kind: str, cls: str = "") -> list:
        """The words of the links to the site's lists of ``kind`` (``cls`` — only the links of that class: the
        title's own genres, not the ones of the menus and of the other titles on its page)."""
        out = []
        for attrs, inner in re.findall(r'<a\b([^>]*href="/list/' + kind + r'/[^"]*"[^>]*)>(.*?)</a>', page, re.S):
            if cls and not re.search(r'class="[^"]*\b' + re.escape(cls) + r'\b', attrs):
                continue
            x = _text(inner).lstrip("#").strip()
            if x and x not in out:
                out.append(x)
        return out

    def title(self, net: Net, key: str) -> TitleRef:
        url = f"{self.site()}/{key}"
        page = self._page(net, url)
        h1 = re.search(r'<h1[^>]*class="[^"]*cr-hero-names__main[^"]*"[^>]*>(.*?)</h1>', page, re.S) or \
            re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
        name = _text(h1.group(1)) if h1 else ""
        if not name:
            raise SourceError(f"на странице {self.label} нет тайтла")
        alt: list = []
        for found in [*re.findall(r'itemprop="alternativeHeadline"\s+content="([^"]*)"', page),
                      *[_text(x) for x in re.findall(r"<span>([^<]+)</span>", _block(page, "cr-hero-names__alt"))]]:
            for x in str(_html.unescape(found)).split(" / "):
                x = x.strip()
                if x and x != name and x not in alt:
                    alt.append(x)
        hero = _block(page, "cr-hero-short-details")      # the kind and the year of the title, not of the menus
        kind = next((x for x in self._links(hero, "category") if x in KNOWN_TYPES), "")
        year = next((y for y in (_year(x) for x in self._links(hero, "year")) if y), 0)
        genres = [g[:1].upper() + g[1:] for g in self._links(page, "genre", "creation-element-tags__item")]
        status = re.search(r'data-production-status="[^"]*"[^>]*>(.*?)<', page, re.S)
        poster = re.search(r'class="cr-hero-overlay__bg[^"]*"[^>]*data-bg="([^"]+)"', page) or \
            re.search(r'"(https?://[^"\s]+/uploads/pics/[^"\s]+_o\.(?:jpe?g|png|webp)[^"\s]*)"', page)
        authors = []
        for x in re.findall(r'class="cr-main-person-item__name[^"]*"[^>]*>(.*?)</span>', page, re.S):
            x = _text(x)
            if x and x not in authors:
                authors.append(x)
        return TitleRef(site=self.name, key=key, url=url, title=name, alt_titles=alt, type=kind,
                        status=_text(status.group(1)) if status else "", year=year,
                        age=next(iter(self._links(page, "age_limitation")), ""),
                        is_erotic=any(g.casefold() in ("хентай", "эротика") for g in genres), genres=genres,
                        tags=[t[:1].upper() + t[1:] for t in self._links(page, "tag", "creation-element-tags__item")],
                        description=plain(_block(page, "cr-description__content")),
                        cover_url=_html.unescape(poster.group(1)) if poster else "", authors=authors,
                        branches=[Branch(id="", name=self.label)])

    _ROW = re.compile(r'<tr[^>]*class="[^"]*\bitem-row\b[^"]*"([^>]*)>(.*?)</tr>', re.S)

    def chapters(self, net: Net, title: TitleRef, branch: str = "") -> list:
        page = self._page(net, title.url)
        found: dict = {}
        for m in self._ROW.finditer(page):
            attrs, inner = m.group(1), m.group(2)
            link = re.search(r'<a[^>]*href="(/' + re.escape(title.key) + r'/vol(\d+)/([\d.]+))[^"]*"[^>]*>(.*?)</a>',
                             inner, re.S)
            if not link:
                continue
            tome, raw = _int(link.group(2)), link.group(3)
            tenth = re.search(r'data-num="(\d+)"', attrs)
            number = _num(f"{int(tenth.group(1)) // 10}.{int(tenth.group(1)) % 10}") if tenth else _num(raw)
            own = re.sub(r"^\s*\d+\s*-\s*[\d.]+\s*", "", _text(link.group(4)))
            own = re.sub(r"(?:^|\s)КОН$", "", own).strip()          # «КОН» — the title's last chapter
            date = re.search(r'data-date-raw="([^"]*)"', inner)
            found.setdefault((tome, number), ChapterRef(
                id=f"{tome}/{raw}", tome=tome, number=number, name=own,
                date=date.group(1).replace(" ", "T")[:19] if date else "", url=urljoin(self.site() + "/", link.group(1))))
        out = sorted(found.values(), key=lambda c: (c.tome, _number_key(c.number)))
        for n, ch in enumerate(out, 1):
            ch.index = n
        return out

    _ITEM = re.compile(r"\[\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*\"([^\"]*)\"\s*,\s*(\d+)\s*,\s*(\d+)")
    GONE = "глава убрана с ReadManga (обычно — по лицензии): там её больше нет"

    def pictures(self, net: Net, title: TitleRef, chapter: ChapterRef) -> list:
        def fetch(address: str) -> str:
            try:
                return self._page(net, address, referer=title.url)
            except Missing as exc:
                raise Paid(self.GONE) from exc
        return self._marked(title, chapter, fetch)

    @staticmethod
    def _address(url: str, mark: str) -> str:
        """A chapter's page as the site's reader asks it: «mtr=true» (its own «мне есть 18»); (v5.44.107) a signed-in
        reader's — with «d=<the account's mark>» too, as its script makes the chapters' links (``initUserHash``)."""
        parts = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in ("d", "mtr")]
        if mark:
            query.append(("d", mark))
        query.append(("mtr", "true"))
        return urlunsplit(parts._replace(query=urlencode(query)))

    def _marked(self, title: TitleRef, chapter: ChapterRef, fetch) -> list:
        """(v5.44.107) The pictures of a chapter's page (``fetch`` — its text): with the account's mark once a page of
        the site named it; a page that shows none of its pictures but names the mark — asked again with it."""
        url = chapter.url or f"{title.url}/vol{chapter.id}"
        mark = _ACCOUNT_MARKS.get(self.name, "")
        page = self._quietly(fetch, self._address(url, mark), mark)
        try:
            return self._reader(page)
        except SourceError:
            found = _mark_in(page)
            if not found or found == mark:
                raise
        _ACCOUNT_MARKS[self.name] = found
        return self._reader(self._quietly(fetch, self._address(url, found), found))

    @staticmethod
    def _quietly(fetch, address: str, mark: str) -> str:
        """``fetch(address)`` — words of a failure without the account's mark (they may reach the journal)."""
        try:
            return fetch(address)
        except SourceError as exc:
            if mark:
                exc.args = tuple(str(a).replace(mark, "…") for a in exc.args)
            raise

    def _reader(self, page: str) -> list:
        """(v5.44.99) The pictures of a chapter's page: ``rm_h.readerInit(…, [[server, '', path, w, h, ''], …])``."""
        start = max(page.find("rm_h.readerInit("), page.find("readerDoInit("))
        if start < 0:
            if re.search(r"(?i)войдите|авториз|только для зарегистрированных", _text(page[:400_000])):
                raise Refused(f"главу {self.label} показывает только тем, кто вошёл на сайт")
            raise SourceError(f"на странице главы {self.label} нет картинок")
        out = []
        for server, middle, path, width, height in self._ITEM.findall(page[start:start + 600_000]):
            if not server and path.startswith("/static/"):
                raise Paid(self.GONE)
            link = server + middle + path
            out.append(Picture(url=link if link.startswith("http") else urljoin(self.site() + "/", link),
                               width=_int(width), height=_int(height)))
        if not out:
            raise SourceError(f"на странице главы {self.label} нет картинок")
        return out

    FEED_ORDER = {"new": "DATE_UPDATE", "popular": "RATING"}
    FEED_COUNT = 50

    def feed(self, net: Net, how: str, page: int) -> tuple:
        """Its catalog's API ``/api/catalog/search?offset=&sortType=`` (as Yomi asks it)."""
        data = net.json(f"{self.site()}/api/catalog/search?offset={self.FEED_COUNT * (max(1, page) - 1)}&sortType="
                        f"{self.FEED_ORDER.get(how, 'DATE_UPDATE')}", what=self.label,
                        headers={"Accept": "application/json, text/plain, */*", "Referer": self.site() + "/"})
        out = []
        for x in (data.get("list") if isinstance(data, dict) else None) or []:
            element = x.get("elementId") if isinstance(x, dict) and isinstance(x.get("elementId"), dict) else {}
            link = str(element.get("linkName") or "").strip("/")
            if not link or not re.fullmatch(r"[\w.-]{1,200}", link):
                continue
            kind = str((x.get("category") or {}).get("name") or "") if isinstance(x.get("category"), dict) else ""
            rating = x.get("rating") if isinstance(x.get("rating"), dict) else {}     # (v5.44.99) {rate, voteCount}
            out.append(Hit(site=self.name, key=link, names=[str(x.get("name") or link).strip()],
                           type=kind if kind in KNOWN_TYPES else "", url=f"{self.site()}/{link}",
                           cover=str(x.get("picUrl") or ""),
                           rating=round(_float(x.get("rate") or rating.get("rate")), 1),
                           votes=_int(rating.get("voteCount")), chapters=_int(x.get("chaptersCount"))))
        total = _int(data.get("total")) if isinstance(data, dict) else 0
        offset = _int(data.get("offset")) if isinstance(data, dict) else 0
        limit = _int(data.get("limit")) if isinstance(data, dict) else 0
        return out, (offset + (limit or self.FEED_COUNT) < total) if total else len(out) >= self.FEED_COUNT

    def search(self, net: Net, query: str) -> list:
        data = net.json(f"{self.site()}/search/suggestion?query={quote(query)}", what=self.label,
                        headers={"Accept": "application/json, text/javascript, */*; q=0.01",
                                 "X-Requested-With": "XMLHttpRequest", "Referer": self.site() + "/"})
        out = []
        for x in (data.get("suggestions") if isinstance(data, dict) else None) or []:
            if not isinstance(x, dict):
                continue
            link = str(x.get("link") or "")
            element = x.get("elementId") if isinstance(x.get("elementId"), dict) else {}
            if not link.startswith("/") or str(element.get("type") or "MANGA") != "MANGA":
                continue                               # another site of the network, a person, a character
            key = self.key_of(self.site() + link)
            if not key:
                continue
            names = [str(n).strip() for n in [x.get("value"), *(x.get("names") or [])] if n and str(n).strip()]
            kind = str(element.get("typeName") or "")
            out.append(Hit(site=self.name, key=key, names=list(dict.fromkeys(names)),
                           type=kind if kind in KNOWN_TYPES else "", year=_int(x.get("year")),
                           url=f"{self.site()}/{key}", cover=str(x.get("thumbnail") or ""),
                           rating=round(_float(x.get("rate")), 1)))
        return out

    def referer(self, title: TitleRef) -> str:
        return self.site() + "/"


class AllHentai(ReadManga):
    """(v5.44.99) AllHentai (``20.allhen.online``; the owner, 6.11.2026: «https://20.allhen.online/ добавить») — the
    hentai site of the Grouple network, the engine of ReadManga (looked at from the owner's computer 6.11.2026,
    ``_to_delete/cl96_research31–33.txt``): the same title's page (``cr-hero-…``; genres and tags — ``/list/genre/``,
    ``/list/tag/`` of the class ``creation-element-tags__item``), chapters (``tr.item-row``), reader
    (``rm_h.readerInit``), search (``/search/suggestion``), catalog API (``/api/catalog/search?offset=&sortType=``: 50 a
    page; ``DATE_UPDATE``, ``POPULARITY``, ``DATE_CREATE``, ``RATING``, ``NAME`` — an order it does not know is its
    default; no filters; a title's ``elements`` — its genres, tags, parodies and year, ``category`` — «Манга»,
    «Манхва», «Комикс», «3D», «Без текста», ``rating`` {rate, voteCount}, ``picUrl`` on ``staticahen2.aaa200.one``).
    Without a sign-in a chapter's page answers 404 with every picture ``/static/deleted1.png`` (or with no reader): its
    chapters are read with the owner's sign-in — the site's cookies («Настройки → Сайты манги»); (v5.44.107) and with
    the mark of his account in the chapter's address, as the site's own script adds it to the links
    (:meth:`ReadManga._marked`; without it a signed-in reader got the «deleted» pictures as well — the owner's «не
    скачалась … AllHentai не показал страницы главы», 7.11.2026).  Everything there is hentai; a title depicting
    minors (its names, genres, tags) is never shown, opened or taken."""
    name = "allhentai"
    label = "AllHentai"
    hosts = ("allhen.online", "allhentai.ru", "allhentai.online")
    adult_only = True
    SIGNED_ONLY = True
    FEED_ORDER = {"new": "DATE_UPDATE", "popular": "POPULARITY"}
    GONE = "глава убрана с AllHentai: там её больше нет"
    SIGN_IN = ("AllHentai даёт читать и скачивать главы только со входом: войдите на сайте в своём браузере и добавьте "
               "его куки в «Настройки → Сайты манги»")
    NOT_SHOWN = ("AllHentai не показал страницы главы: либо вход устарел (обновите куки в «Настройки → Сайты манги»), "
                 "либо главу убрали с сайта")

    def site(self) -> str:
        return address(self.name) or ALLHENTAI_SITE

    def signed_in(self) -> bool:
        """The owner's cookies of the site (its address now, or any of its domains)."""
        host = (urlparse(self.site()).hostname or "").lower()
        return bool(signed_cookies(host)) or any(signed_cookies(h) for h in self.hosts)

    def sign_in_words(self) -> str:
        return self.SIGN_IN

    def title(self, net: Net, key: str) -> TitleRef:
        ref = super().title(net, key)
        ref.is_erotic = True
        if depicts_minors([ref.title, *ref.alt_titles, *ref.genres, *ref.tags]):
            raise Excluded(EXCLUDED)
        return ref

    def pictures(self, net: Net, title: TitleRef, chapter: ChapterRef) -> list:
        if not self.signed_in():
            raise Closed(self.sign_in_words())

        def fetch(address: str) -> str:
            body, ctype = net.fetch(address, what=self.label, keep_missing=True,
                                    headers={"Referer": title.url, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
            return _decoded(body, ctype)
        try:
            return self._marked(title, chapter, fetch)       # (v5.44.107) with the mark of the owner's account
        except (Refused, Paid) as exc:
            raise Closed(self.NOT_SHOWN) from exc
        except Closed:
            raise
        except SourceError as exc:
            if "нет картинок" in str(exc):
                raise Closed(self.NOT_SHOWN) from exc
            raise

    def feed(self, net: Net, how: str, page: int) -> tuple:
        data = net.json(f"{self.site()}/api/catalog/search?offset={self.FEED_COUNT * (max(1, page) - 1)}&sortType="
                        f"{self.FEED_ORDER.get(how, 'DATE_UPDATE')}", what=self.label,
                        headers={"Accept": "application/json, text/plain, */*", "Referer": self.site() + "/"})
        return self.catalog_hits(data)

    def catalog_hits(self, data) -> tuple:
        """([:class:`Hit`], whether there is a page more) of an answer of its catalog API — a title the program's rule
        leaves out (its names, its ``elements``) not among them; the kind — its ``category``."""
        out = []
        for x in (data.get("list") if isinstance(data, dict) else None) or []:
            element = x.get("elementId") if isinstance(x, dict) and isinstance(x.get("elementId"), dict) else {}
            link = str(element.get("linkName") or "").strip("/")
            if not link or not re.fullmatch(r"[\w.-]{1,200}", link):
                continue
            names = [str(x.get("name") or link).strip()]
            marks = [str(e.get("name") or "") for e in x.get("elements") or [] if isinstance(e, dict)]
            if depicts_minors(names + marks):
                continue
            category = x.get("category") if isinstance(x.get("category"), dict) else {}
            rating = x.get("rating") if isinstance(x.get("rating"), dict) else {}
            out.append(Hit(site=self.name, key=link, names=names, type=str(category.get("name") or ""),
                           url=f"{self.site()}/{link}", cover=str(x.get("picUrl") or ""),
                           rating=round(_float(rating.get("rate")), 1), votes=_int(rating.get("voteCount")),
                           chapters=_int(x.get("chaptersCount")), adult=True))
        total = _int(data.get("total")) if isinstance(data, dict) else 0
        offset = _int(data.get("offset")) if isinstance(data, dict) else 0
        limit = _int(data.get("limit")) if isinstance(data, dict) else 0
        return out, (offset + (limit or self.FEED_COUNT) < total) if total else len(out) >= self.FEED_COUNT

    def search(self, net: Net, query: str) -> list:
        hits = [h for h in super().search(net, query) if not depicts_minors(h.names)]
        for h in hits:
            h.adult = True
        return hits


class Desu(Source):
    """(v5.44.77) desu.uno (Desu.Me; desu.me itself no longer opens) — its new API, looked at from the owner's computer
    (5.11.2026; the old ``/manga/api/…`` answers 410 «api_deprecated»): ``/api/manga/<id>`` — the title (``russian``,
    ``name`` — the original one, ``synonyms``, ``kind`` — manga / manhwa / manhua…, ``status``, ``content_rating``,
    ``aired_on`` — unix time, ``description``, ``genres`` [{name, slug}], ``cover`` {preview, snippet, x120});
    ``/api/manga/<id>/chapters`` — every chapter (``id``, ``volume``, ``number``, ``title``, ``publish_date``,
    ``is_readable``, ``view_url``); ``/api/manga/<id>/chapters/<chapter id>`` — {"chapter": {…, ``pages`` [{page, url,
    width, height}]}} (as the site's reader asks: ``shared-api.js``).  Its search — the page ``/manga/search/?q=``
    (cards ``.AniMangaSearchCard``: the link ``manga/<slug>.<id>/``, the Russian and the original name, the kind, the
    year, the score).  One translation, all free; a chapter it cannot show (``is_readable`` false) is not listed."""
    name = "desu"
    label = "Desu"
    hosts = ("desu.uno", "desu.me", "desu.city", "desu.win")
    KINDS = {"manga": "Манга", "manhwa": "Манхва", "manhua": "Маньхуа", "one_shot": "Манга", "doujin": "Манга",
             "comics": "Комикс", "comic": "Комикс"}
    STATUSES = {"ongoing": "Продолжается", "released": "Завершён", "anons": "Анонс", "paused": "Приостановлен",
                "discontinued": "Выпуск прекращён"}

    def site(self) -> str:
        return address(self.name) or DESU_SITE

    def headers(self, referer: str = "") -> dict:
        return {"Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest",
                "Referer": referer or self.site() + "/"}

    def key_of(self, url: str) -> str:
        if not _host_of(url, self.hosts, self.site()):
            return ""
        m = re.match(r"/manga/[^/?#]*?\.?(\d+)(?:/|$)", urlparse(url).path)
        return m.group(1) if m else ""

    def title(self, net: Net, key: str) -> TitleRef:
        data = net.json(f"{self.site()}/api/manga/{quote(str(key), safe='')}", what=self.label, headers=self.headers())
        m = data.get("manga") if isinstance(data, dict) else None
        if not isinstance(m, dict) or not (m.get("russian") or m.get("name")):
            raise Missing(f"{self.label} не знает такого тайтла")
        name = str(m.get("russian") or m.get("name")).strip()
        alt = []
        for x in [m.get("name"), *(m.get("synonyms") or [])]:
            x = str(x or "").strip()
            if x and x != name and x not in alt:
                alt.append(x)
        genres = _names(m.get("genres"))
        slugs = {str(g.get("slug") or "").casefold() for g in m.get("genres") or [] if isinstance(g, dict)}
        cover = m.get("cover") if isinstance(m.get("cover"), dict) else {}
        key = str(m.get("id") or key)
        url = str(m.get("view_url") or "").strip() or f"{self.site()}/manga/{key}/"
        return TitleRef(site=self.name, key=key, url=url if url.endswith("/") else url + "/", title=name,
                        alt_titles=alt, type=self.KINDS.get(str(m.get("kind") or "").casefold(), ""),
                        status=self.STATUSES.get(str(m.get("status") or "").casefold(), ""),
                        year=_year(_date(m.get("aired_on"))),
                        age="" if str(m.get("content_rating") or "").casefold() in ("unrated", "none", "unknown")
                        else str(m.get("content_rating") or ""),
                        is_erotic=bool(slugs & {"hentai", "erotica"}) or any(
                            g.casefold() in ("хентай", "эротика") for g in genres),
                        genres=genres, description=plain(m.get("description")),
                        cover_url=str(cover.get("preview") or cover.get("snippet") or cover.get("x120") or ""),
                        branches=[Branch(id="", name=self.label)])

    def chapters(self, net: Net, title: TitleRef, branch: str = "") -> list:
        data = net.json(f"{self.site()}/api/manga/{quote(title.key, safe='')}/chapters", what=self.label,
                        headers=self.headers(title.url))
        found: dict = {}
        for x in (data.get("chapters") if isinstance(data, dict) else None) or []:
            if not isinstance(x, dict) or x.get("id") is None or x.get("is_readable") is False:
                continue
            ch = ChapterRef(id=str(x["id"]), tome=_int(x.get("volume")), number=_num(x.get("number")),
                            name=str(x.get("title") or "").strip(), date=_date(x.get("publish_date")),
                            url=str(x.get("view_url") or ""))
            old = found.get((ch.tome, ch.number))           # one chapter given twice: the later one
            if old is None or _int(ch.id) > _int(old.id):
                found[(ch.tome, ch.number)] = ch
        out = sorted(found.values(), key=lambda c: (c.tome, _number_key(c.number)))
        for n, ch in enumerate(out, 1):
            ch.index = n
        return out

    def pictures(self, net: Net, title: TitleRef, chapter: ChapterRef) -> list:
        data = net.json(f"{self.site()}/api/manga/{quote(title.key, safe='')}/chapters/{quote(chapter.id, safe='')}",
                        what=self.label, headers=self.headers(chapter.url or title.url))
        found = data if isinstance(data, dict) else {}
        for wrap in ("chapter", "data"):
            if isinstance(found.get(wrap), dict) and "pages" in found[wrap]:
                found = found[wrap]
        if found.get("is_readable") is False:
            raise Paid(f"главу {self.label} не показывает — программа её не скачивает")
        pages = found.get("pages")
        if isinstance(pages, dict):                    # {"list": […]} — as the old API had it
            pages = pages.get("list")
        out = []
        for p in sorted((p for p in pages or [] if isinstance(p, dict) and p.get("url")), key=lambda p: _int(p.get(
                "page"))):
            out.append(Picture(url=urljoin(self.site() + "/", str(p["url"])), width=_int(p.get("width")),
                               height=_int(p.get("height"))))
        if not out:
            raise SourceError(f"в главе на {self.label} нет страниц")
        return out

    _CARD = re.compile(r'<li[^>]*class="[^"]*\bAniMangaSearchCard\b[^"]*"[^>]*>(.*?)</li>', re.S)

    FEED_ORDER = {"new": "updated", "popular": "popular"}

    def feed(self, net: Net, how: str, page: int) -> tuple:
        """(v5.44.83) Its catalog's API ``/api/manga/catalog/?limit=20&page=&order_by=`` (as Yomi asks it)."""
        data = net.json(f"{self.site()}/api/manga/catalog/?limit=20&page={max(1, page)}&order_by="
                        f"{self.FEED_ORDER.get(how, 'updated')}", what=self.label,
                        headers={"Accept": "application/json", "Referer": self.site() + "/"})
        out = []
        for x in (data.get("mangas") if isinstance(data, dict) else None) or []:
            if not isinstance(x, dict) or not str(x.get("id") or "").isdigit():
                continue
            names = [str(n).strip() for n in (x.get("russian"), x.get("name")) if n and str(n).strip()]
            cover = x.get("cover") if isinstance(x.get("cover"), dict) else (
                x.get("image") if isinstance(x.get("image"), dict) else {})
            link = str(cover.get("preview") or cover.get("original") or "")
            out.append(Hit(site=self.name, key=str(x["id"]), names=list(dict.fromkeys(names)),
                           type=self.KINDS.get(str(x.get("kind") or ""), ""), url=f"{self.site()}/manga/{x['id']}",
                           cover=urljoin(self.site() + "/", link) if link else "",
                           rating=_float(x.get("score")), adult=str(x.get("content_rating") or "") in ("hentai",)))
        pages = data.get("pagination") if isinstance(data, dict) and isinstance(data.get("pagination"), dict) else {}
        if pages:
            return out, _int(pages.get("current_page") or page) < _int(pages.get("last_page"))
        return out, len(out) >= 20

    def search(self, net: Net, query: str) -> list:
        page = net.text(f"{self.site()}/manga/search/?q={quote(query)}", what=self.label,
                        headers={"Referer": self.site() + "/"})
        out = []
        for m in self._CARD.finditer(page):
            card = m.group(1)
            href = re.search(r'<a[^>]*href="([^"]*manga/[^"/]*?\.?(\d+)/?)"', card)
            if not href:
                continue
            main = re.search(r'class="AniMangaSearchCard__title"[^>]*>(.*?)</span>', card, re.S)
            sub = re.search(r'class="AniMangaSearchCard__subtitle"[^>]*>(.*?)</span>', card, re.S)
            facts = _block(card, "AniMangaSearchCard__facts")
            spans = [_text(x) for x in re.findall(r"<span[^>]*>(.*?)</span>", facts, re.S)]
            score = re.search(r'class="is-score"[^>]*>(.*?)</span>', facts, re.S)
            cover = re.search(r'<img[^>]*src="([^"]+)"', card)
            out.append(Hit(site=self.name, key=href.group(2),
                           names=[_text(x.group(1)) for x in (main, sub) if x and _text(x.group(1))],
                           type=next((x for x in spans if x in KNOWN_TYPES), ""),
                           year=next((int(x) for x in spans if re.fullmatch(r"(19|20)\d\d", x)), 0),
                           url=urljoin(self.site() + "/", _html.unescape(href.group(1))),
                           cover=urljoin(self.site() + "/", _html.unescape(cover.group(1))) if cover else "",
                           rating=_float(_text(score.group(1))) if score else 0.0))
        return out

    def referer(self, title: TitleRef) -> str:
        return self.site() + "/"


class MangaDex(Source):
    """(v5.44.79) MangaDex — a title in every language it is translated into; the owner (5.11.2026, of his friend's
    reader): «искало именно того перевода который тебе надо или оригинал».  Its open API (``api.mangadex.org``; from
    the owner's computer it first sends every request to its own check for browsers — ``mangadex.org/edge/challenge``:
    the program window's browser goes through it as through com-x.life's, ``Net``): ``/manga/<id>?includes[]=…`` —
    the title (``title``/``altTitles`` in many languages, ``description``, ``originalLanguage``, ``status``,
    ``year``, ``contentRating``, ``tags``, ``availableTranslatedLanguages``, the cover's file —
    ``uploads.mangadex.org/covers/<id>/<file>``); its «translators» here are languages (:data:`WANTED`: Russian,
    English, the original one), each a branch with its chapters counted by ``/manga/<id>/aggregate?
    translatedLanguage[]=``; the chapters of one — ``/manga/<id>/feed?translatedLanguage[]=…`` page by page (500): a
    chapter of several groups — of the group with the most chapters, one hosted elsewhere (``externalUrl``) or
    without pages — not taken; a chapter's pictures — ``/at-home/server/<chapter id>`` → ``<baseUrl>/data/<hash>/
    <file>``.  The language taken unless the card picks another — «Настройки → Сайты манги» (:data:`LANGUAGE`)."""
    name = "mangadex"
    label = "MangaDex"
    hosts = ("mangadex.org",)
    languages = True
    WANTED = ("ru", "en")
    RATINGS = "&".join(f"contentRating[]={r}" for r in ("safe", "suggestive", "erotica", "pornographic"))
    KINDS = {"ja": "Манга", "ko": "Манхва", "zh": "Маньхуа", "zh-hk": "Маньхуа", "en": "Комикс"}
    NAMES = {"ru": "Русский", "en": "English", "ja": "японский", "ko": "корейский", "zh": "китайский",
             "zh-hk": "китайский", "id": "индонезийский", "fr": "французский", "es": "испанский"}
    STATUSES = {"ongoing": "Продолжается", "completed": "Завершён", "hiatus": "Приостановлен",
                "cancelled": "Выпуск прекращён"}
    AGES = {"suggestive": "16+", "erotica": "18+", "pornographic": "18+"}
    UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    PAGE = 500

    def site(self) -> str:
        return address(self.name) or MANGADEX_SITE

    def api(self) -> str:
        return address(self.name + "_api") or _api_of(address(self.name)) or MANGADEX_API

    def headers(self) -> dict:
        return {"Accept": "application/json", "Referer": self.site() + "/"}

    def key_of(self, url: str) -> str:
        if not _host_of(url, self.hosts, self.site()):
            return ""
        m = re.match(r"/(?:title|manga)/(" + self.UUID + r")(?:/|$)", urlparse(url).path, re.I)
        return m.group(1).lower() if m else ""

    def title_of_chapter(self, net: Net, chapter: str) -> str:
        """(v5.44.81) The link of the title of one of its chapters (``chapter`` — the chapter's id: SauceNAO's answers,
        ComicInfo.xml of «Из архива» name chapters): ``/chapter/<id>`` → its relation «manga»; "" — none."""
        if not re.fullmatch(self.UUID, str(chapter or ""), re.I):
            return ""
        data = net.json(f"{self.api()}/chapter/{str(chapter).lower()}", what=self.label, headers=self.headers())
        body = data.get("data") if isinstance(data, dict) else None
        relations = body.get("relationships") if isinstance(body, dict) else None
        manga = next((str(r.get("id") or "") for r in relations or [] if isinstance(r, dict) and
                      r.get("type") == "manga"), "")
        return f"{self.site()}/title/{manga.lower()}" if re.fullmatch(self.UUID, manga, re.I) else ""

    @staticmethod
    def _in(names, *order) -> str:
        """The name of ``names`` ({language: name}) in the first of ``order`` it has, else any."""
        names = names if isinstance(names, dict) else {}
        for lang in order:
            if str(names.get(lang) or "").strip():
                return str(names[lang]).strip()
        return next((str(v).strip() for v in names.values() if str(v or "").strip()), "")

    def _cover(self, key: str, relations: list, size: str = "") -> str:
        art = next((r for r in relations if isinstance(r, dict) and r.get("type") == "cover_art"), None)
        name = str(((art or {}).get("attributes") or {}).get("fileName") or "")
        return f"{_cover_host(self)}/covers/{key}/{name}{size}" if name else ""

    def _count(self, net: Net, key: str, lang: str) -> int:
        data = net.json(f"{self.api()}/manga/{key}/aggregate?translatedLanguage[]={lang}", what=self.label,
                        headers=self.headers())
        volumes = data.get("volumes") if isinstance(data, dict) else None
        found = set()
        for volume in (volumes.values() if isinstance(volumes, dict) else volumes or []):
            chapters = volume.get("chapters") if isinstance(volume, dict) else None
            for number in (chapters.keys() if isinstance(chapters, dict) else []):
                if number and number != "none":
                    found.add(number)
        return len(found)

    def title(self, net: Net, key: str) -> TitleRef:
        key = str(key).lower()
        data = net.json(f"{self.api()}/manga/{key}?includes[]=cover_art&includes[]=author&includes[]=artist",
                        what=self.label, headers=self.headers())
        item = data.get("data") if isinstance(data, dict) else None
        attrs = item.get("attributes") if isinstance(item, dict) else None
        if not isinstance(attrs, dict):
            raise Missing(f"{self.label} не знает такого тайтла")
        relations = item.get("relationships") if isinstance(item.get("relationships"), list) else []
        alts = [a for a in attrs.get("altTitles") or [] if isinstance(a, dict)]
        russian = next((str(a["ru"]).strip() for a in alts if str(a.get("ru") or "").strip()), "")
        main = russian or self._in(attrs.get("title"), "en", "ja-ro", "ko-ro", "zh-ro")
        names = [self._in(attrs.get("title"), "en", "ja-ro")] + [str(v).strip() for a in alts for v in a.values()]
        alt = []
        for name in names:
            if name and name != main and name not in alt:
                alt.append(name)
        original = str(attrs.get("originalLanguage") or "")
        available = [str(x) for x in attrs.get("availableTranslatedLanguages") or [] if x]
        branches = []
        for lang in dict.fromkeys([*self.WANTED, original]):
            if lang and lang in available:
                label = self.NAMES.get(lang, lang)
                name = f"{label} — оригинал" if lang == original else label
                branches.append(Branch(id=lang, name=name, count=self._count(net, key, lang)))
        wanted = LANGUAGE[0]
        rating = str(attrs.get("contentRating") or "")
        tags = [t for t in attrs.get("tags") or [] if isinstance(t, dict)]
        tag_name = lambda t: self._in((t.get("attributes") or {}).get("name"), "ru", "en")       # noqa: E731
        return TitleRef(
            site=self.name, key=key, url=f"{self.site()}/title/{key}", title=main, alt_titles=alt,
            type=self.KINDS.get(original, ""), status=self.STATUSES.get(str(attrs.get("status") or ""), ""),
            year=_int(attrs.get("year")), age=self.AGES.get(rating, ""), is_erotic=rating == "pornographic",
            genres=[tag_name(t) for t in tags if (t.get("attributes") or {}).get("group") in ("genre", "theme")],
            tags=[tag_name(t) for t in tags if (t.get("attributes") or {}).get("group") not in ("genre", "theme")],
            description=plain(self._in(attrs.get("description"), "ru", "en")),
            cover_url=self._cover(key, relations, ".512.jpg"),
            authors=list(dict.fromkeys(str((r.get("attributes") or {}).get("name") or "") for r in relations
                                       if isinstance(r, dict) and r.get("type") in ("author", "artist")
                                       and (r.get("attributes") or {}).get("name"))),
            branches=branches, original=original, prefer=original if wanted == "original" else wanted)

    def chapters(self, net: Net, title: TitleRef, branch: str = "") -> list:
        lang = branch or (title.best_branch().id if title.best_branch() else "")
        if not lang:
            return []
        rows, offset = [], 0
        while offset < 10000:
            data = net.json(f"{self.api()}/manga/{title.key}/feed?translatedLanguage[]={lang}&limit={self.PAGE}"
                            f"&offset={offset}&order[volume]=asc&order[chapter]=asc&includes[]=scanlation_group"
                            f"&{self.RATINGS}", what=self.label, headers=self.headers())
            items = data.get("data") if isinstance(data, dict) else None
            rows += [x for x in items or [] if isinstance(x, dict)]
            offset += self.PAGE
            if not items or offset >= _int(data.get("total")):
                break
        groups: dict = {}
        usable = []
        for x in rows:
            attrs = x.get("attributes") if isinstance(x.get("attributes"), dict) else {}
            if attrs.get("externalUrl") or not _int(attrs.get("pages")) or attrs.get("chapter") in (None, ""):
                continue                               # hosted elsewhere, no pages, no number
            group = next((r for r in x.get("relationships") or [] if isinstance(r, dict) and
                          r.get("type") == "scanlation_group"), {})
            name = str((group.get("attributes") or {}).get("name") or "")
            groups[name] = groups.get(name, 0) + 1
            usable.append((x, attrs, name))
        found: dict = {}
        for x, attrs, name in usable:
            ch = ChapterRef(id=str(x.get("id")), tome=_int(attrs.get("volume")), number=_num(attrs.get("chapter")),
                            name=str(attrs.get("title") or "").strip(), date=str(attrs.get("publishAt") or ""),
                            url=f"{self.site()}/chapter/{x.get('id')}")
            old = found.get(ch.number)
            if old is None or groups.get(name, 0) > groups.get(old[1], 0):
                found[ch.number] = (ch, name)       # one chapter of several groups: of the one with the most
        out = sorted((ch for ch, _name in found.values()), key=lambda c: (c.tome, _number_key(c.number)))
        for n, ch in enumerate(out, 1):
            ch.index = n
        return out

    def pictures(self, net: Net, title: TitleRef, chapter: ChapterRef) -> list:
        data = net.json(f"{self.api()}/at-home/server/{chapter.id}", what=self.label, headers=self.headers())
        base = str(data.get("baseUrl") or "") if isinstance(data, dict) else ""
        holder = data.get("chapter") if isinstance(data, dict) and isinstance(data.get("chapter"), dict) else {}
        files = [str(f) for f in holder.get("data") or [] if f]
        if not base or not holder.get("hash") or not files:
            raise SourceError(f"в главе на {self.label} нет страниц")
        return [Picture(url=f"{base.rstrip('/')}/data/{holder['hash']}/{f}") for f in files]

    def search(self, net: Net, query: str) -> list:
        data = net.json(f"{self.api()}/manga?title={quote(query)}&limit=10&includes[]=cover_art&order[relevance]=desc"
                        f"&{self.RATINGS}", what=self.label, headers=self.headers())
        return self._hits(data)

    FEED_ORDER = {"new": "latestUploadedChapter", "popular": "followedCount"}
    FEED_COUNT = 30

    def feed(self, net: Net, how: str, page: int) -> tuple:
        """Its titles with chapters in the language chosen («Язык глав MangaDex»; «оригинал» — Russian)."""
        language = LANGUAGE[0] if LANGUAGE and LANGUAGE[0] in ("ru", "en") else "ru"
        offset = self.FEED_COUNT * (max(1, page) - 1)
        data = net.json(f"{self.api()}/manga?limit={self.FEED_COUNT}&offset={offset}&includes[]=cover_art"
                        f"&availableTranslatedLanguage[]={language}&hasAvailableChapters=true&{self.RATINGS}"
                        f"&order[{self.FEED_ORDER.get(how, 'latestUploadedChapter')}]=desc", what=self.label,
                        headers=self.headers())
        hits = self._hits(data)
        total = _int(data.get("total")) if isinstance(data, dict) else 0
        return hits, offset + len(hits) < total

    def _hits(self, data) -> list:
        out = []
        for item in (data.get("data") if isinstance(data, dict) else None) or []:
            attrs = item.get("attributes") if isinstance(item, dict) else None
            if not isinstance(attrs, dict) or not item.get("id"):
                continue
            names = [self._in(attrs.get("title"), "en", "ja-ro")] + [
                str(v).strip() for a in attrs.get("altTitles") or [] if isinstance(a, dict) for v in a.values()]
            key = str(item["id"]).lower()
            out.append(Hit(site=self.name, key=key, names=list(dict.fromkeys(n for n in names if n)),
                           type=self.KINDS.get(str(attrs.get("originalLanguage") or ""), ""), year=_int(attrs.get("year")),
                           url=f"{self.site()}/title/{key}",
                           cover=self._cover(key, item.get("relationships") or [], ".256.jpg"),
                           adult=attrs.get("contentRating") == "pornographic"))
        return out

    def referer(self, title: TitleRef) -> str:
        return self.site() + "/"


def _cover_host(source: "MangaDex") -> str:
    """MangaDex's covers: «uploads.» + its address (the checks' own site — itself)."""
    site = urlparse(source.site())
    host = site.netloc.lower()
    if host.endswith("mangadex.org") and not host.startswith("uploads."):
        return f"{site.scheme}://uploads.{host.removeprefix('www.')}"
    return f"{site.scheme}://{host}"


# ── (v5.44.85) Хентай-тян ──────────────────────────────────────────────────────────────────────────────────────────
_PART_AT = re.compile(r"(?i)(?<![^\W\d_])(?:глава|главы|часть|части|chapter|chap|ch|part|эпизод|episode|ep)\.?\s*[№#]?"
                      r"\s*(\d+(?:[.,]\d+)?)")
_TRAILING_NUMBER = re.compile(r"[\s\-–—:,.#№]+(\d+(?:[.,]\d+)?)\s*$")
CLOUD = 60                   # a column of more tags than this is the site's every tag, not a title's


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


def _chan_names(full) -> tuple:
    """Хентай-тян's name of an entry, «Главное (Другое)» → ("Главное", ["Другое", …]): the one in brackets at its end
    is another name (Russian, Japanese, English — «A / B», «A | B» are two); without them — itself alone."""
    text = " ".join(_html.unescape(str(full or "")).split())
    if text.endswith(")"):
        depth = 0
        for i in range(len(text) - 1, -1, -1):
            if text[i] == ")":
                depth += 1
            elif text[i] == "(":
                depth -= 1
                if depth == 0:
                    inner, outer = text[i + 1:-1].strip(), text[:i].strip()
                    if inner and outer:
                        return outer, [x.strip() for x in re.split(r"\s+[/|]\s+", inner) if x.strip()]
                    break
    return text, []


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


class Hentaichan(Source):
    """(v5.44.85) Хентай-тян (hentaichan.live; its pages move from mirror to mirror — x5, x9, x17.h-chan.me…) — the
    owner (5.11.2026): «https://hentaichan.live/ еще добавь в поддерживаемые».  Pages of DataLife Engine, as they are
    made (its copies of 2025–2026 in the Wayback Machine, read from the owner's computer: the site itself did not answer
    then) and as the readers Tachiyomi (HenChan) and Kotatsu read them: an entry ``/manga/<id>-<slug>.html`` (the site
    takes it by the number: «/related/10704-undefined.html» answers) — its name in ``a.title_top_a`` («Главное
    (Другое)»: English or Japanese and Russian), the rows of ``#info_wrap`` (``.item`` — ``.item2``: «Аниме/манга» —
    the work it parodies, «Автор», «Переводчик»), ``#description``, ``img#cover``, its tags — the column ``.sidetags``
    (``li.sidetag``: «+», «-», the tag; a list page's column is the site's every tag); the parts of one work are entries
    of their own (« - глава 5», « - часть 2. Имя»), ``/related/<id>-<slug>.html`` lists them all («Все главы»,
    ``div.related`` → ``.related_info h2 a``; ten to a page, ``#pagination_related`` «Вперед» — ``?offset=``) — of an
    entry alone it lists others («Хентай похожий на …»): one title — the first part, its parts — its chapters; a part's
    pictures — ``/online/<id>-<slug>.html``: ``"fullimg": ['…', …]`` of its script (``mimg4.imgschan.xyz``).  Its lists —
    ``div.content_row`` (``h2 a``, its picture, its tags — the links of ``.genre``; entries taken down are named «.»):
    «Обновления» ``/manga/newest?offset=`` (20 to a page), «Популярное» ``/mostviews&sort=manga?offset=``; its search —
    DataLife Engine's ``/?do=search&subaction=search&story=``.  Everything there is hentai (``adult_only``); a title
    depicting minors is never opened or taken (:class:`Excluded`)."""
    name = "hentaichan"
    label = "Хентай-тян"
    hosts = ("hentaichan.live", "h-chan.me", "henchan.pro", "hentaichan.ru")
    adult_only = True
    slow_pages = True              # (v5.44.95) 12–15 s a page at times (6.11.2026): its feed waits longer (manhwa_feed)
    PER_PAGE = 20                  # the titles of a page of its lists
    PARTS_PAGES = 40               # the pages of a work's parts asked at most (ten parts to a page)
    FEED = {"new": "/manga/newest?offset={offset}", "popular": "/mostviews&sort=manga?offset={offset}"}
    KEY = re.compile(r"/(?:manga|online|related|download)/+(\d+(?:-[^/]*?)?)\.html?$")
    LINK = re.compile(r'<a\b([^>]*\bhref=["\']([^"\']*/manga/+(\d+(?:-[^"\'/]*?)?)\.html?)(?:\?[^"\']*)?["\'][^>]*)>'
                      r'(.*?)</a>', re.S)
    ROW = re.compile(r'<div\b[^>]*class=["\'][^"\']*\bcontent_row\b[^"\']*["\']([^>]*)>')

    def site(self) -> str:
        return address(self.name) or HENTAICHAN_SITE

    def key_of(self, url: str) -> str:
        if not _host_of(url, self.hosts, self.site()):
            return ""
        m = self.KEY.match(urlparse(url).path)
        return m.group(1) if m else ""

    def referer(self, title: TitleRef) -> str:
        return self.site() + "/"

    def _page(self, net: Net, url: str, referer: str = "") -> str:
        body, ctype = net.fetch(url, what=self.label, headers={"Referer": referer or self.site() + "/",
                                                               "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        return _decoded(body, ctype)

    @staticmethod
    def _rows_of(page: str) -> dict:
        """The rows of ``#info_wrap``: {«Автор»: [its words], …}."""
        out: dict = {}
        for label, value in re.findall(r'class=["\']item["\'][^>]*>(.*?)</div>\s*<div[^>]*class=["\']item2["\'][^>]*>'
                                       r'(.*?)</div>', _by_id(page, "info_wrap"), re.S):
            words = [_text(x) for x in re.findall(r"<a\b[^>]*>(.*?)</a>", value, re.S)]
            out.setdefault(_text(label).rstrip(":").strip(), [x for x in words if x] or [_text(value)])
        return out

    @staticmethod
    def _tags_of(page: str) -> tuple:
        """(the entry's tags as the site writes them, the keys of their links): its column ``.sidetags`` — a column of
        more than :data:`CLOUD` is the site's every tag, not the entry's."""
        items = re.findall(r'<li[^>]*class=["\'][^"\']*\bsidetag\b[^"\']*["\'][^>]*>(.*?)</li>', _block(page, "sidetags"),
                           re.S)
        words, keys = [], []
        if len(items) > CLOUD:
            return words, keys
        for item in items:
            links = re.findall(r"<a\b([^>]*)>(.*?)</a>", item, re.S)
            if not links:
                continue
            attrs, inner = links[-1]                  # its «+», «-» first, the tag itself last
            word = _text(inner).replace("_", " ").strip()
            key = re.search(r'/tags/([^"\'/?#&<>]+)', attrs)
            if word and word not in ("+", "-", "−") and word not in words:
                words.append(word)
            if key:
                keys.append(unquote(_html.unescape(key.group(1))))
        return words, keys

    def _entry(self, page: str) -> dict:
        """What an entry's page says: its names, authors, translators, tags, description, cover; :class:`Missing` — no
        such entry (DataLife Engine's «публикаций не найдено»: none, or only for those signed in)."""
        top = re.search(r'<a\b[^>]*class=["\'][^"\']*\btitle_top_a\b[^"\']*["\'][^>]*>(.*?)</a>', page, re.S)
        heading = re.search(r"<title>(.*?)</title>", page, re.S)
        full = _text(top.group(1)) if top else ""
        if not full and heading:
            full = _text(heading.group(1)).split(" »")[0].strip()
        if not re.search(r"\w", full) or not (_by_id(page, "info_wrap") or re.search(r'<img\b[^>]*\bid=["\']cover', page)):
            if re.search(r"(?i)публикаций на сайте не найдено|нет доступа для просмотра", page):
                raise Missing(f"{self.label}: такой страницы нет (или её видят только те, кто вошёл на сайт)")
            raise SourceError(f"на странице {self.label} нет тайтла")
        main, others = _chan_names(full)
        rows = self._rows_of(page)
        words, keys = self._tags_of(page)
        series = [x for x in rows.get("Аниме/манга", rows.get("Серия", [])) if x and x != "Оригинальные работы"]
        if depicts_minors([*words, *keys]):
            raise Excluded(EXCLUDED)
        about = _by_id(page, "description")
        cut = re.search(r"<div\b", about)
        cover = re.search(r'<img\b[^>]*\bid=["\']cover["\'][^>]*>', page)
        src = re.search(r'\b(?:data-src|src)=["\']([^"\']+)["\']', cover.group(0)) if cover else None
        team = [x for x in rows.get("Переводчик", []) if x and x != "-"]
        return {"main": main, "others": others, "authors": [x for x in rows.get("Автор", []) if x and x != "-"],
                "team": ", ".join(team), "tags": [*series, *(w for w in words if w not in series)],
                "description": plain(about[:cut.start()] if cut else about),
                "cover": urljoin(self.site() + "/", _html.unescape(src.group(1))) if src and src.group(1).strip() else ""}

    def title(self, net: Net, key: str) -> TitleRef:
        entry = self._entry(self._page(net, f"{self.site()}/manga/{key}.html"))
        parts = self._parts(net, key, entry["main"])
        if parts and parts[0]["key"] != key:            # a work of parts: the title is its first part
            key = parts[0]["key"]
            entry = self._entry(self._page(net, f"{self.site()}/manga/{key}.html"))
        net.kept[(self.name, key)] = parts
        if parts:                                        # its name without «глава N»; the other one if every part has it
            base = _without_part(entry["main"]) or entry["main"]
            common = [x for x in entry["others"] if all(x in p["others"] for p in parts)]
            name, alt = _shown_name(base, common)
        else:
            name, alt = _shown_name(entry["main"], entry["others"])
        return TitleRef(site=self.name, key=key, url=f"{self.site()}/manga/{key}.html", title=name,
                        alt_titles=[x for x in dict.fromkeys(alt) if x and x != name], type="Манга", age="18+",
                        is_erotic=True, genres=["Хентай"], tags=entry["tags"], description=entry["description"],
                        cover_url=entry["cover"], authors=entry["authors"],
                        branches=[Branch(id="", name=entry["team"] or self.label)])

    def _parts(self, net: Net, key: str, main: str) -> list:
        """The parts of the work the entry ``key`` is one of — its «Все главы» (``/related/``): [{key, main, others,
        number, name}] in their order; [] — the entry is alone («похожий на» — other works; 404).  An entry there of
        another name is another work."""
        wanted = names_of(_without_part(main))
        if not wanted:
            return []
        found: dict = {}
        url = f"{self.site()}/related/{key}.html"
        for _n in range(self.PARTS_PAGES):
            try:
                page = self._page(net, url, referer=f"{self.site()}/manga/{key}.html")
            except Missing:
                break
            heading = re.search(r"<title>(.*?)</title>", page, re.S)
            if heading and re.search(r"(?i)похож", _text(heading.group(1))):
                return []                               # «Хентай похожий на …» — other works, not its parts
            for m in re.finditer(r'<(\w+)\b[^>]*class=["\']related_info["\'][^>]*>', page):
                link = self.LINK.search(_inside(page, m))
                if not link or link.group(3) in found:
                    continue
                said = re.search(r'\btitle=["\']([^"\']*)["\']', link.group(1))
                other, others = _chan_names(_text(link.group(4)) or (said.group(1) if said else ""))
                if names_of(_without_part(other)) & wanted:
                    found[link.group(3)] = {"key": link.group(3), "main": other, "others": others,
                                            "number": _part_number(other), "name": _part_name(other)}
            more = re.search(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>\s*Вперед', _by_id(page, "pagination_related"))
            if not more:
                break
            url = urljoin(url, _html.unescape(more.group(1)))
        if key not in found or len(found) < 2:
            return []
        parts = list(found.values())
        numbers = [p["number"] for p in parts]
        if all(numbers) and len(set(numbers)) == len(numbers):
            parts.sort(key=lambda p: _number_key(p["number"]))
        else:                                         # in the order the site added them (their numbers)
            parts.sort(key=lambda p: _int(p["key"].split("-", 1)[0]))
            for n, part in enumerate(parts, 1):
                part["number"] = str(n)
        return parts

    def chapters(self, net: Net, title: TitleRef, branch: str = "") -> list:
        parts = net.kept.get((self.name, title.key))
        if parts is None:
            try:
                main = _text(re.search(r"<title>(.*?)</title>", self._page(net, title.url), re.S).group(1))
            except (SourceError, AttributeError):
                main = ""
            parts = self._parts(net, title.key, main.split(" »")[0]) if main else []
        parts = parts or [{"key": title.key, "number": "1", "name": ""}]
        return [ChapterRef(id=p["key"], number=p["number"], name=p.get("name", ""),
                           url=f"{self.site()}/online/{p['key']}.html", index=n) for n, p in enumerate(parts, 1)]

    def pictures(self, net: Net, title: TitleRef, chapter: ChapterRef) -> list:
        url = chapter.url or f"{self.site()}/online/{chapter.id}.html"
        page = self._page(net, url, referer=f"{self.site()}/manga/{chapter.id}.html")
        found = re.search(r"""["']fullimg["']\s*:\s*\[(.*?)\]""", page, re.S)
        links = re.findall(r"""["']([^"'\s]+)["']""", found.group(1)) if found else []
        out = [Picture(url=urljoin(url, _html.unescape(x.replace("\\/", "/")))) for x in links
               if x.strip() and "/manganew_mp4/" not in x]       # a video among the pages is not a page
        if not out:
            if re.search(r"(?i)зарегистрир|авториз|войдите на сайт", _text(page[:400_000])):
                raise Refused(f"главу {self.label} показывает только тем, кто вошёл на сайт (cookies входа — "
                              "«Настройки → Сайты манги»)")
            raise SourceError(f"на странице чтения {self.label} нет картинок")
        return out

    def _rows(self, page: str) -> tuple:
        """The titles of a page of its lists (``div.content_row``): ([Hit], how many rows the page had)."""
        starts = list(self.ROW.finditer(page))
        out, seen = [], set()
        for n, m in enumerate(starts):
            row = page[m.start():starts[n + 1].start() if n + 1 < len(starts) else m.end() + 20000]
            kind = re.search(r'title=["\']Выбрать только ([^"\']*)["\']', row)
            if re.search(r'class=["\']item["\'][^>]*>\s*Тип\s*<', row) or kind and re.search(r"(?i)игр|видео", kind.group(1)):
                continue                               # no manga: a game, a video
            heading = re.search(r"<h2\b[^>]*>(.*?)</h2>", row, re.S)
            link = (self.LINK.search(heading.group(1)) if heading else None) or self.LINK.search(row)
            if not link or link.group(3) in seen:
                continue
            tags = _block(row, "genre")
            words = [_text(x) for x in re.findall(r"<a\b[^>]*>(.*?)</a>", tags, re.S)]
            if depicts_minors([*words, *re.findall(r'/tags/([^"\'/?#&<>]+)', tags)]):
                continue
            main, others = _chan_names(_text(link.group(4)))
            if not re.search(r"\w", main):
                continue                               # an entry taken down: its name is «.»
            name, alt = _shown_name(main, others)
            picture = (re.search(r'<img\b[^>]*\bdata-src=["\']([^"\']+)["\']', row)
                       or re.search(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', row))
            cover = urljoin(self.site() + "/", _html.unescape("".join(picture.group(1).split()))) if picture else ""
            seen.add(link.group(3))
            out.append(Hit(site=self.name, key=link.group(3), names=[name, *alt], type="Манга",
                           url=urljoin(self.site() + "/", _html.unescape(link.group(2))),
                           cover=cover if urlparse(cover).path.strip("/") else "", adult=True))
        return out, len(starts)

    def search_url(self, query: str) -> str:
        return (f"{self.site()}/?do=search&subaction=search&search_start=1&full_search=0&result_from=1"
                f"&result_num=40&story={quote(str(query or ''))}")

    def feed_url(self, how: str, page: int) -> str:
        return self.site() + self.FEED.get(how, self.FEED["new"]).format(offset=self.PER_PAGE * (max(1, page) - 1))

    def listing(self, net: Net, url: str) -> tuple:
        """([Hit], whether there is a page more) of a page of its lists (:meth:`feed_url`, :meth:`search_url`: one
        page) — (v5.44.93) the «Лента» of the manga asks it too (``core/catalog.py``)."""
        return self._listing(net, url)[:2]

    def _listing(self, net: Net, url: str) -> tuple:
        text = self._page(net, url)
        hits, rows = self._rows(text)
        if "do=search" in url:
            return hits, False, rows
        return hits, bool(re.search(r">\s*(?:Вперед|Вперёд|Далее)\s*<", text)) or rows >= self.PER_PAGE, rows

    def search(self, net: Net, query: str) -> list:
        return self.listing(net, self.search_url(query))[0]

    # (v5.44.93) its «Популярное» (and «Избранное») gave 19 bytes and no rows after 9–27 s, asked from the owner's
    # computer on 6.11.2026, while «Обновления» gave its 20 titles: once empty, «Обновления» instead for this run
    popular_empty = False

    def feed(self, net: Net, how: str, page: int) -> tuple:
        if how == "popular" and not Hentaichan.popular_empty:
            hits, more, rows = self._listing(net, self.feed_url("popular", page))
            if rows or page > 1:
                return hits, more
            Hentaichan.popular_empty = True
            _LOG.info("%s: its «Популярное» is empty — «Обновления» instead", self.label)
        return self.listing(net, self.feed_url("new", page))


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
# very last
SOURCES = (Remanga(), ReComics(), MangaLib(), MangaBuff(), ReadManga(), Desu(), ComX(), MangaDex(), HentaiLib(),
           AllHentai(), Hentaichan())


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
