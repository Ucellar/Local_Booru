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

import json
import logging
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import quote

from core import video_library as V
from core.video_meta import FIELD_ORDER, FIELD_TITLES, LIST_FIELDS
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


def _frame(req: Request, title: str, body: str, *, kind: str = "videos", show: str = "", body_data=None) -> Response:
    """A page of the tab; ``show`` — which of its pages is shown in the section bar ("get" — «Скачать с сайта»)."""
    tags = req.arg("tags").strip()
    tabs = ()
    if show:
        tabs = tuple((label, url("/videos", tags=tags, show=key), key == show) for key, label in SHOWS)
        tabs += (("Скачать с сайта", "/videos/get", show == "get"),)
    search_show = show if show in dict(SHOWS) else "all"
    return Response.html(page_frame(title, body, active="videos", subnav=False, kind=kind, tabs=tabs,
                                    top_search=_search_form(tags, search_show), body_data=body_data, **_look()))


def _root(req: Request) -> str:
    return V.video_root(req.settings)


def _library(req: Request, *, refresh: bool = False) -> list:
    root = _root(req)
    return V.LIBRARY.items(root, refresh=refresh) if Path(root).is_dir() else []


def _series(req: Request) -> V.Series | None:
    root = _root(req)
    return V.LIBRARY.get(root, req.params["id"]) if Path(root).is_dir() else None


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
    if sort == "watched":
        watched = {s.id: V.PROGRESS.last_at(s.id) for s in items}
        return sorted(items, key=lambda s: (-watched[s.id], -s.mtime))
    if sort == "rating":                       # the person's marks, the highest first; not rated — after them
        return sorted(items, key=lambda s: (-s.rating, V.natural_key(s.title)))
    return sorted(items, key=lambda s: -s.mtime)


