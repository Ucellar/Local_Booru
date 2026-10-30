"""«Брак: фото или рисунок» on «Настройки»: which model answers, what it needs (onnxruntime, its weights), the
check on the person's own library and «Разобрать Брак заново» — page tasks of one kind (``core/visual_work``).
The section's fields are saved like any other section (``webui/pages/settings.py``)."""
from __future__ import annotations

import time

from webui.html import esc
from webui.server import Request, Response

KIND = "visual"
CHECK_PER_GROUP = 100           # pictures of each kind the check asks the models about


def register(router) -> None:
    router.add("GET", r"/api/visual/state", visual_state)
    router.add("POST", r"/api/visual/act", visual_act)


def fields() -> tuple:
    from core import visual_models
    from webui.settings_form import Field
    return (
        Field("visual_nomatch_classify_enabled", "bool", "Определять в Браке, фото это или рисунок",
              "новые файлы Брака — сразу, остальные — понемногу в фоне", default=True),
        Field("visual_nomatch_auto_download_model", "bool", "Скачивать модель самой",
              "с huggingface.co, один раз; картинки никуда не отправляются", default=True),
        Field("visual_nomatch_model", "choice", "Модель", default="auto",
              choices=tuple(visual_models.CHOICE_TITLES.items())),
        Field("visual_nomatch_min_confidence", "int", "Решать, только если уверена хотя бы на",
              default=visual_models.DEFAULT_MIN_CONFIDENCE, lo=50, hi=99, suffix="%",
              hint="иначе — «не уверена»: такой файл не попадёт ни в «фото», ни в «рисунок»"),
    )


def _when(ts) -> str:
    try:
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(float(ts))) if ts else ""
    except (TypeError, ValueError, OverflowError):
        return ""


