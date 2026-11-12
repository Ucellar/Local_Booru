"""(v5.44.64) «Телефон» — the library on the owner's phone away from home, through Tailscale (``core/phone_access``).

* «Настройки → Телефон» (:func:`settings_block`; its script — the part «Телефон» of ``static/settings.js``): what
  Tailscale on this computer is, «Пускать телефон» (:func:`switch` → ``phone_access.switch_on``: Tailscale sends
  https://<computer>.<tailnet>.ts.net to this program; the settings keep the name, the account and the port), the
  address to open on the phone and its QR code (:func:`qr_svg`, OpenCV's encoder);
* the frame of the phone's pages (:func:`phone_frame`, ``static/phone.css``, ``static/phone.js``) — the bar of the page
  on top, the sections at the bottom, as the owner's sketch has it; ``/m/`` (:func:`home`) — the first section the
  mode shows; (v5.44.65) the gallery — ``webui/pages/phone_gallery.py``; the sections not made for the phone yet open
  their computer pages.
"""
from __future__ import annotations

import logging
import re

from webui.html import esc
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.webui")

TABS = (("posts", "Галерея", "/m/posts"), ("manga", "Манга", "/m/manga"), ("manhwa", "Манхва", "/m/manhwa"),
        ("videos", "Видео", "/m/videos"))
# (v5.44.65) a page of the computer the phone has its own page for (a link of a computer page, a bookmark): the phone's
TWINS = ((re.compile(r"/"), "/m/", False), (re.compile(r"/posts"), "/m/posts", True),
         (re.compile(r"/post/(\d+)"), r"/m/post/\1", True),
         (re.compile(r"/manhwa"), "/m/manhwa", True),                                   # (v5.44.66) the manhwa
         (re.compile(r"/manhwa/(t|r)/([0-9a-f]{12})"), r"/m/manhwa/\1/\2", True),
         (re.compile(r"/manga"), "/m/manga", True),                                     # (v5.44.67) the manga
         (re.compile(r"/manga/(g|s)/([0-9a-f]{12})"), r"/m/manga/\1/\2", False),
         (re.compile(r"/manga/r/([0-9a-f]{12})/(\d+)"), r"/m/manga/r/\1?p=\2", False),
         (re.compile(r"/manga/r/([0-9a-f]{12})"), r"/m/manga/r/\1", True),       # (v5.44.117) the one reader: ?p=N
         (re.compile(r"/manga/(tags|artists|characters|parodies|random)"), r"/m/manga/\1", False),
         (re.compile(r"/videos"), "/m/videos", True),                                   # (v5.44.68) the videos
         (re.compile(r"/videos/s/([0-9a-f]{12})"), "series", False),
         (re.compile(r"/online"), "/m/online", True),                     # (v5.44.82) a chapter read from a site
         (re.compile(r"/manga/read"), "/m/manga/read", True),             # (v5.44.148) a gallery of nhentai read so
         (re.compile(r"/(manga|manhwa|videos)/feed"), r"/m/\1/feed", True),           # (v5.44.105) «Лента»
         (re.compile(r"/(manga|manhwa)/title"), r"/m/\1/title", True),               # and the title's page
         (re.compile(r"/title"), "/m/title", True),                     # (v5.44.119) any way in to the title's page
         (re.compile(r"/videos/watch"), "/m/videos/watch", True),                     # (v5.44.106) «Смотреть»
         # (v5.44.116) «Скачать → По ссылке» — the phone's «Скачать по ссылке»; the pages of before with a link — too
         (re.compile(r"/get"), "/m/get", True),
         (re.compile(r"/get/downloads"), "/m/get/downloads", False),          # (v5.44.120) «Скачать → Загрузки»
         (re.compile(r"/(manga|manhwa|videos)/get"), "get", False))
