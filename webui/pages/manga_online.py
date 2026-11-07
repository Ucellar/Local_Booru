"""(v5.44.82) «Читать» a title of the sites without downloading it (``core/manga_online.py``): the program's own reader
— the strip of «Манхва» (``static/manhwa.js``: the next chapter coming by itself below, the place kept, the bar hiding
while the strip is read) — with the site's pictures through the program; the site is not opened.

* ``/online?url=<the title's link>&ch=<n>`` (:func:`reader`; ``&branch=`` — the card's translators) — on the computer,
  the frame of «Манхва»; ``/m/online`` (:func:`phone_reader`) — the phone's bars (``/online`` on the phone goes there —
  ``phone.TWINS``).  ``n`` — the chapter's tile on the card (from 1).
* ``/api/online/chapter`` — the next chapter for the strip (as ``/api/manhwa/chapter/<id>`` answers); ``/api/online/pic``
  — a page's picture (a media path: ``server.MEDIA_PATHS``); ``/api/online/progress`` — the place (the last chapter of
  the title, its page); ``/api/online/last`` — it, for the card's «Продолжить».
* «SFW»: a title the site marks as erotica is not shown.
* (as the friend's reader Yomi: «лента (манхва), страницы справа налево / слева направо, автовыбор режима») the way a
  chapter is read (``manga_online.MODES``): the strip, or the pages one at a time (``static/online.js``: the sides of
  the page turn it — right to left for manga, a tap in the middle hides the bars, a double tap zooms, the slider
  jumps, the next pages come ahead); «авто» — ``manga_online.auto_mode``; the owner's choice per title —
  ``/api/online/mode`` (``PROGRESS.put_mode``).
* (v5.44.110; the owner, 7.11.2026: «на компе просмотр манги с ленты пиздец какой не удобный я думал ты сделаешь такой
  же как в проге») on the computer the pages one at a time are the reader of «Манга» for its downloaded manga
  (``webui/pages/manga.reader``, :func:`_book_body`): a page per address (``&p=``; ``last`` — the chapter's last), the
  bars of pages above and below it (« ‹ the page's number › », the chapter, «К тайтлу»), a click on its left third —
  back, elsewhere — on, ← → (``booru.js``), the strip of the chapter's pages under it, beside it or none («Лента
  страниц» — the folder's reader's choice), after the last page — the next chapter; a manga in the frame of «Манга».
  The strip's pictures — ``/api/online/thumb`` (small copies of the pages the program has).  The phone keeps its own.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from urllib.parse import quote

from webui.html import esc
from webui.server import Request, Response

MAX_URL = 2000


def register(router) -> None:
    router.add("GET", r"/online", reader)
    router.add("GET", r"/m/online", phone_reader)
    router.add("GET", r"/api/online/chapter", chapter_json)
    router.add("GET", r"/api/online/pic", picture)
    router.add("GET", r"/api/online/thumb", thumb)          # (v5.44.110) a page in the strip of the pages
    router.add("POST", r"/api/online/progress", progress)
    router.add("GET", r"/api/online/last", last)
    router.add("POST", r"/api/online/mode", mode)


MODE_WORDS = (("", "Авто"), ("strip", "Лента"), ("rtl", "Справа налево"), ("ltr", "Слева направо"))


def _args(req: Request) -> tuple:
    url = str(req.arg("url") or "").strip()[:MAX_URL]
    branch = str(req.arg("branch") or "").strip()[:40]
    return url, branch


def _q(url: str, branch: str) -> str:
    return "url=" + quote(url, safe="") + (f"&branch={quote(branch, safe='')}" if branch else "")


def _hidden(reading) -> bool:
    """The mode hides the title: «SFW» — the program takes it for hentai (``content_marks.manhwa_reason``: the site
    calls it erotica, a genre or tag of hentai, a site of hentai) — (v5.44.103) not «18+» alone: «Поднятие уровня в
    одиночку» is 18+ on Remanga, «Лента» and the title's page show it in «SFW», the reader refused it; «NSFW» — a title
    that is not hentai."""
    from types import SimpleNamespace
    from core import content_marks
    from webui import content_mode
    adult = bool(content_marks.manhwa_reason(SimpleNamespace(info=reading.title.info())))
    return not content_mode.shows(adult)


def _hidden_words() -> str:
    from webui import content_mode
    if content_mode.mode() == "nsfw":
        return "Этот тайтл не хентай, а включён режим «NSFW» — переключите на «Всё», чтобы читать"
    return "Этот тайтл — хентай, а включён режим «SFW» — переключите на «Всё», чтобы читать"


def _open(req: Request):
    """(the reading, n, the error page's words) of the request."""
    from core import manga_online as O
    url, branch = _args(req)
    if not url:
        return None, 0, "Не указан тайтл"
    try:
        n = max(1, int(req.arg("ch") or "1"))
    except ValueError:
        n = 1
    try:
        reading = O.ONLINE.title(url, req.settings, branch)
    except O.OnlineError as exc:
        return None, n, str(exc)
    if _hidden(reading):
        return None, n, _hidden_words()
    if not reading.readable(n):                          # a paid chapter nobody has free: the next one that is
        n = reading.neighbour(n, 1) or reading.neighbour(n, -1) or n
    return reading, n, ""


def _pages(reading, n: int, req: Request) -> list:
    """[{src, w, h}] of chapter ``n``: the addresses of the program, never the site's."""
    from core import manga_online as O
    _r, pages = O.ONLINE.chapter(reading.url, n, req.settings, reading.branch)
    q = _q(reading.url, reading.branch)
    return [{"src": f"/api/online/pic?{q}&ch={n}&p={k}", "w": max(0, int(p.width or 0)), "h": max(0, int(p.height or 0))}
            for k, p in enumerate(pages.pictures, 1)]


def _mode(req: Request, reading, n: int) -> tuple:
    """(the way the chapter is read, the owner's own choice or "")."""
    from core import manga_online as O
    chosen = str(O.PROGRESS.of(reading.url).get("mode") or "")
    if chosen in O.MODES:
        return chosen, chosen
    _r, pages = O.ONLINE.chapter(reading.url, n, req.settings, reading.branch)
    return O.auto_mode(reading, pages.pictures), ""


def modes_html(chosen: str, now: str) -> str:
    """The switch of the way it is read: «Авто» and the three ways (both readers: the sites' and the folder's)."""
    out = []
    for key, words in MODE_WORDS:
        pressed = "true" if key == chosen else "false"
        hint = f' title="Сейчас: {dict(MODE_WORDS).get(now, now)}"' if not key else ""
        out.append(f'<button type="button" data-action="ol-mode" data-mode="{key}" aria-pressed="{pressed}"{hint}>'
                   f'{esc(words)}</button>')
    return f'<div class="ol-modes" role="group" aria-label="Как читать">{"".join(out)}</div>'


def _section(reading, n: int, pages: list) -> str:
    imgs = []
    for k, p in enumerate(pages, 1):
        size = f' width="{p["w"]}" height="{p["h"]}"' if p["w"] > 0 and p["h"] > 0 else ""
        lazy = "" if k <= 3 else ' loading="lazy"'
        imgs.append(f'<img class="mh-page" id="c{n}-p{k}" src="{esc(p["src"])}"{size} alt="" decoding="async"{lazy}>')
    before, after = reading.neighbour(n, -1), reading.neighbour(n, 1)
    return (f'<section class="mh-chapter" data-chapter="{n}" data-label="{esc(reading.label(n))}" '
            f'data-prev="{before or ""}" data-next="{after or ""}">'
            f'<h2 class="mh-chapter-head">{esc(reading.full(n))}</h2>{"".join(imgs)}</section>')


def _list(reading, current: int, base: str) -> str:
    items = []
    for n in range(len(reading.takes), 0, -1):
        cls = "current" if n == current else ""
        if reading.readable(n):
            items.append(f'<li class="{cls}" data-chapter="{n}"><a href="{esc(base)}{n}"><span>'
                         f'{esc(reading.label(n))}</span><i aria-hidden="true">✓</i></a></li>')
        else:
            items.append(f'<li class="paid" data-chapter="{n}"><span>{esc(reading.label(n))} — платная</span></li>')
    return "".join(items)


def _get_link(reading, phone: bool = False) -> str:
    """The page of this title — (v5.44.102) its page in the tab of its kind (``webui/pages/title.py``), with the
    translators read and its name; (v5.44.105) on the phone — the phone's page of the title (until then its card of
    «Скачать по ссылке»)."""
    from webui.pages import title as T
    return T.href(getattr(reading.found, "kind", "") or "manhwa", reading.title.url,
                  branch=getattr(reading, "branch", "") or "", name=getattr(reading.title, "title", "") or "",
                  phone=phone)


def _problem(req: Request, words: str, *, phone: bool, reading=None, n: int = 0, hidden: bool = False) -> Response:
    """A chapter (or a title) that did not open: why, and the ways on — (v5.44.103) a chapter of a title: the next one
    that can be read and «К тайтлу» (its page — on the phone its card); a title the mode hides: «Показать всё»."""
    links = []
    if reading is not None:
        after = reading.neighbour(n, 1) if n else 0
        if after:
            links.append((f"{'/m/online' if phone else '/online'}?{_q(reading.url, reading.branch)}&ch={after}",
                          f"Следующая: {reading.label(after)}", True))
        links.append((_get_link(reading, phone), "К тайтлу", not after))
    if phone:
        from webui.pages import phone as P
        links = links or [("/m/get", "Скачать по ссылке", False)]
        body = (f'<div class="m-empty"><p>{esc(words)}</p><p>'
                + "".join(f'<a class="m-button{"" if main else " m-quiet"}" href="{esc(href)}">{esc(label)}</a> '
                          for href, label, main in links) + '</p></div>')
        return P.page("Не открылось", body, status=404)
    from webui import content_mode
    from webui.html import page_frame
    if hidden:
        ways = content_mode._actions('<a class="mh-btn" href="/manhwa/feed">К ленте</a>')
    else:
        links = links or [("/get", "Скачать по ссылке", False)]
        ways = "<p>" + "".join(f'<a class="mh-btn{" primary" if main else ""}" href="{esc(href)}">{esc(label)}</a> '
                               for href, label, main in links) + "</p>"
    body = f'<div class="mh-online-problem"><h1>Не открылось</h1><p>{esc(words)}</p>{ways}</div>'
    return Response.html(page_frame("Не открылось", body, active="manhwa", subnav=False, kind="manhwa"), status=404)


def _place(req: Request, reading, n: int, pages: list) -> tuple:
    """(page, part) where chapter ``n`` was left (``?from=start`` — its start)."""
    from core import manga_online as O
    place = O.PROGRESS.of(reading.url)
    page = int(place.get("page") or 0) if place.get("ch") == n and not place.get("done") else 0
    part = float(place.get("part") or 0) if page else 0.0
    if req.arg("from") == "start":
        page, part = 0, 0.0
    return min(page, max(0, len(pages) - 1)), part


def _bar(req: Request, reading, n: int, base: str, *, phone: bool, tools: str, list_action: str) -> str:
    """The reader's bars: back to the title, the chapter (its list), the chapters before and after, ``tools``."""
    before, after = reading.neighbour(n, -1), reading.neighbour(n, 1)
    get_link = esc(_get_link(reading, phone))
    chapters = f'<div class="mh-list" id="mh-list" hidden><ol>{_list(reading, n, base)}</ol></div>'
    where = (f'<button type="button" class="mh-bar-where" data-action="{list_action}" aria-expanded="false" '
             f'aria-controls="mh-list">{esc(reading.label(n))}</button>')
    if phone:
        from webui.pages import phone as P

        def step(cls: str, target: int, rel: str, words: str, icon: str, first: bool) -> str:
            inside = f"{icon}<span>{words}</span>" if first else f"<span>{words}</span>{icon}"
            if not target:
                return f'<a class="{cls}" aria-disabled="true">{inside}</a>'
            return f'<a class="{cls}" href="{esc(base)}{target}" rel="{rel}">{inside}</a>'
        card = get_link                                  # (v5.44.105) the phone's page of the title
        return (f'<div class="m-mh-bars" data-bar><div class="m-mh-top"><div class="m-mh-row">'
                f'<a class="m-icon" href="{card}" aria-label="К тайтлу">'
                f'{P.svg(P.BACK, width=2)}</a>{where}<span class="m-icon" aria-hidden="true"></span></div>'
                f'<span class="m-bar m-bar-thin" aria-hidden="true"><span data-chapter-read></span></span>'
                f'{tools}{chapters}</div><nav class="m-mh-bottom" aria-label="Главы">'
                + step("mh-bar-prev", before, "prev", "Предыдущая", P.svg(P.BACK, size=18, width=2), True)
                + step("mh-bar-next", after, "next", "Следующая", P.svg(P.FORTH, size=18, width=2), False)
                + "</nav></div>")
    prev_link = (f'<a class="mh-bar-prev" rel="prev" href="{esc(base)}{before}" title="Предыдущая глава" '
                 f'aria-label="Предыдущая глава">‹</a>' if before else
                 '<a class="mh-bar-prev" aria-disabled="true" aria-label="Предыдущей главы нет">‹</a>')
    next_link = (f'<a class="mh-bar-next" rel="next" href="{esc(base)}{after}" title="Следующая глава" '
                 f'aria-label="Следующая глава">›</a>' if after else
                 '<a class="mh-bar-next" aria-disabled="true" aria-label="Следующей главы нет">›</a>')
    return (f'<div class="mh-bar" data-bar><a class="mh-bar-back" href="{get_link}" title="К тайтлу — его главы, '
            f'скачать"><span aria-hidden="true">←</span> <span class="mh-bar-title">{esc(reading.title.title)}</span></a>'
            f'<div class="mh-bar-mid">{prev_link}{where}{next_link}</div>'
            f'<div class="mh-bar-tools">{tools}<a class="mh-online-get" href="{get_link}">Скачать</a></div>'
            f'{chapters}</div>')


def _strip_body(req: Request, reading, n: int, pages: list, modes: str, *, phone: bool) -> str:
    """The strip (``static/manhwa.js`` — as the reader of «Манхва»: the next chapter comes by itself below)."""
    q = _q(reading.url, reading.branch)
    base = ("/m/online?" if phone else "/online?") + q + "&ch="
    page, part = _place(req, reading, n, pages)
    data = (f'data-title="online" data-start="{n}" data-page="{page}" data-part="{part}" '
            f'data-base="{esc(base)}" data-chapter-api="{esc("/api/online/chapter?" + q + "&ch=")}" '
            f'data-progress="{esc("/api/online/progress?" + q)}" data-mode-api="{esc("/api/online/mode?" + q)}"')
    if phone:
        bar = _bar(req, reading, n, base, phone=True, tools=modes, list_action="mh-list")
        tail = (f'<div class="mh-tail" data-tail hidden><p>Это последняя глава на сайтах.</p><p><a class="m-button '
                f'm-quiet" href="{esc(_get_link(reading, True))}">К тайтлу</a></p></div>')
        return (f'<div class="mh-reader m-mh-reader mh-online" {data} data-width="full" style="--mh-w:100%">'
                f'{bar}<div class="mh-strip">{_section(reading, n, pages)}</div>{tail}</div>')
    from webui.pages import manhwa as MH
    width = MH._width()
    widths = "".join(f'<button type="button" data-action="mh-width" data-width="{key}" aria-pressed='
                     f'"{"true" if key == width else "false"}">{esc(label)}</button>' for key, label in MH.WIDTHS.items())
    tools = f'<div class="mh-widths" role="group" aria-label="Ширина ленты">{widths}</div>{modes}'
    bar = _bar(req, reading, n, base, phone=False, tools=tools, list_action="mh-list")
    tail = (f'<div class="mh-tail" data-tail hidden><p>Это последняя глава на сайтах.</p>'
            f'<p><a class="mh-btn" href="{esc(_get_link(reading))}">К тайтлу — скачать главы</a></p></div>')
    return (f'<div class="mh-reader mh-online" {data} style="--mh-w:{"100%" if width == "full" else width + "px"}" '
            f'data-width="{width}">{bar}<div class="mh-strip">{_section(reading, n, pages)}</div>{tail}</div>')


def pager_html(*, mode: str, chapter: str, page: int, pages: list, progress_api: str, mode_api: str,
               next_href: str, prev_href: str, next_label: str, label: str, bar: str, back_href: str,
               back_words: str, phone: bool) -> str:
    """(v5.44.83) The pages one at a time (``static/online.js``) — right to left (``rtl``) or left to right (``ltr``):
    the reader of a site's chapter and of a chapter in the folder of manhwa.  ``chapter`` — what the progress API is
    told; ``pages`` — [{src, w, h}]; ``bar`` — the reader's bar (its chapter list opens with «ol-list»)."""
    import json as _json
    total = len(pages)
    data = (f'data-mode="{mode}" data-chapter="{esc(str(chapter))}" data-page="{page}" data-total="{total}" '
            f'data-pages="{esc(_json.dumps(pages))}" data-progress="{esc(progress_api)}" '
            f'data-mode-api="{esc(mode_api)}" data-next="{esc(next_href)}" data-prev="{esc(prev_href)}" '
            f'data-label="{esc(label)}"')
    end = (f'<div class="ol-end" hidden><p class="ol-end-head">Глава закончилась</p>'
           + (f'<p><a class="ol-next mh-btn" href="{esc(next_href)}">{esc("Следующая: " + next_label)}</a></p>'
              if next_href else
              f'<p>Это последняя глава.</p><p><a class="mh-btn" href="{esc(back_href)}">{esc(back_words)}</a></p>')
           + '</div>')
    # right to left: the slider too, and the page's number on its right (as the friend's reader Yomi has it)
    slider = (f'<div class="ol-bottom"{" dir=rtl" if mode == "rtl" else ""}><span class="ol-at" aria-live="polite">'
              f'{page + 1}</span><input class="ol-slider" type="range" min="1" max="{max(1, total)}" '
              f'value="{page + 1}" aria-label="Страница"><span class="ol-total">{total}</span></div>')
    stage = (f'<div class="ol-stage" tabindex="0" aria-label="Страница — стрелки и щелчок по краям листают">'
             f'<img class="ol-page" alt="" decoding="async"></div>')
    return (f'<div class="ol-reader{" ol-phone" if phone else ""}" {data}>{bar}{stage}{end}{slider}'
            f'<p class="ol-problem" hidden role="alert"></p></div>')


def _pager_body(req: Request, reading, n: int, pages: list, mode: str, modes: str, *, phone: bool) -> str:
    """A site's chapter, the pages one at a time (:func:`pager_html`)."""
    q = _q(reading.url, reading.branch)
    base = ("/m/online?" if phone else "/online?") + q + "&ch="
    page, _part = _place(req, reading, n, pages)
    before, after = reading.neighbour(n, -1), reading.neighbour(n, 1)
    return pager_html(mode=mode, chapter=str(n), page=page, pages=pages, progress_api="/api/online/progress?" + q,
                      mode_api="/api/online/mode?" + q, next_href=base + str(after) if after else "",
                      prev_href=base + str(before) if before else "",
                      next_label=reading.label(after) if after else "", label=reading.label(n),
                      bar=_bar(req, reading, n, base, phone=phone, tools=modes, list_action="ol-list"),
                      back_href=_get_link(reading, phone), back_words="К тайтлу", phone=phone)


def _body(req: Request, reading, n: int, pages: list, *, phone: bool) -> tuple:
    """(the body, its way) of chapter ``n``; (v5.44.110) the pages one at a time on the computer — "book" (the reader of
    «Манга»)."""
    now, chosen = _mode(req, reading, n)
    if now != "strip" and not phone:
        return _book_body(req, reading, n, pages, chosen), "book"
    modes = modes_html(chosen, now)
    if now == "strip":
        return _strip_body(req, reading, n, pages, modes, phone=phone), now
    return _pager_body(req, reading, n, pages, now, modes, phone=phone), now


# ── (v5.44.110) the pages one at a time on the computer: the reader of «Манга» ─────────────────────────────────────
BOOK_MODES = (("", "Авто"), ("strip", "Лентой"), ("ltr", "По страницам"))
THUMB_WIDTH = 168                 # px of a page in the strip of pages (it shows them 84 px wide)
THUMBS_KEPT = 600                 # small copies kept in memory
_THUMBS: "OrderedDict[tuple, bytes]" = OrderedDict()
_THUMBS_LOCK = threading.Lock()


def book_modes_html(chosen: str) -> str:
    """«Как читать: Авто · Лентой · По страницам» under the reader of «Манга» («справа налево» of before — pages)."""
    pressed = "ltr" if chosen == "rtl" else chosen
    buttons = "".join(f'<button type="button" class="link{" active" if key == pressed else ""}" data-action="ol-mode" '
                      f'data-mode="{key}" aria-pressed="{"true" if key == pressed else "false"}">{esc(words)}</button>'
                      for key, words in BOOK_MODES)
    return f'<div class="strip-places reader-modes" role="group" aria-label="Как читать">Как читать: {buttons}</div>'


def _book_page(req: Request, reading, n: int, pages: list) -> int:
    """The page (from 1) of chapter ``n`` to show: ``&p=`` (``last`` — its last), else where it was left."""
    total = len(pages)
    asked = str(req.arg("p") or "").strip().lower()
    if asked == "last":
        return total
    if asked.isdigit():
        return max(1, min(int(asked), total))
    page, _part = _place(req, reading, n, pages)
    return max(1, min(page + 1, total))


def _book_body(req: Request, reading, n: int, pages: list, chosen: str) -> str:
    """(v5.44.110) A site's chapter, the pages one at a time, as the reader of «Манга» shows a manga of its folder
    (``webui/pages/manga.reader`` — the same markup, ``manga.css``, ``booru.js``): the page asked by its address."""
    from core import manga_online as O
    from webui import prefs
    from webui.pages import manga as MG
    total = len(pages)
    if not total:
        raise O.OnlineError("У главы нет страниц")
    p = _book_page(req, reading, n, pages)
    O.PROGRESS.put(reading.url, reading, n, p - 1, 0.0, p == total)      # the page open is what was read
    q = _q(reading.url, reading.branch)
    home = _get_link(reading)
    before, after = reading.neighbour(n, -1), reading.neighbour(n, 1)
    here = f"/online?{q}&ch={n}&p="
    prev_href = f"{here}{p - 1}" if p > 1 else (f"/online?{q}&ch={before}&p=last" if before else home)
    next_href = f"{here}{p + 1}" if p < total else (f"/online?{q}&ch={after}&p=1" if after else home)
    readable = [k for k in range(1, len(reading.takes) + 1) if reading.readable(k)]

    def nav(cls: str, chapters: bool) -> str:
        options = "".join(f'<option value="{k}"{" selected" if k == p else ""}>{k}</option>' for k in range(1, total + 1))
        choose = ""
        if chapters and len(readable) > 1:
            names = "".join(f'<option value="{k}"{" selected" if k == n else ""}>{esc(reading.label(k))}</option>'
                            for k in readable)
            choose = (f'<label class="reader-count reader-chapter">Глава <select data-action="goto-page" '
                      f'data-base="{esc(f"/online?{q}&p=1&ch=")}" aria-label="Глава">{names}</select></label>')
        return (f'<nav class="reader-nav {cls}" aria-label="Страницы">'
                f'<a href="{esc(here)}1" title="Первая страница" aria-label="Первая страница">«</a>'
                f'<a href="{esc(prev_href)}" rel="prev" title="Предыдущая (←)" aria-label="Предыдущая страница">‹</a>'
                f'<label class="reader-count"><select data-action="goto-page" data-base="{esc(here)}" '
                f'aria-label="Страница">{options}</select> / {total}</label>'
                f'<a href="{esc(next_href)}" rel="next" title="Следующая (→)" aria-label="Следующая страница">›</a>'
                f'<a href="{esc(here)}{total}" title="Последняя страница" aria-label="Последняя страница">»</a>'
                f'{choose}<a class="reader-home" href="{esc(home)}">К тайтлу</a></nav>')

    title = f"{reading.title.title} — {reading.label(n)}"
    image = (f'<div class="reader-stage loading" data-prev="{esc(prev_href)}" data-next="{esc(next_href)}">'
             f'<img class="reader-image" src="{esc(pages[p - 1]["src"])}" alt="Страница {p}"></div>'
             f'<div class="picture-problem" hidden role="alert"><p></p>'
             f'<button type="button" data-action="picture-retry">Загрузить заново</button></div>')
    keys = (f'<a hidden data-key="ArrowLeft" href="{esc(prev_href)}"></a>'
            f'<a hidden data-key="ArrowRight" href="{esc(next_href)}"></a>')
    ahead = "".join(f'<link rel="prefetch" href="{esc(pages[k - 1]["src"])}">' for k in range(p + 1, min(total, p + 2) + 1))
    place = str(prefs.load().get("manga_strip") or "bottom")
    place = place if place in MG.STRIP_PLACES else "bottom"
    strip = ""
    if place != "off":
        thumbs = "".join(
            f'<a class="strip-page{" current" if k == p else ""}" href="{esc(here)}{k}"{" aria-current=page" if k == p else ""}>'
            f'<img src="{esc(f"/api/online/thumb?{q}&ch={n}&p={k}")}" alt="Страница {k}" loading="lazy" decoding="async">'
            f'<span>{k}</span></a>' for k in range(1, total + 1))
        strip = f'<nav class="page-strip" aria-label="Страницы главы">{thumbs}</nav>'
    switch = "".join(
        f'<button type="button" class="link{" active" if key == place else ""}" data-action="manga-strip" '
        f'data-place="{key}"{" aria-pressed=true" if key == place else ""}>{label}</button>'
        for key, label in MG.STRIP_PLACES.items())
    places = f'<div class="strip-places" role="group" aria-label="Лента страниц">Лента страниц: {switch}</div>'
    return (f'<div class="reader online-book strip-{place}" data-reader data-mode-api="{esc("/api/online/mode?" + q)}" '
            f'data-chapter="{n}" data-page="{p}" data-total="{total}">{nav("at-top", True)}'
            f'<h1 class="reader-title">{esc(title)}</h1><div class="reader-body">{image}{strip}</div>'
            f'{nav("at-bottom", False)}{places}{book_modes_html(chosen)}{keys}{ahead}</div>')


def _book_frame(req: Request, reading, title: str, body: str) -> Response:
    """The reader of «Манга» in its tab's frame (a manga — by its type, also when the manga folder is not chosen and its
    chapters would go to the manhwa folder; the others — «Манхва»'s)."""
    from core.manhwa_download import kind_of
    from webui.html import page_frame
    if getattr(reading.found, "kind", "") == "manga" or kind_of(reading.title) == "manga":
        from webui.pages import manga as MG
        return Response.html(page_frame(title, body, active="manga", subnav=False, kind="manga-reader",
                                        top_search=MG._search_form("", "newest"), assets=("manga", "online"),
                                        **MG._prefs(req)))
    from webui.pages import manhwa as MH
    return Response.html(page_frame(title, body, active="manhwa", subnav=False, kind="manga-reader",
                                    top_search=MH._search_form(""), assets=("manga", "online")))


def _small(data: bytes) -> bytes | None:
    """A page's small copy for the strip of pages (JPEG :data:`THUMB_WIDTH` wide), None — the picture does not open."""
    import io
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            img.draft("RGB", (THUMB_WIDTH * 2, THUMB_WIDTH * 8))
            img = img.convert("RGB")
            img.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 6))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=82, optimize=True)
            return out.getvalue()
    except Exception:  # noqa: BLE001 - a picture Pillow does not open (AVIF): the page itself goes
        return None