def _series_card(series: V.Series) -> str:
    done = V.PROGRESS.episodes([e.id for e in series.episodes])
    watched = sum(1 for e in series.episodes if done.get(e.id, {}).get("done"))
    badges = []
    if series.rating:
        badges.append(f'<span class="vbadge vrating" title="Ваша оценка: {series.rating} из 10">{STAR}'
                      f'<b>{series.rating}</b></span>')
    if not series.single:
        badges.append(f'<span class="vbadge">{len(series.episodes)} {_plural(len(series.episodes))}</span>')
    year = str(series.info.get("year") or "").strip()
    if year:
        badges.append(f'<span class="vbadge">{esc(year[:12])}</span>')
    genres = series.info.get("genres") or []
    sub = ", ".join(str(g) for g in genres[:3]) if isinstance(genres, list) else str(genres)
    bar = ""
    if watched:
        part = 100 * watched / max(1, len(series.episodes))
        bar = f'<span class="vcard-bar" style="--p:{part:.0f}%" title="Просмотрено {watched} из {len(series.episodes)}"></span>'
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
    tags = req.arg("tags").strip()
    show = req.arg("show", "all")
    show = show if show in dict(SHOWS) else "all"
    sort = req.arg("sort", "newest")
    sort = sort if sort in SORTS else "newest"
    order = req.arg("order", "newest") or "newest"
    source = req.arg("source", "all") or "all"
    page = req.int_arg("page", 1, lo=1)
    root = _root(req)
    folder_ok = Path(root).is_dir()
    items = _library(req, refresh=bool(req.arg("refresh"))) if folder_ok else []
    found = [s for s in items if _matches(s, tags)] if tags else list(items)
    found = _sorted(found, sort)
    parts = []
    head_count = 0

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
        else:
            body = _no_series(req, root, folder_ok, tags)
        parts.append(f'<section class="vshelf"><div class="vhead"><h2>{esc(title)} <span class="muted">{len(found)}</span>'
                     f'</h2><nav class="vsorts">{sorts}<a href="{esc(url("/videos", tags=tags, show=show, sort=sort, refresh=1))}" '
                     f'title="Перечитать папку видео">Обновить</a></nav>{more}</div>{body}</section>')

    if show in ("all", "long"):
        parts.append(_long_section(req, tags, order, source, page))
    waiting = _waiting(req)
    note = (f'<p class="vnote">Программа ещё узнаёт длину видео из библиотеки: осталось {waiting}. Длинные появятся '
            f'здесь сами.</p>' if waiting else "")
    body = f'<div class="vindex">{note}{"".join(parts)}</div>'
    return _frame(req, tags or "Видео", body, show=show)


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
    items, total = data.search_page(req.settings, tags, section=data.VIDEO_SECTION, source=source, order=order,
                                    page=page, per_page=per_page)
    pages = max(1, (total + per_page - 1) // per_page)
    ctx = {"tags": tags, "section": data.VIDEO_SECTION, "source": source, "order": order}
    from webui.pages.gallery import _thumb
    if items:
        first = (page - 1) * per_page
        rows = rows_attrs("lb_vlong_pp", LONG_PER_PAGE, per_page, total, min(page, pages))
        grid = f'<div class="thumbs"{rows}>' + "".join(_thumb(it, ctx, first + n) for n, it in enumerate(items)) + "</div>"
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
             '<a href="/videos/get">скачать с сайта</a>.</p>')
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
    series = _series(req)
    if series is None:
        return _frame(req, "Нет такого видео", '<div class="vempty"><p>Такого сериала нет — возможно, папку '
                      'переименовали или убрали.</p><p><a href="/videos?refresh=1">Перечитать папку видео</a></p></div>')
    episode = series.episode(req.arg("ep")) or series.episode(V.PROGRESS.last(series.id)) or series.episodes[0]
    play = req.arg("play") == "1"
    full = play and req.arg("full") == "1"         # the next episode after one played full size: full size again
    done = V.PROGRESS.episodes([e.id for e in series.episodes])
    here = done.get(episode.id, {})
    resume = float(here.get("pos") or 0) if not here.get("done") else 0.0
    length = float(here.get("dur") or 0)
    if resume < 5 or (length and resume > length - min(10.0, length * 0.1)):     # the very start or the end: anew
        resume = 0.0
    before, after = series.neighbours(episode)
    # the episodes over the player (as on the sites): the next one is chosen where the play is seen
    watch = (f'<div class="vwatch" id="player">{_episodes(series, episode, done)}'
             f'{_dubs_row(req, series, episode)}'
             f'{_player(req, series, episode, before, after, resume, play, full, length=length)}</div>')
    body = (f'<div class="vseries" data-series="{series.id}">{_hero(req, series, episode, resume)}{watch}'
            f'{_meta_box(req, series)}</div>')
    from webui import prefs
    autonext = str(prefs.load().get("video_autonext") or "on")
    return _frame(req, series.title, body, kind="video-series", body_data={"autonext": autonext})