NO_STORE_OFF = {"Cache-Control": "private, no-cache"}   # the phone's pages may wait in the browser's memory: «назад»
#                                                         from a picture finds the gallery as it was left (bfcache)
ICONS = {
    "posts": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>'
             '<rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "manga": '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 21V5"/><path d="M9 8h6"/>',
    "manhwa": '<rect x="7" y="2" width="10" height="20" rx="1.5"/><path d="M7 8h10M7 14h10"/>',
    "videos": '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M10 9l5 3-5 3z"/>',
}
STAR = '<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3 6.4 20.2l1.1-6.2L3 9.6l6.2-.9z"/>'
BACK = '<path d="M15 5l-7 7 7 7"/>'
FORTH = '<path d="M9 5l7 7-7 7"/>'
DOWN = '<path d="M6 9l6 6 6-6"/>'
SEARCH = '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>'
SET = '<rect x="7" y="7" width="13" height="13" rx="1.5"/><path d="M4 16V5.5A1.5 1.5 0 0 1 5.5 4H16"/>'
SAVE = '<path d="M12 4v11M7 10l5 5 5-5"/><path d="M5 20h14"/>'
FULL = '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>'
TICK = '<path d="M5 12.5l4.5 4.5L19 7.5"/>'
PLAY = '<path d="M7 5l12 7-12 7z"/>'
SWAP = '<path d="M7 7h13M16 3l4 4-4 4"/><path d="M17 17H4M8 13l-4 4 4 4"/>'
ROWS = '<rect x="5" y="3" width="14" height="8" rx="1"/><rect x="5" y="13" width="14" height="8" rx="1"/>'
EYE = '<path d="M2.5 12c1-2 4.5-6 9.5-6s8.5 4 9.5 6c-1 2-4.5 6-9.5 6s-8.5-4-9.5-6z"/><circle cx="12" cy="12" r="3"/>'
EYE_OFF = ('<path d="M3 3l18 18"/><path d="M10.6 6.1A9.8 9.8 0 0 1 12 6c5 0 8.5 4 9.5 6-.4.9-1.2 2.1-2.3 3.2"/>'
           '<path d="M6.6 6.6C4.6 7.9 3.2 9.8 2.5 12c1 2 4.5 6 9.5 6 1.6 0 3-.4 4.3-1"/>')


def register(router) -> None:
    router.add("GET", r"/api/phone/state", state)
    router.add("GET", r"/api/phone/qr\.svg", qr_image)
    router.add("POST", r"/api/phone/switch", switch)
    router.add("GET", r"/m/?", home)
    router.add("GET", r"/m/hello", hello)                                  # (v5.44.69) the phone as an app
    router.add("GET", r"/m/offline", offline)
    router.add("GET", r"/m/manifest\.webmanifest", manifest)
    router.add("GET", r"/m/icon/(?P<mask>mask-)?(?P<size>180|192|512)\.png", icon)
    router.add("GET", r"/m/sw\.js", service_worker)
    from webui.pages import phone_gallery, phone_get, phone_manga, phone_manhwa, phone_relay, phone_videos
    phone_gallery.register(router)
    phone_manhwa.register(router)
    phone_manga.register(router)
    phone_videos.register(router)
    phone_get.register(router)                                             # (v5.44.70) «Скачать по ссылке»
    phone_relay.register(router)                                           # (v5.44.71) through the owner's server


