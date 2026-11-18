"""«Скачать с сайта» on the tab «Видео» (``/videos/get``): the address of a title's page (or the page saved) → what
video files it links to directly (``core/video_download.look``, a page task) → the chosen ones downloaded into the
video folder as a series with its description and poster (``video_download.download``, another page task with a
progress bar: ``/api/videos/download-state`` carries ``meter``).

The list the page shows is the result of the last «Найти видео» kept by its task (``page_jobs.current``); «Скачать»
names it by the task's number, so a list read before another search is never downloaded by mistake.  The page gets
the names of the files only — their addresses stay in the program.

«Скачать» while another download goes (the owner, 02.11.2026: the second season found while the first one downloads —
no button, and its tiles showed the first season's episodes) puts this one in a queue (``_QUEUE``): it starts by
itself when the one before it ends (``_next``); «Остановить» stops the download and empties the queue.

The queue and the download going are kept in ``video_queue.json`` (the owner, 03.11.2026: «ещё бы оно очередь
запоминало»): after the program starts again (``restore``) each one is found again by its page's address and goes on —
the files there are had, a begun one goes on from its «.part»; a pause stays a pause; «Остановить» keeps nothing.
Until then the page says what goes on soon (``restoring`` of the state), and with no list found on it the download
going is shown on its own (the owner, 04.11.2026: it was going, unseen, till another title was found).

(v5.44.142) Each download says which page it is of and which episode each of its files is (``_shown_of``: the
download going — ``_CURRENT["shown"]``, one in the queue — its entry's): the page of a series shows on its tiles what
downloads for it (``going_for`` ← ``/api/series/getting``), whichever page began it, after a reload and after a restart.
"""
from __future__ import annotations

import itertools
import json
import logging
import re
import threading
import time
from collections import Counter
from pathlib import Path

from core import video_library as V
from webui.html import esc
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.video")
FIND = "video_find"                    # the page task «Найти видео»
GET = "video_download"                 # the page task «Скачать»
_QUEUE: list = []                      # downloads asked while one went: {"id", "find", "title", "chosen", "label", "run"}
_QUEUE_LOCK = threading.Lock()
_KEEP_LOCK = threading.Lock()
_QUEUE_IDS = itertools.count(1)
_CURRENT: dict = {"saved": None,       # the download going: what it is, to begin it again after a restart;
                  "shown": None}       # (v5.44.142) its page and its files' episodes (``_shown_of``)
_LAST: dict = {}                       # (v5.44.142) the download that ended last: its page, when, how it ended
_USER_STOP = threading.Event()         # «Остановить»: what it stops is not begun again
_RESTORED = threading.Event()          # the downloads of the last run read (once a run of the program)
_PENDING: dict = {"titles": [], "at": 0.0}   # those downloads before they are begun again: for the page
KEEP_NAME = "video_queue.json"
RESTORE_AFTER = 30.0                   # seconds after the start: the program's own start goes first
_ORDERS: dict = {}                     # YummyAnime's alias -> (when asked, its viewing order): asked once an hour
ORDER_TTL = 3600.0
_ORDERS_LOCK = threading.Lock()


def register(router) -> None:
    router.add("GET", r"/videos/get", page)
    router.add("POST", r"/api/videos/find", find_url)
    router.add("POST", r"/api/videos/find-file", find_file)
    router.add("GET", r"/api/videos/find-state", find_state)
    router.add("GET", r"/api/videos/find-poster", find_poster)
    router.add("POST", r"/api/videos/download", download)
    router.add("GET", r"/api/videos/download-state", download_state)
    router.add("GET", r"/api/videos/measure", measure)
    router.add("POST", r"/api/videos/download/stop", download_stop)
    router.add("POST", r"/api/videos/download/unqueue", download_unqueue)
    router.add("POST", r"/api/videos/download/pause", download_pause)
    router.add("GET", r"/api/videos/order/(?P<id>[0-9a-f]{12})", series_order)
    router.add("GET", r"/api/videos/order", title_order)             # (v5.44.145) a title of a site, by its address
    if _await_restore():                             # the downloads the program was busy with when it closed
        threading.Thread(target=_restore_later, name="video-download-restore", daemon=True).start()


def _root(settings: dict) -> tuple[Path | None, str]:
    """The video folder, or why there is none: a folder chosen in «Настройки» whose disk is not here is never made
    anew (the default one is made with the first download)."""
    root = Path(V.video_root(settings))
    if root.is_dir() or not str(settings.get("video_root") or "").strip():
        return root, ""
    return None, f"Папки видео нет на месте: {root}. Подключите её диск или выберите другую в «Настройки → Хранение и копии»"


