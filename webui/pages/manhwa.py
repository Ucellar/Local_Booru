"""«Манхва» (v5.44.59): long strips read by scrolling, as on Remanga (``core/manhwa_library.py``).

* ``/manhwa`` — the titles of the manhwa folder: «Продолжить чтение» (the titles being read, the last first) and all
  of them, with a search, the sorts and how much of each is read;
* ``/manhwa/t/<id>`` — a title, as on Remanga: the cover, the names, «Продолжить: Том 1 Глава 7» (or «Читать»),
  the tabs «Главная» (the description, genres, tags, facts) and «Главы» (newest first, read ones ticked, «Вы
  остановились здесь» at the last one read; (v5.44.126) when a chapter came, another site's «с MangaLib · сегодня»,
  «новая» — as the phone's: one row with a series of «Манга», :func:`chapter_row`, :func:`arrival`);
* ``/manhwa/r/<chapter id>`` — (v5.44.117) the one reader of the program (``webui/pages/reader.py``,
  ``static/reader.js``): by default the chapter as one strip (its slices glued without gaps, each with its size given,
  so the strip never jumps and the place found again is exact); the next chapter comes by itself below when the end
  is near (``/api/manhwa/chapter/<id>``), the address and the panel follow the chapter being read; the place is kept
  (``/api/manhwa/progress``: the page at the top of the window and how much of it is above) and a chapter read to its
  end is ticked; the panel hides while the strip is read down and comes back when it is scrolled up.  «По страницам»
  — the pages one at a time (``?p=`` — the page; the strip of their small copies — ``/manhwa/thumb/<id>/<n>``).

The owner (4.11.2026): «манхва должна переходить без кнопки с запоминанием где остановился и галочкой о том что
прочел эту главу сверху всплывающие меню с стрелочками перехода и номерном том/глава при нажатии вылезает список глав
при прокручивании в низ скрывается эта менюшка а при пролистывании вверх появляется».
"""
from __future__ import annotations

import time
from pathlib import Path

from core import manhwa_library as H
from webui.html import esc, page_frame, paginator, rows_attrs, rows_per_page, url
from webui.server import Request, Response

PER_PAGE = 30
ROWS_COOKIE = "lb_manhwa_pp"
COVERS = {"s": 360, "l": 720}                # px, the long side of a cover: on the list / on a title's page
SHELF = 6                                    # titles in «Продолжить чтение»
SORTS = {"newest": "Сначала новые", "title": "По названию", "read": "Недавно читал", "chapters": "Больше глав"}


def register(router) -> None:
    router.add("GET", r"/manhwa", index)
    router.add("GET", r"/manhwa/t/(?P<id>[0-9a-f]{12})", title_page)
    router.add("GET", r"/manhwa/r/(?P<id>[0-9a-f]{12})", reader)
    router.add("GET", r"/api/manhwa/feed-cover", feed_cover)
    router.add("GET", r"/manhwa/cover/(?P<id>[0-9a-f]{12})", cover)
    router.add("GET", r"/manhwa/page/(?P<id>[0-9a-f]{12})/(?P<n>\d+)", page_image)
    router.add("GET", r"/manhwa/thumb/(?P<id>[0-9a-f]{12})/(?P<n>\d+)", page_thumb)    # (v5.44.117) «Лента страниц»
    router.add("GET", r"/api/manhwa/chapter/(?P<id>[0-9a-f]{12})", chapter_json)
    router.add("POST", r"/api/manhwa/progress", progress)
    router.add("POST", r"/api/manhwa/read", mark_read)
    router.add("POST", r"/api/manhwa/mode", mode)
    router.add("POST", r"/api/manhwa/(?P<id>[0-9a-f]{12})/reveal", reveal)


# ── shared bits ──────────────────────────────────────────────────────────────────────────────────────────────────
def _search_form(query: str) -> str:
    return (f'<form class="top-search" action="/manhwa" method="get" role="search"><div class="search-row">'
            f'<input type="search" name="q" value="{esc(query)}" placeholder="Название, жанр или тег" autocomplete="off" '
            f'spellcheck="false" aria-label="Поиск манхвы"><button type="submit" class="search-go">Найти</button></div>'
            f'</form>')


# (v5.44.60) «Скачать» — manhwa_get.py; (v5.44.75) it is «Скачать → Манхва» of the menu's «Скачать» (get_hub.py);
# (v5.44.87) «Скачать → По ссылке» — one field for every kind (get_link.py)
TABS = (("all", "Вся манхва", "/manhwa"), ("feed", "Лента", "/manhwa/feed"))     # (v5.44.83) «Лента» — the sites'
# (v5.44.89) no «Скачать» here — the menu's «Скачать» alone (the owner, 6.11.2026)


def _frame(req: Request, title: str, body: str, *, kind: str = "manhwa", show: str = "", body_data=None,
           assets: tuple = ()) -> Response:
    """A page of the tab; ``assets`` — the stylesheets and scripts it takes instead of the tab's own (the reader's)."""
    tabs = tuple((label, href, key == show) for key, label, href in TABS) if show else ()
    return Response.html(page_frame(title, body, active="manhwa", subnav=False, kind=kind, tabs=tabs,
                                    top_search=_search_form(req.arg("q")), body_data=body_data, assets=assets))


def _root(req: Request) -> str:
    return H.manhwa_root(req.settings)


def _library(req: Request, *, refresh: bool = False) -> list:
    root = _root(req)
    return H.LIBRARY.items(root, refresh=refresh) if Path(root).is_dir() else []


def _title(req: Request, title_id: str) -> H.Title | None:
    root = _root(req)
    return H.LIBRARY.get(root, title_id) if Path(root).is_dir() else None


def _chapter(req: Request, chapter_id: str) -> tuple[H.Title | None, H.Chapter | None]:
    root = _root(req)
    return H.LIBRARY.chapter(root, chapter_id) if Path(root).is_dir() else (None, None)