def _hero(req: Request, series: V.Series, episode: V.Episode, resume: float) -> str:
    info = series.info
    alt = " / ".join(esc(t) for t in series.alt_titles[:4])
    facts = []
    for key in FIELD_ORDER:
        value = info.get(key)
        if not value:
            continue
        facts.append(f'<dt>{esc(FIELD_TITLES[key])}</dt><dd>{_value(key, value)}</dd>')
    for pair in info.get("extra") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and str(pair[0]).strip():
            facts.append(f'<dt>{esc(pair[0])}</dt><dd>{esc(pair[1])}</dd>')
    ratings = [f'{esc(pair[0])} <b>{esc(pair[1])}</b>' for pair in (info.get("ratings") or [])
               if isinstance(pair, (list, tuple)) and len(pair) == 2]
    if ratings:
        facts.append(f'<dt>Оценки</dt><dd>{" · ".join(ratings)}</dd>')
    if not series.single:
        facts.append(f'<dt>Файлов</dt><dd>{len(series.episodes)}</dd>')
    tags = info.get("tags") or []
    tag_html = ""
    if isinstance(tags, list) and tags:
        tag_html = '<p class="vtags">' + "".join(
            f'<a class="vchip" href="{esc(url("/videos", tags=str(t)))}">{esc(t)}</a>' for t in tags[:30]) + "</p>"
    text = str(info.get("description") or "").strip()
    desc = ""
    if text:
        paragraphs = "".join(f"<p>{esc(p)}</p>" for p in text.split("\n\n") if p.strip())
        long = " long" if len(text) > 700 else ""
        desc = (f'<div class="vdesc{long}" data-desc><div class="vdesc-text">{paragraphs}</div>'
                + ('<button type="button" class="link" data-action="vid-desc">Показать полностью</button>' if long else "")
                + "</div>")
    watch = "Смотреть" if not resume else f"Продолжить с {human_duration(resume)}"
    label = "" if series.single else f" серию {esc(episode.label)}"
    href = f"/videos/s/{series.id}?ep={episode.id}&play=1#player"
    source = str(info.get("source_url") or "")
    buttons = [f'<a class="vbutton primary" href="{esc(href)}">▶ {esc(watch)}{label}</a>',
               '<button type="button" class="vbutton" data-action="vid-meta-open" aria-controls="vid-meta">Описание с сайта</button>',
               f'<button type="button" class="vbutton" data-action="vid-reveal" data-id="{series.id}">Показать в папке</button>']
    if source.startswith(("http://", "https://")):
        buttons.append(f'<a class="vbutton" href="{esc(source)}" target="_blank" rel="noopener noreferrer">'
                       f'{esc(info.get("site") or "На сайте")}</a>')
        from core.video_download import is_frame_site
        if not series.single and not is_frame_site(source):
            buttons.append(f'<a class="vbutton" href="{esc(url("/videos/get", url=source))}" '
                           f'title="Скачать серии с этой страницы сайта (те, которых ещё нет)">Скачать с сайта</a>')
    where = ""
    try:
        imported = int(info.get("imported_at") or 0)        # info.json may have been edited by hand
    except (TypeError, ValueError):
        imported = 0
    if imported > 0:
        when = time.strftime("%d.%m.%Y", time.localtime(imported))
        where = f'<p class="vsource muted">Описание взято {esc(when)}' + (f' с {esc(info.get("site"))}' if info.get("site") else "") + '</p>'
    return (f'<section class="vhero"><a class="vposter" href="{esc(href)}">'
            f'<img src="/videos/cover/{series.id}?size=l&v={esc(_stamp(series))}" alt=""></a>'
            f'<div class="vabout"><h1>{esc(series.title)}</h1>'
            + (f'<p class="valt">{alt}</p>' if alt else "") + _rating_box(series)
            + (f'<dl class="vfacts">{"".join(facts)}</dl>' if facts else "")
            + f'{tag_html}{desc}<div class="vactions">{"".join(buttons)}<span class="action-status" role="status"></span></div>'
            f'{where}</div></section>')


def _value(key: str, value) -> str:
    if isinstance(value, list):
        items = [str(v) for v in value if str(v).strip()]
        if key in ("genres", "voice", "studio", "translation", "director"):
            chips = [f'<a class="vchip" href="{esc(url("/videos", tags=v))}">{esc(v)}</a>' for v in items]
        else:
            chips = [esc(v) for v in items]
        if key == "notes":
            return "<br>".join(chips)
        if len(chips) > MANY:
            return (" ".join(chips[:MANY]) + f'<details class="vmore-list"><summary>ещё {len(chips) - MANY}</summary>'
                    + " ".join(chips[MANY:]) + "</details>")
        return " ".join(chips) if key in LIST_FIELDS else ", ".join(chips)
    return esc(value)


