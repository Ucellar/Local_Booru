"""Tool pages: the parser run (live log and progress), and the actions of the gallery sections
«Брак» (search again) and «Корзина» (restore, delete for good) on many cards at once."""
from __future__ import annotations

from pathlib import Path

from core.database.connection import db
from webui import data
from webui.html import esc, page_frame
from webui.server import Request, Response

RETRY_LIMIT = 5000          # Брак files one retry may take


def register(router) -> None:
    router.add("GET", r"/parser", parser_page)
    router.add("GET", r"/api/parser/state", parser_state)
    router.add("POST", r"/api/parser/start", parser_start)
    router.add("POST", r"/api/parser/(?P<what>pause|resume|stop)", parser_control)
    router.add("POST", r"/api/parser/site-again", parser_site_again)
    router.add("POST", r"/api/parser/folder", parser_folder)
    router.add("GET", r"/api/parser/thumb/(?P<job>\d+)/(?P<file>\d+)", parser_thumb)
    router.add("GET", r"/api/parser/file/(?P<job>\d+)/(?P<file>\d+)", parser_file)
    router.add("POST", r"/api/parser/open", parser_open)
    router.add("POST", r"/api/parser/retry", parser_retry)
    router.add("POST", r"/api/trash/restore", trash_restore)
    router.add("POST", r"/api/trash/purge", trash_purge)


def _prefs(req: Request) -> dict:
    from webui import prefs
    stored = prefs.load()
    return {"thumbs": stored.get("thumbs", "m")}         # the look is the page's own (page_frame)


# ── what the run will use, in words ────────────────────────────────────────
_REVERSE = (("enable_fluffle", "Fluffle", True), ("enable_e621_iqdb", "e621 IQDB", True),
            ("enable_atf_iqdb", "ATF IQDB", True), ("enable_iqdb", "IQDB", True),
            ("enable_danbooru_iqdb", "Danbooru IQDB", False), ("enable_ascii2d", "Ascii2D", False))


def site_plan(settings: dict) -> tuple[list, list]:
    """(sites asked by MD5, reverse-search services) as the parser will use them, by name."""
    from core.parser.sites import engine_class
    sites = []
    for domain, cfg in (settings.get("sites") or {}).items():
        if isinstance(cfg, dict) and cfg.get("enabled") and engine_class(cfg, domain) is not None:
            sites.append(str(cfg.get("name") or domain))
    for cfg in settings.get("custom_sites") or []:
        if isinstance(cfg, dict) and cfg.get("enabled") and cfg.get("domain"):
            sites.append(str(cfg.get("name") or cfg.get("domain")))
    e621_on = bool(((settings.get("sites") or {}).get("e621.net") or {}).get("enabled"))
    atf_on = bool(((settings.get("sites") or {}).get("booru.allthefallen.moe") or {}).get("enabled"))
    # a site's own IQDB leads to that site's posts: without the site among the run's it is not asked (core/parser/reverse)
    reverse = [label for key, label, default in _REVERSE if settings.get(key, default)
               and (key != "enable_e621_iqdb" or e621_on) and (key != "enable_atf_iqdb" or atf_on)]
    if settings.get("enable_saucenao", True):
        reverse.append("SauceNAO" if str(settings.get("saucenao_api_key") or "").strip() else "SauceNAO (нет ключа — выключен)")
    return sites, reverse


# ── /parser ────────────────────────────────────────────────────────────────
def _chips(names: list, *, empty: str) -> str:
    if not names:
        return f'<span class="tool-warn">{esc(empty)}</span>'
    out = []
    for name in names:
        off = "нет ключа" in name
        label = name.split(" (", 1)[0] if off else name
        title = ' title="Нет ключа — выключен; ключ вписывается на странице «Сайты»"' if off else ""
        out.append(f'<li class="plan-chip{" off" if off else ""}"{title}>{esc(label)}</li>')
    return f'<ul class="plan-chips">{"".join(out)}</ul>'


