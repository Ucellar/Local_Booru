"""(v5.44.166) «Манга через парсер»: a folder's pages against the pages of a title of a site of manga — the names a
folder is searched by, which of its pages are compared with which pages of the title, and what «the same» is.

The part of ``core/parser_manga.py`` that answers «is this folder that title» (its steps «files» — a link — and
«names»), taken out of it (the file grew past two thousand lines) and widened.  The owner (12.11.2026): «бывает по
названию у меня папки и которые не нашло по картинке попробует по названию и пару картинок сверяет друг с другом 90%+
совпадение это она и ищет так же другие главы если они есть».  The name was searched since 5.44.125, before SauceNAO —
and missed where the folder was not quite what the site has:

* **the name** was searched as the folder is called: «[Group] Title (2019) [RUS]» finds nothing.  Now made clean first
  (``manga_archive.clean_name`` — as «Из архива» searches), :func:`names_of`;
* **the titles** compared were only those called by the very name searched: «Title: Subtitle» of the site was never
  looked at.  Now the first titles the sites give for the name are compared too (:data:`LOOSE_TILES`) — there the
  pages decide alone: two of them alike at 90 % and more (:data:`STRICT`); a name alike never confirms;
* **the pages**: two of the folder against the first three of the chapter — a cover or the translators' page first on
  either side, and nothing was alike.  Now the folder's first four (``parser_manga.LOCAL_PAGES``; pages that tell
  nothing — an empty one — passed over) against the chapter's first :data:`SITE_FIRST`, and :data:`SITE_MORE` more
  of them when that is
  worth asking (the very name; one page alike already);
* **the chapter**: a part's number was read only from «Глава 7» — «Chapter 5», «Том 1 Глава 3», «c012» were compared
  with the title's first chapter.  Now by ``parser_loose_pages.chapter_of`` (:func:`chapter_of`);
* **«не манга»** said «ни тайтла на сайтах манги, ни ответа SauceNAO» whatever happened.  Now it says what the name
  gave (:func:`not_words`): nothing found — or a title found whose pages are not the folder's.

What confirms (as before, nothing looser for the very name): a title called by the very name searched — one page
alike (the parser's own «how alike», ``parser_visual_max_distance``); any other title — two pages alike at 90 %; each
page of the folder with a page of its own of the title.  The chapters the folder has not are fetched as ever — by the
copy's link (``parser_manga.link_folder`` → «Загрузки»).

``parser_manga`` is imported inside the functions (it imports this module).
"""
from __future__ import annotations

import logging
import re

from core import manga_detect as MD

_LOG = logging.getLogger("local_booru.manga")

LOCAL_LOOKED = 8                # the folder's pages compared (``parser_manga.LOCAL_PAGES`` — four) are the first that
#                                 tell something among so many first pages (an empty page is passed over)
SITE_FIRST = 3                  # pages of the title's chapter asked at first
SITE_MORE = 3                   # …and more of them when it is worth it (the very name; a page alike already)
LOOSE_TILES = 2                 # titles of a name's search not called by that name whose pages are compared
STRICT = 6                      # of 64 bits: «90 %+» — how alike the pages of a title of another name must be


def flat(mark: str) -> bool:
    """A pHash that tells nothing (an empty page is alike every other empty page)."""
    from core.parser_loose_pages import flat as said
    return said(mark)


# ── the folder's side ───────────────────────────────────────────────────────────────────────────────────────────────
def local(work: "MD.Work", listed: list) -> list:
    """[(part index, page number, path)] of the folder's pages compared and searched: the first of its first part with
    pages (:data:`LOCAL_LOOKED` at most — :func:`local_marks` keeps ``parser_manga.LOCAL_PAGES`` of them)."""
    for index, pages in enumerate(listed):
        if pages:
            return [(index, n, p) for n, p in pages[:LOCAL_LOOKED]]
    return []


def local_marks(work: "MD.Work", listed: list) -> list:
    """[(part index, pHash)] of the folder's pages compared with a title's: the first ``parser_manga.LOCAL_PAGES``
    that tell something (an empty page, one Pillow does not read — passed over)."""
    from core import parser_manga as PM
    out = []
    for index, _number, path in local(work, listed):
        mark = PM._phash(PM._bytes(work.parts[index], path))
        if mark and not flat(mark):
            out.append((index, mark))
            if len(out) >= PM.LOCAL_PAGES:
                break
    return out


