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
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path

from core.manhwa_download import TASK as GET      # the page task «Скачать» (and the nightly round of new chapters)
from webui.html import esc
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.manhwa_get")
FIND = "manhwa_find"                  # the page task «Найти»
SITES = ("remanga.org", "recomics.org", "mangalib.me", "mangabuff.ru", "readmanga (a.zazaza.me)", "desu.uno",
         "com-x.life", "mangadex.org")         # (v5.44.77) ReadManga and Desu, (v5.44.79) MangaDex
# (v5.44.85) the sites of hentai alone: named only where hentai is shown; (v5.44.99) HentaiLib, AllHentai
ADULT_SITES = ("hentaichan.live", "hentailib.me", "allhentai (20.allhen.online)")
# (v5.44.75) not searched by a name (their search goes through the window's browser: a check for browsers first)
LINK_ONLY = ("com-x.life", "mangadex.org")
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
    """«Скачать → Манхва»."""
    from webui.pages import get_hub
    body = block(req, "manhwa", req.arg("url").strip(), req.arg("q").strip())
    return get_hub.frame(req, "Скачать манхву", body, part="manhwa", kind="manhwa-get", assets=("manhwa",))


def block(req: Request, part: str = "manhwa", asked: str = "", name: str = "", note: str = "") -> str:
    """The search and the download of a title of the sites (``#manhwa-get``, ``static/manhwa.js``): «Скачать →
    Манхва», and «Скачать → Манга» before its nhentai.  ``asked`` — a link looked for at once (``?url=`` — «Докачать
    сейчас» of a title, a link from another part of «Скачать»), ``name`` — a name searched at once (``?q=``); ``note``
    — words under the lead (the manga folder not chosen)."""
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
  <section class="mh-find job" data-job="find" hidden aria-live="polite">
    <div class="job-head"><span class="mh-spin" aria-hidden="true"></span><b class="job-title"></b>
      <span class="job-status"></span></div>
    <p class="mh-step"></p>
    <details class="mh-log"><summary>Подробности</summary>
      <pre class="job-log" tabindex="0" aria-label="Журнал поиска"></pre></details>
  </section>
  <section class="mh-going" hidden aria-label="Скачивание">
    <p class="mh-going-note" hidden aria-live="polite"></p>
  </section>
  <section class="mh-found" hidden aria-labelledby="mh-found-title">
    <div class="mh-fcard">
      <div class="mh-fcard-cover"><img alt="" hidden><span class="mh-fcard-initial" aria-hidden="true"></span></div>
      <div class="mh-fcard-head">
        <p class="mh-found-head">Нашлось</p>
        <h2 class="mh-found-title" id="mh-found-title"></h2>
        <p class="mh-found-alt"></p>
      </div>
      <div class="mh-fcard-more">
        <ul class="mh-found-facts"></ul>
        <p class="mh-found-about" hidden title="Нажмите, чтобы прочитать целиком"></p>
        <div class="mh-found-notes"></div>
        <div class="mh-found-choice"></div>
        <p class="mh-found-paid" hidden></p>
      </div>
    </div>
    <div class="mh-pick">
      <div class="mh-pick-head"><h3>Главы</h3><span class="mh-read-tools"></span>
        <span class="mh-pick-count" aria-live="polite"></span>
        <span class="mh-pick-tools" role="group" aria-label="Отметить"><span class="mh-pick-label">Отметить:</span>
          <button type="button" class="link" data-pick="all">все</button>
          <button type="button" class="link" data-pick="none">ничего</button></span></div>
      <div class="mh-chapters" role="group" aria-label="Главы"></div>
    </div>
    <div class="mh-dlbar">
      <div class="mh-dlbar-go"><button type="button" class="mh-btn primary" data-action="manhwa-get-go">Скачать</button>
        <span class="mh-dlbar-sum"></span><span class="action-status" role="status"></span></div>
      <div class="mh-run job" data-job="download" hidden>
        <div class="job-head"><b class="job-title"></b> <span class="job-status"></span>
          <span class="action-status" role="status"></span>
          <span class="mh-run-buttons"><button type="button" class="mh-btn" data-action="manhwa-get-pause" hidden>Пауза</button>
          <button type="button" class="mh-btn" data-action="manhwa-get-stop" hidden>Остановить</button></span></div>
        <ul class="mh-queue" hidden aria-label="Очередь скачивания"></ul>
        <div class="mh-meter" hidden><progress max="1000" value="0"></progress><b class="mh-meter-pct"></b>
          <span class="mh-meter-text"></span></div>
        <div class="mh-run-end"><p class="mh-result" hidden></p>
          <p class="mh-open" hidden><a class="mh-btn primary" href="/manhwa">Открыть тайтл</a></p></div>
        <details class="mh-log"><summary>Подробности</summary>
          <pre class="job-log" tabindex="0" aria-label="Журнал скачивания"></pre></details>
      </div>
    </div>
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
    kept = None if body.get("again") else _kept_found(ask)

    def run(say, stop_check):
        if kept is not None:                         # (v5.44.93) found a while ago: at once, the folder read anew
            kept.have = D.had(Path(kept.folder), kept.title.site)
            say(f"«{kept.title.title}» уже открывали — показываю сразу")
            return {"found": kept, "url": url, "kept": True}
        if source.readable:
            say(f"{source.label}: читаю тайтл «{key}»")
        try:
            found = D.look(url, settings, branch=branch, net=S.Net(stop_check), where=where, say=say, name=name)
        except D.DownloadError as exc:
            return {"failed": str(exc)}
        if stop_check():
            return {"failed": "остановлено"}
        _keep_found(ask, found)
        extra = len([t for t in found.new(True) if t.helped])
        say(f"Нашлось: «{found.title.title}» — глав {len(found.chapters)}, новых бесплатных {len(found.new(False))}"
            + (f", с других сайтов — ещё {extra}" if extra else ""))
        return {"found": found, "url": url}
    try:
        # (v5.44.93) another title asked while one is still searched: that one stops, this one starts (it said «уже
        # идёт» before — a tile of the feed after another one seemed not to open at all)
        task = page_jobs.start(FIND, f"Найти: {source.label}", run, replace=True)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца."}, 409)
    return Response.json({"ok": True, "id": task.id})


