"""«Удалить всё» (Диагностика → Удаление и сброс): the working library starts over — its folders of pictures
(found, partial_match, no_match, downloads, trash, pixiv), the base with the tags and the sources, the cache
and, if asked, the subscriptions with the folder of everything downloaded.  The user's source folder is never
touched: only what lies inside the library's own output folder is removed, and a folder that holds the source
folder is left.  The base is copied into the archive's backups first.

The reset is done at the next start of the program, before anything opens the base or the files: while the
program runs, the gallery, the web server and the workers hold them open (on Windows a held file is not
removed, and half a reset is worse than none).  The page asks for it (``request_reset``) and can take it back
(``cancel_reset``); ``app.py`` calls ``perform_pending_reset`` first thing, and that asks the person once more
(``confirm``: «yes» / «cancel» / «later») — an order can be days old.  The request remembers which library and
base it was made for: a program that starts with another archive does not reset that one.

The base is moved aside before anything is deleted: on Windows a base that another copy of the program holds
open cannot be moved, so then nothing is deleted and the reset waits for a start when the base is free.  Once
the copy is made, the request says «deleting» (and where the copy is): a start after the program was stopped
half-way through finishes the reset instead of taking the missing base for a drive not connected.  Once the
folders and the old base are gone, it says «base_gone»: the folders are never deleted twice (a new library may
have grown in them meanwhile), and a base found in the place of the old one is a new one — it is left alone.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Callable

_LOG = logging.getLogger("local_booru.reset")
FOLDERS = ("found", "partial_match", "no_match", "downloads", "trash", "pixiv")
SUBSCRIPTIONS_FOLDER = "subscriptions"
TITLE = "Удалить всё"                  # the history of «Диагностика» names the reset so
DELETING = "deleting"                  # the request's phase once the copy of the base is made
BASE_GONE = "base_gone"                # … and once the library's folders and the old base are deleted
CLOSED = "closed"                      # nothing to do: a request file that could not be removed says so
_ASIDE = ".resetting"                  # the base moved aside: <base>.resetting (+ -wal, -shm)
_PARTS = ("", "-wal", "-shm")
ANSWERS = ("yes", "cancel", "later")


def _request_file() -> Path:
    from core.paths import RUNTIME_DIR
    return Path(RUNTIME_DIR) / "library_reset_request.json"


def _result_file() -> Path:
    from core.paths import RUNTIME_DIR
    return Path(RUNTIME_DIR) / "library_reset_last.json"


def _places(settings: dict) -> dict:
    from core.database.connection import db_path
    from core.storage_roots import configured_output_root
    return {"output_root": str(configured_output_root(settings)), "db": str(db_path(settings))}


def _write_json(path: Path, data: dict) -> None:
    """Whole or not at all: a request cut in half is not read as an instruction."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def request_reset(settings: dict, *, include_subscriptions: bool) -> dict:
    """Ask for the reset at the next start; returns the request (it says whether the base and the library's
    folder were there: at the start they are expected there again — a drive not connected is not a reset)."""
    places = _places(settings)
    request = {"at": time.time(), "include_subscriptions": bool(include_subscriptions), **places,
               "db_existed": Path(places["db"]).exists(), "output_existed": Path(places["output_root"]).exists()}
    _write_json(_request_file(), request)
    return request


def _read_request() -> tuple[str, dict | None]:
    """("none" | "ok" | "closed" | "unreadable", the request).  A file held for a moment (an antivirus) is read
    again; one that stays unreadable is not an instruction — and not a reason to touch anything either."""
    path = _request_file()
    for attempt in range(3):
        try:
            if not path.is_file():
                return "none", None
            data = json.loads(path.read_text(encoding="utf-8"))
            break
        except (OSError, ValueError) as exc:
            if attempt == 2:
                _LOG.warning("library reset request not read (%s): nothing is done with it", exc)
                return "unreadable", None
            time.sleep(0.2)
    if not isinstance(data, dict):
        return "unreadable", None
    return ("closed" if data.get("phase") == CLOSED else "ok"), data


def pending_reset() -> dict | None:
    """The reset asked for and not done yet (its ``phase`` says how far it got), None when there is none."""
    status, data = _read_request()
    return data if status == "ok" else None


def started(request: dict | None) -> bool:
    """The reset has begun (the copy is made): it is finished at the next start, not asked about or taken back."""
    return bool(request) and request.get("phase") in (DELETING, BASE_GONE)