# ── «SFW / Всё / NSFW» (webui/content_mode.py, core/content_marks.py) ─────────────────────────────────────────
def _visible(items: list, now: str | None = None) -> list:
    from core import content_marks as CM
    from webui import content_mode
    now = now or content_mode.mode()
    if now == "all":
        return items
    hand = CM.marks()["manhwa"]
    return [t for t in items if content_mode.shows(CM.manhwa_verdict(t, hand).hentai, now)]


def _hidden(req: Request, title: H.Title) -> Response | None:
    from core import content_marks as CM
    from webui import content_mode
    found = CM.manhwa_verdict(title)
    if content_mode.shows(found.hentai):
        return None
    body = content_mode.hidden_item(active="manhwa", what="эта манхва", reason=found.reason, hentai=found.hentai,
                                    back="/manhwa", back_label="Вся манхва")
    return _frame(req, "Скрыто", body, kind="mode-hidden")


def item_shown(settings: dict, item_id: str, now: str) -> bool | None:
    """Is a title (or the title of a chapter) shown in the mode ``now``; None — no such thing."""
    from core import content_marks as CM
    from webui import content_mode
    root = H.manhwa_root(settings)
    if not Path(root).is_dir():
        return None
    title = H.LIBRARY.get(root, item_id) or H.LIBRARY.chapter(root, item_id)[0]
    return None if title is None else content_mode.shows(CM.manhwa_verdict(title).hentai, now)


def find_title(settings: dict, item_id: str) -> H.Title | None:
    """The title a mark by hand is for (a chapter's — its title)."""
    root = H.manhwa_root(settings)
    if not Path(root).is_dir():
        return None
    return H.LIBRARY.get(root, item_id) or H.LIBRARY.chapter(root, item_id)[0]


# ── what was read ───────────────────────────────────────────────────────────────────────────────────────────────
def _read_state(title: H.Title) -> dict:
    """{"read": set of chapter ids, "last": the chapter read last (or None), "at": when}."""
    state = H.PROGRESS.title(title.id)
    read = {c for c in state["read"] if title.chapter(c) is not None}
    return {"read": read, "last": title.chapter(state["last"]) if state["last"] else None, "at": state["at"]}


def _continue(title: H.Title, state: dict) -> tuple[H.Chapter, str]:
    """Where «Продолжить» leads, and what it says: the chapter read last — or, read to its end, the next one."""
    last = state["last"]
    if last is None:
        return title.chapters[0], "Читать"
    if last.id in state["read"]:
        _before, after = title.neighbours(last)
        if after is not None:
            return after, f"Продолжить: {after.label}"
    return last, f"Продолжить: {last.label}"