def parser_page(req: Request) -> Response:
    """The page «Парсер»: what to press on top (the folder, «Запустить / Пауза / Остановить»), what the run
    will ask under it, then the run: its progress by answers, the pictures in work (each with the sites asking
    about it), the last answers and the journal.  A picture opens large in the viewer (tools.js)."""
    s = req.settings
    root = str(s.get("root") or "")
    exists = bool(root) and Path(root).is_dir()
    sites, reverse = site_plan(s)
    in_app = "QtWebEngine" in req.headers.get("User-Agent", "")
    if in_app:          # the window has a folder dialog; a browser page takes the path as text
        folder = (f'<code class="tool-path">{esc(root) or "не выбрана"}</code>'
                  '<button type="button" data-action="parser-choose-folder">Выбрать папку…</button>')
    else:
        folder = (f'<form class="inline-form" data-form="parser-folder"><input type="text" name="path" value="{esc(root)}" '
                  'spellcheck="false" aria-label="Папка с картинками" placeholder="Путь к папке">'
                  '<button type="submit">Сохранить</button></form>')
    folder_note = "" if exists else '<p class="tool-warn">Такой папки нет — выберите другую.</p>'
    recheck = " checked" if s.get("parser_recheck_new_sites") else ""
    nomatch_only = (" checked" if s.get("parser_recheck_nomatch_only") else "") + ("" if recheck else " disabled")
    counts = "".join(f'<div class="lg-{key}"><dt>{label}</dt><dd data-count="{key}">0</dd></div>'
                     for key, label in (("tagged", "найдено"), ("nomatch", "в Брак"), ("deferred", "отложено"),
                                        ("skip", "пропущено"), ("error", "ошибки")))
    segments = "".join(f'<span class="seg seg-{key}"></span>' for key in ("tagged", "nomatch", "deferred", "skip", "error"))
    body = f"""
<section class="tool parser-tool" id="parser" data-state-url="/api/parser/state">
  <header class="tool-head">
    <h1>Парсер</h1>
    <p class="tool-lead">Ищет теги для картинок из папки: точный MD5 на booru-сайтах, потом обратный поиск.
    Найденное попадает в галерею, ненайденное — в «Брак».</p>
  </header>
  <div class="parser-top">
    <div class="parser-controls" role="group" aria-label="Запуск">
      <div class="parser-folder"><span class="tool-label">Папка</span>
        <div class="tool-value">{folder}{folder_note}</div></div>
      <div class="tool-actions parser-buttons">
        <button type="button" class="primary" data-action="parser-start">Запустить</button>
        <button type="button" data-action="parser-pause" disabled>Пауза</button>
        <button type="button" class="danger" data-action="parser-stop" disabled>Остановить</button>
        <span class="action-status" role="status"></span>
      </div>
      <label class="check parser-recheck"><input type="checkbox" name="recheck"{recheck}> Добор новых сайтов
        <small>обработанные раньше файлы ищутся только там, где их ещё не искали; сначала — новые файлы, потом
        Брак, потом найденные</small></label>
      <label class="check parser-recheck parser-recheck-only"><input type="checkbox" name="recheck_only"{nomatch_only}>
        Добор только для Брака <small>уже найденным файлам теги других сайтов не добираются — так быстрее</small></label>
    </div>
    <dl class="parser-plan" aria-label="Где ищет">
      <div><dt>По MD5</dt><dd>{_chips(sites, empty="нет включённых сайтов")}</dd></div>
      <div><dt>Обратный поиск</dt><dd>{_chips(reverse, empty="выключен")}</dd></div>
      <div class="plan-edit"><dt></dt><dd><a href="/sites">Сайты и ключи</a></dd></div>
    </dl>
  </div>
  <p class="parser-idle muted" data-idle>Здесь будут картинки, которые парсер разбирает: нажмите «Запустить».</p>
  <div class="job parser-run" data-job hidden>
    <div class="run-head"><b class="job-title"></b> <span class="job-status run-status"></span>
      <span class="job-numbers"></span></div>
    <div class="run-bar" role="progressbar" aria-label="Готово" aria-valuemin="0" aria-valuemax="1" aria-valuenow="0">
      {segments}<span class="run-percent"></span></div>
    <dl class="job-counts run-legend">{counts}</dl>
    <div class="job-links" hidden>Смотреть: <a href="/posts?section=found">найденные</a> · <a href="/posts?section=no_match">Брак</a></div>
    <div class="run-alert" role="status" hidden></div>
    <section class="run-part run-queues" aria-labelledby="run-queues-title" hidden>
      <h2 id="run-queues-title">Очереди</h2>
      <p class="queues-lead muted">Сначала файл читается (его MD5 и что о нём уже знает библиотека), потом его
        спрашивают сайты — у каждого сайта и каждого сервиса обратного поиска своя очередь: найденное сразу
        записывается в библиотеку, медленный не держит остальных. Файл, который сайты не нашли по MD5, идёт в обратный
        поиск — от сервиса к сервису, пока какой-нибудь не найдёт.</p>
      <h3 class="queues-group" data-for="files" hidden>Файлы — чтение и запись в библиотеку</h3>
      <ul class="site-queues" data-kind="files" hidden></ul>
      <h3 class="queues-group">Сайты — точный MD5</h3>
      <ul class="site-queues" data-kind="site"></ul>
      <h3 class="queues-group" data-for="reverse" hidden>Обратный поиск — похожие картинки</h3>
      <ul class="site-queues" data-kind="reverse" hidden></ul>
    </section>
    <section class="run-part run-flight" aria-labelledby="run-flight-title">
      <h2 id="run-flight-title">Сейчас в работе</h2>
      <ul class="flight-cards" aria-live="off"></ul>
      <p class="flight-more muted" hidden></p>
      <p class="flight-empty muted" hidden>Сейчас ни один файл не в работе.</p>
    </section>
    <section class="run-part run-recent" aria-labelledby="run-recent-title" hidden>
      <h2 id="run-recent-title">Готово недавно</h2>
      <ul class="recent-strip"></ul>
    </section>
    <details class="run-part run-log" data-remember="parser-log" open>
      <summary>Журнал</summary>
      <div class="log-tabs" role="tablist" aria-label="Что показать в журнале"></div>
      <pre class="job-log" tabindex="0" aria-label="Журнал запуска"></pre>
    </details>
  </div>
  <dialog class="parser-viewer" aria-labelledby="pv-title">
    <div class="pv-media">
      <img alt="" hidden>
      <video controls preload="metadata" hidden></video>
      <button type="button" class="pv-nav pv-prev" data-action="pv-prev" aria-label="Предыдущая">‹</button>
      <button type="button" class="pv-nav pv-next" data-action="pv-next" aria-label="Следующая">›</button>
    </div>
    <div class="pv-side">
      <h2 id="pv-title"></h2>
      <p class="pv-what"></p>
      <ul class="pv-chips"></ul>
      <div class="tool-actions pv-actions">
        <a class="button primary" data-pv-card hidden>Открыть карточку</a>
        <button type="button" data-action="pv-open">Открыть файл</button>
        <button type="button" data-action="pv-reveal">Показать в папке</button>
        <span class="action-status" role="status"></span>
      </div>
      <button type="button" class="pv-close" data-action="pv-close">Закрыть</button>
    </div>
  </dialog>
</section>"""
    return Response.html(page_frame("Парсер", body, active="parser", subnav=False, kind="tool",
                                    body_data={"tool": "parser"}, **_prefs(req)))


