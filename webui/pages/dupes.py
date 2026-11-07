"""The page «Дубли»: pictures of the library that look the same (pHash), in pairs — keep one, the
other goes to the trash and gives its tags, sources and the rest to the one kept; «Альтернативная
версия» and «Не дубль» are remembered (the pair is not shown again), (v5.44.116) and «Альтернативная версия» puts the
two cards into one сборка.  The search runs as a page task (``core/page_jobs``); the pairs are what the last search
stored (``duplicate_relations``)."""
from __future__ import annotations

import time
from pathlib import Path

from webui.html import esc, human_size, page_frame
from webui.server import Request, Response

PAGE_PAIRS = 60                  # pairs shown at once («Показать следующие» brings the next ones)
KIND = "duplicates"


def register(router) -> None:
    router.add("GET", r"/dupes", dupes_page)
    router.add("GET", r"/api/dupes/state", dupes_state)
    router.add("POST", r"/api/dupes/scan", dupes_scan)
    router.add("POST", r"/api/dupes/stop", dupes_stop)
    router.add("POST", r"/api/dupes/resolve", dupes_resolve)
    router.add("POST", r"/api/dupes/relation", dupes_relation)


def _prefs() -> dict:
    from webui import prefs
    stored = prefs.load()
    return {"thumbs": stored.get("thumbs", "m")}         # the look is the page's own (page_frame)


def _threshold(settings: dict) -> int:
    try:
        return max(50, min(100, int(settings.get("duplicate_visual_threshold") or 85)))
    except (TypeError, ValueError):
        return 85


def _quality(item: dict) -> tuple:
    width, height = (item.get("pixels") or [0, 0])[:2]
    return int(width or 0) * int(height or 0), int(item.get("bytes") or 0)


def _item_html(item: dict, relation_id: int, best: bool) -> str:
    image_id = int(item["image_id"])
    width, height = (item.get("pixels") or [0, 0])[:2]
    ext = Path(item.get("name") or item.get("path") or "").suffix.lstrip(".").upper() or "?"
    facts = [f"{int(width)}×{int(height)}" if width and height else "", human_size(int(item.get("bytes") or 0)), ext]
    better = '<span class="badge owned" title="Больше пикселей (при равных — больше байт)">лучше</span>' if best else ""
    return (f'<figure class="dupe-item{" best" if best else ""}" data-id="{image_id}">'
            f'<a class="dupe-pic" href="/post/{image_id}" title="Открыть в галерее"><img src="/thumb/{image_id}" alt="" loading="lazy"></a>'
            f'<figcaption><span class="dupe-name">{esc(item.get("name") or "")}</span>'
            f'<span class="dupe-facts">{esc(" · ".join(f for f in facts if f))} {better}</span>'
            f'<label class="check"><input type="radio" name="keep-{relation_id}" value="{image_id}"'
            f'{" checked" if best else ""}> Оставить эту</label></figcaption></figure>')


def _pair_html(group: dict) -> str:
    items = group["items"][:2]
    best = max(range(len(items)), key=lambda i: _quality(items[i])) if items else 0
    if len(items) == 2 and _quality(items[0]) == _quality(items[1]):
        best = 0                                       # equal: the older card (smaller id comes first)
    rid = int(group["relation_id"])
    ids = " ".join(str(int(i["image_id"])) for i in items)
    return (f'<article class="dupe" data-relation="{rid}" data-ids="{ids}">'
            f'<header class="dupe-head"><span class="dupe-score">{float(group.get("similarity") or 0):.0f}%</span> похожесть '
            f'<small>pHash, расстояние {int(group.get("distance") or 0)}</small></header>'
            f'<div class="dupe-pair">{"".join(_item_html(it, rid, i == best) for i, it in enumerate(items))}</div>'
            f'<div class="tool-actions">'
            f'<button type="button" class="primary" data-action="dupe-resolve">Оставить отмеченную, вторую — в корзину</button>'
            f'<button type="button" data-action="dupe-relation" data-relation-kind="alternative" '
            f'title="Обе остаются и собираются в одну сборку; пара больше не показывается">Альтернативная версия</button>'
            f'<button type="button" data-action="dupe-relation" data-relation-kind="not_duplicate">Не дубль</button>'
            f'<button type="button" class="link" data-action="dupe-skip">Пропустить</button>'
            f'<span class="action-status" role="status"></span></div></article>')


