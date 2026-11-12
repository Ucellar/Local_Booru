"""The trash in words and in bulk: why each card is there (the codes ``library_lifecycle.move_to_trash`` writes into
``delete_log`` — the old page «Удалено» and the section «Корзина» of the new window show the same words), and
«Вернуть всё» — many cards back in portions, with a journal and a stop between portions; (v5.44.118) «В корзину
выбранные / все найденные» of the gallery — many cards in, the same way (:func:`trash_many`)."""
from __future__ import annotations

import contextlib
import threading
from pathlib import Path
from typing import Callable

LABELS = {
    "gallery_context_delete": "удалено вручную",
    "post_context_delete": "удалено вручную из просмотра",
    "delete": "удалено (причина не записана)",
    "duplicate_delete": "удалено на «Дублях»",
    "subscription_visual_duplicate": "авто: похожий дубликат подписки",
    "subscription_session_cleanup": "«Удалить скачанные» у подписки",
    "downloader_exact_duplicate": "авто: точный дубликат загрузчика",
    "downloader_exact_copy": "авто: точная копия у старого граббера",
    "downloader_manual_duplicate": "дубликат, удалённый в старом граббере",
    "downloader_blocklist_cleanup": "очистка по блоклисту старого граббера",
    "reimport_deleted_rejected": "авто: снова скачан удалённый файл",
    "restore_exact_duplicate_cleanup": "авто: точная копия после возврата из корзины",
    "exact_md5_auto_normalized": "авто: склеены точные копии",
    "delete_by_tag": "«Удаление и сброс»: по тегу",
    "delete_by_source": "«Удаление и сброс»: по источнику",
    "delete_by_buckets": "«Удаление и сброс»: результаты парсера или скачанное",
    "missing_file": "файл пропал с диска",
    "moved_to_manga": "перенесено в мангу",                 # (v5.44.111) a comic of the gallery: core/comic_manga.py
    # (v5.44.118) «В корзину выбранные» / «В корзину все найденные» of the gallery (:func:`trash_many`): the search the
    # cards were found by is the reason's «what» (``tag_or_source``)
    "gallery_selected": "выбрано в галерее",
    "gallery_found": "все найденные по поиску",
    "unknown": "неизвестно (старая версия программы)",
}
# (v5.44.118) a reason in a word or two — the corner of a tile of the phone's «Корзина» (the sketch E: «вручную»,
# «Дубли», «в мангу»); a reason not here — none
SHORT = {
    "gallery_context_delete": "вручную", "post_context_delete": "вручную", "gallery_selected": "вручную",
    "gallery_found": "по поиску", "duplicate_delete": "«Дубли»", "moved_to_manga": "в мангу",
    "missing_file": "файл пропал", "delete_by_tag": "по тегу", "delete_by_source": "по сайту",
    "delete_by_buckets": "сброс", "subscription_visual_duplicate": "подписка", "subscription_session_cleanup": "подписка",
    "downloader_exact_duplicate": "авто", "downloader_exact_copy": "авто", "downloader_manual_duplicate": "авто",
    "downloader_blocklist_cleanup": "авто", "reimport_deleted_rejected": "авто", "restore_exact_duplicate_cleanup": "авто",
    "exact_md5_auto_normalized": "авто",
}

# SQL (on ``images i``) of why a trashed card is there: the newest line of ``delete_log`` for its file, or
# «missing_file» for a card whose file vanished (``lifecycle='missing'``: nobody deleted it — «Проверка библиотеки»
# writes that line, cards marked by migration m039 have none).
_LAST_LOG = ("SELECT d.{col} FROM delete_log d WHERE d.path = CASE WHEN COALESCE(i.original_media_path,'')<>'' "
             "THEN i.original_media_path ELSE i.path END ORDER BY d.deleted_at DESC, d.id DESC LIMIT 1")
REASON_SQL = (f"(CASE WHEN i.lifecycle='missing' THEN 'missing_file' "
              f"ELSE COALESCE(({_LAST_LOG.format(col='reason')}), 'unknown') END)")
