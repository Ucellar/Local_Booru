"""Gallery pages in the classic booru layout: post list, post view, tag list."""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

from core import video_library
from core.external_browser import is_web_url
from core.tag_utils import normalize_tag
from webui import data, icons
from webui.html import (
    CATEGORY_ORDER, CATEGORY_TITLES, THUMB_SIZES, category_class, esc, human_count, human_size, page_frame,
    paginator, search_box, url,
)
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.webui")

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"}


def register(router) -> None:
    router.add("GET", r"/", _home)
    router.add("GET", r"/posts", posts)
    router.add("GET", r"/post/(?P<id>\d+)", post)
    router.add("GET", r"/tags", tags)
    router.add("GET", r"/random", random_post)


def _home(req: Request) -> Response:
    """«/»: the gallery — (v5.44.53) in «SFW» the manga (``webui/content_mode.py``)."""
    from webui import content_mode
    return Response.redirect(content_mode.home())


# ── shared bits ──────────────────────────────────────────────────────────────
def _ctx(req: Request) -> dict:
    """The search a page belongs to; kept in every link so «назад», «дальше» and the sidebar agree."""
    return {
        "tags": req.arg("tags").strip(),
        "section": req.arg("section", "all") or "all",
        "source": req.arg("source", "all") or "all",
        "order": req.arg("order", "newest") or "newest",
    }


FIT_COOKIE = "lb_fit_pp"     # how many pictures fit the window of this browser (booru.js counts it)
FIT_FIRST = 42               # before the window has counted


def _per_page(req: Request) -> int:
    """Pictures on a page: the number set on «Настройки → Галерея», or — 0, by default — as many as fit the window
    without scrolling (columns × rows of the look and the thumbnail size), counted by booru.js into a cookie of
    this browser: the program window and a browser on another screen keep each their own."""
    try:
        configured = int(req.settings.get("webui_posts_per_page") or 0)
    except (TypeError, ValueError):
        configured = 0
    if configured > 0:
        return max(10, min(200, configured))
    from http.cookies import CookieError, SimpleCookie
    try:
        cookie = SimpleCookie(req.headers.get("Cookie", ""))
        fit = int(cookie[FIT_COOKIE].value) if FIT_COOKIE in cookie else 0
    except (CookieError, ValueError, TypeError):
        fit = 0
    return max(2, min(400, fit)) if fit > 0 else FIT_FIRST


def _fits_window(req: Request) -> bool:
    try:
        return int(req.settings.get("webui_posts_per_page") or 0) <= 0
    except (TypeError, ValueError):
        return True


def _add_tag(query: str, tag: str, negate: bool = False) -> str:
    token = ("-" if negate else "") + tag
    parts = [p for p in query.split() if p.lstrip("-") != tag]
    return " ".join(parts + [token]).strip()


def _tag_line(name: str, count: int, category: str, ctx: dict, colors: dict | None = None) -> str:
    """One sidebar tag: + and − refine the current search, the name starts a new one.
    A colour chosen for the tag (right click on it) wins over its category colour."""
    q = ctx["tags"]
    plus = url("/posts", tags=_add_tag(q, name), section=ctx["section"], source=ctx["source"], order=ctx["order"])
    minus = url("/posts", tags=_add_tag(q, name, negate=True), section=ctx["section"], source=ctx["source"],
                order=ctx["order"])
    only = url("/posts", tags=name, section=ctx["section"], source=ctx["source"], order=ctx["order"])
    label = esc(name.replace("_", " "))
    key = normalize_tag(name).lower()
    color = (colors or {}).get(key)
    style = f' style="color:{esc(color)}"' if color else ""
    about = url("/tags", name=name)
    return (f'<li class="{category_class(category)}">'
            f'<a class="tag-wiki" href="{esc(about)}" title="Этот тег в списке тегов: тип, сколько картинок">?</a>'
            f'<a class="tag-op" href="{esc(plus)}" title="Добавить к поиску">+</a>'
            f'<a class="tag-op" href="{esc(minus)}" title="Исключить из поиска">−</a>'
            f'<a class="tag-name" href="{esc(only)}" data-tag="{esc(key)}"{style}>{label}</a>'
            f'<span class="tag-count">{human_count(count)}</span></li>')


def _tag_colors(req: Request) -> dict:
    colors = req.settings.get("tag_colors") or {}
    return {str(k).lower(): str(v) for k, v in colors.items() if v} if isinstance(colors, dict) else {}


def _tag_groups_html(groups: dict, ctx: dict, counts: dict | None = None, *, heading_level: str = "h4",
                     colors: dict | None = None) -> str:
    out = []
    ordered = [c for c in CATEGORY_ORDER if groups.get(c)] + [c for c in groups if c not in CATEGORY_ORDER and groups.get(c)]
    for category in ordered:
        lines = []
        for entry in groups[category]:
            if isinstance(entry, tuple):
                name, count = entry
            else:
                name = entry
                count = (counts or {}).get(normalize_tag(name), (name, category, 0))[2]
            lines.append(_tag_line(name, count, category, ctx, colors))
        title = CATEGORY_TITLES.get(category, category)
        out.append(f'<{heading_level}>{esc(title)}</{heading_level}><ul class="tag-list">{"".join(lines)}</ul>')
    return "".join(out)