# ── «Настройки → Телефон» ───────────────────────────────────────────────────────────────────────────────────────
def settings_block(settings: dict) -> str:
    """The part of the page «Настройки»: (v5.44.71) through the owner's own server (``phone_relay.settings_part``), and
    through Tailscale — under «Через Tailscale», open when it is on; what Tailscale says is asked by the page itself
    when that part is opened (``/api/phone/state``: the command takes seconds — the page is not kept waiting)."""
    from webui.pages import phone_relay
    on = bool(settings.get("phone_access"))
    return f"""
<section class="panel settings-section phone-panel" id="set-phone" data-phone-on="{'1' if on else ''}">
  <h2>Телефон</h2>
  <p class="hint">Библиотека на телефоне, пока компьютер включён и программа запущена: дома и через мобильный
    интернет. Два способа связи — через ваш сервер или через Tailscale.</p>
{phone_relay.settings_part(settings)}
  <details class="phone-way phone-tailscale" data-phone-tailscale{' open' if on else ''}>
  <summary>Через Tailscale</summary>
  <p class="hint">Tailscale соединяет ваш компьютер и телефон в личную сеть, и программу видят только ваши устройства.
    Поставьте Tailscale на компьютер (tailscale.com/download) и на телефон и войдите в обоих одним аккаунтом.</p>
  <div class="phone-status" data-phone-status aria-live="polite"><p class="hint">Спрашиваю Tailscale…</p></div>
  <div class="tool-actions">
    <button type="button" class="primary" data-action="phone-switch" data-on="1" hidden>Пускать телефон</button>
    <button type="button" data-action="phone-switch" data-on="" hidden>Не пускать телефон</button>
    <button type="button" data-action="phone-check">Проверить снова</button>
    <span class="action-status" role="status"></span>
  </div>
  <div class="phone-open" data-phone-open hidden>
    <p>Откройте на телефоне (Tailscale там включён):</p>
    <p class="phone-address"><a data-phone-address href="#" target="_blank" rel="noopener"></a>
      <button type="button" data-copy="" data-what="адрес" data-phone-copy>Скопировать</button></p>
    <img class="phone-qr" data-phone-qr alt="QR-код адреса для телефона" width="198" height="198" hidden>
    <p class="hint">Наведите на код камеру телефона. Чтобы библиотека открывалась как приложение — в меню браузера
      «Добавить на главный экран».</p>
  </div>
  </details>
</section>"""


def _state_of(req: Request) -> dict:
    from core import phone_access
    tailscale = phone_access.status()
    on = bool(req.settings.get("phone_access"))
    address = phone_access.address(req.settings) if on else ""
    serving = bool(on and tailscale.running and phone_access.serve_target().rstrip("/") ==
                   f"http://127.0.0.1:{req.server.port if req.server else 0}")
    return {"ok": True, "tailscale": tailscale.public(), "on": on, "serving": serving, "address": address,
            "qr": bool(address and qr_svg(address)),
            "renamed": bool(on and tailscale.name and tailscale.name != str(req.settings.get("phone_name") or ""))}


def state(req: Request) -> Response:
    return Response.json(_state_of(req))


def qr_image(req: Request) -> Response:
    """The QR code of the phone's address — the page shows it as a picture."""
    from core import phone_access
    address = phone_access.address(req.settings) if req.settings.get("phone_access") else ""
    drawing = qr_svg(address) if address else ""
    if not drawing:
        return Response.not_found("Кода нет")
    return Response(200, drawing.encode("utf-8"), "image/svg+xml", {"Cache-Control": "no-store"})


def switch(req: Request) -> Response:
    """{on: true|false} — «Пускать телефон» / «Не пускать телефон»."""
    from core import phone_access
    from webui import settings_store
    on = bool(req.json().get("on"))
    if not on:
        result = phone_access.switch_off()
        if not settings_store.save(result["changes"]):
            return Response.json({"ok": False, "error": "Окно программы не ответило — не сохранено"}, 503)
        return Response.json({"ok": True, "message": "Телефон больше не пускается"})
    port = req.server.port if req.server is not None else 0
    if not port:
        return Response.json({"ok": False, "error": "Программа не знает своего порта"}, 409)
    result = phone_access.switch_on(port)
    if not result["ok"]:
        return Response.json({"ok": False, "error": result["words"], "link": result.get("link") or ""}, 409)
    if not settings_store.save(result["changes"]):
        return Response.json({"ok": False, "error": "Окно программы не ответило — не сохранено"}, 503)
    return Response.json({"ok": True, "message": "Телефон пускается", "address": phone_access.address(result["changes"])})


