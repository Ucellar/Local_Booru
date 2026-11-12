"""The switch «SFW / Всё / NSFW» in the header of every page (v5.44.53): what the pages show.

* «SFW» — без хентая: the gallery and everything of its world (its sections and «Теги», «Парсер», «Подписки»,
  «Сайты», «Дубли», the long videos of «Видео»; of «Скачать» — its part «Галерея» and the galleries of nhentai) leave
  the menu, and their pages say they are hidden (a link, a bookmark); the manga and «Видео» list only what is not
  hentai (``core/content_marks.py``).
* «Всё» — everything, as before the switch was made (and until it is first touched).
* «NSFW» — only the adult: the gallery as it is; the manga and «Видео» list only hentai.

The owner (4.11.2026): the switch is there to see less of it, not to lock it away — one click, no password; the work in
the background (subscriptions, the grabber, downloads) goes on in every mode.  The mode is the viewer's choice, kept
with the looks (``webui_prefs.json``: ``content_mode``) — the same in the program window and in a browser.  Only pages
are hidden: the pictures and files themselves are served as before (a page of another part may show them).

(v5.44.116; the review: the mode switched on the phone changed the program's window at home) The phone has a mode of
its own (``phone_content_mode``; none — «ПК: …», «как на компьютере», its default: the computer's mode, as before).
A request is the phone's (:func:`of_phone`) when it comes from the owner's phone, or is a page of the phone (``/m/…``)
or a request of one; ``server._dispatch`` answers it inside :func:`answering`, and :func:`mode` gives the phone's mode
there.
"""
from __future__ import annotations

import contextlib
import re
import threading
from urllib.parse import parse_qs, urlparse

from webui.html import esc

MODES = ("sfw", "all", "nsfw")
PHONE_FOLLOWS = "pc"            # (v5.44.116) the phone's choice «ПК: …»: the computer's mode (the phone's default)
_ANSWERING = threading.local()  # (v5.44.116) is the request this thread answers the phone's (:func:`answering`)
LABELS = {"sfw": "SFW", "all": "Всё", "nsfw": "NSFW"}
TITLES = {"sfw": "SFW — без хентая: галерея и всё, что с ней связано, скрыты",
          "all": "Всё — показывать всё",
          "nsfw": "NSFW — только взрослое: галерея, хентай-манга и хентай-сериалы"}
# the pages of the menu that are the gallery's world: not shown in «SFW» (``html.MENU`` keys; "grab" — (v5.44.75) the
# part «Галерея» of «Скачать», ``get_hub.py``)
GALLERY_PAGES = ("posts", "tags", "grab", "parser", "subs", "sites", "dupes")
# their addresses: a page there says it is hidden ((v5.44.75) «Скачать → Манга» is shown: its nhentai part is not)
_GALLERY_PATH = re.compile(r"/(?:posts|post/\d+|post/remote|tags|random|grab|parser|subs|sites|dupes)")
_PHONE_GALLERY = re.compile(r"/m/(?:posts|post/\d+)")     # (v5.44.65) the gallery of the phone
_NAMES = (("/grab", "Скачивание в галерею", "get"), ("/parser", "Парсер", "parser"),
          ("/subs", "Подписки", "subs"), ("/sites", "Парсер и сайты", "sites"), ("/dupes", "Дубли", "dupes"),
          ("/tags", "Теги галереи", "tags"))


def mode(phone: bool | None = None) -> str:
    """The mode now: "sfw", "all" or "nsfw" — (v5.44.116) of the phone for a request of the phone (:func:`answering`;
    ``phone`` — asked for it or for the computer's whatever the request): its own choice, else the computer's."""
    from webui import prefs
    stored = prefs.load()
    if getattr(_ANSWERING, "phone", False) if phone is None else phone:
        own = str(stored.get("phone_content_mode") or "")
        if own in MODES:
            return own
    value = str(stored.get("content_mode") or "all")
    return value if value in MODES else "all"


