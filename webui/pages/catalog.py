"""(v5.44.90) «Лента» of the tabs «Манга», «Манхва» and «Видео»: the sites' titles in one list, with filters
(``core/catalog.py``; ``static/catalog.js``, ``catalog.css``).

The owner (6.11.2026): «а вопрос где нахуй ленты манги и видео?»; the look — his approved mockups «Манга → Онлайн —
каталог с фильтрами» and «Видео → Онлайн — каталог сериалов»: the name and «Порядок» over the tiles, the filters
chosen as chips with ×, «Показать ещё» below; on the right «Фильтры» — genres (a click — «да», another — «нет»),
tags, type, status, «от — до» of chapters, rating and year, «Сбросить» / «Применить».

* ``/manga/feed``, ``/manhwa/feed`` — Remanga and MangaLib together (the tab's kinds of comics); ``?site=`` — the feed
  of one site of ``core/manhwa_feed`` (ReadManga, MangaBuff, Desu, MangaDex, Хентай-тян, Nude-Moon — as the site gives
  it, no filters: «Новые главы» or «Популярное»; the address of before, ``?site=&how=``, — the same);
* ``/videos/feed`` — YummyAnime's catalog;
* ``?part=1`` — the tiles of a page as JSON ({html, more, next, notes, pending}): «Показать ещё» and «отвечает»;
* what was chosen last is remembered for each tab (``prefs`` ``feed_<kind>``): the tab opens as it was left;
* a tile — (v5.44.102) the title's page in the tab (``webui/pages/title.py``: read without downloading, download);
  a series of «Видео» — the card of «Скачать → По ссылке» (``get_link.link_of``), until it has a page of its own;
* (v5.44.105) the phone — the same lists in its frame: ``/m/manga/feed``, ``/m/manhwa/feed`` (until then the feed of
  one site, no filters), ``/m/videos/feed``; its addresses (``Asked.phone``): the tiles — the phone's title page, a
  series — the phone's card ``/m/get``; the computer's feeds opened on the phone go there (``phone.TWINS``).
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode

from core import catalog as C
from webui.html import esc, page_frame
from webui.server import Request, Response

PATHS = {"manga": "/manga/feed", "manhwa": "/manhwa/feed", "video": "/videos/feed"}
HEADS = {"manga": "Манга онлайн", "manhwa": "Манхва онлайн", "video": "Сериалы онлайн"}
PREF = {"manga": "feed_manga", "manhwa": "feed_manhwa", "video": "feed_video"}
# the address's words: what a link and the form keep (``page``, ``part``, ``again`` — not remembered)
# (v5.44.148) ``sg`` / ``sx`` — a site's own tags, «да» and «нет» («nhentai:artist:name»: ``catalog.Query.extra``)
PARAMS = ("order", "q", "g", "x", "tg", "tx", "sg", "sx", "t", "st", "ch1", "ch2", "r1", "r2", "y1", "y2", "s", "site")
ALONE = ("readmanga", "mangabuff", "desu", "mangadex", "hentaichan", "nudemoon")   # sites shown as feeds of their own
SHOWN_TAGS = 24                   # tags a search of «Найти тег» shows at most (the feed's own: all are in the page;
#                                   (v5.44.148) a site's own — asked of it as the words are typed, ``tags_json``)
ICON_SEARCH = ('<svg class="cat-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle>'
               '<path d="m20 20-3.5-3.5"></path></svg>')


def register(router) -> None:
    router.add("GET", r"/manga/feed", lambda req: page(req, "manga"))
    router.add("GET", r"/manhwa/feed", lambda req: page(req, "manhwa"))
    router.add("GET", r"/videos/feed", lambda req: page(req, "video"))
    router.add("GET", r"/m/manga/feed", lambda req: page(req, "manga", phone=True))      # (v5.44.105) the phone
    router.add("GET", r"/m/manhwa/feed", lambda req: page(req, "manhwa", phone=True))
    router.add("GET", r"/m/videos/feed", lambda req: page(req, "video", phone=True))
    router.add("GET", r"/api/catalog/cover", cover)
    router.add("GET", r"/api/catalog/filters", filters_json)          # (v5.44.130) a page's genres as searches
    router.add("GET", r"/api/catalog/tags", tags_json)                # (v5.44.148) a site's own tags for «Найти тег»


# ── what is asked ────────────────────────────────────────────────────────────────────────────────────────────────
class Asked:
    """The query of an address (or the one remembered), the site of a feed of its own ("" — the sites together)."""

    def __init__(self, kind: str, source: dict, mode: str, phone: bool = False):
        def many(name):
            return [str(v).strip() for v in source.get(name) or [] if str(v).strip()]

        def one(name):
            return (many(name) or [""])[0]

        self.kind = kind
        self.phone = phone             # (v5.44.105) the phone's page: its addresses
        self.find = ""                 # (v5.44.149) words put into «Найти тег» by the address (``find``; an author
        self.pick = ""                 # of a title's page) and whose tag of exactly that name is chosen at once
        order = one("order") or {"new": "updates", "popular": "popular"}.get(one("how"), "")   # the old «how»
        self.query = C.Query(
            kind, order=order, text=one("q"), genres=tuple(many("g")), without=tuple(many("x")),
            tags=tuple(many("tg")), untags=tuple(many("tx")), types=tuple(many("t")), status=tuple(many("st")),
            chapters=(C.as_number(one("ch1")), C.as_number(one("ch2"))),
            rating=(C.as_number(one("r1"), float), C.as_number(one("r2"), float)),
            years=(C.as_number(one("y1")), C.as_number(one("y2"))), sites=tuple(many("s")), mode=mode,
            extra=tuple(many("sg")), unextra=tuple(many("sx")))
        site = one("site")
        self.site = site if kind != "video" and site in _alone_names(mode) else ""

    def pairs(self, **change) -> list:
        """The address's words of this query (``change`` — name: value to put instead, None — to leave out)."""
        q = self.query
        values = {                     # «order» always: an address without words is «as it was left» (remembered)
            "order": [q.order if not self.site or q.order in ("updates", "popular") else "popular"],
            "q": [q.text] if q.text else [],
            "g": list(q.genres), "x": list(q.without), "tg": list(q.tags), "tx": list(q.untags), "t": list(q.types),
            "sg": list(q.extra), "sx": list(q.unextra),
            "st": list(q.status), "ch1": _num(q.chapters[0]), "ch2": _num(q.chapters[1]), "r1": _num(q.rating[0]),
            "r2": _num(q.rating[1]), "y1": _num(q.years[0]), "y2": _num(q.years[1]), "s": list(q.sites),
            "site": [self.site] if self.site else []}
        for name, value in change.items():
            values[name] = [] if value is None else (list(value) if isinstance(value, (list, tuple)) else [value])
        return [(name, v) for name in PARAMS for v in values.get(name, [])]

    def href(self, **change) -> str:
        """The address of this query (``page``, ``part``, ``again``, (v5.44.98) ``rest`` — of the request only)."""
        tail = []
        for key in ("page", "part", "again", "rest"):
            value = change.pop(key, None)
            if value in (None, "") or key == "page" and int(value) <= 1:
                continue
            tail.append((key, value))
        return self.path() + "?" + urlencode(self.pairs(**change) + tail)

    def path(self) -> str:
        return ("/m" if self.phone else "") + PATHS[self.kind]

    def without_value(self, name: str, value) -> str:
        """The address without one value of ``name`` (a chip's ×)."""
        return self.href(**{name: [v for v in dict(self.groups()).get(name, []) if v != value]})

    def groups(self) -> list:
        q = self.query
        return [("g", list(q.genres)), ("x", list(q.without)), ("tg", list(q.tags)), ("tx", list(q.untags)),
                ("sg", list(q.extra)), ("sx", list(q.unextra)),
                ("t", list(q.types)), ("st", list(q.status)), ("s", list(q.sites)), ("q", [q.text] if q.text else [])]


