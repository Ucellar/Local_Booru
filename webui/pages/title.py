"""(v5.44.102) The page of a title of «Лента» (the owner, 6.11.2026: «а почему просмотр открывается как ссылка на
скачивание? А не как страница с тайтлом?»; its look — the mockup «Тайтл из «Онлайн»» of the canvas «Local Booru —
Скачать и Онлайн» he approved with the feed).  Until 5.44.101 a tile led to the card of «Скачать → По ссылке».

``/manga/title?url=`` and ``/manhwa/title?url=`` — in the tab of the feed («Лента» marked; «← Лента» back to it): the
title as its site gives it — cover, names, kind, year, status, rating, genres, description; the chapters' language
(MangaDex) or translators; «Продолжить» / «Читать с начала» (without downloading — ``/online``); «Скачать всё»; the
chapters as rows, the newest first («▶ Читать», ticked — «Скачать отмеченные»), what is in the folder, what goes
while they download.  The title is found by the machinery of «Скачать» (``manhwa_get``: ``/api/manhwa/find``,
``find-state``, ``find-cover``, ``download``, ``download-state``) — this page only shows it (``static/title.js``).
A gallery of nhentai — the same page for it: its facts, tags, pages (``manga_get``: ``/api/manga/gallery``,
``/api/manga/download``).  ``c`` — the tile's cover (``/api/catalog/cover``), ``r`` — its rating, ``t`` — its name:
shown before the site answers; ``b`` — the translators (or the language) chosen.  The mode: «SFW» — a title of a site
of hentai is not shown (the find refuses it too), a title its site calls erotica — hidden by the page once the site
has answered (``<template data-tt-hidden>``, the words of ``content_mode.hidden_item``); «NSFW» — the other way.
(v5.44.105) The phone — ``/m/manga/title``, ``/m/manhwa/title``: the same page in its frame (back to the phone's
«Лента», its reader ``/m/online``, its card ``/m/get``; the computer's address opened on the phone goes here —
``phone.TWINS``)."""
from __future__ import annotations

from urllib.parse import quote, urlencode

from webui.html import esc, page_frame
from webui.server import Request, Response

KINDS = ("manga", "manhwa")
HEADS = {"manga": "Манга", "manhwa": "Манхва"}
WHAT = {"manga": "эта манга", "manhwa": "эта манхва"}       # the words of a page the mode hides


def register(router) -> None:
    router.add("GET", r"/manga/title", lambda req: page(req, "manga"))
    router.add("GET", r"/manhwa/title", lambda req: page(req, "manhwa"))
    router.add("GET", r"/m/manga/title", lambda req: page(req, "manga", phone=True))      # (v5.44.105) the phone
    router.add("GET", r"/m/manhwa/title", lambda req: page(req, "manhwa", phone=True))


def href(kind: str, url: str, *, cover: str = "", rating: float = 0.0, name: str = "", branch: str = "",
         phone: bool = False) -> str:
    """The address of the page of the title at ``url`` in the tab ``kind`` (a tile of «Лента» gives its cover, rating
    and name — the page shows them at once; the reader — the translators it read); ``phone`` — the phone's page."""
    pairs = [("url", url)]
    if branch:
        pairs.append(("b", str(branch)[:40]))
    if cover:
        pairs.append(("c", cover))
    if rating:
        pairs.append(("r", f"{float(rating):.1f}"))
    if name:
        pairs.append(("t", name[:200]))
    return ("/m" if phone else "") + f"/{kind if kind in KINDS else 'manga'}/title?" + urlencode(pairs, quote_via=quote)


def gallery_of(url: str) -> int:
    """The number of the one gallery of nhentai ``url`` names, else 0."""
    from core import manga_download as MD
    if "nhentai" not in str(url or "").lower():
        return 0
    ids, _bad = MD.gallery_ids(url)
    return int(ids[0]) if len(ids) == 1 else 0


def opens(url: str) -> bool:
    """A link this page can show: a title of a site of manga, a gallery of nhentai."""
    from core import manhwa_sources as S
    return bool(gallery_of(url)) or S.source_for(url)[0] is not None


def _frame(kind: str, title: str, body: str, phone: bool = False, status: int = 200) -> Response:
    """In the tab of the feed: its look, its search, its section bar with «Лента» marked; (v5.44.105) the phone's —
    in its frame: back to its «Лента» (the script goes back in the history when the feed was the page before)."""
    if phone:
        from webui.pages import phone as P
        return P.page(title, body, status, active=kind, heading=HEADS[kind], back=f"/m/{kind}/feed",
                      top=P.mode_switch(), scripts=("title",), kind="m-title-page",
                      head='<link rel="stylesheet" href="/static/title.css">')
    if kind == "manga":
        from webui.pages import manga
        tabs = tuple((label, link, key == "feed") for key, label, link in manga.TABS)
        return Response.html(page_frame(title, body, active="manga", subnav=False, kind="title", tabs=tabs,
                                        top_search=manga._search_form("", "newest"), assets=("title",)))
    from webui.pages import manhwa
    tabs = tuple((label, link, key == "feed") for key, label, link in manhwa.TABS)
    return Response.html(page_frame(title, body, active="manhwa", subnav=False, kind="title", tabs=tabs,
                                    top_search=manhwa._search_form(""), assets=("title",)))