def _words(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


# ── /manhwa ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _matches(title: H.Title, query: str) -> bool:
    words = title.words()
    for raw in query.lower().replace("ё", "е").replace("_", " ").split():
        neg = raw.startswith("-") and len(raw) > 1
        if ((raw[1:] if neg else raw) in words) == neg:
            return False
    return True


def _sorted(items: list, sort: str) -> list:
    if sort == "title":
        return sorted(items, key=lambda t: H.natural_key(t.title))
    if sort == "read":
        at = {t.id: H.PROGRESS.title(t.id)["at"] for t in items}
        return sorted(items, key=lambda t: (-at[t.id], -t.mtime))
    if sort == "chapters":
        return sorted(items, key=lambda t: (-len(t.chapters), H.natural_key(t.title)))
    return sorted(items, key=lambda t: -t.mtime)


def cover_src(title: H.Title, size: str = "") -> str:
    """(v5.44.123) The address of a title's cover with its picture's version: a cover chosen in the menu of covers is
    another picture under the same address (``cover.*`` of the folder) — the window does not keep the old one."""
    try:
        version = Path(title.cover).stat().st_mtime_ns if title.cover else 0
    except OSError:
        version = 0
    pairs = ([f"size={size}"] if size else []) + ([f"v={version}"] if version else [])
    return f"/manhwa/cover/{title.id}" + ("?" + "&".join(pairs) if pairs else "")


def card_html(href: str, cover: str, name: str, sub: str, *, done: int = 0, total: int = 0, badge: str = "") -> str:
    """A card of a title (the list, «Продолжить чтение»): its cover with its kind (``badge``) and how much of it is read
    (``done`` of ``total``), its name, ``sub`` under it.  (v5.44.124) «Вся манга» draws its shelf with it too."""
    bar = (f'<span class="mh-card-bar" style="--p:{100 * done / max(1, total):.0f}%" '
           f'title="Прочитано {done} из {total}"></span>' if done else "")
    kind = f'<span class="mh-card-type">{esc(badge[:14])}</span>' if badge else ""
    return (f'<a class="mh-card" href="{esc(href)}" title="{esc(name)}">'
            f'<span class="mh-card-pic"><img src="{esc(cover)}" alt="" loading="lazy" decoding="async">'
            f'{kind}{bar}</span><span class="mh-card-title">{esc(name)}</span>'
            f'<span class="mh-card-sub">{esc(sub)}</span></a>')


def _card(title: H.Title, *, shelf: bool = False) -> str:
    state = _read_state(title)
    total = len(title.chapters)
    target, _said = _continue(title, state)
    sub = target.label if shelf else f"{total} {_words(total, 'глава', 'главы', 'глав')}"
    href = f"/manhwa/r/{target.id}" if shelf else f"/manhwa/t/{title.id}"
    return card_html(href, cover_src(title), title.title, sub, done=len(state["read"]), total=total,
                     badge=str(title.info.get("type") or "").strip())


def reading_now(items: list, progress, limit: int = SHELF) -> list:
    """(v5.44.124) The titles of ``items`` being read, the one read last first, ``limit`` of them — «Продолжить чтение»
    of «Вся манхва» and «Вся манга» (``progress`` — the tab's memory of reading: ``PROGRESS`` of its library)."""
    at = {t.id: progress.title(t.id)["at"] for t in items}
    return sorted((t for t in items if at[t.id]), key=lambda t: -at[t.id])[:limit]


def shelf_html(cards: list) -> str:
    """(v5.44.124) The shelf «Продолжить чтение» over the list of «Вся манхва» and «Вся манга»: one block for both;
    nothing read — no shelf."""
    if not cards:
        return ""
    return ('<section class="mh-shelf"><h2>Продолжить чтение</h2><div class="mh-cards">' + "".join(cards)
            + "</div></section>")


def _no_titles(req: Request, root: str, query: str, items: list) -> str:
    from webui import content_mode
    if query:
        return f'<div class="mh-empty"><p>По запросу «{esc(query)}» манхвы нет.</p></div>'
    if items:                                        # there are titles, the mode hides them all
        now = content_mode.mode()
        why = "в папке только хентай" if now == "sfw" else "хентая в папке нет"
        return (f'<div class="mh-empty mode-empty"><p>В режиме «{content_mode.LABELS[now]}» здесь пусто: {why}.</p>'
                '<p><button type="button" class="mh-btn primary" data-action="content-mode" data-mode="all">Показать всё'
                '</button><span class="action-status" role="status"></span></p></div>')
    rules = ('<p class="hint">Одна папка — один тайтл, её подпапки (или архивы .zip/.cbz) — главы: «Том 1 Глава 7», '
             '«Глава 12», «12»; картинки главы — полосы ленты по порядку имён. Описание — <code>info.json</code> рядом, '
             'обложка — <code>cover.jpg</code>.</p>')
    where = (f'<p>Папки манхвы {"нет на месте" if str(req.settings.get("manhwa_root") or "").strip() else "ещё нет"}: '
             f'<code>{esc(root)}</code>.</p>' if not Path(root).is_dir()
             else f'<p>В папке манхвы пока ничего нет: <code>{esc(root)}</code>.</p>')
    return (f'<div class="mh-empty">{where}<p><a class="mh-btn primary" href="/get">Скачать манхву</a>'
            f'</p>{rules}<p class="hint">Другую папку можно выбрать в '
            f'<a href="/settings#set-folders">Настройки → Хранение и копии</a>.</p></div>')


def index(req: Request) -> Response:
    root = _root(req)
    every = _library(req, refresh=bool(req.arg("refresh")))
    items = _visible(every)
    query = req.arg("q").strip()
    sort = req.arg("sort", "newest")
    sort = sort if sort in SORTS else "newest"
    found = _sorted([t for t in items if _matches(t, query)] if query else list(items), sort)
    parts = []
    if not query and found:
        parts.append(shelf_html([_card(t, shelf=True) for t in reading_now(items, H.PROGRESS)]))
    per_page = rows_per_page(req, ROWS_COOKIE, PER_PAGE)
    pages = max(1, (len(found) + per_page - 1) // per_page)
    page = min(req.int_arg("page", 1, lo=1), pages)
    shown = found[(page - 1) * per_page: page * per_page]
    sorts = "".join(f'<a class="{"active" if key == sort else ""}" href="{esc(url("/manhwa", q=query, sort=key))}">'
                    f'{esc(label)}</a>' for key, label in SORTS.items())
    head = (f'<div class="mh-head"><h1>{"Поиск: " + esc(query) if query else "Вся манхва"} '
            f'<span class="muted">{len(found)}</span></h1><nav class="mh-sorts">{sorts}'
            f'<a href="{esc(url("/manhwa", q=query, sort=sort, refresh=1))}" title="Перечитать папку манхвы">Обновить</a>'
            f'</nav></div>')
    if shown:
        rows = rows_attrs(ROWS_COOKIE, PER_PAGE, per_page, len(found), page)
        grid = f'<div class="mh-cards"{rows}>' + "".join(_card(t) for t in shown) + "</div>"
        grid += paginator(page, pages, lambda n: url("/manhwa", q=query, sort=sort, page=n))
    else:
        grid = _no_titles(req, root, query, every)
    body = f'<div class="mh-index">{"".join(parts)}{head}{grid}</div>'
    return _frame(req, query or "Манхва", body, show="all")


# ── (v5.44.83) /manhwa/feed — «Лента»: the titles of the sites ──────────────────────────────────────────────────
# (v5.44.90) «Лента» — webui/pages/catalog.py (the sites together, with filters); (v5.44.105) the phone's — the same
def feed_cover(req: Request) -> Response:
    from core import manhwa_feed as F
    got = F.cover(req.arg("c"))
    if got is None:
        return Response.not_found("Обложки нет")
    return Response(200, got[0], got[1], {"Cache-Control": "private, max-age=3600"})


# ── /manhwa/t/<id> ──────────────────────────────────────────────────────────────────────────────────────────────
FACTS = (("type", "Тип"), ("status", "Статус"), ("year", "Выпуск"), ("age", "Возраст"), ("author", "Автор"),
         ("authors", "Автор"), ("artist", "Художник"), ("publisher", "Издатель"), ("translator", "Переводчик"))
PEOPLE = ("author", "authors", "artist")        # (v5.44.135) a name there — a search of the folder by it


def facts_html(title: H.Title, home: str = "/manhwa") -> str:
    """The facts of a title's description: one row of a name (``author``, else ``authors`` — as «Скачать» writes a
    site's authors); (v5.44.135) a person's name — a search of the folder by it (``home`` — the computer's list or the
    phone's)."""
    rows, said = [], set()
    for key, name in FACTS:
        value = title.info.get(key)
        if not value or name in said:
            continue
        said.add(name)
        values = title.list_of(key) if isinstance(value, list) else [str(value)]
        if key in PEOPLE:
            shown = ", ".join(f'<a href="{esc(url(home, q=v))}">{esc(v)}</a>' for v in values)
        else:
            shown = esc(", ".join(values))
        rows.append(f'<dt>{esc(name)}</dt><dd>{shown}</dd>')
    return "".join(rows)


def _chips(values: list, cls: str) -> str:
    return "".join(f'<a class="mh-chip {cls}" href="{esc(url("/manhwa", q=v))}">{esc(v)}</a>' for v in values[:40])


def _chapter_rows(title: H.Title, state: dict) -> str:
    home = home_site(title.path)
    now = time.time()
    rows = []
    last = state["last"]
    for chapter in reversed(title.chapters):              # newest first, as on Remanga
        read = chapter.id in state["read"]
        here = last is not None and chapter.id == last.id
        came, fresh = arrival(chapter.info, _came(chapter), home, read=read, here=here and not read, now=now)
        page, _part = H.PROGRESS.place(title.id, chapter.id) if here else (0, 0.0)
        rows.append(chapter_row(chapter.id, f"/manhwa/r/{chapter.id}", chapter.label,
                                name=chapter.name if chapter.number else "", read=read,
                                here=here_words(page + 1, len(chapter.pages), read=read) if here else "",
                                came=came, fresh=fresh))
    return "".join(rows)


# ── (v5.44.126) one chapter row, one rule of «новая»: «Манхва» and a series of «Манга», the computer and the phone ────
NEW_FOR = 3 * 86400            # a chapter that came this many seconds ago and is not read: «новая» (the phone's)
CAME_SAID = 30 * 86400         # …and the day it came under its name this long


def came_of(info: dict, mtime: float) -> float:
    """When a chapter came: ``downloaded_at`` of its ``chapter.json`` (a chapter the program downloaded), else the
    time of its folder (the reader's «новая» too)."""
    try:
        return float((info or {}).get("downloaded_at") or 0) or float(mtime or 0)
    except (TypeError, ValueError, AttributeError):
        return float(mtime or 0)


def day_of(stamp: float, now: "float | None" = None) -> str:
    """The day ``stamp`` is in words: «сегодня», «вчера», «7 ноября», «7 ноября 2025»."""
    if not stamp:
        return ""
    from webui.pages.reader import MONTHS
    moment = time.localtime(stamp)
    today = time.localtime(time.time() if now is None else now)
    days = (time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))
            - time.mktime((moment.tm_year, moment.tm_mon, moment.tm_mday, 0, 0, 0, 0, 0, -1))) / 86400
    if -0.5 < days < 0.5:
        return "сегодня"
    if 0.5 <= days < 1.5:
        return "вчера"
    day = f"{moment.tm_mday} {MONTHS[moment.tm_mon - 1]}"
    return day if moment.tm_year == today.tm_year else f"{day} {moment.tm_year}"