def _num(value) -> list:
    if not value:
        return []
    return [f"{value:g}" if isinstance(value, float) else str(value)]


def _alone_names(mode: str) -> list:
    from core import manhwa_feed as F
    from webui import content_mode
    return [s.name for s in F.sources(content_mode.shows(True, mode))]


def _asked(req: Request, kind: str, phone: bool = False) -> "tuple[Asked, bool]":
    """(the query, whether the address said it — else the one remembered)."""
    from webui import content_mode, prefs
    mode = content_mode.mode()
    said = {name: values for name, values in req.query.items() if name in PARAMS or name == "how"}
    # (v5.44.149) ``find`` — the words of «Найти тег» (an author's name from a title's page), ``pick`` — whose tag of
    # exactly that name the page chooses itself («lib»): asked on a list with nothing else chosen, not the remembered
    find = " ".join(str(req.arg("find") or "").split())[:60]
    if not said and not find:
        said = parse_qs(str(prefs.load().get(PREF[kind]) or ""))
        return Asked(kind, said, mode, phone), False
    asked = Asked(kind, said or {"order": ["popular"]}, mode, phone)
    asked.find = find if kind != "video" else ""
    asked.pick = req.arg("pick") if asked.find and req.arg("pick") in C.EXTRA_SITES else ""
    return asked, bool(said)


def _remember(kind: str, asked: Asked) -> None:
    from webui import prefs
    text = urlencode(asked.pairs())
    if str(prefs.load().get(PREF[kind]) or "") != text:
        prefs.save({PREF[kind]: text})


# ── tiles ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _rating(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _tile(t: dict, kind: str = "", phone: bool = False) -> str:
    from webui.pages import title as T
    from webui.pages.get_link import link_of
    title = t["title"]
    meta = " · ".join(str(x) for x in (t["type"], t["year"] or "", t["status"]) if x)
    picture = (f'<img src="/api/catalog/cover?c={t["cover"]}" alt="" loading="lazy" decoding="async">' if t["cover"]
               else '<span class="cat-nocover" aria-hidden="true"></span>')
    rate = f'<span class="cat-rate">★ {_rating(t["rating"])}</span>' if t["rating"] else ""
    hint = title + (f" ({t['alt']})" if t["alt"] and t["alt"] != title else "") + " — " + ", ".join(t["sites"])
    # (v5.44.102) a title of manga or manhwa — its page in this tab; (v5.44.106) a series of YummyAnime — its page
    # «Смотреть» (video_watch); another series — the card of «По ссылке»
    from core import video_watch as W
    from webui.pages import video_watch as VW
    if kind in T.KINDS and T.opens(t["url"]):
        target = T.href(kind, t["url"], cover=t["cover"], rating=t["rating"], name=title, phone=phone)
    elif kind == "video" and W.is_watchable(t["url"]):
        target = VW.href(t["url"], cover=t["cover"], rating=t["rating"], name=title, phone=phone)
    else:
        target = ("/m" if phone else "") + link_of(t["url"])
    return (f'<a class="cat-tile" href="{esc(target)}" title="{esc(hint)}">'
            f'<span class="cat-cover">{picture}{rate}</span><span class="cat-name">{esc(title)}</span>'
            f'<span class="cat-meta">{esc(meta)}</span></a>')


