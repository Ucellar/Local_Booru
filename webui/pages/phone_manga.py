"""(v5.44.67) «Манга» on the owner's phone — the sketch «Local Booru на телефоне» («Манга», «Читалка манги»).

* ``/m/manga`` (:func:`index`) — the search (a title, a tag, ``artist:имя``), the sections «Вся манга / Теги /
  Артисты / Персонажи / Пародии / Случайная», «Продолжить чтение» (``core.manga_library.PROGRESS``: what was read on
  the phone and on the computer — the page it stopped at), the covers in three columns (a сборка — one tile «⧉ N»);
  «Показать ещё» brings the next covers in (``?part=1`` — JSON, as the gallery's);
* ``/m/manga/g/<id>`` (:func:`title_page`) — the cover, the names, the tags by kind (a tag — its search), «Читать» /
  «Продолжить: стр. 14» / «Читать с начала» (v5.44.116: the computer's words), the chapters of a series or the pages
  of a manga, the сборка it is in; (v5.44.126) as the phone's «Манхва»: «Прочитано N из M глав», «Скачано с …», the
  chapters the newest first with «Сначала первые», «с MangaLib · сегодня», «новая» and the tick «прочитана» by hand
  (``phone_manhwa.chapter_row``, ``read_line``, ``from_site``; the computer's ``/api/manhwa/read`` with «manga»);
* ``/m/manga/s/<id>`` — a сборка: its parts; ``/m/manga/<tags|artists|characters|parodies>`` — the tags of the folder
  with how many manga have each (a filter field); ``/m/manga/random``;
* ``/m/manga/r/<id>?p=14`` (:func:`reader`) — (v5.44.117) the program's one reader (``webui/pages/reader.py``,
  ``static/reader.js``; ``manga.show(phone=True)``): a page over the whole screen; a tap on its sides or a swipe turns
  it, in the middle — the panels («‹ К тайтлу», «‹ Глава ›» and the list of chapters on top; «Страница N из M», its
  slider and «Как читать» — left to right, right to left, «Прокруткой» — at the bottom); the page open is kept
  (``/api/manga/progress``), the way — for the title (``/api/manga/mode``); after the last one — «Глава закончилась»
  and the next chapter.  Without its script the reader is links ``?p=``.

The data is the computer's tab (``webui/pages/manga.py``: the folder, the сборки, the mode «SFW / Всё / NSFW»);
downloading and the changes by hand stay on the computer — but (v5.44.122) «Это хентай» / «Это не хентай» of a title
(``phone.mark_box``: the computer's API).
"""
from __future__ import annotations

import random
import time
from pathlib import Path

from core import manga_library as M
from webui.html import esc, url
from webui.pages import manga as MG, phone
from webui.server import Request, Response

PER_PAGE = 60
SHELF = 10
SECTIONS = (("all", "Вся манга", "/m/manga"), ("feed", "Лента", "/m/manga/feed"),     # (v5.44.105) the sites'
            ("tags", "Теги", "/m/manga/tags"),
            ("artists", "Артисты", "/m/manga/artists"), ("characters", "Персонажи", "/m/manga/characters"),
            ("parodies", "Пародии", "/m/manga/parodies"), ("random", "Случайная", "/m/manga/random"))


def register(router) -> None:
    router.add("GET", r"/m/manga", index)
    router.add("GET", r"/m/manga/random", random_title)
    router.add("GET", r"/m/manga/(?P<kind>tags|artists|characters|parodies)", tag_index)
    router.add("GET", r"/m/manga/g/(?P<id>[0-9a-f]{12})", title_page)
    router.add("GET", r"/m/manga/s/(?P<id>[0-9a-f]{12})", set_page)
    router.add("GET", r"/m/manga/r/(?P<id>[0-9a-f]{12})", reader)


# ── shared bits ──────────────────────────────────────────────────────────────────────────────────────────────────
def _page(req: Request, title: str, body: str, **frame) -> Response:
    frame.setdefault("active", "manga")
    frame.setdefault("heading", "Манга")
    frame.setdefault("top", phone.mode_switch())
    return phone.page(title, body, **frame)