# ── the page ──────────────────────────────────────────────────────────────────────
def page(req: Request) -> Response:
    from core import video_dubs, video_sources
    from webui.pages import get_hub
    from webui.pages.videos import _look
    address = req.arg("url").strip()
    root, why = _root(req.settings)
    trouble = f'<p class="vnote" role="alert">{esc(why)}</p>' if why else ""
    sources = video_sources.enabled(req.settings)
    dubs = video_dubs.enabled(req.settings)
    can_dubs = getattr(video_dubs, "AVAILABLE", True)    # the public copy has none (its core/video_dubs: a stand-in)
    sites = ["hentasis1.top", "hentai-xs.com", "hentaihaven.xxx"]
    embeds = _has_embeds()
    sites += ["hfshare.me"] if embeds else []
    sites += [f"YummyAnime — все озвучки (Kodik, CVH{', AniLiberty' if sources else ''})"] if dubs \
        else ["YummyAnime — серии с AniLiberty"] if sources else []
    sites += ["AniLiberty"] if sources else []
    if dubs:
        frames = ("У YummyAnime программа берёт все озвучки и субтитры тайтла из плееров Kodik (до 720p) и CVH (до "
                  "1080p)" + (", а озвучку AniLibria — ещё и с её собственного сайта AniLiberty (до 1080p)" if sources else "")
                  + ": каждая озвучка — своя вкладка сериала. Отметьте нужные серии или «пробную серию» озвучки, "
                  "выберите качество и нажмите «Скачать». Плееры Alloha и Aksor программа не скачивает.")
    elif sources:
        frames = ("У YummyAnime видео в чужом плеере (Kodik) — сейчас программа его не трогает, а серии этого тайтла ищет на "
                  "AniLiberty по номерам Shikimori и MyAnimeList: это будет тот же тайтл и тот же сезон, а описание и "
                  "постер останутся с YummyAnime. Ссылка прямо на релиз AniLiberty тоже подходит."
                  + (" Все озвучки из Kodik и CVH — это включается в «Настройки → Видео»." if can_dubs else ""))
    else:
        frames = ("С YummyAnime и других сайтов с плеером Kodik и подобными программа сейчас не скачивает. "
                  + ("Все озвучки YummyAnime или серии с AniLiberty" if can_dubs else "Серии с AniLiberty")
                  + " — это включается в «Настройки → Видео».")
    chips = "".join(f'<li>{esc(site)}</li>' for site in sites) + ('<li class="vget-any">и другие — пробуйте любую ссылку</li>'
                                                                 if embeds else "")
    embeds_help = ("""      <p>Если видео на странице — в плеере другого сайта (как на hfshare: несколько плееров, в каждом все серии),
      программа открывает каждый плеер и берёт видео из первого, который отдаёт его открыто; его субтитры
      кладутся рядом с серией. Так получается и с сайтами не из списка.</p>""" if embeds else "")
    merge_on = " checked" if req.settings.get("video_dub_merge", True) else ""
    autofind = ' data-autofind="1"' if address and req.arg("go") == "1" else ""
    body = f"""<div class="vget" id="vid-get"{autofind}>
  <section class="vget-ask" aria-labelledby="vget-title">
    <h1 id="vget-title">Скачать с сайта</h1>
    <p class="vget-lead">Вставьте ссылку на тайтл — программа найдёт его серии и скачает их в папку видео вместе с
      описанием и постером.</p>{trouble}
    <form class="vget-form" data-form="vid-find-url">
      <label class="vh" for="vid-find-url">Ссылка на тайтл</label>
      <div class="vget-row"><input id="vid-find-url" type="text" inputmode="url" name="url" spellcheck="false"
        autocomplete="off" value="{esc(address)}" placeholder="Ссылка на тайтл или на любую его серию — вставьте, поиск начнётся сам">
      <button type="submit" class="vbutton primary">Найти серии</button></div>
      <span class="action-status" role="status"></span>
    </form>
    <div class="vget-where">
      <span class="vget-sites-label">Подходят:</span><ul class="vget-sites">{chips}</ul>
    </div>
    <div class="vget-more">
      <form class="vget-saved" data-form="vid-find-file">
        <label class="vget-file">Сайт не пускает программу? <span>Загрузить сохранённую страницу</span>
          <input id="vid-find-file" type="file" name="file" accept=".html,.htm,.mhtml,.mht,.zip"></label>
        <span class="action-status" role="status"></span>
      </form>
    <details class="vget-help">
      <summary>Почему подходят не все сайты</summary>
      <p>Программа берёт только то, что страница отдаёт открыто: файл, на который ссылается плеер сайта, или открытый
      поток HLS. Если каждая серия на своей странице (как на hentaihaven), программа откроет их сама — хватит ссылки на
      тайтл или на любую серию.</p>
      <p class="vget-sources">{esc(frames)}</p>
{embeds_help}
      <p>Зашифрованные потоки и закрытые плееры программа не скачивает: это защита сайта. Если сайт не пускает
      программу (проверка на роботов), сохраните страницу в браузере (Ctrl+S) и загрузите файл.</p>
    </details>
    </div>
  </section>
  <section class="vget-status job" data-job="find" hidden aria-live="polite">
    <div class="job-head"><span class="vspin" aria-hidden="true"></span><b class="job-title"></b>
      <span class="job-status"></span></div>
    <p class="vget-step"></p>
    <details class="vlog"><summary>Подробности</summary>
      <pre class="job-log" tabindex="0" aria-label="Журнал поиска"></pre></details>
  </section>
  <section class="vget-going" hidden aria-label="Скачивание">
    <p class="vget-going-note" hidden aria-live="polite"></p>
  </section>
  <section class="vfound" hidden aria-labelledby="vfound-title">
    <div class="vcard">
      <div class="vcard-poster"><img alt="" hidden><span class="vcard-initial" aria-hidden="true"></span></div>
      <div class="vcard-head">
        <p class="vfound-head">Нашлось на странице</p>
        <h2 class="vfound-title" id="vfound-title"></h2>
        <p class="vfound-alt"></p>
      </div>
      <div class="vcard-more">
        <ul class="vfound-facts"></ul>
        <p class="vfound-about" hidden title="Нажмите, чтобы прочитать целиком"></p>
        <div class="vfound-sources" hidden></div>
        <p class="vfound-where"></p>
        <div class="vfound-notes"></div>
      </div>
    </div>
    <div class="vpick">
      <div class="vpick-head"><h3>Серии</h3><span class="vpick-count" aria-live="polite"></span>
        <span class="vpick-tools" role="group" aria-label="Отметить"><span class="vpick-label">Отметить:</span>
          <button type="button" class="link" data-pick="new">новые</button>
          <button type="button" class="link" data-pick="all">все</button>
          <button type="button" class="link" data-pick="none">ничего</button></span></div>
      <div class="vfound-list" role="group" aria-label="Серии и файлы"></div>
    </div>
    <div class="vbar">
      <div class="vbar-go tool-actions"><button type="button" class="vbutton primary" data-action="vid-download">Скачать</button>
        <label class="vmerge" hidden><input type="checkbox" name="merge"{merge_on}> Собрать озвучки серии в один файл</label>
        <label class="vquality" hidden>Качество <select name="quality">
          <option value="best">лучшее</option><option value="1080">не выше 1080p</option>
          <option value="720">не выше 720p</option><option value="480">не выше 480p</option>
          <option value="360">не выше 360p</option></select></label>
        <span class="vbar-sum"></span><span class="action-status" role="status"></span></div>
      <div class="vbar-run job" data-job="download" hidden>
        <div class="job-head tool-actions"><b class="job-title"></b> <span class="job-status"></span>
          <span class="action-status" role="status"></span>
          <span class="vbar-buttons"><button type="button" class="vbutton" data-action="vid-download-pause" hidden>Пауза</button>
          <button type="button" class="vbutton" data-action="vid-download-stop" hidden>Остановить</button></span></div>
        <ul class="vqueue" hidden aria-label="Очередь скачивания"></ul>
        <div class="vmeter" hidden><progress max="1000" value="0"></progress><b class="vmeter-pct"></b><span class="vmeter-text"></span></div>
        <div class="vbar-end"><p class="vget-result" hidden></p>
          <p class="vget-open" hidden><a class="vbutton primary" href="/videos">Открыть сериал</a></p></div>
        <details class="vlog"><summary>Подробности</summary>
          <pre class="job-log" tabindex="0" aria-label="Журнал скачивания"></pre></details>
      </div>
    </div>
  </section>
</div>"""
    # (v5.44.75) «Скачать → Видео»: the page under the frame of «Скачать» (get_hub.py) — nothing else of it changed
    return get_hub.frame(req, "Скачать с сайта", body, part="video", kind="video-get", assets=("videos",), **_look())


# ── «Найти видео» ────────────────────────────────────────────────────────────────────
def _start_find(req: Request, label: str, get_page, address: str = "") -> Response:
    from core import page_jobs
    from webui.pages.videos import _window_fetch
    root, why = _root(req.settings)
    if root is None:
        return Response.json({"ok": False, "error": why}, 409)
    window = _window_fetch()
    settings = _find_settings(req.settings)

    def run(say, stop_check):
        return _find_with(address, get_page, root, settings, window, say, stop_check)
    try:
        task = page_jobs.start(FIND, label, run)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца."}, 409)
    return Response.json({"ok": True, "id": task.id})


def _find_settings(settings: dict) -> dict:
    return {key: settings.get(key) for key in ("video_source_aniliberty", "aniliberty_url", "video_source_dubs",
                                               "yummy_api_url")}


def _find_with(address: str, get_page, root: Path, settings: dict, window, say, stop_check) -> dict:
    """«Найти серии»: what a page (``address``; ``get_page(say)`` reads it — a saved one too) offers →
    {"found", "root", "address"} | {"failed"} | {"stopped"}."""
    from core import video_download, video_dubs, video_sources
    from core import video_meta_import as I
    if get_page is None:
        def get_page(say):
            say(f"Страница: {address}")
            return I.page_from_url(address, progress=say, window_fetch=window)
    try:                                         # a page of AniLiberty: its release, the page is not read
        found = video_sources.find_alias(address, root, settings=settings, say=say, stop_check=stop_check)
    except video_sources.SourceError as exc:
        return {"failed": str(exc)}
    dub_failure = ""
    if found is None:                            # a page of YummyAnime: all its voice-overs, by its API
        try:
            found = video_dubs.find_address(address, root, settings=settings, say=say, stop_check=stop_check)
        except video_dubs.DubError as exc:
            if stop_check():
                return {"stopped": True}
            say(f"Озвучки из API {video_dubs.NAME} не получены: {exc} — читаю саму страницу")
            found, dub_failure = None, str(exc)
    if found is None:
        try:
            got = get_page(say)
        except I.MetaImportError as exc:
            return {"failed": str(exc)}
        from core import video_watch             # (v5.44.139) a site of hentai: the program's gate, as «Смотреть»
        closed = video_watch.page_closed(address, getattr(got, "html", ""), getattr(got, "url", "") or address)
        if closed:
            return {"failed": closed}

        def fetch(address: str):                 # the pages of the series' episodes, its own page
            return I.page_from_url(address, progress=lambda _text: None, window_fetch=window)
        found = video_download.look(got, root, say=say, stop_check=stop_check, fetch=fetch)
        dubs = None
        if not found.files and not dub_failure:  # its video is another site's player (YummyAnime): its
            try:                                 # voice-overs from the players, by the site's API
                dubs = video_dubs.find(got, root, settings=settings, say=say, stop_check=stop_check)
            except video_dubs.DubError as exc:
                if stop_check():
                    return {"stopped": True}
                say(f"Озвучки из API {video_dubs.NAME} не получены: {exc}")
                dub_failure = str(exc)
        if dubs is not None:
            found = dubs
        elif not found.files and not found.frame_site and _embeds(found, say, stop_check):
            pass                                 # … else its episodes through other sites' players in its frames
        elif not found.files:                    # … else the title on a site that gives its episodes
            try:
                found = video_sources.find(got, root, settings=settings, say=say, stop_check=stop_check) or found
            except video_sources.SourceError as exc:
                then = _nothing(found, exc.summary or str(exc), advice=False)
                return {"failed": _nothing(found, str(exc)),
                        "history_error": f"{dub_failure}. {then}" if dub_failure else then}
    videos = sum(1 for f in found.files if f.kind == "video")
    sounds = len(found.files) - videos
    if found.files and all(f.blocked for f in found.files):
        return {"failed": "Скачать с этой страницы нечего: " + found.files[0].blocked}
    if found.files:
        say(f"Нашлось серий во всех озвучках: {videos}" if found.choose == "dubs" else
            f"Нашлось видео: {videos}" + (f", звук: {sounds}" if sounds else ""))
        say(f"Папка: {found.folder}" + (" (новая)" if found.new_folder else " (уже есть — докачается в неё)"))
        poster_key = ""
        if not stop_check():                     # the poster for the list, written with the description later
            found.poster = I.poster_of(found.about or found.page, say=say, stop_check=stop_check)
            if found.poster is not None:
                from core import video_download_covers
                poster_key = video_download_covers.remember(address, found.poster)
        return {"found": found, "root": str(root), "address": address, "poster_key": poster_key}
    failure = {"failed": _nothing(found, off=not video_sources.enabled(settings))}
    if dub_failure:
        failure["history_error"] = dub_failure
    return failure