def site_label(name: str) -> str:
    """A site of manga as the pages name it («Remanga», «MangaLib»); one the program does not know — as written."""
    from core import manhwa_sources as S
    found = S.by_name(str(name or ""))
    return found.label if found else str(name or "")


def home_site(folder) -> str:
    """The site a title's folder was downloaded from (``.lb/source.json`` «site»: «Манхва» and «Манга» alike); "" — a
    title the program did not download."""
    from core import manhwa_download as D
    path = Path(str(folder or ""))
    if not (path / D.LB_DIR / D.SOURCE_FILE).is_file():
        return ""
    return str(D.read_source(path).get("site") or "")


def arrival(info: dict, came: float, home: str, *, read: bool, here: bool, now: float) -> tuple:
    """(where and when the chapter came in words, «новая») — one rule for the chapters of «Манхва» and «Манга», on the
    computer and on the phone (until 5.44.126 — the phone's manhwa alone): a chapter of another site than its title's
    (``chapter.json`` «source» — «Скачать» took it there: the title's site has it paid or not at all) — «с MangaLib ·
    сегодня»; else the day it came, for a month («7 ноября»; not beside «Вы остановились здесь» — ``here``); «новая» —
    came these three days and not read.  ``came`` — :func:`came_of`, ``home`` — :func:`home_site`."""
    source = (info or {}).get("source") if isinstance((info or {}).get("source"), dict) else {}
    site = str(source.get("site") or "")
    words = ""
    if site and home and site != home:
        words = f"с {site_label(site)}" + (f" · {day_of(came, now)}" if came else "")
    elif not here and came and now - came < CAME_SAID:
        words = day_of(came, now)
    return words, bool(not read and came and now - came < NEW_FOR)


def here_words(page: int, pages: int, *, read: bool = False) -> str:
    """«Вы остановились здесь · стр. 2 из 3» (``page`` from 1) of the chapter read last; read to its end — without the
    page (it opens from its start)."""
    if read or not pages:
        return "Вы остановились здесь"
    return f"Вы остановились здесь · стр. {min(max(1, int(page)), pages)} из {pages}"


def chapter_row(chapter_id: str, href: str, label: str, *, name: str = "", read: bool = False, here: str = "",
                came: str = "", fresh: bool = False, kind: str = "") -> str:
    """(v5.44.126) A chapter on a title's page of the computer — «Манхва» and a series of «Манга» (until then the
    manga's were covers): its label and its own name, ``here`` — «Вы остановились здесь · стр. 2 из 3»
    (:func:`here_words`), ``came`` — where and when it came (:func:`arrival`), «новая» (``fresh``), the tick «прочитана»
    by hand (``/api/manhwa/read``; ``kind`` "manga" — a chapter of the manga's library: its memory of reading)."""
    cls = "mh-ch" + (" read" if read else "") + (" here" if here else "")
    where = f' data-kind="{esc(kind)}"' if kind else ""
    tip = "Прочитана — снять отметку" if read else "Отметить прочитанной"
    return (f'<li class="{cls}" data-chapter="{esc(chapter_id)}"><a href="{esc(href)}">'
            f'<span class="mh-ch-label">{esc(label)}</span>'
            + (f'<span class="mh-ch-name">{esc(name)}</span>' if name else "")
            + (f'<span class="mh-here">{esc(here)}</span>' if here else "")
            + (f'<span class="mh-ch-came">{esc(came)}</span>' if came else "")
            + ('<span class="mh-new">новая</span>' if fresh else "")
            + f'</a><button type="button" class="mh-tick" data-action="mh-read" '
            f'data-chapter="{esc(chapter_id)}"{where} aria-pressed="{"true" if read else "false"}" title="{tip}">'
            f'<span aria-hidden="true">✓</span><span class="sr-only">Прочитана</span></button></li>')


