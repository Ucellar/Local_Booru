"""(v5.44.106) «Смотреть» — the page of a series of a site, watched in the program without downloading it.

The owner (6.11.2026): «Делай просмотр видео»; the look — the mockup «Сериал» of the canvas «Local Booru — Скачать и
Онлайн» he approved (the title and its facts, «Продолжить: серия N», the voice-over, the player and the quality, the
episodes as tiles — in the folder, watched, now — the player, the episodes before and after, «следующая серия сама»).

``/videos/watch?url=`` — a title of YummyAnime in the tab «Видео» («Лента» marked; «← Лента» back to it — a tile of
«Видео → Лента» leads here): ``c`` — the tile's cover (``/api/catalog/cover``), ``r`` — its rating, ``t`` — its name,
shown before the site answers.  ``/m/videos/watch`` — the phone's (its frame, back to its «Лента»).  The title, the
streams and where it stopped — ``core/video_watch``; (v5.44.121) the page is the one page of a series
(``webui/pages/video_series.py``, ``static/series.js``: the folder's episodes and the site's, one player, one memory —
``static/watch.js`` before); these stay for it and for the cards of «По ссылке»:

* ``/api/watch/title?url=`` — the title (waited for :data:`video_watch.LOOK_WAIT`, then «… отвечает» and asked again);
* ``POST /api/watch/open`` {url, dub, ep, player, quality} — the episode's video asked of its player: a token and the
  program's address of it (``/watch/s/<token>/…`` — :data:`webui.server.MEDIA_PATHS`; the window's own player asks it
  without the page's cookie: ``video_watch.known``);
* ``POST /api/watch/progress`` {url, ep, at, length, ended, …} — where it stopped; ``POST /api/watch/choose`` — the
  voice-over, player and quality chosen.

The player: in the program's window — its own (``gallery.native_video_box`` with ``data-stream``; booru.js plays the
token's address: the window's browser has no H.264 and AAC); in a browser and on the phone — ``<video>`` (hls.js for a
stream where the browser does not play HLS itself).  «Скачать серию N» and «Скачать остальные» — the machinery of
«Скачать» (``video_get``: ``/api/videos/find``, ``/api/videos/download`` — the other chat's, only asked); «Скачать…» —
its card («По ссылке»).  The mode: «SFW» — a title YummyAnime calls hentai (the genre, «Rx») is not shown, nor played;
«NSFW» — only such.

(v5.44.109) A title of Hentasis too (``core/video_watch.PAGE_SITES``): its page read the way «Скачать» reads it (through
the window's browser at a robot check — ``videos._window_fetch``); no «Плеер» and «Качество» on the page (one file of an
episode), its voice-overs — the site's.  ``/api/watch/can?url=`` — whether a link is watched here (the card «По ссылке»
offers «Смотреть»).
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlencode

from webui.html import esc, page_frame
from webui.server import Request, Response

BACK = "/videos/feed"
PHONE_BACK = "/m/videos/feed"


def register(router) -> None:
    router.add("GET", r"/videos/watch", page)
    router.add("GET", r"/m/videos/watch", lambda req: page(req, phone=True))
    router.add("GET", r"/api/watch/title", title_json)
    router.add("GET", r"/api/watch/can", can_json)
    router.add("POST", r"/api/watch/open", open_json)
    router.add("POST", r"/api/watch/progress", progress_json)
    router.add("POST", r"/api/watch/choose", choose_json)
    token = r"(?P<token>[A-Za-z0-9_-]{16,64})"
    router.add("GET", rf"/watch/s/{token}/index\.m3u8", playlist)
    router.add("GET", rf"/watch/s/{token}/(?P<which>video|audio)\.m3u8", playlist)   # (v5.44.138) the sound apart
    router.add("GET", rf"/watch/s/{token}/(?P<n>\d{{1,6}})\.(?:ts|m4s)", piece)
    router.add("GET", rf"/watch/s/{token}/a(?P<n>\d{{1,6}})\.(?:ts|m4s|aac)", sound_piece)
    router.add("GET", rf"/watch/s/{token}/init\.mp4", init)
    router.add("GET", rf"/watch/s/{token}/ainit\.mp4", sound_init)
    router.add("GET", rf"/watch/s/{token}/video\.mp4", video_file)


def href(url: str, *, cover: str = "", rating: float = 0.0, name: str = "", phone: bool = False) -> str:
    """The address of the page of the series at ``url`` (a tile of «Лента» gives its cover, rating and name — the page
    shows them at once); ``phone`` — the phone's page."""
    pairs = [("url", url)]
    if cover:
        pairs.append(("c", cover))
    if rating:
        pairs.append(("r", f"{float(rating):.1f}"))
    if name:
        pairs.append(("t", name[:200]))
    return ("/m" if phone else "") + "/videos/watch?" + urlencode(pairs, quote_via=quote)