def _tiles(tiles: list, kind: str = "", phone: bool = False) -> str:
    return "".join(_tile(t, kind, phone) for t in tiles)


class Answer:
    """What a page of the list is: tiles, «есть ещё», words about the sites (HTML), why it is empty (HTML); None in
    ``tiles`` — still asked; (v5.44.98) ``rest`` — the address of the late sites' tiles (they come after)."""

    def __init__(self, tiles=None, more=False, notes="", empty="", rest=""):
        self.tiles, self.more, self.notes, self.empty, self.rest = tiles, more, notes, empty, rest


def _had_note(had: int) -> str:
    """(v5.44.93) The titles not shown: they are in the program's folders already."""
    if not had:
        return ""
    return (f'<p class="cat-note quiet">Уже скачанные не показаны: {had} — они в папке программы.</p>')


def _answer(req: Request, asked: Asked, n: int, wait: float, rest: bool = False) -> Answer:
    again = req.arg("again") == "1"
    have = C.downloaded(asked.kind, req.settings)
    if asked.site:
        return _alone(asked, n, wait, again, have)
    got = C.page(asked.query, n, wait=wait, again=again, settings=req.settings, have=have, rest=rest)
    if got is None:
        return Answer()
    notes = []
    if not rest:
        for labels, why in _skipped_together(got.skipped):
            notes.append(f'<p class="cat-note">Без {esc(_joined(labels))}: {esc(why)}.</p>')
        own = list(asked.query.extra)
        if own and n == 1:                           # (v5.44.148) a site's own tag: no other site has such a one
            what = C.extra_parts(own[0])[1]
            sites = [C.LABELS[s] for s in asked.query.asked()]    # (v5.44.149) an author of the Lib API — two sites
            notes.append(f'<p class="cat-note quiet">{"Показан только" if len(sites) < 2 else "Показаны только"} '
                         f'{esc(_joined(sites) or C.extra_where(own[0]))}: «{esc(C.extra_shown(own[0]))}» — '
                         f'{"его" if len(sites) < 2 else "их"} {esc(C.EXTRA_WORDS.get(what, "тег"))}, у других '
                         f'сайтов такого нет.</p>')
    for label, why in got.errors.items():
        if rest and label not in got.asked:
            continue
        said = why if why.startswith(label) else f"{label} не ответил: {why}"     # (v5.44.98) the label once
        notes.append(f'<p class="cat-note bad" role="alert">{esc(said)} '
                     f'<a href="{esc(asked.href(page=n, again=1))}">Спросить ещё раз</a></p>')
    rest_href = ""
    if got.late:                     # (v5.44.98) a site that is late: the others' tiles now, its own come after
        rest_href = asked.href(page=n, part=1, rest=1)
        who = _joined(got.late)
        notes.append(f'<p class="cat-note quiet cat-late" role="status" data-rest="{esc(rest_href)}">'
                     f'<span class="cat-spin" aria-hidden="true"></span> {esc(who)} '
                     f'{"отвечают — их тайтлы" if len(got.late) > 1 else "отвечает — его тайтлы"} добавятся сюда сами.'
                     f'</p>')
    if rest:
        return Answer(got.tiles, got.more, "".join(notes), "", "")
    if asked.query.order == "popular":
        newest = [label for label in ("Хентай-тян", "Hentasis", "HentaiHaven", "Hentai-XS", "HFShare")
                  if label in got.asked]                  # (v5.44.139) Hentai-XS, HFShare
        if newest:
            notes.append(f'<p class="cat-note quiet">{esc(_joined(newest))} — сначала новые: '
                         f'{"популярного у них нет" if len(newest) > 1 else "популярного у него нет"}.</p>')
    notes.append(_had_note(got.had))
    empty = ""
    if not got.tiles and not got.late:
        if not got.asked:
            empty = esc("Ни на одном сайте ленты нет таких тайтлов.")
        elif got.errors and len(got.errors) == len(got.asked):
            empty = esc("Сайты не ответили.")
        elif got.had and n == 1:
            empty = esc("Всё, что здесь нашлось, уже скачано.")
        else:
            empty = esc("По этим условиям ничего не нашлось." if n == 1 else "Больше ничего нет.")
    return Answer(got.tiles, got.more, "".join(notes), empty, rest_href)


def _joined(names: list) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " и " + names[-1] if names else ""


def _skipped_together(skipped) -> list:
    """(v5.44.157) The sites a list is shown without — those that say the same of one tag or genre in one line:
    [([labels], why)], «Без HentaiLib, nhentai и Nude-Moon: у них нет тега «Гяру-о»».  A tag one site alone has
    (AllHentai's 56, 5.44.155) made a line of every other site of the list — four of them above the tiles.  Any other
    reason, and the reason of one site, — as the site's builder says it."""
    said, order = {}, []
    for label, why in skipped:
        found = re.fullmatch(rf"у {re.escape(label)} (нет (?:тега|жанра) «.+»)", why)
        key = ("", found.group(1)) if found else (label, why)
        if key not in said:
            order.append(key)
        said.setdefault(key, []).append((label, why))
    out = []
    for key in order:
        rows = said[key]
        if len(rows) > 1:
            out.append(([label for label, _why in rows], "у них " + key[1]))
        else:
            out.append(([rows[0][0]], rows[0][1]))
    return out


