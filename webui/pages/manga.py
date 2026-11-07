"""Manga pages, laid out like nhentai: a wall of covers, a title page with its tags and page
thumbnails, and a reader that turns pages by click or arrow keys.

(v5.44) The parts of one work («Title», «Title 2», «Title Part 3» — ``core/manga_works.py``) are one tile of the wall,
a сборка: its page (``/manga/s/<id>``) is the wall of its parts in their order.  A title page says the сборка it is in
and takes it out of it, or puts it with another title, by hand («Вся манга» → «Каждую отдельно» shows every title).

(v5.44.124; the plan of 7.11.2026 — «„Манга“ ПК как „Манхва“ и телефон») What «Манхва» of the computer has, in its
own blocks and words: «Продолжить чтение» over «Вся манга» (the titles being read, the last first — ``manhwa.
shelf_html``, ``card_html``, ``reading_now``), a title's «Продолжить: стр. 14» / «Продолжить: Глава 2, стр. 3» and
«Читать с начала» (:func:`where_read` — the phone's too), its chapters «✓ прочитана» / «Вы остановились здесь», and a
title downloaded from a site of manga — the block «Скачано с … · переводчики», «Новые главы — скачивать самой»,
«брать с других сайтов», «Докачать сейчас», the paid ones waiting (``manhwa.source_block``; its boxes —
``/api/manhwa/follow`` with ``kind: "manga"``).

(v5.44.126, «„Манга“ как „Манхва“ до конца») A series' page — «Прочитано N из M глав» with its bar
(``manhwa.progress_html``) and the chapters as «Манхва» shows them (``manhwa.chapter_row``, ``chapters_html``: the
newest first, «Сначала первые», «Вы остановились здесь · стр. 2 из 3», «с MangaLib · сегодня», «новая», the tick
«прочитана» by hand — ``/api/manhwa/read`` with ``kind: "manga"``); a chapter ticked counts as read for «Продолжить»."""
from __future__ import annotations

import re
import time
import zipfile
from pathlib import Path

from core import manga_library as M
from webui.html import esc, human_count, page_frame, paginator, rows_attrs, rows_per_page, url
from webui.server import Request, Response

PER_PAGE = 25             # about this many covers a page (as nhentai); the page is whole rows of its columns
ROWS_COOKIE = "lb_manga_pp"     # (v5.44.1) covers a page in this browser: whole rows (booru.js ``fitRows``)
COVER_SIZE = 500          # px, long side of a cover (shown at ~250 px)
THUMB_SIZE = 320          # px, long side of a page thumbnail on the title page
SORTS = {"newest": "Сначала новые", "title": "По названию", "pages": "Больше страниц"}
SHELF = 6                 # (v5.44.124) titles in «Продолжить чтение» — as «Вся манхва» (``manhwa.SHELF``)
ASSETS = ("manga", "manhwa")    # (v5.44.124) «Вся манга» and a title's page: the shelf and the block «Скачано с …»
#                                 of «Манхва» are drawn by its stylesheet, its script hears the block's boxes


def register(router) -> None:
    router.add("GET", r"/manga", index)
    router.add("GET", r"/manga/g/(?P<id>[0-9a-f]{12})", title_page)
    router.add("GET", r"/manga/r/(?P<id>[0-9a-f]{12})", reader)        # (v5.44.117) the one reader: ?p=N
    router.add("GET", r"/manga/r/(?P<id>[0-9a-f]{12})/(?P<n>\d+)", reader)   # the address of before
    router.add("GET", r"/api/manga/chapter/(?P<id>[0-9a-f]{12})", chapter_json)   # (v5.44.117) the strip's next one
    router.add("POST", r"/api/manga/mode", mode)                      # (v5.44.117) the way a title is read
    router.add("GET", r"/manga/cover/(?P<id>[0-9a-f]{12})", cover)
    router.add("GET", r"/manga/thumb/(?P<id>[0-9a-f]{12})/(?P<n>\d+)", page_thumb)
    router.add("GET", r"/manga/page/(?P<id>[0-9a-f]{12})/(?P<n>\d+)", page_image)
    router.add("POST", r"/api/manga/(?P<id>[0-9a-f]{12})/reveal", reveal)
    router.add("GET", r"/manga/random", random_title)
    router.add("GET", r"/manga/s/(?P<id>[0-9a-f]{12})", set_page)
    router.add("POST", r"/api/manga/(?P<id>[0-9a-f]{12})/set", set_change)
    router.add("GET", r"/manga/(?P<kind>tags|artists|characters|parodies)", tag_index)
    router.add("POST", r"/api/manga/progress", progress)


def _prefs(req: Request) -> dict:
    """Manga pages keep a look of their own (nhentai unless chosen otherwise — ``page_frame`` with active="manga")."""
    return {"thumbs": "m"}


TABS = (("all", "Вся манга", "/manga"), ("feed", "Лента", "/manga/feed"), ("tags", "Теги", "/manga/tags"),
        ("artists", "Артисты", "/manga/artists"), ("characters", "Персонажи", "/manga/characters"),
        ("parodies", "Пародии", "/manga/parodies"), ("random", "Случайная", "/manga/random"))
# (v5.44.90) «Лента» — the titles of the sites, with filters (webui/pages/catalog.py)
# (v5.44.89) no «Скачать» here: the owner — «убрать кнопку скачать которая в них находится оставив только вкладку
# "скачать"»; downloading is the menu's «Скачать» alone
# The lists of the manga folder's tags (``tag_index``): (title, groups of core.manga_library, «нет …»)
INDEX_TITLES = {"tags": ("Теги", ("general",), "тегов"), "artists": ("Артисты", ("artist",), "артистов"),
                "characters": ("Персонажи", ("character",), "персонажей"),
                "parodies": ("Пародии", ("parody", "copyright"), "пародий")}


def _frame(req: Request, title: str, body: str, *, kind: str = "manga", show: str = "", assets: tuple = ()) -> Response:
    """A page of the tab; ``show`` — which of its pages the section bar marks ("" — no bar: a title, the reader).
    (v5.44.75) «Скачать» leads to «Скачать → Манга» (``get_hub.py``) — in «SFW» too (its nhentai is not there then).
    ``assets`` — the stylesheets and scripts it takes instead of the tab's own (the reader's)."""
    search = _search_form(req.arg("q"), req.arg("sort", "newest"))
    tabs = tuple((label, href, key == show) for key, label, href in TABS) if show else ()
    return Response.html(page_frame(title, body, active="manga", subnav=False, kind=kind, top_search=search, tabs=tabs,
                                    assets=assets, **_prefs(req)))


def _search_form(query: str, sort: str) -> str:
    hidden = f'<input type="hidden" name="sort" value="{esc(sort)}">' if sort in SORTS and sort != "newest" else ""
    return (f'<form class="top-search" action="/manga" method="get" role="search"><div class="search-row">'
            f'<input type="search" name="q" value="{esc(query)}" placeholder="Название или теги" autocomplete="off" '
            f'spellcheck="false" aria-label="Поиск манги"><button type="submit" class="search-go">Найти</button></div>'
            f'{hidden}</form>')


