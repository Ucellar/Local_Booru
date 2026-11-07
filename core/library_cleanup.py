"""What «Удаление и сброс» (Диагностика) deletes besides «Удалить всё» (``core/library_reset.py``): the cards of
a tag or of a source, the parser's results, what the program downloaded — into the trash, where they can be
restored from — and the cards whose files are gone (out of the base).

The user's source folder is never touched: ``move_to_trash`` refuses anything outside the library's own output
folder (and inside the source folder, wherever that lies).  ``find_cards`` says what a choice takes; the page
shows it and keeps the cards it showed (their numbers and paths: ``shown_keys``), and ``trash_cards`` takes
exactly those of them that still answer the choice (a card that lost the tag meanwhile, or went to the trash
already, stays where it is) — in parts, each under the import lock and with the files' streams held, so an
import or a video playing never meets a half-moved card.
"""
from __future__ import annotations

import logging
from contextlib import nullcontext
from pathlib import Path
from typing import Callable, Iterable, Mapping

_LOG = logging.getLogger("local_booru.cleanup")

CHUNK = 500                         # cards moved under one hold of the import lock

WHAT = ("tag", "source", "parser", "downloads")

# Where a tag or a source is looked for.  «найденные» and «Брак» are the gallery's sections of those names.
SCOPES = (
    ("all", "во всей библиотеке"),
    ("parser", "только в результатах парсера"),
    ("downloads", "только в скачанном"),
    ("found", "только в найденных"),
    ("no_match", "только в Браке"),
)
PARSER_MODES = (
    ("all", "все результаты парсера"),
    ("found", "найденные и частичные"),
    ("no_match", "только Брак"),
)
DOWNLOAD_MODES = (
    ("all", "всё скачанное"),
    ("grabber", "«Галерея (booru)», подписки и расширение"),
    ("found", "старый загрузчик: найденные"),
    ("partial_match", "старый загрузчик: частичные"),
    ("no_match", "старый загрузчик: не найденные"),
)
MODES = {"parser": PARSER_MODES, "downloads": DOWNLOAD_MODES}

_PARSER = {"all": ("found", "partial_match", "no_match"), "found": ("found", "partial_match"), "no_match": ("no_match",)}
_FOUND = ("found", "partial_match", "downloaded_found", "downloaded_partial_match")
_NO_MATCH = ("no_match", "downloaded_no_match")
# why a card is in the trash (delete_log): a tag or a source the user does not want stays out of the library
# when the trash is emptied (``purge_trash``); results cleared to start over do not
REASONS = {"tag": "delete_by_tag", "source": "delete_by_source", "parser": "delete_by_buckets",
           "downloads": "delete_by_buckets"}


class CleanupError(ValueError):
    """A choice the page cannot make (an unknown kind, an empty tag)."""


def downloads_folder(settings: dict) -> Path:
    """Where the grabber, the subscriptions and the extension keep what they download."""
    from core.storage_roots import configured_output_root
    return Path(configured_output_root(settings)) / "subscriptions"


def _in_downloads(settings: dict) -> Callable[[str], bool]:
    """Is a path in the folder of the downloads (by the path: the bucket of such a card is not «downloaded»)."""
    from core.storage_roots import path_is_within_root
    folder = str(downloads_folder(settings))
    name = downloads_folder(settings).name.lower()
    return lambda path: name in path.lower() and path_is_within_root(path, folder)


def _downloaded_test(settings: dict, mode: str = "all") -> Callable[[str, str], bool]:
    inside = _in_downloads(settings)
    if mode == "grabber":
        return lambda bucket, path: inside(path)
    if mode in ("found", "partial_match", "no_match"):
        return lambda bucket, path: bucket == "downloaded_" + mode
    return lambda bucket, path: bucket.startswith("downloaded") or inside(path)


def _parser_test(settings: dict, mode: str = "all") -> Callable[[str, str], bool]:
    """The parser's results: its buckets, and not a download whose folder happens to be named so."""
    buckets, inside = _PARSER[mode], _in_downloads(settings)
    return lambda bucket, path: bucket in buckets and not inside(path)


