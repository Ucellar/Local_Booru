"""(v5.44.66) «Манхва» on the owner's phone — the sketch «Local Booru на телефоне» («Манхва — тайтл», «Лента манхвы»).

* ``/m/manhwa`` (:func:`index`) — the search, «Продолжить чтение» (the titles being read, the last first: their next
  chapter), all the titles in three columns with how much of each is read;
* ``/m/manhwa/t/<id>`` (:func:`title_page`) — the cover, the names, the type, year and status, «Прочитано N из M»,
  «Продолжить: Глава 57» and «С начала», the tabs «Главная» (the description, genres, tags, facts) and «Главы» (newest
  first; a chapter of another site and when it came, «новая» for a few days, «Вы остановились здесь · стр. 12 из 40»,
  a tick by hand);
* ``/m/manhwa/r/<chapter id>`` (:func:`reader`) — the strip of the computer's reader (``static/manhwa.js``: the place
  kept, the chapter read to its end ticked, the next chapter coming by itself below, the bar hiding while the strip is
  read down) in the phone's bars: on top — back, «Том · Глава» opening the list of chapters, the line of how much of
  the chapter is read; at the bottom — the chapters before and after.

The data is the computer's tab (``webui/pages/manhwa.py``: the folder, what was read, the mode «SFW / Всё / NSFW»); the
downloading, its switches and the marks «Это хентай» stay on the computer.
"""
from __future__ import annotations

import time
from pathlib import Path

from core import manhwa_library as H
from webui.html import esc, url
from webui.pages import manhwa as MH, phone
from webui.server import Request, Response

NEW_FOR = 3 * 86400            # a chapter that came this many seconds ago and is not read: «новая»
SHELF = 10


def register(router) -> None:
    router.add("GET", r"/m/manhwa", index)
    # (v5.44.105) /m/manhwa/feed — «Лента» of the computer in the phone's frame: webui/pages/catalog.py
    router.add("GET", r"/m/manhwa/t/(?P<id>[0-9a-f]{12})", title_page)
    router.add("GET", r"/m/manhwa/r/(?P<id>[0-9a-f]{12})", reader)


def _hidden(req: Request, title: H.Title) -> Response | None:
    """The title the mode hides: said, «Показать всё», back to the list; None — show it."""
    from core import content_marks as CM
    from webui import content_mode
    found = CM.manhwa_verdict(title)
    if content_mode.shows(found.hentai):
        return None
    now = content_mode.mode()
    why = ("Включён режим «SFW», а эта манхва — хентай" if now == "sfw"
           else "Включён режим «NSFW» — видно только взрослое, а эта манхва не помечена как хентай")
    body = (f'<section class="m-hello"><h2>Скрыто в режиме «{content_mode.LABELS[now]}»</h2><p>{esc(why)}.</p>'
            f'<p><button type="button" class="m-button" data-mode-set="all">Показать всё</button></p>'
            f'<p><a class="m-link" href="/m/manhwa">Вся манхва</a></p></section>')
    return phone.page("Скрыто", body, active="manhwa", heading="Манхва", back="/m/manhwa", top=phone.mode_switch())


def _percent(done: int, total: int) -> str:
    return f"{100 * done / max(1, total):.0f}%"


def _bar(done: int, total: int) -> str:
    return (f'<span class="m-bar" aria-hidden="true"><span style="width:{_percent(done, total)}"></span></span>'
            if done else "")


def _card(title: H.Title, *, shelf: bool = False) -> str:
    state = MH._read_state(title)
    total = len(title.chapters)
    done = len(state["read"])
    if shelf:
        target, _said = MH._continue(title, state)
        return (f'<a class="m-shelf-card" href="/m/manhwa/r/{target.id}">'
                f'<span class="m-cover"><img src="/manhwa/cover/{title.id}" alt="" loading="lazy" decoding="async">'
                f'</span>{_bar(done, total) or _bar(1, 10 ** 9)}<span class="m-card-title">{esc(title.title)}</span>'
                f'<span class="m-card-sub">{esc(target.label)}</span></a>')
    kind = str(title.info.get("type") or "").strip()
    badge = f'<span class="m-badge m-badge-top">{esc(kind[:14])}</span>' if kind else ""
    sub = (f"{done} из {total} {MH._words(total, 'главы', 'глав', 'глав')}" if done
           else f"{total} {MH._words(total, 'глава', 'главы', 'глав')}")
    return (f'<a class="m-card" href="/m/manhwa/t/{title.id}">'
            f'<span class="m-cover"><img src="/manhwa/cover/{title.id}" alt="" loading="lazy" decoding="async">{badge}'
            f'</span>{_bar(done, total)}<span class="m-card-title">{esc(title.title)}</span>'
            f'<span class="m-card-sub">{esc(sub)}</span></a>')


