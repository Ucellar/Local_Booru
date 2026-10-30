"""The tab «Обслуживание» of «Диагностика» (/diag/maintenance): what the old settings page kept under
«Обслуживание библиотеки» — the base (optimize, VACUUM, a copy, integrity, the index), the tables built
from it (similar pictures, tag categories), the files (are they there, are their bytes right, recover,
thumbnails, paths after the archive moved) and the tags (e621 junk, general-only sets, site synonyms,
old .tags/.sources files).

Each job is a page task of the diagnostics' kind (``diag.KIND``): one at a time together with the check
and the repairs, with the same journal, «Остановить» and history.  A job that changes the base makes its
copy first; a job that rewrites much of it (``Job.quiet``) refuses while the parser, the subscriptions or
another page task write to the library, and holds them back (their claims) until it ends; one that rewrites
the base itself (``Job.holds_imports``) also makes any other import wait (``import_pipeline.IMPORT_LOCK``)."""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Callable

from webui.html import esc, human_size, page_frame
from webui.pages import diag
from webui.server import Request, Response

KIND = diag.KIND
_LOG = logging.getLogger("local_booru.webui")


def register(router) -> None:
    router.add("GET", r"/diag/maintenance", maintenance_page)
    router.add("POST", r"/api/diag/maintain", maintain)


@dataclass(frozen=True)
class Job:
    id: str
    label: str                 # the button
    title: str                 # the journal and the history
    hint: str                  # what it does and what it leaves alone, in words
    work: Callable             # (settings, progress, stop_check, options) -> lines | {"lines", "stopped"}
    ask: str = ""              # the question of the second press
    danger: bool = False
    quiet: bool = False        # rewrites much of the library: only when nothing else writes to it
    options: tuple = ()        # ((name, label), …) switches sent with the button
    holds_imports: bool = False  # rewrites the base itself: a picture arriving meanwhile (the extension) waits
    stoppable: bool = True     # has places to stop half-way («Остановить» is off for the others)


