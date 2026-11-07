"""(v5.44.68) «Видео» on the owner's phone — the sketch «Local Booru на телефоне» («Видео», «Серия», «Во весь экран»).

* ``/m/videos`` (:func:`index`) — the search, «Продолжить просмотр» (the series watched last: the episode where it
  stopped, how much is left), the series as posters in three columns (the person's mark, the episodes, how many
  watched), the order; «Длинные видео» — the phone's gallery of them (``/m/posts?section=videos``);
* ``/m/videos/s/<id>`` (:func:`series_page`) — (v5.44.121) the one page of a series, the computer's
  (``webui/pages/video_series.py``) in the phone's frame: «Продолжить: серия 6 с 12:40», «Откуда серии» (the folder,
  «Смотреть онлайн»), the episodes with their marks; ``?ep=<number>&play=1`` — its player (the same as the computer's:
  the bar of the page, not the browser's buttons; the previous and next episode, the number, the opening and the ending on the bar);
* ``/m/videos/e/<file id>`` (:func:`player`) — the address of before: the one page's player of that file.

A file the phone's browser plays as it is (an MP4 or a WebM, one sound it plays) is sent as it is
(``/videos/file/<id>``); any other is made into an MP4 for the phone first (``core/phone_video``: seconds — the picture
is copied), its progress shown, then sent from the cache (``/m/videos/file/<id>?a=<voice-over>``; :func:`data_of`,
``/api/phone/video/prepare|state``); the next episode is made while this one plays.  Subtitle files beside the episode
— the computer's ``/videos/subs``; inside the file — ``/m/videos/subs/<id>?s=<n>``.
"""
from __future__ import annotations

from pathlib import Path

from core import video_library as V
from webui.html import esc
from webui.pages import phone, videos as VD
from webui.server import Request, Response

SHELF = 6


def register(router) -> None:
    router.add("GET", r"/m/videos", index)
    router.add("GET", r"/m/videos/s/(?P<id>[0-9a-f]{12})", series_page)
    router.add("GET", r"/m/videos/e/(?P<id>[0-9a-f]{12})", player)
    router.add("GET", r"/m/videos/file/(?P<id>[0-9a-f]{12})", made_file)
    router.add("GET", r"/m/videos/subs/(?P<id>[0-9a-f]{12})", inner_subs)
    router.add("GET", r"/api/phone/video/episode/(?P<id>[0-9a-f]{12})", episode_json)
    router.add("GET", r"/api/phone/video/state", state)
    router.add("POST", r"/api/phone/video/prepare", prepare)


# ── shared bits ──────────────────────────────────────────────────────────────────────────────────────────────────
def _page(title: str, body: str, **frame) -> Response:
    frame.setdefault("active", "videos")
    frame.setdefault("heading", "Видео")
    frame.setdefault("top", phone.mode_switch())
    return phone.page(title, body, **frame)


def _hidden(series: V.Series) -> Response | None:
    from core import content_marks as CM
    from webui import content_mode
    found = CM.series_verdict(series)
    if content_mode.shows(found.hentai):
        return None
    now = content_mode.mode()
    why = ("Включён режим «SFW», а этот сериал — хентай" if now == "sfw"
           else "Включён режим «NSFW» — видно только взрослое, а этот сериал не помечен как хентай")
    body = (f'<section class="m-hello"><h2>Скрыто в режиме «{content_mode.LABELS[now]}»</h2><p>{esc(why)}.</p>'
            f'<p><button type="button" class="m-button" data-mode-set="all">Показать всё</button></p>'
            f'<p><a class="m-link" href="/m/videos">Все видео</a></p></section>')
    return _page("Скрыто", body, back="/m/videos")


def _episode(req: Request, episode_id: str) -> tuple:
    root = VD._root(req)
    return V.LIBRARY.episode(root, episode_id) if Path(root).is_dir() else (None, None)


def _resume(series: V.Series, episode: V.Episode) -> tuple[float, float]:
    """(where to start, the length known) of a file of an episode — its episode's place in the one memory of the
    series; the very start or the end: anew."""
    here = V.PROGRESS.episode_state(series, episode)
    resume = float(here.get("pos") or 0) if not here.get("done") else 0.0
    length = float(here.get("dur") or 0)
    return V.resume_from(resume, length), length


def _label(series: V.Series, episode: V.Episode) -> str:
    unit = series.unit_of(episode)
    return series.title if series.single else f"Серия {unit.label if unit is not None else episode.label}"


