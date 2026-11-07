"""(v5.44.82) «Читать» a title of the sites without downloading it (``core/manga_online.py``): the program's own reader
with the site's pictures through the program; the site is not opened.

* ``/online?url=<the title's link>&ch=<n>`` (:func:`reader`; ``&branch=`` — the card's translators; ``&p=`` — the page,
  ``last`` — the chapter's last, ``end`` — its end screen) — on the computer, in the frame of the title's kind;
  ``/m/online`` (:func:`phone_reader`) — on the phone (``/online`` on the phone goes there — ``phone.TWINS``).  ``n`` —
  the chapter's tile on the card (from 1).
* ``/api/online/chapter`` — the next chapter for the strip (as ``/api/manhwa/chapter/<id>`` answers); ``/api/online/pic``
  — a page's picture (a media path: ``server.MEDIA_PATHS``); ``/api/online/thumb`` — (v5.44.110) its small copy for
  «Лента страниц»; ``/api/online/progress`` — the place (the last chapter of the title, its page); ``/api/online/last``
  — it, for the card's «Продолжить»; ``/api/online/mode`` — the way the title is read.
* «SFW»: a title the program takes for hentai is not shown.
* (v5.44.117) the reader is the program's one reader (``webui/pages/reader.py``, ``static/reader.js``; the owner's
  approved mockup B): «По страницам» (left to right, right to left) or «Прокруткой» (the strip of «Манхва»: the next
  chapter comes by itself below), the way kept for the title (``PROGRESS.put_mode``), the first time — by its kind
  (``manga_online.auto_mode``: manhwa — the strip, manga — the pages left to right).  Until then the computer and the
  phone had three readers of their own here (the strip, the reader of «Манга», ``online.js``).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from urllib.parse import quote

from webui.html import esc
from webui.server import Request, Response

MAX_URL = 2000
THUMB_WIDTH = 168                 # px of a page in the strip of pages (it shows them 84 px wide)
THUMBS_KEPT = 600                 # small copies kept in memory
_THUMBS: "OrderedDict[tuple, bytes]" = OrderedDict()
_THUMBS_LOCK = threading.Lock()


def register(router) -> None:
    router.add("GET", r"/online", reader)
    router.add("GET", r"/m/online", phone_reader)
    router.add("GET", r"/api/online/chapter", chapter_json)
    router.add("GET", r"/api/online/pic", picture)
    router.add("GET", r"/api/online/thumb", thumb)          # (v5.44.110) a page in the strip of the pages
    router.add("POST", r"/api/online/progress", progress)
    router.add("GET", r"/api/online/last", last)
    router.add("POST", r"/api/online/mode", mode)


def _args(req: Request) -> tuple:
    url = str(req.arg("url") or "").strip()[:MAX_URL]
    branch = str(req.arg("branch") or "").strip()[:40]
    return url, branch


def _q(url: str, branch: str) -> str:
    return "url=" + quote(url, safe="") + (f"&branch={quote(branch, safe='')}" if branch else "")


def _hidden(reading) -> bool:
    """The mode hides the title: «SFW» — the program takes it for hentai (``content_marks.manhwa_reason``: the site
    calls it erotica, a genre or tag of hentai, a site of hentai) — (v5.44.103) not «18+» alone: «Поднятие уровня в
    одиночку» is 18+ on Remanga, «Лента» and the title's page show it in «SFW», the reader refused it; «NSFW» — a title
    that is not hentai."""
    from types import SimpleNamespace
    from core import content_marks
    from webui import content_mode
    adult = bool(content_marks.manhwa_reason(SimpleNamespace(info=reading.title.info())))
    return not content_mode.shows(adult)


def _hidden_words() -> str:
    from webui import content_mode
    if content_mode.mode() == "nsfw":
        return "Этот тайтл не хентай, а включён режим «NSFW» — переключите на «Всё», чтобы читать"
    return "Этот тайтл — хентай, а включён режим «SFW» — переключите на «Всё», чтобы читать"


def _open(req: Request, *, hidden_too: bool = False):
    """(the reading, n, the error page's words) of the request; ``hidden_too`` — (v5.44.116) a title the mode hides
    comes back too, with its words (its page: the frame of its kind, «К тайтлу»)."""
    from core import manga_online as O
    url, branch = _args(req)
    if not url:
        return None, 0, "Не указан тайтл"
    try:
        n = max(1, int(req.arg("ch") or "1"))
    except ValueError:
        n = 1
    try:
        reading = O.ONLINE.title(url, req.settings, branch)
    except O.OnlineError as exc:
        return None, n, str(exc)
    if _hidden(reading):
        return (reading if hidden_too else None), n, _hidden_words()
    if not reading.readable(n):                          # a paid chapter nobody has free: the next one that is
        n = reading.neighbour(n, 1) or reading.neighbour(n, -1) or n
    return reading, n, ""


def _pages(reading, n: int, req: Request) -> tuple:
    """([{src, w, h, thumb}] of chapter ``n`` — the addresses of the program, never the site's; the site that gives
    them)."""
    from core import manga_online as O
    _r, pages = O.ONLINE.chapter(reading.url, n, req.settings, reading.branch)
    q = _q(reading.url, reading.branch)
    out = [{"src": f"/api/online/pic?{q}&ch={n}&p={k}", "w": max(0, int(p.width or 0)), "h": max(0, int(p.height or 0)),
            "thumb": f"/api/online/thumb?{q}&ch={n}&p={k}"} for k, p in enumerate(pages.pictures, 1)]
    return out, pages


def online_link(url: str, branch: str, phone: bool = False) -> str:
    """The address of a chapter of a title read without downloading, but its number (``…&ch=``)."""
    return f"{'/m' if phone else ''}/online?{_q(url, branch)}&ch="


def _follows(req: Request, reading) -> bool:
    """The title is downloaded and its new chapters come by themselves at night (its folder's ``source.json``)."""
    from webui.pages import manhwa as MH
    folder = str(getattr(reading.found, "folder", "") or "")
    return MH.follows(req, MH.site_of(folder)) if folder else False


def book(req: Request, reading, n: int, *, phone: bool = False, asked: "str | None" = None):
    """(v5.44.117) Chapter ``n`` of a site's title for the one reader (``webui/pages/reader.py``): the card's chapters
    (``Found.takes(True)`` — paid everywhere: no link; in the folder; another site's copy), the chapter's pages through
    the program, where it was left (the title's last chapter), the way it is read."""
    from core import manga_online as O
    from webui.pages import reader as R
    link = online_link(reading.url, reading.branch, phone)
    place = O.PROGRESS.of(reading.url)
    read_ids = {str(x) for x in place.get("read") or []}       # (v5.44.119) every chapter read to its end
    found = reading.found
    rows = []
    for k, take in enumerate(reading.takes, 1):
        readable = take.source is not None
        href = f"{link}{k}" if readable else ""
        try:
            had = bool(found.is_had(take.ch))
        except (AttributeError, TypeError):
            had = False
        rows.append(R.Row(key=str(k), label=take.label, name=take.name if take.ch.number else "", href=href,
                          start=f"{href}&p=1" if href else "", last=f"{href}&p=last" if href else "",
                          read=bool((place.get("ch") == k and place.get("done")) or str(take.id) in read_ids),
                          where="site" if readable else "paid",
                          site=take.source.label if readable and take.helped else "",
                          free_at="" if readable else R.day_of(take.ch.free_at), in_folder=had,
                          tome=int(take.ch.tome or 0), number=R.number_of(take.ch.number)))
    pages, given = _pages(reading, n, req)
    rows[n - 1].pages = len(pages)
    chosen = str(place.get("mode") or "")
    chosen = chosen if chosen in O.MODES else ""
    way = chosen or O.auto_mode(reading, given.pictures)
    mine = place.get("ch") == n
    saved = (int(place.get("page") or 0), float(place.get("part") or 0)) if mine else (0, 0.0)
    page, part = R.place_of(req, saved, len(pages), read=bool(mine and place.get("done")), asked=asked)
    if way == "strip":
        page = min(page, max(0, len(pages) - 1))
    q = _q(reading.url, reading.branch)
    return R.Book(src="site", kind="manga" if _is_manga(reading) else "manhwa", phone=phone, name=reading.title.title,
                  home=_get_link(reading, phone), rows=rows, at=n - 1, pages=pages, href=f"{link}{n}",
                  base=link + "{key}", progress=f"/api/online/progress?{q}", mode_api=f"/api/online/mode?{q}",
                  chapter_api=f"/api/online/chapter?{q}&ch=" + "{key}" + ("&m=1" if phone else ""),
                  mode=way, chosen=chosen, page=page, part=part, where="site",
                  where_words=f"с {given.source.label}, без скачивания", follow=_follows(req, reading),
                  dir_default=chosen if chosen in ("ltr", "rtl") else "ltr")


def _get_link(reading, phone: bool = False) -> str:
    """The page of this title — (v5.44.102) its page in the tab of its kind (``webui/pages/title.py``), with the
    translators read and its name; (v5.44.105) on the phone — the phone's page of the title (until then its card of
    «Скачать по ссылке»)."""
    from webui.pages import title as T
    return T.href(getattr(reading.found, "kind", "") or "manhwa", reading.title.url,
                  branch=getattr(reading, "branch", "") or "", name=getattr(reading.title, "title", "") or "",
                  phone=phone)


def _problem(req: Request, words: str, *, phone: bool, reading=None, n: int = 0, hidden: bool = False) -> Response:
    """A chapter (or a title) that did not open: why, and the ways on — (v5.44.103) a chapter of a title: the next one
    that can be read and «К тайтлу» (its page — on the phone its card); a title the mode hides: «Показать всё» —
    (v5.44.116) on the phone «К тайтлу» too; on the computer the frame of its kind and its feed (:func:`_frame`)."""
    links = []
    if reading is not None and not hidden:
        after = reading.neighbour(n, 1) if n else 0
        if after:
            links.append((f"{'/m/online' if phone else '/online'}?{_q(reading.url, reading.branch)}&ch={after}",
                          f"Следующая: {reading.label(after)}", True))
        links.append((_get_link(reading, phone), "К тайтлу", not after))
    if phone:
        from webui.pages import phone as P
        if hidden:      # (v5.44.116) the phone's «Показать всё» (its own mode, as its other pages) and its title's page
            ways = ('<button type="button" class="m-button" data-mode-set="all">Показать всё</button> '
                    + (f'<a class="m-button m-quiet" href="{esc(_get_link(reading, True))}">К тайтлу</a>'
                       if reading is not None else ""))
        else:
            links = links or [("/m/get", "Скачать по ссылке", False)]
            ways = "".join(f'<a class="m-button{"" if main else " m-quiet"}" href="{esc(href)}">{esc(label)}</a> '
                           for href, label, main in links)
        body = f'<div class="m-empty"><p>{esc(words)}</p><p>{ways}</p></div>'
        return P.page("Не открылось", body, status=404, top=P.mode_switch() if hidden else "")
    from webui import content_mode
    if hidden:
        feed = "/manga/feed" if reading is not None and _is_manga(reading) else "/manhwa/feed"
        ways = content_mode._actions(f'<a class="mh-btn" href="{feed}">К ленте</a>')
    else:
        links = links or [("/get", "Скачать по ссылке", False)]
        ways = "<p>" + "".join(f'<a class="mh-btn{" primary" if main else ""}" href="{esc(href)}">{esc(label)}</a> '
                               for href, label, main in links) + "</p>"
    body = f'<div class="mh-online-problem"><h1>Не открылось</h1><p>{esc(words)}</p>{ways}</div>'
    return _frame(req, reading, "Не открылось", body, kind="manhwa", assets=("manhwa", "reader"), search=False,
                  status=404)


def _is_manga(reading) -> bool:
    """A manga — by its type, also when the manga folder is not chosen and its chapters would go to the manhwa folder:
    its pages are in the frame of «Манга» (the others — «Манхва»'s)."""
    from core.manhwa_download import kind_of
    return getattr(reading.found, "kind", "") == "manga" or kind_of(reading.title) == "manga"


def _frame(req: Request, reading, title: str, body: str, *, kind: str, assets: tuple, search: bool = True,
           status: int = 200) -> Response:
    """A page of the reader on the computer in its tab's frame: (v5.44.116; the review — the strip and «Не открылось»
    were «Манхва»'s for a manga too) «Манга» for a manga (:func:`_is_manga`), else — and when the title is not known —
    «Манхва»; ``assets`` — the stylesheets and scripts the page takes whatever its frame."""
    from webui.html import page_frame
    if reading is not None and _is_manga(reading):
        from webui.pages import manga as MG
        return Response.html(page_frame(title, body, active="manga", subnav=False, kind=kind, assets=assets,
                                        top_search=MG._search_form("", "newest") if search else "", **MG._prefs(req)),
                             status=status)
    from webui.pages import manhwa as MH
    return Response.html(page_frame(title, body, active="manhwa", subnav=False, kind=kind, assets=assets,
                                    top_search=MH._search_form("") if search else ""), status=status)


def _small(data: bytes) -> bytes | None:
    """A page's small copy for the strip of pages (JPEG :data:`THUMB_WIDTH` wide), None — the picture does not open."""
    import io
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            img.draft("RGB", (THUMB_WIDTH * 2, THUMB_WIDTH * 8))
            img = img.convert("RGB")
            img.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 6))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=82, optimize=True)
            return out.getvalue()
    except Exception:  # noqa: BLE001 - a picture Pillow does not open (AVIF): the page itself goes
        return None