def qr_svg(text: str) -> str:
    """The QR code of ``text`` as an SVG drawing (OpenCV's encoder); "" — none (the address is shown anyway)."""
    try:
        import cv2
        matrix = cv2.QRCodeEncoder.create().encode(str(text))
    except Exception as exc:  # noqa: BLE001 - an OpenCV without the encoder: the address alone
        _LOG.info("phone: no QR code: %s: %s", type(exc).__name__, exc)
        return ""
    rows, cols = int(matrix.shape[0]), int(matrix.shape[1])
    pad = 2                                               # OpenCV leaves two modules around: four in all
    runs = []
    for y in range(rows):
        x = 0
        while x < cols:
            if matrix[y, x] < 128:
                start = x
                while x < cols and matrix[y, x] < 128:
                    x += 1
                runs.append(f"M{start + pad} {y + pad}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    size = cols + 2 * pad
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size * 6}" '
            f'height="{size * 6}" role="img" '
            f'aria-label="QR-код адреса для телефона" shape-rendering="crispEdges"><rect width="{size}" height="{size}" '
            f'fill="#fff"/><path d="{"".join(runs)}" fill="#000"/></svg>')


# ── the phone's pages ───────────────────────────────────────────────────────────────────────────────────────────
def svg(paths: str, *, size: int = 24, width: float = 1.8, cls: str = "", fill: str = "none", label: str = "") -> str:
    """A line icon of the phone's pages (``paths`` — one of the drawings above)."""
    named = f'role="img" aria-label="{esc(label)}"' if label else 'aria-hidden="true"'
    klass = f' class="{cls}"' if cls else ""
    return (f'<svg{klass} {named} width="{size}" height="{size}" viewBox="0 0 24 24" fill="{fill}" '
            f'stroke="currentColor" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>')


def _icon(name: str) -> str:
    return svg(ICONS[name])


def tab_bar(active: str) -> str:
    """The sections at the bottom; in «SFW» without the gallery (``content_mode``)."""
    from webui import content_mode
    now = content_mode.mode()
    links = []
    for key, label, href in TABS:
        if not content_mode.page_shown(key, now):
            continue
        mark = ' aria-current="page"' if key == active else ""
        links.append(f'<a class="m-tab" href="{href}"{mark}>{_icon(key)}<span>{esc(label)}</span></a>')
    return (f'<nav class="m-tabs" aria-label="Разделы программы" style="--tabs-n:{len(links)}">'
            f'{"".join(links)}</nav>')


def get_link() -> str:
    """(v5.44.70) «Скачать по ссылке» in the bar of the lists of manga, manhwa and videos — (v5.44.120) none: the bar of
    every page has ⬇ «Загрузки» with its number (:func:`phone_frame`), whose «Скачать» has «По ссылке» and «Загрузки»."""
    return ""


def mode_switch() -> str:
    """«SFW / Всё / NSFW» in the bar of the page (``webui/content_mode.py``): the phone's own list to choose from —
    (v5.44.116) the phone's own mode: «ПК: …» (its default, «как на компьютере») — the computer's, named; short: a
    list as wide as its longest choice would take the bar's title."""
    from webui import content_mode
    own = content_mode.phone_choice()
    pc = content_mode.LABELS[content_mode.mode(phone=False)]
    options = (f'<option value="{content_mode.PHONE_FOLLOWS}"{"" if own else " selected"} title="Как на компьютере — '
               f'сейчас там «{pc}»">ПК: {pc}</option>'
               + "".join(f'<option value="{m}"{" selected" if m == own else ""}>{content_mode.LABELS[m]}</option>'
                         for m in content_mode.MODES))
    return (f'<label class="m-mode"><span class="m-sr">Что показывать</span><select data-mode>{options}</select>'
            f'{svg(DOWN, size=14, width=2)}</label>')


