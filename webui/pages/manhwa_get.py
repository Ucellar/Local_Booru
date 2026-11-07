"""(v5.44.60) «Скачать манхву» (``/manhwa/get``) — the tab «Манхва»: a link to a title on Remanga, ReComics (v5.44.61),
MangaLib (v5.44.61), MangaBuff or (v5.44.73) com-x.life (through the window's browser: ``core/site_browser.py``) →
«Найти» (a page task: the title, its translators, its chapters and what the folder already has; (v5.44.62) the other
sites with this title and the chapters they have free that its own site has not) → «Скачать» (a page task: the free
chapters the folder has not, into the title's folder — ``core/manhwa_download.py``; with the box «брать с других
сайтов» — theirs too; the paid ones nobody has free kept with the date they become free, and taken then by the
nightly round) → the title's page.

(v5.44.74) As «Скачать с сайта» of the videos (the owner, 5.11.2026: «можно было бы сделать скачивание и красивее и с
паузой и если глава выдала ошибку перепроверить 3 раза», «посмотри как сделано скачивание видео»): the title's card
under its cover's own light, the chapters as tiles to tick (by tomes), «Скачать N глав» in a bar that stays at the
bottom of the window; in it the download — its chapter, strips, speed and time left, «Пауза» / «Продолжить»,
«Остановить», each tile filling as its strips come (``core/manhwa_run.py``: a chapter that failed asked again 3 times).
«Скачать» while another download goes — into the queue (``core/manhwa_queue.py``, kept between runs of the program).
The state gives only the chapters that changed after the page's last question (``since``, ``run``).

(v5.44.75) A part of «Скачать» (``get_hub.py``): «Скачать → Манхва» and, before its nhentai, «Скачать → Манга»
(:func:`block` — the same search and download, the manga first there).  The field takes a name too: «Найти» asks the
sites' searches at once (``core/manhwa_search.py``, a page task — :func:`search`, :func:`search_state`) and shows the
titles as covers (``/api/manhwa/hit-cover``); a cover picked — its chapters looked for as by its link.

(v5.44.116, the owner's review) In «SFW» a download of hentai (a site of hentai alone, a title its site marks as erotica
or marked «Это хентай») is in «Загрузки» without its name and cover — «Скрытая загрузка 18+» (:func:`download_state`
itself, so the phone's page too); «Не продолжать» for the downloads of the last run while they wait to be begun again
(``/api/manhwa/download/forget-restore``); the sites' names of the pages' hints — :func:`site_names`.

(v5.44.117) Each «Найти» is a search of its own (:class:`_Find`, by its task's number): several go at once
(:data:`FINDS_AT_ONCE`), and a page asks ``find-state``, ``find-cover`` and ``download`` by its own number (``find``) —
before, one search for the whole program: a second «Найти» (another tab, another title's page, the phone) stopped the
first, and the first page heard «Найденное устарело».  The same title asked again while it is searched joins that
search.  Ended searches are let go by time and by number (:data:`FIND_KEEP`, :data:`FINDS_KEPT`); «Найденное устарело»
only when the page's search is not there any more.  The page sees the link's site's chapters at once and the other
sites' as they answer (``manhwa_download.look(shown=)``: ``found.asking``; ``mark`` — «number.version» while it goes).

(v5.44.119, step 3A of the plan) The card of a title found here is gone: a link of a title (``/manhwa/get?url=``
included) or a cover picked opens the title's one page (``webui/pages/title.py``), whose block «Скачивание» is the card's
download, queue and boxes.  This module keeps its machinery — the searches, the download, the queue — and the parts'
field with the search by a name; :func:`follow` takes the page's search too (``{find, follow|helpers}``).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path

from core.manhwa_download import TASK as GET      # the page task «Скачать» (and the nightly round of new chapters)
from webui.html import esc
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.manhwa_get")
FIND = "manhwa_find"                  # the page tasks «Найти» — (v5.44.117) each page's its own (``many``: by its number)
FINDS_AT_ONCE = 5                     # (v5.44.117) searches going at once (one more — an abandoned one stops for it)
FIND_KEEP = 30 * 60                   # an ended search is kept this long after a page last asked of it…
FINDS_KEPT = 16                       # …and this many of them at most
ABANDONED = 20.0                      # a search going that no page has asked of for this long: its page has gone
FIND_WAIT_MORE = 15.0                 # «Скачать» while the other sites still answer: their time and this much more
STALE = "Найденное устарело — нажмите «Найти» ещё раз"
SITES = ("remanga.org", "recomics.org", "mangalib.me", "mangabuff.ru", "readmanga (a.zazaza.me)", "desu.uno",
         "com-x.life", "mangadex.org")         # (v5.44.77) ReadManga and Desu, (v5.44.79) MangaDex
# (v5.44.85) the sites of hentai alone: named only where hentai is shown; (v5.44.99) HentaiLib, AllHentai
ADULT_SITES = ("hentaichan.live", "hentailib.me", "allhentai (20.allhen.online)")
# (v5.44.75) not searched by a name (their search goes through the window's browser: a check for browsers first)
LINK_ONLY = ("com-x.life", "mangadex.org")
# (v5.44.116) a download of hentai in «Загрузки» while «SFW» is on: these words for its name, no cover
HIDDEN = "Скрытая загрузка 18+"
HIDDEN_KIND = "18+"
MAX_CHAPTERS = 20000                  # the ids «Скачать» may name
# (v5.44.75) the words of the two parts of «Скачать» this block is in: (the heading, the folder, the kind shown first)
PARTS = {"manhwa": ("Скачать манхву", "в папку манхвы", "manhwa"),
         "manga": ("Скачать мангу", "в папку манги", "manga")}
# (v5.44.93) a title found is shown again at once (the owner: «если я уже в ссылке прогрузил тайтл он должен моментально
# открываться») — by its site, its key, the translators and the folder asked; ``again`` in the request — asked anew
FOUND_TTL = 30 * 60
FOUND_KEPT = 16
_FOUND: "OrderedDict[tuple, tuple]" = OrderedDict()      # (site, key, branch, where) → (when, Found)
_FOUND_LOCK = threading.Lock()


def _kept_found(ask: tuple):
    """The title found a while ago for ``ask`` (site, key, branch, where) — (v5.44.103) or found without translators
    named, when the ones it chose are the ones asked now (the reader's «К тайтлу» names them: the page of the title
    came back slowly, as if never found)."""
    site, key, branch, where = ask
    with _FOUND_LOCK:
        now = time.monotonic()
        for one in (ask, (site, key, "", where)) if branch else (ask,):
            kept = _FOUND.get(one)
            if kept is None or now - kept[0] > FOUND_TTL:
                _FOUND.pop(one, None)
                continue
            found = kept[1]
            chosen = getattr(getattr(found, "branch", None), "id", "")
            if one != ask and str(chosen or "") != branch:
                continue
            _FOUND.move_to_end(one)
            return found
        return None


def _keep_found(ask: tuple, found) -> None:
    with _FOUND_LOCK:
        _FOUND[ask] = (time.monotonic(), found)
        _FOUND.move_to_end(ask)
        while len(_FOUND) > FOUND_KEPT:
            _FOUND.popitem(last=False)


def forget_found() -> None:
    with _FOUND_LOCK:
        _FOUND.clear()


class _Find:
    """(v5.44.117) One «Найти» of a page: its task (the page keeps its number), what was asked (site, key, translators,
    folder), the title as found so far (``found``, a copy given by ``manhwa_download.look(shown=)``, and ``rev`` — how
    many times it changed) — the page shows the link's site's chapters at once, the other sites' as they answer — and
    when a page last asked of it (``seen``: an ended search no page asks of is let go; a search going that no page asks
    of is abandoned)."""

    def __init__(self, ask: tuple, url: str):
        self.ask = ask
        self.url = url
        self.task = None
        self.found = None
        self.rev = 0
        self.seen = time.monotonic()
        self.stamp = None                # (the title shown, the time of its folder when its chapters were read)
        self.lock = threading.Lock()

    def show(self, found) -> None:
        """(``look``'s ``shown``) the title as it is so far."""
        with self.lock:
            self.found, self.rev = found, self.rev + 1

    def now(self) -> tuple:
        """(the title to show — None: not yet, or it ended without one —, its mark: the task's number, «number.rev»
        while it goes — a page that has it is not sent it again)."""
        task = self.task
        if task is None:
            return None, ""
        if task.running:
            with self.lock:
                found, rev = self.found, self.rev
            return found, (f"{task.id}.{rev}" if found is not None else "")
        kept = _found_of(task)
        return (kept["found"] if kept is not None else None), str(task.id)


_FINDS: "OrderedDict[int, _Find]" = OrderedDict()     # (v5.44.117) the searches by their numbers, the oldest first
_FINDS_LOCK = threading.Lock()
_START_LOCK = threading.Lock()                        # one «Найти» started at a time: the same title — one search


def _find_of(asked: str, *, newest: bool = True) -> "_Find | None":
    """The search of the number ``asked`` — "" (a page that has none yet): the newest one, when ``newest`` — asked of
    now (it is kept on)."""
    with _FINDS_LOCK:
        if not str(asked or ""):
            entry = next(reversed(_FINDS.values()), None) if newest else None
        else:
            try:
                entry = _FINDS.get(int(asked))
            except (TypeError, ValueError):
                entry = None
        if entry is not None:
            entry.seen = time.monotonic()
        return entry


def _forget_old(now: float) -> None:
    """(under ``_FINDS_LOCK``) The ended searches no page has asked of for :data:`FIND_KEEP`, and the oldest-asked
    beyond :data:`FINDS_KEPT`, let go; the ones going stay."""
    ended = [(n, f) for n, f in _FINDS.items() if f.task is not None and not f.task.running]
    for number, entry in ended:
        if now - entry.seen > FIND_KEEP:
            del _FINDS[number]
    ended = sorted(((n, f) for n, f in _FINDS.items() if f.task is not None and not f.task.running),
                   key=lambda x: x[1].seen)
    for number, _entry in ended[:max(0, len(ended) - FINDS_KEPT)]:
        del _FINDS[number]


def _room(now: float) -> bool:
    """(under ``_FINDS_LOCK``) May one more search start: fewer than :data:`FINDS_AT_ONCE` go — or one of them has been
    abandoned by its page (no question for :data:`ABANDONED` seconds): that one stops for it."""
    going = [f for f in _FINDS.values() if f.task is not None and f.task.running]
    if len(going) < FINDS_AT_ONCE:
        return True
    left = sorted((f for f in going if now - f.seen > ABANDONED), key=lambda f: f.seen)
    if not left:
        return False
    left[0].task.stop()
    return True


def found_titles() -> list:
    """(v5.44.117) The titles the pages' searches found, the newest first: [(Found, the link asked)] — the reader
    without downloading (``core/manga_online.Online._card``) takes a title found by its card instead of asking the
    sites again."""
    from core import page_jobs
    with _FINDS_LOCK:
        entries = [(e.task, e.url) for e in reversed(_FINDS.values())]
    newest = page_jobs.current(FIND)             # (one started without this registry: the newest of the kind)
    if newest is not None and all(task is not newest for task, _url in entries):
        entries.insert(0, (newest, ""))
    out = []
    for task, url in entries:
        kept = _found_of(task) if task is not None else None
        if kept is not None:
            out.append((kept["found"], str(kept.get("url") or url)))
    return out


def site_names(adult: bool = False) -> list:
    """(v5.44.116) The sites of manga and manhwa as the hints of the pages name them («Скачать → По ссылке», the phone's
    page and their words when a link is not one of them) — :data:`SITES` («readmanga», not its address in brackets);
    ``adult`` — the sites of hentai alone instead (:data:`ADULT_SITES`: a hint names them only outside «SFW»).  One
    list, not a hand-written one in each hint."""
    return [site.split(" (")[0] for site in (ADULT_SITES if adult else SITES)]


def hint_sites(adult: bool) -> tuple:
    """(v5.44.116) (the sites of manga and manhwa, the sites of hentai — nhentai and :data:`ADULT_SITES`; "" when
    ``adult`` is false: «SFW») as one text each, for the hints and the pages' scripts (``data-sites``)."""
    from core.manga_download import SITE
    hentai = [SITE.split("//", 1)[-1], *site_names(adult=True)] if adult else []
    return ", ".join(site_names()), ", ".join(hentai)


def register(router) -> None:
    from core import manhwa_queue
    router.add("GET", r"/manhwa/get", page)
    router.add("POST", r"/api/manhwa/find", find)
    router.add("GET", r"/api/manhwa/find-state", find_state)
    router.add("GET", r"/api/manhwa/find-cover", find_cover)
    router.add("POST", r"/api/manhwa/download", download)
    router.add("GET", r"/api/manhwa/download-state", download_state)
    router.add("POST", r"/api/manhwa/download/stop", download_stop)
    router.add("POST", r"/api/manhwa/download/pause", download_pause)
    router.add("POST", r"/api/manhwa/download/unqueue", download_unqueue)
    router.add("POST", r"/api/manhwa/download/forget-restore", download_forget_restore)     # (v5.44.116)
    router.add("GET", r"/api/manhwa/download-cover", download_cover)   # (v5.44.87) «Загрузки» of «По ссылке»
    router.add("POST", r"/api/manhwa/follow", follow)
    router.add("POST", r"/api/manhwa/search", search)              # (v5.44.75) «Найти» by a name
    router.add("GET", r"/api/manhwa/search-state", search_state)
    router.add("GET", r"/api/manhwa/hit-cover", hit_cover)
    from core import manhwa_access
    from core.settings import load_settings
    try:                                             # (v5.44.78) the owner's addresses of the sites and his sign-in
        manhwa_access.apply(load_settings())
    except (OSError, ValueError) as exc:
        _LOG.warning("the addresses of the sites of manga not read: %s", exc)
    if manhwa_queue.await_restore():                 # the downloads the program was busy with when it closed
        manhwa_queue.restore_later(load_settings)


def page(req: Request) -> Response:
    """«Скачать → Манхва»; (v5.44.119) with a title's link — that title's one page (``webui/pages/title.py``)."""
    from webui.pages import get_hub, title as T
    asked = req.arg("url").strip()
    if T.is_title_link(asked):
        return Response.redirect(T.address_for(asked, req.settings, hint="manhwa", branch=req.arg("b").strip()))
    body = block(req, "manhwa", asked, req.arg("q").strip())
    return get_hub.frame(req, "Скачать манхву", body, part="manhwa", kind="manhwa-get", assets=("manhwa",))


def block(req: Request, part: str = "manhwa", asked: str = "", name: str = "", note: str = "") -> str:
    """The search of a title of the sites (``#manhwa-get``, ``static/manhwa.js``): «Скачать → Манхва», and «Скачать →
    Манга» before its nhentai.  ``asked`` — a link looked for at once (a link from another part of «Скачать»), ``name``
    — a name searched at once (``?q=``); ``note`` — words under the lead (the manga folder not chosen).  (v5.44.119) A
    title found — a link, or one picked among the ones found by its name — opens its one page (``title.py``): the card
    of before with its chapters and its download is gone."""
    from webui import content_mode
    heading, folder, first = PARTS.get(part, PARTS["manhwa"])
    trouble = f'\n    <p class="mh-note" role="note">{esc(note)}</p>' if note else ""
    chips = "".join(f'<li>{esc(site)}<small> — по ссылке</small></li>' if site in LINK_ONLY else f"<li>{esc(site)}</li>"
                    for site in (*SITES, *(ADULT_SITES if content_mode.shows(True) else ())))
    autofind = "1" if asked else ""
    autoname = esc(name[:120]) if name and not asked else ""
    return f"""<div class="mh-get" id="manhwa-get" data-part="{esc(first)}" data-autofind="{autofind}" data-autoname="{autoname}">
  <section class="mh-ask" aria-labelledby="mh-get-title">
    <h1 id="mh-get-title">{esc(heading)}</h1>
    <p class="mh-lead">Найдите тайтл по названию или вставьте ссылку на него — программа найдёт его главы на всех
      сайтах, которые знает, и скачает бесплатные {esc(folder)} вместе с описанием и обложкой. Новые главы она будет
      скачивать сама — раз в сутки, ночью.</p>{trouble}
    <form class="mh-get-form" data-form="manhwa-find" role="search">
      <label class="mh-vh" for="mh-get-url">Название или ссылка на тайтл</label>
      <div class="mh-get-row"><input id="mh-get-url" name="url" type="text" enterkeyhint="search" autocomplete="off"
        spellcheck="false" placeholder="Название тайтла или ссылка на него"
        value="{esc(asked or name)}">
        <button type="submit" class="mh-btn primary">Найти</button></div>
      <span class="action-status" role="status"></span>
    </form>
    <div class="mh-get-where"><span class="mh-get-sites-label">Сайты:</span><ul class="mh-get-sites">{chips}</ul>
      <a class="mh-get-access" href="/settings#set-manga-sites">Адреса сайтов и вход</a></div>
    <details class="mh-get-help">
      <summary>Платные главы и главы с других сайтов</summary>
      <p>Платные главы программа не покупает. Глава, которая на сайте ссылки платная или которой там нет, берётся с
        другого сайта из списка, где она бесплатная; нет нигде — программа ждёт: сайт сделает её бесплатной или она
        появится на другом сайте — скачает сама.</p>
      <p>Глава, которая не скачалась с первого раза, проверяется ещё 3 раза с паузами, пока качаются следующие.</p>
    </details>
  </section>
  <section class="mh-hits" hidden aria-labelledby="mh-hits-title">
    <div class="mh-hits-head"><span class="mh-spin" aria-hidden="true"></span><h2 id="mh-hits-title"></h2>
      <ul class="mh-hits-sites" aria-label="Что ответили сайты"></ul></div>
    <div class="mh-hits-list" aria-live="polite"></div>
    <p class="mh-hits-fold" hidden><button type="button" class="mh-btn" data-action="manhwa-hits-all"></button></p>
  </section>
</div>"""


def _found_of(task):
    result = task.result if task is not None and task.status == "done" else None
    return result if isinstance(result, dict) and result.get("found") is not None else None


def find(req: Request) -> Response:
    from core import manhwa_download as D, manhwa_sources as S, page_jobs
    body = req.json()
    url = str(body.get("url") or "").strip()[:2000]
    if not url:
        return Response.json({"ok": False, "error": "Вставьте ссылку на тайтл"}, 400)
    source, key = S.source_for(url)
    if source is None:
        return Response.json({"ok": False, "error": D.UNKNOWN_LINK}, 400)
    refused = adult_refusal(source)
    if refused:
        return Response.json({"ok": False, "sfw": True, "error": refused}, 403)
    branch = str(body.get("branch") or "")[:40]
    where = str(body.get("where") or "")
    name = str(body.get("name") or "").strip()[:200]       # (v5.44.112) the tile's name: a 404 of the link — by it
    settings = dict(req.settings)
    ask = (source.name, key, branch, where)
    with _START_LOCK:
        with _FINDS_LOCK:
            now = time.monotonic()
            # (v5.44.117) the same title searched now (another page, «Найти» pressed again): this page joins that search
            going = next((f for f in reversed(_FINDS.values())
                          if f.ask == ask and f.task is not None and f.task.running), None)
            if going is not None:
                going.seen = now
                return Response.json({"ok": True, "id": going.task.id, "joined": True})
        kept = None if body.get("again") else _kept_found(ask)
        with _FINDS_LOCK:
            if kept is None and not _room(time.monotonic()):     # (one found a while ago is shown at once)
                return Response.json({"ok": False, "error": f"Сейчас ищутся уже {FINDS_AT_ONCE} тайтлов — подождите, "
                                                            "пока один из них найдётся"}, 409)
        entry = _Find(ask, url)

        def run(say, stop_check):
            if kept is not None:                     # (v5.44.93) found a while ago: at once, the folder read anew
                kept.have = D.had(Path(kept.folder), kept.title.site)
                say(f"«{kept.title.title}» уже открывали — показываю сразу")
                return {"found": kept, "url": url, "kept": True}
            if source.readable:
                say(f"{source.label}: читаю тайтл «{key}»")
            try:
                found = D.look(url, settings, branch=branch, net=S.Net(stop_check), where=where, say=say, name=name,
                               shown=entry.show)
            except D.DownloadError as exc:
                return {"failed": str(exc)}
            if stop_check():
                return {"failed": "остановлено"}
            if not found.late:                       # (v5.44.117) a site did not answer in time: asked anew next time
                _keep_found(ask, found)
            extra = len([t for t in found.new(True) if t.helped])
            say(f"Нашлось: «{found.title.title}» — глав {len(found.chapters)}, новых бесплатных {len(found.new(False))}"
                + (f", с других сайтов — ещё {extra}" if extra else ""))
            return {"found": found, "url": url}
        # (v5.44.117) each page's search is its own: a second one does not stop the first (it did since 5.44.93)
        task = page_jobs.start(FIND, f"Найти: {source.label}", run, many=True)
        entry.task = task
        with _FINDS_LOCK:
            _FINDS[task.id] = entry
            _forget_old(time.monotonic())
    return Response.json({"ok": True, "id": task.id})


def find_state(req: Request) -> Response:
    """The page's search (``find`` — its number; none — the newest one: a page opened anew shows the last title
    found), or the title it found — (v5.44.74) with its chapters as they are in the folder now (read again when the
    folder changed); ``seen`` — the mark of the title the page shows already (``mark``): not sent again (a title has
    hundreds of chapters, the page asks every few seconds).  (v5.44.117) While the other sites still answer — the title
    so far (``found.asking``, ``partial``); the page's search gone — ``gone`` and words."""
    from core import content_marks, manhwa_download as D
    asked = req.arg("find")
    entry = _find_of(asked)
    if entry is None or entry.task is None:
        if asked:
            try:
                number = int(asked)
            except ValueError:
                number = 0
            return Response.json({"ok": True, "id": number, "status": "failed", "gone": True, "failed": STALE,
                                  "error": STALE})
        return Response.json({"ok": True, "status": "idle"})
    task = entry.task
    state = {"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0))}
    found, mark = entry.now()
    state["mark"] = mark
    if found is not None and mark and req.arg("seen") == mark:
        state["found_same"] = True
    elif found is not None:
        folder = Path(found.folder)
        try:
            stamp = folder.stat().st_mtime_ns
        except OSError:
            stamp = 0
        if stamp and (id(found), stamp) != entry.stamp:  # chapters came (or went) since the search: as they are now
            found.have = D.had(folder, found.title.site)
            entry.stamp = (id(found), stamp)
        if (folder / D.LB_DIR / D.SOURCE_FILE).is_file():  # (v5.44.116) «Новые главы» as a download of it left them
            stored = D.read_source(folder)
            found.follow_on = D.follow_choice(stored)
            found.helpers_on = bool(stored.get("helpers_on", True))   # (v5.44.119) its page's box saved at once
        public = found.public()
        from webui import content_mode
        from types import SimpleNamespace
        from webui.pages.title import hand_mark
        hentai = bool(content_marks.manhwa_reason(SimpleNamespace(info=found.title.info())))
        hand = hand_mark(found, req.settings)        # (v5.44.122) «Это хентай» / «Это не хентай» of its folder wins
        if hand:
            hentai = hand == "hentai"
        hidden = content_mode.mode() == "sfw" and hentai
        public["cover"] = bool(found.cover) and not hidden
        public["cover_hidden"] = bool(found.cover) and hidden
        public["roots"] = _roots_public(req.settings)
        from core import manhwa_sources as S          # (v5.44.87) «Хентай» in the field of «По ссылке»
        public["adult_site"] = bool(getattr(S.by_name(found.title.site), "adult_only", False))
        public["hentai"] = hentai if hand else hentai or public["adult_site"]   # (v5.44.102) the page: hidden by the mode
        state["found"] = public
        state["partial"] = task.running                # (v5.44.117) the other sites still answer: more is coming
    elif isinstance(task.result, dict) and task.result.get("failed"):
        state["failed"] = str(task.result["failed"])
    return Response.json(state)


def _roots_public(settings: dict) -> dict:
    from core import manhwa_download as D
    roots = D.roots(settings)
    return {"manhwa": roots["manhwa"], "manga": roots["manga"]}


def find_cover(req: Request) -> Response:
    """The cover of the title of the page's search (``find`` — its number); (v5.44.117) asked beside the other sites,
    it may come after the chapters (the page asks for it once ``found.cover`` says it is there)."""
    entry = _find_of(req.arg("find"), newest=False)
    found = entry.now()[0] if entry is not None else None
    if found is None:
        return Response.not_found("Обложки нет")
    from webui.pages.covers import title_picture
    fresh = title_picture(found, req.settings)       # (v5.44.123) the cover the owner chose (the menu of covers)
    if fresh is not None:
        return Response(200, bytes(fresh[0]), fresh[1], {"Cache-Control": "private, max-age=600"})
    if not found.cover:
        return Response.not_found("Обложки нет")
    from core.manhwa_download import _picture_type
    kinds = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
             ".avif": "image/avif", ".bmp": "image/bmp"}
    ext = _picture_type(found.cover, found.cover_type)
    return Response(200, bytes(found.cover), kinds.get(ext, "image/jpeg"), {"Cache-Control": "private, max-age=600"})


def download(req: Request) -> Response:
    """«Скачать» of the title of the page's search (``find`` — its number): the chapters ticked (``chapters``; none
    named — every new one), where, the boxes.  (v5.44.117) While the other sites still answer, it waits for them (the
    chapters each site gives are decided by the owner's order — the same whoever answered first)."""
    from core import manhwa_download as D, manhwa_queue as Q, manhwa_sources as S, page_jobs
    body = req.json()
    entry = _find_of(str(body.get("find") or ""), newest=False)
    if entry is None or entry.task is None:
        return Response.json({"ok": False, "error": STALE}, 409)
    task = entry.task
    if task.running and not task.wait(D.ASK_DEADLINE + FIND_WAIT_MORE):
        return Response.json({"ok": False, "error": "Тайтл ещё ищется на других сайтах — подождите немного и нажмите "
                                                    "«Скачать» ещё раз"}, 409)
    kept = _found_of(task)
    if kept is None:
        return Response.json({"ok": False, "error": STALE}, 409)
    found = kept["found"]
    where = str(body.get("where") or found.kind)
    if where not in ("manhwa", "manga"):
        where = found.kind
    roots = D.roots(req.settings)
    root = roots.get(where) or ""
    if not root:
        return Response.json({"ok": False, "error": "Папка манги не выбрана — выберите её в «Настройки → Папки» или "
                                                    "положите тайтл в папку манхвы"}, 409)
    try:
        Path(root).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Response.json({"ok": False, "error": f"Папка {root} недоступна: {exc}"}, 409)
    if where != found.kind or not found.folder.startswith(str(Path(root))):
        found.kind = where
        found.folder = str(D.folder_for(Path(root), found.title))
        found.have = D.had(Path(found.folder), found.title.site)
    follow_new = bool(body.get("follow", True))
    helpers = bool(body.get("helpers", found.helpers_on))
    chapters = body.get("chapters")                  # (v5.44.74) the tiles ticked; none named — every new one
    if chapters is not None and (not isinstance(chapters, list) or len(chapters) > MAX_CHAPTERS
                                 or not all(isinstance(c, (str, int)) for c in chapters)):
        return Response.json({"ok": False, "error": "Непонятный список глав"}, 400)
    # (v5.44.107; the owner: «почему нет очереди на скачивании манги и манхвы») more chapters of a title while it
    # downloads, or while it waits in the queue: into the queue — not the ones downloading now, one download of it
    from core import manhwa_run as R
    going = page_jobs.current(GET)
    meter, items = R.meter(since=0, run=-1) if going is not None and going.running else ({}, [])
    if chapters is not None and meter.get("site") == found.title.site and meter.get("title") == found.title.title:
        taken = {str(x.get("id")) for x in items if x.get("state") != "failed"}
        chapters = [c for c in chapters if str(c) not in taken]
        if not chapters:
            return Response.json({"ok": False, "error": "Эти главы уже качаются"}, 409)
    waiting_one = Q.queued(found.title.url, where)
    if waiting_one is not None:
        before = waiting_one["chapters"]
        chapters = None if before is None or chapters is None else list(dict.fromkeys(
            [str(c) for c in before] + [str(c) for c in chapters]))
    new = found.new(helpers)
    ticked = None if chapters is None else {str(c) for c in chapters}
    wanted = [t for t in new if ticked is None or t.id in ticked]
    waiting = found.waiting(helpers)
    if not new and not waiting and Path(found.folder).is_dir():
        return Response.json({"ok": False, "error": "Все главы уже в папке"}, 409)
    if not wanted and not waiting:
        return Response.json({"ok": False, "error": "Отметьте главы, которые скачать"}, 400)
    if wanted and all(D.needs_sign_in(t) for t in wanted):     # (v5.44.96) HentaiLib without a sign-in
        return Response.json({"ok": False, "error": wanted[0].source.sign_in_words()}, 409)
    settings = dict(req.settings)

    def work(say, stop_check):
        result = D.download(found, chapters=chapters, follow=follow_new, helpers=helpers, net=S.Net(stop_check),
                            say=say, stop_check=stop_check)
        result["open"] = Q.open_link(settings, result.get("folder") or "", result.get("kind") or "manhwa")
        return result
    record = Q.record_of(found, chapters=chapters, where=where, follow=follow_new, helpers=helpers)
    started = Q.start(f"Скачать: {found.title.title}", work, record=record, about=task.id, title=found.title.title,
                      count=len(wanted), chosen=[t.id for t in wanted])
    return Response.json({"ok": True, **started, "count": len(wanted)})


def download_state(req: Request) -> Response:
    """The download going (or the last one): the task's journal, the meter of ``core/manhwa_run.py`` with the chapters
    changed after ``since`` (all of them when ``run`` is not this download's; none — ``items=none``: the phone's card
    needs only the counts), «Пауза», the queue, what goes on soon after a restart; its result once it has ended."""
    from core import manhwa_queue as Q, manhwa_run as R, page_jobs
    from webui import content_mode
    task = page_jobs.current(GET)
    sfw = content_mode.mode() == "sfw"
    soon = Q.restoring_public()
    if soon:                                         # (v5.44.116) a title of hentai: not named in «SFW»
        hentai = soon.pop("hentai", [])
        if sfw:
            soon["titles"] = [HIDDEN if k < len(hentai) and hentai[k] else t for k, t in enumerate(soon["titles"])]
    queue = Q.public()
    for q in queue:
        if q.pop("hentai", False) and sfw:
            q["title"] = HIDDEN
    extra = {"queue": queue, **({"restoring": soon} if soon else {})}
    if task is None:
        return Response.json({"ok": True, "status": "idle", **extra})
    state = {"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0)), "find": task.about, **extra}
    meter, items = R.meter(since=req.int_arg("since", 0, lo=0), run=req.int_arg("run", 0, lo=0))
    state["meter"] = meter
    state["what"] = what_of(meter)                   # (v5.44.87) «Хентай» / «Манга» / «Манхва»
    state["items"] = [] if req.arg("items") == "none" else items
    state["paused"] = bool(meter.get("paused")) and task.running
    result = task.result if isinstance(task.result, dict) else {}
    if result:
        state["result"] = {k: result.get(k) for k in ("title", "got", "paid", "stopped", "open", "kind", "helped",
                                                      "gave_up", "skipped")}
        state["result"]["failed"] = list(result.get("failed_chapters") or [])   # the chapters that did not come
    if sfw and hentai_download(req.settings, meter, Q.current_record() if task.running else None):
        _hide(state)
    return Response.json(state)


def hentai_download(settings: dict, meter: dict, record: "dict | None" = None) -> bool:
    """(v5.44.116) The download is of hentai: its record says so (``manhwa_queue.hentai_of`` — known before its title is
    read), its site is one of hentai alone (:func:`what_of`), or the description in its folder does — the site's mark
    of erotica, a genre, a mark by hand on the title's page (``core/content_marks``, as the libraries judge it)."""
    from core import manhwa_queue as Q
    if Q.hentai_of(record) or what_of(meter) == "Хентай":
        return True
    folder = Path(str(meter.get("folder") or ""))
    if not str(meter.get("folder") or "") or not (folder / "info.json").is_file():
        return False
    from types import SimpleNamespace
    from core import content_marks, manga_library as M, manhwa_library as H
    try:
        info = json.loads((folder / "info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    reason = content_marks.manhwa_reason(SimpleNamespace(info=info if isinstance(info, dict) else {}))
    if meter.get("kind") == "manga":
        root = M.manga_root(settings)
        mark = content_marks.verdict("manga", M._id_for(Path(root), folder), reason) if root else None
    else:
        mark = content_marks.verdict("manhwa", H._id_for(Path(H.manhwa_root(settings)), folder), reason)
    return bool(reason) if mark is None else mark.hentai


def _hide(state: dict) -> None:
    """(v5.44.116) A download of hentai in «SFW»: «Скрытая загрузка 18+» — no name (its label, the meter's title and
    site, its folder, the site its chapters come from, its journal — the lines name it), no cover (``hidden``: the
    page does not ask for it; :func:`download_cover` refuses it too), no link to its page."""
    state.update(label=HIDDEN, what=HIDDEN_KIND, hidden=True, log=[])
    meter = state.get("meter") or {}
    meter.update(title="", site="", folder="")
    if isinstance(meter.get("now"), dict):
        meter["now"] = dict(meter["now"], name="", **{"from": ""})
    state["items"] = [dict(x, name="", **{"from": ""}) for x in state.get("items") or []]
    if state.get("result"):
        state["result"].update(title=HIDDEN, open="")


def download_forget_restore(req: Request) -> Response:
    """(v5.44.116) «Не продолжать»: the downloads of the last run are not begun again (the owner's review: during the
    30 s of «С прошлого запуска … продолжится через N с» there was no way to cancel)."""
    from core import manhwa_queue as Q
    if not Q.forget_restore():
        return Response.json({"ok": False, "error": "Они уже продолжаются — остановить можно «Остановить»"}, 409)
    return Response.json({"ok": True})


def what_of(meter: dict) -> str:
    """(v5.44.87) What a download is, in a word for «Загрузки»: «Хентай» (a site of hentai alone), «Манга» (into the
    manga folder), else «Манхва»."""
    from core import manhwa_sources as S
    if getattr(S.by_name(str(meter.get("site") or "")), "adult_only", False):
        return "Хентай"
    return "Манга" if meter.get("kind") == "manga" else "Манхва"


COVER_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
               ".gif": "image/gif", ".avif": "image/avif", ".bmp": "image/bmp"}


def download_cover(req: Request) -> Response:
    """(v5.44.87) The cover of the title being downloaded (the one its download put into its folder) — ``run``: the
    download's number (a cover asked by an older page is not another title's); (v5.44.116) none of hentai in «SFW»."""
    from core import manhwa_run as R
    from webui import content_mode
    meter, _items = R.meter(since=10 ** 12, run=req.int_arg("run", 0, lo=0))
    if not meter.get("run") or str(meter.get("run")) != req.arg("run") or not meter.get("folder"):
        return Response.not_found("Обложки нет")
    if content_mode.mode() == "sfw" and hentai_download(req.settings, meter):
        return Response.not_found("Обложки нет")
    folder = Path(str(meter["folder"]))
    for ext, kind in COVER_TYPES.items():
        picture = folder / f"cover{ext}"
        try:
            if picture.is_file() and picture.stat().st_size <= 20 * 1024 * 1024:
                return Response(200, picture.read_bytes(), kind, {"Cache-Control": "private, max-age=600"})
        except OSError:
            continue
    return Response.not_found("Обложки нет")


def download_stop(req: Request) -> Response:
    """«Остановить»: the download going, and the queue after it (a stop that the next one would undo is no stop)."""
    from core import manhwa_queue as Q, manhwa_run as R, page_jobs
    task = page_jobs.current(GET)
    dropped = Q.stop_all()
    if task is None or not task.running:
        if dropped:
            return Response.json({"ok": True, "dropped": len(dropped)})
        return Response.json({"ok": False, "error": "Скачивание не идёт"}, 409)
    if dropped:
        task._say("Очередь убрана: " + ", ".join(f"«{t}»" for t in dropped))
    task.stop()
    R.resume()                                       # a stop while paused: not paused any more
    return Response.json({"ok": True, "dropped": len(dropped)})


def download_pause(req: Request) -> Response:
    """(v5.44.74) «Пауза» / «Продолжить» of the download going: its chapter stops where it is and waits (the queue
    waits too)."""
    from core import manhwa_queue as Q, manhwa_run as R, page_jobs
    task = page_jobs.current(GET)
    if task is None or not task.running:
        return Response.json({"ok": False, "error": "Скачивание не идёт"}, 409)
    if req.json().get("pause"):
        if not R.paused():
            R.pause()
            task._say("ПАУЗА: глава остановится, где она есть, — «Продолжить» докачает её с этого места")
    elif R.paused():
        R.resume()
        task._say("ПРОДОЛЖАЮ")
    Q.note_paused(R.paused())
    return Response.json({"ok": True, "paused": R.paused()})


def download_unqueue(req: Request) -> Response:
    """«Убрать» a download from the queue (it has not begun)."""
    from core import manhwa_queue as Q
    if not Q.unqueue(req.json().get("id")):
        return Response.json({"ok": False, "error": "Его уже нет в очереди — возможно, оно уже началось"}, 409)
    return Response.json({"ok": True})


def library_folder(settings: dict, item_id: str, kind: str = "") -> "Path | None":
    """(v5.44.124) The folder of a title of either library by its id there: «Вся манхва» (``kind`` "manhwa"), «Вся
    манга» (``kind`` "manga": a series or a manga of a folder — a chapter's id names its series), "" — the manhwa's,
    then the manga's (their ids are made apart: ``manhwa_library._id_for`` takes the kind in)."""
    from core import manga_library as M, manhwa_library as H
    item_id = str(item_id or "")
    if kind in ("", "manhwa"):
        title = next((t for t in H.LIBRARY.items(H.manhwa_root(settings)) if t.id == item_id), None)
        if title is not None:
            return Path(title.path)
    if kind in ("", "manga"):
        root = M.manga_root(settings)
        manga = M.LIBRARY.get(root, item_id) if root and Path(root).is_dir() else None
        if manga is not None and manga.parent_id:
            manga = M.LIBRARY.get(root, manga.parent_id)
        if manga is not None and Path(manga.path).is_dir():
            return Path(manga.path)
    return None


def follow(req: Request) -> Response:
    """«Следить за новыми главами» on a title's page: {id, follow} — the nightly round takes it or leaves it;
    (v5.44.62) {id, helpers} — «брать с других сайтов» the chapters its site has not free; (v5.44.119) {find, …} — the
    title of the page's search (the one page of a title: the folder of either library, or the booru parser's);
    (v5.44.124) {id, kind: "manga", …} — a title of «Вся манга» (the same block on its page: both folders)."""
    from core import manhwa_download as D
    body = req.json()
    if body.get("find"):
        entry = _find_of(str(body.get("find") or ""), newest=False)
        found = entry.now()[0] if entry is not None else None
        if found is None:
            return Response.json({"ok": False, "error": STALE}, 409)
        folder = Path(found.folder)
    else:
        kind = str(body.get("kind") or "")
        if kind not in ("", "manga", "manhwa"):
            return Response.json({"ok": False, "error": "Такой библиотеки нет"}, 400)
        folder = library_folder(req.settings, str(body.get("id") or ""), kind)
        if folder is None:
            return Response.json({"ok": False, "error": "Такого тайтла нет"}, 404)
    data = D.read_source(folder) if (folder / D.LB_DIR / D.SOURCE_FILE).is_file() else {}
    if not data.get("site"):
        return Response.json({"ok": False, "error": "Этот тайтл скачан не программой — следить не за чем"}, 409)
    if "helpers" in body:
        data["helpers_on"] = bool(body.get("helpers"))
    else:
        data["follow"] = bool(body.get("follow"))
    D.write_source(folder, data)
    if body.get("find"):                             # the search's title as the page shows it again
        found.helpers_on = bool(data.get("helpers_on", True))
        found.follow_on = D.follow_choice(data)
    return Response.json({"ok": True, "follow": bool(data.get("follow", True)),
                          "helpers": bool(data.get("helpers_on", True))})


def adult_refusal(source, now: "str | None" = None) -> str:
    """(v5.44.85) The words of a refusal when ``source`` is a site of hentai alone and the mode is «SFW» (as nhentai's
    galleries are refused there); "" — none."""
    from webui import content_mode
    if source is None or not getattr(source, "adult_only", False) or content_mode.shows(True, now):
        return ""
    return (f"{source.label} — сайт хентая, а включён режим «SFW»: его тайтлы не показываются. «Показать всё» — и "
            "показываются.")


# ── (v5.44.75) «Найти» by a name: the sites' searches at once, the titles as covers ──────────────────────────────────
def search(req: Request) -> Response:
    from core import manhwa_search as MS, page_jobs
    from webui import content_mode
    query = " ".join(str(req.json().get("q") or "").split())[:MS.MAX_QUERY]
    if len(query) < 2:
        return Response.json({"ok": False, "error": "Напишите название — хотя бы две буквы"}, 400)
    try:
        _search, task = MS.start(query, adult=content_mode.shows(True))
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца."}, 409)
    return Response.json({"ok": True, "id": task.id})


def search_state(req: Request) -> Response:
    """What the sites have answered so far: {query, sites: [{label, state, count, error}], titles: [tile], done}; in
    «SFW» a title the site marks as erotica — without its cover."""
    from core import manhwa_search as MS
    from webui import content_mode
    found = MS.get(req.arg("id"))
    if found is None:
        return Response.json({"ok": True, "id": 0, "done": True, "gone": True, "titles": [], "sites": [],
                              "query": ""})
    state = found.public()
    if content_mode.mode() == "sfw":
        for tile in state["titles"]:
            if tile["adult"] and tile["cover"]:
                tile["cover"], tile["cover_hidden"] = "", True
    return Response.json({"ok": True, "id": found.id, **state})


def hit_cover(req: Request) -> Response:
    from core import manhwa_search as MS
    got = MS.cover(req.arg("s"), req.arg("c"))
    if got is None:
        return Response.not_found("Обложки нет")
    return Response(200, got[0], got[1], {"Cache-Control": "private, max-age=3600"})
