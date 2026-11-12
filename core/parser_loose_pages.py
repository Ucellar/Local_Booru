"""(v5.44.164) A loose picture and the pages of a site of manga — the comparing part of ``core/parser_loose.py`` (the
pages of manga lying loose among the parser's pictures): which chapter SauceNAO's answer names (:func:`chapter_of`),
every page of that chapter of a title as pHashes (:func:`pages_of` — kept an hour: a round cut short goes on), and one
manga's pictures against the sites (:func:`check`): the name searched (``manhwa_search.search_now`` at
``parser_manga``'s pace of the hour — its tiles past the program's gate), the titles of that very name compared page
by page (a site's ``title()`` ends with the gate too).  Nothing is kept here but that hour's pages: what was found is
``parser_loose``'s to keep.
"""
from __future__ import annotations

import logging
import re
import threading
import time

from core import parser_manga as PM

_LOG = logging.getLogger("local_booru.manga")

MANGA, NOT, WAIT = PM.MANGA, PM.NOT, PM.WAIT
PAGES = 80                      # pages of a chapter compared at most
CHAPTERS = 2                    # chapters of one number compared at most (a site that counts them tome by tome)
PAGES_KEPT = 3600               # a chapter's pages (their pHashes) are kept so long: a round cut short goes on
SEARCH_AGAIN = 24 * 3600        # a name the sites had no confirming title of is searched again so much later at most
TRIES = 3                       # the sites not answering: asked again every ``parser_manga.LATER`` so many times, then daily

_CHAPTER = re.compile(r"(?<![a-zа-яё])(?:chapter|chap|ch|c|глава|гл|episode|ep|эпизод)\.?\s*#?\s*"
                      r"(\d{1,4}(?:[.,]\d{1,2})?)(?!\d)", re.I)
_TOME = re.compile(r"(?<![a-zа-яё])(?:volume|vol|v|том|т)\.?\s*#?\s*(\d{1,3})(?!\d)", re.I)
_BARE = re.compile(r"\W*(\d{1,4}(?:[.,]\d{1,2})?)\W*")


def chapter_of(part: str) -> tuple:
    """(the tome, the chapter's number) SauceNAO's «part» names: «- Chapter 12» → (0, "12"), «Vol. 3 Ch. 12.5» → (3,
    "12.5"), «v03 c012» → (3, "12"), «12» → (0, "12"); (0, "") — it names no chapter."""
    from core import manhwa_sources as S
    text = str(part or "")
    tome = _TOME.search(text)
    found = _CHAPTER.search(text) or (None if tome else _BARE.fullmatch(text))
    return (int(tome.group(1)) if tome else 0), (S._num(found.group(1)) if found else "")


def sim_of(rec: dict, key: str = "") -> float:
    """How alike SauceNAO found a picture to its manga (``key`` — to the manga of that name; else to its best one)."""
    from core import manhwa_sources as S
    hits = [h for h in rec.get("hits") or [] if isinstance(h, dict)]
    if key:
        hits = [h for h in hits if S.name_key(h.get("name")) == key] or hits[:1]
    return max((float(h.get("sim") or 0) for h in hits), default=0.0)


def chapter_for(rec: dict, key: str) -> tuple:
    """(the tome, the number) of the chapter a picture is of — as the answer that named the manga ``key`` said (else as
    its best answer did)."""
    from core import manhwa_sources as S
    hits = [h for h in rec.get("hits") or [] if isinstance(h, dict)]
    mine = [h for h in hits if S.name_key(h.get("name")) == key] or hits[:1]
    for hit in mine:
        tome, number = chapter_of(hit.get("part"))
        if number:
            return tome, number
    return 0, ""


def flat(mark: str) -> bool:
    """A pHash that tells nothing (an empty page: alike every other empty page)."""
    try:
        bits = bin(int(str(mark), 16)).count("1")
    except ValueError:
        return True
    return bits < 8 or bits > 56