def cancel_reset() -> bool:
    """Take the request back; True when there was one.  A file that cannot be removed (held for a moment) is
    marked «closed» instead — never left as an instruction."""
    if pending_reset() is None:
        return False
    _close_request()
    return True


def _close_request() -> None:
    path = _request_file()
    for attempt in range(5):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            _LOG.warning("library reset request not removed yet: %s", exc)
            time.sleep(0.2 * (attempt + 1))
    _write_json(path, {"phase": CLOSED, "at": time.time()})


def last_result() -> dict | None:
    try:
        data = json.loads(_result_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_result(result: dict) -> None:
    try:
        _write_json(_result_file(), result)
    except OSError as exc:
        _LOG.warning("library reset result not written: %s", exc)


def describe_request(request: dict) -> str:
    """The question asked at the start, in words."""
    when = time.strftime("%d.%m.%Y %H:%M", time.localtime(float(request.get("at") or 0)))
    subs = "вместе с подписками и всем, что они скачали" if request.get("include_subscriptions") else "подписки останутся"
    return (f"На странице «Удаление и сброс» заказан полный сброс библиотеки ({when}; {subs}).\n\n"
            "Будут удалены папки библиотеки с картинками (найденное, Брак, скачанное, корзина), база с тегами, "
            "источниками и запретами «не добавлять снова» и кэш. Копия базы останется в резервных копиях архива. "
            "Исходная папка не трогается.\n\nУдалить всё сейчас?")


# ── what goes ──────────────────────────────────────────────────────────────
def _source_inside(settings: dict, target: Path) -> bool:
    from core.source_protection import is_inside, source_root
    source = source_root(settings)
    return source is not None and is_inside(source, target)


def plan(settings: dict, *, include_subscriptions: bool = True) -> dict:
    """What a reset of this library would remove now (for the page): {"remove": [paths], "kept": [paths that
    hold the source folder], "base": the base file, "cache": the cache folder}."""
    from core.paths import CACHE_DIR
    places = _places(settings)
    out = Path(places["output_root"])
    names = FOLDERS + ((SUBSCRIPTIONS_FOLDER,) if include_subscriptions else ())
    remove, kept = [], []
    for name in names:
        target = out / name
        if target.exists():
            (kept if _source_inside(settings, target) else remove).append(str(target))
    return {"remove": remove, "kept": kept, "base": places["db"], "cache": str(CACHE_DIR)}


def _long(path: Path) -> str:
    """Windows: the «\\\\?\\» form, so a file deeper than 260 characters (subscription folders) is reached too."""
    text = str(path)
    if os.name == "nt" and path.is_absolute() and not text.startswith("\\\\?\\"):
        return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text
    return text


def _rmtree(path: str) -> None:
    """``shutil.rmtree`` that passes over an entry gone by itself meanwhile (an antivirus): the walk goes on."""
    def skip(exc) -> None:
        if not isinstance(exc, FileNotFoundError):
            raise exc
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=lambda _func, _path, exc: skip(exc))
    else:
        shutil.rmtree(path, onerror=lambda _func, _path, info: skip(info[1]))


def _remove_tree(target: Path, attempts: int = 6) -> None:
    """A folder of the library, retrying a file Windows holds for a moment (an antivirus, a preview); raises
    when the folder is still there in the end (it is not reported as removed)."""
    for attempt in range(max(1, attempts)):
        try:
            _rmtree(_long(target))
        except OSError as exc:
            transient = getattr(exc, "winerror", None) in (32, 33) or getattr(exc, "errno", None) == 13
            if not transient or attempt >= attempts - 1:
                raise
            time.sleep(0.2 * (attempt + 1))
            continue
        if not target.exists():
            return
    raise OSError(f"папка удалена не вся: {target}")


# ── the base aside ─────────────────────────────────────────────────────────
def _aside(base: Path, part: str = "", stamp: str = "") -> Path:
    return Path(str(base) + _ASIDE + (f".{stamp}" if stamp else "") + part)


