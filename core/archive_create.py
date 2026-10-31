"""«Настройки → Папки → Создать архив» (v5.40): a program whose base and settings still lie in «Документах»
(``Documents/Local_Booru``) gets its archive — one folder ``Local_Booru_Archive``: ``settings`` (the base, the settings,
the cache, the journals) beside ``output`` (the library's files, which are there already).  The whole archive can then
be moved to another drive («Перенести на другой диск…», ``core/archive_move.py``) or connected on another computer.

Two steps, because the base is open while the program runs:

1. **Now** (:func:`request`): where the archive will be — the archive of the library's files (:func:`target`: its
   ``settings`` folder); refused there when an archive is in it already («Подключить существующий архив…» is for that),
   when another archive is chosen for the next start, when a reset of the library is ordered, when the base lies apart
   (``sqlite_db_folder``), when the drive has no room.  The order (:data:`REQUEST`) goes to the runtime folder in
   «Документах».
2. **At the next start, before anything opens the base** (:func:`finish`, app.py, after one more question): every file
   of «Документы\\Local_Booru» is copied into the archive's ``settings`` (``settings`` → ``config``; the journals,
   runtime notes, backups and reports → ``output``; the base, the cache and the rest under their own names), the copy
   of the base is checked (``quick_check``), the archive's settings name its folders, and the pointer files name the
   archive — the program starts again and opens it.  A copy made so far is marked (:data:`MARK`): a finish that failed
   goes on over it next time.

«Документы\\Local_Booru» stays as it was: if anything goes wrong, nothing is lost and the program opens it as before.
(Until v5.40 only the old window's settings page made an archive — at once, copying the base while the program wrote
to it; what was written after the copy stayed in «Документах».)
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Callable

from core.archive_move import (BASE_EXTS, BASE_TAILS, MAIN_BASE, MARGIN, TMP, MoveError, _base_free, _copy, _inside,
                               _key, _long, _read_json, _write_json, human)

_LOG = logging.getLogger("local_booru.archive_create")

REQUEST = "archive_create_request.json"     # in the runtime folder of «Документы»
MARK = ".local_booru_create.json"           # in the archive's settings folder while the copy is made
ANSWERS = ("yes", "cancel", "later")
TO_OUTPUT = ("logs", "runtime", "backups", "reports")   # «Документы»: beside the base; the archive: in settings/output
LOOSE = ("cache", "logs", "reports")        # copied when they can be: a file that cannot be read does not stop it
SKIP = {REQUEST.lower(), "thumbs.db", "desktop.ini"}


class CreateError(Exception):
    """The archive cannot be made — said in words the person can act on."""


# ── where ──────────────────────────────────────────────────────────────────
def available() -> bool:
    """The data is in «Документах» (no archive yet) and nothing is wrong with the place this run opened."""
    from core import paths
    return not paths.USING_SEPARATE_STORAGE and not paths.WORKSPACE_UNAVAILABLE


def target(settings: dict) -> Path:
    """The ``settings`` folder of the archive to be made: beside the ``output`` the library's files go to (shown
    without making any folder).  An old «Local_Booru_Output» gets a ``Local_Booru_Archive`` beside it."""
    from core.paths import OUTPUT_FOLDER_NAME
    from core.storage_roots import configured_output_root
    output = Path(configured_output_root(settings))
    root = output.parent if output.name.lower() == "output" else output.parent / OUTPUT_FOLDER_NAME
    return root / "settings"


def _request_file() -> Path:
    from core.paths import RUNTIME_DIR
    return Path(RUNTIME_DIR) / REQUEST


def pending() -> dict | None:
    """The archive ordered in this run or an earlier one and not made yet, else None."""
    if not available():
        return None
    order = _read_json(_request_file())
    return order if order and order.get("target") else None


def _ours(settings_dir: Path) -> bool:
    """The archive's settings folder holds a copy this program began (its mark)."""
    mark = _read_json(Path(settings_dir) / MARK)
    from core import paths
    return bool(mark) and _key(mark.get("source") or "") == _key(paths.DATA_DIR)


def _has_archive(settings_dir: Path) -> bool:
    return (settings_dir / "config" / "app_settings.json").is_file() or (settings_dir / "db" / MAIN_BASE).is_file()


def _size(folder: Path) -> int:
    total = 0
    for top, _dirs, names in os.walk(_long(folder)):
        for name in names:
            try:
                total += os.stat(os.path.join(top, name)).st_size
            except OSError:
                pass
    return total