def chapters_html(rows: str) -> str:
    """(v5.44.126) The chapters of a title's page of the computer — «Манхва», a series of «Манга»: the newest first, as
    on Remanga, «Сначала первые» turns them (``manhwa.js`` ``mh-order``)."""
    return (f'<div class="mh-ch-head"><button type="button" class="mh-order" data-action="mh-order" '
            f'data-order="desc">Сначала первые</button></div><ol class="mh-chapters" data-order="desc">{rows}</ol>')


def progress_html(done: int, total: int) -> str:
    """(v5.44.126) «Прочитано N из M глав» with its bar — a title of «Манхва», a series of «Манга»."""
    return (f'<div class="mh-progress" title="Прочитано {done} из {total}"><span style="--p:'
            f'{100 * done / max(1, total):.0f}%"></span></div><p class="mh-progress-text">Прочитано {done} из {total} '
            f'{_words(total, "главы", "глав", "глав")}</p>')


def title_page(req: Request) -> Response:
    title = _title(req, req.params["id"])
    if title is None:
        return _frame(req, "Нет такой манхвы", '<div class="mh-empty"><p>Такой манхвы нет — возможно, папку '
                      'переименовали или убрали.</p><p><a href="/manhwa?refresh=1">Перечитать папку манхвы</a></p></div>')
    hidden = _hidden(req, title)
    if hidden is not None:
        return hidden
    from core import content_marks as CM
    state = _read_state(title)
    target, said = _continue(title, state)
    total = len(title.chapters)
    done = len(state["read"])
    found = CM.manhwa_verdict(title)
    buttons = [f'<a class="mh-btn primary" href="/manhwa/r/{target.id}">{esc(said)}</a>']
    if state["last"] is not None and target.id != title.chapters[0].id:
        buttons.append(f'<a class="mh-btn" href="/manhwa/r/{title.chapters[0].id}?from=start">Читать с начала</a>')
    source = str(title.info.get("source_url") or "")
    if source.startswith(("http://", "https://")):
        from urllib.parse import urlparse
        site = (urlparse(source).hostname or "").removeprefix("www.")
        buttons.append(f'<a class="mh-btn" href="{esc(source)}" target="_blank" rel="noopener noreferrer">{esc(site or "На сайте")}</a>')
    buttons.append(f'<button type="button" class="mh-btn" data-action="mh-reveal" data-id="{title.id}">Показать в папке</button>')
    value = "safe" if found.hentai else "hentai"
    buttons.append(f'<button type="button" class="mh-btn" data-action="content-mark" data-kind="manhwa" '
                   f'data-id="{title.id}" data-value="{value}">{"Это не хентай" if found.hentai else "Это хентай"}</button>')
    chips = [esc(str(title.info.get(key))) for key in ("type", "year", "status", "age") if title.info.get(key)]
    alt = " / ".join(esc(t) for t in title.alt_titles[:4])
    progress = progress_html(done, total)
    hero = (f'<section class="mh-hero"><a class="mh-cover" href="/manhwa/r/{target.id}">'
            f'<img src="{esc(cover_src(title, "l"))}" alt=""></a><div class="mh-about">'
            f'<h1>{esc(title.title)}</h1>' + (f'<p class="mh-alt">{alt}</p>' if alt else "")
            + (f'<p class="mh-facts-line">{"".join(f"<span>{c}</span>" for c in chips)}</p>' if chips else "")
            + f'<div class="mh-actions">{"".join(buttons)}<span class="action-status" role="status"></span></div>'
            f'{progress}{_source_line(title)}</div></section>')
    text = str(title.info.get("description") or "").strip()
    desc = "".join(f"<p>{esc(p)}</p>" for p in text.split("\n\n") if p.strip()) or '<p class="muted">Описания нет.</p>'
    facts = facts_html(title)
    if found.hentai or found.by_hand:
        facts += f'<dt>Хентай</dt><dd class="content-row">{esc(("да" if found.hentai else "нет") + (" — " + found.reason if found.reason else ""))}</dd>'
    facts += f'<dt>Глав</dt><dd>{total}</dd>'
    genres = _chips(title.list_of("genres"), "genre")
    tags = _chips(title.list_of("tags"), "tag")
    main = (f'<div class="mh-desc">{desc}</div>'
            + (f'<div class="mh-chips">{genres}{tags}</div>' if genres or tags else "")
            + f'<dl class="mh-facts">{facts}</dl>')
    chapters = chapters_html(_chapter_rows(title, state))
    tab = "chapters" if req.arg("tab") == "chapters" else "main"
    tabs = (f'<nav class="mh-tabs" role="tablist" aria-label="Тайтл">'
            f'<button type="button" role="tab" data-action="mh-tab" data-tab="main" aria-selected="{"true" if tab == "main" else "false"}">Главная</button>'
            f'<button type="button" role="tab" data-action="mh-tab" data-tab="chapters" aria-selected="{"true" if tab == "chapters" else "false"}">'
            f'Главы <span class="muted">{total}</span></button></nav>')
    body = (f'<div class="mh-title" data-title="{title.id}">{hero}{tabs}'
            f'<section class="mh-pane" data-pane="main"{" hidden" if tab != "main" else ""}>{main}</section>'
            f'<section class="mh-pane" data-pane="chapters"{" hidden" if tab != "chapters" else ""}>{chapters}</section></div>')
    return _frame(req, title.title, body, kind="manhwa-title")


def _source_line(title: H.Title) -> str:
    return source_block("manhwa", title.id, title.path, title.title)


