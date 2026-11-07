"""The page «Диагностика»: a check of the library that only reads (``core.library_diagnostics.audit_library``)
— what needs attention, with its fix next to it, and the numbers behind it — and the repairs, each a
separate button: a copy of the base, merging exact copies, lifting MD5 blocks of pictures that are back in
the library, SQLite indexes, stale tag-category jobs, old logs cleaned of keys.

The check and the repairs are page tasks of one kind (``core/page_jobs``): one at a time, with a journal
and «Остановить».  The last report and the last repairs are kept on disk (``library_diagnostics``), so the
page shows them after a restart too.

(v5.44.118, the sketch E) «Файлы библиотеки» on top (:func:`_files_panel`): one check of the files — «Проверить файлы»
— and one repair — «Починить файлы (N)», which does by itself, in order, what the check showed and nothing else
(``core/file_check.py``); the five checks and four repairs of the files of before («Проверить библиотеку» с наличием
файлов, «Проверить целостность», «Проверить наличие файлов», «Проверить MD5 по байтам», «Найти файлы после переноса
архива»; «Починить безопасное», «Восстановить пропавшие и испорченные», «Переписать пути», «Достроить превью») are in
these two."""
from __future__ import annotations

import json
import time
from pathlib import Path

from webui.html import esc, human_size, page_frame, url
from webui.server import Request, Response

KIND = "diag"
SAMPLES_SHOWN = 30


def register(router) -> None:
    router.add("GET", r"/diag", diag_page)
    router.add("GET", r"/api/diag/state", diag_state)
    router.add("GET", r"/api/diag/report\.json", diag_report_json)
    router.add("POST", r"/api/diag/audit", diag_audit)
    router.add("POST", r"/api/diag/stop", diag_stop)
    router.add("POST", r"/api/diag/repair", diag_repair)
    router.add("POST", r"/api/diag/sources", diag_sources)
    router.add("GET", r"/api/diag/sources\.json", diag_sources_json)
    router.add("POST", r"/api/diag/files/check", files_check)          # (v5.44.118) «Проверить файлы»
    router.add("POST", r"/api/diag/files/repair", files_repair)        # «Починить файлы»


def _prefs() -> dict:
    from webui import prefs
    stored = prefs.load()
    return {"thumbs": stored.get("thumbs", "m")}         # the look is the page's own (page_frame)


# ── small formatting ───────────────────────────────────────────────────────
def _n(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return esc(value)


def _when(ts) -> str:
    try:
        ts = float(ts or 0)
    except (TypeError, ValueError):
        return ""
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts)) if ts > 0 else ""


def _stamp(value) -> str:
    """A time the base keeps either as seconds or as text."""
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.replace(".", "", 1).isdigit()):
        return _when(value)
    return str(value or "")


def _md5_link(md5) -> str:
    value = esc(md5)
    return f'<a href="{esc(url("/posts", tags="md5:" + str(md5)))}" title="Показать в галерее"><code>{value}</code></a>'


RECENT_DAYS = 7


