"""The tab «Видео»: series from the video folder (``core/video_library.py``) and the long videos of the library (the
gallery leaves them out: ``data.section_filter``), in a look of its own («Хаб» unless chosen otherwise).

* ``/videos`` — the series as posters, then the long videos as the gallery's cards (a card opens the gallery's post
  page in the tab's look: ``/post/<id>?section=videos``);
* ``/videos/s/<id>`` — a series: its poster and description, the player and the episode buttons (sub-folders —
  tabs over them); ``?ep=<id>`` — that episode, ``&play=1`` — it starts at once.  In the program window the program
  plays it (``ui/video_inline.py`` through the bridge, as the post page's videos); in a browser — ``<video>``.
* «Описание с сайта» — ``core/video_meta_import.py`` in a page task (``/api/videos/<id>/meta`` by address,
  ``/api/videos/<id>/meta-file`` — a saved page sent as the body).
* What was watched — ``/api/videos/progress`` (``video_library.PROGRESS``).
* Voice-overs and subtitles (``core/video_tracks``): in the program window — its player's menus; in a browser — the
  episode's subtitle files as ``<track>`` (``/videos/subs/<id>?n=`` — WebVTT, by their place, never by a path), the
  one chosen is kept for the series (``/api/videos/tracks``).
"""
from __future__ import annotations

import logging
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import quote

from core import video_library as V
from core.video_probe import human_duration
from webui import data
from webui.html import esc, page_frame, paginator, rows_attrs, rows_per_page, url
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.webui")

KIND = "video_meta"                    # the page task of «Описание с сайта»
LONG_PER_PAGE = 40                     # long videos on a page of the tab
SHELF = 12                             # series shown above the long videos on «Всё»
SERIES_PER_PAGE = 36
SORTS = {"newest": "Сначала новые", "title": "По названию", "watched": "Недавно смотрел", "rating": "По оценке"}
SHOWS = (("all", "Всё"), ("series", "Сериалы"), ("long", "Длинные видео"))
COVER = {"s": (360, 540), "l": (720, 1080)}
FRAME = (1280, 720)
SEARCH_SITES = (("hentasis1.top", "https://hentasis1.top/index.php?do=search&subaction=search&story={q}"),
                ("YummyAnime", "https://old.yummyani.me/search?word={q}"))
# the address of a title's page on each of them — the field's example (in «SFW» — without the sites of hentai)
EXAMPLES = {"hentasis1.top": "https://hentasis1.top/…", "YummyAnime": "https://old.yummyani.me/catalog/item/…"}
MANY = 10                              # a list longer than this (voice-overs) shows its first ones and «ещё N»

for _ext, _type in ((".mkv", "video/x-matroska"), (".webm", "video/webm"), (".m4v", "video/mp4"), (".mp4", "video/mp4"),
                    (".mov", "video/quicktime"), (".avi", "video/x-msvideo"), (".flv", "video/x-flv"),
                    (".wmv", "video/x-ms-wmv"), (".ts", "video/mp2t")):
    mimetypes.add_type(_type, _ext)


def register(router) -> None:
    router.add("GET", r"/videos", index)
    router.add("GET", r"/videos/s/(?P<id>[0-9a-f]{12})", series_page)
    router.add("GET", r"/videos/cover/(?P<id>[0-9a-f]{12})", cover)
    router.add("GET", r"/videos/frame/(?P<id>[0-9a-f]{12})", frame)
    router.add("GET", r"/videos/file/(?P<id>[0-9a-f]{12})", episode_file)
    router.add("GET", r"/videos/subs/(?P<id>[0-9a-f]{12})", episode_subs)
    router.add("POST", r"/api/videos/progress", progress)
    router.add("POST", r"/api/videos/tracks", tracks_choice)
    router.add("POST", r"/api/videos/folder", make_folder)
    router.add("POST", r"/api/videos/(?P<id>[0-9a-f]{12})/meta", meta_url)
    router.add("POST", r"/api/videos/(?P<id>[0-9a-f]{12})/meta-file", meta_file)
    router.add("GET", r"/api/videos/meta-state", meta_state)
    router.add("POST", r"/api/videos/(?P<id>[0-9a-f]{12})/reveal", reveal)
    router.add("POST", r"/api/videos/(?P<id>[0-9a-f]{12})/rating", rating)
    router.add("POST", r"/api/videos/root/reveal", reveal_root)
    router.add("POST", r"/api/videos/episode/(?P<id>[0-9a-f]{12})/open", open_episode)
    router.add("POST", r"/api/videos/card-progress", card_progress)       # (v5.44.121) a long video's place
    from webui.pages import video_series                                     # (v5.44.121) one page of a series
    video_series.register(router)


# ── shared bits ──────────────────────────────────────────────────────────────
def _look() -> dict:
    from webui.pages.gallery import _video_prefs
    return _video_prefs()


def _search_form(tags: str, show: str) -> str:
    hidden = f'<input type="hidden" name="show" value="{esc(show)}">' if show != "all" else ""
    return (f'<form class="top-search" action="/videos" method="get" role="search"><div class="search-row">'
            f'<input type="search" name="tags" value="{esc(tags)}" placeholder="Название, жанр, озвучка или теги" '
            f'autocomplete="off" spellcheck="false" data-autocomplete aria-label="Поиск видео">'
            f'<button type="submit" class="search-go">Найти</button></div>{hidden}</form>')