def source_block(kind: str, item_id: str, folder, name: str) -> str:
    """(v5.44.60) A title «Скачать манхву» downloaded: its site and translators, «Новые главы — скачивать самой» (the
    nightly round), the paid chapters waiting for their dates, «Докачать» (the download page with its link);
    (v5.44.62) «брать с других сайтов» the chapters its site has not free — when other sites were found (or it is
    switched off); (v5.44.74) the chapters left unticked on the download page (the nightly round leaves them).
    (v5.44.124) One block for both libraries: a title of «Вся манга» (``kind`` "manga") downloaded from a site of
    manga has it too — its boxes say the library (``data-kind``: ``/api/manhwa/follow`` finds the title there),
    «Докачать сейчас» — its one page in the tab «Манга».  ``item_id`` — the title's id in its library, ``folder`` —
    its folder (``.lb/source.json``), ``name`` — its name; a title the program did not download — ""."""
    from core import manhwa_download as D, manhwa_sources as S
    if not (Path(folder) / ".lb" / "source.json").is_file():
        return ""
    data = D.read_source(Path(folder))
    if not data.get("site") or not data.get("url"):
        return ""
    where = ' data-kind="manga"' if kind == "manga" else ""
    source = S.by_name(str(data["site"]))
    label = source.label if source else str(data["site"])
    team = f" · переводчики: {esc(data['branch_name'])}" if data.get("branch_name") else ""
    on = bool(data.get("follow", True))
    waiting = [w for w in data.get("waiting") or [] if isinstance(w, dict)]
    wait = ""
    if waiting and not any(w.get("free_at") for w in waiting):
        # (v5.44.73) paid with no date to become free (Remanga's licensed ones): they wait for another site
        wait = (f'<p class="mh-source-wait">Платные, которых пока нет бесплатно ни на одном из сайтов: {len(waiting)} '
                f'{_words(len(waiting), "глава", "главы", "глав")} — появятся, программа скачает сама.</p>')
    elif waiting:
        first = waiting[0]
        wait = (f'<p class="mh-source-wait">Ждут, пока станут бесплатными: {len(waiting)} '
                f'{_words(len(waiting), "глава", "главы", "глав")} — ближайшая {esc(first.get("label") or "")}'
                f'{(", " + esc(D._date(first.get("free_at") or ""))) if first.get("free_at") else ""}.</p>')
    skipped = len(data.get("skipped") or [])
    if skipped:
        wait += (f'<p class="mh-source-wait">Не отмечены при скачивании: {skipped} '
                 f'{_words(skipped, "глава", "главы", "глав")} — сама программа их не скачивает; скачать — «Докачать '
                 f'сейчас».</p>')
    helpers = [h for h in data.get("helpers") or [] if isinstance(h, dict) and h.get("site")]
    names = ", ".join(dict.fromkeys(getattr(S.by_name(str(h["site"])), "label", str(h["site"])) for h in helpers))
    help_on = bool(data.get("helpers_on", True))
    others = ""
    if helpers or not help_on:
        others = (f'<label class="mh-source-follow"><input type="checkbox" data-helpers="{esc(item_id)}"{where}'
                  f'{" checked" if help_on else ""}> Главы, которых нет бесплатно на {esc(label)}, — брать с других '
                  f'сайтов{(" (" + esc(names) + ")") if names else ""}</label>')
    # (v5.44.119) «Докачать сейчас» — the title's one page (until then the card of «Скачать → По ссылке»);
    # (v5.44.124) ``data-title-page`` — as «Докачать» of every other page that leads there
    more = home_of("manga" if kind == "manga" else "manhwa", data, name) or "/get"
    return (f'<div class="mh-source"><p>Скачано с {esc(label)}{team}.</p>'
            f'<label class="mh-source-follow"><input type="checkbox" data-follow="{esc(item_id)}"{where}'
            f'{" checked" if on else ""}> Новые главы — скачивать самой (раз в сутки, ночью)</label>{others}'
            f'<a class="mh-source-more" href="{esc(more)}" data-title-page>Докачать сейчас</a>'
            f'<span class="action-status" role="status"></span>{wait}</div>')


# ── /manhwa/r/<chapter id> — (v5.44.117) the one reader (webui/pages/reader.py) ────────────────────────────────────
def _pages(chapter: H.Chapter) -> list:
    """[(n from 1, width, height)] — the sizes read once (chapter.json, else the pictures' headers)."""
    return [(n, *H.page_size(page)) for n, page in enumerate(chapter.pages, 1)]


def _came(chapter: H.Chapter) -> float:
    """When a chapter came: ``downloaded_at`` of its chapter.json, else its folder's time (:func:`came_of`)."""
    return came_of(chapter.info, chapter.mtime)


def reading_mode(title: H.Title, chapter: H.Chapter) -> tuple:
    """(v5.44.83) (the way the chapter is read, the owner's own choice or "") — «strip», «rtl», «ltr»; (v5.44.117) not
    chosen — by the title's kind and its pages' heights (``manga_online.auto_of``)."""
    from core import manga_online as O
    chosen = H.PROGRESS.mode_of(title.id)
    if chosen in O.MODES:
        return chosen, chosen
    return O.auto_of(str(title.info.get("type") or ""), False, [(w, h) for _n, w, h in _pages(chapter)]), ""


def mode(req: Request) -> Response:
    """(v5.44.83) ?id=<title> {mode}: the way this title of the folder is read ("" — the program's by its kind)."""
    from core import manga_online as O
    title = _title(req, req.arg("id"))
    if title is None:
        return Response.json({"ok": False, "error": "Такого тайтла нет"}, 404)
    value = str(req.json().get("mode") or "")
    if value and value not in O.MODES:
        return Response.json({"ok": False, "error": "Такого способа нет"}, 400)
    H.PROGRESS.set_mode(title.id, value)
    return Response.json({"ok": True, "mode": value})


def waiting_rows(stored: dict) -> list:
    """(v5.44.117) The paid chapters a downloaded title waits for (``.lb/source.json`` «waiting»): rows of the reader's
    list «платная до …» — until the site's own list comes (``reader.site``).  The manga's folder takes them too."""
    from webui.pages import reader as R
    rows = []
    for item in stored.get("waiting") or []:
        if not isinstance(item, dict) or not item.get("label"):
            continue
        tome, number, _own = H.parse_name(str(item["label"]))
        rows.append(R.Row(key="", label=str(item["label"]), where="paid", free_at=R.day_of(item.get("free_at")),
                          glue=False, tome=tome, number=R.number_of(number) if number else None))
    return rows