def _player(req: Request, series: V.Series, episode: V.Episode, before, after, resume: float, play: bool,
            full: bool = False, *, length: float = 0.0) -> str:
    in_app = "QtWebEngine" in req.headers.get("User-Agent", "")
    # (v5.44) in the program window the program's own player plays an episode — but AV1 where it cannot (it decodes
    # AV1 only with a graphics card that can; learnt by the first such episode): the window's browser plays that one
    # (``gallery.window_player``); a player that cannot play the file after all hands it to the other one, once
    # (``other``: this page with it)
    from core import video_probe
    from webui.pages.gallery import window_player
    own = in_app and window_player(req, Path(episode.path), prefer=video_probe.OWN) == video_probe.OWN
    other = ""
    if in_app and req.arg("player") not in (video_probe.BROWSER, video_probe.OWN):
        switch = video_probe.BROWSER if own else video_probe.OWN
        other = (f"/videos/s/{series.id}?ep={episode.id}" + ("&play=1" if play or own else "")
                 + ("&full=1" if full else "") + f"&player={switch}#player")
    next_href = f"/videos/s/{series.id}?ep={after.id}&play=1#player" if after else ""
    now = "" if series.single else f"<b>Серия {esc(episode.label)}</b>"
    note = " · ".join(esc(x) for x in (episode.site_title, episode.note) if x)
    head = (f'<div class="vnow">{now}' + (f' <span class="vnote-ep">{note}</span>' if note else "")
            + f' <span class="vfile" title="{esc(episode.path)}">{esc(episode.name)}</span></div>')
    poster = f"/videos/frame/{episode.id}"
    attrs = {"episode": episode.id, "series": series.id, "from": f"{resume:.1f}", "resume": f"{resume:.1f}",
             "next": next_href, "autoplay": "1" if play else "0", "loop": "0"}
    if length > 0:
        attrs["length"] = f"{length:.1f}"            # where the play will start, shown on the bar before it starts
    if full:
        attrs["full"] = "1"
    skip_buttons = ""
    if not series.single:                             # the opening's and the ending's places (core/video_skips)
        known = _skips(series, episode)
        if known:
            attrs["skips"] = json.dumps(known, separators=(",", ":"))
            skip_buttons = ('<button type="button" class="nv-skip nv-skip-opening" data-skip="opening" hidden>'
                            'Пропустить опенинг</button>'
                            '<button type="button" class="nv-skip nv-skip-ending" data-skip="ending" hidden>'
                            'Пропустить эндинг</button>')
    # frames of the episode one after another over its poster until it is played (videos.js); in a browser they
    # cover its <video> with a button of their own — its buttons are under them till then
    slides = ""
    if not play:
        if own:
            slides = f'<div class="vslides" data-episode="{episode.id}" data-count="{V.SLIDES}" aria-hidden="true"></div>'
        else:
            slides = (f'<div class="vslides vcover" data-episode="{episode.id}" data-count="{V.SLIDES}" '
                      f'data-action="vid-play"><button type="button" class="nv-big" data-action="vid-play" '
                      f'aria-label="Смотреть">▶</button></div>')
    if own:
        from webui.pages.gallery import native_video_box
        if other:
            attrs["other"] = other
        title = series.title if series.single else f"{series.title} · Серия {episode.label}"
        go_on = (f'<a class="nv-next" href="{esc(next_href)}" title="Следующая серия" aria-label="Следующая серия">'
                 f'⏭</a>' if next_href else "")
        viewer = native_video_box(req, attrs, poster, frame_class="vframe", full=f'data-episode="{episode.id}"',
                                  external=f'<button type="button" data-action="vid-open" data-id="{episode.id}">'
                                           f'Открыть в видеоплеере</button>', overlay=slides + skip_buttons,
                                  title=title, bar=go_on)
    else:
        if other:
            attrs["other"] = other
            info = video_probe.probe(episode.path)
            if info is not None and info.video:
                attrs["picture"] = "1"           # a picture that never shows is the browser's failure too
        data_attrs = "".join(f' data-{k}="{esc(v)}"' for k, v in attrs.items())
        viewer = (f'<div class="vscreen"><video class="vvideo" src="/videos/file/{episode.id}" controls preload="metadata" '
                  f'poster="{poster}"{" autoplay" if play else ""}{data_attrs}>{_subtitle_tracks(series, episode)}'
                  f'</video>{slides}{skip_buttons}</div>'
                  f'<div class="video-fallback" hidden><p>Этот файл браузер показать не может (формат не поддерживается).'
                  f'</p><button type="button" data-action="vid-open" data-id="{episode.id}">Открыть в видеоплеере</button></div>')
    links = []
    if before:
        links.append(f'<a class="vbutton" rel="prev" data-key="ArrowLeft" '
                     f'href="{esc(f"/videos/s/{series.id}?ep={before.id}&play=1#player")}">‹ Предыдущая</a>')
    if after:
        links.append(f'<a class="vbutton" rel="next" data-key="ArrowRight" href="{esc(next_href)}">Следующая ›</a>')
    from webui import prefs
    on = str(prefs.load().get("video_autonext") or "on") == "on"
    auto = ("" if series.single or not after else
            f'<label class="vauto"><input type="checkbox" data-autonext{" checked" if on else ""}> '
            f'следующая серия сама</label>')
    tools = (f'<button type="button" class="vbutton" data-action="vid-open" data-id="{episode.id}">Открыть в видеоплеере'
             f'</button>')
    return (f'<section class="vplayer">{head}<div class="vstage">{viewer}</div>'
            f'<nav class="vep-nav" aria-label="Серии">{"".join(links)}{auto}<span class="vspacer"></span>{tools}'
            f'<span class="action-status" role="status"></span></nav></section>')


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