# ── small helpers ──────────────────────────────────────────────────────────
def _n(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _backup(settings: dict, progress, reason: str) -> str:
    from core.library_diagnostics import create_forced_backup
    progress("Копирую базу…")
    path = create_forced_backup(settings, reason=reason)
    if not path:
        raise RuntimeError("Копия базы не создана (базы нет или диск не дал записать) — ничего не изменено")
    progress(f"Копия базы: {path}")
    return path


def _tags_changed() -> None:
    from webui import data
    data.TAG_COUNTS.invalidate()


# The maintenance lane (core.maintenance_coordinator) says why it waits in codes.
_WAITS = {
    "parser_active": "идёт парсер",
    "recent_user_activity": "вы работаете с программой — продолжу, когда будет пауза",
    "maintenance_paused": "обслуживание на паузе",
    "low_system_memory": "мало свободной памяти",
    "high_system_memory_load": "память почти вся занята",
    "database_safe_mode": "база открыта только для чтения",
    "application_shutting_down": "программа закрывается",
}


def _plain(progress):
    """``progress`` that tells the lane's waiting in words."""
    def say(text) -> None:
        text = str(text)
        for prefix, words in (("Обслуживание ожидает завершения: ", "Ждёт, пока закончится: "),
                              ("Обслуживание ожидает: ", "Ждёт: ")):
            if text.startswith(prefix):
                reason = text[len(prefix):]
                if reason.startswith("activity:"):
                    reason = "занято: " + reason[len("activity:"):]
                text = words + _WAITS.get(reason, reason)
                break
        progress(text)
    return say


_INDEXED = re.compile(r"(?:done )?indexed=(\d+)(?: scanned=(\d+))? skipped=(\d+)")


def _index_words(progress):
    """The indexer's «indexed=… skipped=…» in words (every 500th of its lines: one per 100 files)."""
    counted = {"n": 0}

    def say(text) -> None:
        text = str(text)
        m = _INDEXED.match(text)
        if m:
            counted["n"] += 1
            if counted["n"] % 5 and not text.startswith("done"):
                return
            text = f"Записано: {m.group(1)}, без изменений: {m.group(3)}"
        elif text == "scan roots...":
            text = "Обхожу папки рабочей библиотеки…"
        progress(text)
    return say


def _library_in_place(settings: dict, progress) -> None:
    """Work that judges cards by where their files are (the index, the repair, the old .tags import) runs
    only on the library where it is: its folder is there, and no card points to the old place of a moved
    archive while its file is here — those would be taken for gone (deleted, or trashed and then no
    longer movable).  Raises with what to do first."""
    from core.library_lifecycle import relocate_missing_library_paths
    from core.storage_roots import configured_output_root, managed_output_root_state
    state = managed_output_root_state(settings)
    if not state.available:
        raise RuntimeError(f"Папки рабочей библиотеки нет или диск недоступен: {configured_output_root(settings)} — "
                           "ничего не изменено. Подключите диск или верните папку на место.")
    progress("Смотрю, не указывают ли карточки на старое место архива…")
    moved = dict(relocate_missing_library_paths(settings, configured_output_root(settings), apply=False) or {})
    if moved.get("error"):
        raise RuntimeError(str(moved["error"]))
    if _n(moved.get("found")):
        raise RuntimeError(f"{_n(moved['found'])} карточек указывают на старое место архива, а их файлы есть здесь — "
                           "сначала «Найти файлы после переноса архива» и «Переписать пути». Ничего не изменено.")


def _index_lines(result: dict) -> list:
    lines = [f"Файлов в папках библиотеки: {_n(result.get('scanned'))}",
             f"Записано в базу заново или впервые: {_n(result.get('indexed'))}",
             f"Без изменений: {_n(result.get('skipped'))}",
             f"Убрано записей о файлах, которых нет на диске: {_n(result.get('removed'))}"]
    if _n(result.get("elsewhere")):
        lines.append(f"Файлов, чьи карточки указывают на старое место архива: {_n(result['elsewhere'])} — второй "
                     "карточкой не записаны; «Переписать пути» перенесёт карточки к ним")
    if _n(result.get("missing_refused")):
        lines.append(f"Файлов нет у {_n(result['missing_refused'])} карточек — больше половины: похоже, папку "
                     "библиотеки подменили или очистили; записи не удалены")
    return lines


# ── the base ───────────────────────────────────────────────────────────────
def _optimize(settings, progress, stop_check, options):
    from core.database.maintenance import optimize
    progress("ANALYZE, PRAGMA optimize, журнал WAL — в базу…")
    result = dict(optimize(settings) or {})
    before, after = dict(result.get("before") or {}), dict(result.get("after") or {})
    wal = str(result.get("wal_checkpoint") or "")
    wal_words = {"ok": "перенесён в базу", "busy": "занят — программа пишет в базу, перенесётся позже"}
    return ["Статистика запросов SQLite обновлена (ANALYZE, PRAGMA optimize)",
            "Журнал WAL: " + wal_words.get(wal, wal or "—"),
            f"Размер базы: {human_size(before.get('size_bytes'))} → {human_size(after.get('size_bytes'))}"]


def _vacuum(settings, progress, stop_check, options):
    from core.database.maintenance import storage_report, vacuum
    before = dict(storage_report(settings) or {})
    progress(f"База {human_size(before.get('size_bytes'))}, освободится около {human_size(before.get('reclaimable_bytes'))}; "
             "копирую базу и сжимаю…")
    result = dict(vacuum(settings, make_backup=True) or {})
    lines = [f"Копия базы: {result.get('backup') or '—'}",
             f"Размер базы: {human_size(result.get('before_bytes'))} → {human_size(result.get('after_bytes'))}",
             f"Освобождено: {human_size(result.get('reclaimed_bytes'))}"]
    if result.get("wal_checkpoint") != "ok":         # the base was being read: the journal waits for it
        lines.append("Файл базы уменьшится, когда журнал WAL перенесётся в неё — при закрытии программы или в простое")
    return lines


_ISSUES = {
    "missing_media_file": "файла карточки нет на диске",
    "media_path_error": "путь к файлу не читается",
    "storage_root_unavailable": "диск или папка недоступны (их карточки не проверены)",
    "orphan_image_tags_image": "теги у картинок, которых нет",
    "orphan_image_tags_tag": "ссылки на несуществующие теги",
    "orphan_image_sources_image": "источники у картинок, которых нет",
    "orphan_image_sources_source": "ссылки на несуществующие источники",
    "orphan_raw_metadata": "сохранённые ответы сайтов о картинках, которых нет",
    "empty_tags": "теги, которых нет ни у одной картинки",
    "empty_sources": "источники, которых нет ни у одной картинки",
}


def _issue_lines(result: dict) -> list:
    counts = dict(result.get("counts") or {})
    issues = [i for i in result.get("issues") or [] if isinstance(i, dict)]
    if not counts:
        return ["Проблем не найдено"]
    lines = []
    for kind in sorted(counts, key=lambda k: -_n(counts[k])):
        found = [i for i in issues if i.get("issue_type") == kind]
        if kind.startswith(("orphan_", "empty_")) and found:        # one issue: «N rows»
            rows = str(found[0].get("details") or "").replace(" rows", "")
            lines.append(f"{_ISSUES.get(kind, kind)}: {rows} строк")
        else:
            lines.append(f"{_ISSUES.get(kind, kind)}: {_n(counts[kind])}")
    missing = [str(i.get("path") or "") for i in issues if i.get("issue_type") == "missing_media_file"][:10]
    if missing:
        lines.append("Например: " + "; ".join(missing))
    for item in [i for i in issues if i.get("issue_type") == "storage_root_unavailable"][:3]:
        lines.append(f"Недоступно: {item.get('path')} ({item.get('details')})")
    return lines


def _integrity_check(settings, progress, stop_check, options):
    from core.services.library_service import check_integrity, unfinished_operations
    progress("Сверяю записи базы с файлами на диске и ищу осиротевшие строки…")
    result = dict(check_integrity(settings) or {})
    lines = _issue_lines(result)
    unfinished = list(unfinished_operations(settings, limit=100) or [])
    if unfinished:
        lines.append(f"Незавершённых операций в журнале базы: {len(unfinished)}{'+' if len(unfinished) >= 100 else ''}")
    return lines


def _integrity_repair(settings, progress, stop_check, options):
    from core.services.library_service import repair_integrity
    _library_in_place(settings, progress)
    backup = _backup(settings, progress, "integrity_repair")
    progress("Карточки пропавших файлов — в корзину, осиротевшие строки — прочь…")
    result = dict(repair_integrity(settings) or {})
    _tags_changed()
    fixed = dict(result.get("fixed") or {})
    after = dict(result.get("after") or {})
    left = _issue_lines(after)
    lines = [f"Копия базы: {backup}",
             f"В корзину как «файл пропал»: {_n(fixed.get('missing_marked_deleted'))}",
             f"Убрано осиротевших строк: {_n(fixed.get('orphan_rows_removed'))}"]
    if _n(fixed.get("moved_left")):
        lines.append(f"Не тронуты — файл переехал вместе с архивом: {_n(fixed['moved_left'])} («Переписать пути»)")
    if _n(fixed.get("missing_refused")):
        lines.append(f"Файлов нет у {_n(fixed['missing_refused'])} карточек — больше половины: похоже, папку библиотеки "
                     "подменили или очистили; в корзину ничего не убрано")
    lines.append("После починки: " + ("проблем нет" if left == ["Проблем не найдено"] else "; ".join(left[:4])))
    return lines


def _reindex(settings, progress, stop_check, options):
    from core.services.index_service import rebuild_index
    force, md5 = bool(options.get("force")), bool(options.get("md5"))
    _library_in_place(settings, progress)
    backup = _backup(settings, progress, "rebuild_index")
    result = dict(rebuild_index(settings, force=force, with_md5=md5, progress=_index_words(progress),
                                stop_check=stop_check) or {})
    _tags_changed()
    lines = [f"Копия базы: {backup}"] + _index_lines(result)
    return {"stopped": bool(stop_check()), "lines": lines}


# ── the tables built from the base ─────────────────────────────────────────
def _derived(cache: str):
    def work(settings, progress, stop_check, options):
        from core.derived_maintenance import rebuild_derived_cache
        result = dict(rebuild_derived_cache(settings, cache, mode="forced", progress=_plain(progress),
                                            stop_check=stop_check) or {})
        if result.get("cancelled"):
            return {"stopped": True, "lines": ["Остановлено: прежняя таблица осталась как была"]}
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "не построено; прежняя таблица осталась"))
        where = "в отдельном файле рядом с базой" if result.get("storage_backend") == "external" else "в основной базе"
        lines = [f"Готово: строк {_n(result.get('cache_rows'))} из {_n(result.get('source_rows'))} записей базы, {where}"]
        if _n(result.get("skipped_invalid")):
            lines.append(f"Пропущено записей с неверными данными: {_n(result.get('skipped_invalid'))}")
        return lines
    return work


