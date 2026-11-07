"""(v5.44.65) The gallery on the owner's phone — the sketch «Local Booru на телефоне» («Галерея», «Картинка»).

* ``/m/posts`` (:func:`posts`) — the search by tags (hints while typing: ``/api/autocomplete``), the sections («Все»,
  «Новые», «Избранное», «Скачанные», «Найденные»), the order, three columns of square tiles (a video — its length, a
  GIF, a сборка — «⧉ N», the favourite — a star); «Показать ещё» brings the next tiles in (``?part=1`` — JSON), by
  itself when the end comes near;
* ``/m/post/<id>`` (:func:`post`) — the picture over the whole width (its large preview ``/poster``: the original is
  ten times bigger over the mobile internet; a tap — the original), arrows and a swipe to the neighbours of the search
  (``gallery.neighbours``: a сборка card after card), and under it «В избранное», the сборка, «На телефон» (the file
  saved on the phone), what the file is, its tags by kind (a tag — its search), the sites it came from.  A video plays
  in the phone's browser; a file it cannot play — said, with «На телефон».

The pages are the computer's gallery (``webui/data``: the same search, sections, сборки) in the phone's frame
(``webui/pages/phone.py``, ``static/phone.css``, ``static/phone.js``); in «SFW» they are hidden (``content_mode``).

(v5.44.118, the sketch E «Телефон как компьютер») What the computer's card and sections can do, the phone can too
(``static/phone-gallery.js``, ``static/phone-gallery.css``): the card — «В корзину» (a second press; in the trash —
«Вернуть»), the rating 1…5 (the computer's ``/api/post/<n>/rating``), «+ / −» by each tag (add it to the search or
leave such out — the card's tags are not changed), why it is in Брак or in the trash; the sections «Брак» (why it is
there, the words ``брак:…`` with their numbers, «Выбрать», «Искать снова» — the computer's ``/api/parser/retry``) and
«Корзина» (why each card is there, the words ``корзина:…``, «Вернуть», «Вернуть всё» with its journal and
«Остановить» — the computer's ``/api/trash/…``).  Nothing is deleted for good from the phone: «Удалить навсегда» and
«Очистить корзину» are the computer's only — the page says so.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from core.external_browser import is_web_url
from webui import data
from webui.html import CATEGORY_ORDER, CATEGORY_TITLES, esc, human_size, url
from webui.pages import gallery, phone
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.webui")

PER_PAGE = 60
# (v5.44.118) the computer's sections, in its order, with «Брак» and «Корзина»
SECTIONS = (("all", "Все"), ("inbox", "Новые"), ("found", "Найденные"), ("downloaded", "Скачанные"),
            ("no_match", "Брак"), ("favorites", "Избранное"), ("trash", "Корзина"))
ORIGINAL_SHOWN = {".gif", ".webp", ".apng"}      # may move: the file itself, not its still preview
ASSETS = {"scripts": ("phone-gallery",), "head": '<link rel="stylesheet" href="/static/phone-gallery.css">'}
TRASH_HERE = ("«Удалить навсегда» и «Очистить корзину» — только на компьютере: с телефона насовсем ничего не "
              "удаляется.")
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
          "декабря")


def register(router) -> None:
    router.add("GET", r"/m/posts", posts)
    router.add("GET", r"/m/post/(?P<id>\d+)", post)


# ── /m/posts ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _count(n: int) -> str:
    from webui.pages.post_sets import FILES, plural
    return plural(n, FILES).replace(str(n), f"{n:,}".replace(",", " "), 1)


def _is_video(item: dict) -> bool:
    return bool(item.get("is_video")) or Path(str(item.get("path") or "")).suffix.lower() in gallery.VIDEO_EXTS


def _tile(item: dict, ctx: dict, index: int, why: str = "") -> str:
    """One square of the grid: the card's preview, its kind or its сборка, the star of the favourite; (v5.44.118)
    ``why`` — in «Брак» and «Корзина», why the card is there, in a word or two (in the corner, as the sketch E)."""
    from core.video_probe import human_duration
    image_id = int(item["id"])
    href = url(f"/m/post/{image_id}", i=index, **ctx)
    ext = Path(str(item.get("path") or "")).suffix.lower()
    count = int(item.get("set_count") or 0)
    if count >= 2:
        from webui.pages.post_sets import FILES, plural
        badge, what = f"⧉ {count}", f"Сборка: {plural(count, FILES)}"
    elif _is_video(item):
        length = human_duration(item.get("duration") or 0)
        badge, what = f"▶ {length}" if length else "▶", "Видео"
    elif ext == ".gif":
        badge, what = "GIF", "GIF"
    else:
        badge, what = "", "Картинка"
    star = (phone.svg(phone.STAR, size=18, width=1.2, cls="m-star", fill="currentColor", label="В избранном")
            if item.get("favorite") else "")
    corner = f'<span class="m-why">{esc(why)}</span>' if why else ""
    kind = "m-badge m-badge-top" if why and badge else "m-badge"
    return (f'<a class="m-tile" id="i{index}" href="{esc(href)}" data-index="{index}" data-id="{image_id}" '
            f'aria-label="{esc(what + (" — " + why if why else ""))}">'
            f'<img src="{esc(data.media_url("thumb", item))}" alt="" loading="lazy" decoding="async">'
            + (f'<span class="{kind}">{esc(badge)}</span>' if badge else "") + f"{corner}{star}</a>")


def _whys(settings: dict, section: str, items: list) -> dict:
    """{card: why it is in the section} — «Брак»: «фото» / «рисунок»; «Корзина»: the reason in a word or two."""
    try:
        if section == "trash":
            from core import trash_tools
            return {i: trash_tools.short_label(code)
                    for i, code in trash_tools.reasons_of(settings, [int(it["id"]) for it in items]).items()}
        if section == "no_match":
            from webui.pages import nomatch
            kinds = nomatch.kinds_of(settings, [str(it.get("path") or "") for it in items])
            return {int(it["id"]): kinds.get(str(it.get("path") or ""), "") for it in items}
    except Exception as exc:  # the corners are a convenience: the tiles are shown without them
        _LOG.warning("phone gallery: why of the tiles: %s", exc)
    return {}


def _words_link(ctx: dict, word: str, prefix: str) -> str:
    """The address of the section with the word ``word`` (``брак:…`` / ``корзина:…``) instead of the one chosen."""
    rest = " ".join(w for w in ctx["tags"].split() if not w.lower().startswith(prefix))
    return url("/m/posts", tags=" ".join(x for x in (rest, word) if x), section=ctx["section"], source=ctx["source"],
               order=ctx["order"])


def _filters(ctx: dict, prefix: str, links: list, label: str) -> str:
    """The words of the section as chips: [(word, text, count)], a ``None`` — the line between their groups."""
    chosen = next((w.lower() for w in ctx["tags"].split() if w.lower().startswith(prefix)), "")
    out = []
    for link in links:
        if link is None:
            out.append('<span class="m-sep" aria-hidden="true"></span>')
            continue
        word, text, count = link
        mark = ' aria-current="true"' if word == chosen else ""
        out.append(f'<a class="m-filter" href="{esc(_words_link(ctx, word, prefix))}"{mark}>{esc(text)} '
                   f'<span>{count}</span></a>')
    return f'<nav class="m-filters" aria-label="{esc(label)}">{"".join(out)}</nav>'


def _brak_bar(settings: dict, ctx: dict, files_total: int) -> str:
    """«Брак»: what it is, its words ``брак:…`` with their numbers, «Выбрать» and «Искать снова»."""
    from webui.pages import nomatch
    from webui.pages.post_sets import FILES, plural
    numbers = nomatch.counts(settings)
    whole = int(numbers.get("", files_total) or 0)
    head = (f'<p><b>Брак — {esc(plural(whole, FILES))}</b>: картинки, которых парсер не нашёл ни на одном '
            f'сайте.</p>')
    words = ""
    if numbers:
        links = [("", "все", numbers.get("", 0)), *((w, t, numbers.get(w, 0)) for w, t in nomatch.FILTERS[:2]), None,
                 *((w, t, numbers.get(w, 0)) for w, t in nomatch.FILTERS[2:])]
        words = _filters(ctx, "брак:", links, "Какой Брак показать")
    if not files_total:
        return f'<section class="m-secbar">{head}{words}</section>'
    narrowed = bool(ctx["tags"]) or ctx["source"] != "all"
    again = f"Искать снова найденные ({files_total})" if narrowed else "Искать снова весь Брак"
    ask = (f"Искать снова {files_total}? Нажмите ещё раз" if narrowed else "Искать снова весь Брак? Нажмите ещё раз")
    return (f'<section class="m-secbar">{head}{words}</section>'
            f'<div class="m-sel" data-sel-bar data-section="no_match">'
            f'<button type="button" class="m-button m-quiet" data-action="m-select" aria-pressed="false">Выбрать</button>'
            f'<button type="button" class="m-button m-quiet" data-action="m-retry-all" data-tags="{esc(ctx["tags"])}" '
            f'data-source="{esc(ctx["source"])}" data-ask="{esc(ask)}">{esc(again)}</button></div>'
            '<div class="m-actbar" data-actbar hidden><b data-sel-count>0 выбрано</b>'
            '<button type="button" class="m-button" data-action="m-retry" data-sel-go data-go-label="Искать снова" '
            'disabled>Искать снова</button>'
            '<button type="button" class="m-button m-quiet" data-action="m-select-none">снять</button></div>')


def _trash_bar(settings: dict, ctx: dict, files_total: int) -> str:
    """«Корзина»: how much, whether it empties itself, why the cards are there (the words ``корзина:…``), «Выбрать»,
    «Вернуть всё» with its journal — and that deleting for good is the computer's."""
    from core import trash_tools
    from webui.html import human_size
    from webui.pages import trash
    try:
        groups = trash_tools.summary(settings, source=ctx["source"])
    except Exception as exc:  # the line is a convenience: the grid is shown without it
        _LOG.warning("phone trash reasons: %s", exc)
        groups = []
    files = sum(g[2] for g in groups)
    size = sum(g[3] for g in groups)
    head = (f'<p><b>В корзине файлов: {files}</b> ({esc(human_size(size).replace(".", ","))}) — всё это ещё можно '
            f'вернуть.</p><p class="m-muted">{esc(trash.purge_sentence(settings))}.</p>') if groups else ""
    words = ""
    if groups:
        links = [("", "все", files)] + [(trash_tools.WORD + code, text, count) for code, text, count, _s in groups]
        words = _filters(ctx, trash_tools.WORD, links, "Почему в корзине")
    job = ('<div class="m-job" data-job hidden><p class="m-job-head"><b data-job-title></b> '
           '<span data-job-status></span> <button type="button" class="m-link" data-action="m-restore-stop" hidden>'
           'Остановить</button></p><pre class="m-job-log" data-job-log></pre></div>')
    if not files_total:
        return f'<section class="m-secbar">{head}{words}</section>{job}'
    narrowed = bool(ctx["tags"]) or ctx["source"] != "all"
    back = f"Вернуть все найденные ({files_total})" if narrowed else f"Вернуть всё ({files_total})"
    return (f'<section class="m-secbar">{head}{words}</section>'
            f'<div class="m-sel" data-sel-bar data-section="trash">'
            f'<button type="button" class="m-button m-quiet" data-action="m-select" aria-pressed="false">Выбрать</button>'
            f'<button type="button" class="m-button" data-action="m-restore-all" data-tags="{esc(ctx["tags"])}" '
            f'data-source="{esc(ctx["source"])}" data-ask="Вернуть {files_total} из корзины? Нажмите ещё раз">'
            f'{esc(back)}</button></div>'
            f'<p class="m-muted m-pc-only">{esc(TRASH_HERE)}</p>{job}'
            '<div class="m-actbar" data-actbar hidden><b data-sel-count>0 выбрано</b>'
            '<button type="button" class="m-button" data-action="m-restore" data-sel-go data-go-label="Вернуть" disabled>'
            'Вернуть</button>'
            '<button type="button" class="m-button m-quiet" data-action="m-select-none">снять</button></div>')


