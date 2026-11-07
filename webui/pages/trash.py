"""The section «Корзина» of the gallery: why the cards are there (the line above the pictures; its links narrow the
search with the word ``корзина:<причина>``) and «Вернуть всё» — the cards of the search shown (no search: the whole
trash) come back in a page task, in portions, with a journal (``core.trash_tools.restore_many``).

(v5.44.118, the sketch E) «В корзину выбранные (N)» and — with a search in the gallery — «В корзину все найденные (N)»
of the gallery's select bar (:func:`move_buttons`): the cards go into the trash the same way, in a page task of their
own (``trash_tools.MOVE_KIND``: in portions, with a journal and «Остановить»; the page does not wait), with the reason
«выбрано в галерее» / «все найденные по поиску» (``trash_tools.LABELS``) — from there they come back as any other.
Without a search there is no «all»: the whole library never goes into the trash by one press."""
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
    router.add("POST", r"/api/trash/move", move)                    # (v5.44.118) «В корзину выбранные / все найденные»
    router.add("GET", r"/api/trash/move/state", move_state)
    router.add("POST", r"/api/trash/move/stop", move_stop)


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
                f'вернуть. {_purge_words(settings)}</p>'
                f'<nav class="brak-filters" aria-label="Почему в корзине">Почему: {" · ".join(links)}</nav>')
    return (f'<section class="trash-bar" id="trash-bar">{line}'
            '<div class="job" data-job hidden>'
            '<div class="job-head"><b class="job-title"></b> <span class="job-status"></span> '
            '<button type="button" class="link" data-action="trash-restore-stop" hidden>Остановить</button></div>'
            '<pre class="job-log" tabindex="0" aria-label="Журнал"></pre></div></section>')


def purge_sentence(settings: dict) -> str:
    """(v5.44.116) Whether the trash empties itself — «Настройки → Библиотека → Очищать корзину»
    (``trash_auto_purge_days``, ``library_lifecycle.purge_expired_trash``), as text; (v5.44.118) the phone's «Корзина»
    says it too."""
    try:
        days = max(0, int(settings.get("trash_auto_purge_days", 0) or 0))
    except (TypeError, ValueError):
        days = 0
    from webui.pages.post_sets import plural
    return (f"Корзина очищается сама через {plural(days, ('день', 'дня', 'дней'))}: что лежит в ней дольше, "
            "удаляется навсегда" if days else "Корзина очищается только вручную")


def _purge_words(settings: dict) -> str:
    """(v5.44.116) :func:`purge_sentence` with a link to the setting: the trash said nothing of it (the review)."""
    return f'{purge_sentence(settings)} (<a href="/settings#set-library">Настройки → Библиотека</a>).'


def restore_button(ctx: dict, total: int) -> str:
    """«Вернуть всё» of the select bar: the whole trash, or the cards of the search shown."""
    narrowed = bool(ctx["tags"]) or ctx["source"] != "all"
    text = f"Вернуть все найденные ({total})" if narrowed else "Вернуть всё"
    return (f'<button type="button" data-action="trash-restore-all" data-tags="{esc(ctx["tags"])}" '
            f'data-source="{esc(ctx["source"])}" data-count="{total}" '
            f'title="Вернуть из корзины {"все карточки этого поиска" if narrowed else "все карточки"}; если такой '
            f'файл уже есть в библиотеке, его теги и источники добавятся к живой карточке">{esc(text)}</button>')


# ── (v5.44.118) «В корзину выбранные / все найденные» of the gallery ────────
def narrowed(ctx: dict) -> bool:
    """A search in the gallery's address: tags (or words) or a site — «все найденные» are its cards."""
    return bool(ctx["tags"]) or (ctx["source"] or "all") != "all"


def search_words(tags: str, source: str) -> str:
    """The search in words: «lake», «lake · сайт e621.net», «сайт e621.net»."""
    return " · ".join(x for x in (str(tags or "").strip(), f"сайт {source}" if (source or "all") != "all" else "") if x)


def move_buttons(ctx: dict, files_total: int) -> tuple[str, str]:
    """(the button of the chosen cards — inside the select tools; the button of all the cards found — beside them,
    "" without a search).  Both ask again before they move anything (tools.js: «Точно в корзину…? Нажмите ещё
    раз»)."""
    chosen = (f'<button type="button" class="danger" data-action="trash-move" data-needs-selection disabled '
              f'data-count-label="В корзину выбранные" data-section="{esc(ctx["section"])}" '
              f'title="Выбранные карточки — в корзину (плитка сборки — со всеми её карточками); оттуда их можно '
              f'вернуть">В корзину выбранные</button>')
    if not narrowed(ctx) or files_total <= 0:
        return chosen, ""
    words = search_words(ctx["tags"], ctx["source"])
    ask = (f"Точно в корзину все {files_total} найденных по «{words}»? Нажмите ещё раз" if files_total != 1 else
           f"Точно в корзину найденную по «{words}»? Нажмите ещё раз")
    whole = (f'<button type="button" class="danger" data-action="trash-move-all" data-tags="{esc(ctx["tags"])}" '
             f'data-source="{esc(ctx["source"])}" data-section="{esc(ctx["section"])}" data-count="{files_total}" '
             f'data-ask="{esc(ask)}" title="Все карточки этого поиска — в корзину; оттуда их можно вернуть">'
             f'В корзину все найденные ({files_total})</button>')
    return chosen, whole


