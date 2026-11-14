"""SauceNAO gallery links and the public E-Hentai fallback, always confirmed by actual pages.

nhentai keeps its specialised indexed-page matcher. This module handles a source link before that
search and the public gallery site's names afterwards. A source's title/pictures methods enforce gates.
"""
from __future__ import annotations

import time
import threading

_MARKS: dict = {}
_HASH_RESULTS: dict = {}
_MARKS_LOCK = threading.Lock()
_KEEP_FOR = 3600
_KEPT = 40


def _page_marks(asker, url: str, hints: list, deadline: float, count: int, *, evidence: bool = False,
                full: bool = False) -> tuple:
    """Hinted pages and neighbours before the first ``count`` pages, with bounded resumable hashes.

    The boolean means this selection is complete, not that a gallery longer than ``count`` was scanned.
    Page hints only choose candidates; they never confirm an image or bypass the title's gates.
    """
    from core import manhwa_sources as S, parser_loose_pages as LP, parser_manga as PM
    from core.manhwa_sites.ehentai import COMIC_CATEGORIES, is_photo
    source, title, chapters = LP.title_of(asker, url)
    S.checked(title)
    photo = is_photo(title)
    if (title.type.casefold() not in COMIC_CATEGORIES or photo) and not (evidence and photo):
        raise S.Missing("Эта галерея E-Hentai не помечена как комикс")
    chapter = next((chapter for chapter in chapters if chapter.free and chapter.number == "1"), None)
    if chapter is None:
        raise S.Missing("У галереи нет открытых страниц")
    now = time.monotonic()
    with _MARKS_LOCK:
        for old in [link for link, saved in _MARKS.items() if now - saved["at"] > _KEEP_FOR]:
            del _MARKS[old]
        kept = _MARKS.setdefault(url, {"at": now, "marks": {}})
        kept["at"] = now
        while len(_MARKS) > _KEPT:
            del _MARKS[min(_MARKS, key=lambda link: _MARKS[link]["at"])]
    pictures = kept.get("pictures")
    if pictures is None:
        pictures = source.pictures(asker.net(), title, chapter)
        kept["pictures"] = pictures
    suggested = [n for hint in hints[:4] if isinstance(hint, int) and hint > 0
                 for n in (hint, hint - 1, hint + 1, hint + 2) if 1 <= n <= len(pictures)]
    selected = list(dict.fromkeys([*suggested[:12], *range(1, (len(pictures) if full or photo else
                                                             min(count, len(pictures))) + 1)]))
    asked = 0
    for number in selected:
        if number in kept["marks"]:
            continue
        if asker.stopped() or time.monotonic() >= deadline or (full or photo) and asked >= count:
            break
        data, _ctype = S.fetch_picture(asker.net(), pictures[number - 1], what=source.label,
                                     headers={"Referer": source.referer(title), "Accept": "image/*"})
        mark = PM._phash(data)
        if not mark:
            raise S.Silent("E-Hentai прислал нечитаемую картинку страницы")
        kept["marks"][number] = mark if not LP.flat(mark) else ""
        asked += 1
    rows = [(title.title, number, kept["marks"][number]) for number in selected if kept["marks"].get(number)]
    return rows, all(number in kept["marks"] for number in selected)


def public_link(url: str) -> str:
    """A canonical link of a supported public gallery, or an empty string."""
    from core import manhwa_sources as S
    source, key = S.source_for(str(url or ""))
    return source._url(key) if source is not None and source.name == "ehentai" and key else ""


def source_link(url: str) -> str:
    """A usable SauceNAO pointer to E-Hentai, either the gallery or its public hash search."""
    from core.manhwa_sites.ehentai import hash_link
    return public_link(url) or hash_link(str(url or ""))


