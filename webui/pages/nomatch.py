"""Брак in the new interface: why a card is there (the parser's reason, the source a reverse search saw, «фото или
рисунок» of the photo/drawing model), «Получить теги по ссылке» on its page (``core/nomatch_manual``: a page task,
one at a time), and the filter of the section «Брак» by reason and kind (the search words ``брак:…`` —
``webui/data.py``; the links with their counts are made here)."""
from __future__ import annotations

import logging
from pathlib import Path

from core.external_browser import is_web_url
from webui.html import esc, url
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.webui")
KIND = "nomatch"
BRAK_BUCKETS = ("no_match", "downloaded_no_match")
LENS = "https://lens.google.com/"

# the search words of the section «Брак» (webui/data.py turns them into conditions): word -> the link's text
FILTERS = (("брак:ненайдено", "не найдено"), ("брак:источник", "источник без тегов"),
           ("брак:фото", "фото"), ("брак:рисунок", "рисунок"), ("брак:неясно", "вид не определён"))


def register(router) -> None:
    router.add("POST", r"/api/nomatch/url", nomatch_url)
    router.add("GET", r"/api/nomatch/state", nomatch_state)
    router.add("POST", r"/api/nomatch/kind", nomatch_kind)


# ── the box on the page of a Брак card ─────────────────────────────────────
def _percent(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return ""
    return f"{number:.0f} %" if number > 0 else ""


def _kind_line(entry: dict) -> str:
    status = str(entry.get("visual_status") or "").strip().lower()
    if str(entry.get("visual_model") or "") == "manual" and status in ("real", "booru"):
        return "Отмечено вами: " + ("фото" if status == "real" else "рисунок")
    confidence = _percent(float(entry.get("visual_confidence") or 0) * 100)
    if status == "real":
        return f"Похоже на фото{f' ({confidence})' if confidence else ''}"
    if status == "booru":
        return f"Похоже на рисунок{f' ({confidence})' if confidence else ''}"
    if status == "unknown" and not str(entry.get("visual_model") or "").endswith(":error"):
        return "Фото это или рисунок — модель не уверена"
    if status == "unreadable":
        return "Фото это или рисунок — не определить: файл не открывается как картинка"
    return ""


def _kind_problem(settings: dict) -> str:
    """Why «фото или рисунок» is not answered now ("" — the model can answer)."""
    if not bool(settings.get("visual_nomatch_classify_enabled", True)):
        return "проверка выключена"
    try:
        from core.visual_work import problem
        return problem(settings)
    except Exception as exc:  # the page is shown anyway
        _LOG.warning("visual model state: %s", exc)
        return ""


def _kind_buttons(image_id: int, entry: dict) -> str:
    """«Это фото» / «Это рисунок»: the person's mark, stronger than the model's answer."""
    marked = str(entry.get("visual_status") or "") if str(entry.get("visual_model") or "") == "manual" else ""
    buttons = "".join(
        f'<button type="button" data-action="brak-kind" data-id="{image_id}" data-kind="{kind}" '
        f'aria-pressed="{"true" if marked == kind else "false"}">{label}</button>'
        for kind, label in (("real", "Это фото"), ("booru", "Это рисунок")))
    if marked:
        buttons += (f'<button type="button" class="link" data-action="brak-kind" data-id="{image_id}" data-kind="">'
                    'снять отметку</button>')
    return f'<div class="brak-kind" role="group" aria-label="Фото это или рисунок">{buttons}</div>'


def reasons_html(settings: dict, item: dict) -> tuple[str, str]:
    """(why the card is in Брак, in words; the address given for it before)."""
    from core.nomatch_db import entry_for_path
    try:
        entry = entry_for_path(settings, str(item.get("path") or "")) or {}
    except Exception as exc:  # an old base without the Брак table: the box still offers the link
        _LOG.warning("Брак entry of card %s not read: %s", item.get("id"), exc)
        entry = {}
    lines = []
    if str(entry.get("reason") or "") == "source_only":
        lines.append("<p>Обратный поиск нашёл, откуда картинка, но это не booru-сайт — тегов оттуда не взять.</p>")
        label = str(entry.get("source_label") or entry.get("source_host") or "источник")
        source = str(entry.get("source_url") or "")
        shown = (f'<a href="{esc(source)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'
                 if is_web_url(source) else esc(label))
        similarity = _percent(entry.get("source_similarity"))
        lines.append(f'<p>Источник: {shown}{f", сходство {similarity}" if similarity else ""}</p>')
    elif entry:
        lines.append("<p>Ни один сайт не знает эту картинку: её не нашли ни по MD5, ни обратным поиском.</p>")
    else:
        lines.append("<p>Тегов у файла нет: его не нашли ни на одном сайте.</p>")
    kind = _kind_line(entry)
    if kind:
        lines.append(f'<p class="muted">{esc(kind)}</p>')
    else:
        why = _kind_problem(settings)
        if why:
            lines.append(f'<p class="muted">Фото это или рисунок — не определено: {esc(why)} '
                         '(<a href="/settings#set-visual">Настройки</a>)</p>')
    lines.append(_kind_buttons(int(item["id"]), entry))
    if entry.get("last_error"):
        from core.redaction import sanitize_text
        lines.append(f'<p class="muted">Последняя ошибка поиска: {esc(sanitize_text(str(entry["last_error"]))[:300])}</p>')
    manual = str(entry.get("manual_url") or "")
    if manual:
        shown = (f'<a href="{esc(manual)}" target="_blank" rel="noopener noreferrer">{esc(manual)}</a>'
                 if is_web_url(manual) else esc(manual))
        lines.append(f'<p class="muted">Ссылка, которую указывали раньше: {shown}</p>')
    original = str(entry.get("original_path") or "")
    card_file = Path(str(item.get("path") or ""))
    if original and original != str(card_file):
        gone = "" if Path(original).is_file() else " — на месте его уже нет"
        lines.append(f'<p class="muted">Исходный файл: <code class="tool-path">{esc(original)}</code>{gone}</p>')
        if not card_file.is_file() and not gone:
            lines.append('<p class="muted">Копии этой карточки в библиотеке нет, но исходный файл на месте — '
                         'теги по ссылке и «Искать снова» возьмут его.</p>')
    return "".join(lines), str(entry.get("manual_url") or "")


def why_lines(settings: dict, item: dict) -> list:
    """(v5.44.118) Why the card is in Брак, as plain lines — the phone's card says it (:func:`reasons_html` without
    the computer's buttons and links)."""
    from core.nomatch_db import entry_for_path
    try:
        entry = entry_for_path(settings, str(item.get("path") or "")) or {}
    except Exception as exc:  # an old base without the Брак table
        _LOG.warning("Брак entry of card %s not read: %s", item.get("id"), exc)
        entry = {}
    if str(entry.get("reason") or "") == "source_only":
        label = str(entry.get("source_label") or entry.get("source_host") or "источник")
        lines = ["Обратный поиск нашёл, откуда картинка, но это не booru-сайт — тегов оттуда не взять.",
                 f"Источник: {label}"]
    elif entry:
        lines = ["Ни один сайт не знает эту картинку: её не нашли ни по MD5, ни обратным поиском."]
    else:
        lines = ["Тегов у файла нет: его не нашли ни на одном сайте."]
    kind = _kind_line(entry)
    if kind:
        lines.append(kind)
    return lines


def kinds_of(settings: dict, paths) -> dict:
    """(v5.44.118) {path of a Брак card: «фото» / «рисунок»} — what the model (or the person) said of it, for the
    corner of the phone's tile; a card without an answer — none."""
    from core.database.connection import db
    wanted = [str(p) for p in paths or [] if p]
    out: dict = {}
    if not wanted:
        return out
    try:
        with db(settings, readonly=True) as con:
            for start in range(0, len(wanted), 400):
                part = wanted[start:start + 400]
                inside = set(part)
                marks = ",".join("?" for _ in part)
                # the card's path is the copy in Брак or (a file downloaded into Брак) the file itself — as the words
                # «брак:…» find them (``data.brak_condition``)
                for row in con.execute(f"SELECT media_path, original_path, visual_status FROM no_match_items "
                                       f"WHERE active=1 AND (media_path IN ({marks}) OR original_path IN ({marks}))",
                                       part + part):
                    word = {"real": "фото", "booru": "рисунок"}.get(str(row[2] or "").lower(), "")
                    for path in (str(row[0] or ""), str(row[1] or "")):
                        if word and path in inside:
                            out.setdefault(path, word)
    except Exception as exc:  # the corners are a convenience: the tiles are shown without them
        _LOG.warning("Брак kinds of tiles: %s", exc)
    return out


def box(settings: dict, item: dict) -> str:
    """The sidebar box «Брак» of a card's page ("" for a card that is not in Брак)."""
    if int(item.get("deleted") or 0) or str(item.get("bucket") or "") not in BRAK_BUCKETS:
        return ""
    image_id = int(item["id"])
    reasons, manual = reasons_html(settings, item)
    return f"""<section class="box brak" id="brak" data-id="{image_id}">
  <h3>Почему в Браке</h3>
  {reasons}
  <form class="brak-url" data-form="nomatch-url" data-id="{image_id}">
    <label for="brak-url">Ссылка на пост с этой картинкой</label>
    <input id="brak-url" type="text" inputmode="url" name="url" value="{esc(manual)}" spellcheck="false"
      autocomplete="off" placeholder="https://danbooru.donmai.us/posts/123">
    <button type="submit" class="primary">Получить теги по ссылке</button>
    <p class="action-status" role="status"></p>
  </form>
  <p class="hint">Найдите картинку на booru-сайте — например, через <a href="{LENS}" target="_blank"
    rel="noopener noreferrer">Google Lens</a> (файл туда загружается вручную: «Показать в папке») — и вставьте ссылку
    на её пост. Подходит любой сайт из «Сайтов», даже выключенный.</p>
  <div class="job" data-job hidden>
    <div class="job-head"><b class="job-title"></b> <span class="job-status"></span></div>
    <pre class="job-log" tabindex="0" aria-label="Журнал"></pre>
  </div>
</section>"""


# ── the filter of the section «Брак» ───────────────────────────────────────
def counts(settings: dict) -> dict:
    """The number of Брак cards under each filter word, and all of them ("")."""
    from core.database.connection import db
    from webui import data
    out = {}
    base = "SELECT COUNT(*) FROM images i WHERE i.deleted=0 AND i.bucket IN ('no_match','downloaded_no_match')"
    try:
        with db(settings, readonly=True) as con:
            out[""] = int(con.execute(base).fetchone()[0])
            for word, _label in FILTERS:
                where, params = data.brak_condition(word)
                out[word] = int(con.execute(f"{base} AND {where}", params).fetchone()[0])
    except Exception as exc:  # the links are a convenience: the grid is shown without them
        _LOG.warning("Брак filter counts: %s", exc)
        return {}
    return out


def filter_bar(settings: dict, ctx: dict) -> str:
    """«Показать: все · не найдено · источник без тегов │ фото · рисунок · вид не определён» with their numbers."""
    numbers = counts(settings)
    if not numbers:
        return ""
    words = ctx["tags"].split()
    chosen = next((w.lower() for w in words if w.lower().startswith("брак:")), "")
    rest = " ".join(w for w in words if not w.lower().startswith("брак:"))

    def link(word: str, label: str) -> str:
        query = " ".join(x for x in (rest, word) if x)
        href = url("/posts", tags=query, section=ctx["section"], source=ctx["source"], order=ctx["order"])
        current = ' aria-current="true" class="active"' if word == chosen else ""
        return f'<a href="{esc(href)}"{current}>{esc(label)} <span class="muted">{numbers.get(word, 0)}</span></a>'

    reasons = [link("", "все"), *(link(w, t) for w, t in FILTERS[:2])]
    kinds = [link(w, t) for w, t in FILTERS[2:]]
    why = _kind_problem(settings)
    note = (f'<span class="muted">фото или рисунок не определяется: {esc(why)} — '
            f'<a href="/settings#set-visual">Настройки</a></span>' if why else "")
    return (f'<nav class="brak-filters" aria-label="Какой Брак показать">Показать: {" · ".join(reasons)}'
            f'<span class="sep" aria-hidden="true">│</span>{" · ".join(kinds)}{note}</nav>')


# ── the API ────────────────────────────────────────────────────────────────
def nomatch_url(req: Request) -> Response:
    """«Получить теги по ссылке» for a Брак card: checked at once, the site is asked in a page task."""
    from core import nomatch_manual, page_jobs
    from webui import data
    body = req.json()
    try:
        image_id = int(body.get("id") or 0)
    except (TypeError, ValueError):
        image_id = 0
    if image_id <= 0:
        return Response.json({"ok": False, "error": "Не указана карточка"}, 400)
    settings = dict(req.settings)
    try:
        request = nomatch_manual.check(settings, image_id, str(body.get("url") or ""))
    except nomatch_manual.ManualUrlError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 400)
    busy = nomatch_manual.library_busy()
    if busy:
        return Response.json({"ok": False, "error": busy}, 409)

    def run(progress, stop_check):
        progress(f"Ссылка: {request.url}")
        try:
            result = nomatch_manual.apply(settings, request, progress=progress, stop_check=stop_check)
        except nomatch_manual.ManualUrlError as exc:
            return {"failed": str(exc)}
        if result.get("ok"):
            data.TAG_COUNTS.invalidate()
        return result
    try:
        task = page_jobs.start(KIND, f"Теги по ссылке для карточки №{image_id}", run, about=image_id)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца."}, 409)
    return Response.json({"ok": True, "id": task.id})