def thumb(req: Request) -> Response:
    """(v5.44.110) A page of a site's chapter, small — for the strip of pages (the page fetched once by the program,
    kept as :func:`picture` keeps it; its small copy kept in memory)."""
    from core import manga_online as O
    url, branch = _args(req)
    try:
        n, p = int(req.arg("ch") or "0"), int(req.arg("p") or "0")
    except ValueError:
        return Response.not_found("Нет такой страницы")
    if not url or n < 1 or p < 1:
        return Response.not_found("Нет такой страницы")
    key = (url, branch, n, p)
    try:
        reading = O.ONLINE.title(url, req.settings, branch)
        if _hidden(reading):
            return Response.not_found("Скрыто режимом")
        with _THUMBS_LOCK:
            kept = _THUMBS.get(key)
            if kept is not None:
                _THUMBS.move_to_end(key)
        if kept is None:
            data, kind = O.ONLINE.picture(url, n, p, req.settings, branch)
            small = _small(data)
            if small is None:
                return Response(200, data, kind, {"Cache-Control": "private, max-age=3600"})
            with _THUMBS_LOCK:
                _THUMBS[key] = kept = small
                while len(_THUMBS) > THUMBS_KEPT:
                    _THUMBS.popitem(last=False)
    except O.OnlineError as exc:
        return Response(502, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
    return Response(200, kept, "image/jpeg", {"Cache-Control": "private, max-age=3600"})


def _read(req: Request, *, phone: bool) -> Response:
    """A chapter of a site's title in the one reader (v5.44.117; ``webui/pages/reader.py``): a page open — what was
    read (the strip keeps its place itself); the computer's page — in the frame of the title's kind (:func:`_frame`)."""
    from core import manga_online as O
    from webui.pages import reader as R
    reading, n, words = _open(req, hidden_too=True)
    if reading is None or words:
        return _problem(req, words, phone=phone, reading=reading, hidden=words == _hidden_words())
    try:
        found = book(req, reading, n, phone=phone)
    except O.OnlineError as exc:
        return _problem(req, str(exc), phone=phone, reading=reading, n=n)
    if not found.pages:
        return _problem(req, "Глава не открылась — у неё нет страниц", phone=phone, reading=reading, n=n)
    if found.mode != "strip":
        total = len(found.pages)
        O.PROGRESS.put(reading.url, reading, n, min(found.page, total - 1), 0.0, found.page >= total - 1, pages=total)
    return R.page(req, found, frame=lambda title, body, kind, assets: _frame(req, reading, title, body, kind=kind,
                                                                            assets=assets))


def reader(req: Request) -> Response:
    return _read(req, phone=False)


def phone_reader(req: Request) -> Response:
    return _read(req, phone=True)


def chapter_json(req: Request) -> Response:
    """The next chapter for the strip: as ``/api/manhwa/chapter/<id>`` answers, with the words after it
    (``reader.chapter_reply``); ``&m=1`` — the phone's addresses."""
    from core import manga_online as O
    from webui.pages import reader as R
    reading, n, words = _open(req)
    if reading is None:
        return Response.json({"ok": False, "error": words}, 404)
    try:
        found = book(req, reading, n, phone=req.arg("m") == "1", asked="1")
    except O.OnlineError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 502)
    reply = R.chapter_reply(found, found.at, found.pages)
    reply["title"] = "online"
    return Response.json(reply)


