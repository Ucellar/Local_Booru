"""(v5.44.66) «Манхва» on the owner's phone — the sketch «Local Booru на телефоне» («Манхва — тайтл», «Лента манхвы»).

* ``/m/manhwa`` (:func:`index`) — the search, «Продолжить чтение» (the titles being read, the last first: their next
  chapter), all the titles in three columns with how much of each is read;
* ``/m/manhwa/t/<id>`` (:func:`title_page`) — the cover, the names, the type, year and status, «Прочитано N из M»,
  «Продолжить: Глава 57» and «Читать с начала», the tabs «Главная» (the description, genres, tags, facts) and «Главы»
  (newest first; a chapter of another site and when it came, «новая» for a few days, «Вы остановились здесь · стр. 12
  из 40», a tick by hand);
* ``/m/manhwa/r/<chapter id>`` (:func:`reader`) — (v5.44.117) the program's one reader (``webui/pages/reader.py``,
  ``static/reader.js``; ``manhwa.show(phone=True)``): on top — «‹ К тайтлу», «‹ Глава ›» (the chapter's name opens the
  list of chapters), where the pages are from; at the bottom — «Страница N из M» with its slider and «Как читать»
  (left to right, right to left, «Прокруткой» — the strip of the computer: the place kept, the chapter read to its
  end ticked, the next chapter coming by itself below, the panels hiding while it is read down); a tap in the middle —
  the panels shown or hidden.

The data is the computer's tab (``webui/pages/manhwa.py``: the folder, what was read, the mode «SFW / Всё / NSFW»); the
downloading and its switches stay on the computer; (v5.44.122) «Это хентай» / «Это не хентай» — here too
(``phone.mark_box``: the computer's API).
"""
from __future__ import annotations

import time

from core import manhwa_library as H
from webui.html import esc, url
from webui.pages import manhwa as MH, phone
from webui.server import Request, Response

