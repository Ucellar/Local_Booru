"""(v5.44.75) The tab «Скачать» — the grabber renamed, and the downloads of the other tabs put under it.

The owner (5.11.2026): «И вообще скачивание бы обеденить и доработать с переключением что хочется качать видео мангу или
манхву»; «граббер это у нас скачивалка можно переименовать там как раз можно и галерею пополнить и мангу и манхву и видео
будет».  (v5.44.87) And then (6.11.2026): «все можно делать фильтрами одна строка ввода ссылки из ссылки будет понятно
что это манга манхва или видео … мне нужен мульти скачиватель», «"из архива" это нахуй надо».  So one item of the menu,
«Скачать», and two parts in its section bar (:data:`PARTS`):

* «По ссылке» — (``/get``, ``get_link.py``) one field for a link or a name: what it is the program says; the card of
  what was found, «Читать» / «Скачать», and «Загрузки» of every kind on the right;
* «Галерея (booru)» — the grabber (``/grab``, ``grab.py``): booru sites searched by tags, or a post by its link.

The pages of before keep their addresses — «Скачать → Манга» (``/manga/get``), «Манхва» (``/manhwa/get``), «Видео»
(``/videos/get``, the other chat's page: only its frame is this one), «Из архива» (``/get/archive``) — but are not in
the bar: on one of them its own name is there, marked, after «По ссылке» (:func:`tabs`).  A link pasted into a field that is not its own goes to its own (:func:`route`,
``static/get-hub.js``): a booru post's — to «Галерея (booru)», any other — to «По ссылке».  In «SFW» — no «Галерея
(booru)» and no nhentai (``content_mode``).
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from webui.html import page_frame
from webui.server import Request, Response

PARTS = (("link", "По ссылке", "/get"), ("gallery", "Галерея (booru)", "/grab"))      # (v5.44.87)
# (v5.44.87) the pages of before, out of the bar: on one of them the bar names it (marked) after «По ссылке»
OLD_PARTS = {"manga": ("Манга", "/manga/get"), "manhwa": ("Манхва", "/manhwa/get"), "video": ("Видео", "/videos/get"),
             "archive": ("Из архива", "/get/archive")}
FIELDS = ("gallery", "site", "nhentai", "video")      # the fields a link is pasted into (get-hub.js)
_LINK = re.compile(r"https?://\S+", re.I)
# the video sites «Скачать с сайта» names (a link of one from «Галерея» goes there; from the parts of manga — any link
# no site of manga knows goes there: «пробуйте любую ссылку»)
VIDEO_HOSTS = ("hentasis", "hentai-xs", "hentaihaven", "hfshare", "yummyani", "yummy-anime", "aniliberty",
               "anilibria")          # (v5.44.87) «yummyani» — yummyani.me too, not only yummyanime.*


def register(router) -> None:
    from webui.pages import get_link
    router.add("GET", r"/get", get_link.page)          # (v5.44.87) «По ссылке»
    router.add("POST", r"/api/get/route", route_api)
    get_link.register(router)


def shown(part: str, now: str | None = None) -> bool:
    """Is this part of «Скачать» shown in the mode («Галерея» — not in «SFW»)."""
    from webui import content_mode
    return part != "gallery" or content_mode.page_shown("grab", now)


def tabs(current: str, now: str | None = None) -> tuple:
    """The section bar of «Скачать»: ((label, address, current), …) — «По ссылке», «Галерея (booru)»; on a page of
    before (its address kept: «Подробно — «Скачать с сайта»», old links) its own name too, marked, after «По ссылке»."""
    out = []
    for key, label, href in PARTS:
        if not shown(key, now):
            continue
        out.append((label, href, key == current))
        if key == "link" and current in OLD_PARTS:
            out.append((*OLD_PARTS[current], True))
    return tuple(out)


def frame(req: Request, title: str, body: str, *, part: str, kind: str, assets: tuple, sidebar: str = "",
          body_data: dict | None = None, **extra) -> Response:
    """A part of «Скачать» in its frame: «Скачать» of the menu, its look, the bar of its parts; ``assets`` — the pages
    whose stylesheets and scripts it takes (``html.PAGE_CSS``), and the parts' own script (``get-hub.js``)."""
    data = {"hub": part, **(body_data or {})}
    return Response.html(page_frame(title, body, active="get", subnav=False, kind=kind, tabs=tabs(part),
                                    sidebar=sidebar, body_data=data, assets=(*assets, "get"), **extra))