def _search_form(ctx: dict) -> str:
    keep = "".join(f'<input type="hidden" name="{k}" value="{esc(ctx[k])}">' for k in ("section", "source")
                   if ctx[k] != "all")
    return (f'<form class="m-search" id="m-search" action="/m/posts" method="get" role="search">{keep}'
            f'<label class="m-field">{phone.svg(phone.SEARCH, size=18, width=2)}<span class="m-sr">Поиск по тегам</span>'
            f'<input type="search" name="tags" value="{esc(ctx["tags"])}" placeholder="Теги через пробел" '
            f'autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="search" '
            f'data-hints></label><ul class="m-hints" data-hint-list hidden></ul></form>')


def _sections(ctx: dict) -> str:
    links = []
    for key, label in SECTIONS:
        mark = ' aria-current="page"' if key == ctx["section"] else ""
        href = url("/m/posts", tags=ctx["tags"], section=key, source=ctx["source"], order=ctx["order"])
        links.append(f'<a class="m-chip" href="{esc(href)}"{mark}>{esc(label)}</a>')
    return f'<nav class="m-chips" aria-label="Разделы галереи">{"".join(links)}</nav>'


def _order_select(ctx: dict) -> str:
    options = "".join(f'<option value="{k}"{" selected" if k == ctx["order"] else ""}>{esc(v)}</option>'
                      for k, v in data.ORDERS.items())
    return (f'<label class="m-order"><span class="m-sr">Порядок</span><select name="order" form="m-search" '
            f'data-submit>{options}</select>{phone.svg(phone.DOWN, size=14, width=2)}</label>')


