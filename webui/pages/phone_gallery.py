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
SECTIONS = (("all", "Все"), ("inbox", "Новые"), ("favorites", "Избранное"), ("downloaded", "Скачанные"),
            ("found", "Найденные"))
ORIGINAL_SHOWN = {".gif", ".webp", ".apng"}      # may move: the file itself, not its still preview
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


def _tile(item: dict, ctx: dict, index: int) -> str:
    """One square of the grid: the card's preview, its kind or its сборка, the star of the favourite."""
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
    return (f'<a class="m-tile" id="i{index}" href="{esc(href)}" data-index="{index}" aria-label="{esc(what)}">'
            f'<img src="{esc(data.media_url("thumb", item))}" alt="" loading="lazy" decoding="async">'
            + (f'<span class="m-badge">{esc(badge)}</span>' if badge else "") + f"{star}</a>")


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


def _empty(ctx: dict) -> str:
    if ctx["tags"]:
        return (f'<div class="m-empty"><h2>Ничего не нашлось</h2><p>По запросу «{esc(ctx["tags"])}» в этом разделе '
                f'ничего нет. Уберите часть тегов или откройте раздел «Все».</p></div>')
    return ('<div class="m-empty"><h2>Здесь пока пусто</h2><p>Картинки появятся, когда парсер разберёт папку с '
            'ними или граббер что-нибудь скачает.</p></div>')


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
    tiles = "".join(_tile(item, ctx, first + n) for n, item in enumerate(items))
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
        grid, count = _empty(ctx), ""
    body = _search_form(ctx) + _sections(ctx) + count + grid
    title = ctx["tags"] or "Галерея"
    return phone.page(title, body, active="posts", heading="Галерея", top=phone.mode_switch(), kind="m-gallery")


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


def _tags_html(item: dict) -> str:
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
        chips = "".join(
            f'<a class="m-tag m-cat-{kind}" href="{esc(url("/m/posts", tags=name))}">{esc(name.replace("_", " "))}</a>'
            for name in sorted(merged[category], key=str.lower))
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
    strip = _set_strip(req, ctx, members, image_id, index) if len(members) >= 2 else ""
    sheet = (f'<section class="m-sheet" aria-label="О файле"><span class="m-handle" aria-hidden="true"></span>'
             f'<div class="m-acts" style="--acts:{len(actions)}">{"".join(actions)}</div>{strip}'
             f'<p class="m-meta">{esc(_meta_line(item))}</p>{_tags_html(item)}{_sources_html(item)}</section>')
    prefetch = ""
    following = near.get("next") if near else None
    if following and not _is_video(following):
        kind = "media" if Path(str(following.get("path") or "")).suffix.lower() in ORIGINAL_SHOWN else "poster"
        prefetch = f'<link rel="prefetch" href="{esc(data.media_url(kind, following))}">'
    what = "Видео" if _is_video(item) else "Картинка"
    return phone.page(what, prefetch + stage + sheet, back=back, heading=position or what, tabs=False,
                      kind="m-viewer")