def _library(req: Request, *, refresh: bool = False) -> tuple[str, list]:
    root = M.manga_root(req.settings)
    return root, (M.LIBRARY.items(root, refresh=refresh) if root else [])


def _get(req: Request) -> M.Manga | None:
    root = M.manga_root(req.settings)
    return M.LIBRARY.get(root, req.params["id"]) if root else None


# ── (v5.44.53) «SFW / Всё / NSFW»: which titles the mode shows (webui/content_mode.py, core/content_marks.py) ──
def _top(root: str, manga: M.Manga) -> M.Manga:
    """A chapter is judged as its series."""
    return (M.LIBRARY.get(root, manga.parent_id) or manga) if manga.parent_id else manga


def _verdict(manga: M.Manga, hand: dict | None = None):
    from core import content_marks as CM
    return CM.manga_verdict(manga, hand)


def _visible(items: list, now: str | None = None) -> list:
    """The titles of the list the mode shows («Всё» — the list itself)."""
    from core import content_marks as CM
    from webui import content_mode
    now = now or content_mode.mode()
    if now == "all":
        return items
    hand = CM.marks()["manga"]
    return [m for m in items if content_mode.shows(_verdict(m, hand).hentai, now)]


def _shown_sets(items: list, visible: list) -> list:
    """The сборки of the shown titles: one with hidden parts keeps the shown ones (one left — a title on its own)."""
    from core import manga_works as W
    sets = _sets(items)
    if visible is items:
        return sets
    ids = {m.id for m in visible}
    out = []
    for found in sets:
        parts = [p for p in found.parts if p.id in ids]
        if len(parts) == len(found.parts):
            out.append(found)
        elif len(parts) >= 2:
            out.append(W.MangaSet(id=found.id, title=found.title, parts=parts, kind=found.kind))
    return out


def _hidden(req: Request, manga: M.Manga) -> Response | None:
    """The page of a title (its reader) the mode hides: what it is instead; None — show it."""
    from webui import content_mode
    top = _top(M.manga_root(req.settings), manga)
    found = _verdict(top)
    if content_mode.shows(found.hentai):
        return None
    body = content_mode.hidden_item(active="manga", what="эта манга", reason=found.reason, hentai=found.hentai,
                                    back="/manga", back_label="Вся манга")
    return _frame(req, "Скрыто", body, kind="mode-hidden")


def item_shown(settings: dict, item_id: str, now: str) -> bool | None:
    """Is a title (a chapter, a сборка) shown in the mode ``now``; None — no such thing."""
    from webui import content_mode
    root = M.manga_root(settings)
    if not root:
        return None
    manga = M.LIBRARY.get(root, item_id)
    if manga is not None:
        return content_mode.shows(_verdict(_top(root, manga)).hentai, now)
    found = next((s for s in _sets(M.LIBRARY.items(root)) if s.id == item_id), None)
    if found is None:
        return None
    return any(content_mode.shows(_verdict(p).hentai, now) for p in found.parts)


def top_item(settings: dict, item_id: str) -> M.Manga | None:
    """The title a mark by hand is for: the title itself, or the series of a chapter."""
    root = M.manga_root(settings)
    manga = M.LIBRARY.get(root, item_id) if root else None
    return _top(root, manga) if manga is not None else None


def _mode_empty(items: list) -> str:
    """The list is empty because of the mode, not because the folder is."""
    from webui import content_mode
    now = content_mode.mode()
    if not items or now == "all":
        return ""
    why = ("в папке только хентай" if now == "sfw" else "хентая в папке нет")
    return (f'<div class="empty mode-empty"><p>В режиме «{content_mode.LABELS[now]}» здесь пусто: {why}.</p>'
            '<p><button type="button" class="mbutton primary" data-action="content-mode" data-mode="all">'
            'Показать всё</button></p></div>')


# ── search ──────────────────────────────────────────────────────────────────
def _matches(manga: M.Manga, query: str) -> bool:
    """nhentai-like: words must all match; ``-word`` must not; ``artist:name`` looks in one group;
    a word matches a tag exactly or the title as a piece of text."""
    title = manga.title.lower() + " " + manga.title_japanese.lower()
    for raw in query.split():
        neg = raw.startswith("-") and len(raw) > 1
        word = raw[1:] if neg else raw
        group, _, value = word.partition(":")
        if value and M._group(group) != "general" or group in {"general", "tag", "tags"} and value:
            hit = M.norm_tag(value) in manga.groups.get(M._group(group), [])
        else:
            norm = M.norm_tag(word)
            hit = norm in manga.tags or word.lower() in title
        if hit == neg:
            return False
    return True


def _sorted(items: list, sort: str) -> list:
    if sort == "title":
        return sorted(items, key=lambda m: M.natural_key(m.title))
    if sort == "pages":
        return sorted(items, key=lambda m: -m.page_count)
    return sorted(items, key=lambda m: -m.mtime)


def _language(manga: M.Manga) -> str:
    langs = [x for x in manga.groups.get("language", []) if x not in {"translated", "rewrite", "speechless"}]
    return {"russian": "RU", "english": "EN", "japanese": "JP", "chinese": "CN", "korean": "KR"}.get(
        langs[0], langs[0][:2].upper()) if langs else ""


# ── сборки: the parts of one work ──────────────────────────────────────────────
_SETS = {"key": None, "stamp": None, "sets": []}


def _sets(items: list) -> list:
    """The сборки of this scan of the folder (made again for another scan or a change by hand)."""
    from core import manga_works as W
    try:
        stamp = W._manual_file().stat().st_mtime_ns
    except OSError:
        stamp = 0
    if _SETS["key"] is not items or _SETS["stamp"] != stamp:
        _SETS.update(key=items, stamp=stamp, sets=W.sets(items, W.manual()))
    return _SETS["sets"]


def _sets_on() -> bool:
    from webui import prefs
    return str(prefs.load().get("manga_sets") or "on") != "off"


def _set_of(items: list, manga_id: str, visible: list | None = None):
    sets = _sets(items) if visible is None else _shown_sets(items, visible)
    return next((s for s in sets if any(p.id == manga_id for p in s.parts)), None)


class _Tile:
    """A сборка on the wall: sorted and paged as one title."""

    def __init__(self, found):
        self.set = found
        self.title = found.title
        self.mtime = max(p.mtime for p in found.parts)
        self.page_count = sum(p.page_count for p in found.parts)
        self.id = found.id


def cover_src(manga: M.Manga) -> str:
    """(v5.44.123) The address of a manga's cover with its picture's version: a cover chosen in the menu of covers is
    another picture under the same address (``cover.*`` of the folder) — the window does not keep the old one."""
    ref = manga.cover
    return f"/manga/cover/{manga.id}" + (f"?v={ref.stamp()}" if ref is not None else "")


