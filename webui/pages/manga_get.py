"""«Скачать с nhentai» on the tab «Манга» (``/manga/get``): links or numbers of galleries → downloaded into the manga
folder one after another (``core/manga_download.py``, a page task with a progress bar: ``/api/manga/download-state``
carries ``meter``, and when it is over ``open`` — the downloaded manga's page).

(v5.44.75) «Скачать → Манга» (``get_hub.py``): first the titles of the sites of manga — found by a name with their
covers or by a link, into the manga folder (``manhwa_get.block``: the machinery of «Скачать манхву», the manga first),
then the galleries of nhentai (not in «SFW»).  ``?url=`` — a title's link (a gallery's — into the field of nhentai, as
before), ``?nh=`` — galleries of nhentai, ``?q=`` — a name searched at once.

(v5.44.97) ``/api/manga/gallery?id=`` — a gallery's description for its card on «Скачать → По ссылке» (a tile of
«Манга → Лента» leads there): its titles, tags by kind, pages, its cover and the thumbnails of its pages (through the
program: ``/api/catalog/cover``), whether it is in the manga folder already; a gallery the program's rule leaves out —
said, nothing of it shown.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from core import manga_library as M
from webui.html import esc
from webui.server import Request, Response

GET = "manga_download"                 # the page task
_LOG = logging.getLogger("local_booru.manga")


def register(router) -> None:
    router.add("GET", r"/manga/get", page)
    router.add("POST", r"/api/manga/parse", parse)
    router.add("POST", r"/api/manga/download", download)
    router.add("GET", r"/api/manga/download-state", download_state)
    router.add("GET", r"/api/manga/download-cover", download_cover)
    router.add("POST", r"/api/manga/download/stop", download_stop)
    router.add("POST", r"/api/manga/download/unqueue", download_unqueue)     # (v5.44.107)
    router.add("GET", r"/api/manga/gallery", gallery)
    from core import manga_download
    if manga_download.await_restore():               # (v5.44.107) the galleries the program was busy with when it closed
        _restore_later()


def _restore_later() -> None:
    """(v5.44.107) In a thread of its own: the galleries the last run left unfinished — downloaded again
    ``RESTORE_AFTER`` after the start (into the manga folder of the settings then)."""
    import threading
    import time as _time

    def later():
        from core import manga_download, page_jobs
        from core.settings import load_settings
        try:
            _time.sleep(manga_download.RESTORE_AFTER)
            numbers = manga_download.restored()
            if not numbers:
                return
            settings = load_settings()
            root = M.manga_root(settings)
            if not root or not Path(root).is_dir():
                _LOG.warning("the galleries of the last run not begun again: no manga folder")
                return
            if manga_download.add(numbers) is not None:        # a download began meanwhile: they join it
                return
            page_jobs.start(GET, _label(numbers, "С прошлого запуска — "), _run_of(numbers, Path(root)))
        except Exception as exc:  # noqa: BLE001 - the program goes on without them
            _LOG.warning("the galleries of the last run not begun again: %s: %s", type(exc).__name__, exc)
    threading.Thread(target=later, name="manga-download-restore", daemon=True).start()


def _label(numbers: list, before: str = "") -> str:
    from webui.pages.post_sets import plural
    return before + (f"Скачать с nhentai: {numbers[0]}" if len(numbers) == 1 else
                     f"Скачать с nhentai: {plural(len(numbers), ('галерея', 'галереи', 'галерей'))}")


def _run_of(numbers: list, root: Path):
    """The page task's work: the galleries (and the ones that join them) into ``root``."""
    from core import manga_download
    from webui.pages.videos import _window_fetch
    window = _window_fetch()

    def run(say, stop_check):
        result = manga_download.download(numbers, root, say=say, stop_check=stop_check, window_fetch=window)
        # a gallery not downloaded is a line of the list, not a failed task ("failed" of a task's result is its refusal)
        result["failed_galleries"] = result.pop("failed", [])
        result["open"] = _open_link(root, result.get("folders") or [])
        result["links"] = _links(root, result.get("folders") or [])
        return result
    return run


def _root(req: Request) -> tuple[Path | None, str]:
    """The manga folder, or why there is none."""
    root = M.manga_root(req.settings)
    if not root:
        return None, "Папка с мангой не выбрана — выберите её на вкладке «Манга»"
    if not Path(root).is_dir():
        return None, f"Папки манги нет на месте: {root}. Подключите её диск или выберите другую в «Настройки → Папки»"
    return Path(root), ""