def _alone(asked: Asked, n: int, wait: float, again: bool, have: "C.Have | None" = None) -> Answer:
    """The feed of one site (``manhwa_feed``) as the site gives it — every kind (Хентай-тян's are all «Манга», a page
    of ReadManga's «Популярное» may hold no manga at all: a filtered page would often be empty); (v5.44.93) without
    the titles of the program's folders."""
    from core import manhwa_feed as F, manhwa_sources as S
    from webui import content_mode
    how = "new" if asked.query.order == "updates" else "popular"
    got = F.page(asked.site, how, n, wait=wait, again=again)
    if got is None:
        return Answer()
    source = S.by_name(asked.site)
    label = source.label if source is not None else asked.site
    if got.error:
        return Answer([], False, f'<p class="cat-note bad" role="alert">{esc(got.error)} <a href="'
                                 f'{esc(asked.href(page=n, again=1))}">Попробовать ещё раз</a></p>')
    rows = [(h, list(h.names)) for h in got.hits]
    if asked.site in ("hentaichan", "nudemoon"):   # (v5.44.95) the parts of one work — one tile (v5.44.131 Nude-Moon)
        rows = C.works_of(got.hits)
    hits = [(h, names) for h, names in rows if have is None or not have.holds({(h.site, h.key)}, S.names_of(*names))]
    tiles = []
    for h, names in hits:
        tile = C.hit_tile(h, label)
        tile["title"] = names[0] if names else tile["title"]
        if content_mode.shows(tile["adult"]):
            tiles.append(tile)
    had = len(rows) - len(hits)
    return Answer(tiles, got.more, _had_note(had),
                  "" if tiles else esc("Всё, что здесь нашлось, уже скачано." if had and n == 1 else
                                       "Здесь пока ничего нет." if n == 1 else "Больше ничего нет."))


def _together(kind: str, mode: str) -> str:
    """(v5.44.93) The sites of the list in this mode: «Remanga, MangaLib и Хентай-тян»."""
    names = [C.LABELS[s] for s in C.sites_shown(kind, mode) or C.sites_for(kind, mode)]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " и " + names[-1] if names else ""


# ── the page ─────────────────────────────────────────────────────────────────────────────────────────────────────
def page(req: Request, kind: str, phone: bool = False) -> Response:
    asked, said = _asked(req, kind, phone)
    n = req.int_arg("page", 1, lo=1, hi=C.MAX_PAGE)
    part = req.arg("part") == "1"
    rest = part and req.arg("rest") == "1"          # (v5.44.98) the late sites' tiles of a page shown already
    if said and not part:
        _remember(kind, asked)
        if any(not str(v).strip() for name in PARAMS for v in req.query.get(name) or []):
            return Response.redirect(asked.href(page=n))     # the form's empty fields: a clean address
    got = _answer(req, asked, n, C.PART_WAIT if part else C.WAIT, rest=rest)
    next_href = asked.href(page=n + 1) if got.tiles is not None and got.more else ""
    if part:
        if got.tiles is None:
            return Response.json({"ok": True, "pending": True})
        return Response.json({"ok": True, "html": _tiles(got.tiles, kind, phone), "more": bool(next_href),
                              "next": next_href, "notes": got.notes, "empty": got.empty, "rest": got.rest})
    body = _body(req, asked, n, got, next_href)
    return _frame(req, kind, body, phone)


WAIT_PLACES = 12           # (v5.44.158) how many places of tiles are drawn while the sites answer


def _wait(asked: Asked, n: int) -> str:
    if asked.site:
        from core import manhwa_sources as S
        source = S.by_name(asked.site)
        who = f"{source.label if source else asked.site} отвечает"
    else:
        shown = C.sites_shown(asked.kind, asked.query.mode)
        names = [C.LABELS[s] for s in asked.query.asked() if s in shown]
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " и " + names[-1] if names else ""
        who = joined + (" отвечают" if len(names) > 1 else " отвечает")
    # (v5.44.158) under the line — the places of the tiles to come (they shimmer while «Плавные переходы» are on):
    # the page does not jump when the answer is put in
    places = '<span><i></i><b></b><u></u></span>' * WAIT_PLACES
    return (f'<div class="cat-wait" role="status" data-part="{esc(asked.href(page=n, part=1))}">'
            f'<p class="cat-wait-said"><span class="cat-spin" aria-hidden="true"></span> {esc(who)} — лента появится '
            f'здесь сама.</p><div class="cat-skel" aria-hidden="true">{places}</div></div>')


def _grid(asked: Asked, n: int, got: Answer, next_href: str) -> str:
    if got.tiles is None:
        return _wait(asked, n)
    grid = (f'<div class="cat-grid" data-page="{n}">{_tiles(got.tiles, asked.kind, asked.phone)}</div>'
            if got.tiles or got.rest else
            f'<p class="cat-empty">{got.empty}</p>')
    more = (f'<p class="cat-more"><a class="cat-btn" href="{esc(next_href)}" data-cat-more>Показать ещё</a></p>'
            if next_href else "")
    return f'<div class="cat-notes">{got.notes}</div>{grid}{more}'