def site_of(folder: str) -> dict:
    """The title's ``.lb/source.json`` (a title the program downloaded or linked to a site), {} — none."""
    from core import manhwa_download as D
    path = Path(folder)
    if not (path / ".lb" / "source.json").is_file():
        return {}
    data = D.read_source(path)
    return data if data.get("site") and data.get("url") else {}


def follows(req: Request, stored: dict) -> bool:
    """New chapters of the title come by themselves at night: its «follow» and «Новые главы — скачивать самой»."""
    return bool(stored and stored.get("added_at") and stored.get("follow", True)
                and req.settings.get("manhwa_watch_auto", True))


def home_of(kind: str, stored: dict, name: str, phone: bool = False) -> str:
    """(v5.44.119) «К тайтлу» of the reader and «Докачать сейчас» of a title of the folder downloaded from a site (or
    linked to one — its ``source.json``): its one page by its link (``webui/pages/title.py``), with its translators;
    "" — a title of the folder alone (its library's page)."""
    if not stored or not str(stored.get("url") or "").startswith(("http://", "https://")):
        return ""
    from webui.pages import title as T
    return T.href(kind, str(stored["url"]), branch=str(stored.get("branch") or ""), name=name, phone=phone)


def book(req: Request, title: H.Title, chapter: H.Chapter, *, phone: bool = False, asked: "str | None" = None):
    """(v5.44.117) The chapter of the folder for the one reader (``webui/pages/reader.py``): the title's chapters (read,
    new; with ``source.json`` — in the folder, the paid ones it waits for), the chapter's pages with their sizes, where
    it was left, the way it is read."""
    import time as _time
    from webui.pages import reader as R
    state = _read_state(title)
    m = "/m" if phone else ""
    now = _time.time()
    stored = site_of(title.path)
    rows = []
    for c in title.chapters:
        href = f"{m}/manhwa/r/{c.id}"
        read = c.id in state["read"]
        came = _came(c)
        rows.append(R.Row(key=c.id, label=c.label, name=c.name if c.number else "", href=href, start=f"{href}?p=1",
                          last=f"{href}?p={len(c.pages)}", pages=len(c.pages), read=read,
                          new=bool(not read and came and now - came < R.NEW_FOR), tome=c.tome,
                          number=R.number_of(c.number) if c.number else None))
    current = rows[next(i for i, c in enumerate(title.chapters) if c.id == chapter.id)]
    rows = R.interleave(rows, waiting_rows(stored))
    sizes = _pages(chapter)
    pages = [{"src": f"/manhwa/page/{chapter.id}/{n}", "w": w, "h": h, "thumb": f"/manhwa/thumb/{chapter.id}/{n}"}
             for n, w, h in sizes]
    now_mode, chosen = reading_mode(title, chapter)
    page, part = R.place_of(req, H.PROGRESS.place(title.id, chapter.id), len(pages), read=current.read, asked=asked)
    if now_mode == "strip":
        page = min(page, max(0, len(pages) - 1))
    return R.Book(src="manhwa", kind="manhwa", phone=phone, name=title.title,
                  home=home_of("manhwa", stored, title.title, phone) or
                  (f"/m/manhwa/t/{title.id}" if phone else f"/manhwa/t/{title.id}?tab=chapters"),
                  rows=rows, at=rows.index(current), pages=pages, href=f"{m}/manhwa/r/{chapter.id}",
                  base=f"{m}/manhwa/r/" + "{key}", progress="/api/manhwa/progress",
                  mode_api=f"/api/manhwa/mode?id={title.id}",
                  chapter_api="/api/manhwa/chapter/{key}" + ("?m=1" if phone else ""),
                  site_api=(f"/api/reader/site?src=manhwa&id={chapter.id}" + ("&m=1" if phone else "")) if stored else "",
                  mode=now_mode, chosen=chosen, page=page, part=part, follow=follows(req, stored),
                  linked=bool(stored), dir_default=chosen if chosen in ("ltr", "rtl") else "ltr")


def site_book(req: Request, key: str, *, phone: bool = False) -> tuple:
    """(v5.44.117) (the chapter's book with the site's chapters among the folder's, still asked) — ``reader.site``."""
    from webui.pages import reader as R
    from webui.pages.manga_online import online_link
    title, chapter = _chapter(req, key)
    if title is None or chapter is None:
        raise LookupError("Такой главы нет")
    stored = site_of(title.path)
    if not stored:
        return None, False
    url, branch = str(stored["url"]), str(stored.get("branch") or "")
    reading, pending = R.site_reading(url, branch, req.settings)
    if reading is None:
        return None, pending
    found = book(req, title, chapter, phone=phone, asked="1")
    R.merge_site(found, reading, online_link(url, branch, phone))
    return found, False


def reader(req: Request) -> Response:
    title, chapter = _chapter(req, req.params["id"])
    if title is None or chapter is None:
        return Response.redirect("/manhwa")
    hidden = _hidden(req, title)
    if hidden is not None:
        return hidden
    if not chapter.pages:
        return Response.redirect(f"/manhwa/t/{title.id}?tab=chapters")
    return show(req, title, chapter, phone=False)


def show(req: Request, title: H.Title, chapter: H.Chapter, *, phone: bool) -> Response:
    """The reader's page of a chapter (the computer's and the phone's): a page open is what was read."""
    from webui.pages import reader as R
    found = book(req, title, chapter, phone=phone)
    if found.mode != "strip" and found.pages:
        total = len(found.pages)
        H.PROGRESS.record(title.id, chapter.id, min(found.page, total - 1), 0.0, done=found.page >= total - 1)
    return R.page(req, found, frame=lambda name, body, kind, assets: _frame(req, name, body, kind=kind, assets=assets))