def mark_box(kind: str, item_id: str, found) -> str:
    """(v5.44.122) «Это хентай» / «Это не хентай» of a title of a library on the phone — the computer's button and API
    (``/api/content-mark``, its action — ``phone.js``): ``kind`` — "manga", "manhwa" or "series", ``found`` — its
    verdict (``core/content_marks``); beside it what the mode does with the mark, and, as the computer's row «Хентай:
    да — с nhentai.net», why the program takes it for hentai (the site, the genre, a mark by hand)."""
    hentai = bool(found.hentai)
    words = ("в «SFW» — показывать,<br>в «NSFW» — прятать" if hentai else "в «SFW» — прятать,<br>в «NSFW» — показывать")
    why = ""
    if found.hentai or found.by_hand:
        said = ("да" if hentai else "нет") + (f" — {found.reason}" if found.reason else "")
        why = f'<p class="m-mark-why">Хентай: {esc(said)}</p>'
    return (f'<div class="m-mark" data-mark><button type="button" class="m-button m-quiet m-mark-btn" '
            f'data-action="content-mark" data-kind="{esc(kind)}" data-id="{esc(item_id)}" '
            f'data-value="{"safe" if hentai else "hentai"}">{svg(EYE if hentai else EYE_OFF, size=17, width=2)}'
            f'<span>{"Это не хентай" if hentai else "Это хентай"}</span></button>'
            f'<p class="m-mark-words">{words}</p>{why}</div>')


def phone_frame(title: str, body: str, *, active: str = "", heading: str = "", back: str = "", tabs: bool = True,
                top: str = "", kind: str = "", bare: bool = False, scripts: tuple = (), head: str = "") -> str:
    """A page of the phone: the bar on top (the way back or the brand, the title, ``top`` — buttons at its right), the
    page, the sections at the bottom (``tabs``).  ``kind`` — the class of its body (``m-viewer`` and so on); ``bare``
    — without the bar on top (a reader has its own); ``scripts`` — the computer's scripts the page uses too
    (``static/<name>.js``: phone.js gives them what booru.js gives on the computer); ``head`` — more of ``<head>``."""
    back_link = (f'<a class="m-icon" href="{esc(back)}" data-back aria-label="Назад">{svg(BACK, width=2)}</a>'
                 if back else '<span class="m-brand" aria-hidden="true">LB</span>')
    title_tag = f'<h1 class="m-title">{esc(heading or title)}</h1>' if heading is not None else ""
    if not bare:                                   # (v5.44.120) ⬇ «Загрузки» with its number, in every page's bar
        from webui.pages import downloads
        top = downloads.phone_badge() + top
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0d0d0d">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Local Booru">
<title>{esc(title)} — Local Booru</title>
<link rel="manifest" href="/m/manifest.webmanifest">
<link rel="icon" type="image/png" sizes="192x192" href="/m/icon/192.png">
<link rel="apple-touch-icon" href="/m/icon/180.png">
<link rel="stylesheet" href="/static/phone.css">{head}
<script src="/static/phone.js" defer></script>{"".join(f'<script src="/static/{esc(name)}.js" defer></script>' for name in scripts)}
</head>
<body class="m-body{(' ' + esc(kind)) if kind else ''}{'' if tabs else ' m-no-tabs'}">
{'' if bare else f'<header class="m-top">{back_link}{title_tag}{top}</header>'}
<main class="m-main" id="main">{body}</main>
{tab_bar(active) if tabs else ''}
<p class="m-note" data-m-note role="status" hidden></p>
</body>
</html>"""


def page(title: str, body: str, status: int = 200, **frame) -> Response:
    """A phone page as the answer (:data:`NO_STORE_OFF`)."""
    return Response(status, phone_frame(title, body, **frame).encode("utf-8"), headers=dict(NO_STORE_OFF))


def home_path(now: str | None = None) -> str:
    """The phone's first page in the mode: the gallery, in «SFW» — the manga."""
    from webui import content_mode
    return "/m/posts" if content_mode.page_shown("posts", now) else "/m/manga"