def _body(req: Request, asked: Asked, n: int, got: Answer, next_href: str) -> str:
    q = asked.query
    kind = asked.kind
    together = _together(kind, q.mode)
    where = ("лента одного сайта, без фильтров" if asked.site else
             f"тайтлы {together}" + (" в одном списке" if " и " in together else ""))
    head = (f'<div class="cat-head"><h1>{esc(HEADS[kind])}</h1><span class="cat-where">{esc(where)}</span></div>')
    sites = _sites_row(asked) if kind != "video" else ""
    orders = C.orders(kind) if not asked.site else (("updates", "Новые главы", {}), ("popular", "Популярное", {}))
    current = q.order if not asked.site or q.order in ("updates", "popular") else "popular"
    options = "".join(f'<option value="{key}"{" selected" if key == current else ""}>{esc(label)}</option>'
                      for key, label, _s in orders)
    search = ("" if asked.site else
              f'<label class="cat-search">{ICON_SEARCH}<span class="cat-vh">Название</span><input type="search" '
              f'name="q" value="{esc(q.text)}" placeholder="Название" autocomplete="off" spellcheck="false"></label>')
    bar = (f'<div class="cat-bar">{search}<label class="cat-order"><span class="cat-vh">Порядок</span>'
           f'<select name="order" data-cat-order>{options}</select></label>'
           + ("".join(f'<input type="hidden" name="{name}" value="{esc(value)}">' for name, value in asked.pairs()
                      if name != "order") if asked.site else "") + '</div>')
    chips = "" if asked.site else _chips(asked)
    main = f'<div class="cat-main">{head}{sites}{bar}{chips}{_grid(asked, n, got, next_href)}</div>'
    side = _side(asked)
    return (f'<form class="cat" method="get" action="{asked.path()}" data-kind="{kind}" role="search" '
            f'aria-label="Лента">{main}{side}</form>')


def _sites_row(asked: Asked) -> str:
    from core import manhwa_sources as S
    from webui import content_mode
    here = ' class="active" aria-current="page"'
    links = [f'<a{here if not asked.site else ""} href="{esc(asked.href(site=None))}">'
             f'{esc(_together(asked.kind, asked.query.mode))}</a>']
    shown = _alone_names(content_mode.mode())
    for name in ALONE:
        source = S.by_name(name)
        if source is None or name not in shown:
            continue
        links.append(f'<a{here if asked.site == name else ""} href="{esc(asked.href(site=name))}">'
                     f'{esc(source.label)}</a>')
    return f'<nav class="cat-sites" aria-label="Сайты ленты">{"".join(links)}</nav>'


def _chips(asked: Asked) -> str:
    """The filters chosen — a chip each, × takes it away."""
    q = asked.query
    out = []

    def chip(text, href, cls=""):
        out.append(f'<a class="cat-picked{(" " + cls) if cls else ""}" href="{esc(href)}" '
                   f'aria-label="Убрать фильтр: {esc(text)}">{esc(text)}<span aria-hidden="true">×</span></a>')

    if q.text:
        chip(f"«{q.text}»", asked.href(q=None))
    for name in q.genres:
        chip(name, asked.without_value("g", name), "yes")
    for name in q.without:
        chip(f"без: {name}", asked.without_value("x", name), "no")
    for name in q.tags:
        chip(f"тег: {name}", asked.without_value("tg", name), "yes")
    for name in q.untags:
        chip(f"без тега: {name}", asked.without_value("tx", name), "no")
    for value in q.extra:                           # (v5.44.148) a site's own tags: «автор: name · nhentai»
        chip(C.extra_label(value, q.kind, q.mode), asked.without_value("sg", value), "yes")
    for value in q.unextra:
        chip(f"без: {C.extra_label(value, q.kind, q.mode)}", asked.without_value("sx", value), "no")
    labels = {k: label for k, label, _s in C.types(q.kind)}
    for key in q.types:
        chip(labels[key], asked.without_value("t", key))
    labels = {k: label for k, label, _s in C.statuses(q.kind)}
    for key in q.status:
        chip(labels[key], asked.without_value("st", key))
    for name, words, pair in (("ch", ("глав от", "глав до"), q.chapters), ("r", ("оценка от", "оценка до"), q.rating),
                              ("y", ("год от", "год до"), q.years)):
        for i in (0, 1):
            if pair[i]:
                value = _num(pair[i])[0].replace(".", ",")
                chip(f"{words[i]} {value}", asked.href(**{f"{name}{i + 1}": None}))
    for site in q.sites:
        chip(f"только {C.LABELS[site]}", asked.href(s=None))
    if not out:
        return ""
    reset = asked.href(**{name: None for name in PARAMS if name not in ("order", "site")})
    return (f'<div class="cat-chips">{"".join(out)}<a class="cat-reset" href="{esc(reset)}">Сбросить всё</a></div>')