def _search(query: str, sort: str) -> str:
    keep = f'<input type="hidden" name="sort" value="{esc(sort)}">' if sort != "newest" else ""
    return (f'<form class="m-search" id="m-search" action="/m/manhwa" method="get" role="search">{keep}'
            f'<label class="m-field">{phone.svg(phone.SEARCH, size=18, width=2)}<span class="m-sr">Найти манхву</span>'
            f'<input type="search" name="q" value="{esc(query)}" placeholder="Название, жанр или тег" autocomplete="off" '
            f'autocapitalize="off" spellcheck="false" enterkeyhint="search"></label></form>')


def _sort_select(sort: str, query: str) -> str:
    options = "".join(f'<option value="{k}"{" selected" if k == sort else ""}>{esc(v)}</option>'
                      for k, v in MH.SORTS.items())
    hidden = f'<input type="hidden" name="q" value="{esc(query)}">' if query else ""
    return (f'<form class="m-order" action="/m/manhwa" method="get">{hidden}<span class="m-sr">Порядок</span>'
            f'<select name="sort" data-submit aria-label="Порядок">{options}</select>'
            f'{phone.svg(phone.DOWN, size=14, width=2)}</form>')


def _nothing(req: Request, query: str, every: list) -> str:
    from webui import content_mode
    if query:
        return (f'<div class="m-empty"><h2>Ничего не нашлось</h2><p>По запросу «{esc(query)}» манхвы нет.</p></div>')
    if every:
        now = content_mode.mode()
        why = "в папке только хентай" if now == "sfw" else "хентая в папке нет"
        return (f'<div class="m-empty"><h2>Здесь пусто</h2><p>В режиме «{content_mode.LABELS[now]}» {why}.</p>'
                f'<p><button type="button" class="m-button" data-mode-set="all">Показать всё</button></p></div>')
    return ('<div class="m-empty"><h2>Манхвы пока нет</h2><p>Скачайте её по ссылке на тайтл — Remanga, ReComics, '
            'MangaLib, MangaBuff, ReadManga, Desu. Папку манхвы выбирают на компьютере: «Настройки → Папки».</p>'
            '<p><a class="m-button" href="/m/get">Скачать по ссылке</a></p></div>')


def index(req: Request) -> Response:
    every = MH._library(req, refresh=bool(req.arg("refresh")))
    items = MH._visible(every)
    query = req.arg("q").strip()
    sort = req.arg("sort", "newest")
    sort = sort if sort in MH.SORTS else "newest"
    found = MH._sorted([t for t in items if MH._matches(t, query)] if query else list(items), sort)
    from webui.pages import catalog                 # (v5.44.105) «Вся манхва · Лента» — as the computer's tabs
    parts = [_search(query, sort), catalog.phone_chips("manhwa", "all")]
    if not query and found:
        reading = sorted((t for t in items if H.PROGRESS.title(t.id)["at"]),
                         key=lambda t: -H.PROGRESS.title(t.id)["at"])
        if reading:
            parts.append('<section class="m-shelf" aria-labelledby="m-go-on"><h2 id="m-go-on">Продолжить чтение</h2>'
                         '<div class="m-shelf-row">' + "".join(_card(t, shelf=True) for t in reading[:SHELF])
                         + "</div></section>")
    if found:
        parts.append(f'<div class="m-count"><h2 class="m-count-title">{"Найдено" if query else "Вся манхва"} '
                     f'<span>{len(found)}</span></h2>{_sort_select(sort, query)}</div>'
                     f'<div class="m-cards">{"".join(_card(t) for t in found)}</div>')
    else:
        parts.append(_nothing(req, query, every))
    return phone.page(query or "Манхва", "".join(parts), active="manhwa", heading="Манхва",
                      top=phone.get_link() + phone.mode_switch())