def _empty(ctx: dict, settings: dict | None = None) -> str:
    if ctx["tags"]:
        return (f'<div class="m-empty"><h2>Ничего не нашлось</h2><p>По запросу «{esc(ctx["tags"])}» в этом разделе '
                f'ничего нет. Уберите часть тегов или откройте раздел «Все».</p></div>')
    line, meaning = gallery.empty_words(ctx["section"], settings)       # (v5.44.116) each section its own words
    return f'<div class="m-empty"><h2>{esc(line)}</h2><p>{esc(meaning)}</p></div>'


def posts(req: Request) -> Response:
    ctx = gallery._ctx(req)                          # (v5.44.68) section «videos» — the long videos, as tiles too
    page_no = req.int_arg("page", 1, lo=1)
    items, total, files_total = data.search_page(req.settings, ctx["tags"], section=ctx["section"],
                                                 source=ctx["source"], order=ctx["order"], page=page_no,
                                                 per_page=PER_PAGE)
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    if page_no > pages and total:
        return Response.redirect(url("/m/posts", page=pages, **ctx))
    first = (page_no - 1) * PER_PAGE
    whys = _whys(req.settings, ctx["section"], items) if ctx["section"] in ("trash", "no_match") else {}
    tiles = "".join(_tile(item, ctx, first + n, whys.get(int(item["id"]), "")) for n, item in enumerate(items))
    more = url("/m/posts", page=page_no + 1, **ctx) if page_no < pages else ""
    if req.arg("part"):                               # «Показать ещё» (phone.js): the next tiles only
        return Response.json({"ok": True, "html": tiles, "more": more}, )
    earlier = ""
    if page_no > 1:
        earlier = (f'<p class="m-earlier"><a href="{esc(url("/m/posts", page=page_no - 1, **ctx))}">Показать '
                   f'предыдущие</a></p>')
    if items:
        grid = (f'{earlier}<div class="m-grid" data-grid>{tiles}</div>'
                + (f'<a class="m-more" href="{esc(more)}" data-more>Показать ещё</a>' if more else ""))
        count = (f'<div class="m-count"><span>{_count(files_total)}</span>{_order_select(ctx)}</div>')
    else:
        grid, count = _empty(ctx, req.settings), ""
    bar = ""                                          # (v5.44.118) «Брак» and «Корзина»: why, the words, the choice
    if ctx["section"] == "no_match":
        bar = _brak_bar(req.settings, ctx, files_total if items else 0)
    elif ctx["section"] == "trash":
        bar = _trash_bar(req.settings, ctx, files_total if items else 0)
    body = _search_form(ctx) + _sections(ctx) + bar + count + grid
    title = ctx["tags"] or "Галерея"
    return phone.page(title, body, active="posts", heading="Галерея", top=phone.mode_switch(), kind="m-gallery",
                      **ASSETS)


