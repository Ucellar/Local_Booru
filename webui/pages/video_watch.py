"""(v5.44.106) «Смотреть» — the page of a series of a site, watched in the program without downloading it.

The owner (6.11.2026): «Делай просмотр видео»; the look — the mockup «Сериал» of the canvas «Local Booru — Скачать и
Онлайн» he approved (the title and its facts, «Продолжить: серия N», the voice-over, the player and the quality, the
episodes as tiles — in the folder, watched, now — the player, the episodes before and after, «следующая серия сама»).

``/videos/watch?url=`` — a title of YummyAnime in the tab «Видео» («Лента» marked; «← Лента» back to it — a tile of
«Видео → Лента» leads here): ``c`` — the tile's cover (``/api/catalog/cover``), ``r`` — its rating, ``t`` — its name,
shown before the site answers.  ``/m/videos/watch`` — the phone's (its frame, back to its «Лента»).  The title, the
streams and where it stopped — ``core/video_watch``; this page shows them (``static/watch.js``):

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
SCREEN = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 9'%3E%3Crect width='16' "
          "height='9' fill='%23000'/%3E%3C/svg%3E")         # the window's player before an episode is chosen


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
    router.add("GET", rf"/watch/s/{token}/(?P<n>\d{{1,6}})\.(?:ts|m4s)", piece)
    router.add("GET", rf"/watch/s/{token}/init\.mp4", init)
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
    if phone:
        from webui.pages import phone as P
        return P.page(title, body, status, active="videos", heading="Видео", back=PHONE_BACK, top=P.mode_switch(),
                      scripts=("vendor/hls.light.min", "watch"), kind="m-watch-page",
                      head='<link rel="stylesheet" href="/static/watch.css">')
    from webui.pages import videos
    assets = ("watch",) if _in_app(req) else ("hls", "watch")
    return Response.html(page_frame(title, body, active="videos", subnav=False, kind="watch",
                                    tabs=videos.tabs_of(req, "feed"), top_search=videos._search_form("", "all"),
                                    assets=assets, **videos._look()), status=status)


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
    from core import video_watch as W
    from webui import content_mode
    url = req.arg("url").strip()[:2000]
    back = PHONE_BACK if phone else BACK
    if not W.is_watchable(url):
        words = W.NOT_WATCHABLE + "."
        if phone:
            body = (f'<section class="m-hello"><h2>Не открылось</h2><p>{esc(words)}</p><p><a class="m-button m-quiet" '
                    f'href="{esc(back)}">К ленте</a></p></section>')
        else:
            body = (f'<div class="wt wt-problem"><a class="wt-back" href="{esc(back)}" data-wt-back>← Лента</a>'
                    f'<h1>Не открылось</h1><p>{esc(words)}</p></div>')
        return _frame(req, "Не открылось", body, phone=phone, status=404)
    name = req.arg("t").strip()[:200]
    cover = req.arg("c").strip()[:64]
    rating = _rating(req.arg("r"))
    picture = (f'<img src="/api/catalog/cover?c={esc(quote(cover, safe=""))}" alt="" decoding="async" data-wt-tile>'
               if cover else "")
    rate = (f'<span class="wt-rate wt-rate-own"><b>{f"{rating:.1f}".replace(".", ",")}</b></span>' if rating else "")
    mode = content_mode.mode()
    hidden = ("<template data-wt-hidden>" + _hidden_words(phone, mode, back) + "</template>") if mode != "all" else ""
    in_app = _in_app(req) and not phone
    player = _player(req, name) if in_app else _browser_player()
    site = W.site_of(url)
    body = f"""<div class="wt" id="watch-page" data-url="{esc(url)}" data-back="{esc(back)}" data-mode="{esc(mode)}"
  data-phone="{"1" if phone else ""}" data-app="{"1" if in_app else ""}" data-get="{"/m/get" if phone else "/get"}"
  data-site="{esc(site)}">
  <a class="wt-back" href="{esc(back)}" data-wt-back>← Лента</a>
  <section class="wt-hero" aria-busy="true" aria-labelledby="wt-name">
    <div class="wt-main">
      <h1 class="wt-name" id="wt-name" data-wt-name>{esc(name or "Сериал")}</h1>
      <ul class="wt-alt" data-wt-alt aria-label="Другие названия" hidden></ul>
      <div class="wt-ratings" data-wt-ratings>{rate}</div>
      <dl class="wt-facts" data-wt-facts hidden></dl>
      <p class="wt-wait" data-wt-wait role="status">Читаю тайтл с {esc(site)}…</p>
    </div>
    <aside class="wt-side" aria-label="Постер и действия">
      <div class="wt-poster" data-wt-poster>{picture}</div>
      <div class="wt-actions" data-wt-actions></div>
    </aside>
  </section>
  <section class="wt-about" data-wt-about aria-label="Описание" hidden></section>
  <details class="wt-order" data-wt-order hidden></details>
  <section class="wt-watch" data-wt-watch aria-label="Смотреть" hidden>
    <div class="wt-picks" data-wt-picks></div>
    <div class="wt-eps-head"><h2>Серии <span data-wt-count></span></h2>
      <ul class="wt-legend" aria-label="Обозначения"><li><i class="wt-key have"></i>в папке</li>
        <li><i class="wt-key seen"></i>просмотрено</li><li><i class="wt-key now"></i>сейчас</li></ul></div>
    <div class="wt-eps" data-wt-eps role="list"></div>
    <div class="wt-player" data-wt-player>
      <p class="wt-now" data-wt-now role="status">Выберите серию</p>
      {player}
      <div class="wt-marks" data-wt-marks aria-hidden="true" hidden></div>
    </div>
    <div class="wt-under">
      <button type="button" class="wt-btn" data-wt-prev disabled>‹ Предыдущая</button>
      <button type="button" class="wt-btn" data-wt-next disabled>Следующая ›</button>
      <label class="wt-auto"><input type="checkbox" data-wt-autonext{" checked" if _autonext() else ""}>
        следующая серия сама</label>
    </div>
  </section>
</div>{hidden}"""
    return _frame(req, name or "Сериал", body, phone=phone)


def _autonext() -> bool:
    from webui import prefs
    return str(prefs.load().get("video_autonext") or "on") == "on"


def _skip_buttons() -> str:
    return ('<button type="button" class="wt-skip nv-skip" data-wt-skip="opening" hidden>Пропустить опенинг</button>'
            '<button type="button" class="wt-skip nv-skip" data-wt-skip="ending" hidden>Пропустить эндинг</button>')


def _player(req: Request, name: str) -> str:
    """The window's own player (booru.js ``nativeVideo``: ``data-stream`` — the token of the episode's stream,
    set by watch.js before it starts)."""
    from webui.pages.gallery import native_video_box
    attrs = {"stream": "", "autoplay": "0", "loop": "0", "from": "0"}
    return native_video_box(req, attrs, SCREEN, frame_class="wt-frame", overlay=_skip_buttons(), title=name)


def _browser_player() -> str:
    return ('<div class="wt-screen" data-wt-screen><video class="wt-video" data-wt-video controls playsinline '
            'preload="none"></video>' + _skip_buttons() + '<p class="wt-problem-line" data-wt-problem role="alert" '
            'hidden></p></div>')


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
        text = W.playlist(req.params["token"])
    except W.WatchError as exc:
        return _failed(exc)
    return Response(200, text.encode("utf-8"), "application/vnd.apple.mpegurl")


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