def _dubs_row(req: Request, series: V.Series, episode: V.Episode) -> str:
    """«Озвучка: …» under the episodes — the voice-overs of an episode's file (its sound tracks: the MKV the voice-overs
    were put together in), the one chosen for the series marked; videos.js asks the program's player to switch, or
    keeps the choice for the play to come.  Only in the program's window (a browser's player does not switch the
    tracks of a file) and only when there are two or more."""
    if "QtWebEngine" not in req.headers.get("User-Agent", "") or not Path(episode.path).is_file():
        return ""
    try:
        from core.video_merge import tracks_in
        titles = [t.strip() for t in tracks_in(Path(episode.path))]
    except ImportError:
        return ""
    if len(titles) < 2:
        return ""
    from core import video_tracks
    titles = [t or f"Дорожка {n + 1}" for n, t in enumerate(titles)]
    chosen = video_tracks.choose([{"name": t, "title": t, "lang": ""} for t in titles],
                                 V.PROGRESS.tracks(series.id).get("audio"))
    now = titles[chosen if chosen is not None else 0]
    options = "".join(f'<button type="button" role="option" class="vdub" data-title="{esc(t)}" '
                      f'aria-selected="{"true" if t == now else "false"}">{esc(t)}</button>' for t in titles)
    return (f'<div class="vdubs" data-episode="{episode.id}"><span class="vdubs-label">Озвучка</span>'
            f'<div class="vdubs-pick"><button type="button" class="vdubs-now" aria-haspopup="listbox" '
            f'aria-expanded="false"><span class="vdubs-name">{esc(now)}</span>'
            f'<span class="vdubs-count">{len(titles)}</span></button>'
            f'<div class="vdubs-list" role="listbox" aria-label="Озвучки серии" hidden>{options}</div></div></div>')


def _episodes(series: V.Series, current: V.Episode, done: dict) -> str:
    if series.single:
        return ""
    groups = [(name, eps) for name, eps in series.groups if eps]
    tabs = ""
    if len(groups) > 1:
        tabs = '<div class="vgroups" role="tablist" aria-label="Озвучки и сезоны">' + "".join(
            f'<button type="button" role="tab" data-action="vid-group" data-group="{n}" '
            f'aria-selected="{"true" if any(e.id == current.id for e in eps) else "false"}">'
            f'{esc(name or "Серии")} <span class="muted">{len(eps)}</span></button>'
            for n, (name, eps) in enumerate(groups)) + "</div>"
    lists = []
    for n, (name, eps) in enumerate(groups):
        mine = any(e.id == current.id for e in eps)
        buttons = []
        for e in eps:
            state = done.get(e.id, {})
            cls = "vep" + (" current" if e.id == current.id else "") + (" watched" if state.get("done") else "")
            part = ""
            if not state.get("done") and state.get("dur") and state.get("pos"):
                part = f'<i class="vep-bar" style="--p:{min(100, 100 * float(state["pos"]) / float(state["dur"])):.0f}%"></i>'
            title = " · ".join(x for x in (e.site_title, e.note, e.name) if x)
            current_attr = ' aria-current="true"' if e.id == current.id else ""
            buttons.append(f'<a class="{cls}" href="{esc(f"/videos/s/{series.id}?ep={e.id}&play=1#player")}" '
                           f'title="{esc(title)}"{current_attr}>{esc(e.label)}{part}</a>')
        hidden = "" if mine or len(groups) == 1 else " hidden"
        lists.append(f'<div class="vep-list" data-group="{n}" role="tabpanel"{hidden}>{"".join(buttons)}</div>')
    return (f'<section class="vepisodes"><h2>Серии <span class="muted">{len(series.episodes)}</span></h2>{tabs}'
            f'{"".join(lists)}</section>')