# ── the files ──────────────────────────────────────────────────────────────
def _presence(settings, progress, stop_check, options):
    from core.file_integrity import run_presence_check
    result = dict(run_presence_check(settings, mode="forced", progress=_plain(progress), stop_check=stop_check) or {})
    lines = [f"Проверено карточек: {_n(result.get('checked'))} из {_n(result.get('total'))}",
             f"Файл на месте: {_n(result.get('present'))}",
             f"Файла нет: {_n(result.get('missing'))}",
             f"Диск или папка недоступны: {_n(result.get('offline_root'))}",
             f"Путь не читается: {_n(result.get('path_error'))}"]
    if _n(result.get("stale_skipped")):
        lines.append(f"Изменились во время проверки (не учтены): {_n(result['stale_skipped'])}")
    return {"stopped": bool(result.get("cancelled")), "lines": lines}


def _hashes(settings, progress, stop_check, options):
    from core.file_integrity import run_hash_verification
    result = dict(run_hash_verification(settings, mode="idle", progress=_plain(progress), stop_check=stop_check) or {})
    lines = [f"Проверено файлов: {_n(result.get('checked'))} из {_n(result.get('total'))}, прочитано "
             f"{human_size(result.get('bytes_read'))}",
             f"MD5 совпал: {_n(result.get('hash_ok'))}",
             f"Байты не те, что записаны в базе: {_n(result.get('hash_mismatch'))}",
             f"Не прочитались: {_n(result.get('hash_unreadable'))}",
             f"Отложены (файла нет, он занят или диск недоступен): {_n(result.get('hash_deferred'))}"]
    if _n(result.get("hash_mismatch")):
        lines.append("Несовпадения записаны: «Восстановить пропавшие и испорченные» скачает их заново по источникам")
    return {"stopped": bool(result.get("cancelled")), "lines": lines}