def _has_embeds() -> bool:
    try:
        from core import video_embeds  # noqa: F401 - the public copy has none
    except ImportError:
        return False
    return True


def _embeds(found, say, stop_check) -> bool:
    """The episodes of a page through other sites' players in its frames (``core/video_embeds``: hfshare)."""
    try:
        from core import video_embeds
    except ImportError:                          # the public copy: the players' code is not in it
        return False
    try:
        return video_embeds.fill(found, say=say, stop=stop_check)
    except Exception as exc:  # noqa: BLE001 - a player of another site that breaks the reading: the page as it was
        _LOG.warning("other sites' players not read: %s: %s", type(exc).__name__, exc)
        say(f"Плееры других сайтов не прочитались: {exc}")
        return False


def _nothing(found, elsewhere: str = "", *, off: bool = False, advice: bool = True) -> str:
    """Why a page gave no file, in words (``elsewhere`` — why the title's episodes are not on AniLiberty either)."""
    if not (found.frame_site or found.frames or found.hidden):
        if elsewhere:
            return elsewhere
        if found.streams:
            return "Видео на странице идёт потоком DASH (.mpd) — такие программа не скачивает"
        return "На странице нет ссылок на видеофайлы и страниц серий — это точно страница тайтла?"
    if found.frames or not found.hidden:
        where = f" ({', '.join(found.frames[:3])})" if found.frames else ""
        text = (f"Видео на этой странице показывает плеер другого сайта{where}: он прячет, откуда берётся файл, — "
                "оттуда программа не скачивает.")
    else:                                            # its own player hides the files: coded (Playerjs «#…») or
        text = ("Плеер страницы прячет адреса видео (они зашифрованы, или вместо них стоят заглушки) — такие "  # placeholders
                "видео программа не скачивает.")                                                                 # (Jut.su)
    if elsewhere:
        text += f" {elsewhere}."
    elif off and (found.frame_site or found.frames):  # a title another site's player shows: AniLiberty may have it
        text += (" Серии такого тайтла программа может найти на AniLiberty — это включается в «Настройки → "
                 "Видео».")
    return text + " Описание и постер можно взять: «Описание с сайта» на странице сериала" if advice else text


def find_url(req: Request) -> Response:
    from core import video_meta_import as I
    from webui.pages.videos import _window_fetch
    try:
        address = I.check_url(str(req.json().get("url") or ""))
    except I.MetaImportError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 400)
    window = _window_fetch()

    def get_page(say):
        say(f"Страница: {address}")
        return I.page_from_url(address, progress=say, window_fetch=window)
    return _start_find(req, "Поиск серий", get_page, address)


def find_file(req: Request) -> Response:
    from core import video_meta_import as I
    name = Path(req.arg("name") or "page.html").name
    if not name.lower().endswith(I.SAVED_EXTS):
        return Response.json({"ok": False, "error": "Нужна сохранённая страница: .html, .mhtml или .zip"}, 400)
    if not req.body:
        return Response.json({"ok": False, "error": "Файл пустой"}, 400)
    body = req.body

    def get_page(say):
        say(f"Сохранённая страница: {name} ({len(body) // 1024} КБ)")
        return I.page_from_file(name, body)
    return _start_find(req, "Поиск серий в сохранённой странице", get_page)


def _found_of(task):
    result = task.result if task is not None and task.status == "done" else None
    return result if isinstance(result, dict) and result.get("found") is not None else None


def find_state(req: Request) -> Response:
    from core import page_jobs
    task = page_jobs.current(FIND)
    if task is None:
        return Response.json({"ok": True, "status": "idle"})
    state = {"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0))}
    kept = _found_of(task)
    if kept is not None:
        state["found"] = kept["found"].public(Path(kept["root"]))
        # (v5.44.142) the address asked and how long ago the list came: «Скачать серии…» of a series' page takes a
        # list of its own page found a moment ago as it is (what is in the folder is looked at anew above)
        state["address"] = str(kept.get("address") or "")
    if task.finished_at:
        state["age"] = int(max(0.0, time.time() - task.finished_at))
    return Response.json(state)


def find_poster(req: Request) -> Response:
    """The poster of the title the last «Найти серии» found (``find`` — its number: a list of before has none)."""
    from core import page_jobs
    task = page_jobs.current(FIND)
    kept = _found_of(task)
    poster = kept["found"].poster if kept is not None and req.arg("find") == str(task.id) else None
    if not poster:
        return Response.not_found("Постера нет")
    data, ext, _where = poster
    kinds = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
             ".gif": "image/gif", ".avif": "image/avif", ".bmp": "image/bmp"}
    return Response(200, bytes(data), kinds.get(str(ext).lower(), "image/jpeg"), {"Cache-Control": "private, max-age=600"})


