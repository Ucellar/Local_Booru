"""«Скачать с сайта» on the tab «Видео» (``/videos/get``): the address of a title's page (or the page saved) → what
video files it links to directly (``core/video_download.look``, a page task) → the chosen ones downloaded into the
video folder as a series with its description and poster (``video_download.download``, another page task with a
progress bar: ``/api/videos/download-state`` carries ``meter``).

The list the page shows is the result of the last «Найти видео» kept by its task (``page_jobs.current``); «Скачать»
names it by the task's number, so a list read before another search is never downloaded by mistake.  The page gets
the names of the files only — their addresses stay in the program.
"""
from __future__ import annotations

from pathlib import Path

from core import video_library as V
from webui.html import esc
from webui.server import Request, Response

FIND = "video_find"                    # the page task «Найти видео»
GET = "video_download"                 # the page task «Скачать»


def register(router) -> None:
    router.add("GET", r"/videos/get", page)
    router.add("POST", r"/api/videos/find", find_url)
    router.add("POST", r"/api/videos/find-file", find_file)
    router.add("GET", r"/api/videos/find-state", find_state)
    router.add("GET", r"/api/videos/find-poster", find_poster)
    router.add("POST", r"/api/videos/download", download)
    router.add("GET", r"/api/videos/download-state", download_state)
    router.add("POST", r"/api/videos/download/stop", download_stop)


def _root(req: Request) -> tuple[Path | None, str]:
    """The video folder, or why there is none: a folder chosen in «Настройки» whose disk is not here is never made
    anew (the default one is made with the first download)."""
    root = Path(V.video_root(req.settings))
    if root.is_dir() or not str(req.settings.get("video_root") or "").strip():
        return root, ""
    return None, f"Папки видео нет на месте: {root}. Подключите её диск или выберите другую в «Настройки → Папки»"


# ── the page ──────────────────────────────────────────────────────────────────────
def page(req: Request) -> Response:
    from core import video_sources
    from webui.pages.videos import _frame
    address = req.arg("url").strip()
    root, why = _root(req)
    trouble = f'<p class="vnote" role="alert">{esc(why)}</p>' if why else ""
    sources = video_sources.enabled(req.settings)
    sites = ["hentasis1.top", "hentai-xs.com", "hentaihaven.xxx"] + (["YummyAnime — серии с AniLiberty", "AniLiberty"]
                                                                      if sources else [])
    if sources:
        frames = ("У YummyAnime видео в чужом плеере (Kodik) — его программа не трогает, а серии этого тайтла ищет на "
                  "AniLiberty по номерам Shikimori и MyAnimeList: это будет тот же тайтл и тот же сезон, а описание и "
                  "постер останутся с YummyAnime. Ссылка прямо на релиз AniLiberty тоже подходит.")
    else:
        frames = ("С YummyAnime и других сайтов с плеером Kodik и подобными программа не скачивает. Искать серии таких "
                  "тайтлов на AniLiberty — это включается в «Настройки → Вкладка «Видео»».")
    chips = "".join(f'<li>{esc(site)}</li>' for site in sites)
    body = f"""<div class="vget" id="vid-get">
  <section class="vget-ask" aria-labelledby="vget-title">
    <h1 id="vget-title">Скачать с сайта</h1>
    <p class="vget-lead">Вставьте ссылку на тайтл — программа найдёт его серии и скачает их в папку видео вместе с
      описанием и постером.</p>{trouble}
    <form class="vget-form" data-form="vid-find-url">
      <label class="vh" for="vid-find-url">Ссылка на тайтл</label>
      <div class="vget-row"><input id="vid-find-url" type="text" inputmode="url" name="url" spellcheck="false"
        autocomplete="off" value="{esc(address)}" placeholder="Ссылка на тайтл или на любую его серию">
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
        <span class="vbar-sum"></span><span class="action-status" role="status"></span></div>
      <div class="vbar-run job" data-job="download" hidden>
        <div class="job-head tool-actions"><b class="job-title"></b> <span class="job-status"></span>
          <span class="action-status" role="status"></span>
          <button type="button" class="vbutton" data-action="vid-download-stop" hidden>Остановить</button></div>
        <div class="vmeter" hidden><progress max="1000" value="0"></progress><span class="vmeter-text"></span></div>
        <div class="vbar-end"><p class="vget-result" hidden></p>
          <p class="vget-open" hidden><a class="vbutton primary" href="/videos">Открыть сериал</a></p></div>
        <details class="vlog"><summary>Подробности</summary>
          <pre class="job-log" tabindex="0" aria-label="Журнал скачивания"></pre></details>
      </div>
    </div>
  </section>
</div>"""
    return _frame(req, "Скачать с сайта", body, kind="video-get", show="get")