# ── /m/post/<id> ─────────────────────────────────────────────────────────────────────────────────────────────────
def _added(stamp) -> str:
    try:
        moment = time.localtime(int(stamp))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    day = f"{moment.tm_mday} {MONTHS[moment.tm_mon - 1]}"
    return day if moment.tm_year == time.localtime().tm_year else f"{day} {moment.tm_year}"


def _meta_line(item: dict) -> str:
    parts = []
    if item.get("width") and item.get("height"):
        parts.append(f'{int(item["width"])} × {int(item["height"])}')
    ext = Path(str(item.get("path") or "")).suffix.lstrip(".").upper()
    if ext:
        parts.append(ext)
    if _is_video(item) and float(item.get("duration") or 0) > 0:
        from core.video_probe import human_duration
        parts.append(human_duration(item["duration"]))
    if item.get("size_bytes"):
        parts.append(human_size(int(item["size_bytes"])).replace(".", ","))
    added = _added(item.get("indexed_at"))
    if added:
        parts.append(f"добавлена {added}")
    return " · ".join(parts)


def _tag_chip(name: str, kind: str, ctx: dict | None) -> str:
    """A tag of the card: its name — a search of it; (v5.44.118) «+» — it added to the search the card was opened
    from, «−» — such left out of it (``gallery._add_tag``, as the computer's tag list does)."""
    label = name.replace("_", " ")
    chip = f'<a class="m-tag m-cat-{kind}" href="{esc(url("/m/posts", tags=name))}">{esc(label)}</a>'
    if ctx is None:
        return chip
    plus = url("/m/posts", tags=gallery._add_tag(ctx["tags"], name), section=ctx["section"], source=ctx["source"],
               order=ctx["order"])
    minus = url("/m/posts", tags=gallery._add_tag(ctx["tags"], name, negate=True), section=ctx["section"],
                source=ctx["source"], order=ctx["order"])
    return (f'<span class="m-tagline">{chip}'
            f'<a class="m-tag-op m-tag-plus" href="{esc(plus)}" title="Добавить к поиску" '
            f'aria-label="Добавить «{esc(label)}» к поиску">+</a>'
            f'<a class="m-tag-op m-tag-minus" href="{esc(minus)}" title="Исключить из поиска" '
            f'aria-label="Исключить «{esc(label)}» из поиска">−</a></span>')


