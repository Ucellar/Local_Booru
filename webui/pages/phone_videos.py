"""(v5.44.68) «Видео» on the owner's phone — the sketch «Local Booru на телефоне» («Видео», «Серия», «Во весь экран»).

* ``/m/videos`` (:func:`index`) — the search, «Продолжить просмотр» (the series watched last: the episode where it
  stopped, how much is left), the series as posters in three columns (the person's mark, the episodes, how many
  watched), the order; «Длинные видео» — the phone's gallery of them (``/m/posts?section=videos``);
* ``/m/videos/s/<id>`` (:func:`series_page`) — the poster, the names, the facts, the description, «Смотреть» /
  «Продолжить: серия 6 с 12:40», the episodes (their groups as tabs, the watched ones ticked);
* ``/m/videos/e/<episode id>`` (:func:`player`) — the episode: the phone's own player (its controls) with «Пропустить
  опенинг / эндинг», the opening and the ending marked on a line under it, «Озвучка» and «Субтитры», the episodes
  before and after, «Следующая серия сама» (the next one comes into the same player: the phone lets it play on), the
  whole screen turned sideways; where it stopped — ``/api/videos/progress`` (the computer's), the voice-over chosen —
  ``/api/videos/tracks`` (the computer's: one choice for the series on both).

A file the phone's browser plays as it is (an MP4 or a WebM, one sound it plays) is sent as it is
(``/videos/file/<id>``); any other is made into an MP4 for the phone first (``core/phone_video``: seconds — the picture
is copied), its progress shown, then sent from the cache (``/m/videos/file/<id>?a=<voice-over>``); the next episode is
made while this one plays.  Subtitle files beside the episode — the computer's ``/videos/subs``; inside the file —
``/m/videos/subs/<id>?s=<n>``.
"""
from __future__ import annotations

import json
from pathlib import Path

from core import video_library as V
from core.video_probe import human_duration
from webui.html import esc, url
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


def _resume(episode: V.Episode) -> tuple[float, float]:
    """(where to start, the length known) — the very start or the end: anew."""
    here = V.PROGRESS.episode(episode.id)
    resume = float(here.get("pos") or 0) if not here.get("done") else 0.0
    length = float(here.get("dur") or 0)
    if resume < 5 or (length and resume > length - min(10.0, length * 0.1)):
        resume = 0.0
    return resume, length


def _label(series: V.Series, episode: V.Episode) -> str:
    return series.title if series.single else f"Серия {episode.label}"


# ── /m/videos ────────────────────────────────────────────────────────────────────────────────────────────────────
def _card(series: V.Series) -> str:
    done = V.PROGRESS.episodes([e.id for e in series.episodes])
    watched = sum(1 for e in series.episodes if done.get(e.id, {}).get("done"))
    badge = (f'<span class="m-badge m-badge-top">★ {series.rating}</span>' if series.rating else "")
    sub = (f"{len(series.episodes)} {VD._plural(len(series.episodes))}" if not series.single
           else str(series.info.get("year") or "фильм"))
    bar = (f'<span class="m-bar" aria-hidden="true"><span style="width:{100 * watched / max(1, len(series.episodes)):.0f}%">'
           f'</span></span>' if watched else "")
    return (f'<a class="m-card" href="/m/videos/s/{series.id}"><span class="m-cover">'
            f'<img src="/videos/cover/{series.id}?v={esc(VD._stamp(series))}" alt="" loading="lazy" decoding="async">'
            f'{badge}</span>{bar}<span class="m-card-title">{esc(series.title)}</span>'
            f'<span class="m-card-sub">{esc(sub)}</span></a>')


def _going_on(series: V.Series) -> str:
    """A card of «Продолжить просмотр»: the episode stopped at (the frame, the time, what is left)."""
    episode = series.episode(V.PROGRESS.last(series.id)) or series.episodes[0]
    here = V.PROGRESS.episode(episode.id)
    if here.get("done"):
        _before, after = series.neighbours(episode)
        if after is not None:
            episode, here = after, V.PROGRESS.episode(after.id)
    pos, length = float(here.get("pos") or 0), float(here.get("dur") or 0)
    time_line = f"{human_duration(pos) or '0:00'} / {human_duration(length)}" if length else ""
    left = f" · осталось {max(1, round((length - pos) / 60))} мин" if length and pos else ""
    bar = (f'<span class="m-bar m-bar-thin"><span style="width:{100 * pos / length:.0f}%"></span></span>'
           if length and pos else "")
    return (f'<a class="m-going" href="/m/videos/e/{episode.id}"><span class="m-going-pic">'
            f'<img src="/videos/frame/{episode.id}" alt="" loading="lazy" decoding="async">'
            f'{phone.svg(phone.PLAY, size=28, width=1.6, cls="m-going-play", fill="currentColor")}'
            + (f'<span class="m-going-time">{esc(time_line)}</span>' if time_line else "") + f'{bar}</span>'
            f'<span class="m-going-text"><span class="m-card-title">{esc(series.title)}</span>'
            f'<span class="m-card-sub">{esc(_label(series, episode) if not series.single else "Фильм")}{esc(left)}'
            f'</span></span></a>')


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
    parts = [_search(query, sort), catalog.phone_chips("video", "all")]
    if not query and found:
        going = sorted((s for s in items if V.PROGRESS.last_at(s.id) and s.episodes),
                       key=lambda s: -V.PROGRESS.last_at(s.id))
        if going:
            parts.append('<section class="m-shelf" aria-labelledby="m-go-on"><h2 id="m-go-on">Продолжить просмотр</h2>'
                         '<div class="m-going-list">' + "".join(_going_on(s) for s in going[:SHELF]) + "</div></section>")
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


