"""Small HTML helpers: escaping, links that keep the current search, the page frame."""
from __future__ import annotations

from html import escape
from urllib.parse import urlencode

# Tag categories in the order a booru sidebar shows them, with Russian headings.
CATEGORY_ORDER = ("copyright", "character", "artist", "contributor", "species", "general", "meta", "lore", "invalid")
CATEGORY_TITLES = {
    "copyright": "Копирайт", "character": "Персонажи", "artist": "Художники", "contributor": "Участники",
    "species": "Виды", "general": "Общие", "meta": "Мета", "lore": "Лор", "invalid": "Прочее",
}

# The pages of the main menu: (the page — ``page_frame``'s ``active`` —, its name, its address).  (v5.44.75) «Скачать»
# — the grabber renamed; (v5.44.87) its parts: «По ссылке» (every kind by one field) and «Галерея (booru)» —
# ``webui/pages/get_hub.py``.
MENU = (("posts", "Галерея", "/posts"), ("manga", "Манга", "/manga"), ("manhwa", "Манхва", "/manhwa"),
        ("videos", "Видео", "/videos"), ("get", "Скачать", "/get"), ("parser", "Парсер", "/parser"),
        ("subs", "Подписки", "/subs"), ("sites", "Сайты", "/sites"), ("dupes", "Дубли", "/dupes"),
        ("diag", "Диагностика", "/diag"))
# Pages that live inside a menu item (v5.39: «Теги» are the gallery's tags — a tab of «Галерея» after its sections,
# not a menu item of their own).  The page keeps its own look (``prefs.look("tags")``).
MENU_OF = {"tags": "posts"}
PAGE_NAMES = {**{key: name for key, name, _href in MENU}, "tags": "Теги", "settings": "Настройки"}

# Sections of the gallery (the bucket filter of the old gallery), in menu order.
SECTIONS = (
    ("all", "Все"), ("inbox", "Новые"), ("found", "Найденные"), ("downloaded", "Скачанные"),
    ("no_match", "Брак"), ("favorites", "Избранное"), ("trash", "Корзина"),
)