def tabs_of(req: Request, show: str) -> tuple:
    """The section bar of the tab: «Всё», «Сериалы», (v5.44.90) «Лента» — the sites' titles (``catalog.py``),
    «Длинные видео»."""
    from webui import content_mode
    tags = req.arg("tags").strip()
    long_ok = content_mode.long_videos_shown()          # (v5.44.53) the gallery's long videos: not in «SFW»
    tabs = []
    for key, label in SHOWS:
        if long_ok or key != "long":
            tabs.append((label, url("/videos", tags=tags, show=key), key == show))
        if key == "series":
            tabs.append(("Лента", "/videos/feed", show == "feed"))
    # (v5.44.89) no «Скачать с сайта» here: the owner — «убрать кнопку скачать … оставив только вкладку
    # "скачать" потому что переход с видео скачивание переносит на старое окно»; the menu's «Скачать» alone
    return tuple(tabs)


def _frame(req: Request, title: str, body: str, *, kind: str = "videos", show: str = "", body_data=None) -> Response:
    """A page of the tab; ``show`` — which of its pages is shown in the section bar."""
    tags = req.arg("tags").strip()
    tabs = tabs_of(req, show) if show else ()
    search_show = show if show in dict(SHOWS) else "all"
    return Response.html(page_frame(title, body, active="videos", subnav=False, kind=kind, tabs=tabs,
                                    top_search=_search_form(tags, search_show), body_data=body_data,
                                    assets=("videos", "series"), **_look()))


def _root(req: Request) -> str:
    return V.video_root(req.settings)


def _library(req: Request, *, refresh: bool = False) -> list:
    root = _root(req)
    return V.LIBRARY.items(root, refresh=refresh) if Path(root).is_dir() else []


def _series(req: Request) -> V.Series | None:
    root = _root(req)
    return V.LIBRARY.get(root, req.params["id"]) if Path(root).is_dir() else None


# ── (v5.44.53) «SFW / Всё / NSFW»: which series the mode shows (webui/content_mode.py, core/content_marks.py) ──
def _visible(items: list, now: str | None = None) -> list:
    from core import content_marks as CM
    from webui import content_mode
    now = now or content_mode.mode()
    if now == "all":
        return items
    hand = CM.marks()["series"]
    return [s for s in items if content_mode.shows(CM.series_verdict(s, hand).hentai, now)]


def find_series(settings: dict, series_id: str) -> V.Series | None:
    root = V.video_root(settings)
    return V.LIBRARY.get(root, series_id) if Path(root).is_dir() else None


def item_shown(settings: dict, series_id: str, now: str) -> bool | None:
    """Is a series shown in the mode ``now``; None — no such series."""
    from core import content_marks as CM
    from webui import content_mode
    series = find_series(settings, series_id)
    return None if series is None else content_mode.shows(CM.series_verdict(series).hentai, now)


def _mark_button(series: V.Series, found) -> str:
    """«Это хентай» / «Это не хентай»: the mark by hand for the switch «SFW / Всё / NSFW»."""
    value = "safe" if found.hentai else "hentai"
    label = "Это не хентай" if found.hentai else "Это хентай"
    tip = ("Не прятать в режиме «SFW»" if found.hentai else "Прятать в режиме «SFW», показывать в «NSFW»")
    return (f'<button type="button" class="vbutton" data-action="content-mark" data-kind="series" '
            f'data-id="{series.id}" data-value="{value}" title="{esc(tip)}">{label}</button>')


def _episode(req: Request) -> tuple[V.Series | None, V.Episode | None]:
    root = _root(req)
    return V.LIBRARY.episode(root, req.params["id"]) if Path(root).is_dir() else (None, None)


def _stamp(series: V.Series) -> str:
    try:
        st = Path(series.poster).stat() if series.poster else None
    except OSError:
        st = None
    return f"{int(st.st_mtime)}-{st.st_size}" if st else f"{int(series.mtime)}"


def _matches(series: V.Series, query: str) -> bool:
    words = series.words()
    for raw in query.lower().replace("ё", "е").replace("_", " ").split():
        neg = raw.startswith("-") and len(raw) > 1
        hit = (raw[1:] if neg else raw) in words
        if hit == neg:
            return False
    return True


def _sorted(items: list, sort: str) -> list:
    if sort == "title":
        return sorted(items, key=lambda s: V.natural_key(s.title))
    if sort == "watched":                      # (v5.44.121) the one memory: the folder's and its site's together
        watched = {s.id: V.PROGRESS.last_at(V.memory_keys(s)) for s in items}
        return sorted(items, key=lambda s: (-watched[s.id], -s.mtime))
    if sort == "rating":                       # the person's marks, the highest first; not rated — after them
        return sorted(items, key=lambda s: (-s.rating, V.natural_key(s.title)))
    return sorted(items, key=lambda s: -s.mtime)


def series_counts(series: V.Series) -> dict:
    """(v5.44.121) {"total", "disk", "watched"} of a series: its episodes as the person counts them (``Unit``: the
    sub-folders of voice-overs and subtitles are variants, not episodes), the site's when it is known (the one memory
    keeps how many the site has once it was watched online), how many are watched (in the folder or online)."""
    V.PROGRESS.adopt(series)
    item = V.PROGRESS.view(V.memory_keys(series))
    units = series.units()
    own = [u.key for u in units]
    try:
        site = int(item.get("count") or 0)
    except (TypeError, ValueError):
        site = 0
    mine = set(own)
    seen = {str(x) for x in item.get("watched") or [] if str(x) in mine or (site and "/" not in str(x))}
    total = max(len(own), site) if not series.single else 1
    return {"total": total, "disk": len(own), "watched": len(seen)}