def page(req: Request) -> Response:
    from core import manga_download
    from webui import content_mode
    from webui.pages import get_hub, manhwa_get
    asked = req.arg("url").strip()
    galleries = req.arg("nh").strip()
    numbers, bad = manga_download.gallery_ids(asked) if asked else ([], [])
    if numbers and not bad:                 # a gallery's link or number: nhentai's field, as before
        galleries, asked = galleries or asked, ""
    root, why = _root(req)
    chosen = bool(str(req.settings.get("manga_root") or "").strip())
    note = "" if root is not None else why if chosen else (
        "Папка манги не выбрана — пока её нет, манга с сайтов ляжет в папку манхвы и будет читаться лентой. Папку "
        "выбирают на вкладке «Манга» или в «Настройки → Папки».")
    sites = manhwa_get.block(req, "manga", asked, req.arg("q").strip(), note)
    nhentai = "" if content_mode.mode() == "sfw" else _nhentai(req, galleries)
    body = f'<div class="get-manga">{sites}{nhentai}</div>'
    return get_hub.frame(req, "Скачать мангу", body, part="manga", kind="manga-get", assets=("manga", "manhwa"))


def _nhentai(req: Request, asked: str) -> str:
    """The galleries of nhentai: links or numbers → «Скачать» (``static/manga.js``)."""
    _root_path, why = _root(req)
    trouble = f'<p class="mnote" role="alert">{esc(why)}</p>' if why else ""
    return f"""<div class="mget" id="manga-get">
  <section class="mget-ask" aria-labelledby="mget-title">
    <h2 id="mget-title">Галереи nhentai</h2>
    <p class="mget-lead">Вставьте ссылки на галереи или их номера — можно сразу несколько, через пробел или каждую с
      новой строки. Галерея ляжет в папку манги со своими названиями и тегами; прерванная докачается.</p>{trouble}
    <form data-form="manga-get">
      <label class="mh" for="manga-get-text">Ссылки или номера галерей</label>
      <textarea id="manga-get-text" name="text" rows="3" spellcheck="false" autocomplete="off"
        placeholder="https://nhentai.net/g/691957/&#10;691958">{esc(asked)}</textarea>
      <div class="mget-parsed" aria-live="polite"></div>
      <div class="mget-go"><button type="submit" class="mbutton primary">Скачать</button>
        <span class="action-status" role="status"></span></div>
    </form>
  </section>
  <section class="mget-run job" data-job="download" hidden>
    <div class="job-head tool-actions"><span class="mspin" aria-hidden="true"></span><b class="job-title"></b>
      <span class="job-status"></span><span class="action-status" role="status"></span>
      <button type="button" class="mbutton" data-action="manga-get-stop" hidden>Остановить</button></div>
    <div class="mmeter" hidden><progress max="1000" value="0"></progress><span class="mmeter-text"></span></div>
    <ol class="mget-items" aria-label="Галереи"></ol>
    <div class="mget-end"><p class="mget-result" hidden></p>
      <p class="mget-open" hidden><a class="mbutton primary" href="/manga">Открыть мангу</a></p></div>
    <details class="mlog"><summary>Подробности</summary>
      <pre class="job-log" tabindex="0" aria-label="Журнал скачивания"></pre></details>
  </section>
</div>"""


def parse(req: Request) -> Response:
    """What the field holds, for the chips under it: the galleries named and the pieces not understood."""
    from core import manga_download
    numbers, bad = manga_download.gallery_ids(str(req.json().get("text") or "")[:20000])
    return Response.json({"ok": True, "ids": numbers[:manga_download.MAX_AT_ONCE * 2], "bad": bad[:20],
                          "max": manga_download.MAX_AT_ONCE})


def download(req: Request) -> Response:
    from core import manga_download, page_jobs
    text = str(req.json().get("text") or "")
    numbers, bad = manga_download.gallery_ids(text)
    if bad:
        return Response.json({"ok": False, "error": "Это не ссылка на галерею nhentai и не её номер: " + ", ".join(
            f"«{piece}»" for piece in bad[:3]) + (" …" if len(bad) > 3 else "")}, 400)
    if not numbers:
        return Response.json({"ok": False, "error": "Вставьте ссылку на галерею nhentai или её номер"}, 400)
    if len(numbers) > manga_download.MAX_AT_ONCE:
        return Response.json({"ok": False, "error": f"За один раз — не больше {manga_download.MAX_AT_ONCE} галерей"},
                             400)
    root, why = _root(req)
    if root is None:
        return Response.json({"ok": False, "error": why}, 409)
    # (v5.44.107; the owner: «почему нет очереди на скачивании манги») a download going: they join it, after its own
    places = manga_download.add(numbers)
    if places is not None:
        return _queued(places, numbers)
    import time as _time
    deadline = _time.monotonic() + 8.0
    while True:
        try:
            task = page_jobs.start(GET, _label(numbers), _run_of(numbers, root))
            return Response.json({"ok": True, "id": task.id, "count": len(numbers)})
        except page_jobs.TaskBusy as exc:            # the one going is ending this moment: it is waited for a little
            places = manga_download.add(numbers)
            if places is not None:
                return _queued(places, numbers)
            if _time.monotonic() > deadline:
                return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца или остановите."}, 409)
            _time.sleep(0.2)