# ── /m/manhwa/t/<id> ─────────────────────────────────────────────────────────────────────────────────────────────
def _day(stamp: float) -> str:
    if not stamp:
        return ""
    moment = time.localtime(stamp)
    today = time.localtime()
    days = (time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))
            - time.mktime((moment.tm_year, moment.tm_mon, moment.tm_mday, 0, 0, 0, 0, 0, -1))) / 86400
    if -0.5 < days < 0.5:
        return "сегодня"
    if 0.5 <= days < 1.5:
        return "вчера"
    from webui.pages.phone_gallery import MONTHS
    day = f"{moment.tm_mday} {MONTHS[moment.tm_mon - 1]}"
    return day if moment.tm_year == today.tm_year else f"{day} {moment.tm_year}"


def _came(chapter: H.Chapter) -> float:
    try:
        return float(chapter.info.get("downloaded_at") or 0) or float(chapter.mtime or 0)
    except (TypeError, ValueError):
        return float(chapter.mtime or 0)


def _home_site(title: H.Title) -> str:
    from core import manhwa_download as D
    return str(D.read_source(Path(title.path)).get("site") or "")


def _site_label(name: str) -> str:
    from core import manhwa_sources as S
    found = S.by_name(name)
    return found.label if found else name


def _chapter_rows(title: H.Title, state: dict) -> str:
    home = _home_site(title)
    last = state["last"]
    now = time.time()
    rows = []
    for chapter in reversed(title.chapters):            # newest first, as on Remanga
        read = chapter.id in state["read"]
        here = last is not None and chapter.id == last.id and not read
        notes = []
        if here:
            page, _part = H.PROGRESS.place(title.id, chapter.id)
            notes.append(f"Вы остановились здесь · стр. {min(page + 1, max(1, len(chapter.pages)))} из "
                         f"{len(chapter.pages)}")
        source = chapter.info.get("source") if isinstance(chapter.info.get("source"), dict) else {}
        site = str(source.get("site") or "")
        came = _came(chapter)
        if site and home and site != home:
            notes.append(f"с {_site_label(site)}" + (f" · {_day(came)}" if came else ""))
        elif not here and came and now - came < 30 * 86400:
            notes.append(_day(came))
        if chapter.number and chapter.name:
            notes.insert(0, chapter.name)
        fresh = not read and came and now - came < NEW_FOR
        cls = "mh-ch m-ch" + (" read" if read else "") + (" here" if here else "")
        rows.append(f'<li class="{cls}" data-chapter="{chapter.id}"><a href="/m/manhwa/r/{chapter.id}">'
                    f'<span class="m-ch-text"><span class="mh-ch-label">{esc(chapter.label)}</span>'
                    + (f'<span class="m-ch-note">{esc(" · ".join(notes))}</span>' if notes else "") + "</span>"
                    + ('<span class="m-new">новая</span>' if fresh else "") + "</a>"
                    f'<button type="button" class="mh-tick" data-action="mh-read" data-chapter="{chapter.id}" '
                    f'aria-pressed="{"true" if read else "false"}" aria-label="Прочитана">'
                    f'{phone.svg(phone.TICK, size=20, width=2.2)}</button></li>')
    return "".join(rows)