def picture(req: Request) -> Response:
    """A page's picture: fetched by the program from the site (its own Referer), kept a while."""
    from core import manga_online as O
    url, branch = _args(req)
    try:
        n, p = int(req.arg("ch") or "0"), int(req.arg("p") or "0")
    except ValueError:
        return Response.not_found("Нет такой страницы")
    if not url or n < 1 or p < 1:
        return Response.not_found("Нет такой страницы")
    try:
        reading = O.ONLINE.title(url, req.settings, branch)
        if _hidden(reading):
            return Response.not_found("Скрыто режимом")
        data, kind = O.ONLINE.picture(url, n, p, req.settings, branch)
    except O.OnlineError as exc:
        return Response(502, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
    return Response(200, data, kind, {"Cache-Control": "private, max-age=3600"})


def progress(req: Request) -> Response:
    """The place in a title read without downloading: its chapter (``chapter`` — n), page, how much of it."""
    from core import manga_online as O
    url, branch = _args(req)
    body = req.json()
    try:
        n = int(body.get("chapter") or 0)
        page = int(body.get("page") or 0)
        part = float(body.get("part") or 0)
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Не то место"}, 400)
    try:
        reading = O.ONLINE.title(url, req.settings, branch)
    except O.OnlineError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 404)
    if not 1 <= n <= len(reading.takes):
        return Response.json({"ok": False, "error": "Такой главы нет"}, 404)
    O.PROGRESS.put(url, reading, n, page, part, bool(body.get("done")), pages=O.ONLINE.pages_known(url, n, branch))
    return Response.json({"ok": True})


def mode(req: Request) -> Response:
    """{mode} — the way this title is read ("" — «авто»)."""
    from core import manga_online as O
    url, _branch = _args(req)
    value = str(req.json().get("mode") or "")
    if value and value not in O.MODES:
        return Response.json({"ok": False, "error": "Такого способа нет"}, 400)
    try:
        O.PROGRESS.put_mode(url, value)
    except O.OnlineError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 400)
    return Response.json({"ok": True, "mode": value})


def last(req: Request) -> Response:
    """The last chapter read of a title (the card's «Продолжить»): {ch, label, done} — or {}."""
    from core import manga_online as O
    url, _branch = _args(req)
    place = O.PROGRESS.of(url) if url else {}
    return Response.json({"ok": True, "last": {k: place[k] for k in ("ch", "label", "done", "page") if k in place}})