# ── «Скачать» ─────────────────────────────────────────────────────────────────────────
def download(req: Request) -> Response:
    from core import page_jobs, video_download, video_dubs
    body = req.json()
    task = page_jobs.current(FIND)
    kept = _found_of(task)
    if kept is None or str(body.get("find") or "") != str(task.id):
        return Response.json({"ok": False, "error": "Список файлов устарел — нажмите «Найти видео» ещё раз"}, 409)
    chosen = body.get("files")
    quality = str(body.get("quality") or "")
    merge = bool(body.get("merge")) and found_merges(kept)
    # (v5.44.150) «Заменить картинку у серий в папке»: with the voice-overs into one file — the episodes there get
    # the title's best picture too; nothing need be ticked then
    repicture = bool(body.get("repicture")) and merge
    if not isinstance(chosen, list) or not all(isinstance(n, int) for n in chosen) or not (chosen or repicture):
        return Response.json({"ok": False, "error": "Отметьте файлы, которые скачать"}, 400)
    if quality and quality not in video_dubs.QUALITIES:
        return Response.json({"ok": False, "error": "Непонятное качество"}, 400)
    root, why = _root(req.settings)
    if root is None:
        return Response.json({"ok": False, "error": why}, 409)
    found = kept["found"]
    saved = _saved_of(str(kept.get("address") or ""), found, chosen, quality, merge, repicture)
    shown = _shown_of(str(kept.get("address") or ""), found, chosen, quality, merge)
    address = str(kept.get("address") or "")

    def planned(places: list) -> None:           # (v5.44.150) the lent pictures joined: the series' page shows them
        with _QUEUE_LOCK:
            if _CURRENT.get("shown"):
                _CURRENT["shown"] = _shown_of(address, found, places, quality, merge)
    def work(say, stop_check):
        offered = [_file_pair(remote) for remote in found.files] if saved else []
        result = video_download.download(found, chosen, root, say=say, stop_check=stop_check, quality=quality,
                                         merge=merge, repicture=repicture, planned=planned)
        return _with_retry(result, saved, offered, chosen)

    title = found.title or found.folder.name
    label = f"Скачать с сайта: «{title}»"
    admitted = _schedule(label, saved, work, shown, about=task.id, title=title, chosen=chosen, found=found,
                         poster_key=str(kept.get("poster_key") or ""))
    if admitted is not None:
        return Response.json({"ok": True, **admitted}, 200 if admitted.get("ok", True) else 409)
    run = _job(saved, work, shown)
    try:
        started = page_jobs.start(GET, label, run, about=task.id)
    except page_jobs.TaskBusy:
        with _QUEUE_LOCK:                            # the same list asked again: its choice now, its place kept
            entry = next((q for q in _QUEUE if q["find"] == task.id), None)
            if entry is None:
                entry = {"id": next(_QUEUE_IDS), "find": task.id}
                _QUEUE.append(entry)
            entry.update(title=title, chosen=list(chosen), label=label, run=run, saved=saved, shown=shown)
            entry.pop("count", None)
            place = _QUEUE.index(entry) + 1
        _persist()
        going = page_jobs.current(GET)               # one ending just now: the next starts (not after a stop)
        threading.Thread(target=_next, kwargs={"after": going.id if going is not None else None},
                         name="video-download-queue", daemon=True).start()
        return Response.json({"ok": True, "queued": place, "queue_id": entry["id"]})
    return Response.json({"ok": True, "id": started.id})


# ── the downloads kept between runs of the program ────────────────────────────────────
def _schedule(label: str, saved: dict | None, work, shown: dict | None, *, about, title: str, chosen: list,
              found=None, restored: bool = False, poster_key: str = "") -> dict | None:
    import hashlib
    from types import SimpleNamespace
    from core import content_marks, download_scheduler, video_download
    address = str((saved or {}).get("address") or "")
    facts = dict(found.facts or {}) if found is not None else {}
    source = str(found.page.url or address) if found is not None else address
    hentai = not source or bool(content_marks.series_reason(SimpleNamespace(info={**facts, "source_url": source})))
    identity = json.dumps(saved, sort_keys=True) if restored else f"{about}:{address}"
    dedupe = hashlib.sha256(identity.encode()).hexdigest()
    durable = video_download.restorable_address(address)
    if restored and not durable:
        return None
    return download_scheduler.submit(
        "video", label, None if restored else _job(saved, work, shown, scheduled=True),
        record=saved if durable else None, about=about,
        dedupe=dedupe, replace_waiting=not restored,
        presentation={"title": title, "find": about, "count": len((saved or {}).get("files", chosen)),
                      "chosen": list(chosen), "url": address if durable else "", "hentai": hentai,
                      "poster_key": poster_key})


def _keep_file() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / KEEP_NAME


def _saved_of(address: str, found, chosen: list, quality: str, merge: bool, repicture: bool = False) -> dict | None:
    """What a download is, to begin it again after a restart: its page's address and its files by their sub-folder
    and title (the file's extension is its player's — it may be another then); None for a saved page (no address).
    (v5.44.150) ``repicture`` — the episodes of the folder get the title's best picture too."""
    from core import video_download
    if not video_download.restorable_address(address):
        return None
    files = [list(_file_pair(found.files[n])) for n in chosen if 0 <= n < len(found.files)]
    return {"address": address, "title": found.title or found.folder.name, "files": files, "quality": quality,
            "merge": bool(merge), "repicture": bool(repicture)}


def _file_pair(remote) -> tuple[str, str]:
    return remote.group or "", remote.title or remote.name


def _retry_record(saved: dict, pairs, *, aliases: bool = False) -> dict:
    retry = {"address": saved["address"], "title": str(saved.get("title") or ""),
             "files": [list(pair) for pair in dict.fromkeys(pairs)], "quality": str(saved.get("quality") or ""),
             "merge": bool(saved.get("merge")), "history_retry": True}
    if aliases:
        retry["match_aliases"] = True
    if saved.get("library_series"):
        retry["library_series"] = saved["library_series"]
    return retry


def _with_retry(result: dict, saved: dict | None, offered: list, chosen: list, *, missing: tuple = (),
                matched: dict | None = None, aliases: bool = False) -> dict:
    """Keep only explicitly chosen, unfinished files. Picture donors are selected afresh by the merger."""
    if not saved:
        return result
    states = result.get("states") or {}
    failed = result.get("failed") and not states
    stopped = result.get("stopped")
    if not (missing or failed or stopped or result.get("failed_files")):
        return result
    identities = Counter(offered)
    originals = {n: pair for pair, n in (matched or {}).items()}
    pairs = list(missing)
    for n in dict.fromkeys(chosen):
        if not 0 <= n < len(offered):
            continue
        state = states.get(str(n))
        if state in ("failed", "dead", "stopped") or failed or (stopped and state in (None, "queued", "paused", "active")):
            pair = offered[n]
            if identities[pair] == 1:
                pairs.append(originals.get(n, pair))
    if pairs:
        result["retry"] = _retry_record(saved, pairs, aliases=aliases)
    if missing:
        result["failed_files"] = int(result.get("failed_files") or 0) + len(missing)
    return result


def _shown_of(address: str, found, chosen: list, quality: str, merge: bool) -> dict:
    """(v5.44.142) What a download is for the page of its series: its page's address, its title, each video file's
    episode and sub-folder by its place in the list (``tiles``: {"3": ["2", "Озвучка AniLibria"]} — the numbers of
    its tiles, ``video_download.tile_labels``), its sub-folders in order, «Качество», «в один файл»."""
    from core import video_download
    labels = video_download.tile_labels(found.files)
    places = [n for n in chosen if isinstance(n, int) and 0 <= n < len(found.files)]
    tiles = {str(n): [labels[n], found.files[n].group or ""] for n in places if found.files[n].kind != "audio"}
    groups: list = []
    for n in places:
        if (found.files[n].group or "") not in groups:
            groups.append(found.files[n].group or "")
    return {"address": address, "title": found.title or found.folder.name, "tiles": tiles, "groups": groups,
            "quality": quality, "merge": bool(merge), "count": len(places)}


GONE = ("done", "whole", "other", "failed", "dead")      # how a file of a download ends (``video_download.download``)
KEEP_LAST = 180.0                                       # seconds the end of a download is told to its series' page


def going_for(address: str) -> dict:
    """(v5.44.142) What the downloads do for the title of the page ``address`` (a series' page — ``/api/series/getting``):
    {"going": the download going for it (``_going_public``) | None, "queued": its place in the queue | None,
    "other": the title of another one going ("" — none), "ended": how the last one of it ended a moment ago}."""
    from core import page_jobs, video_download
    out: dict = {"going": None, "queued": None, "other": "", "ended": None}
    task = page_jobs.current(GET)
    running = task is not None and task.running
    with _QUEUE_LOCK:
        shown = dict(_CURRENT.get("shown") or {})
        queue = [{"id": q["id"], "title": str(q.get("title") or ""), "shown": dict(q.get("shown") or {}),
                  "saved": dict(q.get("saved") or {}), "count": q.get("count", len(q.get("chosen") or []))}
                 for q in _QUEUE]
        last = dict(_LAST)
    from core import download_scheduler
    queue.extend({"id": item["id"], "title": item["title"], "shown": {}, "saved": dict(record),
                  "count": item["count"], "postponed": item["lane"] == "later"}
                 for item, record in download_scheduler.waiting("video"))
    mine = bool(address) and running and bool(shown) and video_download._same_page(shown.get("address"), address)
    if mine:
        out["going"] = _going_public(shown, video_download.meter(), task)
    elif running:
        out["other"] = str(shown.get("title") or task.label.replace("Скачать с сайта: ", "").strip("«»"))
    if not address:
        return out
    before = out["other"] if running else ""
    for place, entry in enumerate(queue, 1):
        where = entry["shown"].get("address") or entry["saved"].get("address")
        if video_download._same_page(where, address):
            tiles = entry["shown"].get("tiles") or {}
            out["queued"] = {"id": entry["id"], "place": place, "files": entry["count"], "before": before,
                             "labels": sorted({str(t[0]) for t in tiles.values()}, key=V.natural_key),
                             "postponed": bool(entry.get("postponed"))}
            break
        before = entry["title"]
    if not mine and last and video_download._same_page(last.get("address"), address) \
            and time.time() - float(last.get("at") or 0) < KEEP_LAST:
        out["ended"] = {k: last.get(k) for k in ("at", "done", "whole", "failed", "stopped", "merged", "error")}
    return out