def title_page(req: Request) -> Response:
    title = MH._title(req, req.params["id"])
    if title is None:
        body = ('<div class="m-empty"><h2>Такой манхвы нет</h2><p>Возможно, папку переименовали или убрали.</p>'
                '<p><a class="m-link" href="/m/manhwa?refresh=1">Перечитать папку манхвы</a></p></div>')
        return phone.page("Нет такой манхвы", body, status=404, active="manhwa", heading="Манхва", back="/m/manhwa")
    hidden = _hidden(req, title)
    if hidden is not None:
        return hidden
    state = MH._read_state(title)
    target, said = MH._continue(title, state)
    total = len(title.chapters)
    done = len(state["read"])
    if not total:
        body = (f'<div class="m-empty"><h2>{esc(title.title)}</h2><p>В папке этой манхвы пока нет глав.</p></div>')
        return phone.page(title.title, body, active="manhwa", heading="Вся манхва", back="/m/manhwa")
    chips = "".join(f"<span>{esc(str(title.info.get(key)))}</span>" for key in ("type", "year", "status", "age")
                    if title.info.get(key))
    alt = " / ".join(esc(t) for t in title.alt_titles[:3])
    hero = (f'<section class="m-hero"><span class="m-cover m-hero-cover"><img src="/manhwa/cover/{title.id}?size=l" '
            f'alt=""></span><div class="m-hero-about"><h1>{esc(title.title)}</h1>'
            + (f'<p class="m-alt">{alt}</p>' if alt else "")
            + (f'<p class="m-facts">{chips}</p>' if chips else "")
            + f'<p class="m-read-line">Прочитано {done} из {total} {MH._words(total, "главы", "глав", "глав")}</p>'
            f'<span class="m-bar m-bar-always" aria-hidden="true"><span style="width:{_percent(done, total)}"></span>'
            f'</span></div></section>')
    buttons = (f'<div class="m-go"><a class="m-button m-go-main" href="/m/manhwa/r/{target.id}">'
               f'{phone.svg(phone.PLAY, size=18, width=2)}{esc(said)}</a>'
               + (f'<a class="m-button m-quiet" href="/m/manhwa/r/{title.chapters[0].id}?from=start">С начала</a>'
                  if state["last"] is not None and target.id != title.chapters[0].id else "") + "</div>")
    text = str(title.info.get("description") or "").strip()
    desc = "".join(f"<p>{esc(p)}</p>" for p in text.split("\n\n") if p.strip()) or '<p class="m-muted">Описания нет.</p>'
    chip_links = "".join(f'<a class="m-tag" href="{esc(url("/m/manhwa", q=v))}">{esc(v)}</a>'
                         for v in (title.list_of("genres") + title.list_of("tags"))[:40])
    facts = "".join(f'<dt>{esc(name)}</dt><dd>{esc(", ".join(title.list_of(key)) if isinstance(title.info.get(key), list) else title.info.get(key))}</dd>'
                    for key, name in MH.FACTS if title.info.get(key))
    home = _home_site(title)
    if home:
        facts += f'<dt>Скачано с</dt><dd>{esc(_site_label(home))}</dd>'
    facts += f"<dt>Глав</dt><dd>{total}</dd>"
    tab = "main" if req.arg("tab") == "main" else "chapters"
    tabs = (f'<nav class="m-tabs-line" role="tablist" aria-label="Страница тайтла">'
            f'<button type="button" role="tab" data-action="mh-tab" data-tab="main" '
            f'aria-selected="{"true" if tab == "main" else "false"}">Главная</button>'
            f'<button type="button" role="tab" data-action="mh-tab" data-tab="chapters" '
            f'aria-selected="{"true" if tab == "chapters" else "false"}">Главы <span>{total}</span></button>'
            f'<button type="button" class="mh-order m-order-button" data-action="mh-order" data-order="desc" '
            f'data-pane-tool="chapters"{" hidden" if tab != "chapters" else ""}>Сначала первые</button></nav>')
    main = (f'<div class="m-desc">{desc}</div>' + (f'<div class="m-tag-list m-pad">{chip_links}</div>' if chip_links else "")
            + f'<dl class="m-dl">{facts}</dl>')
    body = (f'<div class="mh-title m-title-page" data-title="{title.id}">{hero}{buttons}{tabs}'
            f'<section class="mh-pane" data-pane="main"{" hidden" if tab != "main" else ""}>{main}</section>'
            f'<section class="mh-pane" data-pane="chapters"{" hidden" if tab != "chapters" else ""}>'
            f'<ol class="mh-chapters m-chapters" data-order="desc" aria-label="Главы">{_chapter_rows(title, state)}'
            f'</ol></section></div>')
    return phone.page(title.title, body, active="manhwa", heading="Вся манхва", back="/m/manhwa",
                      top=phone.mode_switch(), scripts=("manhwa",))


# ── /m/manhwa/r/<chapter id> ─────────────────────────────────────────────────────────────────────────────────────
def _list(title: H.Title, current: H.Chapter, read: set) -> str:
    items = []
    for chapter in reversed(title.chapters):
        cls = ("current " if chapter.id == current.id else "") + ("read" if chapter.id in read else "")
        items.append(f'<li class="{cls.strip()}" data-chapter="{chapter.id}"><a href="/m/manhwa/r/{chapter.id}">'
                     f'<span>{esc(chapter.label)}</span><i aria-hidden="true">✓</i></a></li>')
    return "".join(items)