# ── «Найти видео» ────────────────────────────────────────────────────────────────────
def _start_find(req: Request, label: str, get_page, address: str = "") -> Response:
    from core import page_jobs, video_download, video_sources
    from core import video_meta_import as I
    from webui.pages.videos import _window_fetch
    root, why = _root(req)
    if root is None:
        return Response.json({"ok": False, "error": why}, 409)
    window = _window_fetch()
    settings = {key: req.settings.get(key) for key in ("video_source_aniliberty", "aniliberty_url")}

    def run(say, stop_check):
        try:                                         # a page of AniLiberty: its release, the page is not read
            found = video_sources.find_alias(address, root, settings=settings, say=say, stop_check=stop_check)
        except video_sources.SourceError as exc:
            return {"failed": str(exc)}
        if found is None:
            try:
                got = get_page(say)
            except I.MetaImportError as exc:
                return {"failed": str(exc)}

            def fetch(address: str):                 # the pages of the series' episodes, its own page
                return I.page_from_url(address, progress=lambda _text: None, window_fetch=window)
            found = video_download.look(got, root, say=say, stop_check=stop_check, fetch=fetch)
            if not found.files:                      # its video is another site's player (YummyAnime): the title
                try:                                 # on a site that gives its episodes
                    found = video_sources.find(got, root, settings=settings, say=say, stop_check=stop_check) or found
                except video_sources.SourceError as exc:
                    return {"failed": _nothing(found, str(exc))}
        videos = sum(1 for f in found.files if f.kind == "video")
        sounds = len(found.files) - videos
        if found.files and all(f.blocked for f in found.files):
            return {"failed": "Скачать с этой страницы нечего: " + found.files[0].blocked}
        if found.files:
            say(f"Нашлось видео: {videos}" + (f", звук: {sounds}" if sounds else ""))
            say(f"Папка: {found.folder}" + (" (новая)" if found.new_folder else " (уже есть — докачается в неё)"))
            if not stop_check():                     # the poster for the list, written with the description later
                found.poster = I.poster_of(found.about or found.page, say=say, stop_check=stop_check)
            return {"found": found, "root": str(root)}
        return {"failed": _nothing(found, off=not video_sources.enabled(settings))}
    try:
        task = page_jobs.start(FIND, label, run)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца."}, 409)
    return Response.json({"ok": True, "id": task.id})


def _nothing(found, elsewhere: str = "", *, off: bool = False) -> str:
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
                 "Вкладка «Видео»».")
    return text + " Описание и постер можно взять: «Описание с сайта» на странице сериала"


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
    from core import page_jobs, video_download
    body = req.json()
    task = page_jobs.current(FIND)
    kept = _found_of(task)
    if kept is None or str(body.get("find") or "") != str(task.id):
        return Response.json({"ok": False, "error": "Список файлов устарел — нажмите «Найти видео» ещё раз"}, 409)
    chosen = body.get("files")
    if not isinstance(chosen, list) or not chosen or not all(isinstance(n, int) for n in chosen):
        return Response.json({"ok": False, "error": "Отметьте файлы, которые скачать"}, 400)
    root, why = _root(req)
    if root is None:
        return Response.json({"ok": False, "error": why}, 409)
    found = kept["found"]

    def run(say, stop_check):
        return video_download.download(found, chosen, root, say=say, stop_check=stop_check)
    title = found.title or found.folder.name
    try:
        started = page_jobs.start(GET, f"Скачать с сайта: «{title}»", run, about=task.id)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца или остановите."}, 409)
    return Response.json({"ok": True, "id": started.id})


def download_state(req: Request) -> Response:
    from core import page_jobs, video_download
    task = page_jobs.current(GET)
    if task is None:
        return Response.json({"ok": True, "status": "idle"})
    state = {"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0)), "find": task.about}
    result = task.result if isinstance(task.result, dict) else {}
    if task.running:
        state["meter"] = video_download.meter()
        state["states"] = dict(state["meter"].get("states") or {})
    else:                                            # how each file ended, and the sum of it
        state["states"] = dict(result.get("states") or {})
        state["summary"] = {k: result.get(k) for k in ("done", "whole", "failed_files", "stopped") if k in result}
    series = str(result.get("series") or "")
    root = Path(V.video_root(req.settings))
    if series and root.is_dir() and V.LIBRARY.get(str(root), series) is not None:
        state["series_url"] = f"/videos/s/{series}"
    return Response.json(state)


def download_stop(req: Request) -> Response:
    from core import page_jobs
    task = page_jobs.current(GET)
    if task is None or not task.running:
        return Response.json({"ok": False, "error": "Скачивание не идёт"}, 409)
    task.stop()
    return Response.json({"ok": True})