def _recovery(settings, progress, stop_check, options):
    from core.file_integrity import run_verified_recovery
    result = dict(run_verified_recovery(settings, mode="idle", progress=_plain(progress), stop_check=stop_check) or {})
    _tags_changed()
    return {"stopped": bool(result.get("cancelled")), "lines": [
        f"Кандидатов (нет файла или байты не те): {_n(result.get('total'))}, разобрано: {_n(result.get('checked'))}",
        f"Восстановлено: {_n(result.get('recovered'))}",
        f"Не восстановлено: {_n(result.get('recovery_failed'))} — из них нет сохранённых источников: "
        f"{_n(result.get('no_source'))}, защищены или заняты: {_n(result.get('blocked'))}, не скачалось или MD5 не "
        f"совпал: {max(0, _n(result.get('recovery_failed')) - _n(result.get('no_source')) - _n(result.get('blocked')))}"]}


def _thumbs(settings, progress, stop_check, options):
    from core.maintenance_tasks import repair_missing_thumbnails
    result = dict(repair_missing_thumbnails(settings, progress=progress, stop_check=stop_check) or {})
    return {"stopped": bool(stop_check()), "lines": [
        f"Карточек: {_n(result.get('checked'))}",
        f"Превью готово: {_n(result.get('created'))}",
        f"Файла нет — превью не из чего: {_n(result.get('missing'))}",
        f"Ошибок: {_n(result.get('errors'))}"]}


def _relocate(apply: bool):
    def work(settings, progress, stop_check, options):
        from core.library_lifecycle import relocate_missing_library_paths
        from core.storage_roots import configured_output_root
        target = configured_output_root(settings)
        if apply:
            from core.storage_roots import managed_output_root_state
            if not managed_output_root_state(settings).available:
                raise RuntimeError(f"Папки рабочей библиотеки нет или диск недоступен: {target} — ничего не изменено")
        progress(f"Ищу файлы карточек в {target} по их пути внутри рабочей библиотеки…")
        result = dict(relocate_missing_library_paths(settings, target, apply=apply) or {})
        if result.get("error"):
            raise RuntimeError(result["error"])
        lines = [f"Рабочая библиотека: {result.get('new_base') or target}",
                 f"Карточек уже здесь: {_n(result.get('already_ok'))}",
                 f"Найдены здесь по старому пути: {_n(result.get('found'))}",
                 f"Не найдены: {_n(result.get('missing_target'))}",
                 f"Путь не из какой-либо рабочей библиотеки: {_n(result.get('no_relative'))}"]
        if result.get("old_base"):
            lines.append(f"Старое место: {result['old_base']}")
        if _n(result.get("duplicates_here")) and not apply:
            lines.append(f"Из них уже есть карточка без тегов, сделанная пересборкой индекса: "
                         f"{_n(result['duplicates_here'])} — при переносе она сольётся с настоящей")
        if apply:
            _tags_changed()
            lines += [f"Переписано путей: {_n(result.get('updated'))}", f"Копия базы: {result.get('backup') or '—'}"]
            if _n(result.get("merged_duplicates")):
                lines.append(f"Слито карточек, сделанных из этих файлов заново: {_n(result['merged_duplicates'])}")
        elif _n(result.get("found")):
            lines.append("Чтобы карточки смотрели сюда, нажмите «Переписать пути»")
        return lines
    return work