def _free(path: Path) -> int:
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _check(settings: dict, settings_dir: Path) -> None:
    """Why the archive cannot be made there (raises CreateError), checked when ordered and again at the start."""
    from core import archive_switch, library_reset, paths
    if not available():
        raise CreateError("Программа уже работает с архивом — создавать нечего")
    if archive_switch.pending() is not None:
        raise CreateError("Для следующего запуска уже выбран другой архив — «Остаться на открытом», чтобы создать "
                          "новый")
    if library_reset.pending_reset() is not None:
        raise CreateError("Заказан полный сброс библиотеки — сначала выполните или отмените его")
    if str(settings.get("sqlite_db_folder") or "").strip():
        raise CreateError("База лежит отдельно («sqlite_db_folder» в настройках) — архив с базой внутри так не "
                          "сделать")
    data = Path(paths.DATA_DIR)
    if _inside(settings_dir, data) or _inside(data, settings_dir.parent):
        raise CreateError(f"Архив {settings_dir.parent} и папка программы в «Документах» ({data}) вложены друг в "
                          "друга — укажите другую папку для файлов библиотеки")
    if _has_archive(settings_dir) and not _ours(settings_dir):
        raise CreateError(f"В {settings_dir.parent} уже есть архив Local Booru — его можно подключить: «Подключить "
                          "существующий архив…»")
    need = _size(data)
    try:
        free = _free(settings_dir)
    except OSError as exc:
        raise CreateError(f"Диск {settings_dir.parent} недоступен: {exc}") from exc
    if free < need + MARGIN:
        raise CreateError(f"На диске не хватает места: нужно {human(need)} и ещё {human(MARGIN)} про запас, свободно "
                          f"{human(free)}")


def request(settings: dict) -> dict:
    """Order the archive (made at the next start): {"ok": True, "archive": its folder} or {"ok": False, "error": why}."""
    settings_dir = target(settings)
    try:
        _check(settings, settings_dir)
    except CreateError as exc:
        return {"ok": False, "error": str(exc)}
    from core import paths
    order = {"source": str(paths.DATA_DIR), "target": str(settings_dir), "archive": str(settings_dir.parent),
             "at": time.time(), "error": ""}
    _write_json(_request_file(), order)
    return {"ok": True, "archive": order["archive"]}


def cancel() -> bool:
    """The order goes (a copy begun by a failed start stays — its folder is said on the page)."""
    try:
        _request_file().unlink()
        return True
    except FileNotFoundError:
        return False


def describe(order: dict) -> str:
    """The question asked at the start, in words."""
    from core import paths
    when = time.strftime("%d.%m.%Y %H:%M", time.localtime(float(order.get("at") or 0)))
    return (f"Создание архива ({when}):\n{paths.DATA_DIR}\n→ {order.get('target')}\n\nСейчас программа скопирует в "
            "архив базу, настройки, кэш и журналы и откроется с ним: весь архив — одна папка "
            f"{order.get('archive')}, рядом с файлами библиотеки. Папка в «Документах» останется как есть.\n\n"
            "Создать архив сейчас?")


def answer(order: dict, said: str) -> str:
    """The person's answer at the start: "cancel" takes the order back, "later" leaves it for the next start."""
    if said == "cancel":
        cancel()
    return said if said in ANSWERS else "later"


# ── the copy, at the start ────────────────────────────────────────────────
def _place(rel: Path) -> Path:
    """Where a file of «Документы\\Local_Booru» goes inside the archive's ``settings``."""
    if len(rel.parts) < 2:
        return rel
    first = rel.parts[0].lower()
    if first == "settings":
        return Path("config", *rel.parts[1:])
    if first in TO_OUTPUT:
        return Path("output", *rel.parts)
    return rel


def _is_base(rel: Path) -> bool:
    name = rel.name.lower()
    return any(name.endswith(ext + tail) for ext in BASE_EXTS for tail in BASE_TAILS)


def _files(data: Path) -> list:
    """[(relative path, size)] of the regular files of «Документы\\Local_Booru» (its orders and half-copied files left
    out; links to folders not followed)."""
    out, stack = [], [Path()]
    while stack:
        rel_dir = stack.pop()
        try:
            entries = list(os.scandir(_long(data / rel_dir)))
        except FileNotFoundError:
            continue
        for entry in entries:
            rel = rel_dir / entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(rel)
                    continue
                if not entry.is_file() or entry.name.lower() in SKIP or entry.name.endswith(TMP):
                    continue
                out.append((rel, int(entry.stat().st_size)))
            except OSError:
                continue
    return sorted(out)


