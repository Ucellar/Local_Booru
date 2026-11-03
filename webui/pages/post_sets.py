"""Сборки of cards (``core/card_sets.py``) on the pages: the box over a card's picture — its сборка the way Danbooru
shows a parent and its children (the green box «У этой карточки 3 дочерних»), its cards' previews in order, this card
marked — with «изменить» (move, take out, name, take apart, add cards); and the actions of the gallery's choice
(«Собрать в одну сборку», «Разобрать сборки»).

``/api/post/<id>/sets`` — the card's сборка as data; ``/api/sets/{remove,dissolve,move,rename,gather}`` — the edits.

(v5.44.1) A card of no сборка whose post has a family on a site the grabber searches — a child (its parent known), or
any other (its children asked) — gets the same box, hidden until the site shows parts the library has not
(``card_sets.lone_family``, ``set_remote.family``): one picture of three of a family was alone in the library.

(v5.30, «серия» and «одна работа» v5.32) The parts of a сборка the library has not (``core/set_remote.py``):
``/api/post/<id>/sets/remote`` — their tiles with «api» and where they go among the cards (post_sets.js puts them in),
``/sets/media`` — their pictures, ``/post/remote`` — the page of one (its picture, its tags, «Скачать»),
``/api/sets/remote/download`` and ``/api/sets/remote/task`` — the download through the program's browser import and
how it goes.
"""
from __future__ import annotations

import logging
import sqlite3

from webui import data
from webui.html import esc, url
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.webui")


def register(router) -> None:
    router.add("GET", r"/api/post/(?P<id>\d+)/sets", sets)
    router.add("GET", r"/api/post/(?P<id>\d+)/sets/remote", remote_tiles)
    router.add("GET", r"/sets/media", remote_media)
    router.add("GET", r"/post/remote", remote_post)
    router.add("POST", r"/api/sets/remote/download", remote_download)
    router.add("GET", r"/api/sets/remote/task", remote_task)
    router.add("POST", r"/api/sets/remove", remove)
    router.add("POST", r"/api/sets/dissolve", dissolve)
    router.add("POST", r"/api/sets/move", move)
    router.add("POST", r"/api/sets/rename", rename)
    router.add("POST", r"/api/sets/gather", gather)


def enabled(settings: dict) -> bool:
    """«Настройки → Галерея → Сборки над картинкой карточки»."""
    return bool((settings or {}).get("post_sets_enabled", True))


REMOTE_KINDS = ("parent", "work", "series")      # a сборка the person made of chosen cards is not looked for


def remote_enabled(settings: dict, found: dict | None) -> bool:
    """The сборка's missing parts are asked of its site: a сборка the program made (a parent and its children, one
    work, a series), and «Настройки → Галерея → Недостающие части сборки — с сайта» on."""
    return bool(found and found.get("kind") in REMOTE_KINDS and (settings or {}).get("card_sets_remote", True))


# ── words ──────────────────────────────────────────────────────────────────────────────
def plural(n: int, forms: tuple) -> str:
    """«1 карточка», «3 карточки», «5 карточек»."""
    rest10, rest100 = n % 10, n % 100
    form = forms[0] if rest10 == 1 and rest100 != 11 else forms[1] if 2 <= rest10 <= 4 and not 12 <= rest100 <= 14 else forms[2]
    return f"{n} {form}"


CARDS = ("карточка", "карточки", "карточек")
FILES = ("файл", "файла", "файлов")
TILES = ("плитка", "плитки", "плиток")
PAGES = ("страница", "страницы", "страниц")


def children(n: int) -> str:
    return f"{n} дочерний" if n % 10 == 1 and n % 100 != 11 else f"{n} дочерних"