def twin(path: str, query: str = "") -> str:
    """The phone's own page for a page of the computer (``webui/server``: only for the owner's phone), "" — none."""
    for pattern, target, keeps in TWINS:
        found = pattern.fullmatch(path)
        if not found:
            continue
        if target == "series":                         # a series' page with ?ep= — that episode's player
            from urllib.parse import parse_qs, quote
            episode = (parse_qs(query).get("ep") or [""])[0]
            if re.fullmatch(r"[^\s]{1,120}", episode):  # (v5.44.121) its number, or a file of it (before)
                return f"/m/videos/s/{found.group(1)}?ep={quote(episode, safe='')}&play=1"
            return f"/m/videos/s/{found.group(1)}"
        if target == "get":                            # (v5.44.116) «Скачать → Манга / Манхва / Видео» of before
            from urllib.parse import parse_qs, quote
            asked = parse_qs(query)
            link = ((asked.get("url") or asked.get("nh") or [""])[0]).strip()
            if not link:                               # (v5.44.122) a name to search (?q=) — the phone's searches too
                name = (asked.get("q") or [""])[0].strip()
                return f"/m/get?q={quote(name, safe='')}" if name else "/m/get"
            # their link was looked for at once (the videos' — with go=1), so it is on the phone too
            at_once = found.group(1) != "videos" or (asked.get("go") or [""])[0] == "1"
            return f"/m/get?url={quote(link, safe='')}" + ("&go=1" if at_once else "")
        return found.expand(target) + (f"?{query}" if keeps and query else "")
    return ""


HELLO_COOKIE = "lb_m_hello"


def home(req: Request) -> Response:
    """``/m/`` — the first section the mode shows (the address of the QR code leads here through «/»); the very first
    time on this phone — «Телефон подключён» (:func:`hello`)."""
    from http.cookies import CookieError, SimpleCookie
    try:
        seen = HELLO_COOKIE in SimpleCookie(req.headers.get("Cookie", ""))
    except CookieError:
        seen = False
    return Response.redirect(home_path() if seen else "/m/hello")


# ── (v5.44.69) the phone as an app: the first visit, the icon, «Компьютер не отвечает» ───────────────────────────
def hello(req: Request) -> Response:
    """The sketch «Первый вход»: the phone is let in; how to put the library on the home screen."""
    body = f"""
<section class="m-hello m-welcome">
  <div class="m-welcome-mark"><span class="m-brand" aria-hidden="true">LB</span>{svg(TICK, size=28, width=2.4, cls="m-ok")}</div>
  <h2>Телефон подключён</h2>
  <p>Это ваша библиотека с компьютера. Она открывается, пока компьютер включён и Local Booru запущена — и дома, и через
    мобильный интернет.</p>
  <h3>Как обычное приложение</h3>
  <p>Добавьте библиотеку на главный экран телефона: она будет открываться значком, без строки браузера.</p>
  <ol class="m-steps"><li>Меню браузера — три точки (или «Поделиться»).</li><li>«Добавить на главный экран» или
    «Установить приложение».</li></ol>
  <p><button type="button" class="m-button m-quiet" data-install hidden>Установить приложение</button></p>
  <p><a class="m-button" href="{esc(home_path())}">Открыть библиотеку</a></p>
  <p class="m-muted">Отключить этот телефон можно на компьютере: «Настройки → Телефон».</p>
</section>"""
    page_ = Response(200, phone_frame("Телефон подключён", body, tabs=False, heading="Local Booru").encode("utf-8"),
                     headers=dict(NO_STORE_OFF))
    page_.headers["Set-Cookie"] = f"{HELLO_COOKIE}=1; Path=/; Max-Age=31536000; SameSite=Lax"
    return page_