# ── /m/videos/s/<id> ─────────────────────────────────────────────────────────────────────────────────────────────
def _episodes(series: V.Series, current_id: str = "") -> str:
    if series.single:
        return ""
    done = V.PROGRESS.episodes([e.id for e in series.episodes])
    watched = sum(1 for e in series.episodes if done.get(e.id, {}).get("done"))
    groups = [(name, eps) for name, eps in series.groups if eps] or [("", series.episodes)]
    tabs = ""
    if len(groups) > 1:
        tabs = ('<div class="m-tabs-line m-ep-groups" role="tablist" aria-label="Озвучки и сезоны">' + "".join(
            f'<button type="button" role="tab" data-ep-group="{n}" aria-selected="'
            f'{"true" if any(e.id == current_id for e in eps) or (not current_id and n == 0) else "false"}">'
            f'{esc(name or "Серии")} <span>{len(eps)}</span></button>' for n, (name, eps) in enumerate(groups)) + "</div>")
    lists = []
    for n, (_name, eps) in enumerate(groups):
        mine = any(e.id == current_id for e in eps) or (not current_id and n == 0) or len(groups) == 1
        buttons = []
        for e in eps:
            state = done.get(e.id, {})
            cls = "m-ep" + (" current" if e.id == current_id else "") + (" watched" if state.get("done") else "")
            part = ""
            if not state.get("done") and state.get("dur") and state.get("pos"):
                part = (f'<i class="m-ep-bar" style="width:{min(100, 100 * float(state["pos"]) / float(state["dur"])):.0f}%">'
                        f'</i>')
            mark = phone.svg(phone.TICK, size=12, width=3, cls="m-ep-tick") if state.get("done") else ""
            current = ' aria-current="true"' if e.id == current_id else ""
            buttons.append(f'<a class="{cls}" href="/m/videos/e/{e.id}" data-episode="{e.id}" '
                           f'aria-label="Серия {esc(e.label)}"{current}>{esc(e.label)}{mark}{part}</a>')
        lists.append(f'<div class="m-ep-grid" data-ep-list="{n}"{"" if mine else " hidden"}>{"".join(buttons)}</div>')
    return (f'<section class="m-episodes" aria-labelledby="m-eps"><div class="m-count"><h2 class="m-count-title" '
            f'id="m-eps">Серии <span>{len(series.episodes)}</span></h2><span class="m-muted">просмотрено {watched} из '
            f'{len(series.episodes)}</span></div>{tabs}{"".join(lists)}</section>')


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
    episode = series.episode(V.PROGRESS.last(series.id)) or series.episodes[0]
    if V.PROGRESS.episode(episode.id).get("done"):
        _before, after = series.neighbours(episode)
        episode = after or episode
    resume, _length = _resume(episode)
    watch = ("Смотреть" if not resume else f"Продолжить с {human_duration(resume)}")
    if not series.single:
        watch += f" · серия {episode.label}"
    info = series.info
    chips = "".join(f"<span>{esc(str(info.get(key))[:24])}</span>" for key in ("year", "type", "status")
                    if info.get(key) and not isinstance(info.get(key), list))
    if series.rating:
        chips += f"<span>★ {series.rating}</span>"
    alt = " / ".join(esc(t) for t in series.alt_titles[:3])
    hero = (f'<section class="m-hero"><a class="m-cover m-hero-cover" href="/m/videos/e/{episode.id}">'
            f'<img src="/videos/cover/{series.id}?size=l&amp;v={esc(VD._stamp(series))}" alt=""></a>'
            f'<div class="m-hero-about"><h1>{esc(series.title)}</h1>' + (f'<p class="m-alt">{alt}</p>' if alt else "")
            + (f'<p class="m-facts">{chips}</p>' if chips else "") + "</div></section>")
    go = (f'<div class="m-go"><a class="m-button m-go-main" href="/m/videos/e/{episode.id}">'
          f'{phone.svg(phone.PLAY, size=18, width=2)}<span>{esc(watch)}</span></a></div>')
    text = str(info.get("description") or "").strip()
    desc = ""
    if text:
        paragraphs = "".join(f"<p>{esc(p)}</p>" for p in text.split("\n\n") if p.strip())
        desc = (f'<details class="m-more-text"><summary>Описание</summary><div class="m-desc">{paragraphs}</div>'
                f'</details>')
    genres = info.get("genres") if isinstance(info.get("genres"), list) else []
    tags = "".join(f'<a class="m-tag" href="{esc(url("/m/videos", q=str(g)))}">{esc(g)}</a>' for g in genres[:20])
    body = (hero + go + (f'<div class="m-tag-list m-pad">{tags}</div>' if tags else "") + desc
            + _episodes(series, episode.id if not series.single else ""))
    return _page(series.title, body, back="/m/videos", heading="Видео")