def _scope_test(settings: dict, scope: str) -> Callable[[str, str], bool]:
    if scope == "parser":
        return _parser_test(settings)
    if scope == "downloads":
        return _downloaded_test(settings)
    if scope == "found":
        return lambda bucket, path: bucket in _FOUND
    if scope == "no_match":
        return lambda bucket, path: bucket in _NO_MATCH
    return lambda bucket, path: True


def source_key(value: str) -> tuple[str, str]:
    """("url", the address) for a whole address, ("host", the site) for a site name as the library keeps it."""
    text = str(value or "").strip()
    if "://" in text:
        return "url", text.lower()
    from core.media_utils import normalize_host
    return "host", normalize_host(text)


def normalized(what: str, value: str) -> str:
    """The value as it is looked for: a tag as the library writes it, a site without «www.»."""
    if what == "tag":
        from core.tag_utils import normalize_tag
        return normalize_tag(value)
    if what == "source":
        return source_key(value)[1]
    return str(value or "")


def path_key(path) -> int:
    """A short stand-in for a card's path in this run of the program: a shown card is the same card only with
    the same number and the same path (a number of a card deleted for good is given to the next new one)."""
    return hash(str(path or ""))


def shown_keys(rows: Iterable[dict]) -> dict:
    """{card number: path key} of the cards a look showed — what ``trash_cards`` may move."""
    return {int(r["id"]): path_key(r.get("path")) for r in rows}


_COLUMNS = "i.id, i.path, i.file_name, i.bucket, i.size_bytes"


def find_cards(settings: dict, what: str, value: str, scope: str = "all") -> list[dict]:
    """The live cards a choice takes, in the order of their paths: [{id, path, file_name, bucket, size_bytes}].

    ``what``: "tag" (``value`` — the tag), "source" (a site or a whole address), "parser" and "downloads"
    (``value`` — the mode, see ``MODES``); ``scope`` narrows a tag or a source (``SCOPES``)."""
    from core.database.connection import db
    if what == "tag":
        tag = normalized("tag", value)
        if not tag:
            raise CleanupError("Впишите тег")
        sql = (f"SELECT DISTINCT {_COLUMNS} FROM images i JOIN image_tags it ON it.image_id=i.id "
               "JOIN tags t ON t.id=it.tag_id WHERE i.deleted=0 AND t.normalized_name=?")
        params: list = [tag]
        test = _scope_test(settings, scope)
    elif what == "source":
        kind, key = source_key(value)
        if not key:
            raise CleanupError("Впишите сайт или адрес")
        column = "LOWER(s.url)" if kind == "url" else "LOWER(s.host)"
        sql = (f"SELECT DISTINCT {_COLUMNS} FROM images i JOIN image_sources isrc ON isrc.image_id=i.id "
               f"JOIN sources s ON s.id=isrc.source_id WHERE i.deleted=0 AND {column}=?")
        params = [key]
        test = _scope_test(settings, scope)
    elif what == "parser":
        if value not in _PARSER:
            raise CleanupError("Нет такого выбора")
        buckets = _PARSER[value]
        sql = f"SELECT {_COLUMNS} FROM images i WHERE i.deleted=0 AND i.bucket IN ({','.join('?' for _ in buckets)})"
        params = list(buckets)
        test = _parser_test(settings, value)
    elif what == "downloads":
        if value not in dict(DOWNLOAD_MODES):
            raise CleanupError("Нет такого выбора")
        if value in ("found", "partial_match", "no_match"):
            sql, params = f"SELECT {_COLUMNS} FROM images i WHERE i.deleted=0 AND i.bucket=?", ["downloaded_" + value]
        else:                       # the folder of the downloads is told by the path
            sql = f"SELECT {_COLUMNS} FROM images i WHERE i.deleted=0 AND (i.bucket LIKE 'downloaded%' OR i.path LIKE ?)"
            params = [f"%{downloads_folder(settings).name}%"]
        test = _downloaded_test(settings, value)
    else:
        raise CleanupError("Нет такого выбора")
    with db(settings, readonly=True) as con:
        rows = [dict(r) for r in con.execute(sql + " ORDER BY i.path COLLATE NOCASE", params)]
    return [r for r in rows if test(str(r.get("bucket") or ""), str(r.get("path") or ""))]