def headline(found: dict, image_id: int) -> str:
    """What the box says first: «У этой карточки 3 дочерних», «Эта карточка — дочерняя…», «Серия: 12 карточек»…"""
    if found.get("lone"):                       # the rest of the family is on the site: «нет в библиотеке: N (api)»
        return "Эта карточка — дочерняя" if found.get("child") else "У этой карточки есть дочерние"
    count = len(found["members"])
    kind = found["kind"]
    parents = set(found.get("parents") or [])
    if kind == "parent":
        if image_id in parents:
            text = "У этой карточки " + children(count - len(parents))
        elif parents:
            others = count - len(parents) - 1
            text = "Эта карточка — дочерняя" + (f": у её родителя ещё {children(others)}" if others > 0 else "")
        else:
            text = "Дочерние одного поста: " + plural(count, CARDS)
    elif kind == "work":
        text = "Одна работа: " + plural(count, PAGES)
    elif kind == "series":
        text = "Серия: " + plural(count, CARDS)
    else:
        text = "Сборка: " + plural(count, CARDS)
    title = str(found.get("title") or "").strip()
    return f"«{title}» — {text[0].lower()}{text[1:]}" if title else text


def _grab_link(settings: dict, found: dict) -> str:
    """The grabber's search of all the parts of a parent post, on a site the grabber searches ("" — none)."""
    if found["kind"] != "parent" or not found.get("root"):
        return ""
    site, post = found["root"]
    try:
        from core.grabber.search import searchable_sites
        hosts = {host for host, _label in searchable_sites(settings)}
    except Exception as exc:  # the site table: no link then
        _LOG.debug("grabber sites: %s", exc)
        return ""
    return url("/grab", q=f"parent:{post}", sites=site) if site in hosts else ""


def lone(settings: dict, image_id: int) -> dict | None:
    """The family on a site of a card of no сборка (``card_sets.lone_family``) — when the missing parts are asked of
    the sites at all («Недостающие части сборки — с сайта»)."""
    from core import card_sets
    if not (settings or {}).get("card_sets_remote", True):
        return None
    try:
        from core.grabber.search import searchable_sites
        sites = [host for host, _label in searchable_sites(settings)]
    except Exception as exc:  # the site table: no family asked then
        _LOG.debug("grabber sites: %s", exc)
        return None
    return card_sets.lone_family(settings, image_id, sites)


def card_family(settings: dict, image_id: int) -> dict | None:
    """The card's сборка (``card_sets.set_of``), or its family on a site when it is in none (:func:`lone`)."""
    from core import card_sets
    found = card_sets.set_of(settings, image_id)
    return found if found is not None else lone(settings, image_id)