def _hash_results(source, net, url: str) -> tuple:
    now = time.monotonic()
    with _MARKS_LOCK:
        kept = _HASH_RESULTS.get(url)
        if kept and now - kept[0] < _KEEP_FOR:
            return kept[1], False
    tiles = source.search_hash(net, url)
    links = [link for tile in tiles if (link := public_link(tile.url))]
    if getattr(tiles, "incomplete", False):
        return links, True
    with _MARKS_LOCK:
        _HASH_RESULTS[url] = (now, links)
        if len(_HASH_RESULTS) > _KEPT:
            del _HASH_RESULTS[min(_HASH_RESULTS, key=lambda link: _HASH_RESULTS[link][0])]
    return links, False


def names_of(hits: list) -> list:
    from core import nhentai as N
    return list(dict.fromkeys(N.pretty(str(name)) or str(name) for hit in hits
                             for name in [hit.get("name"), *(hit.get("names") or [])] if name))


def _candidates(asker, hits: list, *, search: bool, searched=None, late=None):
    """Direct links, then at most two paced public searches and six candidates per search."""
    from core import manhwa_sources as S, parser_manga as PM, parser_manga_nhentai as PN
    from core.manhwa_sites.ehentai import hash_link
    source = S.by_name("ehentai")
    if source is None:
        return
    seen = set()
    for hit in hits[:4]:
        url = public_link(hit.get("link"))
        if url and url not in seen:
            seen.add(url)
            yield url, True, float(hit.get("sim") or 0), hit, bool(hash_link(hit.get("hash_pointer") or ""))
    if not search:
        for hit in hits[:4]:
            pointer = hash_link(str(hit.get("link") or ""))
            if not pointer or pointer in seen or asker.stopped():
                continue
            seen.add(pointer)
            try:
                links, incomplete = _hash_results(source, asker.net(), pointer)
            except S.SourceError as exc:
                if S.transient(exc):
                    asker.down += 1
                continue
            if incomplete:
                asker.down += 1
            for url in links:
                if url not in seen:
                    seen.add(url)
                    yield url, True, float(hit.get("sim") or 0), {**hit, "hash_pointer": pointer}, True
    if not search:
        return
    for name in names_of(hits)[:2]:
        key = S.name_key(name)
        if asker.stopped() or (searched is not None and "eh:" + key in searched):
            continue
        wait = PM.search_wait()
        if wait:
            raise PM.Pace(wait)
        PM._search_taken()
        try:
            tiles = source.search(asker.net(), name)
        except S.SourceError as exc:
            if S.transient(exc):
                asker.down += 1
                PN.late_add(late, key, name, [source.name])
            continue
        PN.late_drop(late, key, [source.name])
        if getattr(tiles, "incomplete", False):
            asker.down += 1
            PN.late_add(late, key, name, [source.name])
        elif searched is not None and not asker.stopped():
            searched.add("eh:" + key)
        mine = S.names_of(name)
        tiles.sort(key=lambda tile: not bool(mine & S.names_of(*tile.names)))
        for tile in tiles[:6]:
            url = public_link(tile.url)
            if url and url not in seen:
                seen.add(url)
                yield url, bool(mine & S.names_of(*tile.names)), max(
                    (float(h.get("sim") or 0) for h in hits), default=0), None, False