def _root(req: Request) -> Path:
    from core import video_library as V
    return Path(V.video_root(req.settings))


def _rating(text: str) -> float:
    try:
        value = float(str(text or "").replace(",", "."))
    except ValueError:
        return 0.0
    return value if 0 < value <= 10 else 0.0


def _in_app(req: Request) -> bool:
    return "QtWebEngine" in str(req.headers.get("User-Agent") or req.headers.get("user-agent") or "")


def _frame(req: Request, title: str, body: str, *, phone: bool, status: int = 200) -> Response:
    """A page of its own (a link the program does not show online): the frame of the one page of a series."""
    if phone:
        from webui.pages import phone as P
        return P.page(title, body, status, active="videos", heading="Видео", back=PHONE_BACK, top=P.mode_switch(),
                      kind="m-series-page", head='<link rel="stylesheet" href="/static/series.css">')
    from webui.pages import videos
    return Response.html(page_frame(title, body, active="videos", subnav=False, kind="video-series",
                                    tabs=videos.tabs_of(req, "feed"), top_search=videos._search_form("", "all"),
                                    assets=("videos", "series"), **videos._look()), status=status)


def _hidden_words(phone: bool, mode: str, back: str) -> str:
    """The words of a series the mode hides (its name never said)."""
    from webui import content_mode
    if phone:
        why = ("Включён режим «SFW», а этот сериал — хентай" if mode == "sfw" else
               "Включён режим «NSFW» — видно только взрослое, а этот сериал не помечен как хентай")
        return (f'<section class="m-hello" data-hidden="{"hentai" if mode == "sfw" else "safe"}"><h2>Скрыто в режиме '
                f'«{content_mode.LABELS[mode]}»</h2><p>{esc(why)}.</p><p><button type="button" class="m-button" '
                f'data-mode-set="all">Показать всё</button></p><p><a class="m-link" href="{esc(back)}">К ленте</a></p>'
                f'</section>')
    return content_mode.hidden_item(active="videos", what="этот сериал", reason="", hentai=mode == "sfw", back=back,
                                    back_label="К ленте")


def page(req: Request, phone: bool = False) -> Response:
    """(v5.44.121) The one page of a series (``webui/pages/video_series.py``): a title of a site — its episodes online,
    and the folder's when it has one here (that series' page, the address kept)."""
    from core import video_watch as W
    url = req.arg("url").strip()[:2000]
    back = PHONE_BACK if phone else BACK
    if not W.is_watchable(url):
        words = W.NOT_WATCHABLE + "."
        if phone:
            body = (f'<section class="m-hello"><h2>Не открылось</h2><p>{esc(words)}</p><p><a class="m-button m-quiet" '
                    f'href="{esc(back)}">К ленте</a></p></section>')
        else:
            body = (f'<div class="sv sv-problem"><a class="sv-back" href="{esc(back)}" data-sv-back>← Лента</a>'
                    f'<h1>Не открылось</h1><p>{esc(words)}</p></div>')
        return _frame(req, "Не открылось", body, phone=phone, status=404)
    from webui.pages import video_series
    ref = video_series.resolve(req, url=url)
    if ref is not None and ref.series is not None:
        from core import content_marks as CM
        from webui import content_mode
        found = CM.series_verdict(ref.series)
        if not content_mode.shows(found.hentai):
            if phone:
                from webui.pages.phone_videos import _hidden
                return _hidden(ref.series)
            from webui.pages import videos
            body = content_mode.hidden_item(active="videos", what="этот сериал", reason=found.reason,
                                            hentai=found.hentai, back=back, back_label="К ленте")
            return videos._frame(req, "Скрыто", body, kind="mode-hidden")
    tile = {"name": req.arg("t").strip()[:200], "cover": req.arg("c").strip()[:64], "rating": _rating(req.arg("r"))}
    return video_series.render(req, ref, phone=phone, tile=tile)


