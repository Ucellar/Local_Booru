"""(v5.44.75) «Найти по названию» of «Скачать → Манга» and «Скачать → Манхва»: a title looked for by its name on the
sites of manga and manhwa at once, the answers put together — one title one tile.

The owner (5.11.2026), of his friend's reader: «у тебя прямо в приложении качать прямо выбирая обложки?» — so the
covers to choose from, then the title's chapters as «Найти главы» gives them (``manhwa_download.look``: every site's
chapters, the paid ones from the others).

The sites asked (:data:`ASKED`): Remanga, ReComics, MangaLib, MangaBuff, (v5.44.77) ReadManga, Desu, (v5.44.85)
Хентай-тян — only where hentai is shown (``sources(adult)``: not in «SFW») — each one's own search
(``manhwa_sources.Source.search``), side by side; com-x.life only by a link (its search goes through the window's
browser, a search by a name does not open it).  A site that does not answer — said in the line of the sites, the others
are shown.  One title on several sites is one tile (a name of theirs the same, the kind and the year not different —
``manhwa_sources.names_of`` / ``_fits``): the sites in it in the owner's order, its cover of the first that has one.  The
tiles in the order the sites gave them, a title whose name is the one asked first.

The search is a page task (:data:`TASK`): the page asks its state (:func:`state`) and shows the tiles as the sites
answer.  The covers come through the program (``/api/manhwa/hit-cover`` — :func:`cover`): only the addresses a search
gave, from the sites' own servers, kept in memory a while.
"""
from __future__ import annotations

import hashlib
import io
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from urllib.parse import urlparse

from core import manhwa_sources as S

_LOG = logging.getLogger("local_booru.manhwa")

TASK = "manhwa_search"                 # the page task
ASKED = ("remanga", "recomics", "mangalib", "mangabuff", "readmanga", "desu", "hentailib", "allhentai",
         "hentaichan")                                     # (v5.44.99) AllHentai
MAX_QUERY = 120
MAX_TILES = 80
DEADLINE = 30.0                        # seconds the search waits for the slowest site
COVER_HOSTS = ("remanga.org", "recomics.org", "imglib.info", "mangalib.me", "mangalib.org", "cdnlibs.org",
               "lib.social", "mangabuff.ru",
               "one-way.work", "zazaza.me", "readmanga.live", "readmanga.io", "desu.uno", "desu.me",   # (v5.44.77)
               "mangadex.org",                                                                        # (v5.44.79)
               "imgschan.xyz", "hentaichan.live", "h-chan.me", "henchan.pro",                        # (v5.44.85)
               "yani.tv",                     # (v5.44.90) the posters of YummyAnime («Видео → Лента», core/catalog.py)
               "hentasis1.top",               # (v5.44.93) …of Hentasis
               "nhentai.net",                 # (v5.44.97) the thumbnails of nhentai (t1…t4.nhentai.net)
               "aaa200.one", "allhen.online")  # (v5.44.99) AllHentai's covers (staticahen2.aaa200.one)
COVER_LIMIT = 4 * 1024 * 1024
COVER_TIMEOUT = (6, 15)                # (v5.44.95) a cover: (connect, read) seconds, asked once
COVER_FAILED_FOR = 5 * 60              # …one that did not come is not asked again this long
COVERS_KEPT = 300                      # covers kept in memory (a few dozen KB each)
SEARCHES_KEPT = 4


class _QuietNet(S.Net):
    """The sites asked without the program window's browser: a search by a name never opens it (a site that wants
    the check is said to be closed)."""

    def _past_check(self, url, closed, options):
        raise closed

    def _through_browser(self, url, what):
        raise S.Closed(S.closed_words(what))