_KEPT: dict = {}                        # (a title's link, a chapter's id) -> {"at", "marks", "whole"}
_KEPT_LOCK = threading.Lock()


def title_of(asker, url: str) -> tuple:
    """(the site, the title, its free chapters) of a link — asked once a round; past the program's gate (the site's
    ``title`` ends with ``manhwa_sources.checked``).  ``manhwa_sources.SourceError`` — the site gave none."""
    from core import manhwa_sources as S
    kept = asker._titles.get((PM.LOOSE, url))
    if kept is None:
        source, key = S.source_for(url)
        if source is None:
            raise S.SourceError("не сайт манги программы")
        net = asker.net()
        title = source.title(net, key)
        kept = (source, title, [c for c in source.chapters(net, title) if c.free])
        asker._titles[(PM.LOOSE, url)] = kept
    return kept


def pages_of(asker, url: str, tome: int, number: str, deadline: float) -> tuple:
    """([(the chapter's label, a page's number, its pHash)] of the chapter ``number`` of the title at ``url`` —
    :data:`CHAPTERS` chapters of that number at most, the one of ``tome`` first; :data:`PAGES` pages of each —, whether
    every page of them was had (``deadline``, a stop: the rest next time — what was had is kept :data:`PAGES_KEPT`
    seconds), why there is none: «такой главы на сайте нет» …).  ``SourceError`` — the title itself was not given."""
    from core import manhwa_sources as S
    source, title, chapters = title_of(asker, url)
    wanted = [c for c in chapters if number and S._num(c.number) == S._num(number)]
    if not wanted:
        return [], True, "такой главы на сайте нет (или она платная)"
    wanted.sort(key=lambda c: (bool(tome) and c.tome != tome, c.index))
    net = asker.net()
    out, whole, why = [], True, ""
    for chapter in wanted[:CHAPTERS]:
        now = time.monotonic()
        with _KEPT_LOCK:
            for old in [k for k, v in _KEPT.items() if now - v["at"] > PAGES_KEPT]:
                _KEPT.pop(old, None)
            kept = _KEPT.get((url, chapter.id))
        if kept is None or not kept["whole"]:
            marks = list(kept["marks"]) if kept else []
            try:
                pictures = source.pictures(net, title, chapter)[:PAGES]
            except S.SourceError as exc:                # (a paid one, a sign-in: this chapter says nothing)
                from core.redaction import sanitize_text
                why = sanitize_text(str(exc))[:160]     # (the words go to the page)
                continue
            for picture in pictures[len(marks):]:
                if asker.stopped() or time.monotonic() > deadline:
                    break
                try:
                    data, _ctype = S.fetch_picture(net, picture, what=source.label,
                                                   headers={"Referer": source.referer(title),
                                                            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
                except S.SourceError as exc:
                    _LOG.info("parser loose: a page of %s not had: %s", url, exc)
                    data = b""
                marks.append(PM._phash(data))           # ("" — not a picture: its place is kept)
            kept = {"at": time.monotonic(), "marks": marks, "whole": len(marks) >= len(pictures)}
            with _KEPT_LOCK:
                _KEPT[(url, chapter.id)] = kept
        out += [(chapter.label, n, mark) for n, mark in enumerate(kept["marks"], 1) if mark and not flat(mark)]
        whole = whole and kept["whole"]
    return out, whole, why


def names_for(asker, group: dict, probes: list) -> list:
    """The names a manga is searched by on the sites: its own, the other names its pictures' answers gave, the names
    MangaDex has for the title of the chapter SauceNAO linked — ``parser_manga.NAMES`` at most."""
    from core import manga_clues as C
    names = [str(group.get("name") or "")]
    links = []
    for _norm, rec, _mark in probes:
        for hit in rec.get("hits") or []:
            names.append(str(hit.get("name") or ""))
            if hit.get("link"):
                links.append((str(hit["link"]), float(hit.get("sim") or 0)))
    if links:
        names += PM._dex_names(asker, sorted(links, key=lambda x: -x[1]))
    return [n for n in C.good_names(names) if not re.fullmatch(r"[\W\d_]+", n)][:PM.NAMES]


class Compare:
    """One manga's pictures compared with the sites' pages (:func:`check`)."""

    def __init__(self, settings: dict, asker, key: str, probes: list, deadline: float):
        self.asker, self.key, self.probes, self.deadline = asker, key, probes, deadline
        self.limit = PM._limit(settings)
        self.alike: dict = {}               # a picture -> «Глава 12, стр. 7»
        self.compared: set = set()          # the pictures whose chapter's pages were had (all of them)
        self.cut = False                    # a chapter's pages not all had yet (the round's time)
        self.down = 0                       # the sites that did not answer at all
        self.why = ""                       # why a picture could not be compared
        self.found: "tuple | None" = None   # (the link, the name, the site's label) of a title of that name
        self.found_said: dict = {}          # …what its site says of it: {"adult", "site", "key"}
        self.closed = False                 # a title the gate closed
        self.tried: set = set()             # the links compared already
        self.searched = False               # a search of the sites was answered
        self.paced = 0.0                    # seconds until the sites may be searched again (the hour's searches spent)

    def compare(self, url: str, named: str = "") -> "tuple | None":
        """The pictures against the pages of their chapters of the title at ``url`` (``named`` — the key of the name it
        was found by: the chapter is the one the answer of that name gave): (its name, its site's label, the site, the
        title's key); None — the title was not given (closed, gone, the site silent)."""
        from core import manhwa_sources as S
        self.tried.add(url)
        try:
            source, title, _chapters = title_of(self.asker, url)
            for norm, rec, mark in self.probes:
                if norm in self.alike:
                    continue
                tome, number = chapter_for(rec, named or self.key)
                if not number:
                    self.why = self.why or "SauceNAO не назвал главу"
                    continue
                pages, whole, why = pages_of(self.asker, url, tome, number, self.deadline)
                self.cut = self.cut or not whole
                self.why = why or self.why
                if whole and pages:
                    self.compared.add(norm)
                hit = next(((label, n) for label, n, page in pages if PM._alike(mark, page, self.limit)), None)
                if hit is not None:
                    self.alike[norm] = f"{hit[0]}, стр. {hit[1]}"
        except S.Excluded:
            self.closed = True
            return None
        except S.SourceError as exc:
            _LOG.info("parser loose: the title %s not had: %s", url, exc)
            if isinstance(exc, S.Silent) or " не ответил" in str(exc):
                self.down += 1
            return None
        return title.title, source.label, source.name, title.key

    def seen_values(self, group: dict) -> dict:
        """What is kept of the title of that name found (not confirmed): the next picture of the manga is compared
        with it without a search, «Это манга» downloads it."""
        if self.found is None:
            return {}
        said = self.found_said
        return {"seen_url": self.found[0], "seen_title": self.found[1], "seen_label": self.found[2],
                "seen_adult": bool(said["adult"] if "adult" in said else group.get("seen_adult")),
                "seen_site": said.get("site") or group.get("seen_site") or "",
                "seen_key": said.get("key") or group.get("seen_key") or ""}

    def files_after(self, confirmed: bool) -> dict:
        """What is kept of each picture compared: alike a page — a page of the manga; of a manga confirmed and
        SauceNAO sure of it — one too; compared with every page of its chapter and alike none — not one (it stays in
        Брак); its chapter's pages not all had yet — compared again."""
        out = {}
        for norm, rec, _mark in self.probes:
            if norm in self.alike:
                out[norm] = {"state": MANGA, "how": "page", "page": self.alike[norm]}
            elif confirmed and sim_of(rec, self.key) >= PM.SURE:
                out[norm] = {"state": MANGA, "how": "sure"}
            elif confirmed and norm in self.compared:
                out[norm] = {"state": NOT, "why": "не совпала ни с одной страницей главы"}
            elif confirmed and not self.cut:
                out[norm] = {"state": NOT, "why": "сверить не с чем: " + (self.why or "главы на сайте нет")}
        return out

    def manga(self, group: dict, url: str, said: tuple, *, how: str, adult: bool, sure: int) -> dict:
        """The values of a manga confirmed by the title ``said`` (:meth:`compare`'s answer) at ``url``."""
        name, label, site, tkey = said
        best = max((sim_of(rec, self.key) for _n, rec, _m in self.probes), default=0.0)
        if how == "sure":
            by = f"SauceNAO — {PM.plural(sure, PM.PICTURES)} ≥ {PM.SURE:.0f} % → тайтл «{name}» на {label}"
        else:
            pages = list(self.alike.values())
            by = (f"SauceNAO ({best:.0f} %): «{group.get('name')}» → тайтл «{name}» на {label}: "
                  + (f"страница совпала ({pages[0]})" if len(pages) == 1 else
                     f"страницы совпали ({len(pages)} из {len(self.probes)})"))
        return {"state": MANGA, "how": how, "by": by, "url": url, "title": name, "label": label, "site": site,
                "key": tkey, "adult": bool(adult), "sim": round(best, 1), "later_at": 0, "tries": 0, "why": ""}


def _of_confirmed(look: Compare, group: dict, now: int) -> dict:
    """More pictures of a manga confirmed already: its title's pages alone (no search)."""
    if look.compare(str(group["url"])) is None:         # (its title not given now: they wait, asked later)
        return {"values": {"files_at": now + (PM.LATER if look.down else SEARCH_AGAIN)}, "files": {}}
    if look.cut and not look.alike:
        return {"more": True}
    return {"values": {}, "files": look.files_after(True), "more": look.cut}


def _by_known(look: Compare, group: dict, sure: int) -> "dict | None":
    """The title of that name found before (not confirmed then): its pages first, without a search — the verdict
    when a page is alike now, else None."""
    known = str(group.get("seen_url") or "")
    said = look.compare(known) if known else None
    if said is None:
        return None
    look.found = (known, said[0], said[1])
    if not look.alike:
        return None
    return {"values": look.manga(group, known, said, how="site", adult=bool(group.get("seen_adult")), sure=sure),
            "files": look.files_after(True)}


def _by_search(look: Compare, group: dict, sure: int, now: int) -> "dict | None":
    """The manga's names searched on the sites (``parser_manga``'s pace of the hour), the pictures compared with the
    pages of the titles of each name (``parser_manga.TILES`` at most): the verdict when a page is alike; None — none
    (what was learned is in ``look``: a title found, the sites silent, the hour's searches spent)."""
    from core import manga_archive as A, manhwa_search as MS, manhwa_sources as S
    for name in names_for(look.asker, group, look.probes):
        if look.asker.stopped():
            return {"more": True}
        look.paced = PM.search_wait()
        if look.paced:
            return None
        PM._search_taken()
        try:
            tiles, missed = MS.search_now(name, look.asker.stop_check, net=look.asker.net())
        except Exception as exc:  # noqa: BLE001 - the sites' trouble: this name says nothing now
            _LOG.info("parser loose: the sites not asked about %r: %s", name, exc)
            look.down += 1
            continue
        if not tiles and missed and len(missed) >= len(MS.sources()):
            look.down += 1
            continue
        look.searched = True
        mine = S.names_of(name)
        named = [t for t in tiles if mine & S.names_of(*t.names)]
        named.sort(key=lambda t: A._tile_kind(t) not in ("", "manga"))
        for tile in named[:PM.TILES]:
            url = A._best_url(tile)
            if not url or url in look.tried:
                continue
            said = look.compare(url, S.name_key(name))
            if said is None:
                continue
            adult = bool(tile.adult) or bool(getattr(S.by_name(said[2]), "adult_only", False))
            if look.found is None:
                look.found = (url, said[0], said[1])
                look.found_said = {"adult": adult, "site": said[2], "key": said[3]}
            if look.alike:
                return {"values": {**look.manga(group, url, said, how="site", adult=adult, sure=sure),
                                   "searched_at": now}, "files": look.files_after(True)}
    return None


def _unconfirmed(look: Compare, group: dict, sure: int, now: int) -> dict:
    """No page was alike: the round's time ran out among a chapter's pages — it goes on next round; two pictures
    SauceNAO is sure of and a title of that very name — a manga all the same (as two pages of a folder are); the
    hour's searches spent, the sites silent — it waits; else not a manga (the title found is kept)."""
    from core import manhwa_sources as S
    seen = look.seen_values(group)
    if look.cut:                                        # (the title found is kept; the search is not «done»)
        return {"more": True, "values": seen, "files": {}}
    if look.searched:
        seen["searched_at"] = now
    if look.found is not None and sure >= 2:
        source, tkey = S.source_for(look.found[0])
        said = (look.found[1], look.found[2], source.name if source else "", tkey)
        values = look.manga(group, look.found[0], said, how="sure", adult=bool(seen.get("seen_adult")), sure=sure)
        return {"values": {**values, **seen}, "files": {norm: {"state": MANGA, "how": "sure"}
                                                        for norm, rec, _m in look.probes
                                                        if sim_of(rec, look.key) >= PM.SURE}}
    if look.paced and look.found is None:
        return {"values": {"state": WAIT, "how": "site", "later_at": int(time.time() + max(60.0, look.paced)),
                           "by": f"сайты манги — не больше {PM.SEARCHES_PER_HOUR} поисков в час, очередь", **seen},
                "files": {}, "wait": look.paced}
    if look.down and look.found is None and not look.closed:
        tries = int(group.get("tries") or 0) + 1
        return {"values": {"state": WAIT, "how": "down", "tries": tries, "by": "сайты манги не ответили",
                           "later_at": int(time.time()) + (PM.LATER if tries <= TRIES else SEARCH_AGAIN), **seen},
                "files": {}}
    if look.found is None:
        by = "на сайтах манги программы такого тайтла нет"
    elif look.compared:
        by = (f"тайтл «{look.found[1]}» на {look.found[2]} есть, но "
              + ("картинка не совпала" if len(look.probes) == 1 else "картинки не совпали")
              + " ни с одной страницей главы")
    else:
        by = (f"тайтл «{look.found[1]}» на {look.found[2]} есть, но сверить не с чем: "
              + (look.why or "главы на сайте нет"))
    return {"values": {"state": NOT, "how": "nothing", "by": by, "later_at": 0, "tries": 0, **seen}, "files": {}}


def check(settings: dict, asker, key: str, group: dict, probes: list, sure: int, deadline: float) -> dict:
    """One manga's pictures (``probes`` — [(a picture, its record, its pHash)]; ``sure`` — how many of all its
    pictures SauceNAO is sure of) and the sites of manga: {"values": the manga's new values, "files": {a picture: its
    new values}} — confirmed, not, waiting; {"more": True} — the round's time ran out among a chapter's pages (what was
    had is kept: it goes on next round).  Nothing is asked twice: a manga confirmed already — its title's pages
    alone; a title of that name found before — its pages first, and the sites are searched again only
    :data:`SEARCH_AGAIN` seconds after the last search."""
    look = Compare(settings, asker, key, probes, deadline)
    now = int(time.time())
    if group.get("state") == MANGA and group.get("url"):
        return _of_confirmed(look, group, now)
    got = _by_known(look, group, sure)
    if got is None and (not group.get("seen_url") or now - int(group.get("searched_at") or 0) >= SEARCH_AGAIN):
        got = _by_search(look, group, sure, now)
    return got if got is not None else _unconfirmed(look, group, sure, now)