# ── the title, the episodes ──────────────────────────────────────────────────────────────────────────────────────
def _refused(title) -> Response | None:
    """The mode's refusal of a series: «SFW» — YummyAnime calls it hentai; «NSFW» — it does not."""
    from webui import content_mode
    now = content_mode.mode()
    if title.hentai and not content_mode.shows(True):
        return Response.json({"ok": False, "hidden": now, "error": "Включён режим «SFW», а этот сериал — хентай"}, 409)
    if not title.hentai and now == "nsfw":
        return Response.json({"ok": False, "hidden": now,
                              "error": "Включён режим «NSFW» — видно только взрослое, а этот сериал не помечен как "
                                       "хентай"}, 409)
    return None


def _window(url: str):
    """The program window's browser for a page title's page that checks for robots (None — not such a title, or no
    window)."""
    from core import video_watch as W
    if not W.is_page(W.key_of(url)):
        return None
    from webui.pages.videos import _window_fetch
    return _window_fetch()


def can_json(req: Request) -> Response:
    """(v5.44.109) Whether the link is a title this page shows — and its address here (the card «По ссылке»)."""
    from core import video_watch as W
    url = req.arg("url").strip()[:2000]
    if not W.is_watchable(url):
        return Response.json({"ok": True, "watch": False})
    return Response.json({"ok": True, "watch": True, "site": W.site_of(url),
                          "href": href(url, phone=req.arg("phone") == "1")})


def title_json(req: Request) -> Response:
    from core import video_watch as W
    url = req.arg("url").strip()[:2000]
    try:
        title, said, error = W.look(url, req.settings, _root(req), again=req.arg("again") == "1", window=_window(url))
    except W.WatchError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 400)
    if title is None:
        return Response.json({"ok": True, "pending": not error, "said": said, "error": error})
    refused = _refused(title)
    if refused is not None:
        return refused
    return Response.json({"ok": True, "title": W.public(title, W.PROGRESS.of(title.key))})


def _kept(req: Request, body: dict):
    """The title the page shows (found and kept), or the answer why not."""
    from core import video_watch as W
    url = str(body.get("url") or "").strip()[:2000]
    if not W.is_watchable(url):
        return None, Response.json({"ok": False, "error": W.NOT_WATCHABLE}, 400)
    title = W.kept(url)
    if title is None:
        title, _said, _error = W.look(url, req.settings, _root(req), wait=0, window=_window(url))
    if title is None:
        return None, Response.json({"ok": False, "error": "Тайтл ещё читается — подождите и нажмите серию снова"}, 409)
    refused = _refused(title)
    return (None, refused) if refused is not None else (title, None)


def open_json(req: Request) -> Response:
    from core import video_watch as W
    body = req.json()
    title, refused = _kept(req, body)
    if refused is not None:
        return refused
    try:
        stream = W.open_stream(title, str(body.get("dub") or ""), str(body.get("ep") or ""),
                               player=str(body.get("player") or ""), quality=str(body.get("quality") or ""))
    except W.WatchError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 502)
    W.PROGRESS.choose(title.key, name=title.name, dub=stream.dub, player=str(body.get("player") or "") or None,
                      quality=str(body.get("quality") or "") or None)
    return Response.json({"ok": True, **W.stream_public(stream, W.PROGRESS.of(title.key))})


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if 0 <= number < 10 ** 7 else default