# ── the tags ───────────────────────────────────────────────────────────────
def _e621(settings, progress, stop_check, options):
    from core.database.maintenance import repair_e621_tag_metadata
    backup = _backup(settings, progress, "repair_e621_tags")
    progress("Ищу теги e621/e926 с числами и надписями из боковой панели сайта…")
    result = dict(repair_e621_tag_metadata(settings) or {})
    _tags_changed()
    return [f"Копия базы: {backup}",
            f"Исправлено тегов с числом или надписью: {_n(result.get('renamed_links'))}",
            f"Исправлено категорий тегов по сохранённым ответам сайта: {_n(result.get('species_fixed'))}",
            f"Картинок e621/e926 с такими тегами или сохранённым ответом: {_n(result.get('images'))}",
            f"Прочитано сохранённых ответов сайта: {_n(result.get('raw_rows'))}"]


def _general(settings, progress, stop_check, options):
    from core.maintenance_tasks import recheck_general_only_tag_categories
    result = dict(recheck_general_only_tag_categories(settings, create_backup=True, progress=progress,
                                                       stop_check=stop_check) or {})
    if result.get("error"):
        raise RuntimeError(result["error"])
    _tags_changed()
    return {"stopped": bool(result.get("stopped")), "lines": [
        f"Копия базы: {result.get('backup') or '— (нечего было менять)'}",
        f"Наборов тегов сайтов, где все теги «general»: {_n(result.get('found'))}",
        f"Разобрано: {_n(result.get('processed'))} (у сайтов спрошено: {_n(result.get('network_checked'))})",
        f"Исправлено наборов: {_n(result.get('fixed'))}, тегов: {_n(result.get('updated'))}",
        f"Сайт не знает категорий этих тегов: {_n(result.get('no_classified'))}",
        f"Поста больше нет на сайте: {_n(result.get('missing'))}",
        f"Ошибок: {_n(result.get('errors'))}"]}


_SYNC_STATES = {"complete": "весь список", "partial": "часть — в следующий раз продолжит с этого места"}


def _aliases(settings, progress, stop_check, options):
    from core.tag_relations import sync_all_alias_sources
    result = dict(sync_all_alias_sources(settings, progress=progress, stop_check=stop_check) or {})
    lines = [f"Синонимов получено: {_n(result.get('seen'))}, записано: {_n(result.get('written'))}"]
    for site, state in dict(result.get("sites") or {}).items():
        state = dict(state or {})
        status = str(state.get("status") or "")
        words = _SYNC_STATES.get(status) or (f"ошибка: {state.get('error')}" if status == "error" else status)
        lines.append(f"{site}: страниц {_n(state.get('pages'))}, записано {_n(state.get('written'))} — {words}")
    return {"stopped": bool(result.get("stopped")), "lines": lines}


def _sidecars(settings, progress, stop_check, options):
    from core.services.index_service import import_legacy_sidecar_metadata
    _library_in_place(settings, progress)
    result = dict(import_legacy_sidecar_metadata(settings, progress=_index_words(progress), stop_check=stop_check) or {})
    _tags_changed()
    return {"stopped": bool(stop_check()), "lines": [f"Копия базы: {result.get('backup') or '—'}"] + _index_lines(result)}