def _thumb(item: dict, ctx: dict, index: int) -> str:
    href = url(f"/post/{item['id']}", tags=ctx["tags"], section=ctx["section"], source=ctx["source"],
               order=ctx["order"], i=index)
    groups = item.get("tag_groups") or {}
    names = [t for values in groups.values() for t in values]
    title = " ".join(sorted(names)[:80])
    ext = Path(str(item.get("path") or "")).suffix.lower()
    is_video = bool(item.get("is_video")) or ext in VIDEO_EXTS
    badges = []
    if is_video:
        badges.append('<span class="badge kind">видео</span>')
    elif ext == ".gif":
        badges.append('<span class="badge kind">gif</span>')
    in_set = int(item.get("set_count") or 0) >= 2
    if in_set:                                       # one tile for a whole сборка (core/card_sets.py)
        from webui.pages.post_sets import CARDS, plural
        badges.append(f'<span class="badge set" title="Сборка: {plural(int(item["set_count"]), CARDS)} — '
                      f'листаются на странице карточки">⧉ {int(item["set_count"])}</span>')
    elif int(item.get("family_total") or 0) > 1:
        badges.append(f'<span class="badge" title="Серия">{int(item.get("family_index") or 0) + 1}/{int(item["family_total"])}</span>')
    if item.get("favorite"):
        badges.append('<span class="badge fav" title="В избранном">♥</span>')
    width, height = int(item.get("width") or 0), int(item.get("height") or 0)
    ratio = max(0.3, min(3.5, width / height)) if width and height else 1.0
    # The line a look may show over or under the picture: who drew it and who is in it.
    lead = [t for cat in ("artist", "character", "copyright") for t in groups.get(cat, [])][:3]
    caption = " · ".join(t.replace("_", " ") for t in lead) or ("без тегов" if not names else names[0].replace("_", " "))
    info = []
    duration = float(item.get("duration") or 0)
    if is_video and duration > 0:
        from core.video_probe import human_duration
        info.append(f'<span class="ti-dur" title="Длина видео">{human_duration(duration)}</span>')
    if width and height:
        info.append(f'<span class="ti-size">{width}×{height}</span>')
    info.append(f'<span class="ti-kind">{esc(ext.lstrip(".").upper() or "?")}</span>')
    rating = int(item.get("rating") or 0)
    if rating:
        info.append(f'<span class="ti-rating" title="Оценка">★{rating}</span>')
    if item.get("favorite"):
        info.append('<span class="ti-fav" title="В избранном">♥</span>')
    cls = "thumb"
    if not names:
        cls += " untagged"
    if is_video:
        cls += " is-video"
    if in_set:
        cls += " is-set"
    # (v5.44.118) a сборка's tile is all its files: «В корзину выбранные» says how many go (tools.js)
    set_attr = f' data-set="{int(item["set_id"])}" data-files="{int(item["set_count"])}"' if in_set else ""
    return (f'<article class="{cls}" style="--ar:{ratio:.4f}" data-id="{int(item["id"])}"{set_attr}>'
            f'<a class="thumb-link" href="{esc(href)}" title="{esc(title)}">'
            f'<img src="{esc(data.media_url("thumb", item))}" alt="" loading="lazy" decoding="async">'
            f'<span class="thumb-caption">{esc(caption)}</span>'
            f'<span class="badges">{"".join(badges)}</span></a>'
            f'<div class="thumb-info">{"".join(info)}</div></article>')


def _tag_chips(groups: dict, ctx: dict, limit: int = 18) -> str:
    """The most used tags of this page as a row of chips (the looks without a tag column show it)."""
    ranked = []
    for category, entries in groups.items():
        for name, count in entries:
            ranked.append((count, name, category))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    chips = []
    for count, name, category in ranked[:limit]:
        href = url("/posts", tags=_add_tag(ctx["tags"], name), section=ctx["section"], source=ctx["source"],
                   order=ctx["order"])
        chips.append(f'<a class="chip {category_class(category)}" href="{esc(href)}" title="Добавить к поиску">'
                     f'{esc(name.replace("_", " "))}<span class="tag-count">{human_count(count)}</span></a>')
    if not chips:
        return ""
    return f'<nav class="tag-chips" aria-label="Теги этой страницы">{"".join(chips)}</nav>'


def _sources_box(settings: dict, ctx: dict) -> str:
    rows = data.source_hosts(settings)
    if not rows:
        return ""
    links = []
    cls = ' class="active"' if ctx["source"] == "all" else ""
    links.append(f'<li><a{cls} href="{esc(url("/posts", tags=ctx["tags"], section=ctx["section"], order=ctx["order"]))}">все сайты</a></li>')
    for host, count in rows:
        cls = ' class="active"' if ctx["source"] == host else ""
        href = url("/posts", tags=ctx["tags"], section=ctx["section"], source=host, order=ctx["order"])
        links.append(f'<li><a{cls} href="{esc(href)}">{esc(host)}</a> <span class="tag-count">{human_count(count)}</span></li>')
    return f'<section class="box"><h3>Сайты</h3><ul class="plain-list">{"".join(links)}</ul></section>'


def _order_box(ctx: dict) -> str:
    links = []
    for key, label in data.ORDERS.items():
        cls = ' class="active"' if ctx["order"] == key else ""
        href = url("/posts", tags=ctx["tags"], section=ctx["section"], source=ctx["source"], order=key)
        links.append(f'<li><a{cls} href="{esc(href)}">{esc(label)}</a></li>')
    return f'<section class="box"><h3>Порядок</h3><ul class="plain-list">{"".join(links)}</ul></section>'