def move_job() -> str:
    """The journal of «В корзину…» under the select bar (shown while it goes and a moment after its end)."""
    return ('<section class="trash-move" id="trash-move"><div class="job" data-job hidden>'
            '<div class="job-head"><b class="job-title"></b> <span class="job-status"></span> '
            '<button type="button" class="link" data-action="trash-move-stop" hidden>Остановить</button></div>'
            '<pre class="job-log" tabindex="0" aria-label="Журнал"></pre></div></section>')


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


MOVE_LIMIT = 5000           # tiles one «В корзину выбранные» takes (a page shows at most 400)


def _chosen(value) -> list:
    out: list = []
    for item in (value if isinstance(value, list) else [])[:MOVE_LIMIT]:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return [x for x in dict.fromkeys(out) if x > 0]


def move(req: Request) -> Response:
    """(v5.44.118) «В корзину выбранные» — {ids, section}: the chosen tiles (where the section shows a сборка as one
    tile, its whole сборка) — or «В корзину все найденные» — {all: true, tags, section, source}: every card of that
    search, only with a search.  A page task (``trash_tools.trash_many``): the page does not wait."""
    from core import card_sets, page_jobs, trash_tools
    from core.nomatch_manual import library_busy
    from core.redaction import sanitize_text
    from webui import data
    from webui.server import FILE_STREAMS
    body = req.json()
    section = str(body.get("section") or "all").strip() or "all"
    if section == "trash":
        return Response.json({"ok": False, "error": "Эти карточки уже в корзине"}, 400)
    busy = library_busy() or (f"С корзиной уже работают ({trash_tools.bulk_busy()}) — дождитесь конца"
                              if trash_tools.bulk_busy() else "")
    if busy:
        return Response.json({"ok": False, "error": busy}, 409)
    settings = dict(req.settings)
    if body.get("all"):
        tags = str(body.get("tags") or "").strip()
        source = str(body.get("source") or "all").strip() or "all"
        if not tags and source == "all":            # the whole library never goes by one press
            return Response.json({"ok": False, "error": "Без поиска «все найденные» нет — найдите карточки поиском "
                                                        "или выберите их"}, 400)
        ids = data.result_ids(settings, tags, section=section, source=source)
        reason, target = "gallery_found", sanitize_text(search_words(tags, source))
        label = f"В корзину: все найденные по «{target}» ({len(ids)})"
        about = f" — по поиску «{target}»"
        if not ids:
            return Response.json({"ok": False, "error": "По этому поиску ничего нет"}, 400)
    else:
        ids = _chosen(body.get("ids"))
        if not ids:
            return Response.json({"ok": False, "error": "Не выбрано ни одной карточки"}, 400)
        if data.collapses(settings, section):         # a tile of a сборка is the сборка
            ids = card_sets.expand(settings, ids)
        reason, target, about = "gallery_selected", "", " — выбранные"
        label = f"В корзину: выбранные ({len(ids)})"

    def work(progress, stop_check):
        progress(f"Карточек: {len(ids)}{about}; они уходят в корзину, оттуда их можно вернуть")
        try:
            return trash_tools.trash_many(settings, ids, reason=reason, target=target, hold=FILE_STREAMS.holding,
                                          progress=progress, stop_check=stop_check, busy=library_busy)
        except trash_tools.TrashBusy as exc:       # «Вернуть всё» or a purge took the trash between press and start
            return {"failed": str(exc)}
        finally:
            data.TAG_COUNTS.invalidate()

    try:
        task = page_jobs.start(trash_tools.MOVE_KIND, label, work)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": f"{exc} — дождитесь конца"}, 409)
    return Response.json({"ok": True, "id": task.id, "cards": len(ids)})


def move_state(req: Request) -> Response:
    """The journal of «В корзину…» (``ended_ago`` — seconds since its end, as «Вернуть всё»)."""
    from core import page_jobs, trash_tools
    task = page_jobs.current(trash_tools.MOVE_KIND)
    if task is None:
        return Response.json({"id": 0, "status": "", "log": [], "last_seq": 0})
    state = task.snapshot(after=req.int_arg("after", 0, lo=0))
    state["ended_ago"] = int(time.time() - task.finished_at) if task.finished_at else 0
    state["show_ended"] = SHOW_ENDED
    if isinstance(task.result, dict):
        state["trashed"] = int(task.result.get("trashed") or 0)
        state["left"] = int(task.result.get("left") or 0)
    return Response.json(state)


def move_stop(req: Request) -> Response:
    """«Остановить» «В корзину…»: after the portion being written; the cards not reached stay where they are."""
    from core import page_jobs, trash_tools
    task = page_jobs.current(trash_tools.MOVE_KIND)
    if task is None or not task.running:
        return Response.json({"ok": False, "error": "«В корзину» сейчас не идёт"}, 409)
    task.stop()
    return Response.json({"ok": True})