# ── the jobs, in groups ────────────────────────────────────────────────────
_COPY = "Сначала делает копию базы."
GROUPS = (
    ("db", "База", "База SQLite — сама библиотека: карточки, теги, источники. Файлы картинок лежат отдельно.", (
        Job("optimize", "Оптимизировать базу", "Оптимизация базы",
            "Обновляет статистику, по которой SQLite выбирает, как искать, и переносит журнал WAL в базу. "
            "Ничего не удаляет; секунды.", _optimize, stoppable=False),
        Job("vacuum", "Сжать базу (VACUUM)", "Сжатие базы",
            "Переписывает файл базы без пустых мест, оставшихся от удалённого. " + _COPY + " На большой базе — "
            "минуты; начинается, только когда парсер, подписки и поиск дублей стоят.",
            _vacuum, ask="База будет занята несколько минут — нажмите ещё раз", quiet=True, holds_imports=True,
            stoppable=False),
        Job("backup", "Сделать копию базы", "Копия базы",
            "Полная копия базы в папку резервных копий архива; ей можно вернуть библиотеку, как была.",
            lambda settings, progress, stop_check, options: diag._lines_backup(settings, progress, stop_check),
            stoppable=False),
        Job("integrity_check", "Проверить целостность", "Проверка целостности базы",
            "Сверяет каждую карточку с файлом на диске (отключённый диск отличает от пропавшего файла) и ищет "
            "строки тегов и источников, оставшиеся без картинки. Только читает; на большом архиве — минуты, "
            "остановить на середине нельзя.", _integrity_check, stoppable=False),
        Job("integrity_repair", "Починить безопасное", "Починка целостности базы",
            "Карточки файлов, которых точно нет на диске (диск подключён), уходят в корзину как «файл пропал»; "
            "строки без картинки убираются. Сами файлы не трогает. Скачать заново по источникам можно только до "
            "этого — сначала «Восстановить пропавшие и испорченные»: карточки из корзины оно не берёт. Если "
            "архив переносили, сначала «Переписать пути». " + _COPY,
            _integrity_repair, ask="Карточки пропавших файлов уйдут в корзину — нажмите ещё раз", quiet=True,
            holds_imports=True, stoppable=False),
        Job("reindex", "Пересобрать индекс", "Пересборка индекса базы",
            "Сверяет базу с папками рабочей библиотеки: записывает файлы, которых в базе нет, обновляет "
            "изменённые и удаляет записи о файлах, которых точно нет в её папках, — и в корзине тоже, вместе с их "
            "тегами (поэтому сначала «Восстановить пропавшие и испорченные»). Отключённый диск и карточки, "
            "указывающие за пределы библиотеки, не трогает; если архив переносили — сначала «Переписать пути». "
            + _COPY,
            _reindex, ask="Записи пропавших файлов удалятся вместе с тегами — нажмите ещё раз", danger=True, quiet=True,
            options=(("force", "заново все файлы, а не только новые и изменённые (очень долго)"),
                     ("md5", "считать MD5 файлов (дольше; без него MD5 посчитает «Склеить точные копии»)"))),
    )),
    ("derived", "Похожие картинки и категории тегов",
     "Таблицы, которые программа строит из базы для быстрого поиска. Строятся рядом и заменяют прежние, только "
     "когда готовы; теги и файлы не меняют, копия базы не нужна.", (
        Job("vptree", "Пересобрать дерево похожих", "Пересборка дерева похожих картинок (VP-tree)",
            "По нему ищутся похожие картинки и дубли. Нужно, если «Похожие» и «Дубли» перестали находить то, что "
            "явно похоже.", _derived("vp_tree")),
        Job("categories", "Пересобрать категории тегов", "Пересборка категорий тегов",
            "Кто у картинки персонаж, автор, копирайт — от этого цвет тега и его место в списке. Нужно, если "
            "цвета тегов не сходятся с сайтами.", _derived("image_effective_tag_category")),
    )),
    ("files", "Файлы", "Сами картинки и видео рабочей библиотеки. Исходная папка не меняется никогда.", (
        Job("presence", "Проверить наличие файлов", "Проверка наличия файлов",
            "Смотрит, на месте ли файл каждой карточки, и отличает пропавший файл от отключённого диска. "
            "Ничего не меняет.", _presence),
        Job("hashes", "Проверить MD5 по байтам", "Проверка MD5 по байтам",
            "Читает каждый файл и сверяет его MD5 с записанным в базе — это десятки гигабайт, поэтому идёт "
            "только пока вы не работаете с программой. Ничего не исправляет: несовпадения записывает.",
            _hashes, ask="Прочитает весь архив, это долго — нажмите ещё раз"),
        Job("recovery", "Восстановить пропавшие и испорченные", "Восстановление файлов",
            "Скачивает заново файлы, которых нет или чьи байты не совпали с MD5 (по двум проверкам выше), — "
            "только по сохранённым источникам и только если MD5 скачанного совпал точно; испорченный файл "
            "сначала откладывается в карантин. Нужен интернет; идёт, пока вы не работаете с программой.",
            _recovery, ask="Начнёт скачивать — нажмите ещё раз"),
        Job("thumbs", "Достроить превью", "Превью для всех карточек",
            "Делает превью карточкам, у которых его нет (например, после «Очистить кэш превью»).", _thumbs),
        Job("relocate_find", "Найти файлы после переноса архива", "Поиск файлов после переноса архива",
            "Если архив переносили на другой диск, а карточки указывают на старое место: ищет те же файлы в "
            "текущей рабочей библиотеке по пути внутри неё (по одному имени не угадывает). Только смотрит.",
            _relocate(False), stoppable=False),
        Job("relocate_apply", "Переписать пути", "Перенос путей карточек",
            "Переписывает пути карточек, найденных кнопкой выше, на текущую рабочую библиотеку. " + _COPY,
            _relocate(True), ask="Пути карточек перепишутся — нажмите ещё раз", quiet=True, holds_imports=True,
            stoppable=False),
    )),
    ("tags", "Теги", "Наборы тегов, полученные с сайтов. Новых тегов эти кнопки не придумывают.", (
        Job("general_only", "Переразложить «general»", "Раскладка тегов, оставшихся «general»",
            "Наборы Danbooru, Gelbooru, rule34, e621, ATF, где все теги остались общими (general), — так бывало у "
            "старых версий: спрашивает у сайтов настоящие категории. Нужен интернет; на большом архиве — часы. "
            + _COPY,
            _general, ask="Спросит сайты и перепишет категории — нажмите ещё раз", quiet=True),
        Job("aliases", "Синхронизировать синонимы", "Синонимы тегов сайтов",
            "Загружает с сайтов официальные списки синонимов (rule34.xxx, Gelbooru, Danbooru, e621, ATF); "
            "в следующий раз продолжает с места, где остановилась. Нужен интернет.", _aliases),
        Job("e621", "Исправить теги e621", "Исправление тегов e621",
            "Старые версии записывали теги e621 вместе с числом или надписью из боковой панели сайта "
            "(«horse_231k»): исправляет их и возвращает категории видов. " + _COPY,
            _e621, ask="Перепишет теги e621 — нажмите ещё раз", quiet=True, holds_imports=True, stoppable=False),
        Job("sidecars", "Импортировать старые .tags/.sources", "Импорт старых файлов тегов",
            "Разовый перенос тегов и источников из файлов, которые старые версии клали рядом с картинками. Заодно "
            "сверяет базу с папками, как «Пересобрать индекс». Теги, которые у карточек уже есть, остаются. " + _COPY,
            _sidecars, ask="Добавит теги из старых файлов — нажмите ещё раз", danger=True, quiet=True),
    )),
)
JOBS = {job.id: job for _gid, _title, _lead, jobs in GROUPS for job in jobs}