# ── /posts ───────────────────────────────────────────────────────────────────
def posts(req: Request) -> Response:
    ctx = _ctx(req)
    if ctx["section"] == data.VIDEO_SECTION:          # the long videos are the tab «Видео»
        return Response.redirect(url("/videos", tags=ctx["tags"], order=ctx["order"], source=ctx["source"],
                                     page=req.int_arg("page", 1, lo=1)))
    per_page = _per_page(req)
    page = req.int_arg("page", 1, lo=1)
    items, total, files_total = data.search_page(req.settings, ctx["tags"], section=ctx["section"], source=ctx["source"],
                                                 order=ctx["order"], page=page, per_page=per_page)
    add_to = _add_to(req) if ctx["section"] not in {"trash", "no_match"} else None
    keep = {"add_to": add_to["id"]} if add_to else {}         # choosing cards for a сборка goes on over the pages
    pages = max(1, (total + per_page - 1) // per_page)
    if page > pages and total:
        return Response.redirect(url("/posts", page=pages, **ctx, **keep))
    groups = data.sidebar_tags(req.settings, items)
    sidebar = (search_box(ctx["tags"], section=ctx["section"], source=ctx["source"], order=ctx["order"], keep=keep)
               + f'<section class="box tags-box"><h3>Теги</h3>{_tag_groups_html(groups, ctx, colors=_tag_colors(req))}</section>'
               + _sources_box(req.settings, ctx) + _order_box(ctx))
    if items:
        first = (page - 1) * per_page
        grid = '<div class="thumbs">' + "".join(_thumb(it, ctx, first + n) for n, it in enumerate(items)) + "</div>"
    else:
        grid = _empty_result(ctx, req.settings)
    summary = _result_bar(total, files_total, req) if total else ""
    summary += _long_videos_note(req, ctx)
    labels = data.split_query(ctx["tags"])[3]
    if labels:
        summary = (f'<p class="filter-line">Условия: {" · ".join(esc(x) for x in labels)}</p>') + summary
    brak = ""
    if ctx["section"] == "no_match":
        from webui.pages import nomatch
        brak = nomatch.filter_bar(req.settings, ctx)
    elif ctx["section"] == "trash":
        from webui.pages import trash
        brak = trash.bar(req.settings, ctx)
    body = (_tag_chips(groups, ctx) + summary + brak + _select_bar(ctx, total, add_to, files_total) + grid
            + paginator(page, pages, lambda n: url("/posts", page=n, **ctx, **keep)))
    title = ctx["tags"] or "Галерея"
    # booru.js counts how many pictures fit the window and asks again with that many (``_per_page``)
    paging = {"per-page": per_page, "page": page, "total": total, "fit-page": "1" if _fits_window(req) else "0"}
    return Response.html(page_frame(title, body, active="posts", search=ctx["tags"], section=ctx["section"],
                                    sidebar=sidebar, top_search=_top_search(ctx, keep), body_data=paging, **_prefs(req)))


def _long_videos_note(req: Request, ctx: dict) -> str:
    """A search whose long videos are on the tab «Видео» says so (they are not in the gallery)."""
    if ctx["section"] in data.ALL_LENGTHS:
        return ""
    found = data.long_videos_found(req.settings, ctx["tags"], source=ctx["source"])
    if not found:
        return ""
    href = url("/videos", tags=ctx["tags"], source=ctx["source"])
    return (f'<p class="filter-line long-videos">Ещё {found} длинных видео по этому поиску — '
            f'<a href="{esc(href)}">на вкладке «Видео»</a>.</p>')


def _select_bar(ctx: dict, total: int, add_to: dict | None = None, files_total: int = 0) -> str:
    """Actions on many cards (static/tools.js): «Выбрать», then clicks select pictures instead of opening them.
    «Корзина» and «Брак» — their own actions; the rest of the gallery — сборки (core/card_sets.py): «Собрать в одну
    сборку», «Разобрать сборки»; ``add_to`` — the page was opened to choose cards for that сборка.  (v5.44.118) Beside
    them (not in the trash) — «В корзину выбранные» and, with a search, «В корзину все найденные (N)»
    (``trash.move_buttons``; ``files_total`` — the cards of the search) with its journal under the bar."""
    section = ctx["section"]
    if not total:
        return ""
    from webui.pages import trash
    if section not in {"trash", "no_match"}:
        return _sets_select_bar(add_to, ctx, files_total)
    if section == "trash":
        chosen = ('<button type="button" data-action="trash-restore" data-needs-selection disabled>Вернуть выбранные</button>'
                  '<button type="button" class="danger" data-action="trash-purge" data-needs-selection disabled>'
                  'Удалить выбранные навсегда</button>')
        whole = (trash.restore_button(ctx, total)
                 + '<button type="button" class="danger" data-action="trash-purge-all">Очистить корзину</button>')
        job = ""
    else:
        from webui.pages import tools
        to_trash, all_to_trash = trash.move_buttons(ctx, files_total)
        chosen = ('<button type="button" data-action="nomatch-retry" data-needs-selection disabled '
                  'title="Ещё раз через парсер: сайты по MD5 и обратный поиск">Искать снова выбранные</button>'
                  + to_trash)
        whole = tools.retry_button(ctx, total) + all_to_trash   # (v5.44.116) a search in the address: the cards found
        job = trash.move_job()
    return (f'<div class="select-bar" data-section="{esc(section)}">'
            '<button type="button" data-action="select-mode" aria-pressed="false">Выбрать</button>'
            '<span class="select-tools" hidden>'
            '<button type="button" class="link" data-action="select-page">все на странице</button>'
            '<button type="button" class="link" data-action="select-none">снять</button>'
            f'<span class="select-count">0 выбрано</span>{chosen}</span>{whole}'
            f'<span class="action-status" role="status"></span></div>{job}')


def _sets_select_bar(add_to: dict | None, ctx: dict | None = None, files_total: int = 0) -> str:
    if add_to:
        name = f"«{add_to['title']}»" if add_to.get("title") else f"из {add_to['count']} карточек"
        return (f'<div class="select-bar sets-add" data-section="sets" data-add-to="{int(add_to["id"])}" '
                f'data-back="{esc(add_to["back"])}">'
                f'<p class="select-lead">Выберите карточки, которые добавить в сборку {esc(name)} — плитка сборки '
                f'добавляет её всю.</p>'
                '<button type="button" data-action="select-mode" aria-pressed="false">Выбрать</button>'
                '<span class="select-tools" hidden>'
                '<button type="button" class="link" data-action="select-page">все на странице</button>'
                '<button type="button" class="link" data-action="select-none">снять</button>'
                '<span class="select-count">0 выбрано</span>'
                '<button type="button" data-action="sets-add" data-needs-selection disabled>Добавить выбранные</button>'
                '</span>'
                f'<a class="sets-add-cancel" href="{esc(add_to["back"])}">Отмена</a>'
                '<span class="action-status" role="status"></span></div>')
    from webui.pages import trash
    to_trash, all_to_trash = trash.move_buttons(ctx, files_total) if ctx is not None else ("", "")
    return ('<div class="select-bar" data-section="sets">'
            '<button type="button" data-action="select-mode" aria-pressed="false" '
            'title="Выбрать карточки, чтобы собрать их в одну сборку, разобрать сборки или убрать в корзину">'
            'Выбрать</button>'
            '<span class="select-tools" hidden>'
            '<button type="button" class="link" data-action="select-page">все на странице</button>'
            '<button type="button" class="link" data-action="select-none">снять</button>'
            '<span class="select-count">0 выбрано</span>'
            '<button type="button" data-action="sets-gather" data-needs-selection disabled '
            'title="Выбранные карточки — одной сборкой (плитка сборки — со всеми её карточками)">Собрать в одну сборку</button>'
            '<button type="button" data-action="sets-dissolve" data-needs-selection disabled '
            'title="Сборки выбранных плиток — снова отдельными карточками">Разобрать сборки</button>'
            f'{to_trash}</span>{all_to_trash}<span class="action-status" role="status"></span></div>'
            + (trash.move_job() if ctx is not None else ""))


def _add_to(req: Request) -> dict | None:
    """The сборка the gallery was opened to choose cards for (``?add_to=<set>``), or None."""
    from core import card_sets
    set_id = req.int_arg("add_to", 0)
    if set_id <= 0:
        return None
    try:
        found = card_sets.describe_set(req.settings, set_id)
    except sqlite3.Error as exc:
        _LOG.debug("set %s: %s", set_id, exc)
        return None
    if not found:
        return None
    return {**found, "back": f"/post/{found['first']}"}


def _top_search(ctx: dict, keep: dict | None = None) -> str:
    return search_box(ctx["tags"], section=ctx["section"], source=ctx["source"], order=ctx["order"], variant="top",
                      keep=keep)


def _result_bar(total: int, files_total: int, req: Request) -> str:
    current = _prefs(req)["thumbs"]
    sizes = "".join(
        f'<button type="button" class="link{" active" if code == current else ""}" data-action="thumb-size" '
        f'data-size="{code}" aria-pressed="{"true" if code == current else "false"}">{label}</button>'
        for code, label in THUMB_SIZES)
    ctx = _ctx(req)
    export = (f'<button type="button" class="link" data-action="export-results" data-tags="{esc(ctx["tags"])}" '
              f'data-section="{esc(ctx["section"])}" data-source="{esc(ctx["source"])}" '
              f'title="Теги и источники всех найденных картинок в файл JSON или CSV">экспорт тегов</button>')
    lucky = (f'<a class="random-link" href="{esc(url("/random", **ctx))}" '
             f'title="Открыть случайную карточку из найденных">случайная</a>')
    # (v5.44.46) the files found come first (the owner: «число плиток должно показывать число файлов найденных с
    # тегами»); when a сборка stands as one tile for several files, the tiles follow — the pages are counted by them
    from webui.pages.post_sets import FILES, TILES, plural
    count = (f'{plural(files_total, FILES)} · {plural(total, TILES)}' if files_total > total
             else plural(files_total, FILES))
    tip = (' title="Найдено файлов · плиток в галерее: сборка (серия, дочерние одного поста) — одна плитка"'
           if files_total > total else "")
    return (f'<div class="result-bar"><p class="result-count"{tip}>{count}</p>'
            f'<div class="thumb-sizes" role="group" aria-label="Размер превью">{sizes}</div>{lucky}{export}</div>')


def empty_words(section: str, settings: dict | None = None) -> tuple[str, str]:
    """(v5.44.116) What an empty section of the gallery says — (the line, what it means) — on the computer
    (``_empty_result``) and on the phone (``phone_gallery._empty``) alike: the review found one text for «Избранное»,
    «Брак», «Новые» and «Скачанные» («…когда парсер разберёт папку»); an empty Брак is good news, «Избранное» says
    how to add."""
    if section == "inbox":
        if not (settings or {}).get("imports_to_inbox", True):
            return ("Новых картинок нет", "Новые файлы сюда не попадают — так выбрано в «Настройки → Библиотека».")
        try:
            hours = max(1, int((settings or {}).get("inbox_auto_archive_hours", 24) or 24))
        except (TypeError, ValueError):
            hours = 24
        return ("Новых картинок нет", f"Найденное парсером и скачанное лежит здесь первые {hours} ч, потом уходит "
                                      "из этого раздела само.")
    words = {
        "found": ("Найденных пока нет", "Здесь картинки, для которых парсер нашёл теги на сайтах."),
        "downloaded": ("Скачанного пока нет", "Здесь то, что скачали подписки, «Скачать → Галерея (booru)» и "
                                              "расширение браузера."),
        "no_match": ("Брак пуст — это хорошо", "Сюда попадают картинки, которых парсер не нашёл ни на одном сайте; "
                                                "сейчас таких нет."),
        "favorites": ("В избранном пока ничего нет", "Откройте картинку и нажмите «В избранное» — она появится здесь."),
        "trash": ("Корзина пуста", "Сюда уходят удалённые карточки — отсюда их можно вернуть."),
        data.VIDEO_SECTION: ("Длинных видео нет", "Здесь видео библиотеки длиннее, чем выбрано в настройках "
                                                   "(«Вкладка «Видео»»)."),
    }
    return words.get(section, ("Здесь пока пусто", "Картинки появятся, когда парсер разберёт папку с ними или "
                                                    "что-нибудь скачается в «Скачать → Галерея (booru)»."))


def _empty_result(ctx: dict, settings: dict | None = None) -> str:
    if ctx["tags"]:
        return (f'<div class="empty"><p>По запросу «{esc(ctx["tags"])}» ничего нет.</p>'
                '<p>Уберите часть тегов или поищите в другом разделе.</p></div>')
    line, meaning = empty_words(ctx["section"], settings)
    return f'<div class="empty"><p>{esc(line)}.</p><p>{esc(meaning)}</p></div>'


# ── /post/<id> ───────────────────────────────────────────────────────────────
def post(req: Request) -> Response:
    image_id = int(req.params["id"])
    item = data.post_detail(req.settings, image_id)
    if item is None:
        return Response.html(page_frame("Нет такой карточки", '<div class="empty"><p>Такой карточки нет — '
                                        'возможно, её удалили навсегда.</p></div>', **_prefs(req)), 404)
    ctx = _ctx(req)
    index = req.int_arg("i", -1)
    tag_source = req.arg("tag_source", "all") or "all"
    counts = data.TAG_COUNTS.get(req.settings)

    by_source = item.get("tag_groups_by_source") or {}
    shown_groups = item["tag_groups"] if tag_source == "all" else by_source.get(tag_source, {})
    colors = _tag_colors(req)
    tags_html = _tag_groups_html(shown_groups, ctx, counts, colors=colors)
    if item.get("loose_groups") and tag_source == "all":
        tags_html += ('<h4 class="loose-title">Без сайта</h4>'
                      + _tag_groups_html(item["loose_groups"], ctx, counts, heading_level="h5", colors=colors))
    source_tabs = ""
    if len(by_source) > 1:
        tabs = []
        for host in ["all", *sorted(by_source)]:
            label = "все сайты" if host == "all" else host
            cls = ' class="active"' if host == tag_source else ""
            href = url(f"/post/{image_id}", tag_source=host, i=index if index >= 0 else None, **ctx)
            tabs.append(f'<a{cls} href="{esc(href)}">{esc(label)}</a>')
        source_tabs = f'<nav class="source-tabs" aria-label="Теги какого сайта показать">{"".join(tabs)}</nav>'

    from webui.pages import nomatch
    sidebar = (search_box(ctx["tags"], section=ctx["section"], source=ctx["source"], order=ctx["order"])
               + nomatch.box(req.settings, item)
               + f'<section class="box"><h3>Теги</h3>{source_tabs}{tags_html or "<p class=hint>Тегов нет.</p>"}</section>'
               + _info_box(item) + _actions_box(item))

    path = Path(str(item.get("path") or ""))
    media_url = esc(data.media_url("media", item))
    poster_url = esc(data.media_url("poster", item))
    in_app = "QtWebEngine" in req.headers.get("User-Agent", "")
    if path.suffix.lower() in VIDEO_EXTS or item.get("is_video"):
        from core import video_probe
        player = window_player(req, path) if in_app else video_probe.BROWSER
        # the other player of the window, when this one cannot play the file (once: that page is told which it is)
        other = ""
        if in_app and req.arg("player") not in (video_probe.BROWSER, video_probe.OWN):
            other = url(f"/post/{image_id}", i=index if index >= 0 else None, tag_source=tag_source,
                        player=video_probe.OWN if player == video_probe.BROWSER else video_probe.BROWSER, **ctx)
        # (v5.44.121) a long video (the tab «Видео»): played once — at its end «Видео закончилось» —, its place kept
        # («Продолжить с 12:34», a bar on its tile: videos.js, /api/videos/card-progress); a short one goes round
        long_video = data.is_long_video(req.settings, item)
        memory = {}
        if long_video:
            from webui.pages.videos import card_place
            place = card_place(item)
            start = 0.0 if place.get("done") else float(place.get("at") or 0)
            memory = {"loop": "0", "memory": "1", "from": f"{start:.1f}", "resume": f"{start:.1f}"}
            if place.get("length"):
                memory["length"] = f"{float(place['length']):.1f}"
        if player == video_probe.OWN:
            # The program window's browser cannot decode H.264, H.265, AAC (MP4, MOV, MKV, AVI — and a «.webm» with
            # them inside): the program plays the video and shows its frames right here, in the video's place
            # (webui/video_relay.py); ⛶ — its full-size player.
            viewer = _native_video_html(req, item, poster_url, other=other, memory=memory)
        else:
            picture = ' data-picture="1"' if int(item.get("width") or 0) > 0 else ""
            kept = "".join(f' data-{k}="{esc(v)}"' for k, v in memory.items() if k != "loop")
            viewer = (f'<video class="post-media" src="{media_url}" controls{"" if long_video else " loop"} '
                      f'preload="metadata" poster="{poster_url}" data-id="{image_id}"{kept}'
                      + (f' data-other="{esc(other)}"' if other else "") + f'{picture}></video>'
                      f'<div class="video-fallback" hidden><p>Это видео встроенный просмотр не показывает '
                      f'(формат не поддерживается).</p><button type="button" data-action="open-external" '
                      f'data-id="{image_id}">Открыть в видеоплеере</button></div>')
    else:
        viewer = _picture_html(item, path, media_url)
    if not path.is_file():
        viewer = '<div class="empty"><p>Файла этой карточки нет на диске.</p></div>'

    nav = _post_nav(req, ctx, index, _list_per_page(req, ctx), image_id)
    note = ""
    if viewer.startswith('<div class="media-wrap'):
        # shown by booru.js when the picture is drawn smaller than it is (the looks that have such a box: «r34»)
        note = ('<div class="resize-note" hidden role="note">'
                '<span class="note-scaled">Картинка уменьшена под окно. '
                '<button type="button" class="link" data-action="toggle-fit">Показать в полный размер</button>. '
                '<button type="button" class="link" data-action="fit-always-original">Всегда показывать в полный размер</button>. '
                '<button type="button" class="link" data-action="fit-note-off">Больше не показывать это сообщение</button>.'
                '</span><span class="note-full">Картинка показана в полный размер. '
                '<button type="button" class="link" data-action="toggle-fit">Вписать в окно</button>. '
                '<button type="button" class="link" data-action="fit-always-fit">Всегда вписывать в окно</button>.'
                '</span></div>')
    body = f'<div class="post-view">{_sets_box(req, image_id, ctx, index)}{note}{nav}<div class="post-stage">{viewer}</div></div>'
    title = " ".join(sorted({t for v in item["tag_groups"].values() for t in v})[:8]) or f"Карточка {image_id}"
    from webui import prefs
    stored = prefs.load()
    videos = ctx["section"] == data.VIDEO_SECTION
    look = _video_prefs() if videos else _prefs(req)
    return Response.html(page_frame(title, body, active="videos" if videos else "posts", search=ctx["tags"],
                                    section=ctx["section"], sidebar=sidebar, top_search=_top_search(ctx), kind="post",
                                    body_data={"hotkeys": json.dumps(_hotkeys(req)),
                                               "post-fit": stored.get("post_fit") or "fit",
                                               "fit-note": stored.get("fit_note") or "on"}, **look))


def _sets_box(req: Request, image_id: int, ctx: dict, index: int) -> str:
    """The card's сборка over its picture (``webui/pages/post_sets.py``); "" for a card of no сборка."""
    from webui.pages import post_sets
    return post_sets.box_html(req, image_id, ctx, index)


def window_player(req: Request, path: Path, *, prefer: str = "browser") -> str:
    """Which player of the program window plays this video (``core.video_probe.window_player``): the page's own
    choice (``player=`` — it went over to the other one), else what is inside the file, else by its name."""
    from core import video_probe
    asked = req.arg("player")
    if asked in (video_probe.BROWSER, video_probe.OWN):
        return asked
    chosen = video_probe.window_player(path, prefer=prefer, settings=req.settings)
    if chosen:
        return chosen
    return video_probe.BROWSER if path.suffix.lower() == ".webm" else video_probe.OWN


def _native_video_html(req: Request, item: dict, poster_url: str, *, other: str = "", memory: dict | None = None) -> str:
    """A video played by the program inside the page: its frames take the poster's place (booru.js
    ``setupNativeVideo``), the bar under it has the usual buttons; ``other`` — this page with the window's browser
    playing it, when the program's player cannot (AV1 without the graphics card's help); ``memory`` — (v5.44.121) a
    long video: once, from its place (data-loop, data-memory, data-from…)."""
    image_id = int(item["id"])
    width, height = int(item.get("width") or 0), int(item.get("height") or 0)
    attrs = {"id": image_id, **({"other": other} if other else {}), **(memory or {})}
    # (v5.44.116) the card page keeps F for «В избранное» (its keys, «Горячие клавиши»): ⛶ does not name it there
    f_free = not any(str(key).upper() == "F" for key in _hotkeys(req).values())
    return native_video_box(req, attrs, poster_url, width=width, height=height,
                            frame_class="post-media fit", full=f'data-id="{image_id}"',
                            external=f'<button type="button" data-action="open-external" data-id="{image_id}" '
                                     f'title="Открыть файл в программе, которой Windows открывает видео">'
                                     f'Плеер Windows</button>', full_key=f_free)


def native_video_box(req: Request, attrs: dict, poster_url: str, *, width: int = 0, height: int = 0,
                     frame_class: str = "", full: str = "", external: str = "", overlay: str = "", title: str = "",
                     bar: str = "", full_key: bool = True) -> str:
    """The box of a video the program plays (``attrs`` — its data-… : a card's ``id`` or a series' ``episode``,
    and how it starts), shared by the post page and the tab «Видео»; ``overlay`` — markup over its picture (the tab's
    slides of the episode), ``title`` — shown over the picture on the whole screen, ``bar`` — buttons of the bar
    before ⛶ (the next episode); ``full_key`` — F puts it on the whole screen (not on the card page: F is «В
    избранное» there).  ⛶ puts this same player over the whole screen (booru.js ``setFull``)."""
    try:
        volume = max(0, min(100, int(req.settings.get("media_volume", 50) or 0)))
    except (TypeError, ValueError):
        volume = 50
    muted = bool(req.settings.get("media_muted", False))
    dims = f' width="{width}" height="{height}"' if width > 0 and height > 0 else ""
    data_attrs = "".join(f' data-{esc(k)}="{esc(v)}"' for k, v in attrs.items())
    return (f'<div class="native-video"{data_attrs} data-volume="{volume}" data-muted="{1 if muted else 0}" '
            f'data-resume-gap="{video_library.RESUME_GAP:g}" data-resume-end="{video_library.RESUME_END:g}">'
            f'<div class="nv-screen"><img class="{esc(frame_class)} nv-frame" src="{poster_url}" alt=""{dims} '
            f'data-action="nv-toggle" title="Пауза / пуск (пробел)">{overlay}'
            f'<button type="button" class="nv-big" data-action="nv-toggle" aria-label="Смотреть">▶</button>'
            # before the play of an episode watched before: from its beginning instead (booru.js ``showSaved``)
            f'<button type="button" class="nv-restart" data-action="nv-restart" hidden>↺ Сначала</button>'
            # the furthest place reached, when the play is well before it (booru.js)
            f'<button type="button" class="nv-resume" data-action="nv-resume" hidden>▶ Продолжить просмотр '
            f'<span class="nv-resume-at"></span></button>'
            + (f'<div class="nv-title" aria-hidden="true">{esc(title)}</div>' if title else "") + '</div>'
            f'<div class="nv-bar" role="group" aria-label="Видео">'
            # (v5.44.26) line icons of one set (webui/icons.py), not emoji: the button holds both states, CSS shows one
            f'<button type="button" class="nv-play" data-action="nv-toggle" title="Пауза / пуск (пробел)" '
            f'aria-label="Пуск">{icons.PLAY}{icons.PAUSE}</button>'
            f'<span class="nv-time">0:00 / 0:00</span>'
            f'<input type="range" class="nv-seek" min="0" max="1000" step="1" value="0" aria-label="Место в видео">'
            f'<button type="button" class="nv-mute" data-action="nv-mute" data-muted="{1 if muted else 0}" '
            f'title="Звук (E)" aria-label="{"Включить звук" if muted else "Выключить звук"}">'
            f'{icons.SOUND}{icons.MUTED}</button>'
            f'<input type="range" class="nv-volume" min="0" max="100" step="1" value="{volume}" aria-label="Громкость">'
            f'{bar}<button type="button" class="nv-full" data-action="nv-full" {full} '
            f'title="Во весь экран ({"F, " if full_key else ""}двойной щелчок; Esc — выйти)" aria-label="Во весь экран">'
            f'{icons.FULL}{icons.UNFULL}</button></div>'
            # the voice-overs and subtitles of the file (and subtitle files next to it): shown when there is a choice
            f'<div class="nv-tracks" hidden><label class="nv-track" hidden>Озвучка <select class="nv-audio">'
            f'</select></label><label class="nv-track" hidden>Субтитры <select class="nv-subs"></select></label>'
            f'<span class="nv-track-note" role="status"></span></div>'
            # (v5.44.116) the two players side by side, named by whose they are: the program's own window player and
            # the one Windows opens the file in (the review: «Открыть в проигрывателе окна» / «Открыть в видеоплеере»)
            f'<div class="nv-problem" hidden role="alert"><p></p>'
            f'<button type="button" data-action="nv-player" {full} title="Открыть в плеере самой программы — он '
            f'займёт окно вместо страницы (Esc — назад)">Плеер программы</button> {external}'
            f'</div></div>')


def _picture_html(item: dict, path: Path, media_url: str) -> str:
    """The picture of the post page.  It is shown only when it has come whole and is decoded (booru.js
    ``setupPicture``): a big picture drawn while it arrives goes in stripes, and on some graphics drivers such a
    half-drawn frame — even with a strip of the previous page — stayed on the screen.  Meanwhile its thumbnail
    stands in, stretched over the place the picture will take (the card's width and height give the place)."""
    image_id = int(item["id"])
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    width, height = int(item.get("width") or 0), int(item.get("height") or 0)
    dims = f' width="{width}" height="{height}"' if width > 0 and height > 0 else ""
    return (f'<div class="media-wrap loading" data-id="{image_id}">'
            f'<img class="post-ph" src="{esc(data.media_url("thumb", item))}" alt="" aria-hidden="true">'
            f'<img class="post-media fit" src="{media_url}" alt=""{dims} data-bytes="{size}" data-action="toggle-fit" '
            f'title="Щёлкните по нужному месту — покажу его в полный размер">{_notes_html(item)}</div>'
            f'<div class="picture-problem" hidden role="alert"><p></p>'
            f'<button type="button" data-action="picture-retry">Загрузить заново</button> '
            f'<button type="button" data-action="open-external" data-id="{image_id}">Открыть в программе Windows</button></div>')


_HOTKEY_DEFAULTS = {"previous": "A", "next": "D", "favorite": "F", "fit": "W", "volume": "E", "back": "Q",
                    "fullscreen": "F11", "zoom_in": "+", "zoom_out": "-", "zoom_reset": "0", "notes": "N"}


def _hotkeys(req: Request) -> dict:
    """The viewer keys of the old post page («Горячие клавиши» in the settings)."""
    keys = dict(_HOTKEY_DEFAULTS)
    custom = req.settings.get("hotkeys") or {}
    if isinstance(custom, dict):
        keys.update({k: str(v) for k, v in custom.items() if k in keys and str(v).strip()})
    return keys


def _note_text(body: str) -> str:
    """Note text without the site's markup (<br>, <b>, <tn> …), which is never put into the page as HTML."""
    text = re.sub(r"(?i)<br\s*/?>", "\n", str(body or ""))
    text = re.sub(r"<[^>]+>", "", text)
    return html_lib.unescape(text).strip()


def _notes_html(item: dict) -> str:
    notes = item.get("notes") or []
    if not notes:
        return ""
    boxes = []
    for note in notes:
        try:
            x, y = float(note["x_normalized"]) * 100, float(note["y_normalized"]) * 100
            w, h = float(note["width_normalized"]) * 100, float(note["height_normalized"]) * 100
        except (KeyError, TypeError, ValueError):
            continue
        cls = "note local" if note.get("local_detector") else "note"
        text = esc(_note_text(note.get("body") or ""))
        boxes.append(f'<div class="{cls}" style="left:{x:.3f}%;top:{y:.3f}%;width:{w:.3f}%;height:{h:.3f}%">'
                     f'<div class="note-body">{text}</div></div>')
    return f'<div class="notes" hidden>{"".join(boxes)}</div>' if boxes else ""


def _set_members(req: Request, ctx: dict, image_id: int) -> list:
    """The cards of a card's сборка in its order, when the gallery shows the сборка as one tile ([] — none)."""
    from core import card_sets
    if not data.collapses(req.settings, ctx["section"]):
        return []
    try:
        found = card_sets.set_of(req.settings, image_id)
    except sqlite3.Error as exc:                    # an old base: the card on its own
        _LOG.debug("set of %s: %s", image_id, exc)
        return []
    return found["members"] if found and found["index"] >= 0 else []


def tile_card(req: Request, ctx: dict, at: int, last: bool) -> dict | None:
    """The card of the search's tile at place ``at``: a сборка's last card (``last``) or first one, else the tile's."""
    item = data.item_at(req.settings, ctx["tags"], section=ctx["section"], source=ctx["source"], order=ctx["order"],
                        index=at)
    if item is None:
        return None
    cards = _set_members(req, ctx, int(item["id"]))
    return (cards[-1] if last else cards[0]) if cards else item


def neighbours(req: Request, ctx: dict, index: int, image_id: int = 0) -> dict:
    """The cards before and after this one in its search: {"prev", "prev_at", "next", "next_at" — a card (None at
    the ends) and its tile's place; "members", "place" — the card's сборка and its place in it}.  The cards of a
    сборка (one tile of the gallery) come one after another — after its last card the next tile (its first card),
    before its first one the previous tile (its last card).  {} — not opened from a search.  (v5.44.65: the post
    page and the phone's viewer.)"""
    if index < 0:
        return {}
    members = _set_members(req, ctx, image_id) if image_id else []
    place = next((n for n, m in enumerate(members) if int(m["id"]) == int(image_id)), -1)
    prev_item, prev_at = ((members[place - 1], index) if place > 0
                          else (tile_card(req, ctx, index - 1, True), index - 1))
    next_item, next_at = ((members[place + 1], index) if 0 <= place < len(members) - 1
                          else (tile_card(req, ctx, index + 1, False), index + 1))
    return {"prev": prev_item, "prev_at": prev_at, "next": next_item, "next_at": next_at, "members": members,
            "place": place}


def _post_nav(req: Request, ctx: dict, index: int, per_page: int, image_id: int = 0) -> str:
    """«Предыдущая / Следующая» of a search (:func:`neighbours`)."""
    if index < 0:
        return ""
    parts = []
    near = neighbours(req, ctx, index, image_id)
    members = near["members"]
    prev_item, prev_at, next_item, next_at = near["prev"], near["prev_at"], near["next"], near["next_at"]
    if prev_item:
        href = url(f"/post/{prev_item['id']}", i=prev_at, **ctx)
        parts.append(f'<a class="nav-prev" rel="prev" data-key="ArrowLeft" href="{esc(href)}">'
                     f'<span class="arrow" aria-hidden="true">‹</span><span class="nav-text">Предыдущая</span></a>')
    back_page = index // per_page + 1
    parts.append(f'<a class="nav-back" href="{esc(url("/posts", page=back_page, **ctx))}">К результатам</a>')
    prefetch = ""
    if next_item:
        href = url(f"/post/{next_item['id']}", i=next_at, **ctx)
        parts.append(f'<a class="nav-next" rel="next" data-key="ArrowRight" href="{esc(href)}">'
                     f'<span class="nav-text">Следующая</span><span class="arrow" aria-hidden="true">›</span></a>')
        if Path(str(next_item.get("path") or "")).suffix.lower() not in VIDEO_EXTS:
            # the same address as the next page's picture (with its version), or the browser fetches it twice
            prefetch = f'<link rel="prefetch" href="{esc(data.media_url("media", next_item))}">'
        if next_at == index and members:            # inside a сборка: past its other cards at once
            after = tile_card(req, ctx, index + 1, False)
            if after:
                href = url(f"/post/{after['id']}", i=index + 1, **ctx)
                parts.append(f'<a class="nav-skip" href="{esc(href)}" title="Следующая карточка после этой сборки">'
                             f'после сборки ⇥</a>')
    return f'<nav class="post-nav" aria-label="Соседние карточки">{"".join(parts)}</nav>{prefetch}'


def _info_box(item: dict) -> str:
    rows = [("Номер", str(item["id"]))]
    if item.get("width") and item.get("height"):
        rows.append(("Размер", f'{int(item["width"])}×{int(item["height"])}'))
    rows.append(("Файл", f'{human_size(item.get("size_bytes") or 0)}, {Path(str(item.get("path") or "")).suffix.lstrip(".") or "?"}'))
    if item.get("is_video") or Path(str(item.get("path") or "")).suffix.lower() in VIDEO_EXTS:
        from core.video_probe import codecs_label
        inside = codecs_label(str(item.get("path") or ""))
        if inside:
            rows.append(("Внутри", inside))
    if item.get("indexed_at"):
        rows.append(("Добавлена", time.strftime("%d.%m.%Y %H:%M", time.localtime(int(item["indexed_at"])))))
    section = {"inbox": "Новые", "archive": "Архив", "trash": "Корзина", "missing": "Корзина (файл пропал)"}.get(
        str(item.get("lifecycle") or ""), str(item.get("lifecycle") or ""))
    rows.append(("Раздел", section))
    if int(item.get("deleted") or 0):              # why it is in the trash, since when, where it was
        from core import trash_tools
        target = str(item.get("trash_target") or "")
        rows.append(("Почему", trash_tools.label(item.get("trash_reason")) + (f" ({target})" if target else "")))
        if item.get("trashed_at"):
            rows.append(("В корзине с", time.strftime("%d.%m.%Y %H:%M", time.localtime(int(item["trashed_at"])))))
        if item.get("original_media_path"):
            rows.append(("Где была", f'<span class="path">{esc(item["original_media_path"])}</span>'))
    if item.get("hash_md5"):
        rows.append(("MD5", f'<code class="md5">{esc(item["hash_md5"])}</code>'))
    rating = int(item.get("rating") or 0)
    stars = "".join(
        f'<button type="button" class="star{" on" if n <= rating else ""}" data-action="rate" data-id="{item["id"]}" '
        f'data-value="{0 if n == rating else n}" aria-label="Оценка {n}">★</button>' for n in range(1, 6))
    rows.append(("Оценка", f'<span class="stars">{stars}</span>'))
    table = "".join(f"<tr><th>{esc(k)}</th><td>{v if k in {'MD5', 'Оценка', 'Где была'} else esc(v)}</td></tr>"
                    for k, v in rows)
    links = []
    seen = set()
    for src in item.get("sources") or []:
        u = str(src.get("url") or "")
        if u and u not in seen:
            seen.add(u)
            if is_web_url(u):
                links.append(f'<li><a href="{esc(u)}" target="_blank" rel="noopener noreferrer">{esc(src.get("host") or u)}</a></li>')
            else:                           # not a web address (an old import stored anything): text only
                links.append(f'<li class="muted">{esc(u)}</li>')
    sources = f'<h4>Источники</h4><ul class="plain-list sources">{"".join(links)}</ul>' if links else ""
    return f'<section class="box"><h3>Информация</h3><table class="info">{table}</table>{sources}</section>'


def _actions_box(item: dict) -> str:
    image_id = item["id"]
    fav = bool(item.get("favorite"))
    has_file = Path(str(item.get("path") or "")).is_file()
    buttons = [
        f'<button type="button" data-action="favorite" data-id="{image_id}" data-value="{0 if fav else 1}">'
        f'{"Убрать из избранного" if fav else "В избранное"}</button>',
    ]
    if item.get("notes"):
        buttons.append(f'<button type="button" data-action="notes" aria-pressed="false">Заметки ({len(item["notes"])})</button>')
    if has_file:
        buttons += [
            f'<a class="button" href="/media/{image_id}?download=1">Скачать оригинал</a>',
            f'<button type="button" data-action="open-external" data-id="{image_id}">Открыть в программе Windows</button>',
            f'<button type="button" data-action="reveal" data-id="{image_id}">Показать в папке</button>',
            f'<button type="button" data-action="copy-file" data-id="{image_id}" '
            f'title="Сам файл в буфер обмена: вставляется в папку, Telegram и т. п.">Копировать файл</button>',
        ]
    if int(item.get("deleted") or 0):
        buttons.append(f'<button type="button" data-action="restore" data-id="{image_id}">Вернуть из корзины</button>')
    else:
        buttons.append(f'<button type="button" class="danger" data-action="trash" data-id="{image_id}">Удалить в корзину</button>')
    tags = " ".join(sorted({t for v in (item.get("tag_groups") or {}).values() for t in v}))
    links = "\n".join(dict.fromkeys(str(s.get("url") or "") for s in item.get("sources") or [] if s.get("url")))
    copies = [("путь", str(item.get("path") or "")), ("MD5", str(item.get("hash_md5") or "")),
              ("теги", tags), ("ссылки", links)]
    copy_links = " ".join(
        f'<button type="button" class="link" data-action="copy" data-copy="{esc(value)}" data-what="{esc(label)}">{esc(label)}</button>'
        for label, value in copies if value)
    copy_line = f'<p class="copy-line">Скопировать: {copy_links}</p>' if copy_links else ""
    return (f'<section class="box actions"><h3>Действия</h3>{"".join(buttons)}{copy_line}'
            f'<p class="action-status" role="status"></p></section>')


# ── /tags ────────────────────────────────────────────────────────────────────
TAGS_PER_PAGE = 100


def tags(req: Request) -> Response:
    """«Теги»: the library's tags with how many cards have each — as rule34.xxx's list (a search form, a table) and as
    nhentai's (chips by letter); the look shows its own (``webui/tag_lists.py``)."""
    from webui import tag_lists as TL
    name = req.arg("name").strip()[:200]
    category = req.arg("category").strip().lower()
    category = category if category in CATEGORY_TITLES else ""
    order = TL.order_of(req.arg("order"))
    raw_dir = req.arg("dir") if req.arg("dir") in ("asc", "desc") else ""
    desc = TL.descending(order, raw_dir)
    page = req.int_arg("page", 1, lo=1)
    found = [TL.Tag(name=display, category=cat, count=count, key=norm)
             for norm, display, cat, count in data.tag_rows(req.settings, name_filter=name, category=category)]
    found = TL.ordered(found, order, desc, CATEGORY_ORDER)
    total = len(found)
    pages = max(1, (total + TAGS_PER_PAGE - 1) // TAGS_PER_PAGE)
    page = min(page, pages)
    part = [replace(t, href=url("/posts", tags=t.key)) for t in found[(page - 1) * TAGS_PER_PAGE:page * TAGS_PER_PAGE]]

    def at(n: int = 1, **over) -> str:
        params = {"name": name, "category": category, "order": order, "dir": raw_dir, "page": n}
        params.update(over)
        params["order"] = "" if params["order"] == "count" else params["order"]
        return url("/tags", **params)
    pager = paginator(page, pages, at)
    kinds = data.tag_categories(req.settings)
    options = ['<option value="">все типы</option>'] + [
        f'<option value="{c}"{" selected" if c == category else ""}>{esc(CATEGORY_TITLES[c])}</option>'
        for c in CATEGORY_ORDER if kinds.get(c) or c == category]
    type_row = (f'<tr><th><label for="tag-type">Тип</label></th><td><select id="tag-type" name="category">'
                f'{"".join(options)}</select></td></tr>')
    r34 = TL.r34_list(action="/tags", tags=part, name=name, order=order, desc=desc, count_title="Картинок",
                      paginator=pager, extra_rows=type_row,
                      head=f'<h2 class="page-title">Теги <span class="muted">{total}</span></h2>')
    tabs = []
    for c, label in [("", "Все")] + [(c, CATEGORY_TITLES[c]) for c in CATEGORY_ORDER if kinds.get(c)]:
        current = ' class="current" aria-current="page"' if c == category else ""
        tabs.append(f'<a href="{esc(at(category=c, dir="", page=1))}"{current}>{esc(label)}</a>')
    find = (f'<form class="nh-find" action="/tags" method="get">'
            + "".join(f'<input type="hidden" name="{k}" value="{esc(v)}">'
                      for k, v in (("category", category), ("order", "" if order == "count" else order)) if v)
            + f'<input type="search" name="name" value="{esc(name)}" placeholder="Найти тег (* — любой текст)" '
              f'aria-label="Найти тег" autocomplete="off" spellcheck="false"><button type="submit">Найти</button></form>')
    letters = TL.letter_pages(found, TAGS_PER_PAGE, lambda n: at(n)) if order == "name" else None
    nh = TL.nhentai_list(title="Теги", total=total, tags=part, order=order, letters=letters, paginator=pager,
                         order_url=lambda key: at(order=key, dir="", page=1), find=find,
                         tabs=f'<nav class="nh-tabs" aria-label="Тип тегов">{"".join(tabs)}</nav>')
    body = r34 + nh
    return Response.html(page_frame("Теги", body, active="tags", subnav=True, section="",
                                    top_search=_top_search({"tags": "", "section": "all", "source": "all", "order": "newest"}),
                                    **_prefs(req)))


def random_post(req: Request) -> Response:
    """«Случайная»: a random card of the search the link was on (``/random`` alone — of the whole library), opened
    as a card of that search: «Предыдущая / Следующая» and «К результатам» go on from it."""
    ctx = _ctx(req)
    found = data.random_in_search(req.settings, ctx["tags"], section=ctx["section"], source=ctx["source"],
                                  order=ctx["order"])
    if found is None:
        return Response.redirect(url("/posts", **ctx))
    image_id, index = found
    return Response.redirect(url(f"/post/{image_id}", i=index, **ctx))


def _list_per_page(req: Request, ctx: dict) -> int:
    """Cards on a page of the list a card was opened from (for «К результатам»)."""
    if ctx["section"] == data.VIDEO_SECTION:
        from webui.pages.videos import long_per_page
        return long_per_page(req)
    return _per_page(req)


def _video_prefs() -> dict:
    """The tab «Видео»: its look is its own («Хаб» unless chosen otherwise — ``page_frame`` with active="videos")."""
    return _prefs(None)


def _prefs(req: Request | None) -> dict:
    """The viewer's choices: the thumbnail size (``webui/prefs.py``); the look is the page's own (``page_frame``)."""
    from webui import prefs
    thumbs = prefs.load().get("thumbs")
    return {"thumbs": thumbs if thumbs in {code for code, _label in THUMB_SIZES} else "m"}