# ── the parser API ─────────────────────────────────────────────────────────
def parser_state(req: Request) -> Response:
    from core.parser import jobs
    job = jobs.current()
    if job is None:
        busy = jobs.owner()
        return Response.json({"ok": True, "status": "busy" if busy else "idle", "owner": busy})
    snap = job.snapshot(after=req.int_arg("after", 0, lo=0))
    snap["ok"] = True
    return Response.json(snap)


def _busy(exc) -> Response:
    return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца или остановите."}, 409)


def parser_start(req: Request) -> Response:
    from core.parser import jobs
    s = dict(req.settings)
    root = str(s.get("root") or "")
    if not root or not Path(root).is_dir():
        return Response.json({"ok": False, "error": "Папка с картинками не выбрана или её нет"}, 400)
    body = req.json()
    changes = {key: bool(body.get(field)) for field, key in (("recheck_new_sites", "parser_recheck_new_sites"),
                                                               ("recheck_nomatch_only", "parser_recheck_nomatch_only"))
               if field in body}
    if changes:
        s.update(changes)
        _save_settings(changes)
    s.pop("_parser_explicit_paths", None)
    try:
        job = jobs.start(s, label="Парсер: " + Path(root).name, kind="run")
    except jobs.ParserBusy as exc:
        return _busy(exc)
    return Response.json({"ok": True, "id": job.id})