def _move_base_aside(base: Path) -> str:
    """Move the base with its -wal/-shm to ``<base>.resetting``; "" when done (or there is no base), else why not.

    An older base left aside by a reset that was cut short is kept under another name, never overwritten."""
    if not base.exists():
        return ""                       # no base, or it is aside already (a reset cut short goes on)
    if any(_aside(base, part).exists() for part in _PARTS):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        _LOG.warning("library reset: an older base left aside is kept as %s", _aside(base, "", stamp))
        for part in _PARTS:
            if _aside(base, part).exists():
                os.replace(_aside(base, part), _aside(base, part, stamp))
    moved = []
    try:
        for part in _PARTS:
            source = Path(str(base) + part)
            if source.exists():
                os.replace(source, _aside(base, part))
                moved.append(part)
    except OSError as exc:              # held open (another copy of the program): put back what moved
        for part in reversed(moved):
            try:
                os.replace(_aside(base, part), Path(str(base) + part))
            except OSError as back:
                _LOG.error("library reset: %s not put back: %s", _aside(base, part), back)
        return f"{type(exc).__name__}: {exc}"
    return ""


def _put_base_back(base: Path) -> None:
    for part in _PARTS:
        if _aside(base, part).exists() and not Path(str(base) + part).exists():
            os.replace(_aside(base, part), Path(str(base) + part))


def _cards_in(path: Path) -> list | None:
    """[live cards, cards in the trash] of a base file, None when it cannot be read."""
    import sqlite3
    try:
        con = sqlite3.connect(str(path), timeout=10)        # a plain path: «#», «?», «%» break a file: address
        try:
            row = con.execute("SELECT SUM(deleted=0), SUM(deleted<>0) FROM images").fetchone()
        finally:
            con.close()
        return [int(row[0] or 0), int(row[1] or 0)]
    except sqlite3.Error as exc:
        _LOG.warning("library reset: cards of %s not counted: %s", path, exc)
        return None