@dataclass
class Tile:
    """A title of the answers: its names, kind, year, cover and the sites that have it."""
    names: list
    type: str = ""
    year: int = 0
    cover: str = ""                    # the cover's id in :attr:`Search.covers` ("" — none)
    adult: bool = False
    hits: list = field(default_factory=list)       # [(the site's order, its rank there, S.Hit)]

    def public(self, labels: dict, query_key: str) -> dict:
        from core import manhwa_download as D
        hits = sorted(self.hits, key=lambda x: (x[0], x[1]))
        best = hits[0][2]
        chapters = max((h.chapters for _o, _r, h in hits), default=0)
        rated = next((h for _o, _r, h in hits if h.rating), None)
        names = list(dict.fromkeys(n for n in self.names if n))
        return {
            "title": names[0] if names else best.key, "alt": [n for n in names[1:3]],
            "type": self.type, "year": self.year, "kind": "manga" if self.type.strip().lower() in D.MANGA_TYPES else
            ("manhwa" if self.type else ""), "cover": self.cover, "adult": self.adult, "chapters": chapters,
            "rating": round(rated.rating, 1) if rated else 0, "votes": rated.votes if rated else 0,
            "url": best.url, "exact": query_key in {S.name_key(n) for n in names},
            "sites": [{"site": h.site, "label": labels.get(h.site, h.site), "url": h.url, "chapters": h.chapters}
                      for _o, _r, h in hits],
        }


def merge(answers: dict, order: tuple = ASKED) -> list:
    """The sites' hits ({site: [S.Hit]}) as tiles: one title of several sites — one tile."""
    tiles: list = []
    for o, site in enumerate(order):
        for rank, hit in enumerate(answers.get(site) or []):
            keys = S.names_of(*hit.names)
            same = None
            if keys:
                for tile in tiles:
                    if any(h.site == site for _o, _r, h in tile.hits):
                        continue                     # one site's two answers are two titles
                    if keys & S.names_of(*tile.names) and S._fits(hit.type, hit.year, tile.type, tile.year):
                        same = tile
                        break
            if same is None:
                same = Tile(names=[])
                tiles.append(same)
            same.hits.append((o, rank, hit))
            same.names += [n for n in hit.names if n not in same.names]
            same.type = same.type or hit.type
            same.year = same.year or hit.year
            same.adult = same.adult or hit.adult
    return tiles


def _order_key(tile: Tile, query_key: str) -> tuple:
    exact = query_key in {S.name_key(n) for n in tile.names}
    best = min((r for _o, r, _h in tile.hits), default=0)
    return (0 if exact else 1, best, -len(tile.hits))


class Search:
    """One search: what the sites answered so far, as tiles."""

    def __init__(self, query: str, sources: list):
        self.query = query
        self.sources = sources
        self.id = 0
        self.lock = threading.Lock()
        self.answers: dict = {}            # site → [S.Hit]
        self.sites: dict = {s.name: {"site": s.name, "label": s.label, "state": "asking", "count": 0, "error": ""}
                            for s in sources}
        self.covers: dict = {}             # the cover's id → (its address, the site's address for Referer)
        self.done = False
        self.started = time.time()

    def put(self, source, hits: list | None, error: str = "") -> None:
        with self.lock:
            if self.done:                      # too late: the search has ended without it
                return
            if hits is not None:
                self.answers[source.name] = hits
            entry = self.sites[source.name]
            entry.update(state="failed" if error else "done", count=len(hits or []), error=error)

    def public(self) -> dict:
        with self.lock:
            answers = dict(self.answers)
            sites = [dict(self.sites[s.name]) for s in self.sources]
            done = self.done
        query_key = S.name_key(self.query)
        tiles = sorted(merge(answers, tuple(s.name for s in self.sources)), key=lambda t: _order_key(t, query_key))
        labels = {s.name: s.label for s in self.sources}
        out = []
        for tile in tiles[:MAX_TILES]:
            hits = sorted(tile.hits, key=lambda x: (x[0], x[1]))
            first = next((h for _o, _r, h in hits if h.cover and _cover_ok(h.cover)), None)
            if first is not None:
                cid = hashlib.sha1(first.cover.encode("utf-8")).hexdigest()[:16]
                with self.lock:
                    self.covers[cid] = (first.cover, first.url)
                tile.cover = cid
            out.append(tile.public(labels, query_key))
        return {"query": self.query, "sites": sites, "titles": out, "count": len(tiles), "done": done,
                "age": int(time.time() - self.started)}


_LOCK = threading.Lock()
_SEARCHES: "OrderedDict[int, Search]" = OrderedDict()
_COVERS: "OrderedDict[str, tuple]" = OrderedDict()       # the cover's address → (bytes, type)
_COVERS_FAILED: dict = {}                                  # (v5.44.95) the cover's address → when it did not come
_COVER_NET: "list" = []                                  # one session per site for the covers (kept alive)