def thumb(req: Request) -> Response:
    """(v5.44.110) A page of a site's chapter, small — for the strip of pages (the page fetched once by the program,
    kept as :func:`picture` keeps it; its small copy kept in memory)."""
    from core import manga_online as O
    url, branch = _args(req)
    try:
        n, p = int(req.arg("ch") or "0"), int(req.arg("p") or "0")
    except ValueError:
        return Response.not_found("Нет такой страницы")
    if not url or n < 1 or p < 1:
        return Response.not_found("Нет такой страницы")
    key = (url, branch, n, p)
    try:
        reading = O.ONLINE.title(url, req.settings, branch)
        if _hidden(reading):
            return Response.not_found("Скрыто режимом")
        with _THUMBS_LOCK:
            kept = _THUMBS.get(key)
            if kept is not None:
                _THUMBS.move_to_end(key)
        if kept is None:
            data, kind = O.ONLINE.picture(url, n, p, req.settings, branch)
            small = _small(data)
            if small is None:
                return Response(200, data, kind, {"Cache-Control": "private, max-age=3600"})
            with _THUMBS_LOCK:
                _THUMBS[key] = kept = small
                while len(_THUMBS) > THUMBS_KEPT:
                    _THUMBS.popitem(last=False)
    except O.OnlineError as exc:
        return Response(502, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
    return Response(200, kept, "image/jpeg", {"Cache-Control": "private, max-age=3600"})


def _read(req: Request, *, phone: bool) -> Response:
    from core import manga_online as O
    reading, n, words = _open(req)
    if reading is None:
        return _problem(req, words, phone=phone, hidden=words == _hidden_words())
    try:
        pages = _pages(reading, n, req)
        body, now = _body(req, reading, n, pages, phone=phone)
    except O.OnlineError as exc:
        return _problem(req, str(exc), phone=phone, reading=reading, n=n)
    title = f"{reading.label(n)} — {reading.title.title}"
    if now == "book":
        return _book_frame(req, reading, title, body)
    if phone:
        from webui.pages import phone as P
        return P.page(title, body, tabs=False, bare=True, kind="m-reader", scripts=("manhwa", "online"),
                      head='<link rel="stylesheet" href="/static/online.css">')
    from webui.html import page_frame
    from webui.pages import manhwa as MH
    return Response.html(page_frame(title, body, active="manhwa", subnav=False, kind="manhwa-reader",
                                    top_search=MH._search_form(""), assets=("manhwa", "online")))


def reader(req: Request) -> Response:
    return _read(req, phone=False)


def phone_reader(req: Request) -> Response:
    return _read(req, phone=True)


def chapter_json(req: Request) -> Response:
    """The next chapter for the strip: as ``/api/manhwa/chapter/<id>`` answers (``static/manhwa.js``)."""
    from core import manga_online as O
    reading, n, words = _open(req)
    if reading is None:
        return Response.json({"ok": False, "error": words}, 404)
    try:
        pages = _pages(reading, n, req)
    except O.OnlineError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 502)
    before, after = reading.neighbour(n, -1), reading.neighbour(n, 1)
    return Response.json({"ok": True, "id": str(n), "title": "online", "label": reading.label(n),
                          "full": reading.full(n), "pages": pages, "prev": str(before or ""),
                          "next": str(after or ""), "read": False})