def _tri(names, yes: tuple, no: tuple, field_yes: str, field_no: str, *, rest_from: "int | None" = None,
         also: "dict | None" = None) -> str:
    """Chips of three states: none, «да» (``field_yes``), «нет» (``field_no``); the hidden fields — the chosen ones;
    from ``rest_from`` on the ones not chosen wait for «все жанры» / «Найти тег» (``data-rest``); (v5.44.148)
    ``also`` — {a name: its other names}: «Найти тег» looks in them too (``data-also``, «|» between them)."""
    chips, inputs = [], []
    for i, name in enumerate(names):
        state = "yes" if name in yes else "no" if name in no else ""
        mark = "✓" if state == "yes" else "−" if state == "no" else ""
        rest = ' data-rest' if rest_from is not None and i >= rest_from and not state else ""
        others = "|".join((also or {}).get(name) or ())
        chips.append(f'<button type="button" class="cat-tri-chip" data-name="{esc(name)}" data-state="{state}"{rest}'
                     + (f' data-also="{esc(others)}"' if others else "") + '>'
                     f'<span class="cat-mark" aria-hidden="true">{mark}</span>{esc(name)}</button>')
        if state:
            inputs.append(f'<input type="hidden" name="{field_yes if state == "yes" else field_no}" '
                          f'value="{esc(name)}">')
    return (f'<div class="cat-tri" data-yes="{field_yes}" data-no="{field_no}">{"".join(chips)}'
            f'<span class="cat-tri-fields" hidden>{"".join(inputs)}</span></div>')


def own_chip(value: str, state: str = "", found: bool = False, kind: str = "", mode: str = "") -> str:
    """(v5.44.148) A site's own tag as a chip of three states: «автор shindol nhentai» (``data-name`` — what goes to
    the address, ``data-label`` — what is read aloud); the script makes the same of what a site found
    (``catalog.js``: ``ownChip``).  (v5.44.149) ``data-one`` — one of its kind at a time and no «нет» (an author of
    MangaLib: the site's list takes one)."""
    group, what, _name = C.extra_parts(value)
    mark = "✓" if state == "yes" else "−" if state == "no" else ""
    one = f' data-one="{esc(group)}"' if C.extra_single(value) else ""
    return (f'<button type="button" class="cat-tri-chip cat-own-chip{" found" if found else ""}" '
            f'data-name="{esc(value)}" data-label="{esc(C.extra_label(value, kind, mode))}" data-state="{state}"{one}>'
            f'<span class="cat-mark" aria-hidden="true">{mark}</span>'
            f'<span class="cat-own-kind">{esc(C.EXTRA_WORDS.get(what, what))}</span>'
            f'<span class="cat-own-name">{esc(C.extra_shown(value))}</span>'
            f'<span class="cat-own-site">{esc(C.extra_where(value, kind, mode))}</span></button>')


def own_groups(kind: str, mode: str) -> list:
    """(v5.44.149) [(whose — «nhentai», «lib», the labels of its sites shown)] — the sites of the list that have tags
    of their own (``catalog.EXTRA_SITES``): nhentai — tags, artists, characters, parodies; the Lib API — authors."""
    shown = C.sites_shown(kind, mode)
    out = []
    for group, sites in C.EXTRA_SITES.items():
        mine = [C.LABELS[s] for s in sites if s in shown]
        if mine and not (group == "nhentai" and kind != "manga"):
            out.append((group, mine))
    return out


def _own_tags(q, groups: list) -> str:
    """(v5.44.148) The sites' own tags: those of the query (chosen — «да» or «нет») and, put here by the script, those
    the sites found for the words typed in «Найти тег» (``tags_json``); their hidden fields — ``sg`` / ``sx``."""
    chips, inputs = [], []
    for value, state in [(v, "yes") for v in q.extra] + [(v, "no") for v in q.unextra]:
        chips.append(own_chip(value, state, kind=q.kind, mode=q.mode))
        inputs.append(f'<input type="hidden" name="{"sg" if state == "yes" else "sx"}" value="{esc(value)}">')
    said = [f'{"теги, авторы, персонажи и пародии" if group == "nhentai" else "авторы"} {_joined(labels)}'
            for group, labels in groups]
    who = _joined([label for _group, labels in groups for label in labels])
    words = "; ".join(said)
    hint = (f'<p class="cat-hint cat-own-hint" data-cat-own-hint data-sites="{esc(who)}">'
            f'{esc(words[:1].upper() + words[1:])} — начните вводить название (от двух букв).</p>' if groups else "")
    return (f'<div class="cat-tri cat-own" data-yes="sg" data-no="sx" data-cat-own>{"".join(chips)}'
            f'<span class="cat-tri-fields" hidden>{"".join(inputs)}</span></div>{hint}')


def _genre_names(kind: str, mode: str) -> list:
    """The genres in their order on the page: the usual ones first, then the rest by the alphabet; «Эротика» — not in
    «SFW» (and not in «NSFW» — there it is all of them)."""
    every = [g for g in C.offered(C.genres(kind), kind, mode, of_genres=True) if not (g == "Эротика" and mode != "all")]
    top = [g for g in C.top_genres(kind, mode) if g in every]
    return top + [g for g in every if g not in top]