# ── the box over the picture ───────────────────────────────────────────────────────────
def box_html(req: Request, image_id: int, ctx: dict, index: int, *, remote_current: str = "") -> str:
    """The card's сборка over its picture ("" — no сборка, or switched off).  Drawn here; post_sets.js hides it,
    moves cards, takes them out, puts in the parts the library has not (``remote_current`` — the one whose page this
    is: /post/remote).  A card of no сборка — its family on a site (:func:`lone`): hidden until the site shows parts
    the library has not, nothing to change in it."""
    from webui import prefs
    if not enabled(req.settings):
        return ""
    try:
        found = card_family(req.settings, image_id)
    except sqlite3.Error as exc:                     # an old or locked base: the page goes on without it
        _LOG.debug("set of %s: %s", image_id, exc)
        return ""
    if not found or found["index"] < 0:
        return ""
    alone = bool(found.get("lone"))
    hidden = prefs.load().get("sets_box") == "hidden"
    parents = set(found.get("parents") or [])
    count = len(found["members"])
    items = []
    for member in found["members"]:
        mid = int(member["id"])
        current = mid == int(image_id) and not remote_current
        role = (" parent" if mid in parents else " child") if found["kind"] == "parent" else ""
        controls = "" if alone else (
            f'<span class="post-set-ctl">'
            f'<button type="button" data-set-act="left" title="Сдвинуть влево" aria-label="Сдвинуть влево">‹</button>'
            f'<button type="button" data-set-act="remove" title="Убрать из сборки" aria-label="Убрать из сборки">×</button>'
            f'<button type="button" data-set-act="right" title="Сдвинуть вправо" aria-label="Сдвинуть вправо">›</button>'
            f'</span>')
        href = url(f"/post/{mid}", i=index if index >= 0 else None, **ctx)
        what = "эта карточка" if current else ("родитель" if mid in parents else f"карточка {mid}")
        video = '<span class="post-set-play" aria-hidden="true">▶</span>' if member.get("is_video") else ""
        mark = ' aria-current="true"' if current else ""
        items.append(
            f'<div class="post-set-item{role}{" current" if current else ""}" data-id="{mid}">'
            f'<a href="{esc(href)}" title="{esc(what)}"{mark}>'
            f'<img src="{esc(data.media_url("thumb", member))}" alt="" loading="lazy" decoding="async">{video}</a>'
            f'{controls}</div>')
    grab = _grab_link(req.settings, found)
    grab_html = f' (<a href="{esc(grab)}">все части — в граббере</a>)' if grab else ""
    mine = (' <span class="post-set-mine" title="Эту сборку меняли вы: программа её не пересобирает, только добавляет '
            'новые части">✎</span>') if found["locked"] else ""
    add = url("/posts", add_to=found["id"])
    remote = ""
    if remote_enabled(req.settings, found):
        remote = (f' data-remote="{esc(url(f"/api/post/{int(image_id)}/sets/remote", i=index if index >= 0 else None, **ctx))}"'
                  + (f' data-remote-current="{esc(remote_current)}"' if remote_current else ""))
    edit = "" if alone else (
        f'<span class="post-set-edit-wrap"> · <button type="button" class="link post-set-edit" aria-pressed="false">'
        f'изменить</button></span>')
    tools = "" if alone else (
        f'<div class="post-set-tools" hidden>'
        f'<label>Название <input type="text" name="title" maxlength="200" value="{esc(found.get("title") or "")}" '
        f'placeholder="без названия"></label> <button type="button" data-set-act="rename">Сохранить</button> '
        f'<a class="post-set-add" href="{esc(add)}">+ добавить карточки</a> '
        f'<button type="button" class="danger" data-set-act="dissolve">Разобрать сборку</button> '
        f'<span class="action-status" role="status"></span>'
        f'<p class="hint">‹ › — переставить, × — убрать карточку из сборки (сама программа её больше в сборки не '
        f'положит). Сборку, которую вы изменили, программа не пересобирает — только добавляет в конец новые части.</p>'
        f'</div>')
    waiting = ' data-lone="1" hidden' if alone else ""
    return (
        f'<section class="post-sets{" collapsed" if hidden else ""}" data-set="{int(found["id"])}" '
        f'data-post="{int(image_id)}" data-kind="{esc(found["kind"])}"{remote}{waiting} aria-label="Сборка">'
        f'<p class="post-set-head"><span class="post-set-what">{esc(headline(found, int(image_id)))}</span>'
        f'{mine}<span class="post-set-place"> · {found["index"] + 1} из {count}</span>'
        f'<span class="post-set-missing" hidden></span>{grab_html} '
        f'<button type="button" class="link post-set-toggle" aria-expanded="{"false" if hidden else "true"}">'
        f'{"показать »" if hidden else "« скрыть"}</button>{edit}</p>'
        f'<div class="post-set-thumbs">{"".join(items)}</div>{tools}'
        f'</section><script src="/static/post_sets.js" defer></script>')


# ── data and edits ─────────────────────────────────────────────────────────────────────
def describe(settings: dict, image_id: int) -> dict | None:
    """The card's сборка as data: {"id", "kind", "title", "locked", "headline", "index", "count", "grab", "members":
    [{"id", "thumb", "current", "parent", "video"}]} or None."""
    from core import card_sets
    found = card_sets.set_of(settings, image_id)
    if not found or found["index"] < 0:
        return None
    parents = set(found.get("parents") or [])
    return {"id": found["id"], "kind": found["kind"], "title": found["title"], "locked": found["locked"],
            "headline": headline(found, int(image_id)), "index": found["index"], "count": len(found["members"]),
            "grab": _grab_link(settings, found),
            "members": [{"id": int(m["id"]), "thumb": data.media_url("thumb", m), "current": int(m["id"]) == int(image_id),
                         "parent": int(m["id"]) in parents, "video": bool(m.get("is_video"))} for m in found["members"]]}