def _writers_now() -> str:
    """What writes to the library right now ("" — nothing)."""
    from core import page_jobs, subscription_jobs
    from core.parser import jobs as parser_jobs
    ours = page_jobs.current(KIND)
    if ours is not None and ours.running:
        return f"идёт «{ours.label}» — дождитесь конца"
    if parser_jobs.is_running():
        owner = parser_jobs.owner()
        if owner.startswith(parser_jobs.MAINTENANCE_OWNER.split("«")[0]):
            return f"идёт {owner}"
        return "идёт парсер — дождитесь конца или остановите его на странице «Парсер»"
    if subscription_jobs.is_running():
        return "идёт проверка подписок или загрузка граббера"
    others = [task.label for task in page_jobs.running() if task.kind != KIND]
    return f"идёт «{others[0]}»" if others else ""


_START_LOCK = threading.Lock()     # one start at a time: two presses never share the library


def _take_library(title: str) -> str:
    """Take the parser and the subscriptions for a ``quiet`` job: neither starts until it ends.  Returns
    the owner name to give back (``_give_back``), "" when one of them is busy now."""
    from core import subscription_jobs
    from core.parser import jobs as parser_jobs
    owner = parser_jobs.MAINTENANCE_OWNER.format(title)
    if not parser_jobs.claim(owner):
        return ""
    if not subscription_jobs.claim(owner):
        parser_jobs.release(owner)
        return ""
    return owner


def _give_back(owner: str) -> None:
    from core import subscription_jobs
    from core.parser import jobs as parser_jobs
    subscription_jobs.release(owner)
    parser_jobs.release(owner)


# ── the page ───────────────────────────────────────────────────────────────
def _storage_line(settings: dict) -> str:
    from core.database.maintenance import storage_report
    try:
        report = dict(storage_report(settings) or {})
    except Exception as exc:                  # a broken base: the jobs say more; the page still opens
        _LOG.warning("maintenance page: base size not read: %s: %s", type(exc).__name__, exc)
        return ""
    if not report.get("exists"):
        return '<p class="tool-warn">Файла базы нет.</p>'
    return (f'<p class="hint">Сейчас база — {esc(human_size(report.get("size_bytes")))}; сжатие освободит около '
            f'{esc(human_size(report.get("reclaimable_bytes")))}. Версия схемы: {esc(report.get("schema_version"))}.</p>')


def _last_run(job: Job, history: list) -> str:
    entry = next((h for h in history if h.get("label") == job.title), None)
    if entry is None:
        return ""
    first = next(iter(entry.get("lines") or []), "")
    state = "" if entry.get("ok", True) else '<span class="tool-warn">не удалось</span> '
    return (f'<p class="maint-last">Последний раз {esc(diag._when(entry.get("at")))}: {state}'
            f'{esc(first)}</p>')


_QUIET = " Пока идёт, парсер, подписки и загрузки граббера не запускаются."


def _job_html(job: Job, history: list, busy: bool) -> str:
    options = "".join(f'<label class="check"><input type="checkbox" name="{esc(name)}"> {esc(label)}</label>'
                      for name, label in job.options)
    cls = ' class="danger"' if job.danger else ""
    ask = f' data-ask="{esc(job.ask)}"' if job.ask else ""
    return (f'<form class="maint-job" data-form="maint" data-job="{esc(job.id)}" id="job-{esc(job.id)}">'
            f'<div class="maint-go"><button type="submit"{cls}{ask} data-needs-idle{" disabled" if busy else ""}>'
            f'{esc(job.label)}</button></div>'
            f'<div class="maint-what"><p>{esc(job.hint)}{esc(_QUIET) if job.quiet else ""}</p>'
            f'{f"<div class=maint-options>{options}</div>" if options else ""}'
            f'{_last_run(job, history)}<span class="action-status" role="status"></span></div></form>')