TARGET_SQL = f"COALESCE(({_LAST_LOG.format(col='tag_or_source')}), '')"
WORD = "корзина:"            # the search word of a reason: «корзина:subscription_session_cleanup»


def label(code) -> str:
    key = str(code or "unknown")
    return LABELS.get(key, key)


def short_label(code) -> str:
    """(v5.44.118) The reason in a word or two (:data:`SHORT`), "" — none to say."""
    return SHORT.get(str(code or ""), "")


def reasons_of(settings: dict, ids) -> dict:
    """(v5.44.118) {card: its reason code} of the trashed cards among ``ids`` (a page of the phone's «Корзина»)."""
    from core.database.connection import db
    wanted = [int(x) for x in ids or []]
    out: dict = {}
    if not wanted:
        return out
    with db(settings, readonly=True) as con:
        for start in range(0, len(wanted), 500):
            part = wanted[start:start + 500]
            marks = ",".join("?" for _ in part)
            for row in con.execute(f"SELECT i.id, {REASON_SQL} FROM images i WHERE i.deleted=1 AND i.id IN ({marks})",
                                   part):
                out[int(row[0])] = str(row[1] or "unknown")
    return out


def condition(code: str) -> tuple[str, list]:
    """SQL condition on ``images i``: a trashed card put there for ``code``."""
    return f"(i.deleted=1 AND {REASON_SQL}=?)", [str(code)]


def summary(settings: dict, *, source: str = "all") -> list:
    """[(code, label, cards, bytes)] of the trash (of one site with ``source``), the largest group first."""
    from core.database.connection import db
    where, params = "", []
    host = str(source or "all").lower().replace("www.", "")     # as the gallery search takes it
    if host != "all":
        from core.database.repository import SOURCE_CARDS      # (v5.44.152) asked once, not of every trashed card
        where = " AND " + SOURCE_CARDS
        params = [host]
    with db(settings, readonly=True) as con:
        rows = con.execute(
            f"SELECT {REASON_SQL} AS reason, COUNT(*), COALESCE(SUM(i.size_bytes), 0) FROM images i "
            f"WHERE i.deleted=1 AND i.lifecycle IN ('trash','missing'){where} GROUP BY reason ORDER BY COUNT(*) DESC",
            params).fetchall()
    return [(str(r[0]), label(r[0]), int(r[1]), int(r[2] or 0)) for r in rows]


def reason_of(con, image_id: int) -> tuple[str, str]:
    """(reason code, what it was about — a tag, a source…) of a trashed card."""
    row = con.execute(f"SELECT {REASON_SQL}, {TARGET_SQL} FROM images i WHERE i.id=?", (int(image_id),)).fetchone()
    return (str(row[0]), str(row[1] or "")) if row else ("unknown", "")


# ── «Вернуть всё» ──────────────────────────────────────────────────────────
PORTION = 200          # cards one write of the base takes: other writers wait for one portion, not the whole trash
RESTORE_KIND = "trash_restore"         # its page task (core.page_jobs)
MOVE_KIND = "trash_move"               # (v5.44.118) «В корзину выбранные / все найденные» of the gallery: its page task
_BULK = threading.Lock()               # «Вернуть всё» or a purge of the trash: one at a time (a purge would take the
                                       # cards the return has not reached yet; a return would race the purge's deletes)


_HOLDER = [""]                         # what has it, in words


class TrashBusy(RuntimeError):
    """Another bulk operation of the trash goes on."""


@contextlib.contextmanager
def bulk(what: str):
    """The trash for one bulk operation (``what`` — said to the one refused); TrashBusy while another has it."""
    if not _BULK.acquire(blocking=False):
        raise TrashBusy(f"С корзиной уже работают ({_HOLDER[0] or 'другая операция'}) — дождитесь конца")
    _HOLDER[0] = what
    try:
        yield
    finally:
        _HOLDER[0] = ""
        _BULK.release()


