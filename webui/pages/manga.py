"""Manga pages, laid out like nhentai: a wall of covers, a title page with its tags and page
thumbnails, and a reader that turns pages by click or arrow keys."""
from __future__ import annotations

import time
import zipfile
from pathlib import Path

from core import manga_library as M
from webui.html import esc, human_count, page_frame, paginator, url
from webui.server import Request, Response

PER_PAGE = 25
COVER_SIZE = 500          # px, long side of a cover (shown at ~250 px)
THUMB_SIZE = 320          # px, long side of a page thumbnail on the title page
SORTS = {"newest": "Сначала новые", "title": "По названию", "pages": "Больше страниц"}
STRIP_PLACES = {"bottom": "снизу", "side": "сбоку", "off": "скрыть"}   # keys: prefs.MANGA_STRIP_PLACES


def register(router) -> None:
    router.add("GET", r"/manga", index)
    router.add("GET", r"/manga/g/(?P<id>[0-9a-f]{12})", title_page)
    router.add("GET", r"/manga/r/(?P<id>[0-9a-f]{12})/(?P<n>\d+)", reader)
    router.add("GET", r"/manga/cover/(?P<id>[0-9a-f]{12})", cover)
    router.add("GET", r"/manga/thumb/(?P<id>[0-9a-f]{12})/(?P<n>\d+)", page_thumb)
    router.add("GET", r"/manga/page/(?P<id>[0-9a-f]{12})/(?P<n>\d+)", page_image)
    router.add("POST", r"/api/manga/(?P<id>[0-9a-f]{12})/reveal", reveal)
    router.add("GET", r"/manga/random", random_title)
    router.add("GET", r"/manga/(?P<kind>tags|artists|characters|parodies)", tag_index)


def _prefs(req: Request) -> dict:
    """Manga pages keep a look of their own (nhentai unless chosen otherwise — ``page_frame`` with active="manga")."""
    return {"thumbs": "m"}


TABS = (("all", "Вся манга", "/manga"), ("tags", "Теги", "/manga/tags"), ("artists", "Артисты", "/manga/artists"),
        ("characters", "Персонажи", "/manga/characters"), ("parodies", "Пародии", "/manga/parodies"),
        ("random", "Случайная", "/manga/random"), ("get", "Скачать с nhentai", "/manga/get"))
# The lists of the manga folder's tags (``tag_index``): (title, groups of core.manga_library, «нет …»)
INDEX_TITLES = {"tags": ("Теги", ("general",), "тегов"), "artists": ("Артисты", ("artist",), "артистов"),
                "characters": ("Персонажи", ("character",), "персонажей"),
                "parodies": ("Пародии", ("parody", "copyright"), "пародий")}


def _frame(req: Request, title: str, body: str, *, kind: str = "manga", show: str = "") -> Response:
    """A page of the tab; ``show`` — which of its pages the section bar marks ("" — no bar: a title, the reader)."""
    search = _search_form(req.arg("q"), req.arg("sort", "newest"))
    tabs = tuple((label, href, key == show) for key, label, href in TABS) if show else ()
    return Response.html(page_frame(title, body, active="manga", subnav=False, kind=kind, top_search=search, tabs=tabs,
                                    **_prefs(req)))


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