def _tags_html(item: dict, ctx: dict | None = None) -> str:
    groups = item.get("tag_groups") or {}
    loose = item.get("loose_groups") or {}
    merged: dict = {}
    for source in (groups, loose):
        for category, names in source.items():
            for name in names:
                merged.setdefault(str(category or "general").lower(), [])
                if name not in merged[str(category or "general").lower()]:
                    merged[str(category or "general").lower()].append(name)
    ordered = [c for c in CATEGORY_ORDER if merged.get(c)] + sorted(c for c in merged if c not in CATEGORY_ORDER)
    rows = []
    for category in ordered:
        kind = category if category in CATEGORY_TITLES else "general"
        chips = "".join(_tag_chip(name, kind, ctx) for name in sorted(merged[category], key=str.lower))
        rows.append(f'<div class="m-tag-row"><span class="m-tag-kind">{esc(CATEGORY_TITLES.get(category, category))}'
                    f'</span><div class="m-tag-list">{chips}</div></div>')
    if not rows:
        return '<p class="m-muted">Тегов нет.</p>'
    return f'<div class="m-tags">{"".join(rows)}</div>'


def _sources_html(item: dict) -> str:
    links, seen = [], set()
    for src in item.get("sources") or []:
        address = str(src.get("url") or "")
        if address and address not in seen and is_web_url(address):
            seen.add(address)
            links.append(f'<a href="{esc(address)}" target="_blank" rel="noopener noreferrer">'
                         f'{esc(src.get("host") or address)}</a>')
    return f'<p class="m-sources">Откуда: {" · ".join(links)}</p>' if links else ""