def describe(what: str, value: str, scope: str = "all") -> str:
    """The choice in words, for the journal, the history and the trash («why it was deleted») — cleaned of keys:
    a typed address can carry one (rule 14)."""
    from core.redaction import sanitize_text
    if what == "tag":
        text = f"тег «{normalized('tag', value)}»"
    elif what == "source":
        kind, key = source_key(value)
        text = f"адрес «{key}»" if kind == "url" else f"сайт «{key}»"
    else:
        return dict(MODES.get(what, ())).get(value, sanitize_text(str(value)))
    return sanitize_text(text + ("" if scope == "all" else ", " + dict(SCOPES).get(scope, scope)))


def overview(settings: dict) -> dict:
    """How many live cards (and bytes) each mode of the parser's results and of the downloads takes:
    {"parser": {mode: (cards, bytes)}, "downloads": {mode: (cards, bytes)}} — the same as ``find_cards``."""
    from core.database.connection import db
    inside = _in_downloads(settings)
    by_bucket: dict = {}
    folder_by_bucket: dict = {}         # cards in the folder of the downloads, by their bucket
    with db(settings, readonly=True) as con:
        for row in con.execute("SELECT bucket, COUNT(*), COALESCE(SUM(size_bytes),0) FROM images WHERE deleted=0 GROUP BY bucket"):
            by_bucket[str(row[0] or "")] = (int(row[1] or 0), int(row[2] or 0))
        for row in con.execute("SELECT bucket, path, size_bytes FROM images WHERE deleted=0 AND path LIKE ?",
                               (f"%{downloads_folder(settings).name}%",)):
            if inside(str(row[1] or "")):
                n, size = folder_by_bucket.get(str(row[0] or ""), (0, 0))
                folder_by_bucket[str(row[0] or "")] = (n + 1, size + int(row[2] or 0))

    def total(table: dict, buckets) -> tuple:
        picked = [table.get(b, (0, 0)) for b in buckets]
        return sum(p[0] for p in picked), sum(p[1] for p in picked)

    parser = {}
    for mode, buckets in _PARSER.items():
        all_of, in_folder = total(by_bucket, buckets), total(folder_by_bucket, buckets)
        parser[mode] = (all_of[0] - in_folder[0], all_of[1] - in_folder[1])
    old = [b for b in by_bucket if b.startswith("downloaded")]
    old_total, folder_total = total(by_bucket, old), total(folder_by_bucket, list(folder_by_bucket))
    folder_old = total(folder_by_bucket, old)           # counted once: in the folder and downloaded
    downloads = {"all": (old_total[0] + folder_total[0] - folder_old[0], old_total[1] + folder_total[1] - folder_old[1]),
                 "grabber": folder_total}
    for mode in ("found", "partial_match", "no_match"):
        downloads[mode] = by_bucket.get("downloaded_" + mode, (0, 0))
    return {"parser": parser, "downloads": downloads}


