"""(v5.44.118) «Диагностика → Файлы библиотеки» (the sketch E, the owner: «норм мне нравится»): one check of the files
of the cards — «Проверить файлы» — and one repair of them — «Починить файлы (N)».

Before, the files had five checks and four repairs on two pages («Проверка» and «Обслуживание»), and the order to
press them in was the person's to know («сначала…», «если архив переносили…»).  Now:

* :func:`check` only looks (cards and their files stay as they are): whether each live card's file is on the disk
  (``file_integrity.run_presence_check`` — a disk that is not connected is not a loss), whether files of cards that
  point elsewhere are in the archive's new place (``library_lifecycle.relocate_missing_library_paths`` without
  ``apply``: the same path inside the library's own folder, never a guess by a name), with ``md5`` — whether each
  file's bytes are what the base says (``file_integrity.run_hash_verification``: the whole archive is read, only while
  the person does not work with the program; a mismatch recorded by an earlier reading counts too), and it makes the
  thumbnails missing from the cache (the gallery's size).  Its plan — what the repair will do and to how many — is the
  report «files» of ``library_diagnostics`` with its ``stamp``.
* :func:`repair` does exactly that plan, in that order, each card only while it is as the check saw it: a copy of the
  base; the paths of the cards found in the new place (``relocate_missing_library_paths(only=…)``); the files to
  download again by the links kept (``run_verified_recovery(image_ids=…)``: only bytes with the card's MD5 are taken,
  a damaged file goes to quarantine first); the cards whose files are still gone — into the trash as «файл пропал»
  (``invariants.mark_missing``; not when most of the library looks gone: ``storage.MASS_MISSING_SHARE``).  A damaged
  file that was not downloaded again stays where it is.  A disk that is not connected and a path that does not read
  are never touched.
"""
from __future__ import annotations

import logging
import re
import secrets
import time
from typing import Callable

_LOG = logging.getLogger("local_booru.file_check")
KIND = "files"                     # the report of ``library_diagnostics`` (REPORT_FILES)
SAMPLES = 50                       # cards of each kind the page names («какие»)
_PRESENCE = re.compile(r"Presence: (\d+)/(\d+); present=\d+; missing=(\d+)")
_HASH = re.compile(r"Hash integrity: (\d+)/(\d+); ok=\d+; mismatch=(\d+)")
_RECOVERY = re.compile(r"Recovery: (\d+)/(\d+); recovered=(\d+); failed=(\d+)")
_WARNING = re.compile(r"Recovery (?:metadata|page) warning \[([^\]]*)\]: (\w+)")


def _words(say: Callable[[str], None]) -> Callable[[str], None]:
    """The lines of ``file_integrity`` («Presence: 250/1000; …») in words; their first and last lines — not at all."""
    def words(text) -> None:
        text = str(text)
        m = _PRESENCE.match(text)
        if m:
            say(f"Файлы на месте: проверено {m[1]} из {m[2]}, нет на диске — {m[3]}")
            return
        m = _HASH.match(text)
        if m:
            say(f"MD5 по байтам: прочитано {m[1]} из {m[2]}, не совпал — {m[3]}")
            return
        m = _RECOVERY.match(text)
        if m:
            say(f"Скачиваю заново: разобрано {m[1]} из {m[2]}, вернулось {m[3]}, не вышло {m[4]}")
            return
        m = _WARNING.match(text)
        if m:
            say(f"Сайт {m[1]} не ответил как надо ({m[2]}) — оттуда файл не скачан")
            return
        if text.startswith(("Presence: ", "Hash integrity: ", "Recovery")):
            # the technical lines (the downloader's words, links, files) — the program's journal, not the page's
            _LOG.info("files: %s", text)
            return
        say(text)
    return words


def _plural(n: int, forms: tuple) -> str:
    rest10, rest100 = n % 10, n % 100
    form = forms[0] if rest10 == 1 and rest100 != 11 else forms[1] if 2 <= rest10 <= 4 and not 12 <= rest100 <= 14 \
        else forms[2]
    return f"{n} {form}"


def name_of(path) -> str:
    """The file's (or folder's) own name — the pages and the journal name files so, never by their whole paths (a
    Windows path read on another system too)."""
    return re.split(r"[\\/]", str(path or "").rstrip("\\/"))[-1]