def _side(asked: Asked) -> str:
    q = asked.query
    kind = asked.kind
    if asked.site:
        whole = asked.href(site=None)
        return (f'<aside class="cat-side" aria-labelledby="cat-flt"><h2 id="cat-flt">Фильтры</h2><p class="cat-hint">'
                f'У ленты одного сайта фильтров нет. Тайтлы с фильтрами — <a href="{esc(whole)}">'
                f'{esc(_together(kind, q.mode))}</a>.</p></aside>')
    groups = []
    mine = C.sites_shown(kind, q.mode)
    if len(mine) > 1:
        # (v5.44.149) the boxes — what the owner picked; with a site's own tag chosen only its sites are asked: the
        # others are dimmed, not unticked (unticked, they stayed so after the tag was taken away)
        picked = q.sites or mine
        asked_now = q.asked()
        idle = ' idle" title="Сейчас не спрашивается: выбранного тега или автора у этого сайта нет'
        boxes = "".join(
            f'<label class="cat-check{"" if site in asked_now else idle}">'
            f'<input type="checkbox" name="s" value="{site}"{" checked" if site in picked else ""}>'
            f'{esc(C.LABELS[site])}</label>' for site in mine)
        groups.append(f'<fieldset class="cat-group"><legend>Сайты</legend><div class="cat-checks">{boxes}</div>'
                      f'</fieldset>')
    if not C.filters_taken(kind, q.mode):          # (v5.44.93) NSFW: sites of hentai alone — they filter nothing
        groups.append(f'<p class="cat-hint">{esc(_together(kind, q.mode))} — без фильтров: '
                      f'{"его" if len(mine) == 1 else "их"} тайтлы видны как есть.</p>' if mine else
                      '<p class="cat-hint">Фильтровать нечего: в этом режиме сайты ленты не спрашиваются — почему, '
                      'сказано рядом с плитками.</p>')
        return (f'<aside class="cat-side" aria-labelledby="cat-flt"><h2 id="cat-flt">Фильтры</h2>'
                f'{"".join(groups)}</aside>')
    names = _genre_names(kind, q.mode)
    top = len([g for g in C.top_genres(kind, q.mode) if g in names])
    groups.append(f'<div class="cat-group"><div class="cat-group-head"><span class="cat-label">Жанры</span>'
                  f'<span class="cat-hint">нажать — да, ещё раз — нет</span></div>'
                  f'{_tri(names, q.genres, q.without, "g", "x", rest_from=top)}'
                  f'<button type="button" class="cat-link" data-cat-all aria-expanded="false">все жанры ›</button></div>')
    if kind != "video":
        tag_names = C.offered(C.tags(kind), kind, q.mode)    # (v5.44.96) HentaiLib's — not in «SFW»
        # (v5.44.148) the sites asked for their own tags as the words are typed (nhentai: tags, artists, characters…;
        # v5.44.149 — the Lib API: the authors of MangaLib and HentaiLib)
        own = own_groups(kind, q.mode)
        words = ("Найти тег, автора, персонажа" if any(group == "nhentai" for group, _l in own)
                 else "Найти тег или автора" if own else "Найти тег")
        suggest = f' data-cat-suggest="/api/catalog/tags?kind={kind}"' if own else ""
        found = (f' value="{esc(asked.find)}"' if asked.find else "") + \
            (f' data-cat-pick="{esc(asked.pick)}"' if asked.pick and own else "")
        groups.append(f'<div class="cat-group cat-tags"><div class="cat-group-head"><span class="cat-label">Теги</span>'
                      f'<span class="cat-hint">{len(tag_names)}</span></div><label class="cat-vh" for="cat-tagq">'
                      f'{words}</label><input id="cat-tagq" class="cat-field" type="search" placeholder="{words}" '
                      f'autocomplete="off" spellcheck="false" data-cat-tagfind data-limit="{SHOWN_TAGS}"{suggest}'
                      f'{found}>'
                      f'{_tri(tag_names, q.tags, q.untags, "tg", "tx", rest_from=0, also=C.also_names(kind))}'
                      f'{_own_tags(q, own) if own or q.extra or q.unextra else ""}</div>')
    if C.typed(kind, q.mode):                      # (v5.44.137) the videos in «NSFW»: their sites have no types
        boxes = "".join(f'<label class="cat-check"><input type="checkbox" name="t" value="{key}"'
                        f'{" checked" if key in q.types else ""}>{esc(label)}</label>'
                        for key, label, _s in C.types(kind))
        groups.append(f'<fieldset class="cat-group"><legend>Тип</legend><div class="cat-checks">{boxes}</div>'
                      f'</fieldset>')
        boxes = "".join(f'<label class="cat-check"><input type="checkbox" name="st" value="{key}"'
                        f'{" checked" if key in q.status else ""}>{esc(label)}</label>'
                        for key, label, _s in C.statuses(kind))
        groups.append(f'<fieldset class="cat-group"><legend>Статус</legend><div class="cat-checks">{boxes}</div>'
                      f'</fieldset>')
    ranges = [("r", "Оценка", q.rating, "decimal"), ("y", "Год", q.years, "numeric")]
    if kind != "video":
        ranges.insert(0, ("ch", "Количество глав", q.chapters, "numeric"))
    for name, label, pair, mode in ranges:
        values = [_num(pair[0]), _num(pair[1])]
        groups.append(
            f'<fieldset class="cat-group"><legend>{esc(label)}</legend><div class="cat-range">'
            f'<input class="cat-field" type="text" inputmode="{mode}" name="{name}1" placeholder="от" '
            f'aria-label="{esc(label)} от" value="{esc(values[0][0] if values[0] else "")}">'
            f'<input class="cat-field" type="text" inputmode="{mode}" name="{name}2" placeholder="до" '
            f'aria-label="{esc(label)} до" value="{esc(values[1][0] if values[1] else "")}"></div></fieldset>')
    reset = asked.href(**{name: None for name in PARAMS if name not in ("order", "site")})
    groups.append(f'<div class="cat-actions"><a class="cat-btn quiet" href="{esc(reset)}">Сбросить</a>'
                  f'<button type="submit" class="cat-btn primary">Применить</button></div>')
    return (f'<aside class="cat-side" aria-labelledby="cat-flt"><h2 id="cat-flt">Фильтры</h2>'
            f'{"".join(groups)}</aside>')