def _rating(text: str) -> float:
    try:
        value = float(str(text or "").replace(",", "."))
    except ValueError:
        return 0.0
    return value if 0 < value <= 10 else 0.0


def _phone_hidden(kind: str, mode: str, reason: str = "") -> str:
    """(v5.44.105) The words of a title the mode hides, as the phone's pages say them (its «Показать всё»)."""
    from webui import content_mode
    if mode == "sfw":
        why = f"Включён режим «SFW», а {WHAT[kind]} — хентай" + (f" ({reason})" if reason else "")
    else:
        why = f"Включён режим «NSFW» — видно только взрослое, а {WHAT[kind]} не помечена как хентай"
    return (f'<section class="m-hello" data-hidden="{"hentai" if mode == "sfw" else "safe"}"><h2>Скрыто в режиме '
            f'«{content_mode.LABELS[mode]}»</h2><p>{esc(why)}.</p><p><button type="button" class="m-button" '
            f'data-mode-set="all">Показать всё</button></p><p><a class="m-link" href="/m/{kind}/feed">К ленте</a></p>'
            f'</section>')


def page(req: Request, kind: str, phone: bool = False) -> Response:
    from core import manhwa_sources as S
    from webui import content_mode
    url = req.arg("url").strip()[:2000]
    back = ("/m" if phone else "") + f"/{kind}/feed"
    gallery = gallery_of(url) if url else 0
    source = S.source_for(url)[0] if url and not gallery else None
    if not gallery and source is None:
        words = "Эту ссылку программа не знает: нужна страница тайтла на сайте манги или галерея nhentai."
        if phone:
            body = (f'<section class="m-hello"><h2>Не открылось</h2><p>{words}</p><p><a class="m-button m-quiet" '
                    f'href="{esc(back)}">К ленте</a></p></section>')
            return _frame(kind, "Не открылось", body, phone=True, status=404)
        body = (f'<div class="tt tt-problem"><a class="tt-back" href="{esc(back)}" data-tt-back>← Лента</a>'
                f'<h1>Не открылось</h1><p>{words}</p></div>')
        return Response.html(page_frame("Не открылось", body, active=kind, subnav=False, kind="title",
                                        assets=("title",)), status=404)
    if (gallery or getattr(source, "adult_only", False)) and not content_mode.shows(True):
        if phone:
            return _frame(kind, "Скрыто", _phone_hidden(kind, content_mode.mode(), "сайт хентая"), phone=True)
        body = content_mode.hidden_item(active=kind, what=WHAT[kind], reason="сайт хентая", hentai=True, back=back,
                                        back_label="К ленте")
        return _frame(kind, "Скрыто", body)
    name = req.arg("t").strip()[:200]
    cover = req.arg("c").strip()[:64]
    rating = _rating(req.arg("r"))
    picture = (f'<img src="/api/catalog/cover?c={esc(quote(cover, safe=""))}" alt="" decoding="async" data-tt-tile>'
               if cover else "")
    rate = f'<span class="tt-rate">★ {f"{rating:.1f}".replace(".", ",")}</span>' if rating else ""
    site = "nhentai" if gallery else source.label
    branch = req.arg("b").strip()[:40]
    mode = content_mode.mode()
    hidden = ""                    # the mode's words, for a title the site calls erotica (or not, in «NSFW»)
    if mode != "all" and not gallery:
        words = (_phone_hidden(kind, mode) if phone else content_mode.hidden_item(
            active=kind, what=WHAT[kind], reason="", hentai=mode == "sfw", back=back, back_label="К ленте"))
        hidden = "<template data-tt-hidden>" + words + "</template>"
    places = ' data-reader="/m/online" data-get="/m/get"' if phone else ""     # (v5.44.105) the phone's pages
    body = f"""<div class="tt" id="title-page" data-url="{esc(url)}" data-kind="{esc(kind)}" data-gallery="{gallery or ''}"
  data-rating="{rating or ''}" data-back="{esc(back)}" data-site="{esc(site)}" data-branch="{esc(branch)}"
  data-mode="{esc(mode)}" data-name="{esc(name)}"{places}>
  <a class="tt-back" href="{esc(back)}" data-tt-back>← Лента</a>
  <section class="tt-hero" aria-busy="true" aria-labelledby="tt-name">
    <div class="tt-cover" data-tt-cover>{picture}</div>
    <div class="tt-about" data-tt-about>
      <p class="tt-facts" data-tt-facts>{esc(site)}</p>
      <h1 class="tt-name" id="tt-name" data-tt-name>{esc(name or "Тайтл")}</h1>
      <p class="tt-alt" data-tt-alt hidden></p>
      <div class="tt-numbers" data-tt-numbers>{rate}</div>
      <p class="tt-wait" data-tt-wait role="status">Читаю тайтл с сайта {esc(site)}…</p>
    </div>
  </section>
  <section class="tt-list" data-tt-list hidden aria-labelledby="tt-list-head"></section>
</div>{hidden}"""
    return _frame(kind, name or HEADS[kind], body, phone=phone)