def phone_choice() -> str:
    """(v5.44.116) The phone's own choice: "sfw", "all", "nsfw" — or "" («ПК: …» — the computer's mode)."""
    from webui import prefs
    own = str(prefs.load().get("phone_content_mode") or "")
    return own if own in MODES else ""


def _phone_path(path: str) -> bool:
    return path == "/m" or path.startswith("/m/")


def of_phone(req, admitted: "str | None" = None) -> bool:
    """(v5.44.116) Is the request the phone's: the owner's phone (``phone_access.admit`` said «ok» — every request of
    it), a page of the phone (``/m/…`` — opened on the computer too) or a request of such a page — its pictures and
    questions (their Referer: the program's pages tell their address to the program itself — ``server``:
    «Referrer-Policy: same-origin»), not a page of the computer opened from it."""
    if admitted == "ok" or _phone_path(str(getattr(req, "path", "") or "")):
        return True
    headers = getattr(req, "headers", None) or {}
    named = {str(k).lower(): str(v) for k, v in headers.items()
             if str(k).lower() in ("referer", "host", "sec-fetch-mode")}
    if named.get("sec-fetch-mode", "").strip().lower() == "navigate":
        return False
    came = urlparse(named.get("referer", ""))
    return bool(came.netloc) and came.netloc.lower() == named.get("host", "").lower() and _phone_path(came.path)


@contextlib.contextmanager
def answering(phone: bool):
    """(v5.44.116) The request this thread answers (``server._dispatch``) is the phone's or not: :func:`mode` inside
    gives its mode."""
    before = getattr(_ANSWERING, "phone", False)
    _ANSWERING.phone = bool(phone)
    try:
        yield
    finally:
        _ANSWERING.phone = before


def home(now: str | None = None) -> str:
    """The first page of the program in this mode («/», the brand in the header, the program's window)."""
    return "/manga" if (now or mode()) == "sfw" else "/posts"


def page_shown(page: str, now: str | None = None) -> bool:
    """Is this page of the menu (``html.MENU`` key) shown in the mode."""
    return not ((now or mode()) == "sfw" and page in GALLERY_PAGES)


def shows(hentai: bool, now: str | None = None) -> bool:
    """Is a title (hentai or not) shown in the mode."""
    now = now or mode()
    return now == "all" or (now == "nsfw") == bool(hentai)


def long_videos_shown(now: str | None = None) -> bool:
    """The long videos of «Видео» are the gallery's: not in «SFW»."""
    return (now or mode()) != "sfw"


def switch(now: str | None = None) -> str:
    """The switch in the header: three buttons, the mode now pressed."""
    now = now or mode()
    buttons = "".join(
        f'<button type="button" class="mode-opt mode-{m}{" active" if m == now else ""}" data-action="content-mode" '
        f'data-mode="{m}" aria-pressed="{"true" if m == now else "false"}" title="{esc(TITLES[m])}">{LABELS[m]}</button>'
        for m in MODES)
    return f'<div class="mode-switch" role="group" aria-label="Что показывать">{buttons}</div>'


# ── pages that are hidden ─────────────────────────────────────────────────────────────────────────────────────
def guard(req):
    """Before a page is made (``server``): in «SFW» a page of the gallery's world is not made — «/posts» itself (the
    program opens it) goes to the mode's first page, the others say they are hidden.  None — make the page."""
    phone = bool(_PHONE_GALLERY.fullmatch(req.path))
    if req.method not in ("GET", "HEAD") or not (phone or _GALLERY_PATH.fullmatch(req.path)) or mode() != "sfw":
        return None
    from webui.server import Response
    if phone:
        from webui.pages import phone as phone_pages
        if req.path == "/m/posts" and not req.query:
            return Response.redirect(phone_pages.home_path("sfw"))
        return phone_pages.hidden_page(req)
    if req.path == "/posts" and not req.query:
        return Response.redirect(home("sfw"))
    return hidden_page(req)