def _going_public(shown: dict, meter: dict, task) -> dict:
    """The download going, episode by episode, for the tiles of its series: {"labels": {episode: {"state": active /
    paused / queued / done / failed / stopped, "part": 0…1 of its files, "files"}}, "pairs": [{label, group,
    state}], "now": the file going {label, group, part}, "count", "ended", "paused", "merging", "eta" (seconds,
    0 — not known yet), its sub-folders, «Качество», «в один файл», its title}."""
    from core import video_download
    states = {str(k): str(v) for k, v in (meter.get("states") or {}).items()}
    tiles = shown.get("tiles") or {}
    now = str(meter.get("file")) if meter.get("file") is not None and not meter.get("merging") else ""
    total = float(meter.get("total") or 0)
    part_now = max(0.0, min(1.0, float(meter.get("done") or 0) / total)) if total > 0 else 0.0
    labels: dict = {}
    pairs: list = []
    ended = downloaded = 0
    for place, (label, group) in tiles.items():
        state = states.get(place) or "queued"
        slot = labels.setdefault(str(label), {"files": 0, "ended": 0, "part": 0.0, "of": []})
        slot["files"] += 1
        slot["of"].append(state)
        if state in GONE:
            slot["ended"] += 1
            ended += 1
        downloaded += state == "done"
        if place == now and state in ("active", "paused"):
            slot["part"] += part_now
        pairs.append({"label": str(label), "group": str(group), "state": state})
    for slot in labels.values():
        of = slot.pop("of")
        if "paused" in of:
            slot["state"] = "paused"
        elif "active" in of:
            slot["state"] = "active"
        elif all(s in ("done", "whole", "other") for s in of):
            slot["state"] = "done"
        elif "queued" in of:
            slot["state"] = "queued"
        elif "stopped" in of:
            slot["state"] = "stopped"
        else:
            slot["state"] = "failed"
        slot["part"] = round((slot["ended"] + slot["part"]) / max(1, slot["files"]), 3)
    current = None
    if now in tiles:
        current = {"label": str(tiles[now][0]), "group": str(tiles[now][1]), "part": round(part_now, 3)}
    elapsed = max(0.0, time.time() - float(getattr(task, "started_at", 0) or time.time()))
    left = len(tiles) - ended
    eta = int(elapsed / downloaded * left) if downloaded >= 2 and left > 0 else 0
    return {"labels": labels, "pairs": pairs, "now": current, "count": len(tiles), "ended": ended,
            "paused": video_download.paused(), "merging": bool(meter.get("merging")), "eta": eta,
            "groups": list(shown.get("groups") or []), "quality": str(shown.get("quality") or ""),
            "merge": bool(shown.get("merge")), "title": str(shown.get("title") or "")}


def _persist() -> None:
    """``video_queue.json``: the download going (when it is to be begun again) and the queue after it."""
    from core.file_safety import write_json_kept
    with _KEEP_LOCK:
        with _QUEUE_LOCK:
            records = ([_CURRENT["saved"]] if _CURRENT.get("saved") and not _CURRENT.get("scheduled") else []) + [
                q["saved"] for q in _QUEUE if q.get("saved")]
        try:
            write_json_kept(_keep_file(), json.dumps({"downloads": records}, ensure_ascii=False), dict)
        except OSError as exc:
            _LOG.warning("the queue of downloads not kept: %s", exc)


def _job(saved: dict | None, work, shown: dict | None = None, *, scheduled: bool = False):
    """A download as a page task: kept as the one going while it goes; when it ends, the next of the queue starts.
    Ended by the program closing (its stop, not «Остановить») — kept, to be begun again at the next start.
    ``shown`` — what it is for the page of its series (``_shown_of``; a download of the last run sets it itself once
    its page is found again)."""
    def run(say, stop_check):
        _USER_STOP.clear()
        with _QUEUE_LOCK:
            _CURRENT.update(saved=dict(saved) if saved is not None else None,
                            shown=dict(shown) if shown else None, scheduled=scheduled)
        if not scheduled:
            _persist()
        result = None
        try:
            result = work(say, stop_check)
            return result
        except Exception as exc:
            from core.video_download import restorable_address
            if not saved or not saved.get("files") or not restorable_address(saved.get("address") or ""):
                raise
            import traceback
            say(traceback.format_exc()[-2000:])
            result = {"failed": f"{type(exc).__name__}: {exc}",
                      "retry": _retry_record(saved, map(tuple, saved["files"]),
                                             aliases=not saved.get("history_retry") or bool(saved.get("match_aliases")))}
            return result
        finally:
            leaving = not scheduled and stop_check() and not _USER_STOP.is_set()
            with _QUEUE_LOCK:
                if not leaving:
                    _CURRENT["saved"] = None
                ended = _CURRENT.get("shown") or {}
                _CURRENT["shown"] = None
                if ended.get("address"):                 # (v5.44.142) for its series' page: how it ended
                    done = result if isinstance(result, dict) else {}
                    _LAST.clear()
                    _LAST.update(address=ended["address"], at=time.time(), done=int(done.get("done") or 0),
                                 whole=int(done.get("whole") or 0), failed=int(done.get("failed_files") or 0),
                                 stopped=bool(done.get("stopped") or stop_check()), merged=int(done.get("merged") or 0),
                                 error=str(done.get("failed") or ""))
            if not scheduled:
                _persist()
                if not stop_check():                 # the next one in the queue, when this one has ended
                    threading.Thread(target=_next, name="video-download-queue", daemon=True).start()
    return run


def _await_restore() -> bool:
    """The downloads of the last run, before ``restore`` begins them (``RESTORE_AFTER`` from now): their titles kept
    for the page.  False — none."""
    if _RESTORED.is_set():
        return False
    records = _kept_records()
    if not records:
        return False
    with _QUEUE_LOCK:
        _PENDING.update(titles=[str(r.get("title") or r["address"]) for r in records],
                        at=time.monotonic() + RESTORE_AFTER)
    return True


def restoring_public() -> dict | None:
    """{"titles", "in": seconds} of the downloads to be begun again soon; None when none waits."""
    with _QUEUE_LOCK:
        if _RESTORED.is_set() or not _PENDING["titles"]:
            return None
        return {"titles": list(_PENDING["titles"]), "in": max(0, int(round(_PENDING["at"] - time.monotonic())))}


def _restore_later() -> None:
    try:
        from core.settings import load_settings
        time.sleep(RESTORE_AFTER)
        restore(load_settings())
    except Exception as exc:   # noqa: BLE001 - a queue that is not restored: the program goes on without it
        _LOG.warning("the queue of downloads not restored: %s: %s", type(exc).__name__, exc)


def _kept_records() -> list:
    """The downloads ``video_queue.json`` keeps (those with a page's address and their files); [] when none or it
    cannot be read now."""
    from core.file_safety import read_json_kept
    from core.video_download import restorable_address
    try:
        data = read_json_kept(_keep_file(), dict, what="video queue")
    except OSError as exc:
        _LOG.warning("the queue of downloads cannot be read now: %s", exc)
        return []
    return [r for r in (data or {}).get("downloads") or [] if isinstance(r, dict)
            and restorable_address(str(r.get("address") or "")) and isinstance(r.get("files"), list)]


FIND_TRIES = 3                          # a download of the last run: its page asked again so many times …
FIND_AGAIN = 60.0                       # … this many seconds apart