def chapter_of(work: "MD.Work", index: int) -> str:
    """The number of the site's chapter a part is: its folder's number («Сильвер Соул 7» → «7» — its label «Глава 7»),
    (v5.44.166) a chapter folder's own («Chapter 5», «Том 1 Глава 3», «c012» — ``parser_loose_pages.chapter_of``); a
    manga of one folder named with a number at its end — that number; "" — the title's first chapter."""
    from core.parser_loose_pages import chapter_of as named
    part = work.parts[index] if 0 <= index < len(work.parts) else None
    if part is not None and part.label.startswith(MD.PARTS_WORD + " "):
        return part.label.split(" ", 1)[1].strip()
    if part is not None and part.label and not part.archive and len(work.parts) > 1:
        _tome, number = named(part.label)
        if number:
            return number
    from core.manga_archive import plain_name
    _base, number = MD.tail_number(plain_name(work.name))      # (v5.44.169) «Название 7 18+» — 7, «Название +18» — none
    return str(number) if number is not None and len(work.parts) == 1 else ""


def names_of(work: "MD.Work", extra=()) -> list:
    """The names a manga is searched by on the sites: its own (its folder's, without «М4.» and a part's number; with a
    number at its end — without it too), (v5.44.166) each made clean first («[Group] Title (2019) [RUS]» → «Title»; a
    name with a group in brackets is not searched as it stands), and ``extra`` (ComicInfo's «Series», e621's pools,
    SauceNAO's answers) — never a name that names nothing («Новая папка», «123», «1»); ``parser_manga.NAMES`` at
    most.  (v5.44.169) Never with what no site calls a title by: «_» for a space, «18+», «+18», «R-18», «NSFW»
    (``manga_archive.plain_name``; the owner: «даже если название 100% правильное оно не находит просто из за _ или
    18+») — «Название_18+» is searched as «Название», and its number at the end is read without them."""
    from core import manga_archive as A, manga_clues as C, parser_manga as PM
    own = [work.name]
    base, number = MD.tail_number(A.plain_name(work.name))
    if number is not None:
        own.append(base)
    names = []
    for name in own:
        clean = A.clean_name(name)
        if clean:
            names.append(clean)
        if not A._BRACKETS.search(str(name or "")):
            names.append(A.plain_name(name))
    return [n for n in C.good_names([*names, *extra]) if not re.fullmatch(r"[\W\d_]+", n)][:PM.NAMES]