# ── /m/videos ────────────────────────────────────────────────────────────────────────────────────────────────────
def _card(series: V.Series) -> str:
    counts = VD.series_counts(series)               # (v5.44.121) episodes, not files; the site's too when known
    total, watched = counts["total"], min(counts["watched"], counts["total"])
    badge = (f'<span class="m-badge m-badge-top">★ {series.rating}</span>' if series.rating else "")
    sub = (f"{total} {VD._plural(total)}" + (f" · в папке {counts['disk']}" if counts["disk"] < total else "")
           if not series.single else str(series.info.get("year") or "фильм"))
    bar = (f'<span class="m-bar" aria-hidden="true"><span style="width:{100 * watched / max(1, total):.0f}%">'
           f'</span></span>' if watched else "")
    return (f'<a class="m-card" href="/m/videos/s/{series.id}"><span class="m-cover">'
            f'<img src="/videos/cover/{series.id}?v={esc(VD._stamp(series))}" alt="" loading="lazy" decoding="async">'
            f'{badge}</span>{bar}<span class="m-card-title">{esc(series.title)}</span>'
            f'<span class="m-card-sub">{esc(sub)}</span></a>')


def _going_on(tile: dict) -> str:
    """A card of «Продолжить просмотр»: the episode to go on with (the frame, the time, what is left, from where)."""
    picture = f'<img src="{esc(tile["picture"])}" alt="" loading="lazy" decoding="async">' if tile["picture"] else ""
    bar = (f'<span class="m-bar m-bar-thin"><span style="width:{int(tile["part"])}%"></span></span>'
           if tile["part"] else "")
    sub = " · ".join(x for x in (tile["left"], tile["from"]) if x)
    return (f'<a class="m-going" href="{esc(tile["href"])}"><span class="m-going-pic">{picture}'
            f'{phone.svg(phone.PLAY, size=28, width=1.6, cls="m-going-play", fill="currentColor")}'
            + (f'<span class="m-going-time">{esc(tile["clock"])}</span>' if tile["clock"] else "") + f'{bar}</span>'
            f'<span class="m-going-text"><span class="m-card-title">{esc(tile["title"])}</span>'
            f'<span class="m-going-what">{esc(tile["words"])}</span>'
            f'<span class="m-card-sub">{esc(sub)}</span></span></a>')


def _search(query: str, sort: str) -> str:
    keep = f'<input type="hidden" name="sort" value="{esc(sort)}">' if sort != "newest" else ""
    return (f'<form class="m-search" action="/m/videos" method="get" role="search">{keep}'
            f'<label class="m-field">{phone.svg(phone.SEARCH, size=18, width=2)}<span class="m-sr">Найти видео</span>'
            f'<input type="search" name="q" value="{esc(query)}" placeholder="Название, жанр, студия" autocomplete="off" '
            f'autocapitalize="off" spellcheck="false" enterkeyhint="search"></label></form>')


def _sort_select(sort: str, query: str) -> str:
    options = "".join(f'<option value="{k}"{" selected" if k == sort else ""}>{esc(v)}</option>'
                      for k, v in VD.SORTS.items())
    hidden = f'<input type="hidden" name="q" value="{esc(query)}">' if query else ""
    return (f'<form class="m-order" action="/m/videos" method="get">{hidden}<span class="m-sr">Порядок</span>'
            f'<select name="sort" data-submit aria-label="Порядок">{options}</select>'
            f'{phone.svg(phone.DOWN, size=14, width=2)}</form>')


