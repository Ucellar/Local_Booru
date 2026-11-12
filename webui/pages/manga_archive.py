"""(v5.44.76) «Скачать → Из архива» (``/get/archive``): the titles of the manga and manhwa folders that the program did not
download — «Узнать все» (each found on the sites by its names, linked when the answer is sure), the covers to choose
from, «Докачать» (the title's card of «Скачать → Манга / Манхва» with what the folder has as «есть»), «Докачать
недостающее у всех» (the downloads' queue), «Отвязать».  The work — ``core/manga_archive.py`` (a page task); the page —
``static/archive.js``, ``static/archive.css``.

(v5.44.81) The ways a title was known (``how``, ``by``) and what was tried for one «не нашлось» (``tried``); a round of
the background shown as the page's own work, its buttons free (the page's work stops it first).  (v5.44.116) Its
switches «Узнавать само, в фоне» and «…и по картинкам (SauceNAO)» (``manga_archive_auto``, ``manga_archive_pictures``)
are normal fields of «Настройки → Манга и манхва» now (the owner's review): here — a line of what they are and the way
there.
"""
from __future__ import annotations

from webui.html import esc
from webui.server import Request, Response

MAX_KEYS = 5000


def register(router) -> None:
    router.add("GET", r"/get/archive", page)
    router.add("GET", r"/api/archive/state", state)
    router.add("GET", r"/api/archive/task", task_state)
    router.add("POST", r"/api/archive/recognize", recognize)
    router.add("POST", r"/api/archive/link", link)
    router.add("POST", r"/api/archive/unlink", unlink)
    router.add("POST", r"/api/archive/counts", counts)
    router.add("POST", r"/api/archive/download", download)
    router.add("POST", r"/api/archive/stop", stop)
    router.add("GET", r"/api/archive/cover", cover)
    router.add("GET", r"/api/archive/folder-cover", folder_cover)
    router.add("POST", r"/api/archive/scan", scan)
    router.add("POST", r"/api/archive/not-manga", not_manga)


SETTINGS_LINK = "/settings#set-manga_auto"   # «Настройки → Манга и манхва → Что программа делает сама»


def _switches(settings: dict) -> str:
    """(v5.44.81) «Узнавать само, в фоне», «…и по картинкам (SauceNAO)» — (v5.44.116) fields of «Настройки → Сайты
    манги» now: what they are and the way there."""
    if not settings.get("manga_archive_auto", True):
        now = "сама, в фоне, программа тайтлы не узнаёт"
    elif settings.get("manga_archive_pictures", True):
        now = "программа узнаёт тайтлы сама, в фоне, — и по картинкам (SauceNAO)"
    else:
        now = "программа узнаёт тайтлы сама, в фоне, — по картинкам нет"
    return (f'<p class="ar-auto">Сейчас {esc(now)}. Это меняется в <a href="{SETTINGS_LINK}">«Настройки → Манга и '
            'манхва»</a>.</p>')


def stop_background() -> bool:
    """(v5.44.116) «Узнавать само, в фоне» switched off (``webui/pages/settings.py``): a round of the background going
    stops.  True — one was stopped."""
    from core import manga_archive as A, page_jobs
    task = page_jobs.current(A.TASK)
    if task is not None and task.running and task.about == A.BACKGROUND:
        task.stop()
        return True
    return False


def _folder_line(settings: dict) -> str:
    """(v5.44.84) The parser's folder and when it was looked at last."""
    import time as _time
    from core import manga_detect as MD
    root = str(settings.get("root") or "").strip()
    if not root:
        return ('<p class="ar-note" role="note">Папка, из которой парсер booru берёт файлы, не выбрана — её выбирают в '
                '«Настройки → Хранение и копии».</p>')
    data = MD.STORE.read()
    seen = int(data.get("at") or 0) if MD.STORE.titles(root) or data.get("root") == root else 0
    when = _time.strftime("%d.%m.%Y %H:%M", _time.localtime(seen)) if seen else "ещё не просматривалась"
    return (f'<p class="ar-folder">Папка парсера: <b>{esc(root)}</b> · просмотрена: {esc(when)} '
            f'<button type="button" class="ar-btn" data-action="ar-scan">Проверить папку парсера</button></p>')