# ── the site's side ─────────────────────────────────────────────────────────────────────────────────────────────────
def site_marks(asker, url: str, number: str, count: int) -> dict:
    """The first ``count`` pages of the chapter ``number`` of the title at ``url`` (its first free chapter when it has
    no such one) — {"marks": their pHashes (the ones that tell something), "asked": pages asked so far, "pages": the
    chapter's pages, "name": the title's, "label": the site's, "first": the chapter's number}; kept for the round, the
    pages asked one by one as more are wanted.  ``manhwa_sources.SourceError`` — the site gave none (a paid chapter, a
    sign-in, no answer)."""
    from core import manhwa_sources as S, parser_manga as PM
    kept = asker._titles.get((url, number))
    if kept is None:
        source, key = S.source_for(url)
        if source is None:
            raise S.SourceError("не сайт манги программы")
        net = asker.net()
        title = source.title(net, key)
        chapters = [c for c in source.chapters(net, title) if c.free]
        chosen = next((c for c in chapters if number and S._num(c.number) == S._num(number)), None)
        if chosen is None and chapters:
            chosen = min(chapters, key=lambda c: (c.index, S._number_key(c.number)))
        if chosen is None:
            raise S.SourceError("у тайтла нет бесплатных глав")
        pictures = source.pictures(net, title, chosen)      # (a paid one — ``Paid``, a SourceError: none)
        kept = {"source": source, "title": title, "pictures": list(pictures), "marks": [], "asked": 0,
                "pages": len(pictures), "name": title.title, "label": source.label, "first": S._num(chosen.number)}
        asker._titles[(url, number)] = kept
    source, title = kept["source"], kept["title"]
    while kept["asked"] < min(int(count), len(kept["pictures"])) and not asker.stopped():
        pic = kept["pictures"][kept["asked"]]
        kept["asked"] += 1
        data, _ctype = S.fetch_picture(asker.net(), pic, what=source.label,
                                       headers={"Referer": source.referer(title),
                                                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
        mark = PM._phash(data)
        if mark and not flat(mark):
            kept["marks"].append(mark)
    return kept


def alike_pages(marks: list, hashes: list, limit: int) -> int:
    """How many of the folder's pages (``marks`` — [(part, pHash)]) have a page of their own among the title's
    ``hashes`` (each page of the title for one page of the folder: two copies of one page are one page alike)."""
    from core import parser_manga as PM
    free = list(hashes)
    count = 0
    for _index, mark in marks:
        hit = next((h for h in free if PM._alike(mark, h, limit)), None)
        if hit is not None:
            free.remove(hit)
            count += 1
    return count


def _compare(asker, marks: list, url: str, chapter: str, *, exact: bool, named: bool, limit: int) -> dict:
    """The folder's pages against the title at ``url``: the part's own chapter, then the title's first — {"alike",
    "local", "site", "name", "label", "first", "ok"}; {"error": words, "silent": the site did not answer} — its pages
    not had.  A title of the very name is asked for more pages when none is alike yet; any title — when one page is
    alike and one more would decide."""
    from core import manhwa_sources as S
    out: dict = {}
    for wanted in ([chapter, ""] if chapter else [""]):
        try:
            got = site_marks(asker, url, wanted, SITE_FIRST)
            alike = alike_pages(marks, got["marks"], limit)
            if (alike == 1 and not exact) or (alike == 0 and named):
                got = site_marks(asker, url, wanted, SITE_FIRST + SITE_MORE)
                alike = alike_pages(marks, got["marks"], limit)
        except S.SourceError as exc:
            _LOG.info("parser manga: the pages of %s not had: %s", url, exc)
            return {"error": str(exc), "silent": S.transient(exc)}
        out = {"alike": alike, "local": len(marks), "site": int(got["asked"]), "name": got["name"],
               "label": got["label"], "first": got["first"], "ok": alike >= 2 or (alike >= 1 and exact)}
        if out["ok"] or not wanted or got["first"] != S._num(wanted):
            break                                       # (its first chapter was the one compared)
    return out


def by_names(asker, work: "MD.Work", listed: list, names: list, *, how: str = "site", said: str = "",
             searched: "set | None" = None, seen: "list | None" = None, late: "dict | None" = None,
             wide: bool = False) -> tuple:
    """2. ``names`` searched on the sites of manga, the pages of the titles each gives compared with the folder's
    (pHash): the titles called by the very name (``parser_manga.TILES`` at most) — two pages alike, or one and the very
    name; (v5.44.166) then the first titles of the search called otherwise (:data:`LOOSE_TILES`) — two pages alike at
    90 % (:data:`STRICT`).  (the ``Verdict``, its link, its name); none — (None, {a name's key: (the link of a title of
    that name found, its name)}).  ``parser_manga.Pace`` — the hour's searches spent.  ``searched`` — the keys of the
    names searched already (nothing found): not searched again (a check gone on after the hour's pace), the ones
    searched now added.  ``seen`` — what each name gave, for the words of «не манга» (:func:`not_words`): [{"name",
    "titles": [{"title", "label", "alike", "local", "site", "first", "error", "loose"}]}].

    (v5.44.169; the owner: «ищет оно походу по названию либо через раз либо не на всех сайтах») ``late`` — {a name's
    key: {"name", "sites": the sites that said nothing about it — silent, busy, slower than the search waits}}: a
    search some sites did not answer was a search done, and the folder «не манга» though the site that has it was
    never heard.  Now the name is done for the sites that answered, the others are kept here, and the next check asks
    them alone (``parser_manga.check_work`` makes the folder wait for it).  A name of one word with dashes that found
    nothing is asked with spaces too (``manga_archive.spellings``).

    (v5.44.170) ``wide`` — the owner's own «Перепроверить» of this one folder: the sites whose search lets in the
    window's browser alone are asked too (``manhwa_search.sources_wide``: com-x.life, Nude-Moon, MangaDex) — never by
    the background's own looks."""
    from core import manga_archive as A, manhwa_search as MS, manhwa_sources as S, parser_manga as PM
    from core.parser_manga_nhentai import SITE as NHENTAI, late_add, late_drop
    marks = local_marks(work, listed)
    found: dict = {}
    if not marks:
        return None, found
    limit = PM._limit(asker.settings)
    chapter = chapter_of(work, marks[0][0])
    for name in names:
        if asker.stopped():
            break
        key = S.name_key(name)
        again = [site for site in ((late or {}).get(key) or {}).get("sites", []) if site != NHENTAI]
        done = searched is not None and key in searched
        if done and not again:
            continue
        # a name done but for the sites that said nothing: they alone are asked; a new name — every site
        every = [*MS.sources(), *(MS.sources_wide() if wide else [])]
        sites = [s for s in every if s.name in again] if done else every
        if not sites:
            late_drop(late, key, again)                 # (a site of before the program asks no more)
            continue
        down = asker.down
        tiles, missed, quiet, failed = [], {}, [], False
        for query in A.spellings(name) or [name]:
            wait = PM.search_wait()
            if wait:
                raise PM.Pace(wait)
            PM._search_taken(0.25 if done and 3 * len(sites) <= len(MS.sources()) else 1.0)
            quiet = []
            try:
                tiles, missed = MS.search_now(query, asker.stop_check, net=asker.net(), asked=sites, quiet=quiet)
            except Exception as exc:  # noqa: BLE001 - the sites' trouble: this name says nothing now
                _LOG.info("parser manga: the sites not asked about %r: %s", name, exc)
                failed = True
                break
            if tiles or asker.stopped() or len(missed) >= len(sites):
                break                                   # (found; or nobody answered: another spelling asks nobody too)
        if failed or (not done and not tiles and missed and len(missed) >= len(sites)):
            asker.down += 1                         # no site of manga answered: the name said nothing now
            continue
        late_drop(late, key, [s.name for s in sites])
        if wide:                                    # (v5.44.171) a site of the window's browser that was slow is not
            asked_wide = {s.name for s in MS.sources_wide()}    # waited for: its check takes its time, and the
            quiet = [n for n in quiet if n not in asked_wide]   # folder's SauceNAO would wait ten minutes behind it
        late_add(late, key, name, quiet)            # (the ones that said nothing: asked again by the next check)
        note = {"name": name, "titles": []}
        if seen is not None:
            seen.append(note)
        mine = S.names_of(name)
        named = [t for t in tiles if mine & S.names_of(*t.names)]
        named.sort(key=lambda t: (A._tile_kind(t) not in ("", "manga"), key not in {S.name_key(n) for n in t.names}))
        loose = [t for t in tiles if not (mine & S.names_of(*t.names))][:LOOSE_TILES]
        for tile, is_named in [*((t, True) for t in named[:PM.TILES]), *((t, False) for t in loose)]:
            if asker.stopped():
                break
            url = A._best_url(tile)
            if not url:
                continue
            exact = is_named and key in {S.name_key(n) for n in tile.names}
            got = _compare(asker, marks, url, chapter, exact=exact, named=is_named,
                           limit=limit if is_named else min(limit, STRICT))
            shown = tile.names[0] if tile.names else name
            if got.get("error"):
                if got.get("silent"):
                    asker.down += 1                 # the site did not answer at all: not «not alike»
                if is_named:
                    found.setdefault(key, (url, shown))
                note["titles"].append({"title": shown, "label": "", "error": got["error"], "loose": not is_named})
                continue
            if is_named:
                found.setdefault(key, (url, got["name"] or name))
            if got["ok"]:
                where = f" на {got['label']}" if got["label"] else ""
                counted = f"({got['alike']} из {got['local']})"
                by = (f"{said}«{name}» → тайтл «{got['name']}»{where}: страницы совпали {counted}" if is_named else
                      f"{said}«{name}» → тайтл «{got['name']}»{where} — название другое, но страницы те же "
                      f"{counted}, совпадение от 90 %")
                return PM.Verdict(PM.MANGA, how, by, title=got["name"], url=url, first=got["first"]), found
            note["titles"].append({"title": got["name"] or shown, "label": got["label"], "alike": got["alike"],
                                   "local": got["local"], "site": got["site"], "first": got["first"],
                                   "loose": not is_named})
        if searched is not None and asker.down == down and not asker.stopped():
            searched.add(key)                           # (the sites answered: this name is done)
    return None, found


def by_link(asker, work: "MD.Work", listed: list, url: str, *, said: str):
    """(v5.44.127) A link of a title of a site of manga in the folder's files — «Из архива»'s ``.lb/source.json`` (the
    versions 5.44.84–5.44.124 wrote it into the parser's folder by the old guesses), a description's, ComicInfo's «Web»,
    a shortcut — confirms only when that title's pages are the folder's (one page alike: the link names the title, as
    the very name does in :func:`by_names`).  A link alone never (the owner: «если оно как то записалось что это манга и
    оно не будет парсить это пиздец»).  None — not confirmed (the names, SauceNAO go on; no site answering — counted in
    ``asker.down``: it waits)."""
    from core import parser_manga as PM, parser_gallery_sources as PG
    if PG.public_link(url):
        result, _found, _names = PG.by_hits(asker, work, listed, [{"link": url}], said=said)
        return result
    marks = local_marks(work, listed)
    if not marks:
        return None
    got = _compare(asker, marks, url, chapter_of(work, marks[0][0]), exact=True, named=True,
                   limit=PM._limit(asker.settings))
    if got.get("error"):
        if got.get("silent"):
            asker.down += 1
        return None
    if not got["ok"]:
        return None
    return PM.Verdict(PM.MANGA, "link", f"{said} → тайтл «{got['name']}»" + (f" на {got['label']}" if got["label"] else "")
                      + f": страницы совпали ({got['alike']} из {got['local']})", title=got["name"], url=url,
                      first=got["first"])


# ── the words of «не манга» ─────────────────────────────────────────────────────────────────────────────────────────
def late_words(late: "dict | None") -> str:
    """(v5.44.169) The sites that said nothing about the folder's names, by their names («Remanga, Desu»); "" — every
    site asked answered."""
    from core import manhwa_sources as S
    from core.parser_manga_nhentai import LABEL, SITE
    names = sorted({site for entry in (late or {}).values() if isinstance(entry, dict)
                    for site in entry.get("sites") or []})
    labels = []
    for name in names:
        source = S.by_name(name)
        labels.append(LABEL if name == SITE else source.label if source is not None else name)
    return ", ".join(dict.fromkeys(labels))


def not_words(seen: "list | None", late: "dict | None" = None) -> str:
    """What the names gave, in words («не манга» says why): nothing to search by, nothing found, a title found whose
    pages could not be had, or a title found whose pages are not the folder's — the one nearest to «yes» named.
    (v5.44.169) ``late`` — the sites that did not answer about a name to the end (:func:`late_words`): said too."""
    quiet = late_words(late)
    tail = f"; не ответили: {quiet} — «Перепроверить» спросит их снова" if quiet else ""
    seen = [n for n in seen or [] if isinstance(n, dict) and n.get("name")]
    if not seen:
        return "по названию папки искать нечего (оно ничего не называет)" + tail
    titles = [(note["name"], t) for note in seen for t in note.get("titles") or []]
    if not titles:
        names = ", ".join(f"«{name}»" for name in list(dict.fromkeys(n["name"] for n in seen))[:3])
        return f"по названию {names} на сайтах манги ничего не нашлось" + tail
    compared = [(name, t) for name, t in titles if not t.get("error")]
    if not compared:
        name, title = titles[0]
        return (f"по названию «{name}» на сайтах манги есть «{title.get('title')}», но сверить не с чем: "
                f"{str(title.get('error'))[:120]}") + tail
    name, title = max(compared, key=lambda x: (int(x[1].get("alike") or 0), not x[1].get("loose")))
    where = f" ({title['label']})" if title.get("label") else ""
    chapter = " галереи" if title.get("gallery") else f" главы {title['first']}" if title.get("first") else " главы"
    return (f"по названию «{name}» на сайтах манги есть «{title.get('title')}»{where}, но страницы не те: сверено "
            f"страниц папки — {int(title.get('local') or 0)}, страниц{chapter} на сайте — "
            f"{int(title.get('site') or 0)}, совпало — {int(title.get('alike') or 0)}"
            + (f" (тайтлов сверено: {len(compared)})" if len(compared) > 1 else "")) + tail