def index(req: Request) -> Response:
    from webui import content_mode
    every = VD._library(req, refresh=bool(req.arg("refresh")))
    items = VD._visible(every)
    query = req.arg("q").strip()
    sort = req.arg("sort", "newest")
    sort = sort if sort in VD.SORTS else "newest"
    found = VD._sorted([s for s in items if VD._matches(s, query)] if query else list(items), sort)
    from webui.pages import catalog                 # (v5.44.105) «Сериалы · Лента · Длинные видео»
    from webui.pages import video_series
    parts = [_search(query, sort), catalog.phone_chips("video", "all")]
    if not query:                                   # (v5.44.121) the series of the sites watched online too
        going = video_series.going(every, items, limit=SHELF, phone=True)
        if going:
            parts.append('<section class="m-shelf" aria-labelledby="m-go-on"><h2 id="m-go-on">Продолжить просмотр</h2>'
                         '<div class="m-going-list">' + "".join(_going_on(t) for t in going) + "</div></section>")
    if found:
        parts.append(f'<div class="m-count"><h2 class="m-count-title">{"Найдено" if query else "Сериалы"} '
                     f'<span>{len(found)}</span></h2>{_sort_select(sort, query)}</div>'
                     f'<div class="m-cards">{"".join(_card(s) for s in found)}</div>')
    elif query:
        parts.append(f'<div class="m-empty"><h2>Ничего не нашлось</h2><p>По запросу «{esc(query)}» видео нет.</p></div>')
    elif every:
        now = content_mode.mode()
        why = "в папке только хентай" if now == "sfw" else "хентая в папке нет"
        parts.append(f'<div class="m-empty"><h2>Здесь пусто</h2><p>В режиме «{content_mode.LABELS[now]}» {why}.</p>'
                     f'<p><button type="button" class="m-button" data-mode-set="all">Показать всё</button></p></div>')
    else:
        parts.append('<div class="m-empty"><h2>Видео пока нет</h2><p>Сериалы — папки с сериями в папке видео. '
                     'Скачайте серии по ссылке на тайтл.</p>'
                     '<p><a class="m-button" href="/m/get">Скачать по ссылке</a></p></div>')
    return _page(query or "Видео", "".join(parts), top=phone.get_link() + phone.mode_switch())


# ── /m/videos/s/<id> — (v5.44.121) the one page of a series and its player (``video_series``) ─────────────────────
def series_page(req: Request) -> Response:
    series = VD._series(req)
    if series is None:
        body = ('<div class="m-empty"><h2>Такого видео нет</h2><p>Возможно, папку переименовали или убрали.</p>'
                '<p><a class="m-link" href="/m/videos?refresh=1">Перечитать папку видео</a></p></div>')
        return _page("Нет такого видео", body, status=404, back="/m/videos")
    hidden = _hidden(series)
    if hidden is not None:
        return hidden
    if not series.episodes:
        return _page(series.title, f'<div class="m-empty"><h2>{esc(series.title)}</h2><p>Серий нет.</p></div>',
                     back="/m/videos")
    from webui.pages import video_series
    return video_series.render(req, video_series.resolve(req, series.id), phone=True)


# ── /m/videos/e/<id> ─────────────────────────────────────────────────────────────────────────────────────────────
def data_of(req: Request, series: V.Series, episode: V.Episode, audio: int | None = None) -> dict:
    """What the phone's player needs of a file of an episode (the one page's player asks it for the file it plays):
    the file as it is, or made for the phone (``core/phone_video``), its voice-overs, subtitles, opening and ending."""
    from core import phone_video as PV, video_tracks
    before, after = series.neighbours(episode)
    resume, length = _resume(series, episode)
    out = {"episode": episode.id, "series": series.id, "title": series.title, "label": _label(series, episode),
           "note": " · ".join(x for x in (episode.site_title, episode.note) if x), "direct": False, "src": "",
           "audio": [], "chosen": 0, "subs": [], "sub": "", "skips": {}, "resume": round(resume, 1),
           "duration": round(length, 1), "poster": f"/videos/frame/{episode.id}", "error": "",
           "href": f"/m/videos/e/{episode.id}",
           "prev": {"episode": before.id, "label": _label(series, before)} if before else None,
           "next": {"episode": after.id, "label": _label(series, after)} if after else None}
    if not series.single:
        out["skips"] = VD._skips(series, episode)
    prefer = V.PROGRESS.view(V.memory_keys(series)).get("tracks") or {}
    try:
        files = video_tracks.subtitle_files(episode.path, top=VD._top(series))
    except OSError:
        files = []
    for n, f in enumerate(files):
        out["subs"].append({"key": f"x{n}", "label": f.name, "src": f"/videos/subs/{episode.id}?n={n}"})
    if not PV.available():
        out["error"] = "В программе нет PyAV — видео для телефона не готовится"
        out.update(direct=True, src=f"/videos/file/{episode.id}")
        return out
    try:
        media = PV.inspect(episode.path)
    except PV.VideoError as exc:
        out["error"] = str(exc)
        return out
    titles = media.track_titles()
    out["audio"] = [{"index": n, "title": t} for n, t in enumerate(titles)]
    chosen = audio if audio is not None and 0 <= audio < len(titles) else None
    if chosen is None and titles:
        chosen = video_tracks.choose([{"name": t, "title": t, "lang": ""} for t in titles], prefer.get("audio")) or 0
    out["chosen"] = int(chosen or 0)
    for track in media.subtitles:
        label = video_tracks.track_name(track.title, track.language, track.index + 1)
        out["subs"].append({"key": f"s{track.index}", "label": label,
                            "src": f"/m/videos/subs/{episode.id}?s={track.index}"})
    sub = prefer.get("subtitle") or {}
    if sub.get("file") and not sub.get("off"):
        out["sub"] = next((s["key"] for s in out["subs"] if s["label"] == sub.get("title")), "")
    if media.duration and not out["duration"]:
        out["duration"] = round(media.duration, 1)
    if media.direct:
        out.update(direct=True, src=f"/videos/file/{episode.id}")
    elif PV.ready(episode.path, out["chosen"]) is not None:
        out["src"] = f"/m/videos/file/{episode.id}?a={out['chosen']}"
    return out


