"""(v5.44.169) «Манга через парсер»: nhentai confirms a folder too.

The owner (12.11.2026): «я только что проверил мангу которую написало "не манга" snao выдал блять 91.45% на nhentai
так что что то у тебя нихуя не так сделанно».  It was: SauceNAO was asked about its indexes of manga alone (Madokami,
MangaDex — ``manga_pictures.INDEXES``), never about its indexes of hentai manga and doujinshi («H-Misc»: the pages of
nhentai's and e-hentai's galleries — ``manga_pictures.HENTAI_INDEXES``), and nhentai was no site that confirms a
folder at all (it is no ``manhwa_sources.Source``: its galleries have a downloader, a feed and a reader of their own —
``core/manga_download.py``, ``core/nhentai.py``, ``core/gallery_read.py``).  A doujinshi the owner keeps in a folder
was «не манга» whatever SauceNAO knew of it.

What this module adds to the ways of ``core/parser_manga.check_work``:

* **its files** — a link of a gallery in them (``nhentai.net/g/<number>``: a shortcut, a description; nhentai's own
  ``meta.json`` — its ``id``), the folder named by the gallery's number («nhentai-123456 - Title», «123456»,
  «[123456] Title»): :func:`by_number`;
* **its names** — searched on nhentai as on the sites of manga (:func:`by_names`; a name in Russian is not: the site
  has no Russian) — the folder's whole name word for word first when it is a gallery's («[Circle (Artist)] Title (C99)
  [English]»), then the title alone;
* **SauceNAO** — the galleries its indexes of hentai name (:func:`by_hits`): found on nhentai by their names.

**What confirms** is what confirms everywhere (``core/parser_manga_pages.py``): the pages.  The folder's first pages
(pHash) against the small pictures of the gallery's first pages — one page alike when the gallery is called by the very
name, two (at 90 %) when it is called otherwise; a name alone, a number alone, SauceNAO's word alone — never.  Of the
galleries a search gives (one work in several languages is several galleries) the one whose pages are nearest is taken.

**The program's gates**: a search leaves out the tags ``gates.left_out`` names (``nhentai.search_words``), its
answers pass ``catalog.nhentai_items`` (``gates.title`` by a gallery's tags and titles), and a gallery is read only
through ``manga_download.gallery_of`` (``gates.title`` by its whole description): one a gate closes is neither compared
nor described — it confirms nothing.

**A confirmed folder** is copied into «Манга» as any confirmed manga; :func:`describe_copy` puts the gallery's own
description beside its pages (``meta.json``, as the downloader writes it: the titles, the tags by their kinds, the link
— ``core/manga_library.py`` reads it) and says how many of the gallery's pages the folder has.  A copy whose pages are
numbered as the gallery's (page 7 is the gallery's page 7) and that is named by the gallery's number is the folder the
program's own downloader takes for that gallery (``manga_download.folder_for``): «Докачать» of its card downloads
only the pages the folder has not.

**The pace**: its search answers ten times a minute (429 after that) — :data:`PER_MINUTE` of them are the parser's,
«Лента» has the rest; a search waits for its turn (a stop ends the wait); after a 429 the site is not asked for
:data:`REST` seconds — :class:`Late`: the folder waits and asks it again (``check_work``'s «late»).

(v5.44.170) **The pages a copy misses are downloaded by themselves** (:func:`fill` — at the copy's making,
``parser_manga.linked_values``; for the copies of before — ``parser_manga._fill_galleries``): under «Докачивать главы,
которых нет в папке», only into a copy numbered as the gallery (:func:`aligned` looks at its first pages and at its
last).  **A picture lying loose** that is a page of a gallery (``core/parser_loose.py``): :func:`check_loose` finds the
gallery and the page (:func:`page_of`), :func:`fetch` downloads the gallery.  The galleries asked are kept an hour
(:func:`_kept`): a look cut short goes on.

``parser_manga`` is imported inside the functions (it imports this module).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from core import manga_detect as MD

_LOG = logging.getLogger("local_booru.manga")

LABEL = "nhentai"
SITE = "nhentai"                # its name among the sites that did not answer (``check_work``'s «late»)
PER_MINUTE = 6                  # its searches a minute at most (the site lets ten: «Лента» has the rest)
REST = 65.0                     # after its «слишком часто» (429): not asked this long
NAMES = 2                       # names of one manga searched on it at most
QUERIES = 2                     # searches of one name at most
GALLERIES = 6                   # galleries of one name whose pages are compared, after ranking editions
SEARCH_PAGES = 3                # bounded search beyond the popular first page; every page takes its rate turn
PAGES_FIRST = 4                 # their first pages (small pictures) asked at first
PAGES_MORE = 4                  # …and more of them when it is worth it (the very name; a page alike already)
ALIGNED = 4                     # numbered pages of a copy compared with the gallery's pages of the same numbers
LOOSE_PAGES = 120               # (v5.44.170) pages of a gallery a picture lying loose is compared with at most
KEPT_FOR = 3600.0               # …a gallery and the pHashes of its pages asked so far are kept so long (a look cut
#                                 short by a round's time goes on from where it stopped)
KEPT = 40                       # …of so many galleries at most
SEARCH_AGAIN = 24 * 3600        # a name nhentai had no gallery of (for a loose picture) is searched again so much later
TRIES = 3                       # nhentai not answering about a loose picture: asked again soon so many times, then daily

_LINK = re.compile(r"^(?:https?://)?(?:www\.)?nhentai\.net/g/(\d{1,7})(?:[/?#].*)?$", re.I)
_NAMED = re.compile(r"^nhentai[-_ #]*(\d{1,7})(?!\d)", re.I)
_NUMBERED = re.compile(r"^(?:\[(\d{5,7})\]|\((\d{5,7})\)|#(\d{5,7})|(\d{5,7}))(?!\d)(?:$|\s*[-–—_]\s*\S|\s+\S)")
_WORD = re.compile(r"[^\W_]+")
_STATE = {"next": 0.0, "rest": 0.0}
_LOCK = threading.Lock()


class Late(Exception):
    """nhentai is not to be asked now, or did not answer: asked again later (the words — why)."""


# ── what names a gallery ────────────────────────────────────────────────────────────────────────────────────────────
def number_of(url) -> int:
    """The number of the gallery a link names (``nhentai.net/g/123456/``, a page of it too); 0 — not such a link."""
    found = _LINK.match(str(url or "").strip())
    return int(found.group(1)) if found else 0


def link_of(number: int) -> str:
    from core import nhentai as N
    return N.link_of(int(number))


def number_in_name(name) -> int:
    """The number of a gallery a folder is named by: «nhentai-123456 - Title», «123456», «[123456] Title», «#123456»,
    «123456 - Title» (five to seven digits — a year, a page's number are not one); 0 — none.  A number alone confirms
    nothing: the gallery of that number is asked, and its pages decide."""
    text = str(name or "").strip()
    found = _NAMED.match(text)
    if found:
        return int(found.group(1))
    found = _NUMBERED.match(text)
    return int(next(g for g in found.groups() if g)) if found else 0


def wanted(name) -> bool:
    """A name nhentai is searched by: one that tells a title (``manhwa_sources.names_of``) and is not in Russian — the
    site has Japanese, English and Chinese alone."""
    from core import manhwa_sources as S
    text = str(name or "")
    return bool(S.names_of(text)) and not re.search(r"[А-Яа-яЁё]", text)


def first(work: "MD.Work") -> bool:
    """nhentai is asked before the sites of manga: the folder's name is a gallery's («[Circle] Title [English]» — a
    group in brackets and no Russian) or carries a gallery's number."""
    from core import manga_archive as A
    name = str(work.name or "")
    return bool(number_in_name(name)) or bool(A._BRACKETS.search(name)) and wanted(A.clean_name(name))


def queries(name: str, raw: str = "") -> list:
    """The words nhentai is asked by for ``name`` (:data:`QUERIES` at most, the next when the one before found
    nothing): the folder's whole name word for word when it says more than the name («[Circle (Artist)] Title (C99)
    [English]» — every word of it is in the gallery's own title or among its tags), the name as it stands (in quotes:
    the words side by side), the name's words."""
    from core import manga_archive as A, manhwa_sources as S
    title = " ".join(str(name or "").replace('"', " ").split())[:120]
    own = _WORD.findall(title)
    out = []
    if raw and S.name_key(raw) != S.name_key(name):
        words = [w for w in _WORD.findall(A.plain_name(raw)) if not (w.isdigit() and len(w) >= 5)
                 and w.casefold() != "nhentai"][:14]
        if len(words) >= 2 and {w.casefold() for w in words} - {w.casefold() for w in own}:
            out.append(" ".join(words)[:160])
    if title:
        out.append(f'"{title}"')
        if len(own) >= 2 and " ".join(own) != title:
            out.append(" ".join(own[:8]))
    return list(dict.fromkeys(out))[:QUERIES]


# ── asking the site ─────────────────────────────────────────────────────────────────────────────────────────────────
def _turn(asker) -> None:
    """Waits for the next search's turn (:data:`PER_MINUTE`; a stop ends the wait); :class:`Late` — the site said
    «too often» a moment ago and rests."""
    with _LOCK:
        now = time.monotonic()
        if _STATE["rest"] > now:
            raise Late(f"{LABEL} просит спрашивать реже")
        at = max(now, _STATE["next"])
        _STATE["next"] = at + 60.0 / max(1, int(PER_MINUTE))
    while time.monotonic() < at:
        if asker.stopped():
            raise Late("остановлено")
        time.sleep(min(0.2, max(0.0, at - time.monotonic())))


def _json(net, url: str):
    """Its API asked once; :class:`Late` — it did not answer, or said «too often» (then it rests :data:`REST`
    seconds); ``manhwa_sources.SourceError`` — it answered that there is no such thing, or refused."""
    from core import manhwa_sources as S, nhentai as N
    try:
        return net.json(url, what=LABEL, tries=1, headers={"Accept": "application/json", "Referer": N.SITE + "/"})
    except S.SourceError as exc:
        if "429" in str(exc):
            with _LOCK:
                _STATE["rest"] = time.monotonic() + REST
            raise Late(f"{LABEL} просит спрашивать реже") from exc
        if "остановлено" in str(exc):
            raise Late("остановлено") from exc
        if S.transient(exc):                            # (the words of its own: an address is never said on)
            raise Late(str(exc) if isinstance(exc, S.Silent) else f"{LABEL} не ответил") from exc
        raise


def search(asker, query: str) -> list:
    """The galleries of at most :data:`SEARCH_PAGES` search pages the program's gate lets through: [{"number",
    "names": [the title without its brackets, the whole English one, the Japanese one], "pages"}], the most liked
    first.  :class:`Late`; ``manhwa_sources.SourceError`` — the site refused."""
    from core import catalog as C, manhwa_sources as S, nhentai as N
    key = (N.API, query)
    now = time.monotonic()
    with _GALLERIES_LOCK:
        for old in [q for q, value in _SEARCH_RESULTS.items() if now - value["at"] > KEPT_FOR]:
            del _SEARCH_RESULTS[old]
        kept = _SEARCH_RESULTS.get(key)
        if kept is None:
            kept = _SEARCH_RESULTS[key] = {"at": now, "next": 1, "rows": [], "seen": set()}
            while len(_SEARCH_RESULTS) > KEPT:
                del _SEARCH_RESULTS[min(_SEARCH_RESULTS, key=lambda q: _SEARCH_RESULTS[q]["at"])]
    rows, seen = kept["rows"], kept["seen"]
    for page in range(kept["next"], SEARCH_PAGES + 1):
        if asker.stopped():
            raise Late("остановлено")
        _turn(asker)
        data = _json(_net(asker), N.list_url("popular", page, query))
        if isinstance(data, dict):
            data = {**data, "page": page}                # API v2 does not always repeat the requested page number
        items, more = C.nhentai_items(data, _net(asker))
        raw = data.get("result") if isinstance(data, dict) else None
        pages = {S._int(g.get("id")): S._int(g.get("num_pages")) for g in raw or [] if isinstance(g, dict)}
        raw_ids = set(pages) - {0}
        rows += [{"number": int(item.key), "names": [n for n in item.names if n], "pages": pages.get(int(item.key), 0)}
                 for item in items if str(item.key).isdigit() and int(item.key) not in seen]
        kept["next"] = page + 1
        if not more or not raw_ids - seen:
            break
        seen.update(raw_ids)
    with _GALLERIES_LOCK:
        _SEARCH_RESULTS.pop(key, None)                  # only interrupted searches persist across rounds
    return rows


def fetch_gallery(net, number: int):
    """The gallery of this number as the site's API describes it — past the program's gate
    (``manga_download.gallery_of``: one a gate closes — ``manga_download.Forbidden`` with the gate's words).
    :class:`Late` — the site did not answer; ``manga_download.MangaError`` — no such gallery, a description the program
    does not understand."""
    from core import manga_download as D, manhwa_sources as S, nhentai as N
    number = int(number)
    try:
        data = _json(net, f"{N.API}/galleries/{number}")
    except S.Missing as exc:
        raise D.MangaError(f"Галереи {number} на nhentai нет") from exc
    except S.SourceError as exc:
        raise D.MangaError(str(exc)) from exc
    return D.gallery_of(data, number)


def quiet_net(stop_check=None):
    """nhentai is asked without the program window's browser: its API answers a program — one that wants a check
    for robots is said to be closed, and nothing is opened to pass it."""
    from core import manhwa_search as MS
    return MS._QuietNet(stop_check or (lambda: False))


def _net(asker):
    """…the round's one."""
    key = (SITE, "net")
    net = asker._titles.get(key)
    if net is None:
        net = asker._titles[key] = quiet_net(asker.stop_check)
    return net


_GALLERIES: dict = {}                   # a gallery's number -> {"at", "gallery", "marks": {a page: its pHash}}
_SEARCH_RESULTS: dict = {}              # interrupted searches resume after the last completely read page
_GALLERIES_LOCK = threading.Lock()


def forget() -> None:
    """The galleries kept (:func:`_kept`) forgotten."""
    with _GALLERIES_LOCK:
        _GALLERIES.clear()
        _SEARCH_RESULTS.clear()


def _kept(asker, number: int) -> dict:
    """The gallery and the pHashes of its pages asked so far — kept :data:`KEPT_FOR` seconds ((v5.44.170) not a
    round: a picture lying loose is compared with every page of its gallery, and a round's time is short)."""
    number = int(number)
    now = time.monotonic()
    with _GALLERIES_LOCK:
        for old in [n for n, kept in _GALLERIES.items() if now - kept["at"] > KEPT_FOR]:
            del _GALLERIES[old]
        kept = _GALLERIES.get(number)
    if kept is None:
        kept = {"at": now, "gallery": fetch_gallery(_net(asker), number), "marks": {}}
        with _GALLERIES_LOCK:
            _GALLERIES[number] = kept
            while len(_GALLERIES) > KEPT:
                del _GALLERIES[min(_GALLERIES, key=lambda n: _GALLERIES[n]["at"])]
    return kept


def thumb_links(found, n: int) -> list:
    """The addresses of the small picture of page ``n`` (from 1) of a gallery — on each of the site's servers of
    small pictures, the gallery's own first; [] — the description names none."""
    from core import nhentai as N
    pages = found.data.get("pages") if isinstance(found.data.get("pages"), list) else []
    if pages:
        page = pages[n - 1] if 0 < n <= len(pages) and isinstance(pages[n - 1], dict) else {}
        path = str(page.get("thumbnail") or "").lstrip("/")
    else:
        path = f"galleries/{found.media_id}/{n}t{found.kinds[n - 1]}" if 0 < n <= len(found.kinds) else ""
    if not N.path_ok(path):
        return []
    servers = list(N.THUMB_SERVERS)
    start = int(found.id) % len(servers) if servers else 0
    return [f"{server.rstrip('/')}/{path}" for server in servers[start:] + servers[:start]]


def _page_mark(net, found, n: int) -> str:
    """The pHash of the small picture of page ``n`` ("" — it tells nothing: empty, not a picture Pillow reads, not
    given); :class:`Late` — the site's servers of pictures did not answer."""
    from core import manhwa_sources as S, nhentai as N, parser_manga as PM, parser_manga_pages as PP
    links = thumb_links(found, n)
    if not links:
        return ""
    try:
        data, _ctype = S.fetch_picture(net, S.Picture(url=links[0], fallbacks=links[1:]), what=LABEL,
                                       headers={"Referer": N.SITE + "/",
                                                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
    except S.SourceError as exc:
        if "остановлено" in str(exc):
            raise Late("остановлено") from exc
        if S.transient(exc):
            raise Late(f"{LABEL}: картинки страниц не пришли") from exc
        _LOG.info("parser manga: nhentai %s: page %s not given: %s", found.id, n, type(exc).__name__)
        return ""
    mark = PM._phash(data)
    return mark if mark and not PP.flat(mark) else ""


def _marks(asker, kept: dict, count: int) -> list:
    """[(the page's number, its pHash)] of the gallery's first ``count`` pages that tell something."""
    found = kept["gallery"]
    for n in range(1, min(int(count), found.pages) + 1):
        if n in kept["marks"]:
            continue
        if asker.stopped():
            raise Late("остановлено")
        kept["marks"][n] = _page_mark(_net(asker), found, n)
    return [(n, mark) for n, mark in sorted(kept["marks"].items()) if mark and n <= count]


# ── the folder's side ───────────────────────────────────────────────────────────────────────────────────────────────
def _local(asker, work: "MD.Work", listed: list) -> list:
    """[(the page's number by its file's name — None: it has none, its pHash)] of the folder's pages compared: the
    first ``parser_manga.LOCAL_PAGES`` that tell something (kept for the round)."""
    from core import parser_manga as PM, parser_manga_pages as PP
    key = ("local", work.key)
    kept = asker._titles.get(key)
    if kept is None:
        kept = []
        for index, number, path in PP.local(work, listed):
            mark = PM._phash(PM._bytes(work.parts[index], path))
            if mark and not PP.flat(mark):
                kept.append((number, mark))
                if len(kept) >= PM.LOCAL_PAGES:
                    break
        asker._titles[key] = kept
    return kept


def _pairs(local: list, marks: list, limit: int) -> tuple:
    """([(the folder's page, the gallery's page alike it)], how far they are in all) — each page of the gallery for
    one page of the folder, the nearest one."""
    from core.tagger.hashing import phash_distance
    free = list(marks)
    pairs, far = [], 0
    for number, mark in local:
        best = min(free, key=lambda x: phash_distance(mark, x[1]), default=None)
        if best is None:
            break
        distance = phash_distance(mark, best[1])
        if distance <= limit:
            free.remove(best)
            pairs.append((number, best[0]))
            far += distance
    return pairs, far


def _compare(asker, local: list, number: int, *, exact: bool, limit: int, hint: int = 0) -> dict:
    """The folder's pages against the gallery ``number``: {"alike", "local", "site" (its pages asked), "pages" (all of
    them), "name", "far", "pairs", "ok"}.  A gallery of the very name is asked for more pages when none is alike yet;
    any gallery — when one page is alike and one more would decide.  :class:`Late`, ``manga_download.MangaError``
    (``Forbidden`` — a gate closed the gallery)."""
    kept = _kept(asker, number)
    found = kept["gallery"]
    # An indexed middle page, or original page numbers of a partial folder, are stronger places to start.
    suggested = [n for n in (hint, hint - 1, hint + 1, hint + 2) if hint and 1 <= n <= found.pages]
    suggested += [n for n, _mark in local if isinstance(n, int) and 1 <= n <= found.pages]
    for n in dict.fromkeys(suggested[:8]):
        if n not in kept["marks"]:
            if asker.stopped():
                raise Late("остановлено")
            kept["marks"][n] = _page_mark(_net(asker), found, n)
    marks = _marks(asker, kept, PAGES_FIRST)
    marks += [(n, kept["marks"][n]) for n in dict.fromkeys(suggested) if n > PAGES_FIRST and kept["marks"].get(n)]
    pairs, far = _pairs(local, marks, limit)
    if (len(pairs) == 1 and not exact) or (not pairs and exact):
        marks = _marks(asker, kept, PAGES_FIRST + PAGES_MORE)
        marks += [(n, kept["marks"][n]) for n in dict.fromkeys(suggested)
                  if n > PAGES_FIRST + PAGES_MORE and kept["marks"].get(n)]
        pairs, far = _pairs(local, marks, limit)
    asked = len({n for n, _mark in marks})
    return {"alike": len(pairs), "local": len(local), "site": asked, "pages": found.pages, "name": found.title,
            "far": far, "pairs": pairs, "ok": len(pairs) >= 2 or (len(pairs) >= 1 and exact)}


# ── the ways ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _candidates(asker, name: str, raw: str, count: int) -> list:
    """The galleries the searches of a name give, the likeliest first (:data:`GALLERIES` at most): one called by the
    folder's whole name, one called by the name, then the others — of each kind the one whose number of pages is
    nearest to the folder's; ``exact`` — it is called by the very name."""
    from core import manhwa_sources as S, nhentai as N
    key, whole = S.name_key(name), S.name_key(raw) if raw else ""

    def rank(row: dict) -> tuple:
        keys = {S.name_key(n) for n in row["names"]} | {S.name_key(N.pretty(n)) for n in row["names"]}
        named_whole = bool(whole) and whole in keys
        row["exact"] = named_whole or key in keys
        apart = abs(int(row["pages"]) - count) if row["pages"] and count else 10 ** 6
        return (0 if named_whole else 1 if row["exact"] else 2, apart)
    rows: list = []
    for query in queries(name, raw):                    # (the next one — while none found is called by the name)
        there = {row["number"] for row in rows}
        rows += [row for row in search(asker, query) if row["number"] not in there]
        rows.sort(key=rank)
        if asker.stopped() or (rows and rows[0]["exact"]):
            break
    return rows[:GALLERIES]


def late_add(late: "dict | None", key: str, name: str, sites: list) -> None:
    """(``check_work``'s «late») The sites that did not answer about a name: asked again later."""
    if late is None or not sites:
        return
    entry = late.setdefault(key, {"name": name, "sites": []})
    entry["sites"] = sorted({*entry.get("sites", []), *sites})


def late_drop(late: "dict | None", key: str, sites: list) -> None:
    """…the ones that answered now."""
    entry = (late or {}).get(key)
    if entry is None:
        return
    entry["sites"] = [s for s in entry.get("sites", []) if s not in sites]
    if not entry["sites"]:
        late.pop(key, None)


def by_names(asker, work: "MD.Work", listed: list, names: list, *, how: str = "site", said: str = "",
             searched: "set | None" = None, seen: "list | None" = None, late: "dict | None" = None,
             raw: str = "") -> tuple:
    """``names`` searched on nhentai (:data:`NAMES` of them at most; a name in Russian is not), the pages of the
    galleries each gives compared with the folder's: (the ``Verdict``, {}) — the gallery whose pages are the folder's
    (of several — the nearest); (None, {a name's key: (the link of a gallery of that very name, its name)}) — none.
    ``raw`` — the folder's whole name (the first name is asked by it first).  ``searched`` — the names asked already
    (their keys with «nh:» before): not asked again; ``seen`` — what each name gave, for the words of «не манга»
    (``parser_manga_pages.not_words``); ``late`` — the names nhentai did not answer about (:func:`late_add`): asked
    again by the next check."""
    from core import manga_download as D, manhwa_sources as S, parser_manga as PM, parser_manga_pages as PP
    local = _local(asker, work, listed)
    found: dict = {}
    if not local:
        return None, found
    limit = PM._limit(asker.settings)
    count = sum(len(pages) for pages in listed)
    asked = 0
    for name in names:
        if asker.stopped() or asked >= NAMES:
            break
        if not wanted(name):
            continue
        key = S.name_key(name)
        if searched is not None and "nh:" + key in searched:
            continue
        asked += 1
        note = {"name": name, "titles": []}
        best = None
        try:
            for row in _candidates(asker, name, raw if asked == 1 else "", count):
                if asker.stopped():
                    break
                shown = row["names"][0] if row["names"] else name
                try:
                    got = _compare(asker, local, row["number"], exact=row["exact"],
                                   limit=limit if row["exact"] else min(limit, PP.STRICT))
                except D.MangaError as exc:             # (a gate closed it — its words; no such gallery now)
                    note["titles"].append({"title": shown, "label": LABEL, "error": str(exc), "loose": not row["exact"],
                                           "gallery": True})
                    continue
                if row["exact"]:
                    found.setdefault(key, (link_of(row["number"]), got["name"] or shown))
                if got["ok"]:
                    if best is None or (got["alike"], -got["far"]) > (best[1]["alike"], -best[1]["far"]):
                        best = (row, got)
                    if row["exact"] and got["alike"] >= max(2, got["local"]) and got["pages"] == count:
                        break                           # (called by the name, every page compared is its, as many
                    continue                            # of them: no other gallery is nearer)
                note["titles"].append({"title": got["name"] or shown, "label": LABEL, "alike": got["alike"],
                                       "local": got["local"], "site": got["site"], "first": "",
                                       "loose": not row["exact"], "gallery": True})
        except Late as exc:
            if best is None:                            # (it did not answer: this name is asked again later)
                _LOG.info("parser manga: nhentai not asked about %r: %s", name, exc)
                late_add(late, key, name, [SITE])
                continue
        except S.SourceError as exc:                    # it refused the search: this name says nothing here
            _LOG.info("parser manga: nhentai refused the search of %r: %s", name, exc)
        if seen is not None:
            seen.append(note)
        late_drop(late, key, [SITE])
        if searched is not None and not asker.stopped():
            searched.add("nh:" + key)
        if best is not None:
            row, got = best
            counted = f"({got['alike']} из {got['local']})"
            by = (f"{said}«{name}» → галерея nhentai «{got['name']}»: страницы совпали {counted}" if row["exact"] else
                  f"{said}«{name}» → галерея nhentai «{got['name']}» — название другое, но страницы те же {counted}, "
                  "совпадение от 90 %")
            return PM.Verdict(PM.MANGA, how, by, title=got["name"], url=link_of(row["number"])), found
    return None, found


def by_number(asker, work: "MD.Work", listed: list, number: int, *, said: str, how: str = "link",
              exact: bool = True):
    """The gallery of a number the folder itself names (a link in its files — one page alike confirms, as the link
    of a title of a site of manga does; its own name — two: a number may be anything's): the ``Verdict`` — its pages
    are the folder's; None — not (no such gallery, a gate closed it, other pages; nhentai not answering — counted in
    ``asker.down``: the folder waits)."""
    from core import manga_download as D, parser_manga as PM
    local = _local(asker, work, listed)
    if not local or not number:
        return None
    try:
        got = _compare(asker, local, int(number), exact=exact, limit=PM._limit(asker.settings))
    except Late:
        asker.down += 1
        return None
    except D.MangaError as exc:
        _LOG.info("parser manga: nhentai %s named by the folder not taken: %s", number, exc)
        return None
    if not got["ok"]:
        return None
    return PM.Verdict(PM.MANGA, how, f"{said} → галерея nhentai «{got['name']}»: страницы совпали "
                                     f"({got['alike']} из {got['local']})", title=got["name"], url=link_of(number))


def by_hits(asker, work: "MD.Work", listed: list, hits: list, *, searched: "set | None" = None,
            seen: "list | None" = None, late: "dict | None" = None) -> tuple:
    """SauceNAO's galleries (``manga_pictures.hentai_hits``, the two best) found on nhentai by their names and
    compared: (the ``Verdict`` or None, {a name's key: (the gallery's link, its name)}, the names without their
    brackets — names for the sites of manga too)."""
    from core import nhentai as N, manga_download as D, parser_manga as PM, manhwa_sources as S
    found: dict = {}
    names: list = []
    tried = set()
    for hit in hits[:4]:
        if asker.stopped():
            break
        choices = [(N.pretty(n) or n, n) for n in hit["names"] if wanted(n) and wanted(N.pretty(n) or n)]
        if not choices:
            continue
        for title, _whole in choices:
            if title not in names:
                names.append(title)
        where = "nhentai" if hit.get("index") == 18 else "e-hentai"
        said = f"SauceNAO ({hit['sim']:.0f} %, {where}) — "
        number = int(hit.get("gallery") or 0)
        if number and number not in tried:
            tried.add(number)
            try:
                gallery = _kept(asker, number)["gallery"]
                if called(gallery, hit["names"]):
                    got = _compare(asker, _local(asker, work, listed), number, exact=True,
                                   limit=PM._limit(asker.settings), hint=int(hit.get("page") or 0))
                    if got["ok"]:
                        verdict = PM.Verdict(PM.MANGA, "saucenao", said + f"«{got['name']}»: страницы совпали "
                                             f"({got['alike']} из {got['local']})", title=got["name"], url=link_of(number),
                                             sim=float(hit["sim"]))
                        return verdict, found, names
            except Late:
                asker.down += 1
            except D.MangaError as exc:
                _LOG.info("parser manga: indexed gallery %s not taken: %s", number, type(exc).__name__)
        for title, whole in choices[:NAMES]:
            identity = ("name", S.name_key(whole))
            if identity in tried:
                continue
            tried.add(identity)
            verdict, titles = by_names(asker, work, listed, [title], how="saucenao", raw=whole, searched=searched,
                                       seen=seen, late=late, said=said)
            found.update({k: v for k, v in titles.items() if k not in found})
            if verdict is not None:
                verdict.sim = float(hit["sim"])
                return verdict, found, names
    return None, found, names


# ── (v5.44.170) a picture lying loose (``core/parser_loose.py``) ─────────────────────────────────────────────────────
def page_of(asker, number: int, mark: str, limit: int, *, hint: int = 0, deadline: float = 0.0) -> tuple:
    """Which page of the gallery a picture is: (the page's number — 0: none of the pages compared, whether every page
    was compared — of the first :data:`LOOSE_PAGES`).  The page SauceNAO's index named (``hint``) and its neighbours
    first, then the rest in order; the pages asked are kept (:func:`_kept`): past ``deadline`` no more are asked — the
    next round goes on.  :class:`Late`, ``manga_download.MangaError`` (``Forbidden`` — a gate closed the gallery)."""
    from core.tagger.hashing import phash_distance
    kept = _kept(asker, number)
    found = kept["gallery"]
    order = [n for n in (hint, hint - 1, hint + 1) if 1 <= n <= found.pages]
    order += [n for n in range(1, min(found.pages, LOOSE_PAGES) + 1) if n not in order]
    whole = True
    for n in order:
        if n not in kept["marks"]:
            if asker.stopped():
                raise Late("остановлено")
            if deadline and time.monotonic() > deadline:
                whole = False                           # (the ones kept already are still compared)
                continue
            kept["marks"][n] = _page_mark(_net(asker), found, n)
        theirs = kept["marks"][n]
        if theirs and phash_distance(mark, theirs) <= limit:
            return n, True
    return 0, whole


def called(found, names) -> bool:
    """The gallery is called by one of ``names`` — its titles as the site gives them, with and without their
    brackets."""
    from core import manhwa_sources as S, nhentai as N
    mine = {S.name_key(t) for raw in (found.english, found.japanese, found.pretty) if raw
            for t in (raw, N.pretty(raw))}
    asked = {S.name_key(t) for raw in names if raw for t in (str(raw), N.pretty(str(raw)))}
    return bool((mine & asked) - {""})


def is_gallery(group: dict) -> bool:
    """A manga of the loose pictures that is a gallery: SauceNAO named it by its index of hentai
    (``parser_loose._note``), or a gallery of nhentai confirmed it."""
    return bool(group.get("gallery")) or bool(number_of(group.get("url")))


def check_loose(settings: dict, asker, key: str, group: dict, probes: list, deadline: float) -> dict:
    """(``parser_loose_pages.check``) One gallery's pictures lying loose — ``probes``: [(a picture, its record, its
    pHash)] — and nhentai, in the shape of that function's answer ({"values", "files", "more"}): the gallery found
    (the one SauceNAO's index pointed at, the one found before, then the ones a search of its names gives — through
    the program's gates, :func:`search`, :func:`fetch_gallery`) and each picture compared with its pages
    (:func:`page_of`): a page alike — confirmed, the gallery is a manga to be downloaded into «Манга»; a gallery of
    that name whose pages are others, or none — not; nhentai not answering — it waits.  A gallery confirmed already
    — its pages alone, no search."""
    from core import manga_download as D, manhwa_sources as S, parser_manga as PM, parser_manga_pages as PP
    from core.parser_loose import hit_matches
    from core.manga_pictures import HENTAI_INDEXES
    limit = PM._limit(settings)
    now = int(time.time())
    name = str(group.get("name") or "")
    confirmed = number_of(group.get("url")) if group.get("state") == PM.MANGA else 0
    hits = {norm: next((h for h in rec.get("hits") or [] if isinstance(h, dict) and hit_matches(h, key, group)
                        and int(h.get("index") or 0) in HENTAI_INDEXES), {}) for norm, rec, _mark in probes}
    alike: dict = {}                    # a picture -> its page
    compared: set = set()               # the pictures compared with every page of a gallery
    state = {"cut": False, "late": "", "seen": None, "named": False, "more_pages": False}

    said_names = [name, *(str(n) for h in hits.values() for n in (h.get("whole"), *(h.get("names") or [])) if n)]

    def look(number: int, strict: bool, pointed: bool = False) -> "tuple | None":
        """The pictures against the gallery ``number``: (its number, its title) — it was given; None — not (gone, a
        gate closed it — nothing is said of that one; nhentai silent — ``state["late"]``).  ``pointed`` — the
        gallery SauceNAO's index pointed at (a hint): one that is not called as SauceNAO called it is not the one
        meant — not compared (a hint read wrong costs one request, not every page of a stranger)."""
        try:
            found = _kept(asker, number)["gallery"]
            if pointed and not called(found, said_names):
                return None
            for norm, _rec, mark in probes:
                if norm in alike:
                    continue
                page, whole = page_of(asker, number, mark, min(limit, PP.STRICT) if strict else limit,
                                      hint=int(hits[norm].get("page") or 0), deadline=deadline)
                if page:
                    alike[norm] = page
                elif whole:
                    compared.add(norm)
                else:
                    state["cut"] = True
        except Late as exc:
            state["late"] = str(exc)
            return None
        except D.MangaError as exc:
            _LOG.info("parser loose: nhentai %s not taken: %s", number, type(exc).__name__)
            return None
        state["more_pages"] = state["more_pages"] or found.pages > LOOSE_PAGES
        return int(number), found.title

    def files_after(done: bool) -> dict:
        out = {}
        for norm, rec, _mark in probes:
            if norm in alike:
                out[norm] = {"state": PM.MANGA, "how": "page", "page": f"стр. {alike[norm]}"}
            elif done and norm in compared:
                out[norm] = {"state": PM.NOT, "why": "не совпала ни с одной страницей галереи"}
        return out

    if confirmed:                                       # more pictures of a gallery confirmed already
        if look(confirmed, False) is None:
            return {"values": {"files_at": now + (PM.LATER if state["late"] else SEARCH_AGAIN)}, "files": {}}
        if state["cut"] and not alike:
            return {"more": True}
        return {"values": {}, "files": files_after(True), "more": state["cut"]}

    def manga(said: tuple) -> dict:
        number, title = said
        best = max((float(h.get("sim") or 0) for h in hits.values()), default=0.0)
        pages = sorted(alike.values())
        by = (f"SauceNAO ({best:.0f} %): «{name}» → галерея nhentai «{title}»: "
              + (f"страница совпала (стр. {pages[0]})" if len(pages) == 1 else
                 f"страницы совпали ({len(pages)} из {len(probes)})"))
        return {"values": {"state": PM.MANGA, "how": "site", "by": by, "url": link_of(number), "title": title,
                           "label": LABEL, "site": SITE, "key": str(number), "adult": True, "gallery": True,
                           "sim": round(best, 1), "later_at": 0, "tries": 0, "why": "", "searched_at": now},
                "files": files_after(True)}

    tried: set = set()
    hinted = [int(h["gallery"]) for h in hits.values() if int(h.get("gallery") or 0)]
    before = number_of(group.get("seen_url"))
    for number in dict.fromkeys([*hinted, *([before] if before else [])]):
        said = look(number, False, pointed=number != before)
        if said is None:
            continue                                    # (not given, not the one meant: a search may still give it)
        tried.add(number)
        state["seen"] = state["seen"] or said
        if alike:
            return manga(said)
    searched = False
    if not state["cut"] and not state["late"] and \
            (not before or now - int(group.get("searched_at") or 0) >= SEARCH_AGAIN):
        whole = next((str(h.get("whole") or "") for h in hits.values() if h.get("whole")), "")
        others = [str(n) for h in hits.values() for n in h.get("names") or []]
        asked = 0
        for wanted_name in dict.fromkeys([name, *others]):
            if asked >= NAMES or not wanted(wanted_name) or asker.stopped():
                continue
            asked += 1
            try:
                rows = _candidates(asker, wanted_name, whole if asked == 1 else "", 0)
            except Late as exc:
                state["late"] = str(exc)
                break
            except S.SourceError as exc:                # (it refused the search: this name says nothing)
                _LOG.info("parser loose: nhentai refused a search: %s", type(exc).__name__)
                continue
            searched = True
            for row in rows:
                if row["number"] in tried or asker.stopped():
                    continue
                tried.add(row["number"])
                said = look(row["number"], not row["exact"])
                if said is None:
                    continue
                if state["seen"] is None or (row["exact"] and not state.get("named")):
                    state["seen"], state["named"] = said, bool(row["exact"])      # (one of that very name — rather)
                if alike:
                    return manga(said)
            if state["late"] or state["cut"]:
                break
    seen = {}
    if state["seen"] is not None:
        seen = {"seen_url": link_of(state["seen"][0]), "seen_title": state["seen"][1], "seen_label": LABEL,
                "seen_adult": True, "seen_site": SITE, "seen_key": str(state["seen"][0])}
    if searched:
        seen["searched_at"] = now
    if state["cut"] or asker.stopped():                 # (the round's time ran out among a gallery's pages)
        return {"more": True, "values": seen, "files": {}}
    if state["late"]:
        tries = int(group.get("tries") or 0) + 1
        late = state["late"]
        return {"values": {"state": PM.WAIT, "how": "down", "tries": tries,
                           "by": late if late.lower().startswith(LABEL) else f"{LABEL} не ответил: {late}",
                           "later_at": now + (PM.LATER if tries <= TRIES else SEARCH_AGAIN), **seen}, "files": {}}
    if state["seen"] is None:
        by = "на nhentai галереи с таким названием нет"
    else:
        by = (f"галерея «{state['seen'][1]}» на nhentai есть, но "
              + ("картинка не совпала" if len(probes) == 1 else "картинки не совпали") + " ни с одной её страницей"
              + (f" (сверены первые {LOOSE_PAGES})" if state["more_pages"] else ""))
    return {"values": {"state": PM.NOT, "how": "nothing", "by": by, "later_at": 0, "tries": 0, **seen}, "files": {}}


def page_link(settings: dict, number: int) -> str:
    """The page in «Манга» of a gallery's folder ("" — it is not in the manga folder)."""
    from core import comic_manga as CM, manga_library as M
    root = M.manga_root(settings)
    folder = had_folder(Path(root), number) if root and Path(root).is_dir() else None
    try:
        return CM.page_of(settings, str(folder)) if folder is not None else ""
    except Exception as exc:  # noqa: BLE001 - the library busy: the row has no link this time
        _LOG.info("parser loose: the gallery's page not found: %s", type(exc).__name__)
        return ""


# ── the owner's link ────────────────────────────────────────────────────────────────────────────────────────────────
def by_link(asker, work: "MD.Work", listed: list, number: int) -> dict:
    """(``parser_manga_again``: «Указать ссылку») The gallery the owner's link names: {"title", "url", "alike",
    "local", "pages", "ok"} — it is there and no gate closes it (``ok`` — its pages are the folder's; his word
    confirms either way); {"refused": words} — no such gallery, a gate closed it; {"later": words} — nhentai did not
    answer."""
    from core import manga_download as D, parser_manga as PM
    local = _local(asker, work, listed)
    try:
        found = _kept(asker, number)["gallery"]
        got = _compare(asker, local, int(number), exact=True, limit=PM._limit(asker.settings)) if local else {}
    except Late as exc:
        return {"later": str(exc)}
    except D.MangaError as exc:
        return {"refused": str(exc)}
    return {"title": found.title, "url": link_of(number), "alike": int(got.get("alike") or 0),
            "local": int(got.get("local") or 0), "pages": found.pages, "ok": bool(got.get("ok"))}


# ── a confirmed folder's copy ───────────────────────────────────────────────────────────────────────────────────────
def had_folder(root: Path, number: int) -> "Path | None":
    """The folder of the manga folder that is this gallery already — by its name, as the program's downloader knows
    its own («nhentai-<number> …»: a gallery downloaded before, the copy of another folder of the same gallery);
    None — none."""
    mark = re.compile(rf"^nhentai[-_ ]*{int(number)}(?!\d)", re.I)
    try:
        for entry in sorted(Path(root).iterdir()):
            if entry.is_dir() and mark.match(entry.name):
                return entry
    except OSError as exc:
        _LOG.info("parser manga: the manga folder not read for gallery %s: %s", number, exc)
    return None


def copy_name(number: int, name: str) -> str:
    """The name of the copy of a folder nhentai confirmed: «nhentai-<number> - <its own name>» — the folder the
    program's downloader takes for that gallery (``manga_download.folder_for``)."""
    name = str(name or "").strip()
    if number_in_name(name) == int(number):
        return name
    return f"nhentai-{int(number)} - {name}" if name else f"nhentai-{int(number)}"


def _numbered(folder: Path) -> list:
    """[(the page's number, its file)] of a copy's pages named by their numbers («007.jpg»), in order."""
    out = []
    try:
        for path in folder.iterdir():
            if path.is_file() and path.suffix.lower() in MD.IMAGES and path.stem.isdigit():
                out.append((int(path.stem), path))
    except OSError:
        return []
    return sorted(out)


def aligned(net, folder: Path, found, limit: int) -> bool:
    """The copy's pages are numbered as the gallery's: its first numbered pages (:data:`ALIGNED` of them) and
    ((v5.44.170) its last ones — as many) are each alike the gallery's page of the same number — two at least, none of
    another page.  The last ones: a folder some pages were taken out of and the rest numbered anew (1…12 where the
    gallery's are 1…4, 9…16) begins as the gallery does — its end tells; its missing pages are then downloaded by
    themselves (:func:`fill`), and into such a folder the same pages would come twice."""
    from core import parser_manga as PM, parser_manga_pages as PP
    from core.tagger.hashing import phash_distance
    same = 0
    numbered = [(n, p) for n, p in _numbered(folder) if 1 <= n <= found.pages]
    for number, path in dict.fromkeys([*numbered[:ALIGNED], *numbered[-ALIGNED:]]):
        try:
            mine = PM._phash(path.read_bytes())
        except OSError:
            return False
        if not mine or PP.flat(mine):
            continue
        try:
            theirs = _page_mark(net, found, number)
        except Late:
            return False
        if not theirs:
            continue
        if phash_distance(mine, theirs) > limit:
            return False
        same += 1
    return same >= 2


def pages_of_copy(folder: Path) -> int:
    """How many pages a copy has (its pictures, the folders of its parts too)."""
    folder = Path(folder)
    try:
        return sum(1 for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in MD.IMAGES
                   and p.stem.lower() != "cover" and ".lb" not in p.relative_to(folder).parts)
    except OSError:
        return 0


def counts(folder: Path, total: int, fill: bool) -> dict:
    """{"have", "total", "missing"} of a copy against its gallery of ``total`` pages: ``fill`` (its pages are numbered
    as the gallery's) — as the downloader counts them (``manga_download.pages_there``: the pages it would not ask
    again), else by how many pictures there are."""
    from core import manga_download as D
    total = max(0, int(total))
    have = D.pages_there(Path(folder), total) if fill else min(total, pages_of_copy(folder))
    return {"have": have, "total": total, "missing": max(0, total - have)}


def pages_words(numbers: dict) -> str:
    """«все страницы на месте», «в папке 12 стр. из 16»."""
    return ("все страницы на месте" if not numbers.get("missing") else
            f"в папке {int(numbers.get('have') or 0)} стр. из {int(numbers.get('total') or 0)}")


def describe_copy(settings: dict, folder: Path, number: int, *, stop_check=None, was: "dict | None" = None) -> dict:
    """A copy in the manga folder described by the gallery that confirmed it: the gallery's description beside its
    pages (``meta.json``, as the downloader writes it — a ``meta.json`` of another gallery or of the owner's own is
    left as it is), how many of the gallery's pages it has — the values of its record: {"site": {"counts", "queued",
    "error", "gallery", "fill"}, "fill", "gallery"}.  ``was`` — what an earlier description kept of it (the record's
    ``gallery``).  The site not answering, a gate that closes the gallery now — said in ``site["error"]``."""
    from core import manga_download as D, manga_library as M, parser_manga as PM, manga_page_order as PO
    from core.source_protection import is_inside
    folder, number = Path(folder), int(number)
    out = {"site": {"counts": {}, "queued": 0, "error": "", "gallery": number, "fill": False,
                    "order": {"state": "pending"}}}
    root = M.manga_root(settings)
    source = str(settings.get("root") or "").strip()
    if not root or not is_inside(folder, root) or folder.resolve() == Path(root).resolve() or \
            source and is_inside(folder, source):
        out["site"]["error"] = "сверка разрешена только в копии внутри папки «Манга», вне исходной папки"
        out["site"]["order"] = {"state": "unmatched"}
        return out
    try:
        PO._folder(folder, number)
        PO._plain(folder / "meta.json")
        PO._plain(folder / "meta.json.part")
    except OSError as exc:
        out["site"]["error"] = str(exc)
        out["site"]["order"] = {"state": "unmatched"}
        return out
    if going(number):
        return out                                      # A downloader must finish before any page is renamed.
    net = quiet_net(stop_check)
    try:
        found = fetch_gallery(net, number)
    except Late as exc:
        out["site"]["error"] = str(exc)
        return out
    except D.MangaError as exc:
        out["site"]["error"] = str(exc)
        return out
    if stop_check and stop_check():
        return out
    wrote = bool((was or {}).get("meta")) and int((was or {}).get("number") or 0) == number
    meta = folder / "meta.json"
    try:
        old = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else None
    except (OSError, ValueError):
        old = {}
    mine = old is None or wrote or (isinstance(old, dict) and str(old.get("id") or "") == str(number))
    if mine:
        described = {key: value for key, value in found.data.items()
                     if not str(key).startswith("_") and value is not None}
        try:
            if stop_check and stop_check():
                return out
            D._write(meta, json.dumps(described, ensure_ascii=False, indent=1).encode("utf-8"))
            wrote = wrote or old is None
        except OSError as exc:
            out["site"]["error"] = f"описание галереи не записалось в папку: {exc}"
            return out
    try:
        order = PO.prepare(folder, number, found.pages, lambda page: _page_mark(net, found, page), PM._limit(settings),
                           stop_check=stop_check)
    except Late as exc:
        order = {"state": "pending", "words": str(exc)}
    except (OSError, ValueError) as exc:
        order = {"state": "unmatched", "words": str(exc)}
        out["site"]["error"] = str(exc)
    fill = order["state"] == "ready"
    numbers = counts(folder, found.pages, fill)
    out["site"].update(counts=numbers, fill=bool(fill and numbers["missing"]), order=order)
    out["gallery"] = {"number": number, "meta": wrote, "pages": found.pages, "title": found.title}
    words = pages_words(numbers)
    if not fill:
        words += " — " + order.get("words", "сверяю нумерацию страниц")
    out["fill"] = {"state": "done", "missing": numbers["missing"], "words": words}
    M.LIBRARY.invalidate()
    return out


def going(number: int) -> bool:
    """(v5.44.170) The gallery waits in the program's download of nhentai, or is being downloaded now."""
    from core import manga_download as D
    return D.going(int(number))


def fill(settings: dict, number: int) -> dict:
    """(v5.44.170) The pages of the gallery a copy misses, downloaded by the program's own downloader of nhentai
    (``webui/pages/manga_get.begin`` — the page's «Скачать»: it takes the folder named by the gallery's number and asks
    only the pages that are not there; the gallery is read through the program's gate once more).  Only a copy whose
    pages are numbered as the gallery's is filled (:func:`describe_copy`'s ``fill``) — in another the same pages
    would lie twice.  {"ok", "error"}."""
    try:
        from webui.pages import manga_get
        return manga_get.begin(settings, [int(number)], "Докачать страницы — ")
    except Exception as exc:  # noqa: BLE001 - the downloader's trouble: the row keeps its «Докачать»
        _LOG.warning("parser manga: the pages of gallery %s not begun: %s: %s", number, type(exc).__name__, exc)
        return {"ok": False, "error": "скачивание не началось"}


def fetch(settings: dict, number: int) -> dict:
    """(v5.44.170) The gallery a picture lying loose is a page of, downloaded into «Манга» by the program's own
    downloader of nhentai (``webui/pages/manga_get.begin``; the gallery is read through the program's gate once more).
    {"ok", "error"}."""
    try:
        from webui.pages import manga_get
        return manga_get.begin(settings, [int(number)])
    except Exception as exc:  # noqa: BLE001 - the downloader's trouble: the row keeps «Скачать сейчас»
        _LOG.warning("parser loose: gallery %s not begun: %s: %s", number, type(exc).__name__, exc)
        return {"ok": False, "error": "скачивание не началось"}


def forget_copy(folder: Path, was: "dict | None") -> None:
    """The description of a gallery this module wrote into a copy taken back (the copy is another title's now): its
    ``meta.json`` — only the one written here, of that gallery."""
    if not (was or {}).get("meta"):
        return
    meta = Path(folder) / "meta.json"
    try:
        data = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else None
        if isinstance(data, dict) and str(data.get("id") or "") == str(was.get("number") or ""):
            meta.unlink()
    except (OSError, ValueError) as exc:
        _LOG.warning("parser manga: the gallery's description not taken out of %s: %s", folder, exc)