def nomatch_state(req: Request) -> Response:
    """The task of «Получить теги по ссылке» — of this card only (``card``): a page of another card shows nothing."""
    from core import page_jobs
    task = page_jobs.current(KIND)
    card = req.int_arg("card", 0, lo=0)
    if task is None or (card and task.about != card):
        return Response.json({"ok": True, "status": "idle"})
    snap = task.snapshot(after=req.int_arg("after", 0, lo=0))
    result = task.result if task.status == "done" and isinstance(task.result, dict) else {}
    snap["found"] = int(result.get("image_id") or 0) if result.get("ok") else 0
    return Response.json({"ok": True, **snap})


def nomatch_kind(req: Request) -> Response:
    """The person's mark on a Брак card: «real» (фото), «booru» (рисунок), "" — the mark taken off."""
    from core.database.connection import db
    from core.nomatch_db import entry_for_path, set_manual_visual, upsert_nomatch
    from core.nomatch_manual import library_busy
    body = req.json()
    kind = str(body.get("kind") or "")
    try:
        image_id = int(body.get("id") or 0)
    except (TypeError, ValueError):
        image_id = 0
    if kind not in ("real", "booru", "") or image_id <= 0:
        return Response.json({"ok": False, "error": "Не та отметка"}, 400)
    busy = library_busy()
    if busy:
        return Response.json({"ok": False, "error": busy}, 409)
    settings = req.settings
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT path, bucket, deleted FROM images WHERE id=?", (image_id,)).fetchone()
    if row is None or int(row["deleted"] or 0) or str(row["bucket"] or "") not in BRAK_BUCKETS:
        return Response.json({"ok": False, "error": "Эта карточка не в Браке"}, 400)
    path = str(row["path"] or "")
    entry = entry_for_path(settings, path)
    if entry is None and kind:          # a Брак card of an old download without an entry: it gets one
        upsert_nomatch(path, settings=settings, media_path=path)
        entry = entry_for_path(settings, path)
    key = str((entry or {}).get("original_path") or path)
    changed = set_manual_visual(key, kind or None, settings=settings)
    if kind and not changed:
        return Response.json({"ok": False, "error": "Отметка не записана — у карточки нет записи Брака"}, 409)
    message = {"real": "Отмечено: фото", "booru": "Отмечено: рисунок"}.get(kind, "Отметка снята — решит модель")
    return Response.json({"ok": True, "message": message})