NEW_FOR = MH.NEW_FOR           # a chapter that came this many seconds ago and is not read: «новая» (manhwa.arrival)
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
                f'<span class="m-cover"><img src="{esc(MH.cover_src(title))}" alt="" loading="lazy" decoding="async">'
                f'</span>{_bar(done, total) or _bar(1, 10 ** 9)}<span class="m-card-title">{esc(title.title)}</span>'
                f'<span class="m-card-sub">{esc(target.label)}</span></a>')
    kind = str(title.info.get("type") or "").strip()
    badge = f'<span class="m-badge m-badge-top">{esc(kind[:14])}</span>' if kind else ""
    sub = (f"{done} из {total} {MH._words(total, 'главы', 'глав', 'глав')}" if done
           else f"{total} {MH._words(total, 'глава', 'главы', 'глав')}")
    return (f'<a class="m-card" href="/m/manhwa/t/{title.id}">'
            f'<span class="m-cover"><img src="{esc(MH.cover_src(title))}" alt="" loading="lazy" decoding="async">{badge}'
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
# (v5.44.126) when a chapter came, the site of a title, «новая» — one rule with the computer and the manga's pages
# (manhwa.arrival, came_of, day_of, home_site, site_label)
_came = MH._came                 # when a chapter came (the reader's «новая» too)


def _home_site(title: H.Title) -> str:
    return MH.home_site(title.path)


def chapter_row(chapter_id: str, href: str, label: str, *, notes: list = (), read: bool = False, here: bool = False,
                fresh: bool = False, kind: str = "") -> str:
    """A chapter of a title's page of the phone: its label, ``notes`` under it («Возвращение · с MangaLib · сегодня»,
    «Вы остановились здесь · стр. 2 из 3»), «новая», the tick «прочитана» by hand (``manhwa.js`` ``mh-read``).
    (v5.44.126) The manga's series too (``phone_manga``: ``kind`` "manga" — its library's memory of reading)."""
    cls = "mh-ch m-ch" + (" read" if read else "") + (" here" if here else "")
    where = f' data-kind="{esc(kind)}"' if kind else ""
    return (f'<li class="{cls}" data-chapter="{esc(chapter_id)}"><a href="{esc(href)}">'
            f'<span class="m-ch-text"><span class="mh-ch-label">{esc(label)}</span>'
            + (f'<span class="m-ch-note">{esc(" · ".join(notes))}</span>' if notes else "") + "</span>"
            + ('<span class="m-new">новая</span>' if fresh else "") + "</a>"
            f'<button type="button" class="mh-tick" data-action="mh-read" data-chapter="{esc(chapter_id)}"{where} '
            f'aria-pressed="{"true" if read else "false"}" aria-label="Прочитана">'
            f'{phone.svg(phone.TICK, size=20, width=2.2)}</button></li>')


def from_site(folder) -> str:
    """«Скачано с Remanga» among a title's facts (``<dt>``/``<dd>`` of its ``m-dl``): the site its folder was downloaded
    from (``.lb/source.json``); "" — a title the program did not download.  (v5.44.126) The manga's too."""
    home = MH.home_site(folder)
    return f'<dt>Скачано с</dt><dd>{esc(MH.site_label(home))}</dd>' if home else ""


def read_line(done: int, total: int) -> str:
    """«Прочитано N из M глав» and its bar under a title's names — (v5.44.126) a series of «Манга» too."""
    return (f'<p class="m-read-line">Прочитано {done} из {total} {MH._words(total, "главы", "глав", "глав")}</p>'
            f'<span class="m-bar m-bar-always" aria-hidden="true"><span style="width:{_percent(done, total)}"></span>'
            f'</span>')


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
            notes.append(MH.here_words(page + 1, len(chapter.pages)))
        came, fresh = MH.arrival(chapter.info, _came(chapter), home, read=read, here=here, now=now)
        if came:
            notes.append(came)
        if chapter.number and chapter.name:
            notes.insert(0, chapter.name)
        rows.append(chapter_row(chapter.id, f"/m/manhwa/r/{chapter.id}", chapter.label, notes=notes, read=read,
                                here=here, fresh=fresh))
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
    hero = (f'<section class="m-hero"><span class="m-cover m-hero-cover"><img src="{esc(MH.cover_src(title, "l"))}" '
            f'alt=""></span><div class="m-hero-about"><h1>{esc(title.title)}</h1>'
            + (f'<p class="m-alt">{alt}</p>' if alt else "")
            + (f'<p class="m-facts">{chips}</p>' if chips else "")
            + read_line(done, total) + '</div></section>')
    # (v5.44.119) a title downloaded from a site: «Докачать» — its one page (as «Докачать сейчас» of the computer)
    more = MH.home_of("manhwa", MH.site_of(title.path), title.title, phone=True)
    buttons = (f'<div class="m-go"><a class="m-button m-go-main" href="/m/manhwa/r/{target.id}">'
               f'{phone.svg(phone.PLAY, size=18, width=2)}{esc(said)}</a>'
               + (f'<a class="m-button m-quiet" href="/m/manhwa/r/{title.chapters[0].id}?from=start">'
                  f'Читать с начала</a>'           # (v5.44.116) the computer's words (until then «С начала»)
                  if state["last"] is not None and target.id != title.chapters[0].id else "")
               + (f'<a class="m-button m-quiet" href="{esc(more)}" data-title-page>Докачать</a>' if more else "")
               + "</div>")
    from core import content_marks as CM               # (v5.44.122) «Это хентай» / «Это не хентай», as the computer's
    buttons += phone.mark_box("manhwa", title.id, CM.manhwa_verdict(title))
    text = str(title.info.get("description") or "").strip()
    desc = "".join(f"<p>{esc(p)}</p>" for p in text.split("\n\n") if p.strip()) or '<p class="m-muted">Описания нет.</p>'
    chip_links = "".join(f'<a class="m-tag" href="{esc(url("/m/manhwa", q=v))}">{esc(v)}</a>'
                         for v in (title.list_of("genres") + title.list_of("tags"))[:40])
    facts = "".join(f'<dt>{esc(name)}</dt><dd>{esc(", ".join(title.list_of(key)) if isinstance(title.info.get(key), list) else title.info.get(key))}</dd>'
                    for key, name in MH.FACTS if title.info.get(key))
    facts += from_site(title.path)
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


# ── /m/manhwa/r/<chapter id> — (v5.44.117) the one reader of the program (webui/pages/reader.py) ──────────────────
def reader(req: Request) -> Response:
    title, chapter = MH._chapter(req, req.params["id"])
    if title is None or chapter is None:
        return Response.redirect("/m/manhwa")
    hidden = _hidden(req, title)
    if hidden is not None:
        return hidden
    if not chapter.pages:
        return Response.redirect(f"/m/manhwa/t/{title.id}")
    return MH.show(req, title, chapter, phone=True)
