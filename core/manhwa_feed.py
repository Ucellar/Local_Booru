"""(v5.44.83) «Лента» of the tab «Манхва»: the titles of the sites — updated lately or the most popular — right in the
program, to look through, open and read without downloading (the card of «Скачать» — «Читать с начала», ▶ at every
chapter: ``core/manga_online.py``) or to download.

The owner (5.11.2026), at the tab «Манхва»: «чет нет ленты» — and to «что там должно быть?»: «Лента тайтлов с сайтов».
The sites' catalogs are asked the ways the friend's reader Yomi 1.7 asks them (its ``popular``/``latest``):
``manhwa_sources.Source.feed``.

* A page of a site's catalog is asked once and kept :data:`TTL` (a site that did not answer — :data:`FAILED_TTL`).
* (v5.44.89; the owner's journal: «slow page 130.2s: /manhwa/feed?site=hentaichan» four times at once — Хентай-тян
  does not answer from his country, and every page of the program waited behind the four) a page is asked in its own
  thread (:func:`page` with ``wait`` — the request waits that long, then the page says «загружается» and asks again),
  one request of a page at a time however many ask it (:data:`_GOING`), shorter (:class:`FeedNet`: 15 s, two tries);
  the window's browser — for a site that wants its check (MangaDex), as «Скачать» does.
* The covers — only the addresses the sites gave (a cover's number → its address, :func:`cover`), fetched by
  ``manhwa_search.fetch_cover`` (the sites' own hosts, their Referer).
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from core import manhwa_sources as S

_LOG = logging.getLogger("local_booru.manhwa")

SITES = ("remanga", "mangalib", "mangabuff", "readmanga", "desu", "mangadex", "hentailib",   # (v5.44.96) HentaiLib
         "allhentai", "hentaichan")                                     # (v5.44.99) AllHentai; (v5.44.85) Хентай-тян
HOWS = (("new", "Обновления"), ("popular", "Популярное"))
TTL = 10 * 60                          # a page of a catalog kept this long (seconds)
FAILED_TTL = 5 * 60                    # …one that did not come, asked again after this (or by «Попробовать ещё раз»)
WAIT = 2.5                             # (v5.44.89) a page of the program waits this long for a catalog's page
PART_WAIT = 8.0                        # …its «загружается» asks again, waiting this long
PAGES_KEPT = 60
COVERS_KEPT = 2000
MAX_PAGE = 200


@dataclass
class Page:
    site: str
    how: str
    n: int
    hits: list = field(default_factory=list)       # manhwa_sources.Hit
    more: bool = False
    error: str = ""
    at: float = field(default_factory=time.time)


class FeedNet(S.Net):
    """(v5.44.89) The sites asked for a feed: 15 seconds, two tries (a page that does not come is said in half a
    minute, not two minutes); a check for robots — through the window's browser, as «Скачать» goes."""
    timeout = (6, 15)
    tries = 2


def feed_net(source) -> FeedNet:
    """(v5.44.95) The net of a site's feed page: a site whose pages are slow (``Source.slow_pages`` — Хентай-тян: 12–15
    seconds a page on 6.11.2026, the same on every mirror of it) waits longer, once; the page of the program does
    not wait for it anyway («… отвечает»)."""
    net = FeedNet()
    if getattr(source, "slow_pages", False):
        net.timeout, net.tries = SLOW_TIMEOUT, 1
    return net


SLOW_TIMEOUT = (8, 45)


_LOCK = threading.Lock()
_PAGES: "OrderedDict[tuple, Page]" = OrderedDict()
_GOING: dict = {}                                          # a page being asked → threading.Event (set when it came)
_COVERS: "OrderedDict[str, tuple]" = OrderedDict()         # a cover's number → (its address, the title's page)


def sources(adult: bool = True) -> list:
    """The sites of the feed, as their places say them (a site the program does not ask — not); (v5.44.85) ``adult``
    False («SFW») — not the sites of hentai alone."""
    out = []
    for name in SITES:
        source = S.by_name(name)
        if source is not None and source.readable and (adult or not source.adult_only):
            out.append(source)
    return out


def how_of(value: str) -> str:
    return value if value in dict(HOWS) else HOWS[0][0]


def forget() -> None:
    """Everything kept let go (the checks; a site's address changed)."""
    with _LOCK:
        _PAGES.clear()
        _COVERS.clear()
        _GOING.clear()


def page(site: str, how: str, n: int = 1, *, net=None, wait: "float | None" = None, again: bool = False) -> "Page | None":
    """Page ``n`` of the catalog of ``site`` (``how`` — "new" or "popular"). (v5.44.89) Asked in its own thread:
    ``wait`` — seconds to wait for it (None — until it comes); None when it has not come in that time (it goes on, the
    next ask finds it); ``again`` — a page that did not come is asked again at once («Попробовать ещё раз»)."""
    how = how_of(how)
    n = max(1, min(MAX_PAGE, int(n or 1)))
    key = (site, how, n)
    source = S.by_name(site) if site in SITES else None
    if source is None or not source.readable:
        return Page(site, how, n, error="Такого сайта в ленте нет")
    with _LOCK:
        kept = _PAGES.get(key)
        if kept is not None and not (again and kept.error) and \
                time.time() - kept.at < (FAILED_TTL if kept.error else TTL):
            _PAGES.move_to_end(key)
            return kept
        going = _GOING.get(key)
        if going is None:
            going = _GOING[key] = threading.Event()
            threading.Thread(target=_ask, args=(key, source, net, going), daemon=True,
                             name=f"feed-{site}-{how}-{n}").start()
    if not going.wait(wait):
        return None
    with _LOCK:
        return _PAGES.get(key) or Page(site, how, n, error=f"{source.label} не ответил")


def _ask(key: tuple, source, net, going) -> None:
    from core.redaction import sanitize_text
    site, how, n = key
    try:
        hits, more = source.feed(net or feed_net(source), how, n)
        got = Page(site, how, n, hits=[h for h in hits if h.key and h.url], more=bool(more))
    except S.SourceError as exc:
        _LOG.info("feed: %s (%s, page %d) did not come: %s", source.label, how, n, exc)
        got = Page(site, how, n, error=sanitize_text(str(exc))[:300] or f"{source.label} не ответил")
    except Exception as exc:                    # noqa: BLE001 — a page that broke is said, the thread ends
        _LOG.warning("feed: %s (%s, page %d) broke: %s", source.label, how, n, exc, exc_info=True)
        got = Page(site, how, n, error=f"{source.label}: ответ не разобрался")
    with _LOCK:
        _PAGES[key] = got
        _PAGES.move_to_end(key)
        while len(_PAGES) > PAGES_KEPT:
            _PAGES.popitem(last=False)
        _GOING.pop(key, None)
    going.set()


def cover_id(hit) -> str:
    """The number of a tile's cover (only this address is fetched by it) — "" without one."""
    if not hit.cover:
        return ""
    number = hashlib.sha1(hit.cover.encode("utf-8")).hexdigest()[:16]
    with _LOCK:
        _COVERS[number] = (hit.cover, hit.url)
        _COVERS.move_to_end(number)
        while len(_COVERS) > COVERS_KEPT:
            _COVERS.popitem(last=False)
    return number


def cover(number: str, *, net=None) -> "tuple | None":
    """(bytes, type) of a cover the feed gave — None: not one of them, or it did not come."""
    with _LOCK:
        kept = _COVERS.get(str(number or ""))
    if kept is None:
        return None
    from core import manhwa_search as MS
    return MS.fetch_cover(kept[0], kept[1], net=net)