def find_state(req: Request) -> Response:
    """The search going, or the title it found — (v5.44.74) with its chapters as they are in the folder now (read
    again when the folder changed); ``seen`` — the search whose title the page shows already: not sent again (a title
    has hundreds of chapters, the page asks every few seconds)."""
    from core import content_marks, manhwa_download as D, page_jobs
    task = page_jobs.current(FIND)
    if task is None:
        return Response.json({"ok": True, "status": "idle"})
    state = {"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0))}
    kept = _found_of(task)
    if kept is not None and req.arg("seen") == str(task.id):
        state["found_same"] = True
    elif kept is not None:
        found = kept["found"]
        folder = Path(found.folder)
        try:
            stamp = folder.stat().st_mtime_ns
        except OSError:
            stamp = 0
        if stamp and stamp != kept.get("stamp"):         # chapters came (or went) since the search: as they are now
            found.have = D.had(folder, found.title.site)
            kept["stamp"] = stamp
        public = found.public()
        from webui import content_mode
        from types import SimpleNamespace
        hentai = bool(content_marks.manhwa_reason(SimpleNamespace(info=found.title.info())))
        hidden = content_mode.mode() == "sfw" and hentai
        public["cover"] = bool(found.cover) and not hidden
        public["cover_hidden"] = bool(found.cover) and hidden
        public["roots"] = _roots_public(req.settings)
        from core import manhwa_sources as S          # (v5.44.87) «Хентай» in the field of «По ссылке»
        public["adult_site"] = bool(getattr(S.by_name(found.title.site), "adult_only", False))
        public["hentai"] = hentai or public["adult_site"]     # (v5.44.102) the title's page: hidden by the mode
        state["found"] = public
    elif isinstance(task.result, dict) and task.result.get("failed"):
        state["failed"] = str(task.result["failed"])
    return Response.json(state)


def _roots_public(settings: dict) -> dict:
    from core import manhwa_download as D
    roots = D.roots(settings)
    return {"manhwa": roots["manhwa"], "manga": roots["manga"]}


def find_cover(req: Request) -> Response:
    from core import page_jobs
    task = page_jobs.current(FIND)
    kept = _found_of(task)
    if kept is None or req.arg("find") != str(task.id) or not kept["found"].cover:
        return Response.not_found("Обложки нет")
    found = kept["found"]
    from core.manhwa_download import _picture_type
    kinds = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
             ".avif": "image/avif", ".bmp": "image/bmp"}
    ext = _picture_type(found.cover, found.cover_type)
    return Response(200, bytes(found.cover), kinds.get(ext, "image/jpeg"), {"Cache-Control": "private, max-age=600"})


def download(req: Request) -> Response:
    from core import manhwa_download as D, manhwa_queue as Q, manhwa_sources as S, page_jobs
    body = req.json()
    task = page_jobs.current(FIND)
    kept = _found_of(task)
    if kept is None or str(body.get("find") or "") != str(task.id):
        return Response.json({"ok": False, "error": "Найденное устарело — нажмите «Найти» ещё раз"}, 409)
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
    task = page_jobs.current(GET)
    soon = Q.restoring_public()
    extra = {"queue": Q.public(), **({"restoring": soon} if soon else {})}
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
    return Response.json(state)


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
    download's number (a cover asked by an older page is not another title's)."""
    from core import manhwa_run as R
    meter, _items = R.meter(since=10 ** 12, run=req.int_arg("run", 0, lo=0))
    if not meter.get("run") or str(meter.get("run")) != req.arg("run") or not meter.get("folder"):
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


def follow(req: Request) -> Response:
    """«Следить за новыми главами» on a title's page: {id, follow} — the nightly round takes it or leaves it;
    (v5.44.62) {id, helpers} — «брать с других сайтов» the chapters its site has not free."""
    from core import manhwa_download as D, manhwa_library as H
    body = req.json()
    title = next((t for t in H.LIBRARY.items(H.manhwa_root(req.settings)) if t.id == str(body.get("id") or "")), None)
    if title is None:
        return Response.json({"ok": False, "error": "Такого тайтла нет"}, 404)
    data = D.read_source(Path(title.path))
    if not data.get("site"):
        return Response.json({"ok": False, "error": "Этот тайтл скачан не программой — следить не за чем"}, 409)
    if "helpers" in body:
        data["helpers_on"] = bool(body.get("helpers"))
    else:
        data["follow"] = bool(body.get("follow"))
    D.write_source(Path(title.path), data)
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