def sets(req: Request) -> Response:
    image_id = int(req.params["id"])
    found = describe(req.settings, image_id) if enabled(req.settings) else None
    return Response.json({"ok": True, "set": found})


def _busy() -> str:
    from core.nomatch_manual import library_busy
    return library_busy()


def _ids(value) -> list:
    out = []
    for item in value if isinstance(value, list) else []:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in out:
            out.append(number)
    return out[:5000]


def _edit(req: Request, fn) -> Response:
    from core.card_sets import SetError
    busy = _busy()
    if busy:
        return Response.json({"ok": False, "error": busy}, 409)
    try:
        result = fn(req.json())
    except SetError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 400)
    except (TypeError, ValueError) as exc:
        return Response.json({"ok": False, "error": f"Неверный запрос: {exc}"}, 400)
    return Response.json({"ok": True, **result})


def remove(req: Request) -> Response:
    from core import card_sets
    return _edit(req, lambda body: card_sets.remove_cards(req.settings, _ids(body.get("ids"))))


def dissolve(req: Request) -> Response:
    """«Разобрать»: the сборки of ``sets`` and of the cards ``ids`` (a tile of the gallery is its сборка)."""
    from core import card_sets

    def run(body: dict) -> dict:
        wanted = _ids(body.get("sets"))
        for image_id in _ids(body.get("ids")):
            found = card_sets.set_of(req.settings, image_id)
            if found and found["id"] not in wanted:
                wanted.append(found["id"])
        if not wanted:
            raise card_sets.SetError("Среди выбранных нет сборок")
        return card_sets.dissolve(req.settings, wanted)
    return _edit(req, run)


def move(req: Request) -> Response:
    from core import card_sets
    return _edit(req, lambda body: card_sets.move(req.settings, int(body.get("set") or 0), int(body.get("id") or 0),
                                                  int(body.get("to") or 0)))


def rename(req: Request) -> Response:
    from core import card_sets
    return _edit(req, lambda body: card_sets.rename(req.settings, int(body.get("set") or 0), str(body.get("title") or "")))


def gather(req: Request) -> Response:
    """«Собрать в одну сборку» of the gallery's choice (``into`` — add them to that сборка)."""
    from core import card_sets
    return _edit(req, lambda body: card_sets.gather(req.settings, _ids(body.get("ids")), into=int(body.get("into") or 0),
                                                    title=str(body.get("title") or "")))


# ── the parts the library has not (v5.30, core/set_remote.py) ─────────────────────────
def _family(req: Request, key: str):
    """(the parts the address's ``f=`` names — a сборка's, or a family's of v5.30 — None) or (None, an error
    Response)."""
    from core import set_remote
    if set_remote.parse_parts_key(key) is None and set_remote.parse_key(key) is None:
        return None, Response.json({"ok": False, "error": "Неверный адрес части сборки"}, 400)
    try:
        return set_remote.group(req.settings, key), None
    except set_remote.FamilyError as exc:
        return None, Response.json({"ok": False, "error": str(exc)}, 409)


def _remote_href(fam, card: dict, from_id: int, index: int, ctx: dict) -> str:
    return url("/post/remote", f=fam.key, k=card["key"], i=index if index >= 0 else None, **{"from": from_id}, **ctx)


def _remote_media(fam, card: dict, variant: str) -> str:
    return url("/sets/media", f=fam.key, k=card["key"], v=variant)


def _search_ctx(req: Request) -> dict:
    from webui.pages.gallery import _ctx
    return _ctx(req)