def _queued(places: list, numbers: list) -> Response:
    """The answer for galleries put into the download going: {queued — the place of the first one among the galleries
    waiting (0 — all of them are in it already, begun or done)}."""
    from core import page_jobs
    task = page_jobs.current(GET)
    waiting = [p for p in places if p]
    return Response.json({"ok": True, "id": task.id if task is not None else None, "count": len(numbers),
                          "queued": min(waiting) if waiting else 0, "already": not waiting})


def download_unqueue(req: Request) -> Response:
    """(v5.44.107) «Убрать» a gallery waiting in the download going."""
    from core import manga_download
    try:
        number = int(req.json().get("id"))
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Нет номера галереи"}, 400)
    if not manga_download.drop(number):
        return Response.json({"ok": False, "error": "Её уже нет в очереди — возможно, она уже качается"}, 409)
    return Response.json({"ok": True})


def _links(root: Path, folders: list) -> dict:
    """folder -> the page of the manga downloaded into it."""
    wanted = {str(Path(f)) for f in folders}
    return {str(Path(m.path)): f"/manga/g/{m.id}" for m in M.LIBRARY.items(str(root), refresh=True)
            if str(Path(m.path)) in wanted}


def _open_link(root: Path, folders: list) -> str:
    """The page of the manga downloaded; of several — the list of manga (the newest first)."""
    if not folders:
        return ""
    wanted = {str(Path(f)) for f in folders}
    items = M.LIBRARY.items(str(root), refresh=True)
    found = [m for m in items if str(Path(m.path)) in wanted]
    if len(folders) == 1 and found:
        return f"/manga/g/{found[0].id}"
    return "/manga" if found else ""


def _items_of(task) -> list:
    """The galleries of the download with their folders (the program's side): the progress bar's while it goes, the
    result's after."""
    from core import manga_download
    if task.running:
        return list(manga_download.meter().get("items") or [])
    result = task.result if isinstance(task.result, dict) else {}
    return list(result.get("items") or [])


def _first_page(folder: str) -> Path | None:
    try:
        pages = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in M.IMG_EXTS and p.is_file())
    except OSError:
        return None
    return pages[0] if pages else None


