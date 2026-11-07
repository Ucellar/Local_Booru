"""«Манхва» (v5.44.59): long strips read by scrolling, as on Remanga (``core/manhwa_library.py``).

* ``/manhwa`` — the titles of the manhwa folder: «Продолжить чтение» (the titles being read, the last first) and all
  of them, with a search, the sorts and how much of each is read;
* ``/manhwa/t/<id>`` — a title, as on Remanga: the cover, the names, «Продолжить: Том 1 Глава 7» (or «Читать»),
  the tabs «Главная» (the description, genres, tags, facts) and «Главы» (newest first, read ones ticked, «Вы
  остановились здесь» at the last one read);
* ``/manhwa/r/<chapter id>`` — the reader: the chapter as one strip (its slices glued without gaps, each with its size
  given, so the strip never jumps and the place found again is exact); the next chapter comes by itself below when the
  end is near (``/api/manhwa/chapter/<id>``), the address and the bar follow the chapter being read; the place is kept
  (``/api/manhwa/progress``: the page at the top of the window and how much of it is above) and a chapter read to its
  end is ticked; the bar on top — ‹ ›, «Том · Глава» opening the list of chapters, the width of the strip — hides while
  the strip is read down and comes back when it is scrolled up (``static/manhwa.js``).

The owner (4.11.2026): «манхва должна переходить без кнопки с запоминанием где остановился и галочкой о том что
прочел эту главу сверху всплывающие меню с стрелочками перехода и номерном том/глава при нажатии вылезает список глав
при прокручивании в низ скрывается эта менюшка а при пролистывании вверх появляется».
"""
from __future__ import annotations

from pathlib import Path

from core import manhwa_library as H
from webui.html import esc, page_frame, paginator, rows_attrs, rows_per_page, url
from webui.server import Request, Response

PER_PAGE = 30
ROWS_COOKIE = "lb_manhwa_pp"
COVERS = {"s": 360, "l": 720}                # px, the long side of a cover: on the list / on a title's page
SHELF = 6                                    # titles in «Продолжить чтение»
SORTS = {"newest": "Сначала новые", "title": "По названию", "read": "Недавно читал", "chapters": "Больше глав"}
WIDTHS = {"720": "720", "900": "900", "1200": "1200", "full": "по окну"}     # the strip's width (prefs: manhwa_width)
EAGER = 3                                    # pages of a strip asked for at once; the rest — as they come near


def register(router) -> None:
    router.add("GET", r"/manhwa", index)
    router.add("GET", r"/manhwa/t/(?P<id>[0-9a-f]{12})", title_page)
    router.add("GET", r"/manhwa/r/(?P<id>[0-9a-f]{12})", reader)
    router.add("GET", r"/api/manhwa/feed-cover", feed_cover)
    router.add("GET", r"/manhwa/cover/(?P<id>[0-9a-f]{12})", cover)
    router.add("GET", r"/manhwa/page/(?P<id>[0-9a-f]{12})/(?P<n>\d+)", page_image)
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


def _card(title: H.Title, *, shelf: bool = False) -> str:
    state = _read_state(title)
    total = len(title.chapters)
    done = len(state["read"])
    target, _said = _continue(title, state)
    sub = f"{total} {_words(total, 'глава', 'главы', 'глав')}"
    if shelf:
        sub = target.label
    bar = (f'<span class="mh-card-bar" style="--p:{100 * done / max(1, total):.0f}%" '
           f'title="Прочитано {done} из {total}"></span>' if done else "")
    href = f"/manhwa/r/{target.id}" if shelf else f"/manhwa/t/{title.id}"
    kind = str(title.info.get("type") or "").strip()
    badge = f'<span class="mh-card-type">{esc(kind[:14])}</span>' if kind else ""
    return (f'<a class="mh-card" href="{esc(href)}" title="{esc(title.title)}">'
            f'<span class="mh-card-pic"><img src="/manhwa/cover/{title.id}" alt="" loading="lazy" decoding="async">'
            f'{badge}{bar}</span><span class="mh-card-title">{esc(title.title)}</span>'
            f'<span class="mh-card-sub">{esc(sub)}</span></a>')


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
            f'<a href="/settings#set-folders">Настройки → Папки</a>.</p></div>')


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
        reading = sorted((t for t in items if H.PROGRESS.title(t.id)["at"]), key=lambda t: -H.PROGRESS.title(t.id)["at"])
        if reading:
            parts.append('<section class="mh-shelf"><h2>Продолжить чтение</h2><div class="mh-cards">'
                         + "".join(_card(t, shelf=True) for t in reading[:SHELF]) + "</div></section>")
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
         ("artist", "Художник"), ("publisher", "Издатель"), ("translator", "Переводчик"))