def parser_site_again(req: Request) -> Response:
    """«Проверить снова» for a site switched off in the run going now (its cookies renewed, its key written)."""
    from core.parser import jobs
    host = str(req.json().get("host") or "").strip()
    job = jobs.current()
    if not host:
        return Response.json({"ok": False, "error": "Не указан сайт"}, 400)
    if job is None:
        return Response.json({"ok": False, "error": "Парсер сейчас не работает"}, 409)
    why = job.site_again(host)
    if why:
        return Response.json({"ok": False, "error": why}, 409)
    return Response.json({"ok": True})


def parser_control(req: Request) -> Response:
    from core.parser import jobs
    job = jobs.current()
    if job is None or job.status not in {"running", "paused", "starting"}:
        return Response.json({"ok": False, "error": "Парсер сейчас не работает"}, 409)
    getattr(job, req.params["what"])()
    return Response.json({"ok": True, "status": job.status})


# ── the pictures of a run: by their number in it (core/parser/jobs.ParserJob.path_of), never by a path ──
THUMB = 360            # a file in work, as its card shows it
FRAME = 1600           # a video's frame in the viewer


def _run_file(job_id, number) -> tuple[Path | None, str]:
    """(the file, "") of the run shown now (the current or the last one), or (None, why not)."""
    from core.parser import jobs
    job = jobs.current()
    try:
        job_id, number = int(job_id), int(number)
    except (TypeError, ValueError):
        return None, "Нет такого файла"
    if job is None or job.id != job_id:
        return None, "Этот запуск уже не показывается"
    path = job.path_of(number)
    if path is None:
        return None, "Этого файла уже нет в списке запуска"
    if not path.is_file():
        return None, "Файла уже нет на диске"
    return path, ""


def parser_thumb(req: Request) -> Response:
    from webui.pages.media import _MAKING, _placeholder
    path, _why = _run_file(req.params["job"], req.params["file"])
    if path is None:
        return _placeholder("нет файла")
    from core.image_safe import safe_thumbnail_path
    with _MAKING:
        made = safe_thumbnail_path(str(path), THUMB, THUMB)
    if not made or not Path(made).is_file():
        return _placeholder("нет превью")
    return Response(200, content_type="image/jpeg", file=Path(made), headers={"Cache-Control": "private, max-age=600"})


def parser_file(req: Request) -> Response:
    """The file itself (a picture, a video — streamed), or with ``frame=1`` a large frame of it."""
    import mimetypes
    from webui.pages.media import _MAKING, _placeholder
    path, why = _run_file(req.params["job"], req.params["file"])
    if path is None:
        return Response.not_found(why)
    if req.arg("frame"):
        from core.image_safe import safe_thumbnail_path
        with _MAKING:
            made = safe_thumbnail_path(str(path), FRAME, FRAME)
        if not made or not Path(made).is_file():
            return _placeholder("нет кадра")
        return Response(200, content_type="image/jpeg", file=Path(made), headers={"Cache-Control": "private, max-age=600"})
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Response(200, content_type=ctype, file=path, headers={"Cache-Control": "private, max-age=600"})


def parser_open(req: Request) -> Response:
    """«Открыть файл» / «Показать в папке» of the viewer: the source file in its usual program, like the old
    parser page's double click on a preview."""
    from webui import desktop
    body = req.json()
    path, why = _run_file(body.get("job"), body.get("file"))
    if path is None:
        return Response.json({"ok": False, "error": why}, 404)
    try:
        (desktop.reveal if body.get("reveal") else desktop.open_file)(path)
    except OSError as exc:
        return Response.json({"ok": False, "error": f"Не удалось открыть: {exc}"}, 500)
    return Response.json({"ok": True})


def _save_settings(changes: dict) -> None:
    from webui import settings_store
    settings_store.save(changes)


def parser_folder(req: Request) -> Response:
    path = str(req.json().get("path") or "").strip().strip('"')
    if not path or not Path(path).is_dir():
        return Response.json({"ok": False, "error": "Такой папки нет"}, 400)
    _save_settings({"root": str(Path(path))})
    return Response.json({"ok": True, "root": str(Path(path))})


# ── many cards at once ─────────────────────────────────────────────────────
def _ids(body: dict) -> list:
    raw = body.get("ids")
    if not isinstance(raw, list):
        return []
    out = []
    for value in raw[:RETRY_LIMIT]:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _rows(settings: dict, ids: list, where: str = "1=1") -> list:
    if not ids:
        return []
    out = []
    with db(settings, readonly=True) as con:
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            ph = ",".join("?" for _ in chunk)
            out += [dict(r) for r in con.execute(
                f"SELECT id, path, deleted, lifecycle, bucket FROM images WHERE id IN ({ph}) AND {where}", chunk)]
    return out