def _fetch_cover(net, address: str, referer: str) -> tuple:
    first = None
    for link in S.picture_links(address):
        try:
            got = net.fetch(link, headers={"Accept": "image/avif,image/webp,image/*,*/*;q=0.8", "Referer": referer},
                            limit=COVER_LIMIT, paced=False, what="обложка", timeout=COVER_TIMEOUT, tries=1)
            S.gave_picture(link)
            return got
        except S.Closed:
            raise
        except S.SourceError as exc:
            first = first or exc
    raise first or S.SourceError("обложка не пришла")


def _cover_net():
    with _LOCK:
        if not _COVER_NET:
            _COVER_NET.append(_QuietNet())
        return _COVER_NET[0]


def sources(adult: bool = True) -> list:
    """The sites asked; (v5.44.85) ``adult`` False («SFW») — not the sites of hentai alone."""
    return [s for s in S.SOURCES if s.name in ASKED and s.readable and (adult or not s.adult_only)]


def ask_all(query: str, asked: list, put, stop_check, *, net=None, said=None, deadline: float = DEADLINE) -> None:
    """The sites' searches of ``query`` side by side: ``put(source, hits)`` as each answers, ``put(source, None,
    words)`` when it does not; the slow ones left after ``deadline`` (their requests stop)."""
    started = time.monotonic()
    late = lambda: stop_check() or time.monotonic() - started > deadline        # noqa: E731
    quiet = net or _QuietNet(late)

    def ask(source):
        if stop_check():
            return
        try:
            hits = source.search(quiet, query)
        except S.Closed as exc:
            put(source, None, f"не пускает программу ({exc})"[:200])
            return
        except S.SourceError as exc:
            put(source, None, str(exc)[:200])
            return
        except Exception as exc:                         # noqa: BLE001 - one site's surprise: the others are shown
            _LOG.warning("%s: the search for %r failed: %s", source.label, query, exc)
            put(source, None, "ответил непонятно")
            return
        put(source, hits)
        if said is not None:
            said(source, hits)

    pool = ThreadPoolExecutor(max_workers=len(asked) or 1, thread_name_prefix="manhwa-search")
    try:
        futures = [pool.submit(ask, s) for s in asked]
        while futures and not stop_check():
            left = deadline - (time.monotonic() - started)
            if left <= 0:
                break
            _done, pending = wait(futures, timeout=min(0.5, left))
            futures = list(pending)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def search_now(query: str, stop_check=None, *, net=None, asked: "list | None" = None) -> "tuple[list, dict]":
    """(v5.44.76) A search asked and waited for (``core/manga_archive.py``): (the tiles in their order, {site label:
    words of the ones that did not answer})."""
    asked = sources() if asked is None else asked
    stop_check = stop_check or (lambda: False)
    answers: dict = {}
    failed: dict = {}
    lock = threading.Lock()

    def put(source, hits, error=""):
        with lock:
            if hits is None:
                failed[source.label] = error
            else:
                answers[source.name] = hits
    ask_all(query, asked, put, stop_check, net=net)
    key = S.name_key(query)
    tiles = sorted(merge(answers, tuple(s.name for s in asked)), key=lambda t: _order_key(t, key))
    for source in asked:
        if source.name not in answers and source.label not in failed:
            failed[source.label] = "не ответил вовремя"
    return tiles, failed


def best_cover(tile: Tile) -> "tuple[str, str]":
    """(the cover's address, its site's page) of a tile — the first site in order that has one."""
    for _o, _r, hit in sorted(tile.hits, key=lambda x: (x[0], x[1])):
        if hit.cover and _cover_ok(hit.cover):
            return hit.cover, hit.url
    return "", ""


def start(query: str, *, net=None, adult: bool = True) -> "tuple[Search, object]":
    """The search of ``query`` as a page task: (the search, the task).  ``page_jobs.TaskBusy`` — another one goes and
    does not stop.  (v5.44.85) ``adult`` False — the sites of hentai alone not asked (``sources``)."""
    from core import page_jobs
    query = " ".join(str(query or "").split())[:MAX_QUERY]
    going = page_jobs.current(TASK)
    if going is not None and going.running:          # a new name: the old search is not waited for
        going.stop()
        going.wait(3.0)
    search = Search(query, sources(adult))

    def run(say, stop_check):
        say(f"Ищу «{query}»: " + ", ".join(s.label for s in search.sources))
        ask_all(query, search.sources, search.put, stop_check, net=net,
                said=lambda source, hits: say(f"{source.label}: {len(hits)}"))
        with search.lock:
            for entry in search.sites.values():
                if entry["state"] == "asking":
                    entry.update(state="failed", error="не ответил вовремя" if not stop_check() else "остановлено")
            search.done = True
        result = search.public()
        say(f"Нашлось тайтлов: {result['count']}")
        return {"search": search.id, "count": result["count"], "stopped": bool(stop_check())}

    task = page_jobs.start(TASK, f"Найти «{query}»", run)
    search.id = task.id
    with _LOCK:
        _SEARCHES[task.id] = search
        while len(_SEARCHES) > SEARCHES_KEPT:
            _SEARCHES.popitem(last=False)
    return search, task