def _chips(values: list, cls: str) -> str:
    return "".join(f'<a class="mh-chip {cls}" href="{esc(url("/manhwa", q=v))}">{esc(v)}</a>' for v in values[:40])


def _chapter_rows(title: H.Title, state: dict) -> str:
    rows = []
    last = state["last"]
    for chapter in reversed(title.chapters):              # newest first, as on Remanga
        read = chapter.id in state["read"]
        here = last is not None and chapter.id == last.id
        cls = "mh-ch" + (" read" if read else "") + (" here" if here else "")
        name = f'<span class="mh-ch-name">{esc(chapter.name)}</span>' if chapter.number and chapter.name else ""
        mark = '<span class="mh-here">Вы остановились здесь</span>' if here else ""
        rows.append(f'<li class="{cls}" data-chapter="{chapter.id}"><a href="/manhwa/r/{chapter.id}">'
                    f'<span class="mh-ch-label">{esc(chapter.label)}</span>{name}{mark}</a>'
                    f'<button type="button" class="mh-tick" data-action="mh-read" data-chapter="{chapter.id}" '
                    f'aria-pressed="{"true" if read else "false"}" title="{"Прочитана — снять отметку" if read else "Отметить прочитанной"}">'
                    f'<span aria-hidden="true">✓</span><span class="sr-only">Прочитана</span></button></li>')
    return "".join(rows)


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
        buttons.append(f'<a class="mh-btn" href="/manhwa/r/{title.chapters[0].id}?from=start">С первой главы</a>')
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
    progress = (f'<div class="mh-progress" title="Прочитано {done} из {total}"><span style="--p:{100 * done / max(1, total):.0f}%">'
                f'</span></div><p class="mh-progress-text">Прочитано {done} из {total} {_words(total, "главы", "глав", "глав")}</p>')
    hero = (f'<section class="mh-hero"><a class="mh-cover" href="/manhwa/r/{target.id}">'
            f'<img src="/manhwa/cover/{title.id}?size=l" alt=""></a><div class="mh-about">'
            f'<h1>{esc(title.title)}</h1>' + (f'<p class="mh-alt">{alt}</p>' if alt else "")
            + (f'<p class="mh-facts-line">{"".join(f"<span>{c}</span>" for c in chips)}</p>' if chips else "")
            + f'<div class="mh-actions">{"".join(buttons)}<span class="action-status" role="status"></span></div>'
            f'{progress}{_source_line(title)}</div></section>')
    text = str(title.info.get("description") or "").strip()
    desc = "".join(f"<p>{esc(p)}</p>" for p in text.split("\n\n") if p.strip()) or '<p class="muted">Описания нет.</p>'
    facts = "".join(f'<dt>{esc(name)}</dt><dd>{esc(", ".join(title.list_of(key)) if isinstance(title.info.get(key), list) else title.info.get(key))}</dd>'
                    for key, name in FACTS if title.info.get(key))
    if found.hentai or found.by_hand:
        facts += f'<dt>Хентай</dt><dd class="content-row">{esc(("да" if found.hentai else "нет") + (" — " + found.reason if found.reason else ""))}</dd>'
    facts += f'<dt>Глав</dt><dd>{total}</dd>'
    genres = _chips(title.list_of("genres"), "genre")
    tags = _chips(title.list_of("tags"), "tag")
    main = (f'<div class="mh-desc">{desc}</div>'
            + (f'<div class="mh-chips">{genres}{tags}</div>' if genres or tags else "")
            + f'<dl class="mh-facts">{facts}</dl>')
    chapters = (f'<div class="mh-ch-head"><button type="button" class="mh-order" data-action="mh-order" '
                f'data-order="desc">Сначала первые</button></div><ol class="mh-chapters" data-order="desc">'
                f'{_chapter_rows(title, state)}</ol>')
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
    """(v5.44.60) A title «Скачать манхву» downloaded: its site and translators, «Новые главы — скачивать самой» (the
    nightly round), the paid chapters waiting for their dates, «Докачать» (the download page with its link);
    (v5.44.62) «брать с других сайтов» the chapters its site has not free — when other sites were found (or it is
    switched off); (v5.44.74) the chapters left unticked on the download page (the nightly round leaves them)."""
    from core import manhwa_download as D, manhwa_sources as S
    data = D.read_source(Path(title.path))
    if not data.get("site") or not data.get("url"):
        return ""
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
        others = (f'<label class="mh-source-follow"><input type="checkbox" data-helpers="{title.id}"'
                  f'{" checked" if help_on else ""}> Главы, которых нет бесплатно на {esc(label)}, — брать с других '
                  f'сайтов{(" (" + esc(names) + ")") if names else ""}</label>')
    from webui.pages.get_link import link_of               # (v5.44.87) «Скачать → По ссылке»
    return (f'<div class="mh-source"><p>Скачано с {esc(label)}{team}.</p>'
            f'<label class="mh-source-follow"><input type="checkbox" data-follow="{title.id}"'
            f'{" checked" if on else ""}> Новые главы — скачивать самой (раз в сутки, ночью)</label>{others}'
            f'<a class="mh-source-more" href="{esc(link_of(str(data["url"])))}">Докачать сейчас</a>'
            f'<span class="action-status" role="status"></span>{wait}</div>')