def esc(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


# Values every page assumes when a parameter is absent: links leave them out to stay short.
DEFAULTS = {"page": "1", "order": "newest", "section": "all", "source": "all", "tag_source": "all"}


def url(path: str, **params) -> str:
    clean = {}
    for key, value in params.items():
        if value is None or str(value) in {"", "all"} or str(value) == DEFAULTS.get(key):
            continue
        if key == "page" and str(value) == "0":
            continue
        clean[key] = value
    return path + ("?" + urlencode(clean) if clean else "")


def category_class(category: str) -> str:
    cat = str(category or "general").lower()
    return "tag-" + (cat if cat in CATEGORY_TITLES else "general")


def human_size(num: int) -> str:
    size = float(num or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def human_count(num: int) -> str:
    n = int(num or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 10_000:
        return f"{n // 1000}k"
    return str(n)


THUMB_SIZES = (("s", "мелкие"), ("m", "средние"), ("l", "крупные"))
SEARCH_TIP = ("Пробел — все теги сразу, -тег — без него, a/b — любой из двух, * — любой текст; "
              "type:video, size:+5mb, rating:4, tags:0.")


def _skin_item(choice: str, name: str, description: str, swatches, on: bool, extra: str = "",
               natural: str = "light", toggles: bool = True) -> str:
    dots = "".join(f'<i style="background:{esc(color)}"></i>' for color in swatches)
    # (v5.44.45) data-variant/data-toggle: picking this look resets the page to its own side (booru.js resetVariant)
    return (f'<button type="button" role="menuitemradio" aria-checked="{"true" if on else "false"}" '
            f'class="skin-item{extra}{" active" if on else ""}" data-action="skin" data-skin="{esc(choice)}" '
            f'data-variant="{esc(natural)}" data-toggle="{"1" if toggles else "0"}">'
            f'<span class="swatches" aria-hidden="true">{dots}</span>'
            f'<span class="skin-text"><b>{esc(name)}</b><small>{esc(description)}</small></span></button>')


def _motion_item(motion: str) -> str:
    """(v5.44.55) «Плавные переходы» at the foot of the menu «Оформление»: on every page at once (``prefs.motion``)."""
    on = motion != "off"
    said = ("включены — страницы и картинки появляются мягко" if on else "выключены — всё сменяется сразу")
    return (f'<button type="button" role="menuitemcheckbox" aria-checked="{"true" if on else "false"}" '
            f'class="skin-item skin-motion" data-action="motion"><span class="skin-text"><b>Плавные переходы</b>'
            f'<small>{esc(said)}</small></span></button>')


def _skin_menu(look: dict, page: str, motion: str = "on") -> str:
    """The menu «Оформление»: the look of this page only (``prefs.look``); «как у галереи» on the other pages; one
    look for all pages; (v5.44.55) «Плавные переходы» for all pages."""
    from webui.skins import BY_ID, SKINS
    gallery_page = look["key"] == "skin"
    items = []
    if not gallery_page:
        g = BY_ID[look["gallery"]]
        items.append(_skin_item("gallery", "Как у галереи", f"Сейчас там «{g.name}»", g.swatches, look["follows"],
                                " skin-follow", natural="dark" if g.dark else "light", toggles=g.toggle))
    items += [_skin_item(s.id, s.name, s.description, s.swatches, not look["follows"] and s.id == look["skin"],
                         natural="dark" if s.dark else "light", toggles=s.toggle)
              for s in SKINS]
    name = PAGE_NAMES.get(page, PAGE_NAMES["posts"])
    scope = (f'<p class="skin-scope" role="presentation">Оформление страницы «{esc(name)}»'
             + ('<small>его же берут страницы, где выбрано «как у галереи»</small>' if gallery_page else "") + "</p>")
    # (v5.44.45) light / dark switch for this page only; always in the menu so picking another look can reveal it,
    # hidden for a one-sided look (the r34 trio).  Each page keeps its own side (prefs: <key>_variant).
    cur = BY_ID.get(look["skin"])
    now = look.get("variant", "light")
    vhidden = "" if (cur and cur.toggle) else " hidden"
    variant = (f'<div class="skin-variant" role="group" aria-label="Светлая или тёмная"{vhidden}>' + "".join(
        f'<button type="button" class="skin-vbtn{" active" if now == code else ""}" data-action="variant" '
        f'data-variant="{code}" aria-pressed="{"true" if now == code else "false"}">{label}</button>'
        for code, label in (("light", "Светлая"), ("dark", "Тёмная"))) + "</div>")
    every = ('<button type="button" role="menuitem" class="skin-item skin-all" data-action="skin-all">'
             '<span class="skin-text"><b>Это оформление — на всех страницах</b>'
             '<small>у остальных станет «как у галереи»</small></span></button>')
    return (f'<div class="skin-menu"><button type="button" class="skin-button" data-action="skin-menu" '
            f'aria-haspopup="menu" aria-expanded="false" title="Оформление страницы «{esc(name)}»">'
            f'<span class="skin-icon" aria-hidden="true"></span><span class="skin-label">Оформление</span></button>'
            f'<div class="skin-list" role="menu" aria-label="Оформление страницы «{esc(name)}»" hidden>'
            f'{scope}{variant}{"".join(items)}{every}{_motion_item(motion)}</div></div>')


# A gear drawn here (eight teeth and a ring): no icon font, no picture file.
_GEAR = ('<svg class="gear-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><g fill="currentColor">'
         + "".join(f'<rect x="6.75" y="0.5" width="2.5" height="3.4" rx="0.6" transform="rotate({a} 8 8)"/>'
                   for a in range(0, 360, 45))
         + '</g><circle cx="8" cy="8" r="3.9" fill="none" stroke="currentColor" stroke-width="2.6"/></svg>')


def _settings_link(active: str) -> str:
    on = active == "settings"
    current = ' aria-current="page"' if on else ""
    return (f'<a class="skin-button settings-link{" active" if on else ""}" href="/settings" title="Настройки"'
            f'{current}>{_GEAR}<span class="skin-label">Настройки</span></a>')


# The stylesheet and the script of a page of the menu (``page_frame``'s ``assets``, else its ``active``)
PAGE_CSS = {"manga": '\n<link rel="stylesheet" href="/static/manga.css">',
            "manhwa": '\n<link rel="stylesheet" href="/static/manhwa.css">',
            "videos": '\n<link rel="stylesheet" href="/static/videos.css">',
            "grab": '\n<link rel="stylesheet" href="/static/grab.css">',
            "archive": '\n<link rel="stylesheet" href="/static/archive.css">',
            "online": '\n<link rel="stylesheet" href="/static/online.css">',       # (v5.44.82) read from a site
            "getlink": '\n<link rel="stylesheet" href="/static/get-link.css">',    # (v5.44.87) «Скачать → По ссылке»
            "catalog": '\n<link rel="stylesheet" href="/static/catalog.css">',     # (v5.44.90) «Лента» of the tabs
            "title": '\n<link rel="stylesheet" href="/static/title.css">',        # (v5.44.102) a title of «Лента»
            "watch": '\n<link rel="stylesheet" href="/static/watch.css">'}        # (v5.44.106) «Смотреть» a series
PAGE_JS = {"subs": '\n<script src="/static/subs.js" defer></script>',
           "manga": '\n<script src="/static/manga.js" defer></script>',
           "manhwa": '\n<script src="/static/manhwa.js" defer></script>',
           "videos": '\n<script src="/static/videos.js" defer></script>',
           "grab": '\n<script src="/static/grab.js" defer></script>',
           "get": '\n<script src="/static/get-hub.js" defer></script>',
           "getlink": '\n<script src="/static/get-link.js" defer></script>',    # (v5.44.87) «Скачать → По ссылке»
           "catalog": '\n<script src="/static/catalog.js" defer></script>',     # (v5.44.90) «Лента» of the tabs
           "title": '\n<script src="/static/title.js" defer></script>',         # (v5.44.102) a title of «Лента»
           # (v5.44.106) «Смотреть»: hls.js for a browser that does not play HLS itself (the window: its own player)
           "hls": '\n<script src="/static/vendor/hls.light.min.js" defer></script>',
           "watch": '\n<script src="/static/watch.js" defer></script>',
           "archive": '\n<script src="/static/archive.js" defer></script>',
           "online": '\n<script src="/static/online.js" defer></script>',     # (v5.44.82) read from a site
           "dupes": '\n<script src="/static/dupes.js" defer></script>',
           "diag": '\n<script src="/static/diag.js" defer></script>',
           "settings": '\n<script src="/static/settings.js" defer></script>'
                       '\n<script src="/static/phone-relay.js" defer></script>'
                       '\n<script src="/static/manga-sites.js" defer></script>'}


def page_frame(title: str, body: str, *, active: str = "posts", search: str = "", section: str = "all",
               sidebar: str = "", subnav: bool = True, thumbs: str = "m", top_search: str = "", kind: str = "",
               body_data: dict | None = None, tabs: tuple = (), assets: tuple = ()) -> str:
    """The frame of every page: header (brand, menu, search, look), section bar, sidebar, content.

    Every look gets the same HTML: the header search and the sidebar search both exist, and the look's
    stylesheet shows the one it uses; the sidebar is a column in some looks and a drawer in others.  The look is the
    page's own (``active`` — the page of the menu: ``prefs.look``).
    ``tabs`` — ((label, href, current), …): the pages of one menu item, in the section bar.
    ``assets`` — (v5.44.75) the pages whose stylesheets and scripts this one takes (:data:`PAGE_CSS`,
    :data:`PAGE_JS`), else those of ``active``: a part of «Скачать» is the page of another tab under its frame.
    """
    from webui import content_mode, motion_script, prefs
    look = prefs.look(active)
    mode = content_mode.mode()                    # (v5.44.53) «SFW / Всё / NSFW»: the gallery's pages leave the menu
    motion = prefs.motion()                       # (v5.44.55) «Плавные переходы» (static/motion.js)
    nav = []
    item = MENU_OF.get(active, active)
    for key, label, href in MENU:
        if not content_mode.page_shown(key, mode):
            continue
        cls = ' class="active"' if key == item else ""
        nav.append(f'<a href="{href}"{cls}>{label}</a>')
    sections = ""
    if subnav and item == "posts":
        links = []
        for key, label in SECTIONS:
            on = active == "posts" and key == section
            cls = ' class="active" aria-current="page"' if on else ""
            links.append(f'<a href="{esc(url("/posts", tags=search, section=key))}"{cls}>{label}</a>')
        tags_on = ' class="sections-tags active" aria-current="page"' if active == "tags" else ' class="sections-tags"'
        links.append(f'<span class="sections-sep" aria-hidden="true"></span><a href="/tags"{tags_on}>Теги</a>')
        sections = f'<nav class="sections" aria-label="Разделы галереи">{"".join(links)}</nav>'
    elif tabs:
        links = [f'<a href="{esc(href)}" class="active" aria-current="page">{esc(label)}</a>' if current
                 else f'<a href="{esc(href)}">{esc(label)}</a>' for label, href, current in tabs]
        sections = f'<nav class="sections" aria-label="Разделы">{"".join(links)}</nav>'
    side = ""
    drawer_button = ""
    if sidebar:
        side = (f'<aside class="sidebar" id="sidebar" aria-label="Теги и фильтры" data-title="Теги и фильтры">'
                f'<button type="button" class="drawer-close" data-action="drawer" aria-label="Закрыть панель">×</button>'
                f'{sidebar}</aside><div class="drawer-backdrop" data-action="drawer" hidden></div>')
        drawer_button = ('<button type="button" class="drawer-toggle" data-action="drawer" aria-controls="sidebar" '
                         'aria-expanded="false">Теги и фильтры</button>')
    layout_cls = "layout with-sidebar" if sidebar else "layout"
    skin = look["skin"]
    taken = tuple(assets) if assets else (active,)
    manga_css = "".join(PAGE_CSS.get(key, "") for key in taken)
    page_js = "".join(PAGE_JS.get(key, "") for key in taken)
    data_attrs = "".join(f' data-{esc(k)}="{esc(v)}"' for k, v in (body_data or {}).items())
    sections += _reset_notice() + _archive_notice()
    return f"""<!doctype html>
<html lang="ru" data-skin="{esc(skin)}" data-thumb="{esc(thumbs)}" data-skin-key="{esc(look["key"])}" data-gallery-skin="{esc(look["gallery"])}" data-variant="{esc(look.get("variant", "light"))}" data-variant-key="{esc(look.get("variant_key", "skin_variant"))}" data-gallery-variant="{esc(look.get("gallery_variant", "light"))}" data-content-mode="{esc(mode)}" data-motion-pref="{esc(motion)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — Local Booru</title>
{motion_script.tag()}
<link rel="stylesheet" href="/static/booru.css">{manga_css}
<link rel="stylesheet" href="/static/tools.css">
<link rel="stylesheet" href="/static/skins/{esc(skin)}.css" id="skin-css">
<script src="/static/booru.js" defer></script>
<script src="/static/tools.js" defer></script>{page_js}
</head>
<body class="page-{esc(kind or active)}"{data_attrs}>
<header class="top">
  <a class="brand" href="{content_mode.home(mode)}"><span class="brand-mark" aria-hidden="true"></span><span class="brand-name"><span class="bn-1">Local</span> <span class="bn-2">Booru</span></span></a>
  <nav class="main-nav" aria-label="Основное меню">{"".join(nav)}</nav>
  {top_search}
  <div class="top-tools">{drawer_button}{content_mode.switch(mode)}{_settings_link(active)}{_skin_menu(look, active, motion)}<span class="tray-clock" aria-hidden="true"></span></div>
</header>
{sections}
<div class="{layout_cls}">
{side}
<main class="content" data-title="{esc(title)} — Local Booru">{body}</main>
</div>
</body>
</html>"""


def _reset_notice() -> str:
    """A full reset ordered on «Удаление и сброс» is shown on every page until it is done or taken back: the
    pictures added meanwhile go with it."""
    from core.library_reset import pending_reset, started
    request = pending_reset()
    if request is None:
        return ""
    if started(request):
        return ('<p class="reset-notice" role="alert"><b>Полный сброс библиотеки не доделан:</b> он доделается при '
                'следующем запуске программы. <a href="/diag/reset#reset-all">Подробнее</a></p>')
    return ('<p class="reset-notice" role="alert"><b>Заказан полный сброс библиотеки:</b> при следующем запуске '
            'программа спросит ещё раз и удалит её картинки, базу и кэш. <a href="/diag/reset#reset-all">Отменить</a></p>')


def _archive_notice() -> str:
    """Another archive was chosen on «Настройки → Папки»: every page says it opens at the next start."""
    from core.archive_switch import pending
    nxt = pending()                               # the choice of this run: no disk is looked at
    if nxt is None:
        return ""
    return (f'<p class="reset-notice" role="alert"><b>При следующем запуске откроется другой архив:</b> '
            f'{esc(nxt.parent)} — закройте программу и откройте снова; до этого всё сохраняется в открытый архив. '
            '<a href="/settings#set-folders">Подробнее</a></p>')


def search_box(query: str, *, section: str = "all", source: str = "all", order: str = "", variant: str = "side",
               keep: dict | None = None) -> str:
    """The tag search.  ``variant``: "side" — the sidebar box, "top" — the field in the header; ``keep`` — more
    values the search goes on with (the gallery choosing cards for a сборка: ``add_to``)."""
    hidden = ""
    for name, value in (("section", section), ("source", source), ("order", order), *(keep or {}).items()):
        if value and value != "all" and value != DEFAULTS.get(name):
            hidden += f'<input type="hidden" name="{name}" value="{esc(value)}">'
    field = (f'<input type="search" name="tags" value="{esc(query)}" placeholder="Теги через пробел" autocomplete="off" '
             f'spellcheck="false" data-autocomplete aria-label="Теги для поиска" title="{esc(SEARCH_TIP)}">')
    if variant == "top":
        return (f'<form class="top-search" action="/posts" method="get" role="search">'
                f'<div class="search-row">{field}<button type="submit" class="search-go" aria-label="Найти">Найти</button></div>'
                f'{hidden}</form>')
    return f"""<form class="search" action="/posts" method="get" role="search">
  <h3>Поиск</h3>
  <div class="search-row">{field}<button type="submit">Найти</button></div>
  {hidden}
  <p class="hint">Пробел — все теги сразу, <code>-тег</code> — без него, <code>a/b</code> — любой из двух,
  <code>*</code> — любой текст; <code>type:video</code>, <code>size:+5mb</code>, <code>rating:4</code>, <code>tags:0</code>.</p>
</form>"""


# ── lists of covers paged by whole rows (v5.44.1: manga, the series and long videos of «Видео») ──
ROWS_LIMITS = (4, 240)


def rows_per_page(req, cookie: str, usual: int) -> int:
    """Items on a page of a list of covers: whole rows of the columns the window has, as near ``usual`` as they come
    (booru.js ``fitRows`` counts them into the list's ``cookie``; 25 in six columns left one cover alone in its last
    row).  Before the window has counted — ``usual``."""
    from http.cookies import CookieError, SimpleCookie
    try:
        jar = SimpleCookie(req.headers.get("Cookie", ""))
        counted = int(jar[cookie].value) if cookie in jar else 0
    except (CookieError, ValueError, TypeError):
        counted = 0
    low, high = ROWS_LIMITS
    return max(low, min(high, counted)) if counted > 0 else usual


def rows_attrs(cookie: str, usual: int, per_page: int, total: int, page: int = 0) -> str:
    """The attributes of such a list's grid for booru.js ``fitRows`` (``page`` 0 — a list without pages: a shelf)."""
    paged = f' data-page="{int(page)}"' if page else ""
    return (f' data-rows="{int(usual)}" data-rows-cookie="{esc(cookie)}" data-per-page="{int(per_page)}"'
            f' data-total="{int(total)}"{paged}')


def paginator(page: int, pages: int, make_url) -> str:
    """Page numbers: «« ‹ 1 … 4 5 [6] 7 8 … 40 › »» and a field for a page number (the looks that show it)."""
    if pages <= 1:
        return ""
    parts = []

    def link(n: int, text: str, label: str = "", extra: str = "") -> str:
        aria = f' aria-label="{esc(label)}"' if label else ""
        return f'<a href="{esc(make_url(n))}"{aria}{extra}>{text}</a>'

    def arrow(n: int, glyph: str, kind: str, label: str, extra: str = "") -> str:
        return link(n, f'<span class="pg-glyph" aria-hidden="true">{glyph}</span>', label, f' class="pg-{kind}"{extra}')

    if page > 1:
        parts.append(arrow(1, "«", "first", "Первая страница"))
        parts.append(arrow(page - 1, "‹", "prev", "Предыдущая страница", ' rel="prev" data-key="ArrowLeft"'))
    window = sorted({1, pages, *range(max(1, page - 4), min(pages, page + 4) + 1)})
    prev = 0
    for n in window:
        if prev and n - prev > 1:
            parts.append('<span class="gap">…</span>')
        parts.append(f'<b aria-current="page">{n}</b>' if n == page else link(n, str(n)))
        prev = n
    if page < pages:
        parts.append(arrow(page + 1, "›", "next", "Следующая страница", ' rel="next" data-key="ArrowRight"'))
        parts.append(arrow(pages, "»", "last", "Последняя страница"))
    jump = (f'<span class="page-jump"><input type="number" min="1" max="{pages}" placeholder="стр." inputmode="numeric" '
            f'aria-label="Номер страницы (из {pages})" data-first="{esc(make_url(1))}" data-last="{esc(make_url(pages))}">'
            f'<button type="button" data-action="page-jump">Go</button></span>')
    return f'<nav class="paginator" aria-label="Страницы">{"".join(parts)}{jump}</nav>'