# ── /m/videos/e/<id> ─────────────────────────────────────────────────────────────────────────────────────────────
def data_of(req: Request, series: V.Series, episode: V.Episode, audio: int | None = None) -> dict:
    """What the player needs of an episode (the page's JSON, and the next episode coming into the same player)."""
    from core import phone_video as PV, video_tracks
    before, after = series.neighbours(episode)
    resume, length = _resume(episode)
    out = {"episode": episode.id, "series": series.id, "title": series.title, "label": _label(series, episode),
           "note": " · ".join(x for x in (episode.site_title, episode.note) if x), "direct": False, "src": "",
           "audio": [], "chosen": 0, "subs": [], "sub": "", "skips": {}, "resume": round(resume, 1),
           "duration": round(length, 1), "poster": f"/videos/frame/{episode.id}", "error": "",
           "href": f"/m/videos/e/{episode.id}",
           "prev": {"episode": before.id, "label": _label(series, before)} if before else None,
           "next": {"episode": after.id, "label": _label(series, after)} if after else None}
    if not series.single:
        out["skips"] = VD._skips(series, episode)
    prefer = V.PROGRESS.tracks(series.id)
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


def _json_script(data: dict) -> str:
    text = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/json" id="m-vp-data">{text}</script>'


def player(req: Request) -> Response:
    series, episode = _episode(req, req.params["id"])
    if series is None or episode is None:
        return Response.redirect("/m/videos")
    hidden = _hidden(series)
    if hidden is not None:
        return hidden
    data = data_of(req, series, episode)
    from webui import prefs
    autonext = str(prefs.load().get("video_autonext") or "on") == "on"
    nav = []
    for key, words, icon, cls in (("prev", "Предыдущая", phone.BACK, "m-quiet"), ("next", "Следующая", phone.FORTH, "")):
        target = data[key]
        inside = (f'{phone.svg(icon, size=18, width=2)}<span>{words}</span>' if key == "prev"
                  else f'<span>{words}</span>{phone.svg(icon, size=18, width=2)}')
        nav.append(f'<a class="m-button {cls}" data-vp-{key} href="/m/videos/e/{target["episode"]}">{inside}</a>'
                   if target else f'<a class="m-button {cls}" data-vp-{key} aria-disabled="true">{inside}</a>')
    box = (f'<div class="m-vp-box" data-vp-box>'
           f'<video class="m-vp-video" playsinline preload="metadata" controls '
           f'controlslist="nofullscreen nodownload noremoteplayback" poster="{esc(data["poster"])}" data-vp-video></video>'
           f'<div class="m-vp-wait" data-vp-wait hidden><p data-vp-wait-text>Готовлю серию для телефона…</p>'
           f'<span class="m-bar m-bar-always"><span data-vp-wait-bar style="width:0%"></span></span></div>'
           f'<div class="m-vp-problem" data-vp-problem hidden><p data-vp-problem-text></p>'
           f'<a class="m-button m-quiet" data-vp-save href="/videos/file/{episode.id}" download>Сохранить файл на '
           f'телефон</a></div>'
           f'<button type="button" class="m-vp-skip" data-vp-skip hidden></button>'
           f'<button type="button" class="m-vp-full" data-vp-full aria-label="Во весь экран">'
           f'{phone.svg(phone.FULL, size=22, width=2)}</button></div>'
           f'<div class="m-vp-marks" data-vp-marks aria-hidden="true"></div>')
    about = (f'<section class="m-vp-about"><h2 data-vp-label>{esc(data["label"])}</h2>'
             f'<p class="m-muted" data-vp-note>{esc(data["note"])}</p>'
             f'<div class="m-vp-picks"><label class="m-pick" data-vp-audio-box hidden><span>Озвучка</span>'
             f'<select data-vp-audio aria-label="Озвучка"></select></label>'
             f'<label class="m-pick" data-vp-subs-box hidden><span>Субтитры</span>'
             f'<select data-vp-subs aria-label="Субтитры"></select></label></div>'
             + ("" if series.single else
                f'<div class="m-go m-vp-nav">{"".join(nav)}</div>'
                f'<label class="m-check"><input type="checkbox" data-vp-autonext{" checked" if autonext else ""}> '
                f'Следующая серия сама</label>') + "</section>")
    body = (f'<div class="m-vp" data-vp>{box}{about}{_episodes(series, episode.id)}</div>{_json_script(data)}')
    return _page(f"{data['label']} — {series.title}" if not series.single else series.title, body,
                 back=f"/m/videos/s/{series.id}", heading=series.title, top="", tabs=False, kind="m-video")


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