def by_hits(asker, work, listed: list, hits: list, *, search: bool = False, searched=None,
            seen=None, late=None, said: str = "") -> tuple:
    """Folder match: (Verdict or None, found names, names). ``search=False`` checks source links only."""
    from core import manhwa_sources as S, parser_manga as PM, parser_manga_pages as PP, parser_loose_pages as LP
    from core.manhwa_sites.ehentai import is_photo
    names = names_of(hits)
    marks = PP.local_marks(work, listed)
    found = {}
    if not marks:
        return None, found, names
    started_down = asker.down
    pending = False
    deadline = time.monotonic() + 12
    for url, exact, similarity, clue, hashed in _candidates(asker, hits, search=search, searched=searched, late=late):
        if asker.stopped():
            break
        hints = [int(hit.get("page") or 0) for hit in hits if not hit.get("link") or public_link(hit.get("link")) == url]
        if clue and hashed:
            hints.insert(0, int(clue.get("page") or 0))
        # Original page numbers help when a partial folder begins in the middle of a gallery.
        hints += [number for _part, number, _path in PP.local(work, listed) if isinstance(number, int)]
        try:
            source, title_ref, _chapters = LP.title_of(asker, url)
            photo = is_photo(title_ref)
            options = {"evidence": True, "full": hashed} if clue and (photo or hashed) else {}
            pages, whole = _page_marks(asker, url, hints, deadline, PP.SITE_FIRST + PP.SITE_MORE, **options)
            limit = PM._limit(asker.settings) if exact and not (photo or hashed) else min(PM._limit(asker.settings), PP.STRICT)
            alike = PP.alike_pages(marks, [mark for _label, _n, mark in pages], limit)
            got = {"name": title_ref.title, "label": source.label, "alike": alike, "local": len(marks),
                   "site": len(pages), "ok": alike >= (1 if exact else 2)}
            if not whole and not got["ok"]:
                if options:
                    pending = True
                else:
                    asker.down += 1
        except S.SourceError as exc:
            got = {"error": str(exc), "silent": S.transient(exc)}
        if got.get("silent"):
            asker.down += 1
        title = str(got.get("name") or "")
        if exact and title:
            found.setdefault(S.name_key(title), (url, title))
        if seen is not None:
            seen.append({"name": names[0] if names else title, "titles": [{**got, "title": title,
                          "label": "E-Hentai", "gallery": True, "loose": not exact}]})
        if got.get("ok"):
            how = "link" if said else "saucenao"
            words = said or f"SauceNAO ({similarity:.0f} %)"
            by = (f"{words} → галерея E-Hentai «{title}»: страницы совпали "
                  f"({got['alike']} из {got['local']})")
            return PM.Verdict(PM.MANGA, how, by, sim=similarity, title=title, url=url, first="1",
                              eh_photo=photo), found, names
    if asker.stopped():
        return PM.Verdict(""), found, names
    if not search and (pending or asker.down > started_down):
        words = "E-Hentai: продолжаю сверку страниц галереи" if pending else "E-Hentai не ответил — проверю позже"
        return PM.Verdict(PM.WAIT, "site", words, later_at=int(time.time()) + (60 if pending else PM.LATER),
                          late={"eh_hits": hits[:4], **({"eh_said": said} if said else {})}), found, names
    return None, found, names


