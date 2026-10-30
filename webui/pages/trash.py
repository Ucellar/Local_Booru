"""The section «Корзина» of the gallery: why the cards are there (the line above the pictures; its links narrow the
search with the word ``корзина:<причина>``) and «Вернуть всё» — the cards of the search shown (no search: the whole
trash) come back in a page task, in portions, with a journal (``core.trash_tools.restore_many``)."""
from __future__ import annotations

import logging
import time

from webui.html import esc, human_size, url
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.webui")
SHOW_ENDED = 120            # seconds a finished «Вернуть всё» still shows its journal (the page reloads at its end)


def register(router) -> None:
    router.add("POST", r"/api/trash/restore-all", restore_all)
    router.add("GET", r"/api/trash/restore-all/state", restore_state)
    router.add("POST", r"/api/trash/restore-all/stop", restore_stop)


def bar(settings: dict, ctx: dict) -> str:
    """«В корзине: 120 файлов, 1.2 ГБ. Почему: все · удалено вручную 80 · …» and the journal of «Вернуть всё»."""
    from core import trash_tools
    try:
        groups = trash_tools.summary(settings, source=ctx["source"])
    except Exception as exc:  # the line is a convenience: the grid is shown without it
        _LOG.warning("trash reasons: %s", exc)
        groups = []
    words = ctx["tags"].split()
    chosen = next((w.lower() for w in words if w.lower().startswith(trash_tools.WORD)), "")
    rest = " ".join(w for w in words if not w.lower().startswith(trash_tools.WORD))

    def link(word: str, text: str, count: int) -> str:
        query = " ".join(x for x in (rest, word) if x)
        href = url("/posts", tags=query, section="trash", source=ctx["source"], order=ctx["order"])
        current = ' aria-current="true" class="active"' if word == chosen else ""
        return f'<a href="{esc(href)}"{current}>{esc(text)} <span class="muted">{count}</span></a>'

    line = ""
    if groups:
        files = sum(g[2] for g in groups)
        size = sum(g[3] for g in groups)
        links = [link("", "все", files)] + [link(trash_tools.WORD + code, text, count)
                                            for code, text, count, _size in groups]
        line = (f'<p class="trash-total">В корзине файлов: {files} ({esc(human_size(size))}) — всё это ещё можно '
                'вернуть.</p>'
                f'<nav class="brak-filters" aria-label="Почему в корзине">Почему: {" · ".join(links)}</nav>')
    return (f'<section class="trash-bar" id="trash-bar">{line}'
            '<div class="job" data-job hidden>'
            '<div class="job-head"><b class="job-title"></b> <span class="job-status"></span> '
            '<button type="button" class="link" data-action="trash-restore-stop" hidden>Остановить</button></div>'
            '<pre class="job-log" tabindex="0" aria-label="Журнал"></pre></div></section>')


def restore_button(ctx: dict, total: int) -> str:
    """«Вернуть всё» of the select bar: the whole trash, or the cards of the search shown."""
    narrowed = bool(ctx["tags"]) or ctx["source"] != "all"
    text = f"Вернуть все найденные ({total})" if narrowed else "Вернуть всё"
    return (f'<button type="button" data-action="trash-restore-all" data-tags="{esc(ctx["tags"])}" '
            f'data-source="{esc(ctx["source"])}" data-count="{total}" '
            f'title="Вернуть из корзины {"все карточки этого поиска" if narrowed else "все карточки"}; если такой '
            f'файл уже есть в библиотеке, его теги и источники добавятся к живой карточке">{esc(text)}</button>')


# ── the API ────────────────────────────────────────────────────────────────
def restore_all(req: Request) -> Response:
    """Start «Вернуть всё»: the trashed cards of the search ``tags`` / ``source`` (nothing — the whole trash)."""
    from core import page_jobs, trash_tools
    from core.nomatch_manual import library_busy
    from webui import data
    from webui.server import FILE_STREAMS
    body = req.json()
    tags = str(body.get("tags") or "").strip()
    source = str(body.get("source") or "all").strip() or "all"
    busy = library_busy() or (f"С корзиной уже работают ({trash_tools.bulk_busy()}) — дождитесь конца"
                              if trash_tools.bulk_busy() else "")
    if busy:
        return Response.json({"ok": False, "error": busy}, 409)
    settings = dict(req.settings)
    ids = data.result_ids(settings, tags, section="trash", source=source)
    if not ids:
        return Response.json({"ok": False, "error": "В корзине нет таких карточек" if tags or source != "all"
                              else "Корзина пуста"}, 400)
    narrowed = bool(tags) or source != "all"
    label = f"Вернуть из корзины: найденные ({len(ids)})" if narrowed else f"Вернуть из корзины всё ({len(ids)})"

    def work(progress, stop_check):
        about = " ".join(x for x in (f"по запросу «{tags}»" if tags else "", f"сайт {source}" if source != "all" else "") if x)
        progress(f"Карточек: {len(ids)}" + (f" — {about}" if about else "") + "; файлы возвращаются на прежние места")
        try:
            return trash_tools.restore_many(settings, ids, hold=FILE_STREAMS.holding, progress=progress,
                                            stop_check=stop_check, busy=library_busy)
        except trash_tools.TrashBusy as exc:       # a purge took the trash between the press and the start
            return {"failed": str(exc)}
        finally:
            data.TAG_COUNTS.invalidate()

    try:
        task = page_jobs.start(trash_tools.RESTORE_KIND, label, work)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": str(exc)}, 409)
    return Response.json({"ok": True, "id": task.id, "cards": len(ids)})


def restore_state(req: Request) -> Response:
    """The journal of «Вернуть всё» (``ended_ago`` — seconds since it ended: the page shows a fresh end only)."""
    from core import page_jobs, trash_tools
    task = page_jobs.current(trash_tools.RESTORE_KIND)
    if task is None:
        return Response.json({"id": 0, "status": "", "log": [], "last_seq": 0})
    state = task.snapshot(after=req.int_arg("after", 0, lo=0))
    state["ended_ago"] = int(time.time() - task.finished_at) if task.finished_at else 0
    state["show_ended"] = SHOW_ENDED
    if isinstance(task.result, dict):
        state["restored"] = int(task.result.get("restored") or 0)
        state["merged"] = int(task.result.get("merged_existing") or 0)
    return Response.json(state)


def restore_stop(req: Request) -> Response:
    """«Остановить»: after the portion being written; the cards not reached stay in the trash."""
    from core import page_jobs, trash_tools
    task = page_jobs.current(trash_tools.RESTORE_KIND)
    if task is None or not task.running:
        return Response.json({"ok": False, "error": "«Вернуть всё» сейчас не идёт"}, 409)
    task.stop()
    return Response.json({"ok": True})