def parser_retry(req: Request) -> Response:
    """«Брак → Искать снова» for the chosen cards (or the whole Брак): a parser run over them."""
    from core import nomatch_retry
    from core.nomatch_db import list_nomatches
    from core.parser import jobs
    body = req.json()
    s = dict(req.settings)
    if body.get("all"):
        items = list_nomatches(None, settings=s)[:RETRY_LIMIT]
        label = "весь Брак"
    else:
        ids = _ids(body)
        if not ids:
            return Response.json({"ok": False, "error": "Не выбрано ни одной карточки"}, 400)
        rows = _rows(s, ids, "bucket='no_match' AND deleted=0")
        items = nomatch_retry.items_for_media(s, [r["path"] for r in rows])
        label = f"выбранные карточки Брака ({len(items)})"
    if not items:
        return Response.json({"ok": False, "error": "Среди выбранных нет файлов из Брака"}, 400)
    if jobs.is_running():
        return Response.json({"ok": False, "error": jobs.busy_text(jobs.owner())}, 409)
    nomatch_retry.restore_missing_copies(s, items)
    queue_root, queued, skipped = nomatch_retry.prepare_queue(s, items, label=label)
    if not queue_root or queued <= 0:
        return Response.json({"ok": False, "error": "Файлов выбранных карточек нет на диске"}, 409)
    run = nomatch_retry.retry_settings(s, queue_root, label=label, count=queued)
    try:
        job = jobs.start(run, label=f"Повторный поиск: {label}", kind="retry")
    except jobs.ParserBusy as exc:
        return _busy(exc)
    return Response.json({"ok": True, "id": job.id, "queued": queued, "skipped": len(skipped)})


def trash_restore(req: Request) -> Response:
    from core.library_lifecycle import restore_from_trash
    from webui.server import FILE_STREAMS
    ids = _ids(req.json())
    rows = _rows(req.settings, ids, "lifecycle IN ('trash','missing')")
    if not rows:
        return Response.json({"ok": False, "error": "Среди выбранных нет карточек из корзины"}, 400)
    with FILE_STREAMS.holding(*[r["path"] for r in rows]):
        result = restore_from_trash(req.settings, [r["id"] for r in rows])
    data.TAG_COUNTS.invalidate()
    done = int(result.get("restored") or 0) + int(result.get("merged_existing") or 0)
    return Response.json({"ok": bool(done), "restored": int(result.get("restored") or 0),
                          "merged": int(result.get("merged_existing") or 0), "errors": int(result.get("errors") or 0),
                          **({} if done else {"error": "Не удалось вернуть: файлов нет ни в корзине, ни на месте"})})


def trash_purge(req: Request) -> Response:
    """Delete for good: the chosen trashed cards, or the whole trash (``all``).  An empty list is
    refused — ``purge_trash`` takes «no ids» as «everything»."""
    from core.library_lifecycle import purge_trash
    from core.trash_tools import restoring
    from webui.server import FILE_STREAMS
    body = req.json()
    if restoring():             # it would take the cards «Вернуть всё» has not reached yet
        return Response.json({"ok": False, "error": "Идёт «Вернуть всё» — дождитесь конца или остановите его"}, 409)
    if body.get("all"):
        with db(req.settings, readonly=True) as con:
            ids = [int(r[0]) for r in con.execute(
                "SELECT id FROM images WHERE lifecycle IN ('trash','missing') AND deleted=1")]
    else:
        ids = _ids(body)
    rows = _rows(req.settings, ids, "lifecycle IN ('trash','missing') AND deleted=1")
    if not rows:
        return Response.json({"ok": False, "error": "Среди выбранных нет карточек из корзины"}, 400)
    with FILE_STREAMS.holding(*[r["path"] for r in rows]):
        result = purge_trash(req.settings, [r["id"] for r in rows], make_backup=True)
    data.TAG_COUNTS.invalidate()
    if result.get("error"):
        return Response.json({"ok": False, "error": result["error"]}, 409)
    return Response.json({"ok": True, "removed": int(result.get("removed_records") or 0),
                          "files": int(result.get("removed_files") or 0), "errors": int(result.get("errors") or 0)})