def _series_card(series: V.Series) -> str:
    counts = series_counts(series)
    total, watched = counts["total"], min(counts["watched"], counts["total"])
    badges = []
    if series.rating:
        badges.append(f'<span class="vbadge vrating" title="Ваша оценка: {series.rating} из 10">{STAR}'
                      f'<b>{series.rating}</b></span>')
    if not series.single:
        badges.append(f'<span class="vbadge" data-count="{total}">{total} {_plural(total)}</span>')
        if counts["disk"] < total:                # (v5.44.121) a series of a site: how many are in the folder
            badges.append(f'<span class="vbadge vhave" data-have="{counts["disk"]}">в папке {counts["disk"]}</span>')
    year = str(series.info.get("year") or "").strip()
    if year:
        badges.append(f'<span class="vbadge">{esc(year[:12])}</span>')
    genres = series.info.get("genres") or []
    sub = ", ".join(str(g) for g in genres[:3]) if isinstance(genres, list) else str(genres)
    bar = ""
    if watched:
        part = 100 * watched / max(1, total)
        bar = f'<span class="vcard-bar" style="--p:{part:.0f}%" title="Просмотрено {watched} из {total}"></span>'
    return (f'<a class="vcard" href="/videos/s/{series.id}" title="{esc(series.title)}">'
            f'<span class="vcard-pic"><img src="/videos/cover/{series.id}?v={esc(_stamp(series))}" alt="" '
            f'loading="lazy" decoding="async">{bar}<span class="vbadges">{"".join(badges)}</span></span>'
            f'<span class="vcard-title">{esc(series.title)}</span>'
            + (f'<span class="vcard-sub">{esc(sub)}</span>' if sub else "") + '</a>')


STAR = ('<svg class="vstar" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 2.6l2.9 5.9 6.5.9-4.7 '
        '4.6 1.1 6.5L12 17.4l-5.8 3.1 1.1-6.5L2.6 9.4l6.5-.9z"/></svg>')


def _rating_box(series: V.Series) -> str:
    """The person's own mark, 1…10: ten stars; the same star again takes it back."""
    mark = series.rating
    stars = "".join(
        f'<button type="button" role="radio" class="vstar-btn{" on" if n <= mark else ""}" data-action="vid-rate" '
        f'data-value="{n}" aria-checked="{"true" if n == mark else "false"}" aria-label="{n} из 10" '
        f'title="{n} из 10{" — нажмите ещё раз, чтобы убрать" if n == mark else ""}">{STAR}</button>'
        for n in range(1, 11))
    said = f"{mark} из 10" if mark else "не оценено"
    return (f'<div class="vrate" data-series="{series.id}"><span class="vrate-label">Ваша оценка</span>'
            f'<span class="vrate-stars" role="radiogroup" aria-label="Ваша оценка от 1 до 10">{stars}</span>'
            f'<b class="vrate-value" aria-live="polite">{esc(said)}</b></div>')


def _plural(n: int) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "серия"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "серии"
    return "серий"