def _when(ts: int) -> str:
    return time.strftime("%d.%m %H:%M", time.localtime(ts)) if ts else ""


def dupes_page(req: Request) -> Response:
    from core.duplicate_scanner import latest_duplicate_scan, load_duplicate_groups
    s = req.settings
    try:
        data = load_duplicate_groups(s, limit=PAGE_PAIRS)
        last = latest_duplicate_scan(s)
    except Exception as exc:                # an old base without the tables: the search creates them
        data, last = {"total_groups": 0, "groups": []}, {}
        note_error = f'<p class="tool-warn">Список пар не прочитан: {esc(type(exc).__name__)}</p>'
    else:
        note_error = ""
    total, groups = int(data.get("total_groups") or 0), list(data.get("groups") or [])
    if last:
        status = {"completed": "закончен", "running": "идёт или был прерван", "cancelled": "остановлен",
                  "failed": "прерван ошибкой"}.get(str(last.get("status")), str(last.get("status")))
        last_line = (f'Последний поиск: {_when(int(last.get("started_at") or 0))}, порог {int(last.get("threshold_percent") or 0)}%, '
                     f'{status}; картинок {int(last.get("total_images") or 0)}, пар {int(last.get("stored_pairs") or 0)}.')
        if int(last.get("unindexed_files") or 0):
            last_line += f' Файлов в папках библиотеки без записи в базе: {int(last["unindexed_files"])} — они не сравнивались.'
    else:
        last_line = "Поиска ещё не было."
    pairs = "".join(_pair_html(g) for g in groups)
    shown = (f'<p class="dupes-count">Пар на проверку: <b>{total}</b>' + (f", показаны первые {len(groups)}" if total > len(groups) else "")
             + "</p>") if total else ('<div class="empty">Пар на проверку нет — похожих картинок с этим порогом не нашлось.</div>'
                                      if last else '<div class="empty">«Искать дубли» сравнит все картинки библиотеки — '
                                      'пары появятся здесь.</div>')
    more = ('<div class="tool-actions dupes-more"><a class="button" href="/dupes">Показать следующие</a></div>'
            if total > len(groups) else "")
    bulk = ('<div class="tool-actions dupes-bulk">'
            '<button type="button" data-action="dupe-resolve-all">Во всех показанных парах: оставить отмеченную, вторую — в корзину</button>'
            '<span class="action-status" role="status"></span></div>') if groups else ""
    body = f"""
<section class="tool tool-wide dupes" id="dupes">
  <header class="tool-head">
    <h1>Дубли</h1>
    <p class="tool-lead">Похожие картинки библиотеки — по отпечатку (pHash), парами. Оставь лучшую: вторая уйдёт в корзину,
    а её теги, источники и заметки перейдут к оставленной. «Лучше» лишь подсказывает, у какой больше пикселей.</p>
    <form class="tool-actions head-actions dupes-scan" data-form="dupes-scan">
      <label class="num">Порог похожести, % <input type="number" name="threshold" min="50" max="100" value="{_threshold(s)}"></label>
      <button type="submit" class="primary" data-scan>Искать дубли</button>
      <button type="button" class="danger" data-action="dupes-stop" disabled>Остановить</button>
      <span class="action-status" role="status"></span>
    </form>
  </header>
  <p class="hint dupes-last">{esc(last_line)} Ниже порог — больше пар (и больше просто похожих картинок).</p>
  {note_error}
  <div class="job panel" data-job hidden>
    <div class="job-head"><b class="job-title"></b> <span class="job-status"></span></div>
    <pre class="job-log" tabindex="0" aria-label="Журнал поиска"></pre>
  </div>
  <div class="dupes-bar">{shown}{bulk}</div>
  <div class="dupes-list">{pairs}</div>
  {more}
</section>"""
    return Response.html(page_frame("Дубли", body, active="dupes", subnav=False, kind="tool",
                                    body_data={"tool": "dupes"}, **_prefs()))