def _page_of(path: str) -> tuple[str, str]:
    for prefix, name, page in _NAMES:
        if path == prefix or path.startswith(prefix + "/"):
            return name, page
    return "Галерея", "posts"


def _actions(back: str = "") -> str:
    show = ('<button type="button" class="mode-show" data-action="content-mode" data-mode="all">Показать всё</button>')
    return f'<p class="mode-hidden-actions">{show}{back}<span class="action-status" role="status"></span></p>'


def hidden_page(req):
    """A page of the gallery's world in «SFW»: what is hidden and why, «Показать всё»."""
    from webui.html import page_frame
    from webui.server import Response
    name, page = _page_of(req.path)
    body = (f'<div class="mode-hidden" data-hidden="page"><h1>{esc(name)} — скрыто</h1>'
            f'<p>Включён режим «SFW» — без хентая: галерея и всё, что с ней связано, не показываются.</p>'
            f'<p class="muted">Подписки и скачивания работают как обычно — режим только прячет страницы.</p>'
            + _actions('<a href="/manga">Манга</a><a href="/videos">Видео</a>') + "</div>")
    tabs = ()
    if page == "get":                       # (v5.44.75) «Скачать → Галерея»: the other parts of «Скачать» are there
        from webui.pages import get_hub
        tabs = get_hub.tabs("gallery", "sfw")
    return Response.html(page_frame("Скрыто — SFW", body, active=page, subnav=False, kind="mode-hidden", tabs=tabs))


def hidden_item(*, active: str, what: str, reason: str, hentai: bool, back: str, back_label: str,
                now: str | None = None) -> str:
    """The body of a title's page (a manga, a series) the mode hides: why, «Показать всё», back to the list.  The
    title itself is not named."""
    now = now or mode()
    if now == "sfw":
        why = f"Включён режим «SFW», а {esc(what)} — хентай" + (f" ({esc(reason)})" if reason else "") + "."
    else:
        marked = "не помечен" if active == "videos" else "не помечена"      # «этот сериал» / «эта манга»
        why = f"Включён режим «NSFW» — видно только взрослое, а {esc(what)} {marked} как хентай."
    return (f'<div class="mode-hidden" data-hidden="{"hentai" if hentai else "safe"}"><h1>Скрыто в режиме '
            f'«{LABELS[now]}»</h1><p>{why}</p>'
            + _actions(f'<a href="{esc(back)}">{esc(back_label)}</a>') + "</div>")


# ── the switch and the marks: what the pages ask ─────────────────────────────────────────────────────────────
def register(router) -> None:
    router.add("POST", r"/api/content-mode", switch_mode)
    router.add("POST", r"/api/content-mark", set_mark)