def get(search_id) -> "Search | None":
    """A search of this run by its id; "last" — the newest one."""
    with _LOCK:
        if str(search_id) == "last":
            return next(reversed(_SEARCHES.values()), None)
        try:
            return _SEARCHES.get(int(search_id))
        except (TypeError, ValueError):
            return None


def _cover_ok(address: str) -> bool:
    host = (urlparse(str(address or "")).hostname or "").lower()
    return urlparse(str(address or "")).scheme in ("http", "https") and any(
        host == h or host.endswith("." + h) for h in (*COVER_HOSTS, *S.address_hosts()))   # (v5.44.78) a moved site


def cover(search_id, cover_id: str, *, net=None) -> "tuple[bytes, str] | None":
    """A tile's cover (bytes, type) — only an address the search gave; None — none (or not a picture)."""
    search = get(search_id)
    if search is None:
        return None
    with search.lock:
        kept = search.covers.get(str(cover_id or ""))
    if kept is None:
        return None
    return fetch_cover(kept[0], kept[1], net=net)


def _smaller(got: tuple, width: int) -> tuple:
    """(v5.44.90) A picture wider than ``width`` — made smaller, as JPEG: YummyAnime gives its posters only whole
    (1448 px, 600 KB each — forty of them on a page of «Лента»: hundreds of MB in the window); else as it came."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(got[0])) as picture:
            if picture.width <= width:
                return got
            picture.thumbnail((width, width * 4))
            out = io.BytesIO()
            picture.convert("RGB").save(out, "JPEG", quality=86, optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception:                                  # noqa: BLE001 — a picture PIL cannot read: as it came
        return got


def fetch_cover(address: str, page: str = "", *, net=None, shrink: int = 0) -> "tuple[bytes, str] | None":
    """A cover by its address — from the sites' own hosts only (:data:`COVER_HOSTS`), with its site as the Referer,
    kept in memory a while; None — none, or not a picture.  (v5.44.90) ``shrink`` — a picture wider than this is
    kept and given smaller (:func:`_smaller`)."""
    from core import manhwa_download as D
    if not _cover_ok(address):
        return None
    # (v5.44.123) a smaller one is kept apart: the menu of covers shows a cover whole (its size is the site's), «Лента»
    # the same address smaller
    key = f"{address}#{shrink}" if shrink else address
    with _LOCK:
        if key in _COVERS:
            _COVERS.move_to_end(key)
            return _COVERS[key]
        failed = _COVERS_FAILED.get(address)
        if failed is not None and time.monotonic() - failed < COVER_FAILED_FOR:
            return None
    site = urlparse(page or address)
    referer = f"{site.scheme}://{site.netloc}/" if site.netloc else ""
    try:
        # (v5.44.95) short and once, on each of its hosts (Хентай-тян's mirrors: ``S.picture_links``): a picture host
        # that hangs held a thread of the program's server three times 40 s for every cover of a page
        body, ctype = _fetch_cover(net or _cover_net(), address, referer)
    except S.SourceError as exc:
        _LOG.info("a cover of the search not taken (%s): %s", address, exc)
        with _LOCK:
            _COVERS_FAILED[address] = time.monotonic()
            while len(_COVERS_FAILED) > COVERS_KEPT:
                _COVERS_FAILED.pop(next(iter(_COVERS_FAILED)))
        return None
    ext = D._picture_type(body, ctype)
    kinds = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
             ".avif": "image/avif", ".bmp": "image/bmp"}
    if ext not in kinds:
        return None
    got = (bytes(body), kinds[ext])
    if shrink:
        got = _smaller(got, shrink)
    with _LOCK:
        _COVERS[key] = got
        while len(_COVERS) > COVERS_KEPT:
            _COVERS.popitem(last=False)
    return got