def chapter_json(req: Request) -> Response:
    """A chapter for the strip to go on with: its label, pages with their sizes, its neighbours, whether it is read —
    (v5.44.117) and the words after it (``reader.between_html``); ``?m=1`` — the phone's addresses."""
    title, chapter = _chapter(req, req.params["id"])
    if title is None or chapter is None:
        return Response.json({"ok": False, "error": "Такой главы нет"}, 404)
    from core import content_marks as CM
    from webui import content_mode
    from webui.pages import reader as R
    if not content_mode.shows(CM.manhwa_verdict(title).hentai):
        return Response.json({"ok": False, "error": "Скрыто режимом"}, 403)
    phone = req.arg("m") == "1"
    found = None
    if site_of(title.path):                  # a title of a site: the words after it know the site's chapters
        found, _pending = site_book(req, chapter.id, phone=phone)
    if found is None:
        found = book(req, title, chapter, phone=phone, asked="1")
    reply = R.chapter_reply(found, found.at, found.pages)
    reply["title"] = title.id
    return Response.json(reply)


def progress(req: Request) -> Response:
    """{chapter, page, part, done}: where the strip is read now; ``done`` — the chapter was read to its end."""
    body = req.json()
    title, chapter = _chapter(req, str(body.get("chapter") or ""))
    if title is None or chapter is None:
        return Response.json({"ok": False, "error": "Такой главы нет"}, 404)
    try:
        page = int(body.get("page") or 0)
        part = float(body.get("part") or 0)
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Непонятное место"}, 400)
    H.PROGRESS.record(title.id, chapter.id, min(max(0, page), max(0, len(chapter.pages) - 1)), part,
                      done=bool(body.get("done")))
    return Response.json({"ok": True})


def mark_read(req: Request) -> Response:
    """The tick of a chapter by hand: {chapter, read: true | false}; (v5.44.126) ``kind`` "manga" — a chapter of a
    series of «Вся манга» (a manga of one part too): the manga's memory of reading (``manga_library.PROGRESS``, its
    readers' and the phone's), as ``/api/manhwa/follow`` names the library; without it — the manhwa's, then the
    manga's (their ids are made apart).  Neither «где остановились» nor «Продолжить чтение» moves: «Продолжить» goes
    past a chapter ticked."""
    body = req.json()
    kind = str(body.get("kind") or "")
    if kind not in ("", "manga", "manhwa"):
        return Response.json({"ok": False, "error": "Такой библиотеки нет"}, 400)
    key = str(body.get("chapter") or "")
    read = bool(body.get("read"))
    if kind in ("", "manhwa"):
        title, chapter = _chapter(req, key)
        if title is not None and chapter is not None:
            H.PROGRESS.set_read(title.id, chapter.id, read)
            return Response.json({"ok": True, "read": read})
    if kind in ("", "manga"):
        from core import manga_library as M
        from webui.pages import manga as MG
        found = MG.chapter_of(req.settings, key)
        if found is not None:
            top, chapter = found
            M.PROGRESS.set_read(top.id, chapter.id, read)
            return Response.json({"ok": True, "read": read})
    return Response.json({"ok": False, "error": "Такой главы нет"}, 404)


# ── pictures ────────────────────────────────────────────────────────────────────────────────────────────────────
def _placeholder(text: str, width: int = 350, height: int = 500) -> Response:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
           f'<rect width="{width}" height="{height}" fill="#8884"/><text x="{width // 2}" y="{height // 2}" '
           f'font-family="sans-serif" font-size="20" text-anchor="middle" fill="#888">{esc(text)}</text></svg>')
    return Response(200, svg.encode("utf-8"), "image/svg+xml", {"Cache-Control": "no-cache"})


def cover(req: Request) -> Response:
    title = _title(req, req.params["id"])
    if title is None:
        return _placeholder("нет обложки")
    size = COVERS["l" if req.arg("size") == "l" else "s"]
    from webui.pages.media import _MAKING
    with _MAKING:
        made = H.cover_thumb(title, size)
    if made is None:
        return _placeholder("нет обложки")
    return Response(200, content_type="image/jpeg", file=made, headers={"Cache-Control": "private, max-age=86400"})


def page_image(req: Request) -> Response:
    _title_of, chapter = _chapter(req, req.params["id"])
    n = int(req.params["n"])
    if chapter is None or not 1 <= n <= len(chapter.pages):
        return Response.not_found("Нет такой страницы")
    page = chapter.pages[n - 1]
    import mimetypes
    ctype = mimetypes.guess_type("x" + page.ext)[0] or "application/octet-stream"
    headers = {"Cache-Control": "private, max-age=86400"}
    if not page.member:
        return Response(200, content_type=ctype, file=Path(page.path), headers=headers)
    import zipfile
    try:
        return Response(200, page.read_bytes(), ctype, headers)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        H._LOG.warning("manhwa page %s / %s not shown: %s", page.path, page.member, exc)
        return _placeholder("страница не читается", 720, 400)


def page_thumb(req: Request) -> Response:
    """(v5.44.117) A page small — «Лента страниц» of the reader read page by page (the top of a long slice)."""
    _title_of, chapter = _chapter(req, req.params["id"])
    n = int(req.params["n"])
    if chapter is None or not 1 <= n <= len(chapter.pages):
        return _placeholder("нет страницы")
    from webui.pages.media import _MAKING
    with _MAKING:
        made = H.page_thumb(chapter.pages[n - 1])
    if made is None:
        return _placeholder("нет превью")
    return Response(200, content_type="image/jpeg", file=made, headers={"Cache-Control": "private, max-age=86400"})


def reveal(req: Request) -> Response:
    title = _title(req, req.params["id"])
    if title is None:
        return Response.json({"ok": False, "error": "Такой манхвы нет"}, 404)
    from webui import desktop
    try:
        desktop.reveal(Path(title.path))
    except OSError as exc:
        return Response.json({"ok": False, "error": f"Не удалось открыть папку: {exc}"}, 500)
    return Response.json({"ok": True})