# ── /manhwa/r/<chapter id> ──────────────────────────────────────────────────────────────────────────────────────
def _width() -> str:
    from webui import prefs
    value = str(prefs.load().get("manhwa_width") or "900")
    return value if value in WIDTHS else "900"


def _pages(chapter: H.Chapter) -> list:
    """[(n from 1, width, height)] — the sizes read once (chapter.json, else the pictures' headers)."""
    return [(n, *H.page_size(page)) for n, page in enumerate(chapter.pages, 1)]


def _strip(chapter: H.Chapter, start: int = 0) -> str:
    """The chapter's pages glued into one strip; the pages around ``start`` asked for at once."""
    imgs = []
    for n, w, h in _pages(chapter):
        size = f' width="{w}" height="{h}"' if w > 0 and h > 0 else ""
        eager = start - 1 <= n - 1 <= start + EAGER
        lazy = "" if eager else ' loading="lazy"'
        imgs.append(f'<img class="mh-page" id="c{chapter.id}-p{n}" src="/manhwa/page/{chapter.id}/{n}"{size} alt="" '
                    f'decoding="async"{lazy}>')
    return "".join(imgs)


def _section(title: H.Title, chapter: H.Chapter, start: int = 0) -> str:
    before, after = title.neighbours(chapter)
    return (f'<section class="mh-chapter" data-chapter="{chapter.id}" data-label="{esc(chapter.label)}" '
            f'data-prev="{before.id if before else ""}" data-next="{after.id if after else ""}">'
            f'<h2 class="mh-chapter-head">{esc(chapter.full_label)}</h2>{_strip(chapter, start)}</section>')


def _list(title: H.Title, current: H.Chapter, read: set) -> str:
    items = []
    for chapter in reversed(title.chapters):
        cls = ("current " if chapter.id == current.id else "") + ("read" if chapter.id in read else "")
        items.append(f'<li class="{cls.strip()}" data-chapter="{chapter.id}"><a href="/manhwa/r/{chapter.id}">'
                     f'<span>{esc(chapter.label)}</span><i aria-hidden="true">✓</i></a></li>')
    return "".join(items)


def reading_mode(title: H.Title, chapter: H.Chapter) -> tuple:
    """(v5.44.83) (the way the chapter is read, the owner's own choice or "") — as the reader of the sites' chapters:
    «Лента», «Справа налево», «Слева направо»; «Авто» — by the title's kind and its pages' heights."""
    from core import manga_online as O
    chosen = H.PROGRESS.mode_of(title.id)
    if chosen in O.MODES:
        return chosen, chosen
    return O.auto_of(str(title.info.get("type") or ""), False, [(w, h) for _n, w, h in _pages(chapter)]), ""


def mode(req: Request) -> Response:
    """(v5.44.83) ?id=<title> {mode}: the way this title of the folder is read ("" — «авто»)."""
    from core import manga_online as O
    title = _title(req, req.arg("id"))
    if title is None:
        return Response.json({"ok": False, "error": "Такого тайтла нет"}, 404)
    value = str(req.json().get("mode") or "")
    if value and value not in O.MODES:
        return Response.json({"ok": False, "error": "Такого способа нет"}, 400)
    H.PROGRESS.set_mode(title.id, value)
    return Response.json({"ok": True, "mode": value})