def player(req: Request) -> Response:
    """``/m/videos/e/<file>`` (v5.44.68–5.44.120): (v5.44.121) the one page's player of its episode, that file."""
    series, episode = _episode(req, req.params["id"])
    if series is None or episode is None:
        return Response.redirect("/m/videos")
    return Response.redirect(f"/m/videos/s/{series.id}?ep={episode.id}&play=1")


def episode_json(req: Request) -> Response:
    series, episode = _episode(req, req.params["id"])
    if series is None or episode is None:
        return Response.json({"ok": False, "error": "Такой серии нет"}, 404)
    from core import content_marks as CM
    from webui import content_mode
    if not content_mode.shows(CM.series_verdict(series).hentai):
        return Response.json({"ok": False, "error": "Скрыто режимом"}, 403)
    return Response.json({"ok": True, **data_of(req, series, episode)})


def _audio_arg(value) -> int:
    try:
        return max(0, min(64, int(value)))
    except (TypeError, ValueError):
        return 0


def _state_of(episode: V.Episode, audio: int) -> dict:
    from core import phone_video as PV
    found = PV.JOBS.state(episode.path, audio)
    out = {"ok": True, "state": found["state"], "part": found.get("part", 0.0), "error": found.get("error", "")}
    if found["state"] == "ready":
        out["src"] = f"/m/videos/file/{episode.id}?a={audio}"
    return out


def state(req: Request) -> Response:
    _series, episode = _episode(req, req.arg("episode"))
    if episode is None:
        return Response.json({"ok": False, "error": "Такой серии нет"}, 404)
    return Response.json(_state_of(episode, _audio_arg(req.arg("audio"))))


def prepare(req: Request) -> Response:
    """{episode, audio?}: make the episode for the phone (the voice-over chosen for the series when not said)."""
    from core import phone_video as PV
    body = req.json()
    series, episode = _episode(req, str(body.get("episode") or ""))
    if episode is None:
        return Response.json({"ok": False, "error": "Такой серии нет"}, 404)
    if not PV.available():
        return Response.json({"ok": False, "error": "В программе нет PyAV"}, 409)
    if body.get("audio") is None:
        data = data_of(req, series, episode)
        if data["direct"] or data["error"]:
            return Response.json({"ok": not data["error"], "state": "direct" if data["direct"] else "failed",
                                  "src": data["src"], "error": data["error"]})
        audio = data["chosen"]
    else:
        audio = _audio_arg(body.get("audio"))
    try:
        media = PV.inspect(episode.path)
    except PV.VideoError as exc:
        return Response.json({"ok": False, "state": "failed", "error": str(exc)}, 409)
    if media.direct:
        return Response.json({"ok": True, "state": "direct", "src": f"/videos/file/{episode.id}"})
    PV.JOBS.ask(episode.path, audio)
    return Response.json(_state_of(episode, audio))


def made_file(req: Request) -> Response:
    from core import phone_video as PV
    _series, episode = _episode(req, req.params["id"])
    if episode is None:
        return Response.not_found("Такой серии нет")
    made = PV.ready(episode.path, _audio_arg(req.arg("a")))
    if made is None:
        return Response.not_found("Серия для телефона ещё не готова")
    return Response(200, content_type="video/mp4", file=made, headers={"Cache-Control": "private, no-cache"})


def inner_subs(req: Request) -> Response:
    from core import phone_video as PV
    _series, episode = _episode(req, req.params["id"])
    number = req.arg("s")
    if episode is None or not number.isdigit():
        return Response.not_found("Таких субтитров нет")
    try:
        text = PV.subtitles_vtt(episode.path, int(number))
    except PV.VideoError as exc:
        return Response(404, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
    return Response(200, text.encode("utf-8"), "text/vtt; charset=utf-8", {"Cache-Control": "no-cache"})