def _cover_card(manga: M.Manga) -> str:
    badges = []
    lang = _language(manga)
    if lang:
        badges.append(f'<span class="mbadge lang">{esc(lang)}</span>')
    if manga.is_series:
        badges.append(f'<span class="mbadge">{len(manga.chapters)} гл.</span>')
    badges.append(f'<span class="mbadge">{manga.page_count} стр.</span>')
    return (f'<a class="cover" href="/manga/g/{manga.id}" title="{esc(manga.title)}">'
            f'<img src="/manga/cover/{manga.id}" alt="" loading="lazy" decoding="async">'
            f'<span class="mbadges">{"".join(badges)}</span>'
            f'<span class="cover-caption">{esc(manga.title)}</span></a>')


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
    found = [m for m in items if _matches(m, query)] if query else list(items)
    found = _sorted(found, sort if sort in SORTS else "newest")
    pages = max(1, (len(found) + PER_PAGE - 1) // PER_PAGE)
    page = min(req.int_arg("page", 1, lo=1), pages)
    shown = found[(page - 1) * PER_PAGE: page * PER_PAGE]
    sorts = "".join(
        f'<a class="{"active" if key == sort else ""}" href="{esc(url("/manga", q=query, sort=key))}">{esc(label)}</a>'
        for key, label in SORTS.items())
    head = (f'<div class="manga-head"><h1>{"Поиск: " + esc(query) if query else "Вся манга"} '
            f'<span class="muted">{len(found)}</span></h1><nav class="manga-sorts">{sorts}'
            f'<a href="{esc(url("/manga", q=query, sort=sort, refresh=1))}" title="Перечитать папку манги">Обновить</a></nav></div>')
    if shown:
        grid = '<div class="covers">' + "".join(_cover_card(m) for m in shown) + "</div>"
    elif query:
        grid = f'<div class="empty"><p>По запросу «{esc(query)}» манги нет.</p></div>'
    else:
        grid = ('<div class="empty"><p>В папке манги пока ничего нет.</p><p><a class="mbutton primary" '
                'href="/manga/get">Скачать с nhentai</a></p></div>')
    body = (f'<div class="manga-index">{head}{grid}'
            + paginator(page, pages, lambda n: url("/manga", q=query, sort=sort, page=n)) + "</div>")
    return _frame(req, "Манга" if not query else query, body, show="all")


def random_title(req: Request) -> Response:
    import random
    _root, items = _library(req)
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
    entries = [(group, tag, n) for (group, tag), n in _counts(items).items() if group in groups and tag]
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
    _root, items = _library(req)
    read_target = manga.chapters[0] if manga.is_series else manga
    series = M.LIBRARY.get(M.manga_root(req.settings), manga.parent_id) if manga.parent_id else None
    buttons = [f'<a class="mbutton primary" href="/manga/r/{read_target.id}/1">Читать</a>']
    if manga.source_url:
        buttons.append(f'<a class="mbutton" href="{esc(manga.source_url)}" target="_blank" rel="noopener noreferrer">На сайте</a>')
    buttons.append(f'<button type="button" class="mbutton" data-action="reveal-manga" data-id="{manga.id}">Показать в папке</button>')
    back = (f'<p class="manga-back"><a href="/manga/g/{series.id}">← {esc(series.title)}</a></p>' if series else "")
    info = (f'<div class="manga-info"><a class="manga-cover" href="/manga/r/{read_target.id}/1">'
            f'<img src="/manga/cover/{manga.id}" alt=""></a><div class="manga-meta">{back}'
            f'<h1>{esc(manga.title)}</h1>'
            + (f'<h2>{esc(manga.title_japanese)}</h2>' if manga.title_japanese else "")
            + f'<div class="tag-rows">{_tag_rows(manga, _counts(items))}</div>'
            f'<div class="manga-buttons">{"".join(buttons)}<p class="action-status" role="status"></p></div></div></div>')
    parts = M.related(items, manga)
    related_html = ""
    if parts:
        cards = "".join(_cover_card(m) for m in parts)
        related_html = (f'<h3 class="manga-sub">Другие главы этого тайтла <span class="muted">{len(parts)}</span></h3>'
                        f'<div class="covers">{cards}</div>')
    if manga.is_series:
        cards = "".join(
            f'<a class="cover chapter" href="/manga/r/{c.id}/1" title="{esc(c.title)}">'
            f'<img src="/manga/cover/{c.id}" alt="" loading="lazy" decoding="async">'
            f'<span class="mbadges"><span class="mbadge">{len(c.pages)} стр.</span></span>'
            f'<span class="cover-caption">{esc(c.title)}</span></a>' for c in manga.chapters)
        extra = f'<h3 class="manga-sub">Главы <span class="muted">{len(manga.chapters)}</span></h3><div class="covers">{cards}</div>'
    else:
        thumbs = "".join(
            f'<a class="page-thumb" href="/manga/r/{manga.id}/{n}"><img src="/manga/thumb/{manga.id}/{n}" alt="Страница {n}" '
            f'loading="lazy" decoding="async"></a>' for n in range(1, len(manga.pages) + 1))
        extra = f'<div class="page-thumbs">{thumbs}</div>'
    return _frame(req, manga.title, f'<div class="manga-gallery">{info}{extra}{related_html}</div>', kind="manga-title")


# ── /manga/r/<id>/<n> ───────────────────────────────────────────────────────
def reader(req: Request) -> Response:
    manga = _get(req)
    if manga is None or manga.is_series:
        return Response.redirect(f"/manga/g/{req.params['id']}" if manga else "/manga")
    total = len(manga.pages)
    n = int(req.params["n"])
    if n < 1 or n > total:
        return Response.redirect(f"/manga/r/{manga.id}/{max(1, min(n, total))}")
    series = M.LIBRARY.get(M.manga_root(req.settings), manga.parent_id) if manga.parent_id else None
    home = f"/manga/g/{series.id if series else manga.id}"
    after = home
    if series:
        index_in_series = next((i for i, c in enumerate(series.chapters) if c.id == manga.id), -1)
        if 0 <= index_in_series < len(series.chapters) - 1:
            after = f"/manga/r/{series.chapters[index_in_series + 1].id}/1"
    prev_href = f"/manga/r/{manga.id}/{n - 1}" if n > 1 else home
    next_href = f"/manga/r/{manga.id}/{n + 1}" if n < total else after

    def nav(cls: str) -> str:
        options = "".join(f'<option value="{k}"{" selected" if k == n else ""}>{k}</option>' for k in range(1, total + 1))
        return (f'<nav class="reader-nav {cls}" aria-label="Страницы">'
                f'<a href="/manga/r/{manga.id}/1" title="Первая страница" aria-label="Первая страница">«</a>'
                f'<a href="{esc(prev_href)}" rel="prev" title="Предыдущая (←)" aria-label="Предыдущая страница">‹</a>'
                f'<label class="reader-count"><select data-action="goto-page" data-base="/manga/r/{manga.id}/" '
                f'aria-label="Страница">{options}</select> / {total}</label>'
                f'<a href="{esc(next_href)}" rel="next" title="Следующая (→)" aria-label="Следующая страница">›</a>'
                f'<a href="/manga/r/{manga.id}/{total}" title="Последняя страница" aria-label="Последняя страница">»</a>'
                f'<a class="reader-home" href="{esc(home)}">К манге</a></nav>')

    title = (series.title + " — " if series else "") + manga.title
    # shown when it has come whole and is decoded (booru.js ``setupPicture``), as the picture of the post page
    image = (f'<div class="reader-stage loading" data-prev="{esc(prev_href)}" data-next="{esc(next_href)}">'
             f'<img class="reader-image" src="/manga/page/{manga.id}/{n}" alt="Страница {n}"></div>'
             f'<div class="picture-problem" hidden role="alert"><p></p>'
             f'<button type="button" data-action="picture-retry">Загрузить заново</button></div>')
    keys = (f'<a hidden data-key="ArrowLeft" href="{esc(prev_href)}"></a>'
            f'<a hidden data-key="ArrowRight" href="{esc(next_href)}"></a>')
    prefetch = f'<link rel="prefetch" href="/manga/page/{manga.id}/{n + 1}">' if n < total else ""
    place, strip, strip_menu = _page_strip(manga, n)
    body = (f'<div class="reader strip-{place}" data-reader>{nav("at-top")}<h1 class="reader-title">{esc(title)}</h1>'
            f'<div class="reader-body">{image}{strip}</div>{nav("at-bottom")}{strip_menu}{keys}{prefetch}</div>')
    return _frame(req, f"{n}/{total} — {title}", body, kind="manga-reader")


def _page_strip(manga: M.Manga, n: int) -> tuple[str, str, str]:
    """The strip of page thumbnails under (or beside) the page, as in the old reader, and the switch
    of its place.  Returns (place, strip html, switch html)."""
    from webui import prefs
    place = str(prefs.load().get("manga_strip") or "bottom")
    place = place if place in STRIP_PLACES else "bottom"
    strip = ""
    if place != "off":
        thumbs = "".join(
            f'<a class="strip-page{" current" if k == n else ""}" href="/manga/r/{manga.id}/{k}"'
            f'{" aria-current=page" if k == n else ""}><img src="/manga/thumb/{manga.id}/{k}" alt="Страница {k}" '
            f'loading="lazy" decoding="async"><span>{k}</span></a>' for k in range(1, len(manga.pages) + 1))
        strip = f'<nav class="page-strip" aria-label="Страницы главы">{thumbs}</nav>'
    buttons = "".join(
        f'<button type="button" class="link{" active" if key == place else ""}" data-action="manga-strip" '
        f'data-place="{key}"{" aria-pressed=true" if key == place else ""}>{label}</button>'
        for key, label in STRIP_PLACES.items())
    switch = f'<div class="strip-places" role="group" aria-label="Лента страниц">Лента страниц: {buttons}</div>'
    return place, strip, switch


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