def reader(req: Request) -> Response:
    title, chapter = _chapter(req, req.params["id"])
    if title is None or chapter is None:
        return Response.redirect("/manhwa")
    hidden = _hidden(req, title)
    if hidden is not None:
        return hidden
    from webui.pages import manga_online as OP
    page, part = (0, 0.0) if req.arg("from") == "start" else H.PROGRESS.place(title.id, chapter.id)
    page = min(page, max(0, len(chapter.pages) - 1))
    before, after = title.neighbours(chapter)
    state = _read_state(title)
    width = _width()
    now, chosen = reading_mode(title, chapter)
    modes = OP.modes_html(chosen, now)
    widths = "".join(f'<button type="button" data-action="mh-width" data-width="{key}" aria-pressed='
                     f'"{"true" if key == width else "false"}">{esc(label)}</button>' for key, label in WIDTHS.items())
    if now != "strip":                               # the pages one at a time (static/online.js): ← → turn its pages
        widths = ""
    keys = ('data-key="ArrowLeft" ', " (←)", 'data-key="ArrowRight" ', " (→)") if now == "strip" else ("", "", "", "")
    prev_link = (f'<a class="mh-bar-prev" rel="prev" {keys[0]}href="/manhwa/r/{before.id}" '
                 f'title="Предыдущая глава{keys[1]}" aria-label="Предыдущая глава">‹</a>' if before else
                 '<a class="mh-bar-prev" aria-disabled="true" aria-label="Предыдущей главы нет">‹</a>')
    next_link = (f'<a class="mh-bar-next" rel="next" {keys[2]}href="/manhwa/r/{after.id}" '
                 f'title="Следующая глава{keys[3]}" aria-label="Следующая глава">›</a>' if after else
                 '<a class="mh-bar-next" aria-disabled="true" aria-label="Следующей главы нет">›</a>')
    bar = (f'<div class="mh-bar" data-bar><a class="mh-bar-back" href="/manhwa/t/{title.id}?tab=chapters" '
           f'title="К тайтлу"><span aria-hidden="true">←</span> <span class="mh-bar-title">{esc(title.title)}</span></a>'
           f'<div class="mh-bar-mid">{prev_link}<button type="button" class="mh-bar-where" '
           f'data-action="{"mh-list" if now == "strip" else "ol-list"}" aria-expanded="false" aria-controls="mh-list">'
           f'{esc(chapter.label)}</button>{next_link}</div>'
           f'<div class="mh-bar-tools">'
           + (f'<div class="mh-widths" role="group" aria-label="Ширина ленты">{widths}</div>' if widths else "")
           + f'{modes}</div>'
           f'<div class="mh-list" id="mh-list" hidden><ol>{_list(title, chapter, state["read"])}</ol></div></div>')
    name = f"{chapter.label} — {title.title}"
    if now != "strip":
        body = OP.pager_html(
            mode=now, chapter=chapter.id, page=page, pages=[{"src": f"/manhwa/page/{chapter.id}/{n}", "w": w, "h": h}
                                                         for n, w, h in _pages(chapter)],
            progress_api="/api/manhwa/progress", mode_api=f"/api/manhwa/mode?id={title.id}",
            next_href=f"/manhwa/r/{after.id}" if after else "", prev_href=f"/manhwa/r/{before.id}" if before else "",
            next_label=after.label if after else "", label=chapter.label, bar=bar,
            back_href=f"/manhwa/t/{title.id}?tab=chapters", back_words="К списку глав", phone=False)
        return _frame(req, name, body, kind="manhwa-reader", assets=("manhwa", "online"))
    tail = ('<div class="mh-tail" data-tail hidden><p>Это последняя глава.</p>'
            f'<p><a class="mh-btn" href="/manhwa/t/{title.id}?tab=chapters">К списку глав</a></p></div>')
    body = (f'<div class="mh-reader" data-title="{title.id}" data-start="{chapter.id}" data-page="{page}" '
            f'data-part="{part}" data-mode-api="/api/manhwa/mode?id={title.id}" '
            f'style="--mh-w:{"100%" if width == "full" else width + "px"}" data-width="{width}">'
            f'{bar}<div class="mh-strip">{_section(title, chapter, page)}</div>{tail}</div>')
    return _frame(req, name, body, kind="manhwa-reader", assets=("manhwa", "online"))


def chapter_json(req: Request) -> Response:
    """A chapter for the strip to go on with: its label, pages with their sizes, its neighbours, whether it is read."""
    title, chapter = _chapter(req, req.params["id"])
    if title is None or chapter is None:
        return Response.json({"ok": False, "error": "Такой главы нет"}, 404)
    from core import content_marks as CM
    from webui import content_mode
    if not content_mode.shows(CM.manhwa_verdict(title).hentai):
        return Response.json({"ok": False, "error": "Скрыто режимом"}, 403)
    before, after = title.neighbours(chapter)
    return Response.json({
        "ok": True, "id": chapter.id, "title": title.id, "label": chapter.label, "full": chapter.full_label,
        "pages": [{"src": f"/manhwa/page/{chapter.id}/{n}", "w": w, "h": h} for n, w, h in _pages(chapter)],
        "prev": before.id if before else "", "next": after.id if after else "",
        "read": chapter.id in set(H.PROGRESS.title(title.id)["read"])})


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
    """The tick of a chapter by hand: {chapter, read: true | false}."""
    body = req.json()
    title, chapter = _chapter(req, str(body.get("chapter") or ""))
    if title is None or chapter is None:
        return Response.json({"ok": False, "error": "Такой главы нет"}, 404)
    H.PROGRESS.set_read(title.id, chapter.id, bool(body.get("read")))
    return Response.json({"ok": True, "read": bool(body.get("read"))})


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