def _hidden(req: Request, manga: M.Manga) -> Response | None:
    """A title the mode hides: said, «Показать всё», back to the list; None — show it."""
    from webui import content_mode
    top = MG._top(M.manga_root(req.settings), manga)
    found = MG._verdict(top)
    if content_mode.shows(found.hentai):
        return None
    now = content_mode.mode()
    why = ("Включён режим «SFW», а эта манга — хентай" if now == "sfw"
           else "Включён режим «NSFW» — видно только взрослое, а эта манга не помечена как хентай")
    body = (f'<section class="m-hello"><h2>Скрыто в режиме «{content_mode.LABELS[now]}»</h2><p>{esc(why)}.</p>'
            f'<p><button type="button" class="m-button" data-mode-set="all">Показать всё</button></p>'
            f'<p><a class="m-link" href="/m/manga">Вся манга</a></p></section>')
    return _page(req, "Скрыто", body, back="/m/manga")


def _no_folder(root: str) -> str:
    if not root or not Path(root).is_dir():
        return ('<div class="m-empty"><h2>Папки манги нет</h2><p>Папку с мангой выбирают на компьютере: «Манга» или '
                '«Настройки → Папки»; если она на отдельном диске — он должен быть подключён.</p></div>')
    return ""


def _sections(show: str, query: str = "") -> str:
    links = []
    for key, label, href in SECTIONS:
        mark = ' aria-current="page"' if key == show else ""
        links.append(f'<a class="m-chip" href="{esc(href)}"{mark}>{esc(label)}</a>')
    return f'<nav class="m-chips" aria-label="Разделы манги">{"".join(links)}</nav>'


def _search(query: str, sort: str) -> str:
    keep = f'<input type="hidden" name="sort" value="{esc(sort)}">' if sort != "newest" else ""
    return (f'<form class="m-search" id="m-search" action="/m/manga" method="get" role="search">{keep}'
            f'<label class="m-field">{phone.svg(phone.SEARCH, size=18, width=2)}<span class="m-sr">Найти мангу</span>'
            f'<input type="search" name="q" value="{esc(query)}" placeholder="Название, тег, художник" '
            f'autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="search"></label></form>')


def _sort_select(sort: str, query: str) -> str:
    options = "".join(f'<option value="{k}"{" selected" if k == sort else ""}>{esc(v)}</option>'
                      for k, v in MG.SORTS.items())
    hidden = f'<input type="hidden" name="q" value="{esc(query)}">' if query else ""
    return (f'<form class="m-order" action="/m/manga" method="get">{hidden}<span class="m-sr">Порядок</span>'
            f'<select name="sort" data-submit aria-label="Порядок">{options}</select>'
            f'{phone.svg(phone.DOWN, size=14, width=2)}</form>')


def _bar(done: int, total: int) -> str:
    return (f'<span class="m-bar" aria-hidden="true"><span style="width:{100 * done / max(1, total):.0f}%"></span></span>'
            if done else "")


# ── what was read ────────────────────────────────────────────────────────────────────────────────────────────────
def where_read(root: str, top: M.Manga) -> dict:
    """Of a title (a manga or a series): {"target" — the manga or the chapter to open, "page" (from 1), "said" — the
    button's words, "done" — pages or chapters read, "total", "at" — when; "started" — anything read at all}.
    (v5.44.124) The computer's «Вся манга» goes by the same rules: ``manga.where_read`` — here with the chapters'
    titles in the words, as the phone has said them («Продолжить: Long Saga — 2, стр. 3»)."""
    return MG.where_read(top, label=lambda chapter: chapter.title)


def _reader_href(target: M.Manga, page: int = 1) -> str:
    """(v5.44.117) The page named always: the reader without ``p`` opens where the manga was left — these links know
    the page they mean («Продолжить», «Читать снова»)."""
    return f"/m/manga/r/{target.id}?p={max(1, int(page))}"


# ── /m/manga ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _cover_card(manga: M.Manga) -> str:
    badges = []
    lang = MG._language(manga)
    if lang:
        badges.append(f'<span class="m-badge m-badge-top">{esc(lang)}</span>')
    sub = (f"{len(manga.chapters)} гл. · {manga.page_count} стр." if manga.is_series else f"{manga.page_count} стр.")
    return (f'<a class="m-card" href="/m/manga/g/{manga.id}"><span class="m-cover">'
            f'<img src="{MG.cover_src(manga)}" alt="" loading="lazy" decoding="async">{"".join(badges)}</span>'
            f'<span class="m-card-title">{esc(manga.title)}</span><span class="m-card-sub">{esc(sub)}</span></a>')