def remote_tiles(req: Request) -> Response:
    """The parts of the card's сборка the library has not, as tiles with «api», and the card each goes before (0 —
    at the end); asked of the site the first time (seconds), then kept (``set_remote.FAMILY_TTL``)."""
    from core import card_sets, set_remote
    image_id = int(req.params["id"])
    empty = {"ok": True, "tiles": [], "missing": 0}
    if not enabled(req.settings):
        return Response.json(empty)
    try:
        found = card_family(req.settings, image_id)
    except sqlite3.Error as exc:
        _LOG.debug("set of %s: %s", image_id, exc)
        return Response.json(empty)
    if not remote_enabled(req.settings, found):
        return Response.json(empty)
    try:
        if found.get("lone"):                 # a card of no сборка: its family on the site (v5.44.1)
            site, root = found["root"]
            fam = set_remote.family(req.settings, site, root, children_only=not found.get("child"))
        else:
            fam = set_remote.parts(req.settings, found["id"])
        missing = fam.missing()
    except set_remote.FamilyError as exc:
        return Response.json(dict(empty, note=str(exc)))
    except sqlite3.Error as exc:
        _LOG.debug("parts of %s: %s", image_id, exc)
        return Response.json(empty)
    if fam.error and not fam.cards:
        return Response.json(dict(empty, note=f"{fam.site}: {fam.error}" if fam.site else fam.error))
    current = req.arg("current")
    index = req.int_arg("i", -1)
    ctx = _search_ctx(req)
    tiles = []
    numbers = card_sets.numbers_on(found, fam.site)
    for card, before in set_remote.places(found["members"], numbers, missing[:set_remote.SHOWN_MAX], fam):
        number = set_remote.post_id_of(card, fam.site)
        is_current = card["key"] == current
        trash = " — она в корзине" if card.get("owned") == "trash" else ""
        what = f"пост {number} на {fam.site}: этой части нет в библиотеке{trash} — показана с сайта через его API"
        video = '<span class="post-set-play" aria-hidden="true">▶</span>' if any(e.get("video") for e in card["entries"]) else ""
        mark = ' aria-current="true"' if is_current else ""
        # a family's parent and children framed as the site frames them (green, orange), dashed — from the site
        role = (" parent" if number == fam.parent else " child") if found["kind"] == "parent" and fam.parent else ""
        html = (f'<div class="post-set-item remote{role}{" current" if is_current else ""}" data-remote="{esc(card["key"])}">'
                f'<a href="{esc(_remote_href(fam, card, image_id, index, ctx))}" title="{esc(what)}"{mark}>'
                f'<img src="{esc(_remote_media(fam, card, "preview"))}" alt="" loading="lazy" decoding="async">{video}'
                f'<span class="post-set-api" aria-label="с сайта, через API">api</span></a></div>')
        tiles.append({"key": card["key"], "before": before, "html": html})
    answer = {"ok": True, "tiles": tiles, "missing": len(missing), "site": fam.site}
    notes = list(getattr(fam, "notes", []) or [])
    if notes and not tiles:
        answer["note"] = "; ".join(notes)
    return Response.json(answer)