def _set_card(tile: "_Tile") -> str:
    first = tile.set.parts[0]
    langs = sorted({_language(p) for p in tile.set.parts if _language(p)})
    badges = "".join(f'<span class="mbadge lang">{esc(x)}</span>' for x in langs[:3])
    badges += f'<span class="mbadge set">{_count_words(tile.set)}</span>'
    return (f'<a class="cover set" href="/manga/s/{tile.id}" title="{esc(tile.title)} — сборка">'
            f'<img src="{cover_src(first)}" alt="" loading="lazy" decoding="async">'
            f'<span class="mbadges">{badges}</span>'
            f'<span class="cover-caption">{esc(tile.title)}</span></a>')


def _parts_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "часть"
    return "части" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "частей"


def _count_words(found) -> str:
    """(v5.44.125) «3 части» — the parts of one work; «6 манг» — the manga of one folder of the parser's («silver
    soul»: different manga, one сборка by the folder)."""
    n = len(found.parts)
    if getattr(found, "kind", "") == "folder":
        from webui.pages.post_sets import plural
        return plural(n, ("манга", "манги", "манг"))
    return f"{n} {_parts_word(n)}"


def _cover_card(manga: M.Manga) -> str:
    badges = []
    lang = _language(manga)
    if lang:
        badges.append(f'<span class="mbadge lang">{esc(lang)}</span>')
    if manga.is_series:
        badges.append(f'<span class="mbadge">{len(manga.chapters)} гл.</span>')
    badges.append(f'<span class="mbadge">{manga.page_count} стр.</span>')
    return (f'<a class="cover" href="/manga/g/{manga.id}" title="{esc(manga.title)}">'
            f'<img src="{cover_src(manga)}" alt="" loading="lazy" decoding="async">'
            f'<span class="mbadges">{"".join(badges)}</span>'
            f'<span class="cover-caption">{esc(manga.title)}</span></a>')


# ── (v5.44.124) what was read — «Продолжить чтение», «Продолжить: …»: the computer's, as «Манхва», and the phone's ──
def _page_of(state: dict, key: str) -> int:
    """The page (from 1) the manga or the chapter ``key`` was left at (``state`` — ``PROGRESS.title``); 1 — none."""
    try:
        return max(0, int((state["pos"].get(key) or [0])[0])) + 1
    except (TypeError, ValueError, IndexError):
        return 1


def reader_href(target: M.Manga, page: int = 1) -> str:
    """The reader at a page of ``target`` (a manga, a chapter), the page named always: the reader without ``p`` opens
    where the manga was left — these links know the page they mean («Продолжить», «Читать снова», «Читать с начала»)."""
    return f"/manga/r/{target.id}?p={max(1, int(page))}"


def where_read(top: M.Manga, *, part: "M.Manga | None" = None, label=None) -> dict:
    """Where a title (a manga, a series) was left and where «Продолжить» goes (``M.PROGRESS``: the computer's reader
    and the phone's write it): {"target" — the manga or the chapter to open, "page" (from 1), "said" — the button's
    words («Читать», «Продолжить: стр. 14», «Продолжить: Глава 2, стр. 3», the next chapter after one read to its end,
    «Читать снова»), "place" — where it stands in words for «Продолжить чтение» («стр. 14 из 20», «Глава 2, стр. 3»),
    "done" — pages (a manga) or chapters (a series) read, "total", "at" — when, "started" — anything read at all}.
    ``part`` — a chapter of the series ``top`` on its own page: from its own place.  ``label(chapter)`` — a chapter's
    name in the words: the one reader's («Глава 2», «Том 1 Глава 7» — :func:`chapter_label`) unless given (the phone's
    — the chapter's title).  (v5.44.67 — ``phone_manga.where_read``; v5.44.124 — here, for the computer too.)
    (v5.44.126) The chapter read last and read — to its end or ticked by hand (``/api/manhwa/read``) — leads to the
    next one, as «Манхва»'s «Продолжить» (``manhwa._continue``)."""
    state = M.PROGRESS.title(top.id)
    name = label or (lambda chapter: chapter_label(top, chapter)[0])
    if part is not None:
        seen = part.id in state["pos"]
        page = _page_of(state, part.id) if seen else 1
        return {"target": part, "page": page, "said": f"Продолжить: стр. {page}" if page > 1 else "Читать",
                "place": f"стр. {min(page, len(part.pages))} из {len(part.pages)}" if seen else "",
                "done": page if seen else 0, "total": len(part.pages), "started": page > 1, "at": state["at"]}
    parts = top.chapters if top.is_series else [top]
    by_id = {p.id: p for p in parts}
    read = [i for i in state["read"] if i in by_id]
    last = by_id.get(state["last"])
    out = {"target": parts[0] if parts else top, "page": 1, "said": "Читать", "place": "", "started": bool(last),
           "at": state["at"], "done": len(read), "total": len(parts)}
    if not top.is_series:
        page = _page_of(state, top.id) if last else 1
        out.update(total=len(top.pages), done=min(page, len(top.pages)) if last else 0)
        if last:
            out["place"] = f"стр. {min(page, len(top.pages))} из {len(top.pages)}"
        if last and top.id in read and page >= len(top.pages):
            out.update(said="Читать снова", page=1)
        elif last and page > 1:
            out.update(said=f"Продолжить: стр. {page}", page=page)
        return out
    if last is None:
        return out
    page = _page_of(state, last.id)
    if last.id in read:                                   # (v5.44.126) a tick by hand too, as «Манхва»
        index = parts.index(last)
        if index + 1 < len(parts):
            after = parts[index + 1]
            out.update(target=after, page=1, said=f"Продолжить: {name(after)}", place=name(after))
        else:
            out.update(target=parts[0], page=1, said="Читать снова", place=name(last))
    else:
        at = f", стр. {page}" if page > 1 else ""
        out.update(target=last, page=page, said=f"Продолжить: {name(last)}{at}", place=f"{name(last)}{at}")
    return out


def _shelf_card(top: M.Manga) -> str:
    """A card of «Продолжить чтение» — the card of «Вся манхва» (``manhwa.card_html``): the cover, how much is read,
    the title, where it was left; it opens the reader there (read to its end — at the next chapter)."""
    from webui.pages import manhwa as MH
    where = where_read(top)
    return MH.card_html(reader_href(where["target"], where["page"]), cover_src(top), top.title, where["place"],
                        done=where["done"], total=where["total"], badge=_language(top))