def offline(req: Request) -> Response:
    """The sketch «Компьютер не отвечает» — kept by the phone's service worker (``static/phone-sw.js``) and shown by it
    when a page of the phone cannot be had; when it was last on line — ``phone.js`` (the phone's own memory)."""
    body = f"""
<section class="m-hello m-offline">
  {svg('<path d="M3 5h18v11H3z"/><path d="M8 20h8M12 16v4"/><path d="M4 4l16 16"/>', size=44, width=1.6, cls="m-muted")}
  <h2>Компьютер не отвечает</h2>
  <p>Библиотека живёт на компьютере, поэтому без него телефону нечего показать. Проверьте:</p>
  <ul class="m-steps m-checks"><li>Компьютер включён и не ушёл в сон.</li><li>Local Booru на компьютере запущена.</li>
    <li>Связь с телефоном включена: «Настройки → Телефон» на компьютере (через Tailscale — он включён и на
      компьютере, и на телефоне).</li></ul>
  <p class="m-muted" data-last-seen hidden></p>
  <p><a class="m-button" href="/m/" data-reload>Попробовать снова</a></p>
</section>"""
    return Response(200, phone_frame("Компьютер не отвечает", body, tabs=False, bare=True,
                                     kind="m-offline-page").encode("utf-8"), headers={"Cache-Control": "no-cache"})


def manifest(req: Request) -> Response:
    """The library as an app.  (v5.44.70) The home screen's icon is the «maskable» one (``mask-…``: the launcher gives
    it its shape — the tile with margins was put small on a white plate); «Поделиться» of another app brings a link to
    «Скачать по ссылке» (``share_target``: Android's Chrome puts the link in ``text``)."""
    icons = [{"src": f"/m/icon/{size}.png", "sizes": f"{size}x{size}", "type": "image/png", "purpose": "any"}
             for size in (192, 512)]
    icons += [{"src": f"/m/icon/mask-{size}.png", "sizes": f"{size}x{size}", "type": "image/png", "purpose": "maskable"}
              for size in (192, 512)]
    data = {"id": "/m/", "name": "Local Booru", "short_name": "Local Booru", "lang": "ru", "start_url": "/m/",
            "scope": "/m/", "display": "standalone", "background_color": "#1f1f1f", "theme_color": "#0d0d0d",
            "icons": icons,
            "share_target": {"action": "/m/get", "method": "GET",
                             "params": {"title": "title", "text": "text", "url": "url"}}}
    import json
    return Response(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/manifest+json",
                    {"Cache-Control": "no-cache"})


_ICONS: dict = {}


def icon(req: Request) -> Response:
    """The program's icon at the size a phone asks for: ``assets/app_icon.png`` (the tile, transparent around it) or —
    ``mask-…`` and Apple's 180 (it puts its own corners on a full square) — ``assets/app_icon_phone.png`` (v5.44.70:
    the tile without margins, ``tools/make_app_icon.py``)."""
    size = int(req.params["size"])
    full = bool(req.params.get("mask")) or size == 180
    made = _ICONS.get((size, full))
    if made is None:
        import io
        from pathlib import Path

        from PIL import Image
        source = Path(__file__).resolve().parents[2] / "assets" / ("app_icon_phone.png" if full else "app_icon.png")
        try:
            with Image.open(source) as picture:
                small = picture.convert("RGB" if full else "RGBA").resize((size, size), Image.LANCZOS)
                buffer = io.BytesIO()
                small.save(buffer, "PNG", optimize=True)
                made = _ICONS[(size, full)] = buffer.getvalue()
        except OSError:
            return Response.not_found("Значка нет")
    return Response(200, made, "image/png", {"Cache-Control": "private, max-age=86400"})


def service_worker(req: Request) -> Response:
    from webui.server import STATIC_DIR
    return Response(200, (STATIC_DIR / "phone-sw.js").read_bytes(), "text/javascript; charset=utf-8",
                    {"Cache-Control": "no-cache"})


def hidden_page(req: Request) -> Response:
    """A gallery page of the phone in «SFW» (``content_mode.guard``): hidden, «Показать всё»."""
    body = """
<section class="m-hello">
  <h2>Галерея скрыта</h2>
  <p>Включён режим «SFW» — без хентая: галерея не показывается. Подписки и скачивания на компьютере работают как
    обычно.</p>
  <p><button type="button" class="m-button" data-mode-set="all">Показать всё</button></p>
</section>"""
    return page("Скрыто — SFW", body, heading="Галерея", top=mode_switch())