def _set_card(tile) -> str:
    first = tile.set.parts[0]
    count = len(tile.set.parts)
    return (f'<a class="m-card" href="/m/manga/s/{tile.id}"><span class="m-cover">'
            f'<img src="{MG.cover_src(first)}" alt="" loading="lazy" decoding="async">'
            f'<span class="m-badge">⧉ {count}</span></span><span class="m-card-title">{esc(tile.title)}</span>'
            f'<span class="m-card-sub">{count} {MG._parts_word(count)} · {tile.page_count} стр.</span></a>')


def _shelf_card(root: str, top: M.Manga) -> str:
    where = where_read(root, top)
    sub = (f"стр. {where['page']} из {len(where['target'].pages)}" if not top.is_series
           else f"{where['target'].title}")
    return (f'<a class="m-shelf-card" href="{esc(_reader_href(where["target"], where["page"]))}">'
            f'<span class="m-cover"><img src="{MG.cover_src(top)}" alt="" loading="lazy" decoding="async"></span>'
            f'{_bar(where["done"], where["total"]) or _bar(1, 10 ** 9)}'
            f'<span class="m-card-title">{esc(top.title)}</span><span class="m-card-sub">{esc(sub)}</span></a>')


def index(req: Request) -> Response:
    root, items = MG._library(req, refresh=bool(req.arg("refresh")))
    query = req.arg("q").strip()
    sort = req.arg("sort", "newest")
    sort = sort if sort in MG.SORTS else "newest"
    missing = _no_folder(root)
    if missing:
        return _page(req, "Манга", _search(query, sort) + _sections("all") + missing,
                     top=phone.get_link() + phone.mode_switch())
    visible = MG._visible(items)
    found = [m for m in visible if MG._matches(m, query)] if query else list(visible)
    if not query and MG._sets_on():
        sets = MG._shown_sets(items, visible)
        inside = {p.id for s in sets for p in s.parts}
        found = [m for m in found if m.id not in inside] + [MG._Tile(s) for s in sets]
    found = MG._sorted(found, sort)
    page_no = req.int_arg("page", 1, lo=1)
    pages = max(1, (len(found) + PER_PAGE - 1) // PER_PAGE)
    page_no = min(page_no, pages)
    shown = found[(page_no - 1) * PER_PAGE: page_no * PER_PAGE]
    cards = "".join(_set_card(m) if isinstance(m, MG._Tile) else _cover_card(m) for m in shown)
    more = url("/m/manga", q=query, sort=sort, page=page_no + 1) if page_no < pages else ""
    if req.arg("part"):
        return Response.json({"ok": True, "html": cards, "more": more})
    parts = [_search(query, sort), _sections("" if query else "all")]
    if not query and page_no == 1:
        reading = [m for m in visible if M.PROGRESS.title(m.id)["at"]]
        reading.sort(key=lambda m: -M.PROGRESS.title(m.id)["at"])
        if reading:
            parts.append('<section class="m-shelf" aria-labelledby="m-go-on"><h2 id="m-go-on">Продолжить чтение</h2>'
                         '<div class="m-shelf-row">' + "".join(_shelf_card(root, m) for m in reading[:SHELF])
                         + "</div></section>")
    if shown:
        earlier = (f'<p class="m-earlier"><a href="{esc(url("/m/manga", q=query, sort=sort, page=page_no - 1))}">'
                   f'Показать предыдущие</a></p>' if page_no > 1 else "")
        parts.append(f'<div class="m-count"><h2 class="m-count-title">{"Найдено" if query else "Вся манга"} '
                     f'<span>{len(found)}</span></h2>{_sort_select(sort, query)}</div>{earlier}'
                     f'<div class="m-cards" data-grid>{cards}</div>'
                     + (f'<a class="m-more" href="{esc(more)}" data-more>Показать ещё</a>' if more else ""))
    elif query:
        parts.append(f'<div class="m-empty"><h2>Ничего не нашлось</h2><p>По запросу «{esc(query)}» манги нет.</p></div>')
    elif items:
        from webui import content_mode
        now = content_mode.mode()
        why = "в папке только хентай" if now == "sfw" else "хентая в папке нет"
        parts.append(f'<div class="m-empty"><h2>Здесь пусто</h2><p>В режиме «{content_mode.LABELS[now]}» {why}.</p>'
                     f'<p><button type="button" class="m-button" data-mode-set="all">Показать всё</button></p></div>')
    else:
        parts.append('<div class="m-empty"><h2>Манги пока нет</h2><p>Скачайте её по ссылке или положите в папку '
                     'манги на компьютере.</p>'
                     '<p><a class="m-button" href="/m/get">Скачать по ссылке</a></p></div>')
    return _page(req, query or "Манга", "".join(parts), top=phone.get_link() + phone.mode_switch())


def random_title(req: Request) -> Response:
    _root, items = MG._library(req)
    items = MG._visible(items)
    return Response.redirect(f"/m/manga/g/{random.choice(items).id}" if items else "/m/manga")


def tag_index(req: Request) -> Response:
    """The tags (artists, characters, parodies) of the manga in the folder, the most used first; a filter field."""
    kind = req.params["kind"]
    title, groups, none = MG.INDEX_TITLES[kind]
    root, items = MG._library(req)
    missing = _no_folder(root)
    if missing:
        return _page(req, title, _sections(kind) + missing)
    entries = sorted(((n, tag, group) for (group, tag), n in MG._counts(MG._visible(items)).items()
                      if group in groups and tag), key=lambda e: (-e[0], M.natural_key(e[1])))
    if not entries:
        body = f'<div class="m-empty"><h2>{esc(title)}</h2><p>У манги в папке пока нет {esc(none)}.</p></div>'
        return _page(req, title, _sections(kind) + body)
    chips = "".join(f'<a class="m-tag m-cat-{esc(MG.INDEX_TYPES[group][0])}" '
                    f'href="{esc(url("/m/manga", q=f"{group}:{tag}"))}" data-filter-text="{esc(tag.lower())}">'
                    f'{esc(tag.replace("_", " "))}<span class="m-tag-count">{n}</span></a>' for n, tag, group in entries)
    body = (_sections(kind)
            + f'<div class="m-search"><label class="m-field">{phone.svg(phone.SEARCH, size=18, width=2)}'
              f'<span class="m-sr">Найти среди них</span><input type="search" placeholder="Найти среди {len(entries)}" '
              f'autocomplete="off" autocapitalize="off" spellcheck="false" data-filter="m-tag-index"></label></div>'
            + f'<div class="m-tag-list m-pad m-tag-index" id="m-tag-index">{chips}</div>')
    return _page(req, title, body)


# ── /m/manga/g/<id>, /m/manga/s/<id> ─────────────────────────────────────────────────────────────────────────────
def _tag_rows(manga: M.Manga) -> str:
    rows = []
    for group in M.GROUP_ORDER:
        tags = manga.groups.get(group) or []
        if not tags:
            continue
        kind = MG.INDEX_TYPES.get(group, ("general", ""))[0]
        chips = "".join(f'<a class="m-tag m-cat-{esc(kind)}" href="{esc(url("/m/manga", q=f"{group}:{t}"))}">'
                        f'{esc(t.replace("_", " "))}</a>' for t in tags)
        rows.append(f'<div class="m-tag-row"><span class="m-tag-kind">{esc(M.GROUP_TITLES[group])}</span>'
                    f'<div class="m-tag-list">{chips}</div></div>')
    return f'<div class="m-tags m-pad">{"".join(rows)}</div>' if rows else ""


def title_page(req: Request) -> Response:
    root, items = MG._library(req)
    manga = M.LIBRARY.get(root, req.params["id"]) if root else None
    if manga is None:
        body = ('<div class="m-empty"><h2>Такой манги нет</h2><p>Возможно, её переименовали или убрали из папки.</p>'
                '<p><a class="m-link" href="/m/manga?refresh=1">Перечитать папку манги</a></p></div>')
        return _page(req, "Нет такой манги", body, status=404, back="/m/manga", heading="Вся манга")
    hidden = _hidden(req, manga)
    if hidden is not None:
        return hidden
    series = M.LIBRARY.get(root, manga.parent_id) if manga.parent_id else None
    top = series or manga
    # a chapter's page: from its own first page / where it stopped (the computer's rule — ``manga.where_read``)
    where = MG.where_read(top, part=manga) if series is not None else where_read(root, top)
    facts = [f"{manga.page_count} стр."]
    if manga.is_series:
        facts.insert(0, f"{len(manga.chapters)} гл.")
    lang = MG._language(manga)
    if lang:
        facts.insert(0, lang)
    from webui.pages import phone_manhwa as PH
    if manga.is_series:             # (v5.44.126) «Прочитано N из M глав» and its bar — the phone's «Манхва»
        bar = PH.read_line(where["done"], where["total"])
    else:
        bar = (f'<span class="m-bar m-bar-always" aria-hidden="true"><span style="width:'
               f'{100 * where["done"] / max(1, where["total"]):.0f}%"></span></span>' if where["started"] else "")
    hero = (f'<section class="m-hero"><a class="m-cover m-hero-cover" href="{esc(_reader_href(where["target"], where["page"]))}">'
            f'<img src="{MG.cover_src(manga)}" alt=""></a><div class="m-hero-about">'
            + (f'<p class="m-alt"><a class="m-link" href="/m/manga/g/{series.id}">← {esc(series.title)}</a></p>'
               if series else "")
            + f'<h1>{esc(manga.title)}</h1>'
            + (f'<p class="m-alt">{esc(manga.title_japanese)}</p>' if manga.title_japanese else "")
            + f'<p class="m-facts">{"".join(f"<span>{esc(f)}</span>" for f in facts)}</p>'
            + bar + "</div></section>")
    first = manga.chapters[0] if manga.is_series else manga
    from webui.pages import manhwa as MH       # (v5.44.119) a title downloaded from a site: «Докачать» — its one page
    more = MH.home_of("manga", MH.site_of(top.path) if Path(top.path).is_dir() else {}, top.title, phone=True)
    buttons = (f'<div class="m-go"><a class="m-button m-go-main" href="{esc(_reader_href(where["target"], where["page"]))}">'
               f'{phone.svg(phone.PLAY, size=18, width=2)}<span>{esc(where["said"])}</span></a>'
               + (f'<a class="m-button m-quiet" href="/m/manga/r/{first.id}?p=1">Читать с начала</a>'
                  if where["started"] and where["said"] != "Читать снова" else "")
               + (f'<a class="m-button m-quiet" href="{esc(more)}" data-title-page>Докачать</a>' if more else "")
               + "</div>")
    if manga.is_series:             # (v5.44.126) the chapters of the phone's «Манхва»: the newest first, the order
        state = M.PROGRESS.title(top.id)
        extra = (f'<div class="m-sub-line"><h2 class="m-sub">Главы <span>{len(manga.chapters)}</span></h2>'
                 f'<button type="button" class="mh-order m-order-button" data-action="mh-order" data-order="desc">'
                 f'Сначала первые</button></div>'
                 f'<ol class="mh-chapters m-chapters" data-order="desc" aria-label="Главы">'
                 f'{_chapter_rows(manga, state)}</ol>')
    else:
        thumbs = "".join(f'<a class="m-page-thumb" href="/m/manga/r/{manga.id}?p={n}"><img src="/manga/thumb/{manga.id}/'
                         f'{n}" alt="Страница {n}" loading="lazy" decoding="async"><span>{n}</span></a>'
                         for n in range(1, len(manga.pages) + 1))
        extra = (f'<h2 class="m-sub">Страницы <span>{len(manga.pages)}</span></h2>'
                 f'<div class="m-page-thumbs">{thumbs}</div>')
    related = ""
    if not manga.parent_id:
        visible = MG._visible(items)
        found = MG._set_of(items, manga.id, visible)
        if found is not None:
            number = next((n for n, p in enumerate(found.parts, 1) if p.id == manga.id), 0)
            others = "".join(_cover_card(p) for p in found.parts if p.id != manga.id)
            related = (f'<h2 class="m-sub">Сборка: <a class="m-link" href="/m/manga/s/{found.id}">{esc(found.title)}</a> '
                       f'<span>часть {number} из {len(found.parts)}</span></h2><div class="m-cards">{others}</div>')
    # (v5.44.122) «Это хентай» / «Это не хентай» — as on the computer's page (a chapter — its series)
    mark = phone.mark_box("manga", top.id, MG._verdict(top))
    # (v5.44.126) «Скачано с …» of a title «Скачать» brought — among its facts, as the phone's «Манхва» says it
    came_from = PH.from_site(top.path) if Path(top.path).is_dir() else ""
    facts_list = f'<dl class="m-dl">{came_from}</dl>' if came_from else ""
    body = (f'{hero}{buttons}{mark}{_tag_rows(manga)}{facts_list}{extra}{related}')
    return _page(req, manga.title, body, back=f"/m/manga/g/{series.id}" if series else "/m/manga",
                 heading="Вся манга" if not series else series.title,
                 scripts=("manhwa",) if manga.is_series else ())      # the ticks and the order: manhwa.js


def _chapter_rows(series: M.Manga, state: dict) -> str:
    """(v5.44.126) The chapters of a series — the rows of the phone's «Манхва» (``phone_manhwa.chapter_row``), the
    newest first: its pages, or «Вы остановились здесь · стр. 2 из 3»; where and when it came («с MangaLib ·
    сегодня») and «новая» (its ``chapter.json``: ``manhwa.arrival``); the tick «прочитана» by hand — the computer's API
    with ``kind`` "manga" (``M.PROGRESS``: what the computer's page shows too)."""
    from webui.pages import manhwa as MH, phone_manhwa as PH
    home = MH.home_site(series.path) if Path(series.path).is_dir() else ""
    now = time.time()
    rows = []
    for chapter in reversed(series.chapters):
        read = chapter.id in state["read"]
        here = chapter.id == state["last"] and not read
        total = len(chapter.pages)
        notes = [MH.here_words(MG._page_of(state, chapter.id), total) if here else f"{total} стр."]
        came, fresh = MH.arrival(chapter.info, MH.came_of(chapter.info, chapter.mtime), home, read=read, here=here,
                                 now=now)
        if came:
            notes.append(came)
        rows.append(PH.chapter_row(chapter.id, f"/m/manga/r/{chapter.id}", chapter.title, notes=notes, read=read,
                                   here=here, fresh=fresh, kind="manga"))
    return "".join(rows)


def set_page(req: Request) -> Response:
    root, items = MG._library(req)
    found = next((s for s in MG._sets(items) if s.id == req.params["id"]), None) if root else None
    if found is None:
        body = ('<div class="m-empty"><h2>Такой сборки нет</h2><p>Её части переименовали, убрали из папки или из '
                'сборки.</p><p><a class="m-link" href="/m/manga">Вся манга</a></p></div>')
        return _page(req, "Нет такой сборки", body, status=404, back="/m/manga", heading="Вся манга")
    ids = {p.id for p in MG._visible(found.parts)}
    shown = [p for p in found.parts if p.id in ids]
    if not shown:
        return _hidden(req, found.parts[0]) or Response.redirect("/m/manga")
    pages = sum(p.page_count for p in shown)
    first = shown[0].chapters[0] if shown[0].is_series else shown[0]
    body = (f'<section class="m-hello m-set-head"><h2>{esc(found.title)}</h2><p class="m-muted">{len(shown)} '
            f'{MG._parts_word(len(shown))} · {pages} стр.</p><p><a class="m-button" href="/m/manga/r/{first.id}?p=1">'
            f'Читать с первой части</a></p></section><div class="m-cards">{"".join(_cover_card(p) for p in shown)}</div>')
    return _page(req, found.title, body, back="/m/manga", heading="Сборка")


# ── /m/manga/r/<id> — (v5.44.117) the one reader of the program (webui/pages/reader.py) in the phone's frame ───────
def reader(req: Request) -> Response:
    root, _items = MG._library(req)
    manga = M.LIBRARY.get(root, req.params["id"]) if root else None
    if manga is None:
        return Response.redirect("/m/manga")
    if manga.is_series:
        return Response.redirect(f"/m/manga/g/{manga.id}")
    hidden = _hidden(req, manga)
    if hidden is not None:
        return hidden
    if not manga.pages:
        return Response.redirect(f"/m/manga/g/{manga.id}")
    return MG.show(req, manga, phone=True)