def place_of(root) -> str:
    """A disk or a folder not connected, in words: «Диск F:» — or «Папка «name»» (not its whole path)."""
    text = str(root or "")
    drive = re.match(r"([A-Za-z]:)(?:[\\/]|$)", text)
    return f"Диск {drive.group(1).upper()}" if drive else f"Папка «{name_of(text)}»" if name_of(text) else "Диск"


# ── what is wrong with the files ───────────────────────────────────────────
def _problems(settings: dict) -> list[dict]:
    """The live cards whose file the last readings found not in place or not right (``image_file_integrity`` rows of
    their present path and MD5)."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        rows = con.execute(
            """SELECT i.id, i.path, i.file_name, lower(trim(COALESCE(i.hash_md5,''))) AS md5, f.presence_status,
                      f.root_path, CASE WHEN lower(COALESCE(f.expected_md5,''))=lower(trim(COALESCE(i.hash_md5,'')))
                                   THEN COALESCE(f.hash_status,'') ELSE '' END AS hash_status
               FROM images i JOIN image_file_integrity f ON f.image_id=i.id AND f.checked_path=i.path
               WHERE i.deleted=0 AND (f.presence_status<>'present'
                     OR (f.hash_status='mismatch' AND lower(COALESCE(f.expected_md5,''))=lower(trim(COALESCE(i.hash_md5,'')))))
               ORDER BY i.id""").fetchall()
    return [dict(r) for r in rows]


def _with_links(settings: dict, ids: list) -> set:
    """Which of the cards ``ids`` have a link kept to download their file again (what ``file_integrity`` asks:
    the site's answer, a post of an MD5 alias, a source of the card — not a site the program never asks)."""
    from core.database.connection import db
    from core.network import is_permanently_disabled_url
    found: set = set()
    with db(settings, readonly=True) as con:
        for start in range(0, len(ids), 500):
            part = [int(x) for x in ids[start:start + 500]]
            marks = ",".join("?" for _ in part)
            links: dict = {}
            for sql in (f"SELECT image_id, file_url FROM raw_metadata WHERE image_id IN ({marks})",
                        f"SELECT image_id, post_url FROM raw_metadata WHERE image_id IN ({marks})",
                        f"SELECT image_id, source_url FROM image_site_md5_aliases WHERE image_id IN ({marks})",
                        f"SELECT x.image_id, s.url FROM image_sources x JOIN sources s ON s.id=x.source_id "
                        f"WHERE x.image_id IN ({marks})"):
                try:
                    for image_id, link in con.execute(sql, part):
                        links.setdefault(int(image_id), []).append(str(link or "").strip())
                except Exception:          # an old base without one of the tables: the others still say
                    continue
            for image_id, kept in links.items():
                if any(link and not is_permanently_disabled_url(link) for link in kept):
                    found.add(image_id)
    return found


def _sample(rows: list) -> list:
    return [{"id": int(r["id"]), "path": str(r.get("path") or ""),
             "file_name": str(r.get("file_name") or "") or name_of(r.get("path"))} for r in rows[:SAMPLES]]


def _thumbs(settings: dict, *, say, stop) -> dict:
    """The thumbnails of the gallery's size that are not in the cache — made (as «Достроить превью» did)."""
    from pathlib import Path

    from core.database.connection import db
    from core.image_safe import cached_thumbnail_path, card_thumbnail_size, safe_thumbnail_path
    size = card_thumbnail_size(settings)
    with db(settings, readonly=True) as con:
        paths = [Path(str(r[0] or "")) for r in con.execute(
            "SELECT path FROM images WHERE deleted=0 ORDER BY indexed_at DESC").fetchall()]
    made = had = gone = errors = 0
    for n, path in enumerate(paths, 1):
        if stop():
            break
        if cached_thumbnail_path(path, *size):
            had += 1
            continue
        if not path.is_file():
            gone += 1
            continue
        try:
            if safe_thumbnail_path(str(path), *size):
                made += 1
            else:
                errors += 1
        except Exception:                 # a file that does not open as a picture: the others go on
            errors += 1
        if n % 500 == 0:
            say(f"Превью: просмотрено {n} из {len(paths)}, достроено {made}")
    return {"checked": len(paths), "made": made, "had": had, "no_file": gone, "errors": errors}


def check(settings: dict, *, md5: bool = False, progress: Callable[[str], None] | None = None,
          stop_check: Callable[[], bool] | None = None) -> dict:
    """«Проверить файлы»: what is wrong with the files of the cards and what «Починить файлы» will do about it (the
    report «files»: counts, samples, ``plan``, ``stamp``).  Changes no card and no file (the thumbnails' cache —
    made where it lacks one)."""
    from core.database.storage import MASS_MISSING_SHARE
    from core.file_integrity import run_hash_verification, run_presence_check
    from core.library_lifecycle import relocate_missing_library_paths
    from core.storage_roots import configured_output_root
    say = progress or (lambda _m: None)
    stop = stop_check or (lambda: False)
    words = _words(say)
    started = time.time()
    report: dict = {"stamp": secrets.token_hex(8), "md5": bool(md5), "cancelled": False}

    def end(**more) -> dict:
        report.update(more)
        report["took_seconds"] = round(time.time() - started, 1)
        report["finished_at"] = time.time()
        return report

    say("Смотрю, на месте ли файл каждой карточки (отключённый диск — не пропажа)…")
    presence = run_presence_check(settings, mode="forced", progress=words, stop_check=stop)
    report["total"] = int(presence.get("total") or 0)
    report["present"] = int(presence.get("present") or 0)
    if presence.get("cancelled"):
        return end(cancelled=True)
    say("Ищу файлы карточек в новом месте архива (по пути внутри рабочей библиотеки)…")
    target = configured_output_root(settings)
    moved = dict(relocate_missing_library_paths(settings, target, apply=False, all_matches=True) or {})
    if moved.get("error"):                # (its words name the folder: the page and the journal say it without it)
        report["library_gone"] = ("Папки рабочей библиотеки (output) нет на диске — подключите диск архива; по такой "
                                  "проверке ничего не чинится")
    relocate = [(int(i), str(old), str(new)) for i, old, new in moved.get("matches") or []]
    report["old_base"] = str(moved.get("old_base") or "")
    report["new_base"] = str(moved.get("new_base") or target)
    if stop():
        return end(cancelled=True)
    if md5:
        say("Сверяю MD5 каждого файла с базой — читаю весь архив, пока вы не работаете с программой…")
        hashed = run_hash_verification(settings, mode="idle", progress=words, stop_check=stop)
        report["hash"] = {k: int(hashed.get(k) or 0) for k in ("total", "checked", "hash_ok", "hash_mismatch",
                                                               "hash_unreadable", "hash_deferred", "bytes_read")}
        if hashed.get("cancelled"):
            return end(cancelled=True)
    say("Достраиваю превью, которых нет…")
    report["thumbs"] = _thumbs(settings, say=say, stop=stop)
    if stop():
        return end(cancelled=True)

    moving = {i for i, _old, _new in relocate}
    rows = [r for r in _problems(settings) if int(r["id"]) not in moving]
    missing = [r for r in rows if r["presence_status"] == "missing"]
    damaged = [r for r in rows if r["presence_status"] == "present" and r["hash_status"] == "mismatch"]
    offline = [r for r in rows if r["presence_status"] == "offline_root"]
    unreadable = [r for r in rows if r["presence_status"] == "path_error"]
    fixable = [r for r in missing + damaged if re.fullmatch(r"[0-9a-f]{32}", r["md5"] or "")]
    linked = _with_links(settings, [int(r["id"]) for r in fixable])
    recover = [r for r in fixable if int(r["id"]) in linked]
    live = max(report["total"], 1)
    refused = live >= 20 and len(missing) > MASS_MISSING_SHARE * live
    roots: dict = {}
    for r in offline:
        root = str(r.get("root_path") or "")
        roots[root] = roots.get(root, 0) + 1
    report.update({
        "missing": len(missing), "damaged": len(damaged), "offline_root": len(offline), "path_error": len(unreadable),
        "relocate": len(relocate),
        "recover": len(recover), "recover_missing": sum(1 for r in recover if r["presence_status"] == "missing"),
        "recover_damaged": sum(1 for r in recover if r["presence_status"] != "missing"),
        "trash_refused": bool(refused and missing),
        "offline_roots": [{"root": root, "files": n} for root, n in sorted(roots.items(), key=lambda x: -x[1])],
        "samples": {"missing": _sample(missing), "damaged": _sample(damaged), "path_error": _sample(unreadable),
                    "relocate": [{"id": i, "path": old, "new": new, "file_name": name_of(new)}
                                 for i, old, new in relocate[:SAMPLES]]},
        "plan": {"relocate": [i for i, _old, _new in relocate],
                 "recover": [int(r["id"]) for r in recover],
                 "damaged": [int(r["id"]) for r in damaged],
                 "trash": [] if refused else [[int(r["id"]), str(r["path"])] for r in missing]},
    })
    if report.get("library_gone"):        # the library's own folder is not there: nothing is repaired by guess
        report["plan"] = {"relocate": [], "recover": [], "damaged": [], "trash": []}
    report["fix"] = len(report["plan"]["relocate"]) + len({*report["plan"]["recover"],
                                                           *(i for i, _p in report["plan"]["trash"])})
    lines = [f"Карточек: {report['total']}, файл на месте — {report['present']}",
             f"Нет на диске: {len(missing)}",
             f"Смотрят в старое место архива, а файлы есть в новом: {len(relocate)}",
             f"Испорчены (байты не совпали с MD5): {len(damaged)}" + ("" if md5 else " — по прошлой сверке MD5"),
             f"Диск не подключён: {len(offline)}, путь не читается: {len(unreadable)} — их программа не трогает",
             f"Превью достроено: {report['thumbs']['made']}"]
    if missing:
        lines.append("Нет на диске, например: " + "; ".join(r["file_name"] or name_of(r["path"]) for r in missing[:5]))
    if report.get("library_gone"):
        lines.append(report["library_gone"])
    return end(lines=lines)


# ── the repair: exactly the plan ───────────────────────────────────────────
def repair(settings: dict, report: dict, *, progress: Callable[[str], None] | None = None,
           stop_check: Callable[[], bool] | None = None) -> dict:
    """«Починить файлы»: exactly the plan of ``report`` (:func:`check`), in its order.  Returns {"lines", "stopped",
    "relocated", "recovered", "trashed"}."""
    from core.database.invariants import mark_missing
    from core.database.storage import MASS_MISSING_SHARE
    from core.file_integrity import run_verified_recovery
    from core.import_pipeline import IMPORT_LOCK
    from core.library_lifecycle import force_backup_database, relocate_missing_library_paths
    from core.storage_roots import configured_output_root
    say = progress or (lambda _m: None)
    stop = stop_check or (lambda: False)
    plan = report.get("plan") if isinstance(report.get("plan"), dict) else {}
    relocate = [int(x) for x in plan.get("relocate") or []]
    recover = [int(x) for x in plan.get("recover") or []]
    trash = [(int(x[0]), str(x[1])) for x in plan.get("trash") or [] if isinstance(x, (list, tuple)) and len(x) == 2]
    out = {"relocated": 0, "recovered": 0, "trashed": 0, "stopped": False, "lines": []}
    lines = out["lines"]
    if not (relocate or recover or trash):
        lines.append("Чинить нечего — проверка ничего не нашла")
        return out
    step = iter(range(1, 4))                # the journal numbers the steps that are done: 1, 2…
    say("Копирую базу…")
    backup = force_backup_database(settings, "files_repair")
    if not backup:
        raise RuntimeError("Копия базы не создана (базы нет или диск не дал записать) — ничего не изменено")
    lines.append(f"Копия базы: {name_of(backup)} (в резервных копиях архива)")

    if relocate:
        say(f"{next(step)}. Переписываю пути карточек на новое место архива: {len(relocate)}…")
        with IMPORT_LOCK:
            moved = dict(relocate_missing_library_paths(settings, configured_output_root(settings), apply=True,
                                                        only=relocate, make_backup=False) or {})
        if moved.get("error"):            # (its words name the folders: the journal says it without them)
            raise RuntimeError("Пути не переписаны: папки рабочей библиотеки нет на диске — ничего не изменено")
        out["relocated"] = int(moved.get("updated") or 0)
        lines.append(f"Переписано путей: {out['relocated']} из {len(relocate)} — на новое место архива")
        if int(moved.get("merged_duplicates") or 0):
            lines.append(f"Слито карточек, сделанных из этих файлов заново: {int(moved['merged_duplicates'])}")
    if stop():
        out["stopped"] = True
        lines.append("Остановлено после путей: файлы не скачивались, в корзину ничего не ушло")
        return out

    if recover:
        say(f"{next(step)}. Скачиваю заново по сохранённым ссылкам: {len(recover)} — только если MD5 совпадёт…")
        got = dict(run_verified_recovery(settings, image_ids=recover, mode="idle", progress=_words(say),
                                         stop_check=stop) or {})
        out["recovered"] = int(got.get("recovered") or 0)
        failed = int(got.get("recovery_failed") or 0)
        lines.append(f"Скачано заново: {out['recovered']} из {len(recover)}"
                     + (f"; не вышло: {failed} (нет ссылок: {int(got.get('no_source') or 0)}, заняты или "
                        f"недоступны: {int(got.get('blocked') or 0)}, не скачалось или MD5 не совпал: "
                        f"{max(0, failed - int(got.get('no_source') or 0) - int(got.get('blocked') or 0))})"
                        if failed else ""))
        if int(got.get("stale_skipped") or 0):
            lines.append(f"Не тронуто {int(got['stale_skipped'])}: после проверки файл вернулся или карточку меняли")
        if got.get("cancelled"):
            out["stopped"] = True
            lines.append("Остановлено на скачивании: в корзину ничего не ушло")
            return out
    if stop():
        out["stopped"] = True
        lines.append("Остановлено: в корзину ничего не ушло")
        return out

    if trash:
        from core.database.connection import db
        with db(settings, readonly=True) as con:
            live = int(con.execute("SELECT COUNT(*) FROM images WHERE deleted=0").fetchone()[0] or 0)
        if live >= 20 and len(trash) > MASS_MISSING_SHARE * live:
            lines.append(f"В корзину ничего не убрано: файлов нет у {len(trash)} карточек — больше половины "
                         "библиотеки; похоже, папку подменили или очистили")
        else:
            say(f"{next(step)}. " + ("Что не вернулось" if recover else "Пропавшие") + " — в корзину как «файл пропал»…")
            out["trashed"] = mark_missing(settings, trash, hold=lambda *_paths: IMPORT_LOCK)
            lines.append(f"В корзину как «файл пропал»: {out['trashed']} (оттуда их можно вернуть)")
            if len(trash) - out["trashed"]:
                lines.append(f"Не тронуто {len(trash) - out['trashed']}: файл вернулся (скачан или снова на месте) "
                             "или карточку меняли после проверки")
    damaged = {int(x) for x in plan.get("damaged") or []}
    left = [r for r in _problems(settings) if int(r["id"]) in damaged and r["hash_status"] == "mismatch"]
    if left:
        lines.append(f"Испорченные файлы, которые не скачались заново, остались на месте: {len(left)}")
    return out


def plan_words(report: dict) -> list:
    """What «Починить файлы» will do, step by step, in words (the page shows it before the press)."""
    steps = []
    relocate = len(report.get("plan", {}).get("relocate") or [])
    if relocate:
        steps.append(f"{relocate} — пути карточек переписываются на новое место архива")
    recover = int(report.get("recover") or 0)
    if recover:
        parts = []
        if int(report.get("recover_missing") or 0):
            parts.append(_plural(int(report["recover_missing"]), ("пропавший", "пропавших", "пропавших")))
        if int(report.get("recover_damaged") or 0):
            parts.append(_plural(int(report["recover_damaged"]), ("испорченный", "испорченных", "испорченных")))
        steps.append(f"{recover} ({' и '.join(parts)}) — скачиваются заново по сохранённым ссылкам, только если "
                     "MD5 совпадёт; испорченный файл сначала откладывается в карантин")
    trash = len(report.get("plan", {}).get("trash") or [])
    if trash and int(report.get("recover_missing") or 0):
        steps.append(f"что из {_plural(trash, ('пропавшего', 'пропавших', 'пропавших'))} не вернулось — в корзину "
                     "как «файл пропал» (оттуда можно вернуть)")
    elif trash:                           # nothing of them is downloaded again: no links kept
        steps.append(f"{_plural(trash, ('пропавший', 'пропавших', 'пропавших'))} — в корзину как «файл пропал»: "
                     "ссылок, чтобы скачать заново, нет (из корзины можно вернуть)")
    elif report.get("trash_refused") and int(report.get("missing") or 0):
        steps.append("в корзину — ничего: файлов нет у большей части библиотеки, похоже, папку подменили или "
                     "очистили")
    return steps