def page(req: Request) -> Response:
    from webui.pages import get_hub
    body = f"""<div class="ar" id="archive">
  <section class="ar-head" aria-labelledby="ar-title">
    <h1 id="ar-title">Манга и манхва из архива</h1>
    <p class="ar-lead">Тайтлы папок манги и манхвы программы, которые лежат в папке парсера booru, и копии манги,
      которую программа взяла из папки парсера: туда попадает только манга, которую подтвердил сайт манги, —
      ComicInfo.xml, ссылка в файлах, тайтл с теми же страницами или SauceNAO; пул e621 сам по себе мангу не
      подтверждает (вкладка «<a href="/parser#manga">Манга из папки</a>» страницы «Парсер»). Здесь
      программа узнаёт, что это за тайтл, — по ссылке на сайт в файлах папки, по названию из файлов, по имени папки (если
      это название, а не «Новая папка» или «123») и по картинкам; узнанный привязывает, берёт обложку и описание с
      тегами и докачивает главы, которых в папке нет. Файлы самой папки парсера не трогаются — всё делается в копии.
      Если копия — не манга, нажмите «Это не манга»: копия останется, а папку парсер возьмёт как обычно.</p>
    {_folder_line(req.settings)}
    {_switches(req.settings)}
    <div class="ar-actions">
      <button type="button" class="ar-btn primary" data-action="ar-recognize">Узнать неузнанные</button>
      <button type="button" class="ar-btn" data-action="ar-counts">Сверить главы узнанных</button>
      <button type="button" class="ar-btn" data-action="ar-download">Докачать недостающее у всех</button>
      <span class="action-status" role="status"></span>
    </div>
    <div class="ar-run job" data-job="archive" hidden>
      <div class="job-head"><span class="ar-spin" aria-hidden="true"></span><b class="job-title"></b>
        <span class="job-status"></span>
        <button type="button" class="ar-btn" data-action="ar-stop" hidden>Остановить</button></div>
      <p class="ar-step" aria-live="polite"></p>
      <details class="ar-log"><summary>Подробности</summary>
        <pre class="job-log" tabindex="0" aria-label="Журнал"></pre></details>
    </div>
  </section>
  <nav class="ar-filters" aria-label="Какие тайтлы показать"></nav>
  <p class="ar-empty" hidden></p>
  <ol class="ar-list" aria-label="Тайтлы папок"></ol>
  <p class="ar-files" hidden></p>
</div>"""
    return get_hub.frame(req, "Из архива", body, part="archive", kind="archive", assets=("archive",))


def _entries(req: Request) -> list:
    from core import manga_archive as A
    from webui import content_mode
    now = content_mode.mode()
    return [e for e in A.entries(req.settings, refresh=req.arg("refresh") == "1") if content_mode.shows(e.hentai, now)]


def state(req: Request) -> Response:
    """Every title of the folders with what was found for it: {entries: [...], files, counts by state}."""
    from core import manga_archive as A
    kept = A.STORE.read()
    out, files = [], 0
    for entry in _entries(req):
        if not entry.folder:
            files += 1
            continue
        item = entry.public()
        found = kept.get(entry.key) or {}
        if entry.linked:
            item["state"] = "linked" if entry.linked.get("archive") else "own"
        else:
            item["state"] = found.get("state") if found.get("state") in ("choose", "none") else "new"
        item["counts"] = found.get("counts") if entry.linked else None
        item["query"] = found.get("query", "")
        item["why"] = found.get("why", "")
        item["how"] = found.get("how", "") if entry.linked or item["state"] == "choose" else ""
        item["by"] = found.get("by", "") if item["how"] else ""
        item["tried"] = list(found.get("tried") or []) if item["state"] == "none" else []
        item["later"] = int(found.get("later_at") or 0) if item["state"] == "none" else 0
        if item["state"] in ("choose", "none"):
            item["candidates"] = [{k: v for k, v in c.items() if k not in ("cover_address", "cover_page")}
                                  | {"cover": bool(c.get("cover_address")), "n": n}
                                  for n, c in enumerate(found.get("candidates") or [])]
        out.append(item)
    tally: dict = {}
    for item in out:
        tally[item["state"]] = tally.get(item["state"], 0) + 1
    return Response.json({"ok": True, "entries": out, "files": files, "tally": tally})


def task_state(req: Request) -> Response:
    from core import manga_archive as A, page_jobs
    task = page_jobs.current(A.TASK)
    if task is None:
        return Response.json({"ok": True, "status": "idle"})
    snap = task.snapshot(after=req.int_arg("after", 0, lo=0))
    snap["background"] = task.about == A.BACKGROUND       # (v5.44.81) the page's buttons stay free: it gives way
    if isinstance(task.result, dict) and task.result.get("failed"):
        snap["failed"] = str(task.result["failed"])
    if isinstance(task.result, dict):
        snap["result"] = {k: v for k, v in task.result.items() if k != "failed"}
    return Response.json({"ok": True, **snap})


def _keys(body: dict) -> "list | None":
    keys = body.get("keys")
    if keys is None:
        return None
    if not isinstance(keys, list):
        return []
    return [str(k)[:80] for k in keys[:MAX_KEYS]]


def _start(req: Request, what: str, keys, **extra) -> Response:
    from core import manga_archive as A, page_jobs
    try:
        task = A.start(req.settings, what, keys, **extra)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца или нажмите «Остановить»."}, 409)
    return Response.json({"ok": True, "id": task.id})


def recognize(req: Request) -> Response:
    body = req.json()
    query = " ".join(str(body.get("query") or "").split())[:120]
    keys = _keys(body)
    if query and (not keys or len(keys) != 1):
        return Response.json({"ok": False, "error": "Своё название — для одного тайтла"}, 400)
    if query and len(query) < 2:
        return Response.json({"ok": False, "error": "Напишите название — хотя бы две буквы"}, 400)
    return _start(req, "recognize", keys, query=query)