def download_state(req: Request) -> Response:
    from core import manga_download, page_jobs
    task = page_jobs.current(GET)
    soon = manga_download.restoring_public()          # (v5.44.107) the galleries the last run left, soon begun again
    extra = {"queue": [{"id": q["number"], "title": q["title"] or f"Галерея {q['number']}", "count": 0}
                       for q in manga_download.waiting()] if task is not None and task.running else [],
             **({"restoring": soon} if soon else {})}
    if task is None:
        return Response.json({"ok": True, "status": "idle", **extra})
    state = {"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0)), **extra}
    if task.running:
        state["meter"] = {k: v for k, v in manga_download.meter().items() if k != "items"}
    result = task.result if isinstance(task.result, dict) else {}
    if result.get("open"):
        state["open"] = str(result["open"])
    links = result.get("links") or {}
    items = []
    for k, item in enumerate(_items_of(task)):
        folder = str(item.get("folder") or "")
        public = {key: item.get(key) for key in ("number", "title", "pages", "page", "got", "had", "bytes", "state",
                                                  "why")}
        public["cover"] = f"/api/manga/download-cover?task={task.id}&k={k}" if folder and _first_page(folder) else ""
        public["open"] = str(links.get(folder) or "")
        items.append(public)
    state["items"] = items
    return Response.json(state)


def download_cover(req: Request) -> Response:
    """The first page of a gallery of the download (``k`` — its place in it), small: its line on the page."""
    from core import page_jobs
    from webui.pages.manga import COVER_SIZE, _thumb_response
    task = page_jobs.current(GET)
    if task is None or req.arg("task") != str(task.id):
        return Response.not_found("Этой загрузки уже нет")
    items = _items_of(task)
    k = req.int_arg("k", -1, lo=-1)
    folder = str(items[k].get("folder") or "") if 0 <= k < len(items) else ""
    first = _first_page(folder) if folder else None
    if first is None:
        return Response.not_found("Страниц ещё нет")
    return _thumb_response(M.PageRef(path=str(first)), COVER_SIZE)


def download_stop(req: Request) -> Response:
    """«Остановить»: the download going and the galleries waiting in it (nothing of it kept for the next run)."""
    from core import manga_download, page_jobs
    task = page_jobs.current(GET)
    if task is None or not task.running:
        return Response.json({"ok": False, "error": "Скачивание не идёт"}, 409)
    manga_download.USER_STOP.set()
    task.stop()
    return Response.json({"ok": True})


# ── (v5.44.97) a gallery's card ──────────────────────────────────────────────────────────────────────────────────
TAG_KINDS = (("artist", "Художник"), ("group", "Группа"), ("parody", "Пародия"), ("character", "Персонажи"),
             ("tag", "Теги"), ("language", "Язык"), ("category", "Категория"))
THUMBS = 300                      # the thumbnails of a gallery's pages the card gets at most


def gallery(req: Request) -> Response:
    """A gallery's description for its card: {ok, id, title, full, japanese, pages, favorites, date, tags [{kind,
    names}], cover, thumbs [cover numbers], have {folder, pages, open} | None, url} — or {ok: false, error,
    forbidden}."""
    import datetime as _dt
    from core import catalog as C, manga_download as D, nhentai as N
    from webui import content_mode
    if content_mode.mode() == "sfw":
        return Response.json({"ok": False, "error": "Это галерея nhentai, а включён режим «SFW» — они не "
                                                    "показываются. «Показать всё» — и она откроется."}, 409)
    number = req.int_arg("id", 0, lo=0)
    if not number or number > 9_999_999:
        return Response.json({"ok": False, "error": "Нет номера галереи"}, 400)
    try:
        found = D.describe(number)
    except D.Forbidden as exc:
        return Response.json({"ok": False, "error": str(exc), "forbidden": True})
    except D.MangaError as exc:
        return Response.json({"ok": False, "error": str(exc)})
    data = found.data
    link = N.link_of(number)
    tags = [t for t in data.get("tags") or [] if isinstance(t, dict) and t.get("name")]
    groups = [{"kind": label, "names": [str(t["name"]) for t in tags if t.get("type") == kind]}
              for kind, label in TAG_KINDS]
    cover = data.get("cover") if isinstance(data.get("cover"), dict) else {}
    thumb = data.get("thumbnail") if isinstance(data.get("thumbnail"), dict) else {}
    first = N.thumb_of(cover.get("path"), number) or N.thumb_of(thumb.get("path"), number)
    pages = data.get("pages") if isinstance(data.get("pages"), list) else []
    if pages:
        thumbs = [N.thumb_of(p.get("thumbnail"), number) for p in pages[:THUMBS] if isinstance(p, dict)]
    else:                         # the old description: the thumbnails by the pages' kinds
        thumbs = [N.thumb_of(f"galleries/{found.media_id}/{n}t{ext}", number)
                  for n, ext in enumerate(found.kinds[:THUMBS], 1)]
    first = first or N.thumb_of(f"galleries/{found.media_id}/cover{found.kinds[0]}", number)
    when = ""
    try:
        stamp = int(data.get("upload_date") or 0)
        when = _dt.datetime.fromtimestamp(stamp).strftime("%d.%m.%Y") if stamp > 0 else ""
    except (TypeError, ValueError, OSError, OverflowError):
        when = ""
    have = None
    root, _why = _root(req)
    if root is not None:
        folder, new = D.folder_for(root, found)
        if not new:
            try:
                there = sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() in M.IMG_EXTS)
            except OSError:
                there = 0
            have = {"folder": folder.name, "pages": there,
                    "open": str(_links(root, [str(folder)]).get(str(folder)) or "")}
    shown = found.pretty or N.pretty(found.english)
    if len(re.sub(r"[\W_]", "", shown)) < 2:          # «[Artist] ♥ [English]»: the brackets were the most of it
        shown = found.title
    return Response.json({
        "ok": True, "id": number, "url": link, "title": shown,
        "full": found.english, "japanese": found.japanese, "pages": found.pages,
        "favorites": int(data.get("num_favorites") or 0), "date": when,
        "tags": [g for g in groups if g["names"]],
        "cover": C.cover_id(first, link) if first else "",
        "thumbs": [C.cover_id(x, link) for x in thumbs if x], "have": have})