# ── the search ─────────────────────────────────────────────────────────────
def dupes_state(req: Request) -> Response:
    from core import page_jobs
    task = page_jobs.current(KIND)
    if task is None:
        return Response.json({"ok": True, "status": "idle"})
    return Response.json({"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0))})


def dupes_scan(req: Request) -> Response:
    from core import page_jobs
    from webui import settings_store
    try:
        threshold = max(50, min(100, int(req.json().get("threshold") or 85)))
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Порог — число от 50 до 100"}, 400)
    settings_store.save({"duplicate_visual_threshold": threshold})
    from webui.settings_store import for_task
    settings = for_task(req.settings, duplicate_visual_threshold=threshold)

    def run(progress, stop_check):
        from core.duplicate_scanner import run_duplicate_scan
        progress(f"Ищу похожие картинки, порог {threshold}%…")
        result = run_duplicate_scan(settings, threshold, mode="forced", progress=progress, stop_check=stop_check,
                                    result_limit=PAGE_PAIRS)
        if result.get("cancelled"):
            progress("Поиск остановлен — прежний список пар оставлен как был")
        else:
            progress(f"Найдено пар: {int(result.get('total_groups') or 0)}")
        return {k: v for k, v in result.items() if k != "groups"}
    try:
        task = page_jobs.start(KIND, f"Поиск дублей, порог {threshold}%", run)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": str(exc)}, 409)
    return Response.json({"ok": True, "id": task.id})


def dupes_stop(req: Request) -> Response:
    from core import page_jobs
    task = page_jobs.current(KIND)
    if task is None or not task.running:
        return Response.json({"ok": False, "error": "Поиск сейчас не идёт"}, 409)
    task.stop()
    return Response.json({"ok": True})


# ── decisions ──────────────────────────────────────────────────────────────
def _live_rows(settings: dict, ids: list) -> dict:
    from core.database.connection import db
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    with db(settings, readonly=True) as con:
        return {int(r["id"]): dict(r) for r in con.execute(
            f"SELECT id, path, hash_md5 FROM images WHERE deleted=0 AND id IN ({marks})", ids)}


def dupes_resolve(req: Request) -> Response:
    """Keep one picture of each pair, the other goes to the trash with its metadata merged into the
    one kept (``library_service.trash_duplicate_groups``: one backup of the base for the batch)."""
    from core.services.library_service import trash_duplicate_groups
    from webui import data
    from webui.server import FILE_STREAMS
    body = req.json()
    raw = body.get("plans") if isinstance(body.get("plans"), list) else []
    plans: list = []
    for item in raw[:500]:
        try:
            keep = int(item.get("keep"))
            trash = [int(x) for x in item.get("trash") or [] if int(x) != keep]
        except (TypeError, ValueError, AttributeError):
            continue
        if keep and trash:
            plans.append((keep, trash))
    if not plans:
        return Response.json({"ok": False, "error": "Не отмечено, что оставить"}, 400)
    kept = {keep for keep, _t in plans}
    trashed = {x for _k, trash in plans for x in trash}
    if kept & trashed:
        return Response.json({"ok": False, "error": "Одна и та же картинка в одной паре оставляется, а в другой уходит в "
                                                    "корзину — решите эти пары по одной"}, 409)
    rows = _live_rows(req.settings, sorted(kept | trashed))
    if (kept | trashed) - set(rows):
        return Response.json({"ok": False, "error": "Некоторых картинок уже нет в библиотеке (в корзине или удалены) — "
                                                    "обновите страницу"}, 409)
    batch = [{"group_index": i + 1, "group_label": f"Пара {i + 1}", "keep_path": rows[keep]["path"],
              "delete_paths": [rows[x]["path"] for x in trash]} for i, (keep, trash) in enumerate(plans)]
    with FILE_STREAMS.holding(*[rows[x]["path"] for x in trashed]):
        try:
            result = trash_duplicate_groups(req.settings, batch)
        except (ValueError, RuntimeError) as exc:      # the checks of the batch: nothing was changed
            return Response.json({"ok": False, "error": str(exc)}, 409)
    data.TAG_COUNTS.invalidate()
    result = dict(result or {})
    return Response.json({"ok": True, "trashed": int(result.get("trashed_records") or 0),
                          "merged": int(result.get("metadata_merged") or 0), "errors": int(result.get("errors") or 0),
                          "gone": sorted(trashed)})


def dupes_relation(req: Request) -> Response:
    """«Альтернативная версия» / «Не дубль»: remembered by the MD5 of both pictures — the pair is
    not offered again (a picture replaced by other bytes is compared anew).  (v5.44.116) «Альтернативная версия» also
    puts the two cards into one сборка (:func:`_together`; the review: it only remembered the pair)."""
    from core.nomatch_manual import library_busy
    from core.services.library_service import remember_duplicate_relation
    body = req.json()
    kind = str(body.get("relation") or "")
    if kind not in ("alternative", "not_duplicate"):
        return Response.json({"ok": False, "error": "Неизвестное решение"}, 400)
    try:
        ids = [int(x) for x in (body.get("ids") or [])[:2]]
    except (TypeError, ValueError):
        ids = []
    if len(set(ids)) != 2:
        return Response.json({"ok": False, "error": "Нужна пара картинок"}, 400)
    rows = _live_rows(req.settings, ids)
    if len(rows) != 2:
        return Response.json({"ok": False, "error": "Одной из картинок уже нет в библиотеке — обновите страницу"}, 409)
    busy = library_busy() if kind == "alternative" else ""     # a сборка is not written while «Обслуживание» works
    if busy:                                                    # (the gallery's «Собрать в одну сборку» waits too)
        return Response.json({"ok": False, "error": busy}, 409)
    done = remember_duplicate_relation(req.settings, [{"image_id": i, "md5": rows[i]["hash_md5"]} for i in ids], relation=kind)
    answer = {"ok": True, "remembered": int(done or 0)}
    if kind == "alternative":
        answer.update(_together(req.settings, ids))
    return Response.json(answer)


def _together(settings: dict, ids: list) -> dict:
    """(v5.44.116) «Альтернативная версия»: the two cards in one сборка — by ``card_sets.gather``, as «Собрать в одну
    сборку» of the gallery does (``post_sets.gather``): a card of a сборка brings its whole сборка; when one of them is
    in a сборка already, the other joins it (its name and number stay — as «+ добавить карточки» of a сборка); both
    in one already — nothing to do.  {"set": id, "cards": n, "joined": bool} or {"set_error": why} (the pair is
    remembered all the same)."""
    from core import card_sets
    sets = [card_sets.set_id_of(settings, image_id) for image_id in ids]
    target = sets[0] or sets[1]
    try:
        if target and sets[0] == sets[1]:
            found = card_sets.describe_set(settings, target) or {}
            return {"set": target, "cards": int(found.get("count") or 0), "joined": False}
        others = [image_id for image_id, set_id in zip(ids, sets) if set_id != target]
        result = card_sets.gather(settings, others, into=target) if target else card_sets.gather(settings, ids)
    except card_sets.SetError as exc:
        return {"set_error": str(exc)}
    return {"set": int(result["set"]), "cards": int(result["cards"]), "joined": True}