def chapter_rows(series: M.Manga, state: dict) -> str:
    """The chapters of a series on its page — (v5.44.126) the rows of «Манхва» (``manhwa.chapter_row``; until then
    covers with «✓ прочитана»), the newest first: «Глава 2» and its own name (:func:`chapter_label`, the reader's
    words), «Вы остановились здесь · стр. 2 из 3» (it opens there), where and when it came and «новая» (its
    ``chapter.json``: a chapter «Скачать» brought — ``manhwa.arrival``), the tick «прочитана» by hand (``kind``
    "manga": ``M.PROGRESS`` — the memory of the readers of the computer and the phone)."""
    from webui.pages import manhwa as MH
    home = MH.home_site(series.path) if Path(series.path).is_dir() else ""
    now = time.time()
    read_ids = set(state["read"])
    rows = []
    for chapter in reversed(series.chapters):
        read = chapter.id in read_ids
        here = chapter.id == state["last"]
        stopped = here and not read
        total = len(chapter.pages)
        page = min(_page_of(state, chapter.id), max(1, total)) if stopped else 1
        label, name = chapter_label(series, chapter)
        came, fresh = MH.arrival(chapter.info, MH.came_of(chapter.info, chapter.mtime), home, read=read, here=stopped,
                                 now=now)
        rows.append(MH.chapter_row(chapter.id, reader_href(chapter, page), label, name=name, read=read,
                                   here=MH.here_words(page, total, read=read) if here else "", came=came, fresh=fresh,
                                   kind="manga"))
    return "".join(rows)


def chapter_of(settings: dict, key: str) -> "tuple[M.Manga, M.Manga] | None":
    """(v5.44.126) (its title — the series or itself, the manga) of a chapter of a series or a manga of one part — what
    the memory of reading keeps as a «глава» (``M.PROGRESS``); None — no such one (a series is no chapter)."""
    root = M.manga_root(settings)
    if not root or not Path(root).is_dir():
        return None
    manga = M.LIBRARY.get(root, str(key or ""))
    if manga is None or manga.is_series:
        return None
    return _top(root, manga), manga


# ── /manga ──────────────────────────────────────────────────────────────────
def _no_folder(req: Request, root: str, show: str) -> Response | None:
    """The page in place of the list when the manga folder is not chosen or not there."""
    if not root:
        return _frame(req, "Манга", '<div class="empty manga-empty"><p>Папка с мангой не выбрана.</p>'
                      '<p>Каждая папка или архив .zip/.cbz внутри неё — одна манга, папка с подпапками-главами — '
                      'серия, PDF — книга.</p><p><button type="button" class="mbutton primary" '
                      'data-action="choose-manga-folder">Выбрать папку с мангой</button></p>'
                      '<p class="action-status" role="status"></p></div>', show=show)
    if not Path(root).is_dir():
        return _frame(req, "Манга", f'<div class="empty"><p>Папки манги нет на месте: <code>{esc(root)}</code>.</p>'
                      '<p>Подключите диск или выберите другую папку в настройках.</p></div>', show=show)
    return None