def bulk_busy() -> str:
    """What has the trash now ("" — nothing)."""
    return _HOLDER[0] if _BULK.locked() else ""


def restoring() -> bool:
    """«Вернуть всё» is going: nothing purges the trash meanwhile (the cards it has not reached yet would go)."""
    from core import page_jobs
    task = page_jobs.current(RESTORE_KIND)
    return task is not None and task.running


def _paths(settings: dict, ids: list) -> list:
    from core.database.connection import db
    ph = ",".join("?" for _ in ids)
    with db(settings, readonly=True) as con:
        return [str(r[0]) for r in con.execute(f"SELECT path FROM images WHERE id IN ({ph})", ids)]


def restore_many(settings: dict, ids: list, *, hold=None, progress: Callable[[str], None] | None = None,
                 stop_check: Callable[[], bool] | None = None, busy: Callable[[], str] | None = None) -> dict:
    """Take the trashed cards ``ids`` back (``library_lifecycle.restore_from_trash``) in portions.  ``hold(*paths)``
    — a context in which nothing sends those files; ``busy()`` — why the library cannot be written now ("" — it
    can): checked before each portion, as is ``stop_check()``.  Returns the sums and "stopped" (cards left);
    TrashBusy while a purge of the trash goes on (``library_lifecycle.purge_trash`` takes the same ``bulk``)."""
    with bulk("«Вернуть всё»"):
        return _restore_many(settings, ids, hold=hold, progress=progress, stop_check=stop_check, busy=busy)


def _restore_many(settings: dict, ids: list, *, hold, progress, stop_check, busy) -> dict:
    from core.library_lifecycle import restore_from_trash
    say = progress or (lambda _m: None)
    sums = {"restored": 0, "merged_existing": 0, "errors": 0, "protected_source_skipped": 0, "unblocked_md5": 0}
    ids = [int(x) for x in ids]
    done = 0
    for start in range(0, len(ids), PORTION):
        why = busy() if busy is not None else ""
        if why:
            say(f"Библиотека занята: {why}")
        if why or (stop_check is not None and stop_check()):
            say(f"Остановлено: {done} из {len(ids)} разобрано, остальные {len(ids) - done} — в корзине")
            return {**sums, "cards": len(ids), "stopped": True, "left": len(ids) - done}
        part = ids[start:start + PORTION]
        with (hold(*_paths(settings, part)) if hold is not None else contextlib.nullcontext()):
            result = restore_from_trash(settings, part)
        for key in sums:
            sums[key] += int(result.get(key) or 0)
        done += len(part)
        say(f"{done} из {len(ids)}: возвращено {sums['restored']}"
            + (f", слито с живыми копиями {sums['merged_existing']}" if sums["merged_existing"] else ""))
    lost = sums["errors"]
    if sums["merged_existing"]:
        say(f"Слито с живыми копиями: {sums['merged_existing']} — такой файл уже есть в библиотеке; его теги и "
            "источники добавлены к живой карточке, вторая копия не возвращается")
    if lost:
        say(f"Не вернулось: {lost} — файла нет ни в корзине, ни на прежнем месте, или его не удалось перенести "
            "(подробности — в журнале программы); эти карточки остались в корзине")
    if sums["protected_source_skipped"]:
        say(f"Не тронуто: {sums['protected_source_skipped']} — файл лежит в исходной папке, которую программа не "
            "меняет")
    return {**sums, "cards": len(ids), "stopped": False, "left": 0}


# ── (v5.44.118) «В корзину выбранные / все найденные» of the gallery ─────────
def trash_many(settings: dict, ids: list, *, reason: str, target: str = "", hold=None,
               progress: Callable[[str], None] | None = None, stop_check: Callable[[], bool] | None = None,
               busy: Callable[[], str] | None = None) -> dict:
    """Move the live cards ``ids`` into the trash (``library_lifecycle.move_to_trash``; ``reason`` — a code of
    :data:`LABELS`, ``target`` — what it was about: the search) in portions: a copy of the base first (none — nothing
    is moved), then a portion at a time under the import lock with its files held (``hold(*paths)``); ``busy()`` and
    ``stop_check()`` before each portion, as «Вернуть всё».  A card already in the trash or gone is passed over.
    Returns the sums and "stopped" (cards left in place); TrashBusy while «Вернуть всё» or a purge of the trash goes
    on (one bulk operation of the trash at a time: ``bulk``)."""
    with bulk("«В корзину»"):
        return _trash_many(settings, ids, reason=reason, target=target, hold=hold, progress=progress,
                           stop_check=stop_check, busy=busy)