def finish(order: dict, settings: dict, progress: Callable | None = None, log: Callable | None = None) -> dict:
    """The second step: {"done": True, "archive": …} — the program must start again (the pointer names the archive);
    {"done": False, "error": why} — nothing switched, «Документы» open as before (the order stays with its reason)."""
    say = progress or (lambda *_a, **_k: None)
    note = log or (lambda _m: None)
    try:
        return _finish(order, dict(settings or {}), say, note)
    except Exception as exc:  # noqa: BLE001 - whatever it is, the program must open «Документы» as before
        from core.redaction import sanitize_text
        reason = sanitize_text(f"{exc}" if isinstance(exc, CreateError) else f"{type(exc).__name__}: {exc}")
        _LOG.error("archive not created: %s", reason, exc_info=not isinstance(exc, CreateError))
        note(f"Архив не создан: {reason}")
        try:
            _write_json(_request_file(), dict(order, error=reason))
        except OSError:
            pass
        return {"done": False, "error": reason}


def _finish(order: dict, settings: dict, say: Callable, note: Callable) -> dict:
    from core import paths
    from core.settings import write_archive_settings
    settings_dir = Path(order["target"])
    if _key(settings_dir) != _key(target(settings)):
        raise CreateError(f"Файлы библиотеки теперь в другом месте, чем когда архив заказывали ({settings_dir.parent})"
                          " — закажите его снова в «Настройках»")
    _check(settings, settings_dir)
    data = Path(paths.DATA_DIR)
    try:
        _base_free(data / "db" / MAIN_BASE)        # no other copy of the program has it open
    except MoveError as exc:
        raise CreateError(str(exc)) from exc
    settings_dir.mkdir(parents=True, exist_ok=True)
    _write_json(settings_dir / MARK, {"source": str(data), "at": time.time(),
                                      "note": "Архив Local Booru, который делается сейчас. Не удаляйте этот файл, "
                                              "пока программа не откроется с архивом."})
    files = _files(data)
    total = sum(size for _rel, size in files)
    note(f"создание архива: {data} → {settings_dir}, {len(files)} файлов, {human(total)}")
    done = [0]

    def counted(size: int) -> None:
        done[0] += size
        say(f"Копирую в архив: {human(done[0])} из {human(total)}", done[0], total)
    # the bases first (what matters most, while there is room), then the settings, then the rest
    for rel, _size in sorted(files, key=lambda item: (not _is_base(item[0]), item[0].parts[0].lower() != "settings")):
        try:
            _copy(data / rel, settings_dir / _place(rel), lambda: False, counted)
        except OSError as exc:
            if getattr(exc, "errno", None) == 28 or getattr(exc, "winerror", None) == 112:
                raise CreateError(f"На диске {settings_dir.parent} кончилось место") from exc
            if rel.parts[0].lower() not in LOOSE or len(rel.parts) < 2:
                raise CreateError(f"Файл {rel} не копируется: {exc}") from exc
            note(f"создание архива: {rel} не скопирован ({exc})")
    main = settings_dir / "db" / MAIN_BASE
    if (data / "db" / MAIN_BASE).is_file():
        say("Проверяю копию базы…", 0, 0)
        con = None
        try:
            con = sqlite3.connect(str(main), timeout=30)
            word = str(con.execute("PRAGMA quick_check(1)").fetchone()[0])     # the first problem is enough
        except sqlite3.DatabaseError as exc:
            raise CreateError(f"Копия базы не читается: {exc}") from exc
        finally:
            if con is not None:
                con.close()
        if word != "ok":
            raise CreateError(f"Проверка копии базы: {word}")
    for folder in ("config", "db", "cache", "output/logs", "output/runtime", "output/backups", "output/reports"):
        (settings_dir / folder).mkdir(parents=True, exist_ok=True)
    (settings_dir.parent / "output").mkdir(parents=True, exist_ok=True)
    write_archive_settings(settings, settings_dir)
    if paths.normalize_archive_settings_root(settings_dir) is None:
        raise CreateError(f"Архив в {settings_dir.parent} не читается после копирования")
    paths.write_workspace_pointer(settings_dir)
    try:
        (settings_dir / MARK).unlink()
    except OSError as exc:  # the archive opens anyway; the mark only says a copy was being made
        note(f"создание архива: отметка копии не убрана ({exc})")
    cancel()
    note(f"создание архива: готово, {settings_dir.parent}")
    return {"done": True, "archive": str(settings_dir.parent)}