# ── the reset ──────────────────────────────────────────────────────────────
@contextlib.contextmanager
def _one_copy_at_a_time():
    """Yields True when this copy of the program holds the reset: two copies started together never reset (and
    put the base back) at once.  An operating-system lock — it goes away with the process that held it."""
    path = _request_file().with_name("library_reset.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    held = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except OSError:
            held = False
        yield held
    finally:
        if held and os.name == "nt":
            try:
                import msvcrt
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError as exc:
                _LOG.warning("library reset lock not released (it goes with the process): %s", exc)
        os.close(fd)


def perform_pending_reset(settings: dict, *, confirm: Callable[[dict], str],
                          log: Callable[[str], None] | None = None) -> dict | None:
    """Carry out the reset asked for, if any (called at the start of the program, before the base is
    opened).  ``confirm(request)`` asks the person: "yes" — reset now, "cancel" — drop the request, anything
    else — not now (the request stays).  Returns what happened, None when nothing was asked; {"busy": True}
    when another copy of the program is doing it right now.  Never raises: a failure is said and the program
    starts with what is there."""
    say = log or (lambda message: _LOG.info(message))
    state = {"phase": ""}
    base = None
    try:
        with _one_copy_at_a_time() as mine:
            if not mine:
                say("Сброс библиотеки сейчас выполняет другая копия программы")
                return {"done": False, "busy": True, "error": "сброс выполняет другая копия программы"}
            base = Path(_places(settings)["db"])
            status, request = _read_request()
            if status == "unreadable":          # not an instruction — and nothing is put back or deleted either
                return _finish({"done": False, "waiting": True, "error": "заказ сброса не прочитан — ничего не сделано"},
                               say, remember=False)
            if status != "ok":
                _tidy_aside(base, closed=status == "closed", say=say)
                return None
            state["phase"] = str(request.get("phase") or "")
            return _perform(settings, request, say, confirm, state)
    except Exception as exc:            # the start of the program goes on
        _LOG.exception("library reset failed")
        if base is not None and state["phase"] not in (DELETING, BASE_GONE):     # not deleting yet: the base goes back
            try:
                _put_base_back(base)
            except OSError as back:
                _LOG.error("library reset: the base not put back: %s", back)
        return _finish({"done": False, "error": f"сброс прерван ошибкой: {type(exc).__name__}: {exc}"}, say)


def _tidy_aside(base: Path, *, closed: bool, say) -> None:
    """No reset to do.  A base left aside by a finished reset (``closed``: its file could not be deleted then) is
    deleted now; one left aside with no reset asked at all (the request was lost) goes back to its place."""
    if closed:
        for part in _PARTS:
            try:
                _unlink_patiently(_aside(base, part))
            except OSError as exc:              # still held: the mark stays, the next start tries again
                _LOG.warning("library reset: the old base %s is still there: %s", _aside(base, part), exc)
                return
        try:
            _request_file().unlink(missing_ok=True)
        except OSError as exc:
            _LOG.warning("library reset: the closed request is still there: %s", exc)
        return
    if not base.exists() and _aside(base).exists():
        _put_base_back(base)
        say(f"Сброс библиотеки не заказан, а база была отложена — она возвращена на место: {base}")


def _unlink_patiently(path: Path, attempts: int = 6) -> None:
    """Delete a file, waiting a little for one held for a moment (an antivirus reading a big base)."""
    for attempt in range(max(1, attempts)):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if attempt >= attempts - 1:
                raise
            _LOG.warning("library reset: %s not deleted yet: %s", path, exc)
            time.sleep(0.3 * (attempt + 1))


def _finish(result: dict, say, *, history: list | None = None, remember: bool = True) -> dict:
    result = {"at": time.time(), **result}
    _write_result(result)
    say(("Сброс библиотеки выполнен: " if result.get("done") else "Сброс библиотеки не выполнен: ")
        + (result.get("error") or f"удалено {result.get('removed', 0)}, ошибок {len(result.get('errors') or [])}"))
    if remember:
        try:
            from core.library_diagnostics import remember_repair
            remember_repair(TITLE, history or [result.get("error") or ""], ok=bool(result.get("done")))
        except OSError as exc:
            _LOG.warning("library reset: history not written: %s", exc)
    return result


def _perform(settings: dict, request: dict, say, confirm, state: dict) -> dict:
    places = _places(settings)
    if any(str(request.get(key) or "") != places[key] for key in places):
        _close_request()
        return _finish({"done": False, "error": "сброс был заказан для другой библиотеки или базы — ничего не удалено"}, say)
    base, out = Path(places["db"]), Path(places["output_root"])
    backup, cards = str(request.get("backup") or ""), request.get("cards")
    if state["phase"] not in (DELETING, BASE_GONE):
        gone = [what for what, was, place in (("база", request.get("db_existed"), base.exists() or _aside(base).exists()),
                                               ("папка библиотеки", request.get("output_existed"), out.exists()))
                if was and not place]
        if gone:                        # a drive not connected now: half a reset is worse than none
            _put_base_back(base)        # a start cut short had moved it aside: the program needs it now
            return _finish({"done": False, "waiting": True,
                            "error": f"не найдена {' и '.join(gone)} (диск не подключён?) — ничего не удалено; "
                                     "сброс выполнится при запуске, когда они будут на месте"}, say)
        answer = str(confirm(request) or "later")
        if answer != "yes":
            _put_base_back(base)
            if answer == "cancel":
                _close_request()
                return _finish({"done": False, "error": "отменён при запуске программы — ничего не удалено"}, say)
            return _finish({"done": False, "waiting": True, "error": "отложен при запуске программы — ничего не удалено"},
                           say, remember=False)
        why = _move_base_aside(base)
        if why:                         # the request stays: the next start tries again
            return _finish({"done": False, "waiting": True,
                            "error": f"база занята ({why}) — ничего не удалено; закройте все окна программы, "
                                     "сброс выполнится при следующем запуске"}, say)
        if _aside(base).exists():
            cards = _cards_in(_aside(base))
            from core.library_lifecycle import backup_database_file
            backup = backup_database_file(settings, _aside(base), "before_full_reset")
            if not backup:
                _put_base_back(base)
                _close_request()
                return _finish({"done": False, "error": "копия базы не сделана (места на диске?) — ничего не удалено; "
                                                        "освободите место и закажите сброс снова"}, say)
        try:                            # from here on a start after a stop finishes the reset
            _write_json(_request_file(), {**request, "phase": DELETING, "backup": backup, "cards": cards})
        except OSError as exc:
            _put_base_back(base)
            return _finish({"done": False, "waiting": True,
                            "error": f"ход сброса не записан ({exc}) — ничего не удалено"}, say)
        state["phase"] = DELETING
    else:
        if state["phase"] == DELETING and request.get("output_existed") and not out.exists():
            return _finish({"done": False, "waiting": True,       # folders still to delete are on a drive not here
                            "error": "сброс прерван, а папки библиотеки нет на месте (диск не подключён?) — он "
                                     "доделается при запуске, когда она появится"}, say)
        say("Сброс библиотеки был прерван — довожу до конца")
    say(f"Сброс библиотеки: копия базы — {backup or 'базы не было'}")
    return _delete_everything(settings, request, say, base, out, backup, cards, state)


def _delete_everything(settings: dict, request: dict, say, base: Path, out: Path, backup: str, cards,
                       state: dict) -> dict:
    removed, errors = [], []
    subscriptions = bool(request.get("include_subscriptions"))
    if state["phase"] == DELETING:
        if base.exists():               # the old base is aside: this one was made after the reset was cut short
            _close_request()
            return _finish({"done": False, "error": f"на месте базы — новая: сброс остановлен, больше ничего не "
                                                    f"удаляется; прежняя база лежит рядом: {_aside(base)}"}, say)
        _delete_library(settings, say, out, base, subscriptions, removed, errors)
        try:                            # the folders are never deleted twice
            _write_json(_request_file(), {**request, "phase": BASE_GONE, "backup": backup, "cards": cards})
        except OSError as exc:
            errors.append(f"ход сброса не записан: {exc}")
        state["phase"] = BASE_GONE
    from core.database.connection import reset_database_files
    from core.settings import save_settings
    if base.exists():                   # made by a start after the reset was cut short: it is not touched
        errors.append(f"{base}: на месте базы новая — она оставлена")
    else:
        removed.extend(reset_database_files(settings))      # the «initialized» mark: the next start makes a new base
        save_settings(settings)
    from core.paths import CACHE_DIR
    say(f"Удаляю кэш {CACHE_DIR}…")
    try:
        if Path(CACHE_DIR).exists():
            _remove_tree(Path(CACHE_DIR))
            removed.append(str(CACHE_DIR))
        Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        errors.append(f"кэш: {exc}")
    if any(_aside(base, part).exists() for part in _PARTS):     # not deleted: the next start deletes it,
        _write_json(_request_file(), {"phase": CLOSED, "at": time.time()})   # never takes it for a lost base
    else:
        _close_request()
    lines = [f"Выполнено при запуске программы{' (вместе с подписками)' if subscriptions else ''}"]
    if cards:
        lines.append(f"Карточек было: {cards[0]}, в корзине: {cards[1]}")
    lines += [f"Удалено папок и файлов: {len(removed)}", f"Копия базы: {backup or 'базы не было'}"]
    lines += [f"Ошибка: {e}" for e in errors[:10]]
    return _finish({"done": True, "removed": len(removed), "backup": backup, "errors": errors[:20],
                    "cards": cards, "include_subscriptions": subscriptions}, say, history=lines)


def _delete_library(settings: dict, say, out: Path, base: Path, subscriptions: bool, removed: list,
                    errors: list) -> None:
    """The library's folders (but one that holds the source folder), the subscriptions if asked, the old base."""
    from core.source_protection import require_managed_media_mutation
    for name in FOLDERS + ((SUBSCRIPTIONS_FOLDER,) if subscriptions else ()):
        target = out / name
        if not target.exists():
            continue
        if not require_managed_media_mutation(settings, target, "reset_generated_library"):
            errors.append(f"{target}: не тронуто — это не папка рабочей библиотеки")
            continue
        if _source_inside(settings, target):
            errors.append(f"{target}: не тронуто — в ней исходная папка")
            continue
        say(f"Удаляю {target}…")
        try:
            _remove_tree(target)
            removed.append(str(target))
        except OSError as exc:
            errors.append(f"{target}: {exc}")
    if subscriptions:
        from core.subscription_engine.seed_cache import OLD_JSON_FILE, SEED_CACHE_DB
        from core.subscriptions import SUBS_FILE
        for state_file in (Path(SUBS_FILE), Path(SEED_CACHE_DB), Path(str(SEED_CACHE_DB) + "-wal"),
                           Path(str(SEED_CACHE_DB) + "-shm"), Path(OLD_JSON_FILE)):
            try:
                state_file.unlink()
                removed.append(str(state_file))
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append(f"{state_file}: {exc}")
    for part in _PARTS:                 # the base aside (its copy is in the backups)
        if not _aside(base, part).exists():
            continue
        try:
            _unlink_patiently(_aside(base, part))
            removed.append(str(_aside(base, part)))
        except OSError as exc:
            errors.append(f"{_aside(base, part)}: {exc}")