def _media_html(item: dict, path: Path) -> str:
    image_id = int(item["id"])
    width, height = int(item.get("width") or 0), int(item.get("height") or 0)
    dims = f' width="{width}" height="{height}"' if width > 0 and height > 0 else ""
    original = esc(data.media_url("media", item))
    saving = f'<a class="m-button" href="/media/{image_id}?download=1" download>На телефон</a>'
    if not path.is_file():
        return '<div class="m-problem"><p>Файла этой карточки нет на диске компьютера.</p></div>'
    if _is_video(item):
        return (f'<video class="m-media" src="{original}" poster="{esc(data.media_url("poster", item))}" controls '
                f'playsinline loop preload="metadata"{dims} data-video></video>'
                f'<div class="m-problem" data-video-problem hidden><p>Браузер телефона это видео не показывает '
                f'(формат файла). Его можно сохранить на телефон и открыть видеоплеером.</p>{saving}</div>')
    shown = original if path.suffix.lower() in ORIGINAL_SHOWN else esc(data.media_url("poster", item))
    return (f'<a class="m-zoom" href="{original}" aria-label="Открыть в полном размере">'
            f'<img class="m-media" src="{shown}" alt=""{dims} decoding="async"></a>')


TRASH_ICON = '<path d="M4 7h16"/><path d="M9 7V4.5h6V7"/><path d="M6.5 7l1 13h9l1-13"/><path d="M10 11v6M14 11v6"/>'
BACK_ICON = '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>'


def _trash_act(item: dict) -> str:
    """(v5.44.118) «В корзину» (a second press within 4 s, as on the computer) — or, for a card in the trash,
    «Вернуть»."""
    image_id = int(item["id"])
    if int(item.get("deleted") or 0):
        return (f'<button type="button" class="m-act" data-action="m-restore-card" data-id="{image_id}">'
                f'{phone.svg(BACK_ICON, size=20)}<span data-label>Вернуть</span></button>')
    return (f'<button type="button" class="m-act m-act-trash" data-action="m-trash" data-id="{image_id}" '
            f'data-ask="Точно в корзину? Нажмите ещё раз">{phone.svg(TRASH_ICON, size=20)}'
            f'<span data-label>В корзину</span></button>')


def _rating(item: dict) -> str:
    """(v5.44.118) The rating 1…5 of the card — the computer's (``/api/post/<n>/rating``); the star pressed again
    takes it off."""
    image_id = int(item["id"])
    rating = max(0, min(5, int(item.get("rating") or 0)))
    stars = "".join(
        f'<button type="button" class="m-rate-star{" on" if n <= rating else ""}" data-action="m-rate" '
        f'data-id="{image_id}" data-value="{n}" aria-label="Оценка {n}" '
        f'aria-pressed="{"true" if n == rating else "false"}">★</button>' for n in range(1, 6))
    return (f'<div class="m-rate" role="group" aria-label="Оценка" data-rating="{rating}"><span>Оценка</span>'
            f'<div class="m-rate-stars">{stars}</div></div>')


def _why_box(req: Request, item: dict) -> str:
    """(v5.44.118) Why the card is in Брак (the parser's reason, «фото или рисунок») or in the trash (the reason, with
    what, since when)."""
    if int(item.get("deleted") or 0):
        from core import trash_tools
        target = str(item.get("trash_target") or "")
        line = trash_tools.label(item.get("trash_reason")) + (f" ({target})" if target else "")
        since = _added(item.get("trashed_at"))
        return (f'<div class="m-why-box"><b>Почему в корзине</b><p>{esc(line)}'
                + (f' · в корзине с {esc(since)}' if since else "") + f'</p><p class="m-muted">{esc(TRASH_HERE)}</p>'
                '</div>')
    from webui.pages import nomatch
    if str(item.get("bucket") or "") not in nomatch.BRAK_BUCKETS:
        return ""
    lines = "".join(f"<p>{esc(line)}</p>" for line in nomatch.why_lines(req.settings, item))
    return f'<div class="m-why-box"><b>Почему в Браке</b>{lines}</div>'


def _set_strip(req: Request, ctx: dict, members: list, image_id: int, index: int) -> str:
    links = []
    for member in members:
        mid = int(member["id"])
        mark = ' aria-current="true"' if mid == image_id else ""
        href = url(f"/m/post/{mid}", i=index if index >= 0 else None, **ctx)
        links.append(f'<a href="{esc(href)}" data-go{mark}><img src="{esc(data.media_url("thumb", member))}" alt="" '
                     f'loading="lazy"></a>')
    return f'<div class="m-set" id="m-set" hidden>{"".join(links)}</div>'