def remote_media(req: Request) -> Response:
    """A picture of a missing part (``v=sample`` — the larger one) — only the addresses the site gave for it."""
    from core.grabber.media import MediaError, media
    fam, error = _family(req, req.arg("f"))
    if error:
        return error
    search, card = fam.find(req.arg("k"))
    if card is None:
        return Response.json({"ok": False, "error": "Такой части сборки нет"}, 404)
    try:
        data_, ctype = media(search, card["key"], "sample" if req.arg("v") == "sample" else "preview")
    except MediaError as exc:
        return Response.json({"ok": False, "error": str(exc)}, exc.status)
    return Response(200, data_, ctype, {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


def _remote_groups(card: dict) -> list:
    """[(category, its title, tags)] of every site of the card, in the order of the post page."""
    from webui.html import CATEGORY_ORDER, CATEGORY_TITLES
    merged: dict = {}
    for item in card["entries"]:
        groups = item.get("groups") or {}
        if not any(groups.values()):
            groups = {"general": list(item.get("tags") or [])}
        for category, names in groups.items():
            bucket = merged.setdefault(str(category or "general").lower(), [])
            for name in names or []:
                if name not in bucket:
                    bucket.append(name)
    order = list(CATEGORY_ORDER) + [c for c in merged if c not in CATEGORY_ORDER]
    return [(c, CATEGORY_TITLES.get(c, c), merged[c]) for c in order if merged.get(c)]


def _remote_viewer(fam, card: dict, entry: dict) -> str:
    """The part's picture as a card's picture is shown (``gallery._picture_html``, booru.js ``setupPicture``): its
    preview stands in, stretched over the place, until the larger copy has come whole; one that does not come — said,
    with what the site answered (``data-why``), and «Загрузить заново»."""
    sample = esc(_remote_media(fam, card, "sample"))
    preview = esc(_remote_media(fam, card, "preview"))
    if entry.get("video"):
        return (f'<video class="post-media" src="{sample}" controls loop preload="metadata" poster="{preview}"></video>')
    width, height = int(entry.get("width") or 0), int(entry.get("height") or 0)
    dims = f' width="{width}" height="{height}"' if width > 0 and height > 0 else ""
    return (f'<div class="media-wrap loading remote-media" data-id="" data-why="1">'
            f'<img class="post-ph" src="{preview}" alt="" aria-hidden="true">'
            f'<img class="post-media fit" src="{sample}" alt=""{dims} data-action="toggle-fit" '
            f'title="Щёлкните, чтобы показать в полный размер"></div>'
            f'<div class="picture-problem" hidden role="alert"><p></p>'
            f'<button type="button" data-action="picture-retry">Загрузить заново</button></div>')


RATING_WORDS = {"s": "безопасный", "safe": "безопасный", "g": "общий", "general": "общий", "q": "сомнительный",
                "questionable": "сомнительный", "e": "откровенный", "explicit": "откровенный", "sensitive": "чувствительный"}


def _remote_info(fam, card: dict, entry: dict, number: int) -> str:
    """«Информация» of a part from the site, as a card's (``gallery._info_box``): the site's post, its size and file,
    MD5, the site's rating, the links (the post on the site, the post's own sources)."""
    rows = [("Сайт", str(entry.get("label") or fam.site)), ("Пост", str(number))]
    if entry.get("width") and entry.get("height"):
        rows.append(("Размер", f'{int(entry["width"])}×{int(entry["height"])}'))
    if entry.get("ext"):
        rows.append(("Файл", str(entry["ext"])))
    md5 = str(card.get("md5") or entry.get("md5") or "")
    if md5:
        rows.append(("MD5", f'<code class="md5">{esc(md5)}</code>'))
    rating = str(entry.get("rating") or "").strip().lower()
    if rating:
        rows.append(("Рейтинг на сайте", RATING_WORDS.get(rating, rating)))
    rows.append(("В библиотеке", "в корзине" if card.get("owned") == "trash" else "нет"))
    table = "".join(f"<tr><th>{esc(k)}</th><td>{v if k == 'MD5' else esc(v)}</td></tr>" for k, v in rows)
    links, seen = [], set()
    for u in [entry.get("url") or ""] + list(entry.get("sources") or []):
        u = str(u or "")
        if u and u not in seen and u.startswith(("http://", "https://")):
            seen.add(u)
            host = (u.split("//", 1)[1].split("/", 1)[0]).removeprefix("www.")
            links.append(f'<li><a href="{esc(u)}" target="_blank" rel="noopener noreferrer">{esc(host)}</a></li>')
    sources = f'<h4>Источники</h4><ul class="plain-list sources">{"".join(links)}</ul>' if links else ""
    return f'<section class="box"><h3>Информация</h3><table class="info">{table}</table>{sources}</section>'


def remote_post(req: Request) -> Response:
    """The page of a part the library has not — as a card's page (v5.44.10: the person expected «как в галерее»): the
    search and the part's tags (with the library's counts), its «Информация» at the side, the сборка over the picture
    (this part marked), its picture from the site (the preview stands in until it comes), right under it «Скачать»
    (into the library through the parser, the file's MD5 known — as from the browser extension).  A part downloaded
    meanwhile — its card's page."""
    from core import set_remote
    from webui.html import page_frame, search_box
    from webui.pages.gallery import _prefs, _tag_colors, _tag_groups_html, _top_search
    fam, error = _family(req, req.arg("f"))
    from_id = req.int_arg("from", 0, lo=0)
    index = req.int_arg("i", -1)
    ctx = _search_ctx(req)
    title = "Часть сборки с сайта"
    if error:
        body = f'<div class="empty"><p>{esc(_error_text(error))}</p></div>'
        return Response.html(page_frame(title, body, active="posts", **_prefs(req)), status=error.status)
    search, card = fam.find(req.arg("k"))
    if card is None:
        body = ('<div class="empty"><p>Этой части сборки на сайте больше нет (или она скрыта в граббере).</p>'
                + (f'<p><a href="{esc(url(f"/post/{from_id}", i=index if index >= 0 else None, **ctx))}">← к карточке</a></p>'
                   if from_id else "") + '</div>')
        return Response.html(page_frame(title, body, active="posts", **_prefs(req)), status=404)
    fam.missing()                                 # the marks again: downloaded meanwhile?
    if card.get("owned") in ("file", "post") and int(card.get("image_id") or 0):
        return Response.redirect(url(f"/post/{int(card['image_id'])}", **ctx))
    entry = next((e for e in card["entries"] if e.get("site") == fam.site), card["entries"][0])
    number = set_remote.post_id_of(card, fam.site)
    picture = _remote_viewer(fam, card, entry)
    blocked = search.blocked_tags(card)
    can = bool(entry.get("original") or entry.get("sample"))
    if blocked:
        action = (f'<span class="remote-note">У поста теги из блоклиста граббера и подписок ({esc(", ".join(blocked[:6]))})'
                  ' — такой пост не скачивается.</span>')
    elif not can:
        action = '<span class="remote-note">Сайт не даёт этот файл — скачивать нечего.</span>'
    else:
        action = (f'<button type="button" class="remote-download" data-f="{esc(fam.key)}" data-k="{esc(card["key"])}">'
                  f'Скачать</button>')
    trash = ' Её файл — в корзине программы.' if card.get("owned") == "trash" else ""
    site_link = (f' <a href="{esc(entry["url"])}" target="_blank" rel="noopener noreferrer">открыть на сайте</a>'
                 if entry.get("url") else "")
    bar = (f'<div class="remote-bar" role="region" aria-label="Скачать часть сборки">'
           f'<span class="remote-api">api</span> <span class="remote-what">Этой части нет в библиотеке — картинка с сайта '
           f'{esc(entry.get("label") or fam.site)} (пост {number}) через его API.{trash}</span> {action}{site_link} '
           f'<span class="action-status" role="status"></span></div>')
    groups = {cat: list(tags) for cat, _name, tags in _remote_groups(card)}
    tags_html = _tag_groups_html(groups, ctx, data.TAG_COUNTS.get(req.settings), colors=_tag_colors(req))
    back = (f'<p class="remote-back"><a href="{esc(url(f"/post/{from_id}", i=index if index >= 0 else None, **ctx))}">'
            f'← к карточке, с которой пришли</a></p>' if from_id else "")
    sidebar = (search_box(ctx["tags"], section=ctx["section"], source=ctx["source"], order=ctx["order"])
               + f'<section class="box"><h3>Теги</h3>{tags_html or "<p class=hint>Тегов нет.</p>"}</section>'
               + _remote_info(fam, card, entry, number) + back)
    box = box_html(req, from_id, ctx, index, remote_current=card["key"]) if from_id else ""
    body = f'<div class="post-view remote-view">{box}<div class="post-stage">{picture}</div>{bar}</div>'
    from webui import prefs
    stored = prefs.load()
    return Response.html(page_frame(f"{title}: {fam.site} {number}", body, active="posts", search=ctx["tags"],
                                    section=ctx["section"], sidebar=sidebar, top_search=_top_search(ctx), kind="post",
                                    body_data={"post-fit": stored.get("post_fit") or "fit", "fit-note": "off"},
                                    **_prefs(req)))


def _error_text(error: Response) -> str:
    import json as _json
    try:
        return str(_json.loads(error.body.decode("utf-8")).get("error") or "Ошибка")
    except (ValueError, AttributeError):
        return "Ошибка"


def remote_download(req: Request) -> Response:
    """«Скачать» on a part's page: the post goes to the program's browser import (as from the extension)."""
    from core import set_remote
    from core.browser_companion_api import import_manager
    from core.download_policy import downloads_enabled
    body = req.json()
    fam, error = _family(req, str(body.get("f") or ""))
    if error:
        return error
    search, card = fam.find(str(body.get("k") or ""))
    if card is None:
        return Response.json({"ok": False, "error": "Этой части сборки на сайте больше нет"}, 404)
    fam.missing()
    if card.get("owned") in ("file", "post") and int(card.get("image_id") or 0):
        return Response.json({"ok": True, "done": True, "image_id": int(card["image_id"])})
    blocked = search.blocked_tags(card)
    if blocked:
        return Response.json({"ok": False, "error": "У поста теги из блоклиста граббера и подписок: "
                              + ", ".join(blocked[:8]) + " — такой пост не скачивается."}, 409)
    if not downloads_enabled(req.settings):
        return Response.json({"ok": False, "error": "Скачивание новых файлов в этой сессии выключено "
                              "(«Настройки» → «Скачивание»)"}, 409)
    manager = import_manager(req.settings)
    if manager is None:
        return Response.json({"ok": False, "error": "Импорт не запущен — перезапустите программу"}, 503)
    answer = manager.submit(set_remote.import_item(card))
    if not answer.get("ok") or not answer.get("task_id"):
        return Response.json({"ok": False, "error": f"Не принято: {answer.get('error') or 'неизвестно почему'}"}, 400)
    _join_when_done(req.settings, manager, str(answer["task_id"]), fam)
    return Response.json({"ok": True, "task": answer["task_id"]})


_JOINS: dict = {}                       # task id -> where the card it became went: {"set", "how"} (v5.44.10)
_JOINS_MAX = 200


def _join_when_done(settings: dict, manager, task_id: str, fam) -> None:
    """The card a «Скачать» became goes into its сборка as soon as it is in the library (``card_sets.join_part``; a
    family of a lone card — the сборки built again at once), not at the next build three minutes later."""
    import threading
    from core import card_sets, set_remote
    set_id = set_remote.parse_parts_key(fam.key)
    site = str(getattr(fam, "site", "") or "")

    def run() -> None:
        joined = {"set": 0, "how": ""}
        try:
            state = manager.wait_task(task_id, timeout=3600)
            image_id = data.image_id_by_path(settings, str(state.get("result_path") or "")) \
                if str(state.get("status") or "") == "done" else 0
            if image_id and set_id:
                joined = card_sets.join_part(settings, set_id, image_id, site=site)
            elif image_id:
                card_sets.rebuild(settings, force=True, wait=60)
                joined = {"set": card_sets.set_id_of(settings, image_id), "how": "rules"}
        except Exception as exc:  # the card stays in the library; the next build of the сборки looks at it
            _LOG.warning("a part of a сборка downloaded, not put into it: %s", exc)
        finally:
            _JOINS[task_id] = joined
            while len(_JOINS) > _JOINS_MAX:
                _JOINS.pop(next(iter(_JOINS)))
    threading.Thread(target=run, name="set-part-join", daemon=True).start()


TASK_WORDS = {"queued": "в очереди", "waiting": "ждёт", "running": "скачивается", "done": "готово",
              "failed": "не получилось", "cancelled": "отменено", "deferred": "отложено"}


def remote_task(req: Request) -> Response:
    """How a «Скачать» goes: {"status", "words", "message", "image_id" — the card it became (when done)}."""
    from core.browser_companion_api import import_manager
    from core.redaction import sanitize_text
    manager = import_manager()
    state = manager.task(req.arg("id")) if manager is not None else {}
    if not state:
        return Response.json({"ok": False, "error": "Нет такой загрузки"}, 404)
    status = str(state.get("status") or "")
    image_id = 0
    path = str(state.get("result_path") or "")
    if status == "done" and path:
        image_id = data.image_id_by_path(req.settings, path)
    joined = _JOINS.get(str(req.arg("id") or ""))
    return Response.json({"ok": True, "status": status, "words": TASK_WORDS.get(status, status),
                          "message": sanitize_text(str(state.get("message") or ""))[:300], "image_id": image_id,
                          "joined": joined})