def after_switch(address: str, now: str, settings: dict) -> str:
    """Where the page the switch was pressed on goes in the new mode: "" — it stays (shown again), else the list it
    belongs to (its title is hidden now) or the mode's first page."""
    parsed = urlparse(str(address or "/"))
    path = parsed.path or "/"
    if path.startswith("/m/") or path == "/m":                # (v5.44.65) a page of the phone
        from webui.pages import phone as phone_pages
        if now == "sfw" and _PHONE_GALLERY.fullmatch(path):
            return phone_pages.home_path(now)
        m = re.fullmatch(r"/m/manhwa/(?:t|r)/([0-9a-f]{12})", path)     # (v5.44.66) a title of the manhwa
        if m:
            from webui.pages import manhwa
            return "/m/manhwa" if manhwa.item_shown(settings, m.group(1), now) is False else ""
        m = re.fullmatch(r"/m/manga/(?:g|s|r)/([0-9a-f]{12})", path)     # (v5.44.67) a title of the manga
        if m:
            from webui.pages import manga
            return "/m/manga" if manga.item_shown(settings, m.group(1), now) is False else ""
        m = re.fullmatch(r"/m/videos/(s|e)/([0-9a-f]{12})", path)        # (v5.44.68) a series, an episode
        if m:
            from webui.pages import videos
            series_id = m.group(2)
            if m.group(1) == "e":
                from core import video_library as V
                root = V.video_root(settings)
                series = V.LIBRARY.episode(root, series_id)[0] if root else None
                series_id = series.id if series is not None else ""
            return "/m/videos" if series_id and videos.item_shown(settings, series_id, now) is False else ""
        return ""
    if now == "sfw":
        if path == "/grab":                       # (v5.44.75) «Скачать → Галерея»: «Скачать» goes on — (v5.44.87) by a link
            return "/get"
        if path == "/" or _GALLERY_PATH.fullmatch(path):
            return home(now)
        if path == "/videos" and "long" in parse_qs(parsed.query).get("show", []):
            return "/videos"
    m = re.fullmatch(r"/manga/(?:g|s|r)/([0-9a-f]{12})(?:/\d+)?", path)
    if m:
        from webui.pages import manga
        return "/manga" if manga.item_shown(settings, m.group(1), now) is False else ""
    m = re.fullmatch(r"/videos/s/([0-9a-f]{12})", path)
    if m:
        from webui.pages import videos
        return "/videos" if videos.item_shown(settings, m.group(1), now) is False else ""
    m = re.fullmatch(r"/manhwa/(?:t|r)/([0-9a-f]{12})", path)
    if m:
        from webui.pages import manhwa
        return "/manhwa" if manhwa.item_shown(settings, m.group(1), now) is False else ""
    return ""


def switch_mode(req):
    """{mode, path — the page's address}: the computer's mode — (v5.44.116) or, switched on the phone (its request, a
    page of the phone), the phone's own; «pc» there — «ПК: …», as on the computer (the phone's choice forgotten)."""
    from webui import prefs
    from webui.server import Response
    body = req.json()
    now = str(body.get("mode") or "")
    address = str(body.get("path") or "/")
    phone = bool(getattr(_ANSWERING, "phone", False)) or _phone_path(urlparse(address).path or "/")
    if phone and now == PHONE_FOLLOWS:
        prefs.save({"phone_content_mode": ""})
        now = mode(phone=True)                        # the computer's: where the page goes is by it
    elif now not in MODES:
        return Response.json({"ok": False, "error": "Нет такого режима"}, 400)
    else:
        prefs.save({"phone_content_mode": now} if phone else {"content_mode": now})
    return Response.json({"ok": True, "mode": now, "go": after_switch(address, now, req.settings)})


def set_mark(req):
    """«Это хентай» / «Это не хентай» on a title's page: {kind: "manga" | "series" | "manhwa", id, hentai}; ``go`` —
    the list the title is hidden from in the mode now ((v5.44.122) the phone's mark — in the phone's mode, the phone's
    list: ``phone.mark_box``)."""
    from core import content_marks as CM
    from webui.server import Response
    body = req.json()
    kind = str(body.get("kind") or "")
    item_id = str(body.get("id") or "")
    if kind not in CM.KINDS or not re.fullmatch(r"[0-9a-f]{12}", item_id):
        return Response.json({"ok": False, "error": "Непонятно, что пометить"}, 400)
    if kind == "manga":
        from webui.pages import manga
        item = manga.top_item(req.settings, item_id)
        back = "/manga"
    elif kind == "manhwa":
        from webui.pages import manhwa
        item = manhwa.find_title(req.settings, item_id)
        back = "/manhwa"
    else:
        from webui.pages import videos
        item = videos.find_series(req.settings, item_id)
        back = "/videos"
    if item is None:
        return Response.json({"ok": False, "error": "Такого тайтла нет — возможно, папку переименовали"}, 404)
    if getattr(_ANSWERING, "phone", False):
        back = "/m" + back
    found = CM.mark(kind, item, bool(body.get("hentai")))
    return Response.json({"ok": True, "hentai": found.hentai, "by_hand": found.by_hand,
                          "go": "" if shows(found.hentai) else back})