def maintenance_page(req: Request) -> Response:
    from core.library_diagnostics import repair_history
    history = repair_history()
    busy = diag._busy()
    groups = []
    for gid, title, lead, jobs in GROUPS:
        extra = _storage_line(req.settings) if gid == "db" else ""
        groups.append(f'<section class="settings-block maint-group" id="maint-{esc(gid)}"><h2>{esc(title)}</h2>'
                      f'<p class="hint">{esc(lead)}</p>{extra}'
                      f'{"".join(_job_html(job, history, busy) for job in jobs)}</section>')
    body = f"""
<section class="tool tool-wide settings diag maint" id="diag">
  <header class="tool-head">
    <h1>Обслуживание</h1>
    <p class="tool-lead">Работа над базой, файлами и тегами — по одной за раз, вместе с проверкой и починкой
    «Диагностики»: общий журнал и «Остановить». Что меняет базу, сначала делает её копию.</p>
    <div class="tool-actions head-actions">
      <button type="button" class="danger" data-action="diag-stop"{"" if diag._can_stop() else " disabled"}>Остановить</button>
      <span class="action-status" role="status"></span>
    </div>
  </header>
  {diag._running_now()}
  <div class="job panel" data-job hidden>
    <div class="job-head"><b class="job-title"></b> <span class="job-status"></span></div>
    <pre class="job-log" tabindex="0" aria-label="Журнал обслуживания"></pre>
  </div>
  <div class="maint-groups">{"".join(groups)}</div>
  {diag._history_html(history)}
</section>"""
    return Response.html(page_frame("Обслуживание", body, active="diag", subnav=False, kind="tool",
                                    body_data={"tool": "diag"}, tabs=diag.tabs("maintenance"), **diag._prefs()))


# ── starting a job ─────────────────────────────────────────────────────────
def maintain(req: Request) -> Response:
    body = req.json()
    job = JOBS.get(str(body.get("action") or ""))
    if job is None:
        return Response.json({"ok": False, "error": "Нет такого действия"}, 400)
    raw = body.get("options") if isinstance(body.get("options"), dict) else {}
    options = {name: bool(raw.get(name)) for name, _label in job.options}
    from webui.settings_store import for_task
    return start_job(job, for_task(req.settings), options)


def start_job(job: Job, settings: dict, options: dict) -> Response:
    """Start ``job`` as the diagnostics' page task (the jobs of «Удаление и сброс» start here too): a ``quiet``
    one takes the parser and the subscriptions for its time, or is refused while they (or another page task)
    write to the library; its lines go to the journal and the history."""
    from core import page_jobs
    held = {"owner": ""}                 # set below, before the task starts

    def run(progress, stop_check):
        from core.library_diagnostics import remember_repair
        from contextlib import nullcontext
        from core.import_pipeline import IMPORT_LOCK
        try:
            try:
                with IMPORT_LOCK if job.holds_imports else nullcontext():
                    out = job.work(settings, progress, stop_check, options)
            except BaseException as exc:
                from core.redaction import sanitize_text
                remember_repair(job.title, [sanitize_text(f"{type(exc).__name__}: {exc}")], ok=False)
                raise
        finally:
            if held["owner"]:
                _give_back(held["owner"])
        lines, stopped = (out.get("lines") or [], bool(out.get("stopped"))) if isinstance(out, dict) else (out, False)
        for line in lines:
            progress(line)
        remember_repair(job.title, (["Остановлено на середине"] if stopped else []) + list(lines))
        return {"lines": lines, "stopped": stopped}

    with _START_LOCK:
        owner = ""
        if job.quiet:
            reason = _writers_now()
            owner = "" if reason else _take_library(job.title)
            if not owner:
                return Response.json({"ok": False, "error": f"Сейчас нельзя: {reason or _writers_now() or 'библиотека занята'}"}, 409)
        held["owner"] = owner
        try:
            task = page_jobs.start(KIND, job.title, run, stoppable=job.stoppable)
        except page_jobs.TaskBusy as exc:
            if owner:
                _give_back(owner)
            return Response.json({"ok": False, "error": str(exc)}, 409)
        except BaseException:
            if owner:                  # the task never ran: nobody else would give the library back
                _give_back(owner)
            raise
    return Response.json({"ok": True, "id": task.id})
