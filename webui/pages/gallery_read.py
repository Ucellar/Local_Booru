"""(v5.44.148) A gallery of nhentai in the program's one reader, without downloading it (``core/gallery_read.py``).

The owner (10.11.2026): «на nhentai прочтение через программу не работает там просто не кликабельные картинки».

* ``/manga/read?g=<number>`` (the phone — ``/m/manga/read``) — the gallery in the reader (``webui/pages/reader.py``):
  one part, its pages through the program; ``&p=`` — the page to open (else where it was left);
* ``/api/gallery/pic?g=&p=`` — a page's picture (``gallery_read.picture``);
* ``/api/gallery/progress?g=`` — where it was left ({page, part, done}), ``/api/gallery/mode?g=`` — how it is read.

Not in «SFW» (as the gallery's card); a gallery a gate of the program closes is not read — the gate's words.
"""
from __future__ import annotations

from webui.html import esc
from webui.server import Request, Response

MAX_NUMBER = 9_999_999


def register(router) -> None:
    router.add("GET", r"/manga/read", reader)
    router.add("GET", r"/m/manga/read", phone_reader)
    router.add("GET", r"/api/gallery/pic", picture)
    router.add("POST", r"/api/gallery/progress", progress)
    router.add("POST", r"/api/gallery/mode", mode)


def link(number: int, phone: bool = False) -> str:
    """The gallery's address in the reader, without the page (``&p=`` is added)."""
    return f"{'/m' if phone else ''}/manga/read?g={int(number)}"


def _number(req: Request) -> int:
    number = req.int_arg("g", 0, lo=0)
    return number if 0 < number <= MAX_NUMBER else 0


def _hidden() -> bool:
    from webui import content_mode
    return content_mode.mode() == "sfw"


HIDDEN = "Это галерея nhentai, а включён режим «SFW» — они не показываются. «Показать всё» — и она откроется."


def _home(number: int, name: str, phone: bool) -> str:
    from core import nhentai as N
    from webui.pages import title as T
    return T.href("manga", N.link_of(number), name=name, phone=phone)


def book(req: Request, found, *, phone: bool = False):
    """The gallery for the reader: one part, its pages through the program (their sizes as the site tells them, the
    small ones — the site's own, through the program too), where it was left, the way it is read (the owner's choice
    for it, else as a manga: the pages left to right, a strip for tall ones)."""
    from core import catalog as C, gallery_read as GR, manga_online as O, nhentai as N
    from webui.pages import reader as R
    number = found.id
    address = link(number, phone)
    place = GR.place(number)
    sizes = GR.sizes(found)
    site = N.link_of(number)
    pages = [{"src": f"/api/gallery/pic?g={number}&p={k}", "w": w, "h": h,
              "thumb": f"/api/catalog/cover?c={C.cover_id(small, site)}" if small
              else f"/api/gallery/pic?g={number}&p={k}"}
             for k, ((w, h), small) in enumerate(zip(sizes, GR.thumbs(found)), 1)]
    chosen = str(place.get("mode") or "")
    chosen = chosen if chosen in O.MODES else ""
    way = chosen or R.auto("манга", True, [s for s in sizes[:5] if all(s)])
    saved = (int(place.get("page") or 0), float(place.get("part") or 0))
    page, part = R.place_of(req, saved, len(pages), read=bool(place.get("done")))
    if way == "strip":
        page = min(page, max(0, len(pages) - 1))
    name = found.title
    row = R.Row(key="1", label=name, href=address, start=f"{address}&p=1", last=f"{address}&p=last",
                pages=len(pages), read=bool(place.get("done")), where="site", site=GR.LABEL)
    return R.Book(src="site", kind="manga", phone=phone, name=name, home=_home(number, name, phone), rows=[row], at=0,
                  pages=pages, href=address, base=address, progress=f"/api/gallery/progress?g={number}",
                  mode_api=f"/api/gallery/mode?g={number}", mode=way, chosen=chosen, page=page, part=part,
                  where="site", where_words=f"с {GR.LABEL}, без скачивания", single=True,
                  dir_default=chosen if chosen in ("ltr", "rtl") else "ltr")


def _frame(req: Request, title: str, body: str, *, kind: str, assets: tuple, search: bool = True,
           status: int = 200) -> Response:
    """A page of the reader on the computer — in the frame of «Манга» (its look and menu)."""
    from webui.html import page_frame
    from webui.pages import manga as MG
    return Response.html(page_frame(title, body, active="manga", subnav=False, kind=kind, assets=assets,
                                    top_search=MG._search_form("", "newest") if search else "", **MG._prefs(req)),
                         status=status)