def _live_rows(settings: dict, ids: list) -> list:
    from core.database.connection import db
    rows: list = []
    with db(settings, readonly=True) as con:
        for start in range(0, len(ids), 500):
            part = ids[start:start + 500]
            marks = ",".join("?" for _ in part)
            found = {int(r["id"]): dict(r) for r in con.execute(
                f"SELECT id, path, deleted, lifecycle FROM images WHERE deleted=0 AND id IN ({marks})", part)}
            rows += [found[i] for i in part if i in found]
    return rows


def _trash_many(settings: dict, ids: list, *, reason, target, hold, progress, stop_check, busy) -> dict:
    from core.database.connection import db_path
    from core.import_pipeline import IMPORT_LOCK
    from core.library_lifecycle import force_backup_database, move_to_trash
    say = progress or (lambda _m: None)
    wanted = list(dict.fromkeys(int(x) for x in ids))
    rows = _live_rows(settings, wanted)
    sums = {"trashed": 0, "files": 0, "errors": 0, "skipped": 0, "changed": 0}
    passed = len(wanted) - len(rows)
    if passed:
        say(f"Не тронуто {passed}: этих карточек уже нет в библиотеке (в корзине или удалены)")
    if not rows:
        return {**sums, "cards": 0, "stopped": False, "left": 0, "passed": passed, "backup": ""}
    say("Копирую базу…")
    backup = force_backup_database(settings, reason, auto=True)
    if not backup and db_path(settings).exists():
        raise RuntimeError("Копия базы не создана (места на диске?) — ничего не перенесено")
    if backup:
        say(f"Копия базы: {Path(backup).name} (в резервных копиях архива)")      # its name: the journal shows no paths
    done = 0
    for start in range(0, len(rows), PORTION):
        why = busy() if busy is not None else ""
        if why:
            say(f"Библиотека занята: {why}")
        if why or (stop_check is not None and stop_check()):
            say(f"Остановлено: {done} из {len(rows)} разобрано, остальные {len(rows) - done} — на месте")
            return {**sums, "cards": len(rows), "stopped": True, "left": len(rows) - done, "passed": passed,
                    "backup": backup}
        part = rows[start:start + PORTION]
        paths = [str(r["path"]) for r in part]
        with IMPORT_LOCK, (hold(*paths) if hold is not None else contextlib.nullcontext()):
            result = move_to_trash(settings, part, reason=reason, tag_or_source=target, make_backup=False)
        sums["trashed"] += int(result.get("trashed_records") or 0)
        sums["files"] += int(result.get("trashed_files") or 0)
        sums["errors"] += int(result.get("errors") or 0)
        sums["skipped"] += int(result.get("protected_source_skipped") or 0)
        sums["changed"] += int(result.get("changed_meanwhile") or 0)
        done += len(part)
        say(f"{done} из {len(rows)}: в корзине {sums['trashed']}")
    if sums["changed"]:
        say(f"Не тронуто {sums['changed']}: их перенесли или убрали в корзину, пока шло")
    if sums["skipped"]:
        say(f"Не тронуто {sums['skipped']}: это файлы исходной папки или вне папки библиотеки — они не меняются")
    if sums["errors"]:
        say(f"Ошибок: {sums['errors']} — эти карточки остались на месте (подробности — в журнале программы)")
    say("Из корзины всё можно вернуть: «Галерея → Корзина»")
    return {**sums, "cards": len(rows), "stopped": False, "left": 0, "passed": passed, "backup": backup}