def check_loose(settings: dict, asker, key: str, group: dict, probes: list, deadline: float,
                *, search: bool = False):
    """A linked gallery first or the E-Hentai fallback after nhentai. None means no confirming result.

    A confirmed E-Hentai group checks only its own gallery. Never use SauceNAO confidence to approve
    another picture: even when the group is confirmed, each new page needs its own visual match.
    """
    from core import manhwa_sources as S, parser_loose as L, parser_loose_pages as LP, parser_manga as PM
    from core import parser_manga_pages as PP
    from core.manga_pictures import HENTAI_INDEXES
    from core.manhwa_sites.ehentai import is_photo
    if asker.stopped() or time.monotonic() >= deadline:
        return {"more": True, "files": {}}
    now = int(time.time())
    confirmed = public_link(group.get("url")) if group.get("state") == PM.MANGA else ""
    hits = [hit for _norm, rec, _mark in probes for hit in rec.get("hits") or []
            if isinstance(hit, dict) and hit.get("index") in HENTAI_INDEXES and L.hit_matches(hit, key, group)]
    before = public_link(group.get("seen_url"))
    if not hits:
        hits = [{"name": str(group.get("name") or "")}]
    if confirmed or before:
        hint = next((int(h.get("page") or 0) for h in hits if h.get("link") == group.get("seen_hash")), 0)
        hits.insert(0, {"name": str(group.get("name") or ""), "link": confirmed or before,
                        "hash_pointer": group.get("seen_hash"), "page": hint})
    if confirmed:
        candidates = [(confirmed, True, 0, hits[0], bool(group.get("seen_hash")))]
    else:
        search = search and (not before or now - int(group.get("eh_searched_at") or 0) >= LP.SEARCH_AGAIN)
        candidates = _candidates(asker, hits, search=search)
    last_seen, last_why = {}, ""
    down = asker.down
    try:
        pending = False
        for url, exact, _similarity, clue, hashed in candidates:
            if asker.stopped() or time.monotonic() >= deadline:
                return {"more": True, "values": last_seen, "files": {}}
            try:
                source, title, _chapters = LP.title_of(asker, url)
                S.checked(title)
                last_seen = {"seen_url": url, "seen_title": title.title, "seen_label": source.label,
                             "seen_hash": (clue or {}).get("hash_pointer") or "",
                             "seen_adult": True, "seen_site": source.name, "seen_key": title.key}
                hints = [int(hit.get("page") or 0) for hit in hits
                         if not hit.get("link") or public_link(hit.get("link")) == url]
                if clue and hashed:
                    hints.insert(0, int(clue.get("page") or 0))
                photo = is_photo(title)
                options = {"evidence": True, "full": hashed} if clue and (photo or hashed) else {}
                pages, whole = _page_marks(asker, url, hints, deadline, LP.PAGES, **options)
            except S.SourceError as exc:
                if asker.stopped() or "остановлено" in str(exc).casefold():
                    return {"more": True, "values": last_seen, "files": {}}
                if S.transient(exc):
                    asker.down += 1
                if isinstance(exc, (S.Excluded, S.Missing)):
                    last_seen = {}
                last_why = S.library_words(exc)
                continue
            last_why = ""
            limit = PM._limit(settings) if exact and not (photo or hashed) else min(PM._limit(settings), PP.STRICT)
            matched = {norm: next((n for _label, n, mark in pages if PM._alike(mine, mark, limit)), 0)
                       for norm, _rec, mine in probes}
            files = {norm: {"state": PM.MANGA, "how": "page", "page": f"стр. {page}"}
                     for norm, page in matched.items() if page}
            if files or confirmed and whole:
                same = len(files)
                if whole and pages:
                    files.update({norm: {"state": PM.NOT, "why": "не совпала со сверенными страницами галереи"}
                                  for norm, page in matched.items() if not page})
                values = {} if confirmed else {
                    "state": PM.MANGA, "how": "site", "url": url, "title": title.title, "label": source.label,
                    "site": source.name, "key": title.key, "adult": True, "gallery": True, "eh_photo": photo,
                    "by": f"галерея E-Hentai «{title.title}»: страницы совпали ({same} из {len(probes)})",
                    "later_at": 0, "tries": 0, "why": "", "searched_at": now, "seen_hash": last_seen["seen_hash"]}
                return {"values": values, "files": files, "more": not whole}
            if not whole:
                pending = True
                if not options:
                    return {"more": True, "values": last_seen, "files": {}}
        if pending:
            return {"more": True, "values": last_seen, "files": {}}
    except PM.Pace as exc:
        return {"values": {"state": PM.WAIT, "how": "site", "later_at": int(time.time() + max(60, exc.wait)),
                           "by": "E-Hentai: очередь к поиску", **last_seen}, "files": {}, "wait": exc.wait}
    if asker.stopped():
        return {"more": True, "values": last_seen, "files": {}}
    if asker.down > down:
        return {"values": {"state": PM.WAIT, "how": "down", "by": "E-Hentai не ответил",
                           "later_at": now + PM.LATER, **last_seen}, "files": {}}
    if confirmed:
        return {"values": {"files_at": now + LP.SEARCH_AGAIN}, "files": {}}
    if last_seen:
        return {"values": {"state": PM.WAIT if last_why else PM.NOT, "how": "down" if last_why else "nothing",
                           "later_at": now + LP.SEARCH_AGAIN if last_why else 0, "tries": 0,
                           "by": f"галерея E-Hentai «{last_seen['seen_title']}»: " +
                                 ("сверить не удалось — " + last_why if last_why else "страницы не совпали"),
                           "eh_searched_at": now, **last_seen}, "files": {}}
    return None