def address(part: str, link: str) -> str:
    """The page with ``link`` in its field, looked for at once: a booru post's — «Галерея (booru)», any other —
    (v5.44.87) «По ссылке» (a gallery of nhentai: its card, downloaded by its «Скачать»)."""
    q = quote(link, safe="")
    if part == "gallery":
        return f"/grab?q={q}"
    return f"/get?url={q}&go=1"


# ── what a link is ─────────────────────────────────────────────────────────────────────────────────────────────────
def one_link(text: str) -> str:
    """The text when it is one web address and nothing else, else ""."""
    text = str(text or "").strip()
    return text if _LINK.fullmatch(text) else ""


def _booru(settings: dict, link: str) -> bool:
    """A post (or its file) of a booru site the program knows — the grabber opens it."""
    from core.grabber.link import LinkError, parse
    try:
        host, _query, servers = parse(link)
    except LinkError:
        return False
    from core.parser.sites import HOST_ALIASES, engine_class, table_config
    from core.settings import ALL_KNOWN_SITES
    labels = host.split(".")
    candidates = [host] + ([".".join(labels[i:]) for i in range(1, len(labels) - 1)] if servers else [])
    for candidate in candidates:
        name = HOST_ALIASES.get(candidate, candidate)
        try:
            if name in ALL_KNOWN_SITES or table_config(settings, name) is not None or \
                    engine_class({}, name) is not None:
                return True
        except Exception:                               # noqa: BLE001 - a table that cannot be read: not this one
            continue
    return False


def _video_site(link: str) -> bool:
    host = (urlparse(link).hostname or "").lower()
    return any(word in host for word in VIDEO_HOSTS)


def kind_of(link: str, settings: dict) -> str:
    """"nhentai" (a gallery), "site" (a title of a site of manga and manhwa), "booru" (a post of a booru site),
    "video" (a page of a video site «Скачать с сайта» names) or "" (none the program knows)."""
    from core import manga_download, manhwa_sources
    numbers, bad = manga_download.gallery_ids(link)
    if numbers and not bad and not link.strip().isdigit():
        return "nhentai"
    if manhwa_sources.source_for(link)[0] is not None:
        return "site"
    if _booru(settings, link):
        return "booru"
    return "video" if _video_site(link) else ""


def route(text: str, field: str, part: str, settings: dict, now: str | None = None) -> dict:
    """Where a link put into a field of «Скачать» is looked for: {"stay": True} — here, as the field does;
    {"go": address} — another part; {"move": field} — the other field of this page («Манга»: its sites and nhentai);
    {"error": words} — nowhere in this mode."""
    from webui import content_mode
    now = now or content_mode.mode()
    link = one_link(text)
    if not link or field not in FIELDS:
        return {"stay": True}
    kind = kind_of(link, settings)
    sfw = now == "sfw"
    if kind == "nhentai":
        if field == "nhentai":
            return {"stay": True}
        if sfw:
            return {"error": "Это галерея nhentai, а включён режим «SFW» — они не скачиваются. «Показать всё» — и "
                             "скачиваются."}
        return {"move": "nhentai"} if part == "manga" else {"go": address("link", link)}
    if kind == "site":
        from core import manhwa_sources
        from webui.pages.manhwa_get import adult_refusal
        refused = adult_refusal(manhwa_sources.source_for(link)[0], now)   # (v5.44.85) a site of hentai in «SFW»
        if refused:
            return {"error": refused}
        if field == "site":
            return {"stay": True}
        if field == "nhentai":
            return {"move": "site"}
        return {"go": address("link", link)}
    if kind == "booru":
        if field == "gallery":
            return {"stay": True}
        if sfw:
            return {"error": "Это пост booru-сайта — его скачивает «Скачать → Галерея», а она скрыта в режиме «SFW»."}
        return {"go": address("gallery", link)}
    # a video's page — or a page no site of manga knows: «Скачать с сайта» tries any
    if field == "video" or (field == "gallery" and kind != "video"):
        return {"stay": True}
    return {"go": address("link", link)}


def route_api(req: Request) -> Response:
    body = req.json()
    answer = route(str(body.get("text") or "")[:4000], str(body.get("field") or ""), str(body.get("part") or ""),
                   req.settings)
    return Response.json({"ok": True, **answer})
