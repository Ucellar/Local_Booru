"""«Скачать с nhentai» on the tab «Манга» (``/manga/get``): links or numbers of galleries → downloaded into the manga
folder one after another (``core/manga_download.py``, a page task with a progress bar: ``/api/manga/download-state``
carries ``meter``, and when it is over ``open`` — the downloaded manga's page).
"""
from __future__ import annotations

from pathlib import Path

from core import manga_library as M
from webui.html import esc
from webui.server import Request, Response

GET = "manga_download"                 # the page task


def register(router) -> None:
    router.add("GET", r"/manga/get", page)
    router.add("POST", r"/api/manga/parse", parse)
    router.add("POST", r"/api/manga/download", download)
    router.add("GET", r"/api/manga/download-state", download_state)
    router.add("GET", r"/api/manga/download-cover", download_cover)
    router.add("POST", r"/api/manga/download/stop", download_stop)


def _root(req: Request) -> tuple[Path | None, str]:
    """The manga folder, or why there is none."""
    root = M.manga_root(req.settings)
    if not root:
        return None, "Папка с мангой не выбрана — выберите её на вкладке «Манга»"
    if not Path(root).is_dir():
        return None, f"Папки манги нет на месте: {root}. Подключите её диск или выберите другую в «Настройки → Папки»"
    return Path(root), ""


def page(req: Request) -> Response:
    from webui.pages.manga import _frame
    _root_path, why = _root(req)
    trouble = f'<p class="mnote" role="alert">{esc(why)}</p>' if why else ""
    asked = req.arg("url").strip()
    body = f"""<div class="mget" id="manga-get">
  <section class="mget-ask" aria-labelledby="mget-title">
    <h1 id="mget-title">Скачать с nhentai</h1>
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
    return _frame(req, "Скачать с nhentai", body, kind="manga-get", show="get")


def parse(req: Request) -> Response:
    """What the field holds, for the chips under it: the galleries named and the pieces not understood."""
    from core import manga_download
    numbers, bad = manga_download.gallery_ids(str(req.json().get("text") or "")[:20000])
    return Response.json({"ok": True, "ids": numbers[:manga_download.MAX_AT_ONCE * 2], "bad": bad[:20],
                          "max": manga_download.MAX_AT_ONCE})


def download(req: Request) -> Response:
    from core import manga_download, page_jobs
    from webui.pages.videos import _window_fetch
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
    window = _window_fetch()

    def run(say, stop_check):
        result = manga_download.download(numbers, root, say=say, stop_check=stop_check, window_fetch=window)
        # a gallery not downloaded is a line of the list, not a failed task ("failed" of a task's result is its refusal)
        result["failed_galleries"] = result.pop("failed", [])
        result["open"] = _open_link(root, result.get("folders") or [])
        result["links"] = _links(root, result.get("folders") or [])
        return result
    from webui.pages.post_sets import plural
    label = (f"Скачать с nhentai: {numbers[0]}" if len(numbers) == 1 else
             f"Скачать с nhentai: {plural(len(numbers), ('галерея', 'галереи', 'галерей'))}")
    try:
        task = page_jobs.start(GET, label, run)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца или остановите."}, 409)
    return Response.json({"ok": True, "id": task.id, "count": len(numbers)})


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
    if task is None:
        return Response.json({"ok": True, "status": "idle"})
    state = {"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0))}
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
    from core import page_jobs
    task = page_jobs.current(GET)
    if task is None or not task.running:
        return Response.json({"ok": False, "error": "Скачивание не идёт"}, 409)
    task.stop()
    return Response.json({"ok": True})