def index(req: Request) -> Response:
    root, items = _library(req, refresh=bool(req.arg("refresh")))
    missing = _no_folder(req, root, "all")
    if missing is not None:
        return missing
    query = req.arg("q").strip()
    sort = req.arg("sort", "newest")
    visible = _visible(items)           # (v5.44.53) what the mode «SFW / Всё / NSFW» shows
    found = [m for m in visible if _matches(m, query)] if query else list(visible)
    grouped = not query and _sets_on()
    if grouped:                         # the parts of one work: one tile (a search shows each title it found)
        sets = _shown_sets(items, visible)
        inside = {p.id for s in sets for p in s.parts}
        found = [m for m in found if m.id not in inside] + [_Tile(s) for s in sets]
    found = _sorted(found, sort if sort in SORTS else "newest")
    shelf = ""
    if not query and found:             # (v5.44.124) «Продолжить чтение» — as «Вся манхва», not in a search
        from webui.pages import manhwa as MH
        shelf = MH.shelf_html([_shelf_card(m) for m in MH.reading_now(visible, M.PROGRESS, SHELF)])
    per_page = rows_per_page(req, ROWS_COOKIE, PER_PAGE)     # whole rows of the window's columns (v5.44.1)
    pages = max(1, (len(found) + per_page - 1) // per_page)
    page = min(req.int_arg("page", 1, lo=1), pages)
    shown = found[(page - 1) * per_page: page * per_page]
    sorts = "".join(
        f'<a class="{"active" if key == sort else ""}" href="{esc(url("/manga", q=query, sort=key))}">{esc(label)}</a>'
        for key, label in SORTS.items())
    together = "" if query else (
        f'<button type="button" class="link" data-action="manga-sets" data-value="{"off" if grouped else "on"}" '
        f'title="{"Показать каждую часть отдельной мангой" if grouped else "Части одного тайтла — одной плиткой"}">'
        f'{"Каждую отдельно" if grouped else "Сборками"}</button>')
    head = (f'<div class="manga-head"><h1>{"Поиск: " + esc(query) if query else "Вся манга"} '
            f'<span class="muted">{len(found)}</span></h1><nav class="manga-sorts">{sorts}{together}'
            f'<a href="{esc(url("/manga", q=query, sort=sort, refresh=1))}" title="Перечитать папку манги">Обновить</a></nav></div>')
    if shown:
        rows = rows_attrs(ROWS_COOKIE, PER_PAGE, per_page, len(found), page)
        grid = f'<div class="covers"{rows}>' + "".join(_set_card(m) if isinstance(m, _Tile) else _cover_card(m)
                                                       for m in shown) + "</div>"
    elif query:
        grid = f'<div class="empty"><p>По запросу «{esc(query)}» манги нет.</p></div>'
    elif _mode_empty(items):
        grid = _mode_empty(items)
    else:
        grid = ('<div class="empty"><p>В папке манги пока ничего нет.</p><p><a class="mbutton primary" '
                'href="/get">Скачать мангу</a></p></div>')
    body = (f'<div class="manga-index">{shelf}{head}{grid}'
            + paginator(page, pages, lambda n: url("/manga", q=query, sort=sort, page=n)) + "</div>")
    return _frame(req, "Манга" if not query else query, body, show="all", assets=ASSETS)


def random_title(req: Request) -> Response:
    import random
    _root, items = _library(req)
    items = _visible(items)
    return Response.redirect(f"/manga/g/{random.choice(items).id}" if items else "/manga")


# ── /manga/tags, /artists, /characters, /parodies ─────────────────────────────
# a group of core.manga_library -> (the colour's category, the type in the table)
INDEX_TYPES = {"general": ("general", "Тег"), "artist": ("artist", "Артист"), "character": ("character", "Персонаж"),
               "parody": ("copyright", "Пародия"), "copyright": ("copyright", "Копирайт")}
INDEX_PER_PAGE = 100            # rows of the table (rule34.xxx's list); nhentai's chips — all on one page


def tag_index(req: Request) -> Response:
    """The tags (artists, characters, parodies) of the manga in the folder, each with how many manga have it — as
    rule34.xxx's list of tags (a form, a table, pages) and as nhentai's (Популярные / А–Я, a row of letters, the chips
    in columns, a field «Найти среди»); the look shows its own (``webui/tag_lists.py``); a tag opens its manga."""
    from webui import tag_lists as TL
    kind = req.params["kind"]
    title, groups, none = INDEX_TITLES[kind]
    root, items = _library(req)
    missing = _no_folder(req, root, kind)
    if missing is not None:
        return missing
    entries = [(group, tag, n) for (group, tag), n in _counts(_visible(items)).items() if group in groups and tag]
    if not entries:
        body = (f'<div class="manga-head"><h1>{esc(title)} <span class="muted">0</span></h1></div>'
                f'<div class="empty"><p>У манги в папке пока нет {none}.</p><p>Они берутся из описания манги '
                '(<code>meta.json</code> рядом с ней — его пишет «Скачать с nhentai»).</p></div>')
        return _frame(req, title, f'<div class="manga-index tag-index">{body}</div>', show=kind)
    name = req.arg("name").strip()[:200]
    order = TL.order_of(req.arg("order"), "name" if req.arg("sort") == "name" else "count")
    raw_dir = req.arg("dir") if req.arg("dir") in ("asc", "desc") else ""
    desc = TL.descending(order, raw_dir)
    match = TL.matcher(name)
    tags = [TL.Tag(name=tag, category=INDEX_TYPES[group][0], count=n, title=INDEX_TYPES[group][1],
                   href=url("/manga", q=f"{group}:{tag}")) for group, tag, n in entries if match(tag)]
    tags = TL.ordered(tags, order, desc, ("artist", "character", "copyright", "general"), M.natural_key)
    pages = max(1, (len(tags) + INDEX_PER_PAGE - 1) // INDEX_PER_PAGE)
    page = min(req.int_arg("page", 1, lo=1), pages)

    def at(n: int = 1, **over) -> str:
        params = {"name": name, "order": order, "dir": raw_dir, "page": n}
        params.update(over)
        params["order"] = "" if params["order"] == "count" else params["order"]
        return url(f"/manga/{kind}", **params)
    r34 = TL.r34_list(action=f"/manga/{kind}", tags=tags[(page - 1) * INDEX_PER_PAGE:page * INDEX_PER_PAGE],
                      name=name, order=order, desc=desc, count_title="Манги", paginator=paginator(page, pages, at),
                      head=f'<h2 class="page-title">{esc(title)} <span class="muted">{len(tags)}</span></h2>')
    find = (f'<div class="tag-find"><input type="search" class="tag-filter" placeholder="Найти среди {len(tags)}" '
            f'aria-label="Найти: {esc(title.lower())}" autocomplete="off" spellcheck="false"></div>')
    letters = TL.letter_pages(tags, len(tags), lambda _n: "") if order == "name" else None
    nh = TL.nhentai_list(title=title, total=len(tags), tags=tags, order=order, letters=letters, find=find,
                         order_url=lambda key: at(order=key, dir="", page=1))
    return _frame(req, title, f'<div class="manga-index tag-index" data-kind="{esc(kind)}">{r34}{nh}</div>', show=kind)


# ── /manga/g/<id> ───────────────────────────────────────────────────────────
def _tag_rows(manga: M.Manga, counts: dict) -> str:
    rows = []
    for group in M.GROUP_ORDER:
        tags = manga.groups.get(group) or []
        if not tags:
            continue
        links = "".join(
            f'<a class="mtag mtag-{group}" href="{esc(url("/manga", q=f"{group}:{t}"))}">{esc(t.replace("_", " "))}'
            f'<span class="mcount">{human_count(counts.get((group, t), 0))}</span></a>' for t in tags)
        rows.append(f'<div class="tag-row"><span class="tr-name">{esc(M.GROUP_TITLES[group])}:</span>'
                    f'<span class="tr-tags">{links}</span></div>')
    rows.append(f'<div class="tag-row"><span class="tr-name">Страниц:</span><span class="tr-tags">'
                f'<span class="mtag plain">{manga.page_count}</span></span></div>')
    if manga.mtime:
        added = time.strftime("%d.%m.%Y", time.localtime(manga.mtime))
        rows.append(f'<div class="tag-row"><span class="tr-name">Добавлена:</span><span class="tr-tags">'
                    f'<span class="mtag plain">{added}</span></span></div>')
    return "".join(rows)


def _counts(items: list) -> dict:
    out: dict = {}
    for manga in items:
        for group, tags in manga.groups.items():
            for tag in tags:
                out[(group, tag)] = out.get((group, tag), 0) + 1
    return out


def title_page(req: Request) -> Response:
    manga = _get(req)
    if manga is None:
        return _frame(req, "Нет такой манги", '<div class="empty"><p>Такой манги нет — возможно, её переименовали '
                      'или убрали из папки.</p><p><a href="/manga?refresh=1">Перечитать папку манги</a></p></div>')
    hidden = _hidden(req, manga)                 # (v5.44.53) the mode «SFW / Всё / NSFW» hides it
    if hidden is not None:
        return hidden
    _root, items = _library(req)
    visible = _visible(items)
    series = M.LIBRARY.get(M.manga_root(req.settings), manga.parent_id) if manga.parent_id else None
    top = series or manga
    found = _verdict(top)
    # (v5.44.124) where it was left — «Продолжить: стр. 14» / «Продолжить: Глава 2, стр. 3» and «Читать с начала», as
    # «Манхва» and the title's one page say it (a chapter's own page — from its own place)
    where = where_read(top, part=manga if series is not None else None)
    go = reader_href(where["target"], where["page"])
    buttons = [f'<a class="mbutton primary" href="{esc(go)}">{esc(where["said"])}</a>']
    if where["said"].startswith("Продолжить"):
        first = manga.chapters[0] if manga.is_series else manga
        buttons.append(f'<a class="mbutton" href="{esc(reader_href(first))}">Читать с начала</a>')
    if manga.source_url:
        buttons.append(f'<a class="mbutton" href="{esc(manga.source_url)}" target="_blank" rel="noopener noreferrer">На сайте</a>')
    buttons.append(f'<button type="button" class="mbutton" data-action="reveal-manga" data-id="{manga.id}">Показать в папке</button>')
    buttons.append(_mark_button(top, found))
    # (v5.44.119) «Докачать сейчас» of a title downloaded from a site — its one page; (v5.44.124) in the block of
    # «Манхва»: «Скачано с … · переводчики», «Новые главы — скачивать самой», «брать с других сайтов», the paid ones
    from webui.pages import manhwa as MH
    source = MH.source_block("manga", top.id, top.path, top.title)
    # (v5.44.126) a series: «Прочитано N из M глав» with its bar, as «Манхва» (a manga of one part — no chapters)
    progress = MH.progress_html(where["done"], where["total"]) if manga.is_series else ""
    back = (f'<p class="manga-back"><a href="/manga/g/{series.id}">← {esc(series.title)}</a></p>' if series else "")
    info = (f'<div class="manga-info"><a class="manga-cover" href="{esc(go)}">'
            f'<img src="{cover_src(manga)}" alt=""></a><div class="manga-meta">{back}'
            f'<h1>{esc(manga.title)}</h1>'
            + (f'<h2>{esc(manga.title_japanese)}</h2>' if manga.title_japanese else "")
            + f'<div class="tag-rows">{_tag_rows(manga, _counts(visible))}{_mark_row(found)}</div>'
            f'<div class="manga-buttons">{"".join(buttons)}<p class="action-status" role="status"></p></div>'
            f'{progress}{source}</div></div>')
    related_html = _set_box(items, manga, visible) if not manga.parent_id else ""
    if not manga.parent_id and _set_of(items, manga.id, visible) is None:
        parts = M.related(visible, manga)        # (not a сборка: loose chapters by their files' names)
        if parts:
            cards = "".join(_cover_card(m) for m in parts)
            related_html = (f'<h3 class="manga-sub">Другие главы этого тайтла <span class="muted">{len(parts)}</span></h3>'
                            f'<div class="covers">{cards}</div>') + related_html
    if manga.is_series:             # (v5.44.126) the chapters of «Манхва»: the newest first, ticks, «Сначала первые»
        state = M.PROGRESS.title(manga.id)
        extra = (f'<h3 class="manga-sub">Главы <span class="muted">{len(manga.chapters)}</span></h3>'
                 + MH.chapters_html(chapter_rows(manga, state)))
    else:
        thumbs = "".join(
            f'<a class="page-thumb" href="/manga/r/{manga.id}?p={n}"><img src="/manga/thumb/{manga.id}/{n}" alt="Страница {n}" '
            f'loading="lazy" decoding="async"></a>' for n in range(1, len(manga.pages) + 1))
        extra = f'<div class="page-thumbs">{thumbs}</div>'
    return _frame(req, manga.title, f'<div class="manga-gallery">{info}{extra}{related_html}</div>', kind="manga-title",
                  assets=ASSETS)


def _mark_row(found) -> str:
    """(v5.44.53) «Хентай: да — с nhentai.net» among the tag rows: a title the program (or a mark by hand) takes for
    hentai, or one marked «не хентай» by hand."""
    if not found.hentai and not found.by_hand:
        return ""
    said = ("да" if found.hentai else "нет") + (f" — {found.reason}" if found.reason else "")
    return (f'<div class="tag-row content-row"><span class="tr-name">Хентай:</span><span class="tr-tags">'
            f'<span class="mtag plain">{esc(said)}</span></span></div>')


def _mark_button(manga: M.Manga, found) -> str:
    """«Это хентай» / «Это не хентай»: the mark by hand for the switch «SFW / Всё / NSFW» (a chapter — its series)."""
    value = "safe" if found.hentai else "hentai"
    label = "Это не хентай" if found.hentai else "Это хентай"
    tip = ("Не прятать в режиме «SFW»" if found.hentai else "Прятать в режиме «SFW», показывать в «NSFW»")
    return (f'<button type="button" class="mbutton" data-action="content-mark" data-kind="manga" data-id="{manga.id}" '
            f'data-value="{value}" title="{esc(tip)}">{label}</button>')


def _set_box(items: list, manga: M.Manga, visible: list | None = None) -> str:
    """The title's сборка (its other parts, «Убрать из сборки») — or «В сборку с…» (titles by the same people);
    (v5.44.53) only the titles the mode shows (``visible``)."""
    from core import manga_works as W
    visible = items if visible is None else visible
    found = _set_of(items, manga.id, visible)
    done = manga.id in W.manual()["apart"] or manga.id in W.manual()["joined"]
    back = (f'<button type="button" class="link" data-action="manga-set" data-id="{manga.id}" data-do="program">'
            f'Пусть решает программа</button>') if done else ""
    if found is not None:
        others = [p for p in found.parts if p.id != manga.id]
        number = next((n for n, p in enumerate(found.parts, 1) if p.id == manga.id), 0)
        cards = "".join(_cover_card(m) for m in others)
        return (f'<h3 class="manga-sub">Сборка: <a href="/manga/s/{found.id}">{esc(found.title)}</a> '
                f'<span class="muted">{"манга" if found.kind == "folder" else "часть"} {number} из '
                f'{len(found.parts)}</span></h3>'
                f'<p class="manga-set-tools"><button type="button" class="link" data-action="manga-set" '
                f'data-id="{manga.id}" data-do="apart">Убрать из сборки</button> {back}'
                f'<span class="action-status" role="status"></span></p><div class="covers">{cards}</div>')
    mine = W.people_of(manga)
    near = [m for m in visible if m.id != manga.id and mine and W.people_of(m) & mine][:60]
    if not near:
        return (f'<p class="manga-set-tools">{back}<span class="action-status" role="status"></span></p>' if back else "")
    options = "".join(f'<option value="{m.id}">{esc(m.title[:120])}</option>'
                      for m in sorted(near, key=lambda m: M.natural_key(m.title)))
    return (f'<form class="manga-set-tools" data-form="manga-set" data-id="{manga.id}">'
            f'<label>В сборку с <select name="with" aria-label="С какой мангой собрать">{options}</select></label> '
            f'<button type="submit" class="link">Собрать</button> {back}'
            f'<span class="action-status" role="status"></span></form>')


def set_page(req: Request) -> Response:
    root, items = _library(req)
    missing = _no_folder(req, root, "")
    if missing is not None:
        return missing
    found = next((s for s in _sets(items) if s.id == req.params["id"]), None)
    if found is None:
        return _frame(req, "Нет такой сборки", '<div class="empty"><p>Такой сборки нет — её части переименовали, '
                      'убрали из папки или из сборки.</p><p><a href="/manga">Вся манга</a></p></div>')
    ids = {p.id for p in _visible(found.parts)}                          # (v5.44.53) the mode hides some parts
    shown = [p for p in found.parts if p.id in ids]
    if not shown:
        return _hidden(req, found.parts[0])
    if len(shown) < len(found.parts):
        from core import manga_works as W
        found = W.MangaSet(id=found.id, title=found.title, parts=shown, kind=found.kind)
    cards = "".join(_cover_card(m) for m in found.parts)
    pages = sum(p.page_count for p in found.parts)
    head = (f'<div class="manga-head"><h1>{esc(found.title)} <span class="muted">{_count_words(found)} '
            f'· {pages} стр.</span></h1><nav class="manga-sorts">'
            f'<a href="/manga">← Вся манга</a><a class="mbutton primary" href="/manga/r/{_first_page(found.parts[0])}">'
            f'Читать с первой части</a></nav></div>')
    return _frame(req, found.title, f'<div class="manga-index manga-set">{head}<div class="covers">{cards}</div></div>')


def _first_page(manga: M.Manga) -> str:
    target = manga.chapters[0] if manga.is_series else manga
    return f"{target.id}?p=1"


def set_change(req: Request) -> Response:
    """By hand: «apart» — out of its сборка; «with» — together with another title; «program» — as the program decides."""
    from core import manga_works as W
    manga = _get(req)
    if manga is None:
        return Response.json({"ok": False, "error": "Такой манги нет"}, 404)
    body = req.json() or {}
    act = str(body.get("do") or "")
    if act == "apart":
        W.take_apart(manga.id)
    elif act == "with":
        other = M.LIBRARY.get(M.manga_root(req.settings), str(body.get("with") or ""))
        if other is None or other.id == manga.id:
            return Response.json({"ok": False, "error": "Выберите другую мангу"}, 400)
        W.put_with(manga.id, other.id)
    elif act == "program":
        W.back_to_program(manga.id)
    else:
        return Response.json({"ok": False, "error": "Непонятно, что сделать"}, 400)
    _root, items = _library(req)
    found = _set_of(items, manga.id)
    return Response.json({"ok": True, "set": found.id if found else ""})


# ── /manga/r/<id> — (v5.44.117) the one reader of the program (webui/pages/reader.py) ─────────────────────────────
def chapter_label(series: "M.Manga | None", chapter: M.Manga) -> tuple:
    """(its label in the reader, its own name): a chapter of a series — «Глава 3» (its folder «3», «Long Saga — 3»),
    «Том 1 Глава 7» and «Имя» from «Том 1 Глава 7 - Имя»; a manga on its own — its title."""
    from core.manhwa_library import parse_name
    if series is None:
        return chapter.title, ""
    head = series.title + " — "
    rest = chapter.title[len(head):].strip() if chapter.title.startswith(head) else ""
    if rest and _NUMBER.fullmatch(rest):
        return f"Глава {rest}", ""
    tome, number, own = parse_name(Path(chapter.path).stem if Path(chapter.path).is_file() else Path(chapter.path).name)
    if number:
        return (f"Том {tome} " if tome else "") + f"Глава {number}", own
    return rest or chapter.title, ""


_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _sizes(refs: list) -> list:
    """(width, height) of pages (read from their pictures' headers once — ``manhwa_library.page_size``); a page of a
    PDF — (0, 0): not known before it is drawn."""
    from core import manhwa_library as H
    return [(0, 0) if ref.pdf_page >= 0 else H.page_size(H.Page(ref.path, ref.member)) for ref in refs]


def book(req: Request, manga: M.Manga, *, phone: bool = False, asked: "str | None" = None, sizes: "bool | None" = None):
    """(v5.44.117) A manga (a chapter of a series) for the one reader (``webui/pages/reader.py``): the chapters of its
    series (read, new; with ``source.json`` — in the folder, the paid ones it waits for), its pages, where it was left,
    the way its title is read (``PROGRESS.mode_of``; the first time — the pages left to right, a strip for tall ones)."""
    from core.manhwa_library import parse_name
    from webui.pages import manhwa as MH, reader as R
    root = M.manga_root(req.settings)
    series = M.LIBRARY.get(root, manga.parent_id) if manga.parent_id else None
    top = series or manga
    parts = series.chapters if series is not None else [manga]
    state = M.PROGRESS.title(top.id)
    m = "/m" if phone else ""
    now = time.time()
    stored = MH.site_of(top.path) if Path(top.path).is_dir() else {}
    rows = []
    for c in parts:
        label, name = chapter_label(series, c)
        href = f"{m}/manga/r/{c.id}"
        read = c.id in state["read"]
        tome, number = 0, None
        if series is not None:
            tome, num, _own = parse_name(label)
            number = R.number_of(num) if num else None
        came = MH.came_of(c.info, c.mtime)           # (v5.44.126) its chapter.json first — as the title's page says
        rows.append(R.Row(key=c.id, label=label, name=name, href=href, start=f"{href}?p=1",
                          last=f"{href}?p={len(c.pages)}", pages=len(c.pages), read=read,
                          new=bool(series is not None and not read and came and now - came < R.NEW_FOR),
                          tome=tome, number=number))
    current = rows[next(i for i, c in enumerate(parts) if c.id == manga.id)]
    rows = R.interleave(rows, MH.waiting_rows(stored))
    chosen = M.PROGRESS.mode_of(top.id)
    chosen = chosen if chosen in R.MODES else ""
    sample = _sizes(manga.pages[:5]) if not chosen else []
    way = chosen or R.auto("манга", True, sample)
    known = _sizes(manga.pages) if (way == "strip" if sizes is None else sizes) else [(0, 0)] * len(manga.pages)
    pages = [{"src": f"/manga/page/{manga.id}/{k}", "w": w, "h": h, "thumb": f"/manga/thumb/{manga.id}/{k}"}
             for k, (w, h) in enumerate(known, 1)]
    page, part = R.place_of(req, M.PROGRESS.place(top.id, manga.id), len(pages), read=current.read, asked=asked)
    if way == "strip":
        page = min(page, max(0, len(pages) - 1))
    home = MH.home_of("manga", stored, top.title, phone) or f"{m}/manga/g/{top.id}"    # (v5.44.119) its one page
    return R.Book(src="manga", kind="manga", phone=phone, name=top.title, home=home, rows=rows,
                  at=rows.index(current), pages=pages, href=f"{m}/manga/r/{manga.id}", base=f"{m}/manga/r/" + "{key}",
                  progress="/api/manga/progress", mode_api=f"/api/manga/mode?id={top.id}",
                  chapter_api="/api/manga/chapter/{key}" + ("?m=1" if phone else ""),
                  site_api=(f"/api/reader/site?src=manga&id={manga.id}" + ("&m=1" if phone else "")) if stored else "",
                  mode=way, chosen=chosen, page=page, part=part, follow=MH.follows(req, stored),
                  single=series is None, linked=bool(stored), dir_default=chosen if chosen in ("ltr", "rtl") else "ltr")


def site_book(req: Request, key: str, *, phone: bool = False, sizes: "bool | None" = None) -> tuple:
    """(v5.44.117) (the chapter's book with the site's chapters among the folder's, still asked) — ``reader.site``,
    the strip's next chapter (``sizes`` — its pages' sizes read)."""
    from webui.pages import manhwa as MH, reader as R
    from webui.pages.manga_online import online_link
    root = M.manga_root(req.settings)
    manga = M.LIBRARY.get(root, key) if root else None
    if manga is None or manga.is_series:
        raise LookupError("Такой манги нет")
    top = _top(root, manga)
    stored = MH.site_of(top.path) if Path(top.path).is_dir() else {}
    if not stored:
        return None, False
    url, branch = str(stored["url"]), str(stored.get("branch") or "")
    reading, pending = R.site_reading(url, branch, req.settings)
    if reading is None:
        return None, pending
    found = book(req, manga, phone=phone, asked="1", sizes=sizes)
    R.merge_site(found, reading, online_link(url, branch, phone))
    return found, False


def reader(req: Request) -> Response:
    """``/manga/r/<id>?p=N`` (``last``, ``end``); the address of before — ``/manga/r/<id>/<n>`` — the same reader."""
    manga = _get(req)
    if manga is None or manga.is_series:
        return Response.redirect(f"/manga/g/{req.params['id']}" if manga else "/manga")
    hidden = _hidden(req, manga)                 # (v5.44.53) the mode «SFW / Всё / NSFW» hides it
    if hidden is not None:
        return hidden
    total = len(manga.pages)
    if not total:
        return Response.redirect(f"/manga/g/{manga.id}")
    asked = None
    if "n" in req.params:
        n = int(req.params["n"])
        if n < 1 or n > total:
            return Response.redirect(f"/manga/r/{manga.id}?p={max(1, min(n, total))}")
        asked = str(n)
    return show(req, manga, phone=False, asked=asked)


def show(req: Request, manga: M.Manga, *, phone: bool, asked: "str | None" = None) -> Response:
    """The reader's page of a manga (the computer's and the phone's): (v5.44.67) the page open is what was read — the
    phone's «Продолжить чтение» knows it."""
    from webui.pages import reader as R
    found = book(req, manga, phone=phone, asked=asked)
    if found.mode != "strip":
        total = len(found.pages)
        top = _top(M.manga_root(req.settings), manga)
        M.PROGRESS.record(top.id, manga.id, min(found.page, total - 1), 0.0, done=found.page >= total - 1)
    return R.page(req, found, frame=lambda title, body, kind, assets: _frame(req, title, body, kind=kind,
                                                                            assets=assets))


def chapter_json(req: Request) -> Response:
    """(v5.44.117) A manga (a chapter of a series) for the strip to go on with: as ``/api/manhwa/chapter/<id>``
    answers (``reader.chapter_reply``); ``?m=1`` — the phone's addresses."""
    from webui.pages import reader as R
    manga = _get(req)
    if manga is None or manga.is_series:
        return Response.json({"ok": False, "error": "Такой главы нет"}, 404)
    from webui import content_mode
    if not content_mode.shows(_verdict(_top(M.manga_root(req.settings), manga)).hentai):
        return Response.json({"ok": False, "error": "Скрыто режимом"}, 403)
    phone = req.arg("m") == "1"
    found = None
    top = _top(M.manga_root(req.settings), manga)
    if Path(top.path).is_dir() and (Path(top.path) / ".lb" / "source.json").is_file():
        found, _pending = site_book(req, manga.id, phone=phone, sizes=True)    # the words after it: the site's too
    if found is None:
        found = book(req, manga, phone=phone, asked="1", sizes=True)
    return Response.json(R.chapter_reply(found, found.at, found.pages))


def mode(req: Request) -> Response:
    """(v5.44.117) ?id=<a manga or a chapter> {mode}: the way its title is read ("" — the program's by its kind)."""
    from webui.pages import reader as R
    root = M.manga_root(req.settings)
    manga = M.LIBRARY.get(root, req.arg("id")) if root else None
    if manga is None:
        return Response.json({"ok": False, "error": "Такой манги нет"}, 404)
    value = str(req.json().get("mode") or "")
    if value and value not in R.MODES:
        return Response.json({"ok": False, "error": "Такого способа нет"}, 400)
    M.PROGRESS.set_mode(_top(root, manga).id, value)
    return Response.json({"ok": True, "mode": value})


# ── pictures ─────────────────────────────────────────────────────────────────
def _page_ref(req: Request):
    manga = _get(req)
    if manga is None:
        return None
    if "n" in req.params:
        n = int(req.params["n"])
        return manga.pages[n - 1] if 1 <= n <= len(manga.pages) else None
    return manga.cover


def _placeholder(text: str) -> Response:
    from html import escape
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="350" height="500" viewBox="0 0 350 500">'
           '<rect width="350" height="500" fill="#8884"/><text x="175" y="255" font-family="sans-serif" '
           f'font-size="20" text-anchor="middle" fill="#888">{escape(text)}</text></svg>')
    return Response(200, svg.encode("utf-8"), "image/svg+xml", {"Cache-Control": "no-cache"})


def _thumb_response(ref, size: int) -> Response:
    if ref is None:
        return _placeholder("нет страницы")
    from webui.pages.media import _MAKING
    with _MAKING:
        made = M.thumbnail(ref, size)
    if made is None:
        return _placeholder("PDF: нужен PyMuPDF" if ref.ext == ".pdf" else "нет превью")
    return Response(200, content_type="image/jpeg", file=made, headers={"Cache-Control": "private, max-age=86400"})


def cover(req: Request) -> Response:
    return _thumb_response(_page_ref(req), COVER_SIZE)


def page_thumb(req: Request) -> Response:
    return _thumb_response(_page_ref(req), THUMB_SIZE)


def page_image(req: Request) -> Response:
    ref = _page_ref(req)
    if ref is None:
        return Response.not_found("Нет такой страницы")
    if not ref.member and ref.pdf_page < 0:
        import mimetypes
        ctype = mimetypes.guess_type(ref.name)[0] or "application/octet-stream"
        return Response(200, content_type=ctype, file=Path(ref.path), headers={"Cache-Control": "private, max-age=3600"})
    try:
        rendered = M.render_page(ref)
    except (OSError, zipfile.BadZipFile) as exc:      # the archive changed, or the page is too large
        M._LOG.warning("manga page %s / %s not shown: %s", ref.path, ref.member, exc)
        return _placeholder("страница не читается")
    if rendered is None:
        return _placeholder("PDF: нужен PyMuPDF")
    data, ctype = rendered
    return Response(200, data, ctype, {"Cache-Control": "private, max-age=3600"})


def progress(req: Request) -> Response:
    """(v5.44.117) {chapter, page from 0, part, done} — the place in the one reader (``webui/pages/reader.py``: a page,
    the strip's page and how much of it is above the window); (v5.44.67) {id, page from 1} — the page open (the phone's
    reader of before); the last one — the manga (the chapter) is read."""
    body = req.json()
    root = M.manga_root(req.settings)
    one = "chapter" in body
    manga = M.LIBRARY.get(root, str(body.get("chapter" if one else "id") or "")) if root else None
    if manga is None or manga.is_series:
        return Response.json({"ok": False, "error": "Такой манги нет"}, 404)
    try:
        page = int(body.get("page") or 0) + (1 if one else 0)
        part = float(body.get("part") or 0) if one else 0.0
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Непонятное место"}, 400)
    if not 1 <= page <= len(manga.pages):
        return Response.json({"ok": False, "error": "Такой страницы нет"}, 400)
    done = bool(body.get("done")) if one else page == len(manga.pages)     # the strip: read to its end — said
    M.PROGRESS.record(_top(root, manga).id, manga.id, page - 1, part, done=done)
    return Response.json({"ok": True})


def reveal(req: Request) -> Response:
    manga = _get(req)
    if manga is None:
        return Response.json({"ok": False, "error": "Такой манги нет"}, 404)
    from webui import desktop
    try:
        desktop.reveal(Path(manga.path))
    except OSError as exc:
        return Response.json({"ok": False, "error": f"Не удалось открыть папку: {exc}"}, 500)
    return Response.json({"ok": True})