# ── /videos ──────────────────────────────────────────────────────────────────
def index(req: Request) -> Response:
    from webui import content_mode
    tags = req.arg("tags").strip()
    show = req.arg("show", "all")
    show = show if show in dict(SHOWS) else "all"
    long_ok = content_mode.long_videos_shown()          # (v5.44.53) the gallery's long videos: not in «SFW»
    if show == "long" and not long_ok:
        show = "series"
    sort = req.arg("sort", "newest")
    sort = sort if sort in SORTS else "newest"
    order = req.arg("order", "newest") or "newest"
    source = req.arg("source", "all") or "all"
    page = req.int_arg("page", 1, lo=1)
    root = _root(req)
    folder_ok = Path(root).is_dir()
    every = _library(req, refresh=bool(req.arg("refresh"))) if folder_ok else []
    items = _visible(every)                             # (v5.44.53) what the mode «SFW / Всё / NSFW» shows
    found = [s for s in items if _matches(s, tags)] if tags else list(items)
    found = _sorted(found, sort)
    parts = []
    head_count = 0
    if show == "all" and not tags:                     # (v5.44.121) as on the phone: the folder's and the sites'
        parts.append(_going_shelf(every, items))

    if show in ("all", "series"):
        shown = found
        pages = 1
        # whole rows of the window's columns (v5.44.1, booru.js ``fitRows``): the shelf and the pages of the series
        if show == "series":
            per_page = rows_per_page(req, "lb_vseries_pp", SERIES_PER_PAGE)
            pages = max(1, (len(found) + per_page - 1) // per_page)
            page_s = min(page, pages)
            shown = found[(page_s - 1) * per_page: page_s * per_page]
            rows = rows_attrs("lb_vseries_pp", SERIES_PER_PAGE, per_page, len(found), page_s)
        else:
            shelf = rows_per_page(req, "lb_vshelf_pp", SHELF)
            shown = found[:shelf]
            rows = rows_attrs("lb_vshelf_pp", SHELF, shelf, len(found))
        head_count += len(found)
        sorts = "".join(f'<a class="{"active" if key == sort else ""}" href="{esc(url("/videos", tags=tags, show=show, sort=key))}">'
                        f'{esc(label)}</a>' for key, label in SORTS.items())
        title = "Сериалы и фильмы из папки видео"
        more = ""
        if show == "all" and len(found) > len(shown):
            more = f'<a class="vmore" href="{esc(url("/videos", tags=tags, show="series", sort=sort))}">Все {len(found)} →</a>'
        if shown:
            body = f'<div class="vcards"{rows}>' + "".join(_series_card(s) for s in shown) + "</div>"
            if show == "series":
                body += paginator(min(page, pages), pages, lambda n: url("/videos", tags=tags, show=show, sort=sort, page=n))
        elif every and not items:                         # (v5.44.53) empty because of the mode
            now = content_mode.mode()
            why = "в папке только хентай" if now == "sfw" else "хентая в папке нет"
            body = (f'<div class="vempty mode-empty"><p>В режиме «{content_mode.LABELS[now]}» здесь пусто: {why}.</p>'
                    '<p class="vactions"><button type="button" class="vbutton primary" data-action="content-mode" '
                    'data-mode="all">Показать всё</button><span class="action-status" role="status"></span></p></div>')
        else:
            body = _no_series(req, root, folder_ok, tags)
        parts.append(f'<section class="vshelf"><div class="vhead"><h2>{esc(title)} <span class="muted">{len(found)}</span>'
                     f'</h2><nav class="vsorts">{sorts}<a href="{esc(url("/videos", tags=tags, show=show, sort=sort, refresh=1))}" '
                     f'title="Перечитать папку видео">Обновить</a></nav>{more}</div>{body}</section>')

    if show in ("all", "long") and long_ok:
        parts.append(_long_section(req, tags, order, source, page))
    waiting = _waiting(req) if long_ok else 0
    note = (f'<p class="vnote">Программа ещё узнаёт длину видео из библиотеки: осталось {waiting}. Длинные появятся '
            f'здесь сами.</p>' if waiting else "")
    body = f'<div class="vindex">{note}{"".join(parts)}</div>'
    return _frame(req, tags or "Видео", body, show=show)


GOING = 8                              # (v5.44.121) series on the shelf «Продолжить просмотр»


def _going_shelf(every: list, shown: list) -> str:
    """(v5.44.121) «Продолжить просмотр» on the computer, as on the phone: the series watched lately — an episode of the
    folder and one watched online alike (the one memory) — the frame of the episode to go on with, where it stopped,
    how much is left, from where it plays.  "" — nothing watched yet."""
    from webui.pages import video_series
    tiles = video_series.going(every, shown, limit=GOING)
    if not tiles:
        return ""
    cards = []
    for t in tiles:
        picture = (f'<img src="{esc(t["picture"])}" alt="" loading="lazy" decoding="async">' if t["picture"] else "")
        at = f"с {video_series.human(t['at'])}" if t["at"] else "с начала"
        cards.append(f'<a class="vgo" href="{esc(t["href"])}"><span class="vgo-pic">{picture}'
                     f'<span class="vgo-play">▷ {esc(at)}</span>'
                     + (f'<span class="vgo-time">{esc(t["clock"])}</span>' if t["clock"] else "")
                     + f'<span class="vgo-bar"><i style="width:{int(t["part"])}%"></i></span></span>'
                     f'<span class="vgo-title">{esc(t["title"])}</span><span class="vgo-what">{esc(t["words"])}</span>'
                     f'<span class="vgo-sub">' + (f'{esc(t["left"])} · ' if t["left"] else "")
                     + f'<span class="{esc(t["where"])}">{esc(t["from"])}</span></span></a>')
    return (f'<section class="vgoing" aria-labelledby="v-going"><div class="vhead"><h2 id="v-going">Продолжить '
            f'просмотр</h2><span class="vgoing-note">где остановились — серии из папки и с сайта</span>'
            f'<a class="vmore" href="/videos?show=series&amp;sort=watched">Все сериалы →</a></div>'
            f'<div class="vgoing-list">{"".join(cards)}</div></section>')


_WAITING = {"at": 0.0, "value": 0}
WAITING_TTL = 15.0                     # seconds the number of videos still to be measured is kept


def _waiting(req: Request) -> int:
    """Video cards whose length the background duty has not read yet (counted at most every ``WAITING_TTL``)."""
    if data.video_limit(req.settings) <= 0:
        return 0
    if time.monotonic() - _WAITING["at"] < WAITING_TTL:
        return int(_WAITING["value"])
    from core import video_probe
    try:
        value = video_probe.waiting(req.settings)
    except Exception as exc:  # a locked or old base: the note is a convenience
        _LOG.debug("video lengths waiting: %s", exc)
        value = 0
    _WAITING.update(at=time.monotonic(), value=value)
    return value


def _long_section(req: Request, tags: str, order: str, source: str, page: int) -> str:
    limit = data.video_limit(req.settings)
    if limit <= 0:
        return ('<section class="vlong"><div class="vhead"><h2>Длинные видео</h2></div><p class="hint">Все видео '
                'библиотеки остаются в галерее: так выбрано в «<a href="/settings#set-videos">Настройки → Вкладка '
                '«Видео»</a>».</p></section>')
    per_page = long_per_page(req)
    items, total, _files_total = data.search_page(req.settings, tags, section=data.VIDEO_SECTION, source=source,
                                                  order=order, page=page, per_page=per_page)
    pages = max(1, (total + per_page - 1) // per_page)
    ctx = {"tags": tags, "section": data.VIDEO_SECTION, "source": source, "order": order}
    from webui.pages.gallery import _thumb
    if items:
        first = (page - 1) * per_page
        rows = rows_attrs("lb_vlong_pp", LONG_PER_PAGE, per_page, total, min(page, pages))
        grid = (f'<div class="thumbs"{rows}>' + "".join(_long_tile(_thumb(it, ctx, first + n), it)
                                                       for n, it in enumerate(items)) + "</div>")
        grid += paginator(min(page, pages), pages, lambda n: url("/videos", tags=tags, show=req.arg("show", "all"),
                                                                  order=order, source=source, page=n))
    else:
        what = f"по запросу «{esc(tags)}» " if tags else ""
        grid = (f'<p class="hint">Длинных видео {what}в библиотеке нет. Сюда попадают видео длиннее '
                f'{esc(_seconds(limit))} — их нет в галерее.</p>')
    orders = "".join(f'<a class="{"active" if key == order else ""}" href="{esc(url("/videos", tags=tags, show="long", order=key))}">'
                     f'{esc(label)}</a>' for key, label in data.ORDERS.items())
    return (f'<section class="vlong"><div class="vhead"><h2>Длинные видео библиотеки <span class="muted">{total}</span></h2>'
            f'<nav class="vsorts">{orders}</nav></div>{grid}</section>')


def _long_tile(tile: str, item: dict) -> str:
    """(v5.44.121) A long video's tile with what was watched of it (the one memory): a bar under its picture and
    «▷ Продолжить с 12:34» / «✓ досмотрено» / «не смотрели»."""
    place = card_place(item)
    if place.get("done"):
        part, words, kind = 100, "✓ досмотрено", "done"
    elif place:
        part = round(100 * place["at"] / place["length"]) if place["length"] else 0
        words, kind = f"▷ Продолжить с {human_duration(place['at'])}", "here"
    else:
        part, words, kind = 0, "не смотрели", ""
    bar = f'<span class="vlong-bar" aria-hidden="true"><i style="width:{part}%"></i></span>' if part else ""
    line = f'<p class="vlong-place{(" " + kind) if kind else ""}" data-place="{kind or "none"}">{esc(words)}</p>'
    if bar:                                   # under the picture itself (its caption may follow it in the link)
        tile = re.sub(r"(<img [^>]*>)", lambda m: m.group(1) + bar, tile, count=1)
    return tile.replace("</article>", line + "</article>", 1)


def long_per_page(req: Request) -> int:
    """Long videos on a page of the tab: whole rows of the window's columns about :data:`LONG_PER_PAGE` (v5.44.1,
    booru.js ``fitRows``); the post page counts its way back («К результатам») by the same number."""
    return rows_per_page(req, "lb_vlong_pp", LONG_PER_PAGE)


def _seconds(limit: int) -> str:
    return f"{limit // 60} мин" if limit >= 60 and limit % 60 == 0 else f"{limit} с"


def _no_series(req: Request, root: str, folder_ok: bool, tags: str) -> str:
    rules = ('<p class="hint">Одна папка — один сериал, видеофайлы в ней — серии (номера берутся из имён файлов). '
             'Подпапки сериала — озвучки или сезоны: они будут вкладками над сериями. Видеофайл прямо в папке видео — '
             'отдельный фильм. Описание и обложку можно взять с сайта на странице сериала, а серии — '
             '<a href="/get">скачать по ссылке</a>.</p>')
    if tags:
        return f'<p class="hint">По запросу «{esc(tags)}» сериалов нет.</p>'
    if not folder_ok:
        chosen = bool(str(req.settings.get("video_root") or "").strip())
        create = ('' if chosen else '<button type="button" class="vbutton primary" data-action="vid-make-folder">'
                  'Создать папку</button>')
        return (f'<div class="vempty"><p>Папки видео {"нет на месте" if chosen else "ещё нет"}: '
                f'<code>{esc(root)}</code>.</p>{rules}<p class="vactions">{create}'
                f'<button type="button" class="vbutton" data-action="vid-choose-folder">Выбрать другую папку</button>'
                f'<span class="action-status" role="status"></span></p></div>')
    return (f'<div class="vempty"><p>В папке видео пока ничего нет: <code>{esc(root)}</code>.</p>{rules}'
            f'<p class="vactions"><button type="button" class="vbutton" data-action="vid-reveal-root">Показать папку</button>'
            f'<button type="button" class="vbutton" data-action="vid-choose-folder">Выбрать другую папку</button>'
            f'<span class="action-status" role="status"></span></p></div>')


# ── /videos/s/<id> ────────────────────────────────────────────────────────────
def series_page(req: Request) -> Response:
    """(v5.44.121) One page of a series — the folder and its site together (``webui/pages/video_series.py``)."""
    series = _series(req)
    if series is None:
        return _frame(req, "Нет такого видео", '<div class="vempty"><p>Такого сериала нет — возможно, папку '
                      'переименовали или убрали.</p><p><a href="/videos?refresh=1">Перечитать папку видео</a></p></div>')
    from core import content_marks as CM
    from webui import content_mode
    found = CM.series_verdict(series)              # (v5.44.53) the mode «SFW / Всё / NSFW» hides it
    if not content_mode.shows(found.hentai):
        body = content_mode.hidden_item(active="videos", what="этот сериал", reason=found.reason, hentai=found.hentai,
                                        back="/videos", back_label="Все видео")
        return _frame(req, "Скрыто", body, kind="mode-hidden")
    from webui.pages import video_series
    ref = video_series.resolve(req, series.id)
    return video_series.render(req, ref)


def resume_words(single: bool, label: str, resume: float, *, going: bool = False) -> str:
    """(v5.44.116) The words of the button that starts the series — one formula on the computer, the phone and a
    title of a site (``video_series``): «Продолжить: серия 5 с 12:34», «Продолжить: серия 6» (``going`` — the one
    after the episode watched to the end), «Смотреть: серия 1»; a film — «Продолжить с 12:34» / «Смотреть».  (Before:
    «▶ Продолжить с 12:34 серию 5» here, «Продолжить с 12:34 · серия 5» on the phone.)"""
    at = f" с {human_duration(resume)}" if resume > 0 else ""
    word = "Продолжить" if at or going else "Смотреть"
    return word + at if single else f"{word}: серия {label}{at}"


def watch_words(series: V.Series, episode: V.Episode, resume: float, *, going: bool = False) -> str:
    """:func:`resume_words` of a file of an episode (its episode's number)."""
    unit = series.unit_of(episode)
    return resume_words(series.single, unit.label if unit is not None else episode.label, resume, going=going)


def _yummy(req: Request, source: str) -> bool:
    """The series came from a YummyAnime page and its voice-overs are taken from there: «Скачать с сайта» and its
    «Порядок просмотра» (``video_get.series_order``) — not in the public copy (its ``core/video_dubs``: a stand-in)."""
    from core import video_dubs
    yummy = getattr(video_dubs, "is_yummy", None)
    return bool(getattr(video_dubs, "AVAILABLE", True) and yummy is not None and yummy(source)
                and video_dubs.enabled(req.settings))


def _skips(series: V.Series, episode: V.Episode) -> dict:
    """{"opening": [start, end], "ending": [start, end], "length": seconds} of an episode — the ones known
    (``core/video_skips``)."""
    from core import video_skips
    try:
        known = video_skips.skips_of(Path(series.path), Path(episode.path))
    except (OSError, ValueError) as exc:
        _LOG.info("skips of %s not read: %s", episode.path, exc)
        return {}
    out = {k: [round(float(v[0]), 2), round(float(v[1]), 2)] for k in ("opening", "ending")
           if isinstance(v := known.get(k), (list, tuple)) and len(v) == 2 and float(v[1]) > float(v[0])}
    if out and float(known.get("length") or 0) > 0:
        out["length"] = round(float(known["length"]), 1)      # the bar is marked before the episode plays
    return out


def _meta_box(req: Request, series: V.Series) -> str:
    from core.content_marks import adult_site
    from webui import content_mode
    q = quote(series.folder_title if not series.info.get("title") else str(series.info["title"]))
    # (v5.44.116) in «SFW» no site of hentai is named as a place to find the description (the review: hentasis on
    # the series page in «SFW»)
    sfw = content_mode.mode() == "sfw"
    sites = [(name, tpl) for name, tpl in SEARCH_SITES if not (sfw and adult_site(tpl))]
    links = " · ".join(f'<a href="{esc(tpl.format(q=q))}" target="_blank" rel="noopener noreferrer">на {esc(name)}</a>'
                       for name, tpl in sites)
    example = " или ".join(EXAMPLES[name] for name, _tpl in sites if name in EXAMPLES) or "https://…"
    hidden = " hidden" if series.info else ""
    return f"""<section class="vmeta" id="vid-meta" data-series="{series.id}"{hidden}>
  <h2>Описание с сайта</h2>
  <form class="vmeta-url" data-form="vid-meta-url">
    <label for="vid-meta-url">Ссылка на страницу тайтла</label>
    <div class="vmeta-row"><input id="vid-meta-url" type="text" inputmode="url" name="url" spellcheck="false" autocomplete="off"
      value="{esc(series.info.get('source_url') or '')}" placeholder="{esc(example)}">
    <button type="submit" class="vbutton primary">Взять описание</button></div>
  </form>
  <p class="hint">Найти тайтл: {links} — откройте его страницу и скопируйте адрес сюда. Возьмутся название, год, жанры,
  студия, кто озвучивал, описание и обложка.</p>
  <form class="vmeta-file" data-form="vid-meta-file">
    <label for="vid-meta-file">Или сохранённая страница</label>
    <div class="vmeta-row"><input id="vid-meta-file" type="file" name="file" accept=".html,.htm,.mhtml,.mht,.zip">
    <button type="submit" class="vbutton">Загрузить</button></div>
  </form>
  <label class="vreplace"><input type="checkbox" name="replace_poster" data-meta-replace> заменить обложку, если в папке уже
  есть своя</label>
  <p class="hint">Если сайт не пускает программу (проверка «я не робот»), откройте страницу в браузере, сохраните её
  (Ctrl+S: «Веб-страница, один файл» — .mhtml, или полностью) и загрузите файл сюда. Подойдёт и .zip сохранённой
  страницы.</p>
  <div class="job" data-job hidden>
    <div class="job-head"><b class="job-title"></b> <span class="job-status"></span></div>
    <pre class="job-log" tabindex="0" aria-label="Журнал"></pre>
  </div>
</section>"""


# ── pictures and files ───────────────────────────────────────────────────────────
def _placeholder(text: str, width: int, height: int) -> Response:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
           f'<rect width="{width}" height="{height}" fill="#8884"/><text x="{width // 2}" y="{height // 2}" '
           f'font-family="sans-serif" font-size="{max(12, width // 18)}" text-anchor="middle" fill="#888">{esc(text)}</text></svg>')
    return Response(200, svg.encode("utf-8"), "image/svg+xml", {"Cache-Control": "no-cache"})


def cover(req: Request) -> Response:
    series = _series(req)
    width, height = COVER.get(req.arg("size", "s"), COVER["s"])
    if series is None:
        return _placeholder("нет", width, height)
    from webui.pages.media import _MAKING
    with _MAKING:
        made = V.poster_picture(series, width, height)
    if made is None:
        return _placeholder("нет кадра", width, height)
    keep = "private, max-age=31536000, immutable" if req.arg("v") else "no-cache"
    return Response(200, content_type="image/jpeg", file=made, headers={"Cache-Control": keep})


def frame(req: Request) -> Response:
    """The frame of an episode (its poster on the page), or the ``n``-th of its slides (``?n=``)."""
    _series_of, episode = _episode(req)
    slide = req.arg("n")
    from webui.pages.media import _MAKING
    if slide:                       # a slide that cannot be had is not there (never a placeholder): the page skips it
        if episode is None or not re.fullmatch(r"[0-9]{1,2}", slide) or int(slide) >= V.SLIDES:
            return Response.not_found("Такого кадра нет")
        with _MAKING:
            made = V.episode_slide(episode, int(slide), *FRAME)
        if made is None:
            return Response.not_found("Кадра здесь нет")
        return Response(200, content_type="image/jpeg", file=made, headers={"Cache-Control": "private, max-age=3600"})
    if episode is None:
        return _placeholder("нет серии", *FRAME)
    with _MAKING:
        made = V.episode_frame(episode, *FRAME)
    if made is None:
        return _placeholder("нет кадра", *FRAME)
    return Response(200, content_type="image/jpeg", file=made, headers={"Cache-Control": "private, max-age=3600"})


def _top(series: V.Series | None) -> str | None:
    """The folder of a series whose folders of subtitles are its episodes' too (a film in the video folder: none)."""
    return series.path if series is not None and not series.single else None


def episode_subs(req: Request) -> Response:
    """The ``n``-th subtitle file of an episode as WebVTT (a browser's ``<track>``)."""
    from core import video_tracks
    series, episode = _episode(req)
    number = req.arg("n")
    if episode is None or not re.fullmatch(r"[0-9]{1,3}", number):
        return Response.not_found("Таких субтитров нет")
    try:
        files = video_tracks.subtitle_files(episode.path, top=_top(series))
        if int(number) >= len(files):
            return Response.not_found("Таких субтитров нет")
        cues = video_tracks.read_cues(files[int(number)].path)
    except (OSError, ValueError) as exc:
        return Response(404, f"Субтитры не прочитались: {exc}".encode("utf-8"), "text/plain; charset=utf-8")
    return Response(200, video_tracks.to_vtt(cues).encode("utf-8"), "text/vtt; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})


def tracks_choice(req: Request) -> Response:
    """The subtitles chosen in a browser's player (``value`` "xN" — the N-th file, "-1" — none) or the voice-over
    chosen under the episodes before the play (``audio`` — its track's title): kept for the series, its next episode
    opens with them."""
    from core import video_tracks
    body = req.json()
    root = _root(req)
    series, episode = V.LIBRARY.episode(root, str(body.get("episode") or "")) if Path(root).is_dir() else (None, None)
    if episode is None:
        return Response.json({"ok": False, "error": "Такой серии нет"}, 404)
    key = V.series_key(series)                    # (v5.44.121) the one memory: the series' own key
    audio = str(body.get("audio") or "").strip()
    if audio:                                     # «Озвучка» of the episode, chosen before the play
        V.PROGRESS.set_tracks(key, "audio", {"name": "", "title": audio[:120], "lang": ""})
        return Response.json({"ok": True})
    value = str(body.get("value") or "")
    if value == "-1":
        V.PROGRESS.set_tracks(key, "subtitle", {"off": True})
        return Response.json({"ok": True})
    files = video_tracks.subtitle_files(episode.path, top=_top(series)) if re.fullmatch(r"x[0-9]{1,3}", value) else []
    if not files or int(value[1:]) >= len(files):
        return Response.json({"ok": False, "error": "Таких субтитров нет"}, 400)
    name = files[int(value[1:])].name
    V.PROGRESS.set_tracks(key, "subtitle", {"name": name, "title": name, "lang": "", "file": True})
    return Response.json({"ok": True})


def episode_file(req: Request) -> Response:
    _series_of, episode = _episode(req)
    if episode is None or not Path(episode.path).is_file():
        return Response.not_found("Такой серии нет")
    ctype = mimetypes.guess_type(episode.name)[0] or "application/octet-stream"
    return Response(200, content_type=ctype, file=Path(episode.path), headers={"Cache-Control": "no-cache"})


# ── the API ────────────────────────────────────────────────────────────────────
def progress(req: Request) -> Response:
    """Where a file of an episode stopped (the pages of before; the window's player of the program): (v5.44.121) its
    episode's place in the one memory of the series (``video_library.record_file``)."""
    body = req.json()
    root = _root(req)
    series, episode = V.LIBRARY.episode(root, str(body.get("episode") or "")) if Path(root).is_dir() else (None, None)
    if episode is None:
        return Response.json({"ok": False, "error": "Такой серии нет"}, 404)
    try:
        position = max(0.0, float(body.get("position") or 0))
        duration = max(0.0, float(body.get("duration") or 0))
        ending = max(0.0, float(body.get("ending") or 0))
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Неверное время"}, 400)
    entry = V.record_file(series, episode, position, duration, ended=bool(body.get("ended")), ending=ending)
    return Response.json({"ok": True, "done": bool(entry.get("done"))})


def card_progress(req: Request) -> Response:
    """(v5.44.121) {id, position, duration, ended}: where a long video of the library stopped — its place is kept
    («Продолжить с 12:34», a bar on its tile); the key is its file's (``video_library.card_key``), never the page's."""
    body = req.json()
    try:
        image_id = int(body.get("id"))
        position = max(0.0, float(body.get("position") or 0))
        duration = max(0.0, float(body.get("duration") or 0))
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Нет номера карточки или времени"}, 400)
    key = V.card_key(data.post_detail(req.settings, image_id) or {})
    if not key:
        return Response.json({"ok": False, "error": "Такой карточки нет"}, 404)
    kept = V.PROGRESS.record(key, "1", position, duration, ended=bool(body.get("ended")))
    return Response.json({"ok": True, "done": bool(kept.get("done"))})          # this play: to its end


def card_place(item: dict) -> dict:
    """(v5.44.121) What was watched of a long video: {"at", "length", "done"} ({} — never played).  The last play
    decides: stopped at a place — from there (watched to its end before, too); played to its end — «досмотрено»."""
    key = V.card_key(item)
    kept = V.PROGRESS.of(key) if key else {}
    if not kept:
        return {}
    place = (kept.get("places") or {}).get("1")
    if isinstance(place, list) and len(place) == 2 and not kept.get("done"):
        return {"at": float(place[0]), "length": float(place[1]), "done": False}
    if "1" in (kept.get("watched") or []):
        return {"at": 0.0, "length": float(kept.get("length") or 0), "done": True}
    return {}


def make_folder(req: Request) -> Response:
    """«Создать папку»: the default video folder in the archive (a chosen folder is never made here)."""
    if str(req.settings.get("video_root") or "").strip():
        return Response.json({"ok": False, "error": "Папка видео выбрана в настройках — подключите её диск или "
                                                    "выберите другую"}, 409)
    root = Path(V.video_root(req.settings))
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Response.json({"ok": False, "error": f"Папка не создалась: {exc}"}, 500)
    V.LIBRARY.invalidate()
    return Response.json({"ok": True, "path": str(root)})


def rating(req: Request) -> Response:
    """The person's mark of a series: {"value": 1…10, or 0 — taken back}."""
    from core import video_meta_import as I
    series = _series(req)
    if series is None:
        return Response.json({"ok": False, "error": "Такого сериала нет"}, 404)
    value = req.json().get("value")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
        return Response.json({"ok": False, "error": "Оценка — число от 1 до 10 (0 — убрать)"}, 400)
    try:
        I.set_rating(series, value)
    except I.MetaImportError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 409)
    return Response.json({"ok": True, "value": value})


def reveal_root(req: Request) -> Response:
    return _reveal(Path(_root(req)))


def reveal(req: Request) -> Response:
    series = _series(req)
    if series is None:
        return Response.json({"ok": False, "error": "Такого сериала нет"}, 404)
    return _reveal(Path(series.path))


def _reveal(target: Path) -> Response:
    from webui import desktop
    try:
        desktop.reveal(target)
    except OSError as exc:
        return Response.json({"ok": False, "error": f"Не удалось открыть папку: {exc}"}, 500)
    return Response.json({"ok": True})


def open_episode(req: Request) -> Response:
    _series_of, episode = _episode(req)
    if episode is None:
        return Response.json({"ok": False, "error": "Такой серии нет"}, 404)
    from webui import desktop
    try:
        desktop.open_file(Path(episode.path))
    except OSError as exc:
        return Response.json({"ok": False, "error": f"Не открылось: {exc}"}, 500)
    return Response.json({"ok": True})


def _window_fetch():
    """The program window's browser, for a site that checks for robots (None without the window)."""
    from webui import service
    if service.bridge() is None:
        return None

    def fetch(address: str) -> tuple[str, str]:
        from core.video_meta_import import MetaImportError
        box: dict = {}
        state = service.call_window("fetch_page", {"url": address, "box": box}, wait=10.0, finish_wait=60.0)
        if state != "done":
            raise MetaImportError("Окно программы не открыло страницу" + (" (не дождались)" if state == "late" else ""))
        if box.get("error"):
            raise MetaImportError(str(box["error"]))
        return str(box.get("html") or ""), str(box.get("url") or address)
    return fetch


def _start(req: Request, series: V.Series, label: str, get_page, replace: bool) -> Response:
    from core import page_jobs
    from core import video_meta_import as I

    def run(say, stop_check):
        try:
            page = get_page(say)
            result = I.import_into(series, page, progress=say, stop_check=stop_check, replace_poster=replace)
        except I.MetaImportError as exc:
            return {"failed": str(exc)}
        return result
    try:
        task = page_jobs.start(KIND, label, run, about=series.id)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца."}, 409)
    return Response.json({"ok": True, "id": task.id})


def meta_url(req: Request) -> Response:
    from core import video_meta_import as I
    series = _series(req)
    if series is None:
        return Response.json({"ok": False, "error": "Такого сериала нет"}, 404)
    body = req.json()
    try:
        address = I.check_url(str(body.get("url") or ""))
    except I.MetaImportError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 400)
    window = _window_fetch()

    def get_page(say):
        say(f"Страница: {address}")
        return I.page_from_url(address, progress=say, window_fetch=window)
    return _start(req, series, f"Описание с сайта для «{series.title}»", get_page, bool(body.get("replace_poster")))


def meta_file(req: Request) -> Response:
    from core import video_meta_import as I
    series = _series(req)
    if series is None:
        return Response.json({"ok": False, "error": "Такого сериала нет"}, 404)
    name = Path(req.arg("name") or "page.html").name
    if not name.lower().endswith(I.SAVED_EXTS):
        return Response.json({"ok": False, "error": "Нужна сохранённая страница: .html, .mhtml или .zip"}, 400)
    if not req.body:
        return Response.json({"ok": False, "error": "Файл пустой"}, 400)
    body = req.body

    def get_page(say):
        say(f"Сохранённая страница: {name} ({len(body) // 1024} КБ)")
        return I.page_from_file(name, body)
    return _start(req, series, f"Описание из файла для «{series.title}»", get_page, req.arg("replace") == "1")


def meta_state(req: Request) -> Response:
    """The task of «Описание с сайта» — of this series only (``series``)."""
    from core import page_jobs
    task = page_jobs.current(KIND)
    wanted = req.arg("series")
    if task is None or (wanted and task.about != wanted):
        return Response.json({"ok": True, "status": "idle"})
    return Response.json({"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0))})