def _seconds(value) -> float:
    """A moment kept as seconds or as "YYYY-MM-DD HH:MM:SS" text; 0 when it is neither."""
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return time.mktime(time.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return 0.0


def _took(seconds) -> str:
    sec = int(round(float(seconds or 0)))
    if sec < 60:
        return f"{sec} с"
    minutes, sec = divmod(sec, 60)
    if minutes < 60:
        return f"{minutes} мин {sec} с"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин"


def _part(report: dict, name: str) -> dict:
    value = report.get(name)
    return value if isinstance(value, dict) else {}


def _rows(value) -> list:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _numbers(items: list) -> str:
    """Key numbers: (label, value, note, bad)."""
    cells = []
    for label, value, note, bad in items:
        small = f" <small>{esc(note)}</small>" if note else ""
        mark = ' class="bad"' if bad else ""
        cells.append(f"<div{mark}><dt>{esc(label)}</dt><dd>{value}{small}</dd></div>")
    return f'<dl class="diag-numbers">{"".join(cells)}</dl>'


def _table(headers: list, rows: list, *, empty: str = "") -> str:
    if not rows:
        return f'<p class="hint">{esc(empty)}</p>' if empty else ""
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<table class="diag-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _card_link(row: dict) -> str:
    image_id = row.get("id")
    name = row.get("file_name") or Path(str(row.get("path") or "")).name or "?"
    title = esc(row.get("path") or "")
    if image_id:
        return f'<a href="/post/{int(image_id)}" title="{title}">{esc(name)}</a>'
    return f'<span title="{title}">{esc(name)}</span>'


# ── the repairs ────────────────────────────────────────────────────────────
def _lines_backup(settings: dict, progress, stop_check) -> list:
    from core.library_diagnostics import create_forced_backup
    progress("Копирую базу…")
    path = create_forced_backup(settings)
    if not path:
        raise RuntimeError("Копия базы не создана: базы нет или диск не дал записать")
    return [f"Копия базы: {path}"]


def _lines_merge(settings: dict, progress, stop_check) -> dict:
    from core.library_lifecycle import cleanup_live_exact_duplicates
    from webui import data
    from webui.server import FILE_STREAMS
    what = {"hash": "Считаю MD5 файлов, у которых его нет", "verify": "Сверяю байты копий с MD5 в базе"}

    def stage(name, done, total):
        if done == total or done % 500 == 0:
            progress(f"{what.get(name, name)}: {done} из {total}")

    progress("Копирую базу и ищу точные копии…")
    result = cleanup_live_exact_duplicates(settings, make_backup=True, progress=stage, cancel_check=stop_check,
                                           hold=lambda paths: FILE_STREAMS.holding(*paths))
    data.TAG_COUNTS.invalidate()
    result = dict(result or {})
    if result.get("error"):
        raise RuntimeError(result["error"])
    lines = [f"Копия базы: {result.get('backup') or '—'}"]
    if result.get("cancelled"):
        return {"stopped": True, "lines": lines + ["Остановлено до склейки: ничего не склеено и не перенесено",
                                                   f"MD5 посчитан у файлов: {int(result.get('hashed_missing') or 0)}"]}
    lines += [
        f"Групп точных копий: {int(result.get('groups') or 0)}",
        f"Лишних копий в корзине: {int(result.get('trashed_records') or 0)}",
        f"Их теги и источники переданы оставленным: {int(result.get('merged_existing') or 0)}",
        f"Снято запретов MD5 (картинка осталась в библиотеке): {int(result.get('unblocked_md5') or 0)}",
        f"MD5 посчитан у файлов без него: {int(result.get('hashed_missing') or 0)}",
        f"Ошибок: {int(result.get('errors') or 0)}",
    ]
    if result.get("skipped_bytes_differ"):
        lines.append(f"Не тронуты — байты файла не те, что записаны в базе: {int(result['skipped_bytes_differ'])} "
                     "(MD5 в базе от старых версий; файлы — в логе программы)")
    if result.get("skipped_same_file"):
        lines.append(f"Не тронуты — один и тот же файл под двумя записями: {int(result['skipped_same_file'])}")
    return {"stopped": False, "lines": lines}


def _lines_unblock(settings: dict, progress, stop_check) -> list:
    from core.library_diagnostics import clear_obsolete_live_md5_blocks
    progress("Копирую базу и снимаю запреты…")
    result = dict(clear_obsolete_live_md5_blocks(settings) or {})
    if result.get("error"):
        raise RuntimeError(result["error"])
    lines = [f"Копия базы: {result.get('backup') or '—'}", f"Снято запретов: {int(result.get('removed') or 0)}",
             f"Осталось запретов «не скачивать снова»: {int(result.get('remaining') or 0)}"]
    if result.get("legacy_error"):
        lines.append(f"Старый список удалённых (deleted_files_ignore.json) не почищен: {result['legacy_error']}")
    return lines


def _lines_indices(settings: dict, progress, stop_check) -> list:
    from core.library_diagnostics import restore_critical_indices
    progress("Копирую базу и проверяю индексы…")
    result = dict(restore_critical_indices(settings) or {})
    if result.get("error"):
        raise RuntimeError(result["error"])
    restored = ", ".join(result.get("restored") or []) or "не понадобилось"
    left = ", ".join(result.get("missing_after") or []) or "нет"
    return [f"Копия базы: {result.get('backup') or '—'}", f"Восстановлены индексы: {restored}",
            f"Всё ещё нет: {left}"]


def _lines_requeue(settings: dict, progress, stop_check) -> list:
    from core.library_diagnostics import requeue_stale_tag_enrichments
    progress("Копирую базу и возвращаю задания в очередь…")
    result = dict(requeue_stale_tag_enrichments(settings) or {})
    if result.get("error"):
        raise RuntimeError(result["error"])
    return [f"Копия базы: {result.get('backup') or '—'}",
            f"Возвращено в очередь заданий раскладки тегов: {int(result.get('requeued') or 0)}"]


def _lines_logs(settings: dict, progress, stop_check) -> list:
    from core.paths import LOGS_DIR
    from core.redaction import sanitize_log_directory
    progress("Убираю ключи, логины и cookie из старых логов…")
    result = dict(sanitize_log_directory(LOGS_DIR) or {})
    return [f"Просмотрено файлов логов: {int(result.get('checked') or 0)}",
            f"Переписано без ключей: {int(result.get('changed') or 0)}",
            "Ключи, которые уже попадали в логи, лучше выпустить на сайтах заново."]


# action -> (button, journal title, the question of the second press or "", the work)
REPAIRS = {
    "backup": ("Сделать копию базы", "Копия базы", "", _lines_backup),
    "merge_md5": ("Склеить точные копии", "Склейка точных копий",
                  "Лишние копии уйдут в корзину — нажмите ещё раз", _lines_merge),
    "unblock_md5": ("Снять устаревшие запреты", "Снятие устаревших запретов MD5", "", _lines_unblock),
    "indices": ("Восстановить индексы", "Восстановление индексов SQLite", "", _lines_indices),
    "requeue_categories": ("Вернуть в очередь", "Возврат заданий раскладки тегов", "", _lines_requeue),
    "redact_logs": ("Обезличить старые логи", "Обезличивание старых логов",
                    "Логи перепишутся без ключей — нажмите ещё раз", _lines_logs),
}
DANGER = {"merge_md5", "redact_logs"}
STOPPABLE = {"merge_md5"}            # the others have no place to stop half-way


def _busy() -> bool:
    from core import page_jobs
    task = page_jobs.current(KIND)
    return task is not None and task.running


def _can_stop() -> bool:
    """A task is going and it can be stopped half-way (for «Остановить» as the page opens)."""
    from core import page_jobs
    task = page_jobs.current(KIND)
    return task is not None and task.running and task.stoppable


def _button(action: str, busy: bool) -> str:
    text, _title, ask, _fn = REPAIRS[action]
    cls = ' class="danger"' if action in DANGER else ""
    question = f' data-ask="{esc(ask)}"' if ask else ""
    return (f'<button type="button"{cls} data-action="diag-repair" data-repair="{action}" data-needs-idle'
            f'{question}{" disabled" if busy else ""}>{esc(text)}</button>')


# ── what needs attention ───────────────────────────────────────────────────
def _attention(report: dict, busy: bool) -> list:
    """(level, text, the fix: a button or a link) — "bad": something is wrong, "note": worth a look."""
    out: list = []
    database, library, md5 = _part(report, "database"), _part(report, "library"), _part(report, "md5")
    integrity, queues, errors = _part(report, "file_integrity"), _part(report, "queues"), _part(report, "errors")
    if report.get("cancelled"):
        out.append(("note", "Проверка была остановлена — часть чисел неполная.", ""))
    quick = str(database.get("quick_check") or "")
    if database.get("exists") is False:
        out.append(("bad", "Файл базы не найден — проверка ничего не создавала.", ""))
    elif quick and quick.lower() != "ok":
        out.append(("bad", f"Проверка SQLite нашла повреждение базы ({quick}). Сделайте копию и не чините ничего, "
                           "пока не выяснено, что случилось.", _button("backup", busy)))
    if database.get("write_blocked_reason"):
        out.append(("bad", f"Запись в базу выключена: {database['write_blocked_reason']}", ""))
    missing_idx = list(_part(report, "indices").get("missing") or [])
    if missing_idx:
        out.append(("bad", f"Нет индексов SQLite: {len(missing_idx)} — поиск и запись медленнее.", _button("indices", busy)))
    if int(md5.get("redundant_rows") or 0):
        out.append(("bad", f"Точные копии: групп {_n(md5.get('duplicate_groups'))}, лишних копий "
                           f"{_n(md5.get('redundant_rows'))}. Одна картинка — одна карточка: лишние уйдут в корзину, "
                           "их теги и источники — оставленной.", _button("merge_md5", busy)))
    if int(md5.get("obsolete_live_blocks") or 0):
        out.append(("note", f"Запретов «не скачивать снова» для картинок, которые снова в библиотеке: "
                            f"{_n(md5.get('obsolete_live_blocks'))}.", _button("unblock_md5", busy)))
    stale = sum(int(r.get("files") or 0) for r in _rows(queues.get("tag_enrichment")) if r.get("status") == "stale")
    if stale:
        out.append(("note", f"Устаревших заданий раскладки тегов по категориям: {_n(stale)}.",
                    _button("requeue_categories", busy)))
    if library.get("missing_on_disk"):
        out.append(("bad", f"Файлов нет на диске: {_n(library['missing_on_disk'])}.",
                    '<a href="#diag-files">Какие</a>'))
    if library.get("offline_root_on_disk"):
        out.append(("note", f"Диск с частью файлов не подключён: {_n(library['offline_root_on_disk'])} файлов.",
                    '<a href="#diag-files">Какие</a>'))
    if int(integrity.get("hash_mismatch") or 0):
        out.append(("bad", f"Байты файла не совпадают с MD5 в базе: {_n(integrity['hash_mismatch'])} — файл менялся "
                           "или повреждён.", ""))
    if int(_part(report, "filename_collisions").get("duplicate_live_paths") or 0):
        out.append(("bad", f"Один и тот же файл записан в базе дважды: "
                           f"{_n(report['filename_collisions']['duplicate_live_paths'])}.", ""))
    free = int((_part(report, "storage").get("database_disk") or {}).get("free_bytes") or 0)
    if 0 < free < 5 * 1024 ** 3:
        out.append(("bad", f"На диске с базой осталось {human_size(free)}.", ""))
    pairs = int(_part(report, "duplicates").get("visual_candidates") or 0)
    if pairs:
        out.append(("note", f"Пар похожих картинок ждут решения: {_n(pairs)}.", '<a class="button" href="/dupes">Открыть «Дубли»</a>'))
    # the crash record and the blocked attempts are kept forever: only recent ones need attention
    since = float(report.get("finished_at") or time.time()) - RECENT_DAYS * 86400
    crash = errors.get("last_crash") if isinstance(errors.get("last_crash"), dict) else {}
    if crash and _seconds(crash.get("created_at")) >= since:
        out.append(("bad", f"Программа падала: {crash.get('created_at')}.", '<a href="#diag-errors">Подробности</a>'))
    unfinished = len(_rows(queues.get("unfinished_operations")))
    if unfinished:
        out.append(("note", f"Незавершённых операций в журнале базы: {unfinished}.", '<a href="#diag-queues">Какие</a>'))
    blocked = sum(1 for e in _rows(_part(report, "source_protection").get("blocked_events")) if _seconds(e.get("at")) >= since)
    if blocked:
        out.append(("note", f"Остановлено попыток изменить исходный архив за {RECENT_DAYS} дней: {blocked} — архив цел, "
                            "но что-то пыталось.", '<a href="#diag-protect">Какие</a>'))
    return out


# ── the sections of the report ─────────────────────────────────────────────
def _section(anchor: str, title: str, lead: str, inner: str) -> str:
    lead_html = f'<p class="hint">{lead}</p>' if lead else ""
    return f'<section class="diag-block" id="diag-{anchor}"><h2>{esc(title)}</h2>{lead_html}{inner}</section>'


def _library_html(report: dict) -> str:
    lib, integrity = _part(report, "library"), _part(report, "file_integrity")
    thumbs = _part(report, "storage").get("thumbnail_cache") or {}
    numbers = _numbers([
        ("Картинок и видео", _n(lib.get("live_files")), human_size(int(lib.get("live_bytes") or 0)), False),
        ("В корзине", _n(lib.get("trash_files")), human_size(int(lib.get("trash_bytes") or 0)), False),
        ("Без источника", _n(lib.get("without_source")), "", False),
        ("Без тегов", _n(lib.get("without_tags")), "", False),
        ("С несколькими источниками", _n(lib.get("multi_source_files")), "", False),
        ("Тегов", _n(lib.get("tag_count")), "", False),
        ("Источников", _n(lib.get("source_count")), "", False),
        ("Превью в кэше", _n(thumbs.get("files")), human_size(int(thumbs.get("size_bytes") or 0)), False),
    ])
    known = int(integrity.get("total") or 0) - int(integrity.get("unknown") or 0)
    files = ""
    if integrity and not integrity.get("error"):
        files = (f'<p class="hint">По прошлым проверкам файлов (их делает сама программа): на месте '
                 f'{_n(integrity.get("present"))}, нет на диске {_n(integrity.get("missing"))}, диск не подключён '
                 f'{_n(integrity.get("offline_root"))}, путь не читается {_n(integrity.get("path_error"))}; '
                 f'не проверялись {_n(integrity.get("unknown"))} из {_n(integrity.get("total"))} '
                 f'(проверено {_n(known)}).</p>')
    return _section("library", "Библиотека", "", numbers + files)


def _md5_html(report: dict, busy: bool) -> str:
    md5, samples = _part(report, "md5"), _part(report, "samples")
    numbers = _numbers([
        ("Групп точных копий", _n(md5.get("duplicate_groups")), "", bool(md5.get("duplicate_groups"))),
        ("Лишних копий", _n(md5.get("redundant_rows")), "", bool(md5.get("redundant_rows"))),
        ("Без MD5", _n(md5.get("live_without_md5")), "посчитается при склейке", False),
        ("Запретов MD5", _n(md5.get("active_manual_blocks")), "«не скачивать снова»", False),
        ("Из них устаревших", _n(md5.get("obsolete_live_blocks")), "картинка снова в библиотеке",
         bool(md5.get("obsolete_live_blocks"))),
    ])
    groups = [[_md5_link(r.get("md5")), _n(r.get("files")), esc(human_size(int(r.get("bytes") or 0)))]
              for r in _rows(samples.get("duplicate_md5"))]
    table = (f'<details><summary>Группы — первые {len(groups)}</summary>'
             f'{_table(["MD5", "Копий", "Вместе"], groups)}</details>') if groups else ""
    obsolete = [x for x in samples.get("obsolete_live_md5") or [] if isinstance(x, str)]
    obsolete_html = (f'<details><summary>Устаревшие запреты — первые {len(obsolete)}</summary>'
                     f'<p class="diag-codes">{" ".join(_md5_link(x) for x in obsolete)}</p></details>') if obsolete else ""
    actions = f'<div class="tool-actions">{_button("merge_md5", busy)}{_button("unblock_md5", busy)}</div>'
    lead = ("Одна картинка (одни и те же байты) — одна карточка. «Склеить» оставляет лучшую копию, остальные уходят в "
            "корзину, а их теги, источники и избранное переходят к оставленной; перед этим — копия базы. "
            "«Снять запреты» разрешает снова скачивать то, что удалено, но уже опять есть в библиотеке.")
    return _section("md5", "Точные копии", esc(lead), numbers + actions + table + obsolete_html)


def _similar_html(report: dict) -> str:
    dup = _part(report, "duplicates")
    scan = dup.get("latest_scan") if isinstance(dup.get("latest_scan"), dict) else {}
    if scan:
        status = {"completed": "закончен", "running": "шёл или был прерван", "cancelled": "остановлен",
                  "failed": "прерван ошибкой"}.get(str(scan.get("status")), str(scan.get("status") or "?"))
        last = (f'Последний поиск: {esc(_when(scan.get("started_at")))}, порог {_n(scan.get("threshold_percent"))}%, '
                f'{esc(status)}; картинок {_n(scan.get("total_images"))}, сохранено пар {_n(scan.get("stored_pairs"))}.')
    else:
        last = "Поиска похожих ещё не было."
    inner = (f'<p>Пар на решение: <b>{_n(dup.get("visual_candidates"))}</b>, решено раньше: '
             f'{_n(dup.get("reviewed_relations"))}. {last} <a href="/dupes">Открыть «Дубли»</a></p>')
    return _section("similar", "Похожие картинки", "", inner)


def _database_html(report: dict, busy: bool) -> str:
    database = _part(report, "database")
    disk = _part(report, "storage").get("database_disk") or {}
    names = _part(report, "filename_collisions")
    quick = str(database.get("quick_check") or "?")
    numbers = _numbers([
        ("Размер", esc(human_size(int(database.get("size_bytes") or 0))),
         f'журнал WAL {human_size(int(database.get("wal_bytes") or 0))}', False),
        ("Проверка SQLite", esc(quick), "", quick.lower() != "ok"),
        ("Версия схемы", _n(database.get("schema_version")), "", False),
        ("Можно освободить", esc(human_size(int(database.get("reclaimable_bytes") or 0))), "сжатием (VACUUM)", False),
        ("Свободно на диске", esc(human_size(int(disk.get("free_bytes") or 0))) if disk else "—",
         f'из {human_size(int(disk.get("total_bytes") or 0))}' if disk else "", False),
        ("Индексы", "в порядке" if _part(report, "indices").get("ok", True) else
         f'нет {len(_part(report, "indices").get("missing") or [])}', "", not _part(report, "indices").get("ok", True)),
    ])
    path = f'<p class="hint">Файл: <span class="tool-path">{esc(database.get("path") or "")}</span></p>'
    write = (f'<p class="tool-warn">Запись выключена: {esc(database["write_blocked_reason"])}</p>'
             if database.get("write_blocked_reason") else "")
    missing = list(_part(report, "indices").get("missing") or [])
    missing_html = (f'<p class="tool-warn">Нет индексов: {esc(", ".join(missing))}</p>' if missing else "")
    collisions = (f'<p class="hint">Имена файлов: одинаковых путей в базе {_n(names.get("duplicate_live_paths"))}, '
                  f'одинаковых имён с разными байтами {_n(names.get("same_filename_different_md5"))}, старых имён без '
                  f'MD5 в имени {_n(names.get("unsafe_content_names"))} (это не ошибка: так называли файлы старые версии).</p>'
                  if names else "")
    history = [[esc(_stamp(r.get("created_at"))), esc(r.get("operation") or ""), esc(r.get("status") or ""),
                esc(human_size(int(r.get("reclaimed_bytes") or 0)))] for r in _rows(database.get("maintenance_history"))]
    history_html = (f'<details><summary>Обслуживание базы — последние {len(history)}</summary>'
                    f'{_table(["Когда", "Что", "Итог", "Освобождено"], history)}</details>') if history else ""
    actions = f'<div class="tool-actions">{_button("backup", busy)}{_button("indices", busy)}</div>'
    lead = "Копия базы — отдельный файл в папке резервных копий архива; каждая починка ниже делает такую копию сама."
    return _section("db", "База данных", esc(lead), numbers + path + write + missing_html + actions + collisions + history_html)


def _queues_html(report: dict, busy: bool) -> str:
    queues = _part(report, "queues")
    now = time.time()

    def after(ts) -> str:
        wait = int(float(ts or 0) - now)
        return "сейчас" if wait <= 0 else "через " + _took(wait)

    reverse = [[esc(r.get("service")), _n(r.get("files")), esc(after(r.get("next_retry"))), _n(r.get("max_attempts"))]
               for r in _rows(queues.get("reverse_retry"))]
    services = []
    for r in _rows(queues.get("service_state")):
        short, day = r.get("short_remaining"), r.get("long_remaining")
        quota = []
        if short is not None and int(short) >= 0:
            quota.append(f"короткий лимит: {int(short)}")
        if day is not None and int(day) >= 0:
            quota.append(f"на сутки: {int(day)}")
        services.append([esc(r.get("service")), esc(after(r.get("cooldown_until")) if float(r.get("cooldown_until") or 0) > now else "готов"),
                         esc(", ".join(quota) or "—"), esc(r.get("reason") or "")])
    status_names = {"pending": "ждут", "stale": "устарели", "failed": "с ошибкой", "running": "идут", "done": "готово"}
    enrich = [[esc(status_names.get(str(r.get("status")), str(r.get("status") or ""))), esc(r.get("job_key")), _n(r.get("files")),
               _n(r.get("max_attempts"))] for r in _rows(queues.get("tag_enrichment"))]
    cache_total = sum(int(r.get("tags") or 0) for r in _rows(queues.get("tag_category_cache")))
    ops = [[esc(r.get("op_type")), esc(r.get("status")), esc(r.get("error") or r.get("target_id") or ""),
            esc(_stamp(r.get("updated_at")))] for r in _rows(queues.get("unfinished_operations"))]
    stale = any(r.get("status") == "stale" for r in _rows(queues.get("tag_enrichment")))
    inner = (
        "<h3>Обратный поиск — файлы, отложенные до следующей попытки</h3>"
        + _table(["Сервис", "Файлов", "Следующая попытка", "Попыток (макс.)"], reverse, empty="Очередь пуста.")
        + ("<h3>Сервисы обратного поиска</h3>" + _table(["Сервис", "Состояние", "Осталось запросов", "Почему ждёт"], services)
           if services else "")
        + "<h3>Раскладка тегов по категориям</h3>"
        + _table(["Задания", "Сайт", "Файлов", "Попыток (макс.)"], enrich, empty="Заданий нет.")
        + (f'<p class="hint">Категорий тегов запомнено: {_n(cache_total)}.</p>' if cache_total else "")
        + (f'<div class="tool-actions">{_button("requeue_categories", busy)}</div>' if stale else "")
        + ("<h3>Незавершённые операции</h3>" + _table(["Операция", "Состояние", "Подробности", "Обновлена"], ops) if ops else "")
    )
    return _section("queues", "Очереди", "", inner)


def _sites_html(report: dict) -> str:
    scan, journal = _part(report, "site_scan"), _part(report, "source_journal")
    sites = [[esc(r.get("site")), _n(r.get("checked")), _n(r.get("matches")), _n(r.get("pending_among_started")), _n(r.get("errors"))]
             for r in _rows(scan.get("sites"))]
    table = _table(["Сайт", "Проверено файлов", "Найдено", "Осталось среди начатых", "Ошибок"], sites,
                   empty="Включённых сайтов нет.")
    status = {"available": "доступна", "missing_root": "не найдена", "offline_root": "диск не подключён",
              "path_error": "путь не читается", "not_configured": "не выбрана"}.get(
        str(journal.get("source_root_status")), str(journal.get("source_root_status") or "?"))
    reconcile = ""
    if journal:
        reconcile = (f'<p class="hint">Исходная папка ({esc(status)}): файлов {_n(journal.get("source_files"))}, '
                     f'из них картинок и видео {_n(journal.get("source_media_files"))}. В журнале сайтов путей '
                     f'{_n(journal.get("journal_distinct_paths"))}: на месте {_n(journal.get("journal_existing_on_disk"))}, '
                     f'нет на диске {_n(journal.get("journal_missing_on_disk"))}, диск не подключён '
                     f'{_n(journal.get("journal_offline_root"))}, вне текущей исходной папки '
                     f'{_n(journal.get("journal_outside_source"))}.</p>')
    lead = esc(f"Сколько файлов из начатых парсер уже проверил на каждом сайте (всего начато {_n(scan.get('started_paths'))}).")
    return _section("sites", "Проверки сайтов", lead, table + reconcile)


def _trash_html(report: dict) -> str:
    reasons = [[esc(r.get("label") or r.get("reason")), _n(r.get("files"))] for r in _rows(_part(report, "trash").get("by_reason"))]
    return _section("trash", "Корзина — почему там файлы", "",
                    _table(["Причина", "Файлов"], reasons, empty="Корзина пуста.") + '<p class="hint"><a href="/posts?section=trash">Открыть корзину</a></p>')


def _files_html(report: dict) -> str:
    lib, samples = _part(report, "library"), _part(report, "samples")
    parts = []
    if lib.get("missing_on_disk") is None:
        parts.append('<p class="hint">Наличие каждого файла этой проверкой не смотрелось — это делает «Проверить '
                     'файлы» (<a href="#diag-files-check">«Файлы библиотеки»</a> выше).</p>')
    else:
        parts.append(f'<p>Проверено файлов: {_n(lib.get("checked_on_disk"))}. Нет на диске: <b>{_n(lib.get("missing_on_disk"))}</b>, '
                     f'диск не подключён: {_n(lib.get("offline_root_on_disk"))}, путь не читается: '
                     f'{_n(lib.get("path_error_on_disk"))}.</p>')
        for key, title in (("missing_on_disk", "Нет на диске"), ("offline_root_on_disk", "Диск не подключён"),
                           ("path_error_on_disk", "Путь не читается")):
            rows = _rows(samples.get(key))
            if rows:
                items = "".join(f"<li>{_card_link(r)} <small>{esc(r.get('path') or '')}</small></li>" for r in rows)
                parts.append(f'<details><summary>{esc(title)} — первые {len(rows)}</summary><ul class="diag-files">{items}</ul></details>')
    for key, title in (("without_source", "Без источника"), ("without_tags", "Без тегов")):
        rows = _rows(samples.get(key))[:SAMPLES_SHOWN]
        if rows:
            items = "".join(f"<li>{_card_link(r)} <small>{esc(r.get('path') or '')}</small></li>" for r in rows)
            parts.append(f'<details><summary>{esc(title)} — последние {len(rows)}</summary><ul class="diag-files">{items}</ul></details>')
    return _section("files", "Файлы", "", "".join(parts))


def _protect_html(report: dict) -> str:
    protect = _part(report, "source_protection")
    events = _rows(protect.get("blocked_events"))
    rows = [[esc(_when(_seconds(e.get("at")))), esc(e.get("operation") or "?"),
             f'<span class="tool-path">{esc(e.get("path") or "")}</span>'] for e in reversed(events)]
    inner = (f'<p>Исходный архив только читается: <span class="tool-path">{esc(protect.get("source_root") or "не выбран")}</span><br>'
             f'Меняется только рабочая папка: <span class="tool-path">{esc(protect.get("output_root") or "")}</span></p>'
             + _table(["Когда", "Что пыталось", "Файл"], rows, empty="Попыток изменить исходный архив не было."))
    return _section("protect", "Защита исходного архива", "", inner)


def _errors_html(report: dict, busy: bool) -> str:
    errors = _part(report, "errors")
    crash = errors.get("last_crash") if isinstance(errors.get("last_crash"), dict) else {}
    lines = [str(x) for x in errors.get("last_lines") or []]
    parts = []
    if crash:
        parts.append(f'<h3>Последнее падение — {esc(crash.get("created_at") or "")}</h3>'
                     f'<pre class="diag-pre">{esc(crash.get("traceback") or "")}</pre>')
    parts.append(f'<h3>Конец журнала ошибок</h3><pre class="diag-pre">{esc(chr(10).join(lines))}</pre>' if lines
                 else '<p class="hint">В журнале ошибок записей нет.</p>')
    parts.append(f'<div class="tool-actions">{_button("redact_logs", busy)}</div>'
                 '<p class="hint">В логах старых версий бывали API-ключи, логины и cookie: эта кнопка переписывает файлы '
                 'логов без них (копий с ключами не остаётся). База и картинки не трогаются.</p>')
    return _section("errors", "Ошибки", "", "".join(parts))


def _slow_html(report: dict) -> str:
    rows = _rows(_part(report, "performance").get("slow_operations"))
    rows = sorted(rows, key=lambda r: -float(r.get("duration_ms") or 0))[:SAMPLES_SHOWN]
    table = [[esc(r.get("at") or ""), esc(r.get("operation") or ""), _n(r.get("duration_ms")),
              f'<small>{esc(json.dumps(r.get("detail"), ensure_ascii=False) if r.get("detail") else "")}</small>'] for r in rows]
    return _section("slow", "Медленные операции", "Самые долгие из записанных программой.",
                    _table(["Когда", "Что", "мс", "Подробности"], table, empty="Медленных операций не записано."))


def _safe(name: str, fn, *args) -> str:
    """One part of the page; a value it did not expect (an old or damaged report file) costs that part,
    not the whole page — the page is where a new check is started."""
    try:
        return fn(*args)
    except Exception as exc:
        import logging
        logging.getLogger("local_booru.webui").warning("diagnostics page: %s not shown: %s: %s", name, type(exc).__name__,
                                                        exc, exc_info=True)
        return (f'<p class="tool-warn">Раздел «{esc(name)}» не показан ({esc(type(exc).__name__)}) — '
                f'проверьте библиотеку снова.</p>')


def _report_html(report: dict, busy: bool) -> str:
    items = _safe("Что требует внимания", _attention, report, busy)
    if isinstance(items, str):
        return items + _report_parts(report, busy)
    if items:
        lis = "".join(f'<li class="{level}"><span class="what">{esc(text)}</span>{fix}</li>' for level, text, fix in items)
        attention = f'<h2 class="diag-attention-title">Что требует внимания</h2><ul class="diag-attention">{lis}</ul>'
    else:
        attention = '<ul class="diag-attention"><li class="ok"><span class="what">Проблем не найдено.</span></li></ul>'
    return attention + _report_parts(report, busy)


def _report_parts(report: dict, busy: bool) -> str:
    text = (f'<details class="diag-text"><summary>Весь отчёт текстом</summary>'
            f'<pre class="diag-pre">{esc(report.get("summary_text") or "")}</pre></details>')
    parts = (("Библиотека", _library_html, (report,)), ("Точные копии", _md5_html, (report, busy)),
             ("Похожие картинки", _similar_html, (report,)), ("База данных", _database_html, (report, busy)),
             ("Очереди", _queues_html, (report, busy)), ("Проверки сайтов", _sites_html, (report,)),
             ("Корзина", _trash_html, (report,)), ("Файлы", _files_html, (report,)),
             ("Защита исходного архива", _protect_html, (report,)), ("Ошибки", _errors_html, (report, busy)),
             ("Медленные операции", _slow_html, (report,)))
    return "".join(_safe(name, fn, *args) for name, fn, args in parts) + text


SOURCE_CLASSES = (       # core.source_contamination_audit.CLASSIFICATIONS, in words
    ("verified_local_md5", "MD5 поста — этот файл"),
    ("verified_alias_same_image", "MD5 поста — известный другой MD5 этой картинки"),
    ("alias_bound_to_other_image", "MD5 поста — у другой картинки библиотеки"),
    ("verified_mismatch", "MD5 поста другой"),
    ("post_missing", "поста нет на сайте"),
    ("parse_error", "сайт ответил непонятно"),
    ("network_unverified", "не сверялось"),
)


def _sources_html(report: dict | None, busy: bool) -> str:
    """«Несколько постов одного сайта»: the read-only search of ``source_contamination_audit``."""
    form = (f'<form class="tool-actions" data-form="diag-sources">'
            f'<button type="submit" data-needs-idle{" disabled" if busy else ""}>Найти такие картинки</button>'
            '<label class="check"><input type="checkbox" name="verify_network"> сверить MD5 постов с сайтами '
            '(через сеть, медленно)</label>'
            '<label class="num">запросов не больше <input type="number" name="limit" min="0" max="1000000" value="500"></label>'
            '<span class="hint">0 — без предела; сайты спрашиваются в том же темпе, что и парсером</span>'
            '<span class="action-status" role="status"></span></form>')
    lead = esc("У картинки должен быть один пост на каждом сайте. Несколько постов одного сайта — признак, что к ней "
               "прицепились чужие источники (так бывало в старых версиях парсера). Поиск только читает; со сверкой "
               "спрашивает у сайтов MD5 каждого поста и сравнивает с файлом.")
    if not report:
        return _section("sources", "Несколько постов одного сайта", lead, form + '<p class="hint">Поиска ещё не было.</p>')
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    net = report.get("network_verification") if isinstance(report.get("network_verification"), dict) else {}
    when = _when(report.get("finished_at")) or esc(report.get("created_at") or "")
    last = (f'<p class="hint">Последний поиск: {esc(when)}'
            + (f', сверено с сайтами {_n(net.get("attempted"))} постов' if net.get("enabled") else ", без сверки с сайтами")
            + (", остановлен — список неполный" if report.get("cancelled") else "") + ".</p>")
    numbers = _numbers([
        ("Картинок", _n(summary.get("candidate_images")), "с несколькими постами одного сайта",
         bool(summary.get("candidate_images"))),
        ("Пар «картинка — сайт»", _n(summary.get("candidate_groups")), "", False),
        ("Постов в них", _n(summary.get("candidate_sources")), "", False),
        ("Больше всего у одной", _n(summary.get("max_sources_per_host")), "постов одного сайта", False),
    ])
    hosts = [[esc(h.get("host")), _n(h.get("images")), _n(h.get("sources")), _n((h.get("distribution") or {}).get("26+"))]
             for h in _rows(report.get("hosts"))]
    hosts_html = _table(["Сайт", "Картинок", "Постов", "Картинок с 26+ постами"], hosts)
    classes = report.get("classifications") if isinstance(report.get("classifications"), dict) else {}
    classes_html = ""
    if net.get("enabled"):
        classes_html = _numbers([(label, _n(classes.get(key)), "", key in ("alias_bound_to_other_image", "verified_mismatch")
                                  and bool(classes.get(key))) for key, label in SOURCE_CLASSES])
    groups = []
    for g in _rows(report.get("groups"))[:SAMPLES_SHOWN]:
        card = {"id": g.get("image_id"), "path": g.get("path"), "file_name": g.get("file_name")}
        groups.append([_card_link(card), esc(g.get("host")), _n(g.get("source_count")), _n(g.get("current_tag_owner_count")),
                       _n(g.get("verified_valid_sources")), _n(g.get("strong_mismatch_sources")), _n(g.get("unverified_sources"))])
    total = int(report.get("groups_total") or len(_rows(report.get("groups"))))
    groups_html = (f'<details><summary>Картинки — первые {len(groups)} из {_n(total)} (больше всего постов)</summary>'
                   + _table(["Картинка", "Сайт", "Постов", "С тегами", "MD5 совпал", "Чужие", "Не сверено"], groups)
                   + "</details>") if groups else ""
    download = '<p class="hint"><a href="/api/diag/sources.json" download>Скачать всё найденное (JSON)</a></p>'
    return _section("sources", "Несколько постов одного сайта", lead,
                    form + last + numbers + classes_html + hosts_html + groups_html + download)


# ── (v5.44.118) «Файлы библиотеки»: one check and one repair of the files ────
def _which(rows: list) -> str:
    """«какие»: the cards (their pages) of one kind, the first of them — by their files' names (a page never shows
    a whole path)."""
    from core.file_check import name_of
    items = "".join(f'<li><a href="/post/{int(r["id"])}">{esc(r.get("file_name") or name_of(r.get("path")) or "?")}'
                    f'</a></li>' for r in rows if str(r.get("id") or "").isdigit())
    if not items:
        return ""
    return f'<details class="files-which"><summary>какие</summary><ul class="diag-files">{items}</ul></details>'


def _files_found(report: dict) -> str:
    """What the check found, kind by kind (the lines of «Что требует внимания»)."""
    from core.file_check import _plural, place_of
    files = ("файл", "файла", "файлов")
    samples = report.get("samples") if isinstance(report.get("samples"), dict) else {}
    items = []
    whole = "На месте и целы" if report.get("md5") else "На месте"
    items.append(("ok", f"{whole} — {_n(report.get('present'))}", ""))
    if report.get("library_gone"):
        items.append(("bad", str(report["library_gone"]), ""))
    if int(report.get("missing") or 0):
        items.append(("bad", f"Нет на диске — {_n(report['missing'])}", _which(_rows(samples.get("missing")))))
    if int(report.get("relocate") or 0):
        items.append(("bad", f"Смотрят в старое место архива — {_n(report['relocate'])}: их файлы нашлись в новом "
                             f"месте архива", _which(_rows(samples.get("relocate")))))
    if int(report.get("damaged") or 0):
        how = "" if report.get("md5") else ", по прошлой сверке MD5"
        items.append(("bad", f"Испорчены — {_n(report['damaged'])} · байты не совпали с MD5{how}",
                      _which(_rows(samples.get("damaged")))))
    for root in _rows(report.get("offline_roots")):
        where = place_of(root.get("root"))         # «Диск F:» / «Папка «name»» — not its whole path
        count = _plural(int(root.get("files") or 0), files)
        items.append(("note", f"{where} недоступна — {count} в ней · их программа не трогает"
                      if where.startswith("Папка") else
                      f"{where} не подключён — {count} на нём · их программа не трогает", ""))
    if int(report.get("path_error") or 0):
        items.append(("note", f"Путь не читается — {_n(report['path_error'])} · их программа не трогает",
                      _which(_rows(samples.get("path_error")))))
    thumbs = report.get("thumbs") if isinstance(report.get("thumbs"), dict) else {}
    if int(thumbs.get("made") or 0):
        items.append(("note", f"Превью достроено: {_n(thumbs['made'])}", ""))
    return "".join(f'<li class="{level}"><span class="what">{esc(text)}</span>{fix}</li>' for level, text, fix in items)


def _files_fix(report: dict, busy: bool) -> str:
    """«Починить файлы (N)» and what it will do, step by step — or why there is nothing to do."""
    from core.file_check import plan_words
    if report.get("cancelled"):
        return '<p class="hint">Проверка была остановлена — числа неполные, чинить по ней нельзя. Проверьте снова.</p>'
    if report.get("repaired_at"):
        return ('<p class="hint">По этой проверке уже чинили — проверьте снова, чтобы увидеть, что осталось.</p>')
    steps = plan_words(report)
    fix = int(report.get("fix") or 0)
    if not fix or not steps:
        return '<p class="hint files-nothing">Чинить нечего.</p>'
    from webui.pages.post_sets import FILES, plural
    ask = f"Точно починить {plural(fix, FILES)}? Нажмите ещё раз"
    return (f'<div class="files-fix"><div class="tool-actions">'
            f'<button type="button" class="danger" data-action="files-repair" data-stamp="{esc(report.get("stamp"))}" '
            f'data-ask="{esc(ask)}" data-needs-idle{" disabled" if busy else ""}>Починить файлы ({fix})</button>'
            f'<b>Что сделает — по порядку, само:</b><span class="action-status" role="status"></span></div>'
            f'<ol>{"".join(f"<li>{esc(step)}</li>" for step in steps)}</ol>'
            '<p class="hint">Перед починкой — копия базы. '
            + ('Скачивание идёт через интернет и только пока вы не работаете с программой. '
               if int(report.get("recover") or 0) else "")
            + 'Сделает ровно показанное: что изменилось после проверки, не тронет.</p></div>')


def _files_panel(report: dict | None, busy: bool) -> str:
    """(v5.44.118) The panel «Файлы библиотеки» (``core/file_check.py``)."""
    form = ('<form class="tool-actions" data-form="files-check">'
            f'<button type="submit" class="primary" data-files-check data-needs-idle{" disabled" if busy else ""}>'
            'Проверить файлы</button>'
            '<label class="check"><input type="checkbox" name="md5"> и сверить каждый файл по MD5 — читает весь архив, '
            'идёт, пока вы не работаете с программой</label>'
            f'<button type="button" class="danger" data-action="diag-stop"{"" if _can_stop() else " disabled"}>'
            'Остановить</button><span class="action-status" role="status"></span></form>')
    lead = ("Одна проверка: на месте ли файл каждой карточки, цел ли он, не переносили ли архив; заодно достраивает "
            "превью, которых нет. Проверка только смотрит — карточки и файлы не меняет.")
    head = (f'<section class="panel diag-main diag-files-check" id="diag-files-check" aria-labelledby="files-title">'
            f'<h2 id="files-title">Файлы библиотеки</h2><p class="hint">{esc(lead)}</p>{form}')
    if not report:
        return head + '<p class="hint">Проверки файлов ещё не было.</p></section>'
    from core.file_check import _plural
    when = _when(report.get("finished_at"))
    took = f", заняла {_took(report.get('took_seconds'))}" if report.get("took_seconds") else ""
    md5 = ", со сверкой MD5" if report.get("md5") else ""
    last = (f'<p class="hint files-last">Последняя проверка — {esc(when)}{esc(took)}: '
            f'{esc(_plural(int(report.get("total") or 0), ("файл", "файла", "файлов")))}{md5}.</p>')
    return (head + last + f'<ul class="diag-attention files-found">{_files_found(report)}</ul>'
            + _files_fix(report, busy) + '</section>')


def _history_html(history: list) -> str:
    if not history:
        return ""
    items = []
    for h in history[:10]:
        lines = "".join(f"<li>{esc(x)}</li>" for x in h.get("lines") or [])
        state = "" if h.get("ok", True) else ' <span class="tool-warn">не удалось</span>'
        items.append(f'<li><b>{esc(h.get("label") or "")}</b> <small>{esc(_when(h.get("at")))}</small>{state}'
                     f'{f"<ul>{lines}</ul>" if lines else ""}</li>')
    return (f'<details class="diag-history"><summary>Починка — последние действия</summary>'
            f'<ul>{"".join(items)}</ul></details>')


def _running_now() -> str:
    """Work of other pages going right now (the numbers of the report change while it goes)."""
    from core import page_jobs, subscription_jobs
    from core.parser import jobs as parser_jobs
    parts = []
    maintenance = parser_jobs.MAINTENANCE_OWNER.split("«")[0]       # held by our own task: its journal says
    if parser_jobs.is_running() and not parser_jobs.owner().startswith(maintenance):
        parts.append('<a href="/parser">парсер</a>')
    if subscription_jobs.is_running() and not subscription_jobs.owner().startswith(maintenance):
        parts.append('<a href="/subs">проверка подписок</a>')
    for task in page_jobs.running():
        if task.kind == "duplicates":
            parts.append('<a href="/dupes">поиск дублей</a>')
        elif task.kind != KIND:
            parts.append(esc(task.label))
    return f'<p class="hint">Сейчас идёт: {", ".join(parts)} — числа отчёта могут меняться.</p>' if parts else ""


def tabs(current: str) -> tuple:
    """The pages of «Диагностика» for the section bar."""
    return tuple((label, href, key == current) for key, label, href in (
        ("check", "Проверка", "/diag"), ("maintenance", "Обслуживание", "/diag/maintenance"),
        ("reset", "Удаление и сброс", "/diag/reset")))


def diag_page(req: Request) -> Response:
    from core.library_diagnostics import last_report, repair_history
    report = last_report()
    sources = last_report("sources")
    files_report = last_report("files")
    history = repair_history()
    busy = _busy()
    if report:
        took = f', заняла {_took(report.get("took_seconds"))}' if report.get("took_seconds") else ""
        files = ", с проверкой файлов на диске" if report.get("verify_files") else ""
        when = _when(report.get("finished_at")) or esc(report.get("created_at") or "")
        last = f"Последняя проверка: {when}{took}{files}."
        newer = [h for h in history if float(h.get("at") or 0) > float(report.get("finished_at") or 0)]
        if newer:
            last += f" После неё была починка («{newer[0].get('label')}») — проверьте снова, чтобы увидеть новые числа."
        body_report = _report_html(report, busy)
        download = '<a class="button" href="/api/diag/report.json" download>Скачать отчёт (JSON)</a>'
    else:
        last = "Проверки ещё не было."
        body_report = ""
        download = ""
    disabled = " disabled" if busy else ""
    body = f"""
<section class="tool tool-wide settings diag" id="diag">
  <header class="tool-head">
    <h1>Диагностика</h1>
    <p class="tool-lead">Проверка только читает: ничего не удаляет и не переносит. Чинят отдельные кнопки — у того, что
    они чинят; перед каждым изменением базы программа сама делает её копию.</p>
  </header>
  <div class="tool-board">
    <div class="board-main">
      {_running_now()}
      <div class="job panel" data-job hidden>
        <div class="job-head"><b class="job-title"></b> <span class="job-status"></span></div>
        <pre class="job-log" tabindex="0" aria-label="Журнал проверки и починки"></pre>
      </div>
      {_safe("Файлы библиотеки", _files_panel, files_report, busy)}
      <section class="panel diag-main" aria-labelledby="audit-title">
        <h2 id="audit-title">Проверка библиотеки</h2>
        <p class="hint">База, точные копии, очереди, корзина, ошибки — всё, кроме самих файлов (их — «Проверить
          файлы» выше).</p>
        <form class="tool-actions" data-form="diag-audit">
          <button type="submit" class="primary" data-audit data-needs-idle{disabled}>Проверить библиотеку</button>
          <button type="button" class="danger" data-action="diag-stop"{"" if _can_stop() else " disabled"}>Остановить</button>
          {download}
          <span class="action-status" role="status"></span>
        </form>
        <p class="hint diag-last">{esc(last)}</p>
        <div class="diag-report">{body_report}</div>
        {"" if report else '<div class="empty">Нажмите «Проверить библиотеку»: на небольшой библиотеке это секунды, на архиве в сотни тысяч файлов — минуты.</div>'}
      </section>
    </div>
    <aside class="board-side">
      {_safe("Несколько постов одного сайта", _sources_html, sources, busy)}
      {_history_html(history)}
    </aside>
  </div>
</section>"""
    return Response.html(page_frame("Диагностика", body, active="diag", subnav=False, kind="tool",
                                    body_data={"tool": "diag"}, tabs=tabs("check"), **_prefs()))


# ── the task: the check and the repairs ────────────────────────────────────
def diag_state(req: Request) -> Response:
    from core import page_jobs
    task = page_jobs.current(KIND)
    if task is None:
        return Response.json({"ok": True, "status": "idle"})
    return Response.json({"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0))})


def diag_audit(req: Request) -> Response:
    from core import page_jobs
    verify = bool(req.json().get("verify_files"))
    from webui.settings_store import for_task
    settings = for_task(req.settings)

    def run(progress, stop_check):
        from core.library_diagnostics import audit_library, remember_report
        started = time.time()
        report = audit_library(settings, verify_files=verify, progress=progress, stop_check=stop_check)
        report["took_seconds"] = round(time.time() - started, 1)
        report["verify_files"] = verify
        report["finished_at"] = time.time()
        remember_report(report)
        return {"stopped": bool(report.get("cancelled")), "took_seconds": report["took_seconds"]}

    label = "Проверка библиотеки" + (" и файлов на диске" if verify else "")
    try:
        task = page_jobs.start(KIND, label, run)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": str(exc)}, 409)
    return Response.json({"ok": True, "id": task.id})


def diag_repair(req: Request) -> Response:
    from core import page_jobs
    action = str(req.json().get("action") or "")
    if action not in REPAIRS:
        return Response.json({"ok": False, "error": "Неизвестная починка"}, 400)
    _text, title, _ask, work = REPAIRS[action]
    from webui.settings_store import for_task
    settings = for_task(req.settings)

    def run(progress, stop_check):
        from core.library_diagnostics import remember_repair
        try:
            out = work(settings, progress, stop_check)
        except BaseException as exc:
            remember_repair(title, [f"{type(exc).__name__}: {exc}"], ok=False)
            raise
        lines, stopped = (out.get("lines") or [], bool(out.get("stopped"))) if isinstance(out, dict) else (out, False)
        for line in lines:
            progress(line)
        remember_repair(title, lines)
        return {"lines": lines, "stopped": stopped}      # a repair that cannot stop half-way ends «готово»

    try:
        task = page_jobs.start(KIND, title, run, stoppable=action in STOPPABLE)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": str(exc)}, 409)
    return Response.json({"ok": True, "id": task.id})


def diag_sources(req: Request) -> Response:
    """«Несколько постов одного сайта» — ``source_contamination_audit`` as a page task (it only reads)."""
    import re
    from core import page_jobs
    body = req.json()
    verify = bool(body.get("verify_network"))
    try:
        limit = max(0, min(1_000_000, int(body.get("limit") or 0)))
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Предел запросов — число"}, 400)
    from webui.settings_store import for_task
    settings = for_task(req.settings)
    each = re.compile(r"Source audit: \d+/\d+ ")

    def run(progress, stop_check):
        from core.library_diagnostics import remember_report
        from core.source_contamination_audit import audit_source_contamination
        counted = {"n": 0}

        def quiet(message):                 # one line per picture would bury the journal: every 200th
            text = str(message)
            if each.match(text):
                counted["n"] += 1
                if counted["n"] % 200:
                    return
            progress(text)

        started = time.time()
        report = audit_source_contamination(settings, verify_network=verify, max_network_sources=limit,
                                            progress=quiet, stop_check=stop_check)
        report["took_seconds"] = round(time.time() - started, 1)
        report["finished_at"] = time.time()
        remember_report(report, "sources")
        summary = report.get("summary") or {}
        progress(f"Картинок с несколькими постами одного сайта: {int(summary.get('candidate_images') or 0)}")
        return {"stopped": bool(report.get("cancelled"))}

    label = "Поиск нескольких постов одного сайта" + (" со сверкой MD5" if verify else "")
    try:
        task = page_jobs.start(KIND, label, run)
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": str(exc)}, 409)
    return Response.json({"ok": True, "id": task.id})


# ── (v5.44.118) «Проверить файлы» and «Починить файлы» ─────────────────────
FILES_CHECK = "Проверка файлов"            # the journal's and the history's titles
FILES_REPAIR = "Починка файлов"


def _files_check_work(settings, progress, stop_check, options):
    from core import file_check
    from core.library_diagnostics import remember_report
    from webui.pages import maintenance
    report = file_check.check(settings, md5=bool(options.get("md5")), progress=maintenance._plain(progress),
                              stop_check=stop_check)
    if report.get("cancelled"):                 # a plan of half a check is not a plan
        report["plan"] = {}
        report["fix"] = 0
    remember_report(report, "files")
    lines = list(report.get("lines") or [])
    if report.get("cancelled"):
        lines.insert(0, "Остановлено на середине — числа неполные, починки по ней не будет")
    return {"lines": lines, "stopped": bool(report.get("cancelled"))}


def files_check(req: Request) -> Response:
    """«Проверить файлы» ({md5}: also read every file and compare its MD5) — a page task of the diagnostics' lane."""
    from webui.pages import maintenance
    from webui.settings_store import for_task
    job = maintenance.Job("files_check", "Проверить файлы", FILES_CHECK, "", _files_check_work,
                          options=(("md5", "и сверить каждый файл по MD5"),))
    return maintenance.start_job(job, for_task(req.settings), {"md5": bool(req.json().get("md5"))})


def files_repair(req: Request) -> Response:
    """«Починить файлы»: exactly the plan of the last «Проверить файлы» — the one the page showed (``stamp``), once."""
    from core import file_check
    from core.library_diagnostics import last_report, remember_report
    from webui.pages import maintenance
    from webui.settings_store import for_task
    report = last_report("files")
    stamp = str(req.json().get("stamp") or "")
    if not report or not stamp or str(report.get("stamp") or "") != stamp:
        return Response.json({"ok": False, "error": "Проверка файлов с тех пор была другая — обновите страницу"}, 409)
    if report.get("repaired_at"):
        return Response.json({"ok": False, "error": "По этой проверке уже чинили — проверьте файлы снова"}, 409)
    if report.get("cancelled") or not int(report.get("fix") or 0):
        return Response.json({"ok": False, "error": "Чинить нечего — проверьте файлы снова"}, 409)
    writers = maintenance._writers_now()        # the downloads wait for the parser (its lane), so it is not taken
    if writers:
        return Response.json({"ok": False, "error": f"Сейчас нельзя: {writers}"}, 409)

    def work(settings, progress, stop_check, options):
        try:
            out = file_check.repair(settings, report, progress=maintenance._plain(progress), stop_check=stop_check)
        finally:
            report["repaired_at"] = time.time()     # done (or half done): the next repair needs a new check
            remember_report(report, "files")
            maintenance._tags_changed()
        return {"lines": out["lines"], "stopped": out["stopped"]}

    job = maintenance.Job("files_repair", "Починить файлы", FILES_REPAIR, "", work)
    return maintenance.start_job(job, for_task(req.settings), {})


def diag_stop(req: Request) -> Response:
    from core import page_jobs
    task = page_jobs.current(KIND)
    if task is None or not task.running:
        return Response.json({"ok": False, "error": "Сейчас ничего не идёт"}, 409)
    if not task.stop():
        return Response.json({"ok": False, "error": "Это не прерывается на середине — дождитесь конца"}, 409)
    return Response.json({"ok": True})


def _report_download(kind: str, name: str) -> Response:
    from core.library_diagnostics import last_report
    from core.redaction import sanitize_object
    report = last_report(kind)
    if not report:
        return Response.json({"ok": False, "error": "Проверки ещё не было"}, 404)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(float(report.get("finished_at") or time.time())))
    body = json.dumps(sanitize_object(report), ensure_ascii=False, indent=2).encode("utf-8")
    return Response(200, body, "application/json; charset=utf-8",
                    {"Content-Disposition": f'attachment; filename="Local_Booru_{name}_{stamp}.json"'})


def diag_report_json(req: Request) -> Response:
    return _report_download("library", "library_audit")


def diag_sources_json(req: Request) -> Response:
    """The whole last sources search, straight from its file (on a big archive it is tens of MB: it
    was cleaned of keys when it was written and is not built again for each download)."""
    from core.library_diagnostics import last_report, report_file
    report, path = last_report("sources"), report_file("sources")
    if not report or not path.is_file():
        return Response.json({"ok": False, "error": "Поиска ещё не было"}, 404)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(float(report.get("finished_at") or time.time())))
    return Response(200, b"", "application/json; charset=utf-8", file=path,
                    headers={"Content-Disposition": f'attachment; filename="Local_Booru_source_audit_{stamp}.json"',
                             "Cache-Control": "no-store"})