def post(req: Request) -> Response:
    image_id = int(req.params["id"])
    ctx = gallery._ctx(req)
    index = req.int_arg("i", -1)
    back = url("/m/posts", page=index // PER_PAGE + 1 if index >= 0 else None, **ctx) + (
        f"#i{index}" if index >= 0 else "")
    item = data.post_detail(req.settings, image_id)
    if item is None:
        body = ('<div class="m-empty"><h2>Такой карточки нет</h2><p>Возможно, её удалили навсегда.</p></div>')
        return phone.page("Нет такой карточки", body, status=404, back=back, heading="Картинка", tabs=False,
                          kind="m-viewer")
    near = gallery.neighbours(req, ctx, index, image_id)
    members = near.get("members") if near else gallery._set_members(req, ctx, image_id)
    members = members or []
    place = next((n for n, m in enumerate(members) if int(m["id"]) == image_id), -1)

    def link(card, at) -> str:
        return url(f"/m/post/{int(card['id'])}", i=at, **ctx) if card else ""
    prev_href = link(near.get("prev"), near.get("prev_at")) if near else ""
    next_href = link(near.get("next"), near.get("next_at")) if near else ""
    position = ""
    if index >= 0:
        total = data.count_tiles(req.settings, ctx["tags"], section=ctx["section"], source=ctx["source"])
        position = (f"{index + 1:,} из {total:,}".replace(",", " ") if total else "")
    path = Path(str(item.get("path") or ""))
    arrows = ""
    if prev_href:
        arrows += (f'<a class="m-arrow m-prev" href="{esc(prev_href)}" rel="prev" data-go '
                   f'aria-label="Предыдущая">{phone.svg(phone.BACK, size=22, width=2)}</a>')
    if next_href:
        arrows += (f'<a class="m-arrow m-next" href="{esc(next_href)}" rel="next" data-go '
                   f'aria-label="Следующая">{phone.svg(phone.FORTH, size=22, width=2)}</a>')
    stage = (f'<div class="m-stage" data-stage data-index="{index}" data-prev="{esc(prev_href)}" '
             f'data-next="{esc(next_href)}">{_media_html(item, path)}{arrows}</div>')
    fav = bool(item.get("favorite"))
    actions = [f'<button type="button" class="m-act" data-fav="{image_id}" aria-pressed="{"true" if fav else "false"}">'
               f'{phone.svg(phone.STAR, size=20, width=1.6)}<span data-label>{"В избранном" if fav else "В избранное"}'
               f'</span></button>']
    if len(members) >= 2:
        actions.append(f'<button type="button" class="m-act" data-set-toggle aria-expanded="false" aria-controls="m-set">'
                       f'{phone.svg(phone.SET, size=20)}<span>Сборка · {place + 1} из {len(members)}</span></button>')
    elif path.is_file() and not _is_video(item):
        actions.append(f'<a class="m-act" href="{esc(data.media_url("media", item))}">{phone.svg(phone.FULL, size=20)}'
                       f'<span>Оригинал</span></a>')
    if path.is_file():
        actions.append(f'<a class="m-act" href="/media/{image_id}?download=1" download>{phone.svg(phone.SAVE, size=20)}'
                       f'<span>На телефон</span></a>')
    actions.append(_trash_act(item))                 # (v5.44.118) «В корзину» / «Вернуть»
    strip = _set_strip(req, ctx, members, image_id, index) if len(members) >= 2 else ""
    sheet = (f'<section class="m-sheet" aria-label="О файле"><span class="m-handle" aria-hidden="true"></span>'
             f'<div class="m-acts" style="--acts:{len(actions)}">{"".join(actions)}</div>{strip}'
             f'{_rating(item)}{_why_box(req, item)}'
             f'<p class="m-meta">{esc(_meta_line(item))}</p>{_tags_html(item, ctx)}{_sources_html(item)}</section>')
    prefetch = ""
    following = near.get("next") if near else None
    if following and not _is_video(following):
        kind = "media" if Path(str(following.get("path") or "")).suffix.lower() in ORIGINAL_SHOWN else "poster"
        prefetch = f'<link rel="prefetch" href="{esc(data.media_url(kind, following))}">'
    what = "Видео" if _is_video(item) else "Картинка"
    return phone.page(what, prefetch + stage + sheet, back=back, heading=position or what, tabs=False,
                      kind="m-viewer", **ASSETS)