def _check_table(result: dict | None) -> str:
    if not result:
        return ('<p class="hint">Проверка ещё не запускалась. Она возьмёт найденные картинки, про которые известно, '
                'фото это или нет (по тегам и сайтам), и ваши отметки в Браке, и посчитает, как часто каждая модель '
                'отвечает верно.</p>')
    groups = [(g, info) for g, info in (result.get("groups") or {}).items() if int(info.get("count") or 0)]
    head = "".join(f'<th>{esc(info.get("title") or g)}<br><small>{int(info.get("count") or 0)}</small></th>'
                   for g, info in groups)
    rows = []
    best = str(result.get("best") or "")
    for choice, model in (result.get("models") or {}).items():
        cells = []
        for g, _info in groups:
            cell = (model.get("groups") or {}).get(g) or {}
            total = int(cell.get("total") or 0)
            if not total:
                cells.append("<td>—</td>")
                continue
            share = round(100 * int(cell.get("right") or 0) / total)
            wrong = [int(x) for x in cell.get("mistakes") or [] if int(x) > 0]
            links = " ".join(f'<a href="/post/{x}">{x}</a>' for x in wrong[:6])
            unsure = int(cell.get("unsure") or 0)
            cells.append(f'<td><b>{share} %</b><small> ошибок {int(cell.get("wrong") or 0)}'
                         f'{f", не уверена {unsure}" if unsure else ""}</small>'
                         f'{f"<br><small>ошибки: {links}</small>" if links else ""}</td>')
        score = model.get("score")
        ms = model.get("ms")
        pick = (f'<button type="button" class="link" data-action="visual-act" data-act="choose" '
                f'data-model="{esc(choice)}">выбрать</button>' if choice not in ("heuristic",) else "")
        mark = ' class="best"' if choice == best else ""
        rows.append(f'<tr{mark}><th scope="row">{esc(model.get("title") or choice)}'
                    f'{" — лучше всех" if choice == best else ""}<br><small>'
                    f'{f"{score} % в среднем" if score is not None else "мало картинок для оценки"}'
                    f'{f", {ms} мс на картинку" if ms is not None else ""}</small> {pick}</th>{"".join(cells)}</tr>')
    stopped = " (остановлена на середине)" if result.get("stopped") else ""
    note = f'<p class="tool-warn">{esc(result["note"])}</p>' if result.get("note") else ""
    return (f'<p class="hint">Последняя проверка: {esc(_when(result.get("at")))}{stopped}. Верные ответы по видам картинок; '
            f'теги бывают ошибочными, так что это оценка, ваши отметки — точный ответ.</p>{note}'
            f'<div class="table-wrap"><table class="visual-check"><thead><tr><th>Модель</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def extra(settings: dict) -> str:
    """The section's state, actions, journal and the last check."""
    from core import visual_models, visual_work
    state = visual_models.runtime()
    choice = visual_models.chosen(settings)
    lines = []
    if state["ok"]:
        lines.append(f"onnxruntime {esc(state['version'])} установлен.")
    else:
        lines.append('<span class="tool-warn">onnxruntime не установлен</span> — модели «аниме или фото» и «вид '
                     'картинки» без него не работают (≈15 МБ).')
    if choice in visual_models.MODEL_CHOICES:
        absent = visual_models.missing(settings, choice)
        title = visual_models.NAMES.get(choice, choice)
        lines.append(f"Отвечает: {esc(title)}" + (" — <span class=\"tool-warn\">не скачана</span>." if absent else "."))
    elif choice == "clip":
        lines.append("Отвечает: CLIP.")
    elif choice == "heuristic":
        lines.append("Отвечает: простая оценка по цветам — она часто ошибается.")
    else:
        lines.append('<span class="tool-warn">Сейчас отвечать некому</span> — установите onnxruntime.')
    try:
        waiting = visual_work.pending_count(settings)
        lines.append(f"Файлов Брака без ответа этой модели: {waiting}." if waiting else "Все файлы Брака проверены.")
    except Exception:  # an old base without the Брак table: the rest of the section still works
        pass
    buttons = []
    if not state["ok"]:
        buttons.append('<button type="button" class="primary" data-action="visual-act" data-act="install">'
                       'Установить onnxruntime</button>')
    elif choice in visual_models.MODEL_CHOICES and visual_models.missing(settings, choice):
        size = sum(s.size_mb for s in visual_models.missing(settings, choice))
        buttons.append(f'<button type="button" class="primary" data-action="visual-act" data-act="download">'
                       f'Скачать модель ({size} МБ)</button>')
    buttons += ['<button type="button" data-action="visual-act" data-act="check" '
                'title="Найденные картинки с известным ответом и ваши отметки в Браке">Проверить на моей библиотеке</button>',
                '<button type="button" data-action="visual-act" data-act="reclassify" '
                'data-ask="Модель проверит весь Брак заново (ваши отметки останутся) — нажмите ещё раз">'
                'Разобрать Брак заново</button>',
                '<button type="button" class="danger" data-action="visual-act" data-act="stop" hidden>Остановить</button>']
    return (f'<p class="hint">{" ".join(lines)}</p>'
            f'<div class="tool-actions">{"".join(buttons)}<span class="action-status" role="status"></span></div>'
            '<div class="job" data-job data-visual-job hidden><div class="job-head"><b class="job-title"></b> '
            '<span class="job-status"></span></div><pre class="job-log" tabindex="0" aria-label="Журнал"></pre></div>'
            f'{_check_table(visual_work.last_check(settings))}')


# ── the API ────────────────────────────────────────────────────────────────
def visual_state(req: Request) -> Response:
    from core import page_jobs
    task = page_jobs.current(KIND)
    if task is None:
        return Response.json({"ok": True, "status": "idle"})
    return Response.json({"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0))})


def _busy() -> str:
    from core.nomatch_manual import library_busy
    return library_busy()


def visual_act(req: Request) -> Response:
    from core import page_jobs, visual_models, visual_work
    from webui.settings_store import for_task
    body = req.json()
    act = str(body.get("act") or "")
    if act == "stop":
        task = page_jobs.current(KIND)
        if task is None or not task.running:
            return Response.json({"ok": False, "error": "Сейчас ничего не идёт"}, 409)
        task.stop()
        return Response.json({"ok": True, "message": "Останавливаю"})
    if act == "choose":
        model = str(body.get("model") or "")
        if model not in visual_models.CHOICE_TITLES:
            return Response.json({"ok": False, "error": "Нет такой модели"}, 400)
        from webui import settings_store
        if not settings_store.save({"visual_nomatch_model": model}):
            return Response.json({"ok": False, "error": "Окно программы не ответило — не сохранено"}, 503)
        return Response.json({"ok": True, "message": "Выбрано", "reload": True})
    settings = for_task(req.settings)
    if act == "install":
        label, work = "Установка onnxruntime", lambda progress, stop: visual_work.install_runtime(
            progress=progress, stop_check=stop)
    elif act == "download":
        choice = visual_models.chosen(settings)
        if choice not in visual_models.MODEL_CHOICES:
            return Response.json({"ok": False, "error": "Выбранной модели скачивать нечего"}, 400)
        if not visual_models.runtime()["ok"]:
            return Response.json({"ok": False, "error": "Сначала установите onnxruntime"}, 409)

        def work(progress, stop):
            visual_models.download(settings, choice, progress=progress, stop_check=stop)
            return {"ok": True}
        label = "Скачивание модели «фото или рисунок»"
    elif act == "check":
        def work(progress, stop):
            if visual_models.runtime()["ok"] and bool(settings.get("visual_nomatch_auto_download_model", True)):
                for fast in ("anime_real_fast", "anime_class_fast"):       # the fast ones are compared too
                    visual_models.download(settings, fast, progress=progress, stop_check=stop)
            result = visual_work.run_check(settings, per_group=CHECK_PER_GROUP, progress=progress, stop_check=stop)
            if not any(int(g.get("count") or 0) for g in result["groups"].values()):
                return {"failed": "В библиотеке нет картинок с известным ответом: найденных картинок с тегами "
                                  "3D, ИИ или фото и ваших отметок в Браке"}
            return {"stopped": bool(result.get("stopped"))}
        label = "Проверка «фото или рисунок» на вашей библиотеке"
    elif act == "reclassify":
        busy = _busy()
        if busy:
            return Response.json({"ok": False, "error": busy}, 409)
        why = visual_work.problem(settings)
        if why:
            return Response.json({"ok": False, "error": f"Модель не может ответить: {why}"}, 409)

        def work(progress, stop):
            result = visual_work.classify_pending(settings, everything=True, progress=progress, stop_check=stop)
            progress(f"Проверено {result['checked']}: фото {result['photo']}, рисунков {result['drawing']}, "
                     f"не уверена {result['unsure']}" + (f", ошибок {result['errors']}" if result["errors"] else ""))
            return {"stopped": result["stopped"]}
        label = "Брак: фото или рисунок — заново"
    else:
        return Response.json({"ok": False, "error": "Неизвестное действие"}, 400)

    def run(progress, stop):
        with visual_work.holding_work(progress):
            return work(progress, stop)
    try:
        task = page_jobs.start(KIND, label, run)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца."}, 409)
    return Response.json({"ok": True, "id": task.id})
