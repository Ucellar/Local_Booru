"""The trash in words and in bulk: why each card is there (the codes ``library_lifecycle.move_to_trash`` writes into
``delete_log`` — the old page «Удалено» and the section «Корзина» of the new window show the same words), and
«Вернуть всё» — many cards back in portions, with a journal and a stop between portions."""
from __future__ import annotations

import contextlib
import threading
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
    "unknown": "неизвестно (старая версия программы)",
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


def condition(code: str) -> tuple[str, list]:
    """SQL condition on ``images i``: a trashed card put there for ``code``."""
    return f"(i.deleted=1 AND {REASON_SQL}=?)", [str(code)]


def summary(settings: dict, *, source: str = "all") -> list:
    """[(code, label, cards, bytes)] of the trash (of one site with ``source``), the largest group first."""
    from core.database.connection import db
    where, params = "", []
    host = str(source or "all").lower().replace("www.", "")     # as the gallery search takes it
    if host != "all":
        where = (" AND EXISTS(SELECT 1 FROM image_sources x JOIN sources s ON s.id=x.source_id "
                 "WHERE x.image_id=i.id AND s.host=?)")
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