def link(req: Request) -> Response:
    """{key, n} — the title linked to the n-th cover found for it (or {key, url} — to a title's link)."""
    from core import manga_archive as A, manhwa_sources as S
    body = req.json()
    key = str(body.get("key") or "")[:80]
    url = str(body.get("url") or "").strip()[:2000]
    how = "url" if url else "chosen"
    if not url and body.get("n") is not None:
        candidates = (A.STORE.read().get(key) or {}).get("candidates") or []
        try:
            url = str(candidates[int(body["n"])].get("url") or "")
        except (ValueError, IndexError, TypeError):
            url = ""
    if not key or not url:
        return Response.json({"ok": False, "error": "Найденное устарело — нажмите «Узнать» ещё раз"}, 409)
    if S.source_for(url)[0] is None:
        from core import manhwa_download as D
        return Response.json({"ok": False, "error": D.UNKNOWN_LINK}, 400)
    return _start(req, "link", [key], url=url, how=how)


def unlink(req: Request) -> Response:
    from core import manga_archive as A
    key = str(req.json().get("key") or "")[:80]
    entry = next((e for e in A.entries(req.settings) if e.key == key), None)
    if entry is None:
        return Response.json({"ok": False, "error": "Такого тайтла в папках нет"}, 404)
    from pathlib import Path
    if not A.unlink_folder(Path(entry.path)):
        return Response.json({"ok": False, "error": "Эту папку программа скачала сама — её привязка остаётся"}, 409)
    A.STORE.put(entry.key, None)
    return Response.json({"ok": True})


def counts(req: Request) -> Response:
    return _start(req, "counts", _keys(req.json()))


def download(req: Request) -> Response:
    return _start(req, "download", _keys(req.json()))


def stop(req: Request) -> Response:
    from core import manga_archive as A, page_jobs
    task = page_jobs.current(A.TASK)
    if task is None or not task.running:
        return Response.json({"ok": True, "stopped": False})
    task.stop()
    return Response.json({"ok": True, "stopped": True})


def scan(req: Request) -> Response:
    """(v5.44.84) «Проверить папку парсера»: the whole folder looked at now (a page task)."""
    return _start(req, "scan", None)


def not_manga(req: Request) -> Response:
    """(v5.44.84) {key} — «Это не манга»: the folder goes back to the parser (its link, if the program made one,
    taken off first).  (v5.44.116) A row of a copy ``core/comic_manga`` made in the manga folder («root:<id>»): the
    parser's own folder it was copied from is the one marked (``moved()[key]["source"]``), not the copy."""
    from pathlib import Path
    from core import comic_manga, manga_archive as A, manga_detect as MD
    key = str(req.json().get("key") or "")[:80]
    entry = next((e for e in A.entries(req.settings) if e.key == key), None)
    if entry is None:
        return Response.json({"ok": False, "error": "Такой папки в списке нет — откройте страницу заново"}, 404)
    if entry.linked and entry.linked.get("archive"):
        A.unlink_folder(Path(entry.path))
    record = comic_manga.moved().get(entry.key) if entry.key.startswith("root:") else None
    source = str(record.get("source") or "") if isinstance(record, dict) else ""
    MD.set_not_manga(source or entry.path)
    A.STORE.put(entry.key, None)
    return Response.json({"ok": True})


def folder_cover(req: Request) -> Response:
    """(v5.44.84) A title of the parser's folder: its cover, else its first page — small."""
    import io
    from core import manga_archive as A
    picture = A.first_page(req.settings, req.arg("k")[:80])
    if picture is None:
        return Response.not_found("Обложки нет")
    try:
        from PIL import Image
        with Image.open(io.BytesIO(picture.read_bytes())) as im:
            im = im.convert("RGB")
            width, height = im.size
            if height > 2 * width:                   # a strip: its top
                im = im.crop((0, 0, width, int(width * 1.5)))
            im.thumbnail((360, 540))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=85)
    except (OSError, ValueError) as exc:
        return Response.not_found(f"Обложка не прочиталась: {type(exc).__name__}")
    return Response(200, out.getvalue(), "image/jpeg", {"Cache-Control": "private, max-age=600"})


def cover(req: Request) -> Response:
    """The cover of the n-th title found for a title of the folders (only the addresses kept for it)."""
    from core import manga_archive as A, manhwa_search as MS
    found = A.STORE.read().get(req.arg("k")[:80]) or {}
    try:
        kept = (found.get("candidates") or [])[int(req.arg("n") or -1)]
    except (ValueError, IndexError):
        kept = None
    got = MS.fetch_cover(kept.get("cover_address") or "", kept.get("cover_page") or "") if kept else None
    if got is None:
        return Response.not_found("Обложки нет")
    return Response(200, got[0], got[1], {"Cache-Control": "private, max-age=3600"})
