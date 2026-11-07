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
``phone.TWINS``).

(v5.44.119, step 3A of the plan — the owner's approved mockup A, «Норм») **One page of a title for every way in**: the
tile of «Лента», «Скачать → По ссылке» (a link, or a title picked by its name — no card of its own any more),
«Докачать сейчас» of the libraries of manga and manhwa, «К тайтлу» of the one reader; the addresses of before
(``/manhwa/get?url=``, ``/manga/get?url=``, ``/get?url=…&go=1`` with a title, the phone's ``/m/get?url=…&go=1``) come
here.  ``/title?url=`` (:func:`resolve`, the phone's ``/m/title``) — the page in the tab its title belongs to
(:func:`kind_for`: the folder it has, the title found a while ago, the way in's hint ``k``).  «←» goes where the page
was opened from («← Лента», «← Скачать», «← Вся манга», «← К главе 14» — ``title.js``).  Under the buttons — the block
«Скачивание» of the card of before: where, the new chapters at night (the title's own box — :func:`follow` of
``manhwa_get``), the other sites and the chapters they give, the paid ones in words with their dates.  The page knows
what is on the disk (:func:`shelf` — ``/api/title/shelf?find=``: the title's folder read itself — its chapters «в
папке», read ones of the three memories of reading, the new ones the nightly round brought, another site's, where it
was left), and «Продолжить» / «Читать» open the one reader — a chapter of the folder from the disk, the others from the
site without downloading.
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from webui.html import esc, page_frame
from webui.server import Request, Response

KINDS = ("manga", "manhwa")
HEADS = {"manga": "Манга", "manhwa": "Манхва"}
WHAT = {"manga": "эта манга", "manhwa": "эта манхва"}       # the words of a page the mode hides
NEW_FOR = 3 * 86400                 # (v5.44.119) a chapter the nightly round brought, not read: «новая» this long
CAME_SAID = 30 * 86400              # …and «пришла … — ночная проверка» under it this long
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
          "декабря")


def register(router) -> None:
    router.add("GET", r"/manga/title", lambda req: page(req, "manga"))
    router.add("GET", r"/manhwa/title", lambda req: page(req, "manhwa"))
    router.add("GET", r"/m/manga/title", lambda req: page(req, "manga", phone=True))      # (v5.44.105) the phone
    router.add("GET", r"/m/manhwa/title", lambda req: page(req, "manhwa", phone=True))
    router.add("GET", r"/title", lambda req: resolve(req))                                 # (v5.44.119) any way in
    router.add("GET", r"/m/title", lambda req: resolve(req, phone=True))
    router.add("GET", r"/api/title/shelf", shelf)                                         # what the folder has


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


# ── (v5.44.119) any way in: the page in the tab its title belongs to ───────────────────────────────────────────────
def kind_for(url: str, settings: dict, hint: str = "") -> str:
    """The tab of the title at ``url``: a gallery of nhentai — «Манга»; the folder the title has (either library —
    its ``.lb/source.json``), the title found a while ago (the kind its download would take), ``hint`` (the way in
    knows it: a tile, a title picked by its name), else «Манхва» (the program's own default kind of a site's title)."""
    from core import manhwa_download as D, manhwa_sources as S
    if gallery_of(url):
        return "manga"
    source, key = S.source_for(url)
    if source is not None:
        try:
            linked = D.linked_folder(settings, source.name, key)
        except OSError:
            linked = None
        if linked is not None and linked[0] in KINDS:
            return linked[0]
        from webui.pages.manhwa_get import _kept_found
        kept = _kept_found((source.name, key, "", ""))
        if kept is not None and getattr(kept, "kind", "") in KINDS:
            return kept.kind
    return hint if hint in KINDS else "manhwa"


def address_for(url: str, settings: dict, *, phone: bool = False, hint: str = "", branch: str = "", name: str = "",
                cover: str = "", rating: float = 0.0) -> str:
    """The address of the one page of the title at ``url`` (:func:`href` in the tab :func:`kind_for` finds)."""
    return href(kind_for(url, settings, hint), url, cover=cover, rating=rating, name=name, branch=branch, phone=phone)


def is_title_link(text: str) -> bool:
    """``text`` is one link of a title of a site of manga (not a gallery of nhentai: that one has its own ways)."""
    from core import manhwa_sources as S
    text = str(text or "").strip()
    return bool(re.fullmatch(r"https?://\S+", text)) and not gallery_of(text) and S.source_for(text)[0] is not None


def resolve(req: Request, phone: bool = False) -> Response:
    """``/title?url=`` (``/m/title`` — the phone's): the title's page in its tab; ``k`` — the way in's hint of the
    kind; ``b``, ``t``, ``c``, ``r`` — as :func:`href` takes them; a link that is no title (a video's page, a site no
    part knows) — «По ссылке» with it (``go=1``: it says what the link is)."""
    url = req.arg("url").strip()[:2000]
    if not url:
        return Response.redirect("/m/get" if phone else "/get")
    if not opens(url):          # not a title (a video's page, a site no part knows): «По ссылке» says what it is
        return Response.redirect(("/m/get?url=" if phone else "/get?url=") + quote(url, safe="") + "&go=1")
    return Response.redirect(address_for(url, req.settings, phone=phone, hint=req.arg("k").strip(),
                                         branch=req.arg("b").strip(), name=req.arg("t").strip(),
                                         cover=req.arg("c").strip()[:64], rating=_rating(req.arg("r"))))


def _frame(kind: str, title: str, body: str, phone: bool = False, status: int = 200) -> Response:
    """In the tab of the feed: its look, its search, its section bar with «Лента» marked; (v5.44.105) the phone's —
    in its frame: back to its «Лента» (the script goes back in the history when the feed was the page before)."""
    if phone:
        from webui.pages import phone as P
        return P.page(title, body, status, active=kind, heading=HEADS[kind], back=f"/m/{kind}/feed",
                      top=P.mode_switch(), scripts=("title", "covers"), kind="m-title-page",     # (v5.44.123) covers
                      head='<link rel="stylesheet" href="/static/title.css">'
                           '<link rel="stylesheet" href="/static/covers.css">')
    if kind == "manga":
        from webui.pages import manga
        tabs = tuple((label, link, key == "feed") for key, label, link in manga.TABS)
        return Response.html(page_frame(title, body, active="manga", subnav=False, kind="title", tabs=tabs,
                                        top_search=manga._search_form("", "newest"), assets=("title", "covers")),
                             status=status)
    from webui.pages import manhwa
    tabs = tuple((label, link, key == "feed") for key, label, link in manhwa.TABS)
    return Response.html(page_frame(title, body, active="manhwa", subnav=False, kind="title", tabs=tabs,
                                    top_search=manhwa._search_form(""), assets=("title", "covers")), status=status)


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


# ── (v5.44.119) what the title's folder has: /api/title/shelf ─────────────────────────────────────────────────────
def _day(stamp: float, now: "float | None" = None) -> str:
    """«сегодня в 03:12», «вчера в 03:12», «5 ноября в 03:12» (with the year when it is not this one)."""
    if not stamp:
        return ""
    moment, today = time.localtime(stamp), time.localtime(now if now is not None else time.time())
    days = (time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1))
            - time.mktime((moment.tm_year, moment.tm_mon, moment.tm_mday, 0, 0, 0, 0, 0, -1))) / 86400
    clock = f"в {moment.tm_hour:02d}:{moment.tm_min:02d}"
    if -0.5 < days < 0.5:
        return f"сегодня {clock}"
    if 0.5 <= days < 1.5:
        return f"вчера {clock}"
    day = f"{moment.tm_mday} {MONTHS[moment.tm_mon - 1]}"
    return (day if moment.tm_year == today.tm_year else f"{day} {moment.tm_year}") + f" {clock}"


def _number(label: str) -> str:
    found = re.search(r"Глава\s+(\S+)$", str(label or ""))
    return found.group(1) if found else ""


def got_words(labels: list) -> str:
    """What a nightly round brought: «пришла глава 41», «пришли главы 40 и 41», «пришли 7 глав: 40–46», «новых глав не
    было»."""
    from webui.pages.reader import words
    labels = [str(x) for x in labels or [] if str(x).strip()]
    if not labels:
        return "новых глав не было"
    numbers = [_number(x) for x in labels]
    if not all(numbers):
        return f"пришли {len(labels)} {words(len(labels), 'глава', 'главы', 'глав')}" if len(labels) > 1 \
            else f"пришла {labels[0]}"
    if len(numbers) == 1:
        return f"пришла глава {numbers[0]}"
    if len(numbers) <= 4:
        return "пришли главы " + ", ".join(numbers[:-1]) + f" и {numbers[-1]}"
    return f"пришли {len(numbers)} {words(len(numbers), 'глава', 'главы', 'глав')}: {numbers[0]}–{numbers[-1]}"


def _same(a, b) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
    except (OSError, ValueError):
        return False


def _library_of(folder: Path, settings: dict) -> tuple:
    """("manga" | "manhwa" | "", the library's root): the library the title's folder is a title of (a top folder of
    it); "" — none (the booru parser's folder, no folder yet)."""
    from core import manhwa_download as D
    for kind, root in D.roots(settings).items():
        if root and _same(folder.parent, root):
            return kind, root
    return "", ""


_FOLDERS: dict = {}                 # a title's folder → (its time, what ``had`` read, the chapters' chapter.json)
_FOLDERS_LOCK = threading.Lock()
_RESCANNED: dict = {}               # a title's folder → its time when its library was read again for it


def _folder_of(folder: Path, site: str) -> tuple:
    """(``manhwa_download.had`` of the folder, {a chapter's path: its chapter.json}) — read again only when the folder
    changed (its time: a chapter moved in, renamed, taken away)."""
    import json
    from core import manhwa_download as D
    try:
        stamp = folder.stat().st_mtime_ns
    except OSError:
        return {}, {}
    key = (str(folder), site)
    with _FOLDERS_LOCK:
        kept = _FOLDERS.get(key)
        if kept is not None and kept[0] == stamp:
            return kept[1], kept[2]
    have = D.had(folder, site)
    infos = {}
    for path in set(have.values()):
        info_path = Path(path) / "chapter.json"
        if not info_path.is_file():
            continue
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(info, dict):
            infos[str(path)] = info
    with _FOLDERS_LOCK:
        _FOLDERS[key] = (stamp, have, infos)
        while len(_FOLDERS) > 64:
            _FOLDERS.pop(next(iter(_FOLDERS)))
    return have, infos


def _path_of(have: dict, ch, by_number: bool):
    """The folder's own chapter (its path) for a chapter of the list — as ``manhwa_download.is_had`` finds it."""
    return (have.get(("id", ch.id)) or (bool(ch.number) and (have.get(("n", ch.tome, ch.number))
                                                            or by_number and have.get(("v", ch.number))))
            or (bool(ch.tome) and have.get(("t", ch.tome))) or None)


def _library_chapters(lib: str, root: str, folder: Path, paths: list) -> tuple:
    """(the library's title — a manga series or a manhwa title — or None, {a chapter's path: (its id there, its pages)})
    for the chapters of the folder ``paths``; the library read again once when it does not know one of them yet (a
    chapter downloaded a moment ago: its scan is kept a minute)."""
    from core import manga_library as M, manhwa_library as H
    module = M if lib == "manga" else H
    same = lambda path: os.path.normcase(os.path.normpath(str(path)))     # noqa: E731 - one way to compare paths

    def read() -> tuple:
        top = module.LIBRARY.get(root, module._id_for(Path(root), folder))
        return top, {same(c.path): c for c in (top.chapters if top is not None else [])}
    top, known = read()
    if any(same(p) not in known for p in paths):
        try:
            stamp = folder.stat().st_mtime_ns
        except OSError:
            stamp = 0
        if _RESCANNED.get(str(folder)) != stamp:
            _RESCANNED[str(folder)] = stamp
            module.LIBRARY.items(root, refresh=True)
            top, known = read()
    return top, {str(p): (known[same(p)].id, len(known[same(p)].pages)) for p in paths if same(p) in known}


def hand_mark(found, settings: dict) -> str:
    """(v5.44.122) The mark by hand of the title's folder as a title of its library («Это хентай» — "hentai", «Это не
    хентай» — "safe", "" — none; ``core/content_marks``, the library's own id of the folder): it wins over what the
    site says on this page too, as it does on the library's pages."""
    from core import content_marks as CM, manga_library as M, manhwa_library as H
    text = str(getattr(found, "folder", "") or "")
    if not text:
        return ""
    folder = Path(text)
    lib, root = _library_of(folder, settings)
    if not lib:
        return ""
    return CM.marks()[lib].get((M if lib == "manga" else H)._id_for(Path(root), folder), "")


def shelf_of(found, settings: dict, *, phone: bool = False, now: "float | None" = None) -> dict:
    """What the folder of ``found`` (``manhwa_download.Found``) has, for the title's page: ``rows`` — {a chapter's id
    of the list: {have, disk — the one reader's address of the folder's chapter ("" — it cannot be opened from the
    disk: not a library's), read, new, came, from — another site's chapter, its site}}; ``last`` — where it was left
    (the freshest of the folder's memory and of the reading without downloading: the chapter, its page, its pages,
    read to its end, the reader's address with the page); ``folder`` — its name, whether it is there, the library's
    page, «Показать папку»; ``night`` — the nightly round's last look and what it brought; (v5.44.122) ``mark`` — the
    phone's «Это хентай» / «Это не хентай» of the title in its library (``phone.mark_box``; "" — not a library's)."""
    from core import manga_library as M, manga_online as O, manhwa_download as D, manhwa_library as H
    from core import manhwa_sources as S
    now = time.time() if now is None else now
    m = "/m" if phone else ""
    folder = Path(found.folder)
    site = found.title.site
    home = getattr(S.by_name(site), "label", site)
    takes = found.takes(True)                   # the page's list, in its order (``Found.public`` «list»)
    exists = folder.is_dir()
    have, infos = _folder_of(folder, site) if exists else ({}, {})
    by_number = found.by_number()
    paths = {}
    for take in takes:
        path = _path_of(have, take.ch, by_number)
        if path is not None:
            paths[take.id] = Path(path)
    lib, root = _library_of(folder, settings) if exists else ("", "")
    top, ids = (_library_chapters(lib, root, folder, list(dict.fromkeys(paths.values()))) if lib else (None, {}))
    state = (M.PROGRESS if lib == "manga" else H.PROGRESS).title(top.id) if top is not None else \
        {"last": "", "at": 0, "read": [], "pos": {}}
    read_disk = set(state["read"])
    place = O.PROGRESS.of(found.title.url)
    read_site = {str(x) for x in place.get("read") or []}
    by_chapter = {}                              # a library chapter's id → the chapter's id of the list
    rows = {}
    for n, take in enumerate(takes, 1):
        path = paths.get(take.id)
        got = ids.get(str(path)) if path is not None else None
        info = (infos.get(str(path)) or {}) if path is not None else {}
        came = float(info.get("downloaded_at") or 0) if isinstance(info.get("downloaded_at"), (int, float)) else 0.0
        row = {}
        if path is not None:
            row["have"] = True
            if got is not None:
                by_chapter.setdefault(got[0], take.id)
                row["disk"] = f"{m}/{lib}/r/{got[0]}"
                row["pages"] = got[1]
            source = info.get("source") if isinstance(info.get("source"), dict) else {}
            other = str(source.get("site") or "")
            if other and other != site:
                row["from"] = getattr(S.by_name(other), "label", other)
            if info.get("night") and came and now - came < CAME_SAID:
                row["came"] = _day(came, now)
                row["night"] = True
        read = bool(got is not None and got[0] in read_disk) or str(take.id) in read_site or bool(
            place.get("ch") == n and place.get("done") and str(place.get("id") or take.id) == str(take.id))
        if read:
            row["read"] = True
        elif row.get("night") and now - came < NEW_FOR:
            row["new"] = True
        if row:
            rows[take.id] = row
    # where it was left: the freshest of the two memories
    last = None
    if top is not None and state["last"] and state["at"]:
        cid = state["last"]
        lib_chapter = (M.LIBRARY.get(root, cid) if lib == "manga" else top.chapter(cid))
        if lib_chapter is not None:
            pos = state["pos"].get(cid) or [0, 0]
            page_at = max(0, int(pos[0] if isinstance(pos, list) and pos else 0))
            mine = by_chapter.get(cid, "")
            label = next((t.label for t in takes if t.id == mine), "") or getattr(lib_chapter, "label", "") or \
                getattr(lib_chapter, "title", "")
            last = {"at": int(state["at"]), "id": mine, "label": label, "page": page_at,
                    "pages": len(lib_chapter.pages), "done": cid in read_disk,
                    "href": f"{m}/{lib}/r/{cid}?p={page_at + 1}", "where": "disk"}
    if place.get("at") and (last is None or int(place["at"]) > last["at"]):
        n = 0
        if place.get("id"):
            n = next((k for k, t in enumerate(takes, 1) if str(t.id) == str(place["id"])), 0)
        if not n and 1 <= int(place.get("ch") or 0) <= len(takes) and \
                takes[int(place["ch"]) - 1].label == place.get("label"):
            n = int(place["ch"])
        if not n:
            n = next((k for k, t in enumerate(takes, 1) if t.label == place.get("label")), 0)
        if n:
            take = takes[n - 1]
            page_at = max(0, int(place.get("page") or 0))
            row = rows.get(take.id) or {}
            from webui.pages.manga_online import online_link
            at_page = f"p={page_at + 1}"
            href_ = (f"{row['disk']}?{at_page}" if row.get("disk") else
                     f"{online_link(found.title.url, found.branch.id if found.branch else '', phone)}{n}&{at_page}")
            last = {"at": int(place["at"]), "id": take.id, "label": take.label, "page": page_at,
                    "pages": int(row.get("pages") or place.get("pages") or 0), "done": bool(place.get("done")),
                    "href": href_, "where": "disk" if row.get("disk") else "site"}
    stored = D.read_source(folder) if exists and (folder / D.LB_DIR / D.SOURCE_FILE).is_file() else {}
    night = None
    if stored.get("night_at"):
        night = {"at": _day(float(stored["night_at"]), now), "got": got_words(stored.get("night_got") or [])}
    in_folder = sum(1 for take in takes if take.id in paths)
    library = ""
    reveal = ""
    mark = ""
    if top is not None:
        library = f"{m}/{lib}/{'g' if lib == 'manga' else 't'}/{top.id}"
        reveal = "" if phone else f"/api/{lib}/{top.id}/reveal"
        if phone:                       # (v5.44.122) the computer's is on its library's page («Открыть в библиотеке»)
            from core import content_marks as CM
            from webui.pages import phone as P
            mark = P.mark_box(lib, top.id, CM.manga_verdict(top) if lib == "manga" else CM.manhwa_verdict(top))
    return {"rows": rows, "last": last, "home": home,
            "folder": {"name": folder.name, "there": exists, "kind": lib or found.kind, "library": library,
                       "reveal": reveal, "chapters": in_folder},
            "saved": bool(stored.get("site")), "downloaded": bool(stored.get("added_at")),
            "watch": bool(settings.get("manhwa_watch_auto", True)), "night": night, "mark": mark}


def shelf(req: Request) -> Response:
    """``/api/title/shelf?find=<the page's search>`` (``&m=1`` — the phone's addresses): :func:`shelf_of` of the title
    the page's search found; a title the mode hides — nothing of it."""
    from types import SimpleNamespace
    from core import content_marks, manhwa_sources as S
    from webui import content_mode
    from webui.pages import manhwa_get as MG
    entry = MG._find_of(req.arg("find"), newest=False)
    found = entry.now()[0] if entry is not None else None
    if found is None:
        return Response.json({"ok": False, "error": MG.STALE, "gone": entry is None}, 409)
    hentai = bool(content_marks.manhwa_reason(SimpleNamespace(info=found.title.info()))) or \
        bool(getattr(S.by_name(found.title.site), "adult_only", False))
    hand = hand_mark(found, req.settings)            # (v5.44.122) «Это хентай» of its folder, as the library has it
    if hand:
        hentai = hand == "hentai"
    if not content_mode.shows(hentai):
        return Response.json({"ok": False, "hidden": True, "error": "Скрыто режимом"}, 403)
    return Response.json({"ok": True, **shelf_of(found, req.settings, phone=req.arg("m") == "1")})