def trash_cards(settings: dict, shown: Mapping[int, int], *, what: str, value: str, scope: str = "all",
                progress: Callable[[str], None] = lambda _m: None, stop_check: Callable[[], bool] = lambda: False,
                hold: Callable | None = None) -> dict:
    """Move the cards a look showed (``shown`` — ``shown_keys`` of it) that still answer the choice into the trash.

    A copy of the base first (none — nothing is moved).  ``hold(paths)`` — a context in which nothing sends
    those files (the web server's ``FILE_STREAMS.holding``).  Returns {"lines", "stopped", "trashed", "errors"}."""
    from core.database import journal
    from core.database.connection import db_path
    from core.import_pipeline import IMPORT_LOCK
    from core.library_lifecycle import force_backup_database, move_to_trash
    label = describe(what, value, scope)
    rows = [r for r in find_cards(settings, what, value, scope)
            if shown.get(int(r["id"])) == path_key(r.get("path"))]
    left_out = len(shown) - len(rows)
    lines = []
    if left_out:
        lines.append(f"Не тронуто {left_out}: после показа они уже не подходят (в корзине или без этого признака)")
    if not rows:
        return {"lines": lines + ["В корзину нечего переносить"], "stopped": False, "trashed": 0, "errors": 0}
    reason = REASONS.get(what, "delete_by_buckets")
    progress("Копирую базу…")
    backup = force_backup_database(settings, reason, auto=True)
    if not backup and db_path(settings).exists():
        raise RuntimeError("Копия базы не создана (места на диске?) — ничего не удалено")
    progress(f"Копия базы: {backup}")
    trashed = errors = skipped = files = changed = 0
    stopped = False
    with journal.operation(settings, reason, target_type=what, target_id=label, payload={"cards": len(rows)}):
        for start in range(0, len(rows), CHUNK):
            if stop_check():
                stopped = True
                break
            part = rows[start:start + CHUNK]
            paths = [str(r["path"]) for r in part]
            with IMPORT_LOCK, (hold(paths) if hold else nullcontext()):
                result = move_to_trash(settings, part, reason=reason, tag_or_source=label, make_backup=False)
            trashed += int(result.get("trashed_records") or 0)
            files += int(result.get("trashed_files") or 0)
            errors += int(result.get("errors") or 0)
            skipped += int(result.get("protected_source_skipped") or 0)
            changed += int(result.get("changed_meanwhile") or 0)
            progress(f"В корзине: {trashed} из {len(rows)}")
    lines.append(f"В корзину: {trashed} из {len(rows)} ({label}); файлов перенесено: {files}")
    if stopped:
        lines.append(f"Остановлено: {len(rows) - trashed - errors - skipped - changed} остались на месте")
    if changed:
        lines.append(f"Не тронуто {changed}: их перенесли или убрали в корзину, пока шло")
    if skipped:
        lines.append(f"Не тронуто {skipped}: это файлы исходной папки или вне папки библиотеки — они не меняются")
    if errors:
        lines.append(f"Ошибок: {errors} — см. журнал программы")
    lines.append(f"Копия базы до удаления: {backup}")
    return {"lines": lines, "stopped": stopped, "trashed": trashed, "errors": errors}


def clear_missing_records(settings: dict, *, progress: Callable[[str], None] = lambda _m: None,
                          stop_check: Callable[[], bool] = lambda: False) -> dict:
    """«Удалить записи без файлов»: cards whose file is gone for sure leave the base (a drive or folder that is
    not there now is not «gone»; when most of them look gone, nothing is deleted; cards in the trash stay —
    emptying the trash removes them and keeps their pictures out).  A copy of the base first."""
    from core.database.connection import db_path
    from core.database.storage import TooManyMissing, cleanup_missing
    from core.library_lifecycle import force_backup_database
    progress("Копирую базу…")
    backup = force_backup_database(settings, "cleanup_missing_records")
    if not backup and db_path(settings).exists():
        raise RuntimeError("Копия базы не создана (места на диске?) — ничего не удалено")
    progress(f"Копия базы: {backup}")
    progress("Проверяю файлы карточек…")
    try:
        removed = cleanup_missing(settings, stop_check=stop_check)
    except TooManyMissing as exc:
        return {"lines": [f"Ничего не удалено: {exc}. Проверьте, подключён ли диск и та ли папка библиотеки "
                          "в настройках"], "stopped": False, "removed": 0}
    if removed is None:
        return {"lines": ["Остановлено до удаления — ничего не удалено"], "stopped": True, "removed": 0}
    return {"lines": [f"Удалено записей без файлов: {removed}", f"Копия базы до удаления: {backup}"],
            "stopped": False, "removed": removed}