def picture(req: Request) -> Response:
    """A page's picture: fetched by the program from the site (its own Referer), kept a while."""
    from core import manga_online as O
    url, branch = _args(req)
    try:
        n, p = int(req.arg("ch") or "0"), int(req.arg("p") or "0")
    except ValueError:
        return Response.not_found("Нет такой страницы")
    if not url or n < 1 or p < 1:
        return Response.not_found("Нет такой страницы")
    try:
        reading = O.ONLINE.title(url, req.settings, branch)
        if _hidden(reading):
            return Response.not_found("Скрыто режимом")
        data, kind = O.ONLINE.picture(url, n, p, req.settings, branch)
    except O.OnlineError as exc:
        return Response(502, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
    return Response(200, data, kind, {"Cache-Control": "private, max-age=3600"})


def progress(req: Request) -> Response:
    """The place in a title read without downloading: its chapter (``chapter`` — n), page, how much of it."""
    from core import manga_online as O
    url, branch = _args(req)
    body = req.json()
    try:
        n = int(body.get("chapter") or 0)
        page = int(body.get("page") or 0)
        part = float(body.get("part") or 0)
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Не то место"}, 400)
    try:
        reading = O.ONLINE.title(url, req.settings, branch)
    except O.OnlineError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 404)
    if not 1 <= n <= len(reading.takes):
        return Response.json({"ok": False, "error": "Такой главы нет"}, 404)
    O.PROGRESS.put(url, reading, n, page, part, bool(body.get("done")))
    return Response.json({"ok": True})


def mode(req: Request) -> Response:
    """{mode} — the way this title is read ("" — «авто»)."""
    from core import manga_online as O
    url, _branch = _args(req)
    value = str(req.json().get("mode") or "")
    if value and value not in O.MODES:
        return Response.json({"ok": False, "error": "Такого способа нет"}, 400)
    try:
        O.PROGRESS.put_mode(url, value)
    except O.OnlineError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 400)
    return Response.json({"ok": True, "mode": value})


def last(req: Request) -> Response:
    """The last chapter read of a title (the card's «Продолжить»): {ch, label, done} — or {}."""
    from core import manga_online as O
    url, _branch = _args(req)
    place = O.PROGRESS.of(url) if url else {}
    return Response.json({"ok": True, "last": {k: place[k] for k in ("ch", "label", "done", "page") if k in place}})