def _problem(req: Request, words: str, *, phone: bool, number: int = 0, hidden: bool = False,
             closed: bool = False) -> Response:
    """A gallery that did not open: why, and the ways on — its page (not of one a gate closed), the feed; hidden by
    the mode — «Показать всё»."""
    home = _home(number, "", phone) if number and not closed else ""
    feed = ("/m" if phone else "") + "/manga/feed"
    if phone:
        from webui.pages import phone as P
        if hidden:
            ways = ('<button type="button" class="m-button" data-mode-set="all">Показать всё</button> '
                    f'<a class="m-button m-quiet" href="{esc(feed)}">К ленте</a>')
        else:
            ways = ((f'<a class="m-button" href="{esc(home)}">К галерее</a> ' if home else "")
                    + f'<a class="m-button{" m-quiet" if home else ""}" href="{esc(feed)}">К ленте</a>')
        body = f'<div class="m-empty"><p>{esc(words)}</p><p>{ways}</p></div>'
        return P.page("Не открылось", body, status=404, top=P.mode_switch() if hidden else "")
    if hidden:
        from webui import content_mode
        ways = content_mode._actions(f'<a class="mh-btn" href="{esc(feed)}">К ленте</a>')
    else:
        ways = ("<p>" + (f'<a class="mh-btn primary" href="{esc(home)}">К галерее</a> ' if home else "")
                + f'<a class="mh-btn{"" if home else " primary"}" href="{esc(feed)}">К ленте</a></p>')
    body = f'<div class="mh-online-problem"><h1>Не открылось</h1><p>{esc(words)}</p>{ways}</div>'
    return _frame(req, "Не открылось", body, kind="manhwa", assets=("manhwa", "reader"), search=False, status=404)


def _read(req: Request, *, phone: bool) -> Response:
    from core import gallery_read as GR
    from webui.pages import reader as R
    number = _number(req)
    if not number:
        return _problem(req, "Нет номера галереи", phone=phone)
    if _hidden():
        return _problem(req, HIDDEN, phone=phone, number=number, hidden=True)
    try:
        found = GR.gallery(number)
    except GR.Closed as exc:
        return _problem(req, str(exc), phone=phone, number=number, closed=True)
    except GR.ReadError as exc:
        return _problem(req, str(exc), phone=phone, number=number)
    made = book(req, found, phone=phone)
    if not made.pages:
        return _problem(req, "У галереи нет страниц", phone=phone, number=number)
    if made.mode != "strip":                         # a page open is a page read (the strip keeps its place itself)
        total = len(made.pages)
        GR.put_place(number, found.title, min(made.page, total - 1), 0.0, made.page >= total - 1, pages=total)
    return R.page(req, made, frame=lambda title, body, kind, assets: _frame(req, title, body, kind=kind,
                                                                            assets=assets))


def reader(req: Request) -> Response:
    return _read(req, phone=False)


def phone_reader(req: Request) -> Response:
    return _read(req, phone=True)


def picture(req: Request) -> Response:
    """A page's picture: fetched by the program from the site's picture servers, kept a while."""
    from core import gallery_read as GR
    number = _number(req)
    page = req.int_arg("p", 0, lo=0)
    if not number or page < 1:
        return Response.not_found("Нет такой страницы")
    if _hidden():
        return Response.not_found("Скрыто режимом")
    try:
        data, kind = GR.picture(number, page)
    except GR.Closed:
        return Response.not_found("Нет такой страницы")
    except GR.ReadError as exc:
        return Response(502, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
    return Response(200, data, kind, {"Cache-Control": "private, max-age=3600"})


def progress(req: Request) -> Response:
    """{page (from 0), part, done} — where the gallery was left."""
    from core import gallery_read as GR
    number = _number(req)
    body = req.json()
    try:
        page = int(body.get("page") or 0)
        part = float(body.get("part") or 0)
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Не то место"}, 400)
    if not number or _hidden():
        return Response.json({"ok": False, "error": "Такой галереи нет"}, 404)
    try:
        found = GR.gallery(number)
    except GR.ReadError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 404)
    GR.put_place(number, found.title, min(max(0, page), max(0, found.pages - 1)), part, bool(body.get("done")),
                 pages=found.pages)
    return Response.json({"ok": True})


def mode(req: Request) -> Response:
    """{mode} — the way this gallery is read ("" — as a manga is, by itself)."""
    from core import gallery_read as GR, manga_online as O
    number = _number(req)
    value = str(req.json().get("mode") or "")
    if value and value not in O.MODES:
        return Response.json({"ok": False, "error": "Такого способа нет"}, 400)
    if not number:
        return Response.json({"ok": False, "error": "Такой галереи нет"}, 404)
    GR.put_mode(number, value)
    return Response.json({"ok": True, "mode": value})