def _wait(seconds: float, stop_check) -> bool:
    """``seconds`` waited; True — stopped meanwhile."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop_check():
            return True
        time.sleep(0.25)
    return False


def _pair_key(group: str, title: str, *, aliases: bool) -> tuple | None:
    """AniLiberty headings can change; its episode number and studio stay the same."""
    from core.video_sources import DUB, SPECIALS
    group, title = (" ".join(name.casefold().split()) for name in (group, title))
    if group in (DUB.casefold(), (DUB + SPECIALS).casefold()):
        episode = re.fullmatch(r"серия ([0-9]+(?:\.[0-9]+)?)(?: — .+)?", title)
        if episode:
            return group, episode[1]
    if aliases:
        group = re.sub(r"^озвучка\b", "", group)
        return re.sub(r"[\W_]+", "", group), title
    return None


def _restore_matches(wanted: tuple, offered: list, *, aliases: bool) -> dict:
    wanted_set = set(wanted)
    exact = Counter(offered)
    matches = {pair: n for n, pair in enumerate(offered) if pair in wanted_set and exact[pair] == 1}
    wanted_keys = [_pair_key(*pair, aliases=aliases) for pair in wanted]
    requested = {alias: pair for alias, pair in zip(wanted_keys, wanted) if alias is not None}
    available = [_pair_key(*pair, aliases=aliases) for pair in offered]
    counts = Counter(wanted_keys) + Counter(available)
    for n, alias in enumerate(available):
        if alias in requested and counts[alias] == 2:
            matches[requested[alias]] = n
    return matches


def _restore_work(record: dict, settings: dict):
    from core import video_download, video_dubs
    from webui.pages.videos import _window_fetch
    root, why = _root(settings)
    history_retry = bool(record.get("history_retry"))
    aliases = not history_retry or bool(record.get("match_aliases"))
    wanted = tuple(dict.fromkeys(tuple(pair) for pair in record["files"] if isinstance(pair, list) and len(pair) == 2
                                and all(isinstance(name, str) for name in pair)))
    retry = {"retry": _retry_record(record, wanted, aliases=aliases)} if wanted and \
        video_download.restorable_address(record["address"]) else {}
    if root is None:
        return lambda say, stop_check: {"failed": why, **retry}
    window = _window_fetch()
    found_settings = _find_settings(settings)

    def work(say, stop_check):
        from core import video_retry
        action = "Повтор загрузки" if history_retry else "С прошлого запуска программы"
        say(f"{action}: «{record.get('title') or record['address']}» — ищу его серии заново")
        evidence = record.get("library_series")
        inferred = bool(evidence and not wanted)
        if evidence:
            series, address = video_retry.library_source(root, {"series": evidence, "url": record["address"]})
            context = {"series": evidence, **(retry or {"retry": {"url": record["address"]}})}
            if series is None or not address:
                return {"failed": "Папка сериала или адрес его страницы изменились — выберите озвучки заново",
                        "manual_video_choice": True, **context}
            context["folder"] = series.path
        else:
            context = retry
        got, matches, offered = {}, {}, []
        for attempt in range(1, FIND_TRIES + 1):  # the network just after a start (a VPN, the site) may not be up yet
            got = _find_with(str(record["address"]), None, root, found_settings, window, say, stop_check)
            if got.get("found") is not None:
                offered = [_file_pair(f) for f in got["found"].files]
                if inferred:
                    break                         # the remaining episodes are chosen from the current offer below
                matches = _restore_matches(wanted, offered, aliases=aliases)
            if matches or got.get("stopped") or stop_check() or attempt == FIND_TRIES:
                break
            if got.get("found") is not None and not wanted and record.get("repicture"):
                break                            # (v5.44.150) nothing ticked: the pictures of the folder alone
            why = got.get("failed") or ("серий из очереди на странице нет" if got.get("found") is not None
                                        else "серии не нашлись")
            say(f"Не нашлось ({why}) — пробую ещё раз через {int(FIND_AGAIN)} с ({attempt + 1}/{FIND_TRIES})")
            if _wait(FIND_AGAIN, stop_check):
                return {"stopped": True, **context}
        if got.get("found") is None:
            return {**got, **context} if got.get("failed") or got.get("stopped") else {"failed": "Серии не нашлись", **context}
        found = got["found"]
        if evidence:
            if stop_check():
                return {"stopped": True, **context}
            from core.video_download_covers import address_key
            if Path(series.path) != found.folder or address_key(record["address"]) != address_key(found.page.url) or any(
                    source.get("exact") is False for source in found.sources):
                return {"failed": "Источник предложил другой сериал или сезон — выберите серии и озвучки заново",
                        **context, "manual_video_choice": True, "retry": {"url": record["address"]}}
        if inferred:
            try:
                chosen, whole = _remaining_voices(record["voices"], found)
            except ValueError as exc:
                return {"failed": str(exc), "manual_video_choice": True, **context}
            if not chosen:
                return {"done": 0, "whole": whole, "folder": series.path, "series": series.id}
            matches = {offered[n]: n for n in chosen}
            say("Озвучки из первой скачанной серии: " + ", ".join(record["voices"])
                + f". Осталось скачать файлов: {len(chosen)}")
        again = bool(record.get("repicture")) and bool(record.get("merge")) and found.after is not None
        if not matches and not again:
            if stop_check():
                return {"stopped": True, **retry}
            return {"failed": "На странице не нашлись серии и озвучки из сохранённой очереди", **retry}
        chosen = sorted(matches.values())
        missing = tuple(pair for pair in wanted if pair not in matches)
        if missing:                              # said, not dropped silently (owner, 05.11.2026)
            gone = Counter(group or "Серии" for group, _title in missing)
            say("Этих файлов из очереди на странице сейчас нет — они не скачаются: "
                + ", ".join(f"{group} — {count}" for group, count in gone.items()))
        quality = str(record.get("quality") or "")
        quality = quality if quality in ("", *getattr(video_dubs, "QUALITIES", ())) else ""
        merge = bool(record.get("merge")) and found.after is not None
        with _QUEUE_LOCK:                        # (v5.44.142) its series' page: which episode each file is
            _CURRENT["shown"] = _shown_of(str(record["address"]), found, chosen, quality, merge)

        def planned(places: list) -> None:       # (v5.44.150) …and the lent pictures that joined
            with _QUEUE_LOCK:
                if _CURRENT.get("shown"):
                    _CURRENT["shown"] = _shown_of(str(record["address"]), found, places, quality, merge)
        result = video_download.download(found, chosen, root, say=say, stop_check=stop_check, quality=quality,
                                         merge=merge, start_paused=bool(record.get("paused")), repicture=again,
                                         planned=planned)
        return _with_retry(result, record, offered, chosen, missing=missing, matched=matches, aliases=aliases)
    return work


def restore_scheduled(record: dict, settings: dict):
    return _job(record, _restore_work(record, settings), scheduled=True)


def retry_download(settings: dict, retry: dict) -> dict:
    """Queue the failed episode and voice selections saved in a row of «Готово»."""
    from core import video_download
    address = str(retry.get("address") or "")
    files = retry.get("files")
    if not video_download.restorable_address(address) or not isinstance(files, list) or not files or not all(
            isinstance(pair, list) and len(pair) == 2 and all(isinstance(name, str) for name in pair) for pair in files):
        return {"ok": False, "error": "В истории нет списка файлов для повтора"}
    _path, why = _root(settings)
    if why:
        return {"ok": False, "error": why}
    record = _retry_record({**retry, "address": address, "title": retry.get("title") or address}, map(tuple, files),
                           aliases=bool(retry.get("match_aliases")))
    return _enqueue_retry(settings, record)


def _remaining_voices(voices: list[str], found) -> tuple[list[int], int]:
    """Resolve a recovered set of studios against regular episodes of this exact release."""
    from core import video_merge, video_sources
    wanted = {video_merge.dub_key(voice) for voice in voices}
    groups = {}
    episodes = set()
    offered = []
    for n, remote in enumerate(found.files):
        group = remote.group or ""
        key = video_merge.dub_key(group)
        number = re.fullmatch(r"Серия\s+0*([1-9]\d*)(?: — .+)?", remote.title, re.I)
        if key not in wanted or not number or remote.kind != "video" or group.endswith(video_sources.SPECIALS):
            continue
        episode = (key, number[1])
        if episode in episodes or groups.setdefault(key, group) != group:
            raise ValueError("На сайте несколько вариантов одной озвучки — выберите нужную вручную")
        episodes.add(episode)
        offered.append(n)
    if not wanted or groups.keys() != wanted:
        raise ValueError("На сайте не нашлись все озвучки из первой скачанной серии — выберите озвучки заново")
    # Not every source installs a merger. It can still recognize a named audio track already in the library MKV.
    had = found.had or video_merge.Merger(found.files, found.folder).had
    remaining = [n for n in offered if found.files[n].have != "whole" and not had(found.files[n])]
    if any(found.files[n].have == "other" for n in remaining):
        raise ValueError("В папке есть другой файл с именем серии — проверьте выбор перед скачиванием")
    return remaining, len(offered) - len(remaining)


def retry_from_library(settings: dict, entry: dict) -> dict:
    """An old history row with no exact selection: use two completed episodes or open the chooser."""
    from core import content_marks, video_retry
    from webui import content_mode
    root, _ = _root(settings)
    if root is None:
        return {"choose": True}
    series, address = video_retry.library_source(root, entry)
    if content_mode.mode() == "sfw" and (content_marks.adult_site(address) or
                                         series is not None and content_marks.series_verdict(series).hentai):
        return {"ok": False, "error": "Тайтл скрыт в этом режиме"}
    chooser = {"choose": True, "address": address}
    if series is None or not address or (entry.get("retry") or {}).get("choose"):
        return chooser
    folder = Path(series.path)
    voices = video_retry.first_episode_voices(folder)
    if not voices:
        return chooser
    merge = any(re.fullmatch(r"Серия\s+\d+\.mkv", path.name, re.I) for path in folder.glob("*.mkv"))
    return _enqueue_retry(settings, {"address": address, "title": series.title, "files": [], "quality": "",
                                     "merge": merge, "history_retry": True, "library_series": series.id,
                                     "voices": voices})


def _enqueue_retry(settings: dict, record: dict) -> dict:
    import hashlib
    from core import page_jobs
    title = record["title"]
    label = f"Скачать с сайта: «{title}»"
    about = "retry:" + hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    admitted = _schedule(label, record, None, None, about=about, title=title, chosen=[], restored=True)
    if admitted is not None:
        return {"ok": True, **admitted}
    run = _job(record, _restore_work(record, settings))
    with _QUEUE_LOCK:
        going = page_jobs.current(GET)
        if going is not None and going.running and going.about == about:
            return {"ok": True, "id": going.id, "already": True}
        entry = next((q for q in _QUEUE if q["find"] == about), None)
        if entry is None:
            entry = {"id": next(_QUEUE_IDS), "find": about, "title": title, "chosen": [], "count": len(record["files"]),
                     "label": label, "run": run, "saved": record}
            _QUEUE.append(entry)
        place = _QUEUE.index(entry) + 1
    _persist()
    _next(wait=0)
    started = page_jobs.current(GET)
    if started is not None and started.about == about:
        return {"ok": True, "id": started.id}
    threading.Thread(target=_next, kwargs={"after": started.id if started is not None else None},
                     name="video-download-queue", daemon=True).start()
    return {"ok": True, "queued": place, "queue_id": entry["id"]}


def restore(settings: dict) -> int:
    """Restore the old video queue through the chosen scheduler."""
    if _RESTORED.is_set():
        return 0
    _RESTORED.set()
    with _QUEUE_LOCK:
        _PENDING["titles"] = []
    records = _kept_records()
    root = Path(V.video_root(settings)) if V.video_root(settings) else None
    if not records or root is None or not root.is_dir():
        return 0
    for n, record in enumerate(records):
        title = str(record.get("title") or record["address"])
        label = f"Скачать с сайта: «{title}»"
        admitted = _schedule(label, record, None, None, about=f"saved{n}", title=title, chosen=[], restored=True)
        if admitted is not None and admitted.get("ok", True):
            continue
        with _QUEUE_LOCK:
            _QUEUE.append({"id": next(_QUEUE_IDS), "find": f"saved{n}", "title": title, "chosen": [],
                           "count": len(record["files"]), "label": label,
                           "run": _job(record, _restore_work(record, settings)), "saved": record})
    _LOG.info("downloads of the last run: %d — begun again", len(records))
    _persist()
    _next()
    return len(records)


def _next(wait: float = 10.0, after=None) -> None:
    """The first download of the queue started — once the one going has ended (the one that ends calls this before
    its task says so: it is waited for a while; one still going after that starts the queue itself at its end).
    ``after`` — the task going when one was put in the queue: stopped (the program closing) — nothing starts."""
    from core import page_jobs
    deadline = time.monotonic() + wait
    while True:
        task = page_jobs.current(GET)
        if task is None or not task.running:
            break
        if time.monotonic() >= deadline:
            return
        time.sleep(0.1)
    if after is not None and task is not None and task.id == after and task.status == "stopped":
        return
    with _QUEUE_LOCK:
        if not _QUEUE:
            return
        entry = _QUEUE.pop(0)
        try:
            page_jobs.start(GET, entry["label"], entry["run"], about=entry["find"])
        except page_jobs.TaskBusy:                   # another started meanwhile («Скачать» of the list shown)
            _QUEUE.insert(0, entry)


def queue_public() -> list:
    from core import download_scheduler
    with _QUEUE_LOCK:
        legacy = [{"id": q["id"], "find": q["find"], "title": q["title"], "files": q.get("count", len(q["chosen"])),
                   "chosen": list(q["chosen"])} for q in _QUEUE]
    return legacy + [{"id": item["id"], "find": item.get("find"), "title": item["title"], "files": item["count"],
                      "chosen": item["chosen"], "lane": item["lane"]} for item, _ in download_scheduler.waiting("video")
                     if item["lane"] == "next"]


def legacy_queue_busy() -> bool:
    with _QUEUE_LOCK:
        return bool(_QUEUE)


def reorder_queue(expected: list[int], order: list[int]) -> bool:
    """Reorder waiting video downloads if their queue still matches the page."""
    from core.download_queue_order import reordered
    with _QUEUE_LOCK:
        changed = reordered(_QUEUE, expected, order)
        if changed is None:
            return False
        if order == expected:
            return True
        _QUEUE[:] = changed
    _persist()
    return True


def measure(req: Request) -> Response:
    """What an episode of the title found weighs in each quality (every voice-over of a YummyAnime title: one episode
    asked of CVH — ``video_dubs.Resolver.measure``), for the estimate beside «Скачать».  (v5.44.150) And what the
    title's releases really give (``releases``: each one's real height, whether its cut is the original's —
    ``Resolver.look_over``), which of them the picture comes from when the voice-overs ``g`` go into one file within
    ``quality`` (``picture``: one not among them lends it), and how many episodes of the folder have a lower one
    (``lower`` — «Заменить картинку у серий в папке»)."""
    from core import page_jobs, video_dubs
    from core.video_meta_import import _session
    task = page_jobs.current(FIND)
    kept = _found_of(task)
    if kept is None or req.arg("find") != str(task.id):
        return Response.json({"ok": False, "error": "Список устарел"}, 409)
    resolver = kept["found"].resolve
    if resolver is None or not hasattr(resolver, "measure"):
        return Response.json({"ok": True, "measured": {}})
    reply = {"ok": True, "measured": {}}
    session = _session(kept["found"].page.url or "https://site.yummyani.me/")
    quality = req.arg("quality") if req.arg("quality") in getattr(video_dubs, "QUALITIES", ()) else ""
    groups = [str(g) for g in req.query.get("g") or [] if str(g).strip()]
    try:
        if hasattr(resolver, "release_facts"):
            with _LOOK_LOCK:                         # two pages at once: the releases are looked over once
                resolver.look_over(session)
            reply["releases"] = resolver.release_facts()
            reply["picture"] = resolver.picture_for(groups, quality) if groups and found_merges(kept) else {}
            reply["lower"] = (resolver.lower_in_folder(int(reply["picture"].get("height") or 0))
                              if reply["picture"] else {"episodes": 0, "height": 0})
        reply["measured"] = resolver.measure(session)
    except Exception as exc:   # noqa: BLE001 - an estimate that does not come: the page goes without it
        _LOG.info("measure: %s: %s", type(exc).__name__, exc)
    return Response.json(reply)


_LOOK_LOCK = threading.Lock()


def found_merges(kept: dict) -> bool:
    """The list can put its voice-overs into one file (every voice-over of a YummyAnime title)."""
    return kept["found"].after is not None


def download_state(req: Request) -> Response:
    from core import page_jobs, video_download
    task = page_jobs.current(GET)
    soon = restoring_public()
    if task is None:
        return Response.json({"ok": True, "status": "idle", "queue": queue_public(),
                              **({"restoring": soon} if soon else {})})
    state = {"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0)), "find": task.about,
             "queue": queue_public(), **({"restoring": soon} if soon else {})}
    result = task.result if isinstance(task.result, dict) else {}
    if task.running:
        state["meter"] = video_download.meter()
        state["paused"] = video_download.paused()
        state["states"] = dict(state["meter"].get("states") or {})
    else:                                            # how each file ended, and the sum of it
        state["states"] = dict(result.get("states") or {})
        state["summary"] = {k: result.get(k) for k in ("done", "whole", "failed_files", "stopped", "merged") if k in result}
    series = str(result.get("series") or "")
    root = Path(V.video_root(req.settings))
    if series and root.is_dir() and V.LIBRARY.get(str(root), series) is not None:
        state["series_url"] = f"/videos/s/{series}"
    return Response.json(state)


def download_stop(req: Request) -> Response:
    """«Остановить»: the download going, and the queue after it (a stop that the next one would undo is no stop)."""
    from core import download_scheduler, page_jobs
    task = page_jobs.current(GET)
    stopped = download_scheduler.stop_kind("video", task)
    if stopped is not None:
        return Response.json({"ok": True, "dropped": 0}) if stopped else Response.json(
            {"ok": False, "error": "Состояние загрузки изменилось или очередь недоступна"}, 409)
    _USER_STOP.set()
    with _QUEUE_LOCK:
        dropped = [q["title"] for q in _QUEUE]
        _QUEUE.clear()
        _CURRENT["saved"] = None
    _persist()
    if task is None or not task.running:
        if dropped:
            return Response.json({"ok": True, "dropped": len(dropped)})
        return Response.json({"ok": False, "error": "Скачивание не идёт"}, 409)
    if dropped:
        task._say("Очередь убрана: " + ", ".join(f"«{t}»" for t in dropped))
    task.stop()
    from core import video_download
    video_download.resume()                          # a stop while paused: not paused any more
    return Response.json({"ok": True, "dropped": len(dropped)})


def series_order(req: Request) -> Response:
    """«Порядок просмотра» on a series' page (owner, 02.11.2026: «а порядок просмотра с самого сериала, а не только
    скачивания?»): YummyAnime's viewing order of the title the series was downloaded from (its ``source_url``) —
    the seasons, films and extras, each with the series of the library that is it (by the same YummyAnime page), or
    the address to find and download it; and its ratings as they are now (its own people's first — the page puts
    them in «Оценки»).  [] for a series from elsewhere (and in the public copy)."""
    root = V.video_root(req.settings)
    series = V.LIBRARY.get(root, req.params["id"]) if Path(root).is_dir() else None
    if series is None:
        return Response.json({"ok": False, "error": "Такого сериала нет"}, 404)
    return _order_answer(req, str(series.info.get("source_url") or ""), series.id)


def title_order(req: Request) -> Response:
    """(v5.44.145) The same for a title of a site that has no folder yet (``?url=`` — its page on YummyAnime): the one
    page of a series (5.44.121) drew «Порядок просмотра» only for a series of the folder — the page «Смотреть» of
    before had it for every title (the owner, 10.11.2026: «и почему пропал порядок просмотра?»)."""
    return _order_answer(req, req.arg("url").strip(), "")


def _order_answer(req: Request, source: str, here_id: str) -> Response:
    from urllib.parse import quote

    from core import video_dubs
    root = V.video_root(req.settings)
    yummy = getattr(video_dubs, "is_yummy", None)
    if not getattr(video_dubs, "AVAILABLE", True) or yummy is None or not yummy(source) \
            or not video_dubs.alias_of(source):
        return Response.json({"ok": True, "order": []})
    alias = video_dubs.alias_of(source)
    with _ORDERS_LOCK:
        kept = _ORDERS.get(alias)
    if kept is None or time.monotonic() - kept[0] > ORDER_TTL:
        try:
            anime = video_dubs.Api(video_dubs.api_of(req.settings)).anime(alias)
        except (video_dubs.DubError, ValueError, TypeError, KeyError, AttributeError) as exc:
            _LOG.info("viewing order of %s: %s", alias, exc)
            return Response.json({"ok": False, "error": f"{video_dubs.NAME} не ответил — порядок просмотра не получен"})
        kept = (time.monotonic(), video_dubs.order_of(anime, source) if isinstance(anime, dict) else [],
                [[name, value] for name, _address, value in video_dubs.ratings_of(anime, source) if value]
                if isinstance(anime, dict) and hasattr(video_dubs, "ratings_of") else [])
        with _ORDERS_LOCK:
            _ORDERS[alias] = kept
    mine = {}                                        # the library's series by the YummyAnime page they came from
    if Path(root).is_dir():
        for other in V.LIBRARY.items(root):
            page = str(other.info.get("source_url") or "")
            if yummy(page) and video_dubs.alias_of(page):
                mine.setdefault(video_dubs.alias_of(page), other)
    order = []
    for item in kept[1]:
        have = mine.get(item.get("alias") or "")
        address = str(item.get("address") or "")
        order.append(dict(item, here=bool(here_id) and have is not None and have.id == here_id or bool(item.get("here")),
                          series=have.id if have is not None else "",
                          episodes=len(have.episodes) if have is not None and not have.single else 0,
                          # (v5.44.89, the owner: no page of before) «найти и скачать» — the card of «Скачать»
                          get=_card_of(address) if address else "",
                          # (v5.44.145) the part's own page in the program: watched there, downloaded there
                          watch=f"/videos/watch?url={quote(address, safe='')}" if address else ""))
    return Response.json({"ok": True, "order": order, "ratings": kept[2]})


def _card_of(address: str) -> str:
    from webui.pages.get_link import link_of
    return link_of(address)


def download_pause(req: Request) -> Response:
    """«Пауза» / «Продолжить» of the download going: its file stops where it is and waits (the queue waits too)."""
    from core import download_scheduler, page_jobs, video_download
    task = page_jobs.current(GET)
    if task is None or not task.running:
        return Response.json({"ok": False, "error": "Скачивание не идёт"}, 409)
    if req.json().get("pause"):
        if not video_download.paused():
            video_download.pause()
            task._say("ПАУЗА: текущий файл остановится, где он есть, — «Продолжить» докачает его с этого места")
    elif video_download.paused():
        video_download.resume()
        task._say("ПРОДОЛЖАЮ")
    download_scheduler.note_paused("video", video_download.paused(), task)
    with _QUEUE_LOCK:                                # a pause stays a pause after a restart
        if _CURRENT.get("saved"):
            _CURRENT["saved"] = dict(_CURRENT["saved"], paused=video_download.paused())
    _persist()
    return Response.json({"ok": True, "paused": video_download.paused()})


def download_unqueue(req: Request) -> Response:
    """«Убрать» a download from the queue (it has not begun)."""
    from core import download_scheduler
    wanted = req.json().get("id")
    if download_scheduler.remove(wanted, waiting_kind="video"):
        return Response.json({"ok": True})
    with _QUEUE_LOCK:
        entry = next((q for q in _QUEUE if q["id"] == wanted), None)
        if entry is not None:
            _QUEUE.remove(entry)
    _persist()
    if entry is None:
        return Response.json({"ok": False, "error": "Его уже нет в очереди — возможно, оно уже началось"}, 409)
    return Response.json({"ok": True})