def _frame(req: Request, kind: str, body: str, phone: bool = False) -> Response:
    """The page in the frame of its tab (its look, its search, its section bar with «Лента» marked); (v5.44.105) the
    phone's — in its own frame: back to the section, its chips with «Лента» marked."""
    title = HEADS[kind]
    if phone:
        from webui.pages import phone as P
        section = {"manga": "manga", "manhwa": "manhwa", "video": "videos"}[kind]
        return P.page(title, phone_chips(kind) + body, active=section, heading="Лента", back=f"/m/{section}",
                      top=P.get_link() + P.mode_switch(), scripts=("catalog",), kind="m-feed",
                      head='<link rel="stylesheet" href="/static/catalog.css">')
    if kind == "manga":
        from webui.pages import manga
        tabs = tuple((label, href, key == "feed") for key, label, href in manga.TABS)
        return Response.html(page_frame(title, body, active="manga", subnav=False, kind="catalog", tabs=tabs,
                                        top_search=manga._search_form("", "newest"), thumbs="m", assets=("catalog",)))
    if kind == "manhwa":
        from webui.pages import manhwa
        tabs = tuple((label, href, key == "feed") for key, label, href in manhwa.TABS)
        return Response.html(page_frame(title, body, active="manhwa", subnav=False, kind="catalog", tabs=tabs,
                                        top_search=manhwa._search_form(""), assets=("catalog",)))
    from webui.pages import videos
    return Response.html(page_frame(title, body, active="videos", subnav=False, kind="catalog",
                                    tabs=videos.tabs_of(req, "feed"), top_search=videos._search_form("", "all"),
                                    assets=("catalog",), **videos._look()))


def phone_chips(kind: str, current: str = "feed") -> str:
    """(v5.44.105) The chips of a section of the phone with «Лента» among them (as the computer's tabs): the manga's own
    (``phone_manga.SECTIONS``), the manhwa's «Вся манхва», the videos' «Сериалы» and «Длинные видео»; ``current`` —
    the one marked."""
    from webui import content_mode
    if kind == "manga":
        from webui.pages import phone_manga
        return phone_manga._sections(current)
    if kind == "manhwa":
        items = (("all", "Вся манхва", "/m/manhwa"), ("feed", "Лента", "/m/manhwa/feed"))
        label = "Разделы манхвы"
    else:
        items = (("all", "Сериалы", "/m/videos"), ("feed", "Лента", "/m/videos/feed"))
        if content_mode.long_videos_shown():
            items += (("long", "Длинные видео", "/m/posts?section=videos"),)
        label = "Разделы видео"
    here = ' aria-current="page"'
    links = "".join(f'<a class="m-chip" href="{esc(href)}"{here if key == current else ""}>{esc(words)}</a>'
                    for key, words, href in items)
    return f'<nav class="m-chips" aria-label="{label}">{links}</nav>'


def filters_json(req: Request) -> Response:
    """(v5.44.130) ``/api/catalog/filters?kind=manga|manhwa|video&n=<name>&n=…`` — each genre or tag a page shows as a
    search of «Лента» (``core.catalog.feed_filter``): {chips: [{name, g | tg | never}]} — [{name}] the feed has not.
    The cards of «Скачать → По ссылке» make their genres links with it (the answers of the downloads say only names)."""
    kind = req.arg("kind") if req.arg("kind") in C.KINDS else "manga"
    out = []
    for raw in (req.query.get("n") or [])[:40]:
        name = " ".join(str(raw or "").split())[:80]
        if not name:
            continue
        field, value = C.feed_filter(kind, name, tag=req.arg("tags") == "1")
        chip = {"name": name}
        if field == "never":
            chip["never"] = True
        elif field:
            chip[field] = value
        out.append(chip)
    return Response.json({"ok": True, "chips": out})


def tags_json(req: Request) -> Response:
    """(v5.44.148) ``/api/catalog/tags?kind=manga&q=<the words typed>`` — the sites' own tags whose names begin so
    (``core.catalog.suggested``: nhentai's tags, artists, characters, parodies, groups; v5.44.149 — the authors of
    MangaLib and HentaiLib): {found: [{value, name, kind, word, site, count, one}], silent: [the sites that did not
    answer]} — «Найти тег» shows them as chips beside the feed's own tags.  Only the sites the mode shows («SFW» —
    not the sites of hentai), nothing a gate of the program closes."""
    from webui import content_mode
    kind = req.arg("kind") if req.arg("kind") in C.KINDS else "manga"
    words = " ".join(str(req.arg("q") or "").split())[:60]
    found, silent = C.suggested(kind, content_mode.mode(), words)
    return Response.json({"ok": True, "q": words, "found": found, "silent": silent})


def cover(req: Request) -> Response:
    got = C.cover(req.arg("c"))
    if got is None:
        return Response.not_found("Обложки нет")
    return Response(200, got[0], got[1], {"Cache-Control": "private, max-age=3600"})