def reader(req: Request) -> Response:
    title, chapter = MH._chapter(req, req.params["id"])
    if title is None or chapter is None:
        return Response.redirect("/m/manhwa")
    hidden = _hidden(req, title)
    if hidden is not None:
        return hidden
    from webui.pages import manga_online as OP
    page, part = (0, 0.0) if req.arg("from") == "start" else H.PROGRESS.place(title.id, chapter.id)
    page = min(page, max(0, len(chapter.pages) - 1))
    before, after = title.neighbours(chapter)
    state = MH._read_state(title)
    now, chosen = MH.reading_mode(title, chapter)          # (v5.44.83) «Лента», «Справа налево», «Слева направо»
    modes = OP.modes_html(chosen, now)

    def step(cls: str, target, rel: str, words: str, icon: str, first: bool) -> str:
        inside = (f"{icon}<span>{words}</span>" if first else f"<span>{words}</span>{icon}")
        if target is None:
            return f'<a class="{cls}" aria-disabled="true">{inside}</a>'
        return f'<a class="{cls}" href="/m/manhwa/r/{target.id}" rel="{rel}">{inside}</a>'
    bar = (f'<div class="m-mh-bars" data-bar>'
           f'<div class="m-mh-top"><div class="m-mh-row">'
           f'<a class="m-icon" href="/m/manhwa/t/{title.id}" aria-label="К странице тайтла">'
           f'{phone.svg(phone.BACK, width=2)}</a>'
           f'<button type="button" class="mh-bar-where" data-action="{"mh-list" if now == "strip" else "ol-list"}" '
           f'aria-expanded="false" aria-controls="mh-list">{esc(chapter.label)}</button>'
           f'<span class="m-icon" aria-hidden="true"></span></div>'
           f'<span class="m-bar m-bar-thin" aria-hidden="true"><span data-chapter-read></span></span>{modes}'
           f'<div class="mh-list" id="mh-list" hidden><ol>{_list(title, chapter, state["read"])}</ol></div></div>'
           f'<nav class="m-mh-bottom" aria-label="Главы">'
           + step("mh-bar-prev", before, "prev", "Предыдущая", phone.svg(phone.BACK, size=18, width=2), True)
           + step("mh-bar-next", after, "next", "Следующая", phone.svg(phone.FORTH, size=18, width=2), False)
           + "</nav></div>")
    online_css = '<link rel="stylesheet" href="/static/online.css">'
    name = f"{chapter.label} — {title.title}"
    if now != "strip":                                     # the pages one at a time (static/online.js)
        body = OP.pager_html(
            mode=now, chapter=chapter.id, page=page, pages=[{"src": f"/manhwa/page/{chapter.id}/{n}", "w": w, "h": h}
                                                         for n, w, h in MH._pages(chapter)],
            progress_api="/api/manhwa/progress", mode_api=f"/api/manhwa/mode?id={title.id}",
            next_href=f"/m/manhwa/r/{after.id}" if after else "",
            prev_href=f"/m/manhwa/r/{before.id}" if before else "", next_label=after.label if after else "",
            label=chapter.label, bar=bar, back_href=f"/m/manhwa/t/{title.id}", back_words="К списку глав", phone=True)
        return phone.page(name, body, tabs=False, bare=True, kind="m-reader", scripts=("manhwa", "online"),
                          head=online_css)
    tail = (f'<div class="mh-tail" data-tail hidden><p>Это последняя глава.</p><p><a class="m-button m-quiet" '
            f'href="/m/manhwa/t/{title.id}">К списку глав</a></p></div>')
    body = (f'<div class="mh-reader m-mh-reader mh-modes" data-title="{title.id}" data-start="{chapter.id}" '
            f'data-page="{page}" data-part="{part}" data-base="/m/manhwa/r/" data-width="full" style="--mh-w:100%" '
            f'data-mode-api="/api/manhwa/mode?id={title.id}">'
            f'{bar}<div class="mh-strip">{MH._section(title, chapter, page)}</div>{tail}</div>')
    return phone.page(name, body, tabs=False, bare=True, kind="m-reader", scripts=("manhwa", "online"),
                      head=online_css)