def _meta_box(req: Request, series: V.Series) -> str:
    q = quote(series.folder_title if not series.info.get("title") else str(series.info["title"]))
    links = " · ".join(f'<a href="{esc(tpl.format(q=q))}" target="_blank" rel="noopener noreferrer">на {esc(name)}</a>'
                       for name, tpl in SEARCH_SITES)
    hidden = " hidden" if series.info else ""
    return f"""<section class="vmeta" id="vid-meta" data-series="{series.id}"{hidden}>
  <h2>Описание с сайта</h2>
  <form class="vmeta-url" data-form="vid-meta-url">
    <label for="vid-meta-url">Ссылка на страницу тайтла</label>
    <div class="vmeta-row"><input id="vid-meta-url" type="text" inputmode="url" name="url" spellcheck="false" autocomplete="off"
      value="{esc(series.info.get('source_url') or '')}" placeholder="https://hentasis1.top/… или https://old.yummyani.me/catalog/item/…">
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


def _subtitle_tracks(series: V.Series, episode: V.Episode) -> str:
    """The subtitle files of an episode for a browser's ``<video>``; the one chosen for the series before is on."""
    from core import video_tracks
    try:
        files = video_tracks.subtitle_files(episode.path, top=_top(series))
    except OSError as exc:
        _LOG.info("subtitle files of %s not looked at: %s", episode.path, exc)
        return ""
    listed = [{"id": f"x{n}", "name": f.name, "title": f.name, "lang": "", "file": True} for n, f in enumerate(files)]
    _voice, chosen = video_tracks.preferred(V.PROGRESS.tracks(series.id), [], listed)
    return "".join(f'<track kind="subtitles" src="/videos/subs/{episode.id}?n={n}" label="{esc(f.name)}"'
                   f'{" default" if chosen == f"x{n}" else ""}>' for n, f in enumerate(files))


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
    audio = str(body.get("audio") or "").strip()
    if audio:                                     # «Озвучка» under the episodes, chosen before the play
        V.PROGRESS.set_tracks(series.id, "audio", {"name": "", "title": audio[:120], "lang": ""})
        return Response.json({"ok": True})
    value = str(body.get("value") or "")
    if value == "-1":
        V.PROGRESS.set_tracks(series.id, "subtitle", {"off": True})
        return Response.json({"ok": True})
    files = video_tracks.subtitle_files(episode.path, top=_top(series)) if re.fullmatch(r"x[0-9]{1,3}", value) else []
    if not files or int(value[1:]) >= len(files):
        return Response.json({"ok": False, "error": "Таких субтитров нет"}, 400)
    name = files[int(value[1:])].name
    V.PROGRESS.set_tracks(series.id, "subtitle", {"name": name, "title": name, "lang": "", "file": True})
    return Response.json({"ok": True})


def episode_file(req: Request) -> Response:
    _series_of, episode = _episode(req)
    if episode is None or not Path(episode.path).is_file():
        return Response.not_found("Такой серии нет")
    ctype = mimetypes.guess_type(episode.name)[0] or "application/octet-stream"
    return Response(200, content_type=ctype, file=Path(episode.path), headers={"Cache-Control": "no-cache"})


# ── the API ────────────────────────────────────────────────────────────────────
def progress(req: Request) -> Response:
    """Where an episode stopped (the page says it every few seconds and at the end)."""
    body = req.json()
    root = _root(req)
    series, episode = V.LIBRARY.episode(root, str(body.get("episode") or "")) if Path(root).is_dir() else (None, None)
    if episode is None:
        return Response.json({"ok": False, "error": "Такой серии нет"}, 404)
    try:
        position = max(0.0, float(body.get("position") or 0))
        duration = max(0.0, float(body.get("duration") or 0))
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Неверное время"}, 400)
    entry = V.PROGRESS.record(series.id, episode.id, position, duration, ended=bool(body.get("ended")))
    return Response.json({"ok": True, "done": bool(entry.get("done"))})


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