def progress_json(req: Request) -> Response:
    from core import video_watch as W
    body = req.json()
    url = str(body.get("url") or "").strip()[:2000]
    key = W.key_of(url)
    label = str(body.get("ep") or "").strip()[:12]
    if not key or not label:
        return Response.json({"ok": False, "error": "Нет тайтла или серии"}, 400)
    title = W.kept(url)
    item = W.PROGRESS.record(key, label, _number(body.get("at")), _number(body.get("length")),
                             ended=body.get("ended") is True, name=title.name if title is not None else "",
                             dub=str(body.get("dub") or "")[:200] or None, player=str(body.get("player") or "") or None,
                             quality=str(body.get("quality") or "") or None, ending=_number(body.get("ending")))
    return Response.json({"ok": True, "watched": label in (item.get("watched") or []), "progress": item})


def choose_json(req: Request) -> Response:
    from core import video_dubs as Y
    from core import video_watch as W
    body = req.json()
    key = W.key_of(str(body.get("url") or ""))
    if not key:
        return Response.json({"ok": False, "error": W.NOT_WATCHABLE}, 400)
    quality = body.get("quality")
    if quality is not None and str(quality) not in ("",) + tuple(Y.QUALITIES):
        return Response.json({"ok": False, "error": "Непонятное качество"}, 400)
    player = body.get("player")
    if player is not None and str(player) not in ("", W.PAGE_PLAYER) + tuple(Y.PLAYERS):
        return Response.json({"ok": False, "error": "Непонятный плеер"}, 400)
    W.PROGRESS.choose(key, dub=body.get("dub") if body.get("dub") is not None else None,
                      player=player, quality=quality)
    if body.get("autonext") is not None:
        from webui import prefs
        prefs.save({"video_autonext": "on" if body.get("autonext") else "off"})
    return Response.json({"ok": True})


# ── the stream: the program's own addresses of it ─────────────────────────────────────────────────────────────────
def _failed(exc) -> Response:
    from core import video_watch as W
    return Response(404 if isinstance(exc, W.Gone) else 502, str(exc).encode("utf-8"), "text/plain; charset=utf-8")


def playlist(req: Request) -> Response:
    from core import video_watch as W
    try:
        text = W.playlist(req.params["token"], str(req.params.get("which") or ""))
    except W.WatchError as exc:
        return _failed(exc)
    return Response(200, text.encode("utf-8"), "application/vnd.apple.mpegurl")


def sound_piece(req: Request) -> Response:
    """(v5.44.138) A part of a stream's sound that is a stream of its own."""
    from core import video_watch as W
    try:
        body, kind = W.piece(req.params["token"], int(req.params["n"]), sound=True)
    except W.WatchError as exc:
        return _failed(exc)
    return Response(200, body, kind, {"Cache-Control": "private, max-age=3600"})


def sound_init(req: Request) -> Response:
    from core import video_watch as W
    try:
        body, kind = W.piece(req.params["token"], -1, sound=True)
    except W.WatchError as exc:
        return _failed(exc)
    return Response(200, body, kind, {"Cache-Control": "private, max-age=3600"})


def piece(req: Request) -> Response:
    from core import video_watch as W
    try:
        body, kind = W.piece(req.params["token"], int(req.params["n"]))
    except W.WatchError as exc:
        return _failed(exc)
    return Response(200, body, kind, {"Cache-Control": "private, max-age=3600"})


def init(req: Request) -> Response:
    from core import video_watch as W
    try:
        body, kind = W.piece(req.params["token"], -1)
    except W.WatchError as exc:
        return _failed(exc)
    return Response(200, body, kind, {"Cache-Control": "private, max-age=3600"})


def video_file(req: Request) -> Response:
    from core import video_watch as W
    wanted = next((str(v) for k, v in req.headers.items() if str(k).lower() == "range"), "")
    try:
        status, headers, body = W.file_part(req.params["token"], wanted)
    except W.WatchError as exc:
        return _failed(exc)
    return Response(status, b"", "video/mp4", headers, stream=body)
