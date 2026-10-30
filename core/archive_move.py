"""«Настройки → Папки → Перенести архив на другой диск» (v5.36): the whole archive — the library's files, the base, the
settings, the cache, the journals, the backups — goes to another folder, and the program opens it there.

Two steps, because while the program runs the base and some files are open:

1. **While the program works** (a page task, :func:`copy_job`): every file of the archive is copied to the new place
   (``<the chosen folder>/<the archive's name>``; a mark file in it — :data:`MARK` — says it is the copy being made of
   this archive).  A file already there with the same size and time is not copied again: a stopped copy goes on from
   where it was.  The bases (SQLite files of the archive's ``settings``, with their -wal/-shm) wait for the second
   step.  The request (:data:`REQUEST` in the archive's runtime folder) says where to and how far.
2. **At the next start, before anything opens the base or the files** (:func:`finish`, app.py, after one more
   question): what changed meanwhile is copied again, what is gone is removed from the copy (only there: it is marked
   as ours), the bases are copied, every path of the old place in the bases and the settings files becomes the new one,
   the copy is checked (every file and its size, every base: ``quick_check``; the main base must have no old path
   left), and the pointer files name the new archive — the program restarts and opens it.

The old archive stays as it was: if anything goes wrong, nothing is lost and the program opens it as before.  It gets
:data:`MOVED` (where it went); the new one :data:`LAST` (where it came from) — «Настройки» then offers «Удалить старую
копию» (:func:`delete_old_job`: only a folder marked as moved to the archive open now, never the open one, a drive, or
a folder that holds a folder the program uses).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Callable

_LOG = logging.getLogger("local_booru.archive_move")

REQUEST = "archive_move_request.json"       # in the open archive's runtime folder
MARK = ".local_booru_move.json"             # in the copy being made
MOVED = "archive_moved_to.json"             # in the old archive's folder, after the move
LAST = "archive_move_last.json"             # in the new archive's runtime folder, after the move
COPYING, COPIED = "copying", "copied"
TMP = ".lbmove-tmp"
CHUNK = 8 * 1024 * 1024
MARGIN = 1024 ** 3                          # free space kept on the new disk above what the copy needs
SAME_TIME_NS = 2_000_000_000                # FAT/exFAT keep a file's time to two seconds
BASE_EXTS = (".sqlite3", ".sqlite", ".db")
BASE_TAILS = ("", "-wal", "-shm", "-journal")
SKIP = {REQUEST.lower(), "library_reset_request.json", "library_reset.lock",   # this archive's orders: not the copy's
        "thumbs.db", "desktop.ini"}                                           # Windows' own notes in a folder
JSON_MAX = 64 * 1024 * 1024
SAY_EVERY = 2.0                             # seconds between two lines of a copy's progress
SAVE_EVERY = 15.0                           # seconds between two writes of the request's progress
MAIN_BASE = "local_booru_index.sqlite3"
ANSWERS = ("yes", "cancel", "later")
KIND = "archive_move"                       # the page task (core/page_jobs.py): the copy, its cancel, the old copy
_END = r"(?=$|[\\/\"'\s,;|)\]}<>])"         # the old folder's name ends there (…Archive2 is another folder)


class MoveError(Exception):
    """The move cannot go on — said in words the person can act on."""


# ── places ─────────────────────────────────────────────────────────────────
def source() -> Path | None:
    """The open archive's folder (the one holding ``settings`` and ``output``); None — the data is in «Документах»."""
    from core import archive_switch
    return Path(archive_switch.open_archive()) if archive_switch.portable() else None


def _settings_part() -> Path:
    """Where the archive's own part (base, settings, cache) is inside the archive: ``settings``."""
    from core import paths
    root = source()
    return Path(paths.DATA_DIR).relative_to(root) if root is not None else Path("settings")


def _request_file() -> Path:
    from core.paths import RUNTIME_DIR
    return Path(RUNTIME_DIR) / REQUEST


def _key(path) -> str:
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return os.path.normcase(str(Path(path).absolute()))


def _inside(path, folder) -> bool:
    """``path`` is ``folder`` or lies inside it (any letter case on Windows)."""
    a, b = _key(path), _key(folder)
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def _long(path: Path) -> str:
    """Windows: the «\\\\?\\» form, so a file deeper than 260 characters is reached too."""
    text = str(path)
    if os.name == "nt" and path.is_absolute() and not text.startswith("\\\\?\\"):
        return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text
    return text


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict) -> None:
    """Whole or not at all: half an order is not read as one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def human(num: float) -> str:
    size = float(num or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024
    return f"{size:.1f} ГБ".replace(".", ",")


# ── the request ────────────────────────────────────────────────────────────
def pending() -> dict | None:
    """The move asked for in this archive and not done yet (its ``phase`` says how far it got), else None."""
    request = _read_json(_request_file())
    root = source()
    if not request or root is None or _key(request.get("source") or "") != _key(root):
        return None
    if request.get("phase") not in (COPYING, COPIED) or not request.get("target"):
        return None
    return request


def _save(request: dict) -> None:
    _write_json(_request_file(), request)


def _mark_of(target: Path) -> dict | None:
    return _read_json(target / MARK)


def target_for(chosen) -> Path:
    """The new place of the open archive in the folder ``chosen`` (``<chosen>/<the archive's name>``; the chosen
    folder itself when it has that name and is empty or is this archive's copy being made).  Raises MoveError."""
    from core import library_reset
    root = source()
    if root is None:
        raise MoveError("Архива ещё нет — данные лежат в «Документах». Переносить можно только архив "
                        "(Local_Booru_Archive).")
    if library_reset.pending_reset() is not None:
        raise MoveError("Заказан полный сброс библиотеки — сначала выполните или отмените его.")
    if str(_db_folder_setting() or "").strip():
        raise MoveError("База лежит отдельно от архива («sqlite_db_folder» в настройках) — переносится только архив "
                        "целиком, с базой внутри.")
    chosen = Path(str(chosen or "")).expanduser()
    if not str(chosen).strip() or not chosen.is_dir():
        raise MoveError("Такой папки нет")
    chosen = chosen.resolve()
    if chosen.name.lower() == root.name.lower() and (not any(chosen.iterdir()) or _ours(chosen, root)):
        target = chosen
    else:
        target = chosen / root.name
    if _inside(target, root):
        raise MoveError("Это папка самого архива — выберите папку на другом диске или в другом месте")
    if _inside(root, target):
        raise MoveError("Архив уже лежит в этой папке")
    if target.exists() and any(target.iterdir()) and not _ours(target, root):
        raise MoveError(f"В {target} уже есть файлы — выберите пустую папку или другое место")
    try:
        probe = (target.parent if not target.exists() else target) / f".lbmove-write-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise MoveError(f"В эту папку нельзя записывать: {exc}") from exc
    return target


def _ours(target: Path, root: Path) -> bool:
    mark = _mark_of(target)
    return bool(mark) and _key(mark.get("source") or "") == _key(root)


def _db_folder_setting() -> str:
    try:
        from core.settings import load_settings
        return str(load_settings().get("sqlite_db_folder") or "")
    except Exception:  # noqa: BLE001 - only a reason not to start
        return ""


def request(chosen) -> dict:
    """Order the move of the open archive into the folder ``chosen`` (the copy begins with :func:`copy_job`)."""
    root = source()
    target = target_for(chosen)
    target.mkdir(parents=True, exist_ok=True)
    _write_json(target / MARK, {"source": str(root), "at": time.time(),
                                "note": "Копия архива Local Booru, которая делается сейчас. Не удаляйте этот файл, "
                                        "пока перенос не закончен."})
    order = {"source": str(root), "target": str(target), "at": time.time(), "phase": COPYING,
             "files": 0, "bytes": 0, "total_files": 0, "total_bytes": 0, "error": ""}
    _save(order)
    return order


def cancel_job(progress: Callable, stop_check: Callable) -> dict:
    """«Отменить перенос»: the order goes, the copy made so far is removed (only the marked copy of this archive)."""
    order = pending()
    if order is None:
        return {"failed": "Перенос не заказан"}
    target, root = Path(order["target"]), source()
    _close()
    if target.exists() and root is not None and _ours(target, root):
        progress(f"Удаляю сделанную копию: {target}")
        removed = _remove_tree(target, progress, stop_check)
        if removed.get("stopped"):
            return {"stopped": True}
    progress("Перенос отменён")
    return {"cancelled": True}


def _close() -> None:
    try:
        _request_file().unlink()
    except FileNotFoundError:
        pass


# ── what is copied ─────────────────────────────────────────────────────────
def _is_base(rel: Path, part: Path) -> bool:
    """A SQLite file of the archive's own part (with its -wal/-shm) that the program keeps open — the base, the caches:
    copied at the start, not while it is open.  The copies of the base (``output/backups``) are snapshots nobody opens:
    they go with the other files, and the paths in them stay as they were."""
    try:
        inner = rel.relative_to(part)
    except ValueError:
        return False
    if inner.parts[:2] == ("output", "backups"):
        return False
    name = rel.name.lower()
    return any(name.endswith(ext + tail) for ext in BASE_EXTS for tail in BASE_TAILS)


def _loose(rel: Path, part: Path) -> bool:
    """The archive's journals and runtime notes (``output/logs``, ``output/runtime``): the program writes them while it
    starts, the move among them — copied as they are, a file that changes or cannot be read meanwhile does not stop it."""
    try:
        inner = rel.relative_to(part)
    except ValueError:
        return False
    return inner.parts[:2] in (("output", "logs"), ("output", "runtime"))


def _files(root: Path, part: Path, *, bases: bool = False) -> dict:
    """{relative path: (size, mtime_ns)} of the regular files under ``root`` — the bases only (``bases``) or all but
    them; the orders of the archive, the copy's mark and half-copied files left out; links to folders not followed."""
    out: dict = {}
    stack = [Path()]
    while stack:
        rel_dir = stack.pop()
        try:
            entries = list(os.scandir(_long(root / rel_dir)))
        except FileNotFoundError:
            continue
        for entry in entries:
            rel = rel_dir / entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(rel)
                    continue
                if not entry.is_file():
                    continue
                if entry.name.lower() in SKIP or entry.name.endswith(TMP) or (rel_dir == Path() and entry.name == MARK):
                    continue
                if _is_base(rel, part) != bases:
                    continue
                st = entry.stat()
            except OSError:
                continue
            out[rel] = (int(st.st_size), int(st.st_mtime_ns))
    return out


def _same(target: Path, size: int, mtime_ns: int) -> bool:
    try:
        st = os.stat(_long(target))
    except OSError:
        return False
    return int(st.st_size) == size and abs(int(st.st_mtime_ns) - mtime_ns) <= SAME_TIME_NS


def _copy(src: Path, dst: Path, stop_check: Callable, counted: Callable) -> bool:
    """One file whole (a part copy goes to a temporary name first; its time is the original's).  False — stopped."""
    os.makedirs(_long(dst.parent), exist_ok=True)
    temp = dst.with_name(dst.name + TMP)
    try:
        with open(_long(src), "rb") as fin, open(_long(temp), "wb") as fout:
            while True:
                if stop_check():
                    return False
                chunk = fin.read(CHUNK)
                if not chunk:
                    break
                fout.write(chunk)
                counted(len(chunk))
        st = os.stat(_long(src))
        os.utime(_long(temp), ns=(int(st.st_atime_ns), int(st.st_mtime_ns)))
        os.replace(_long(temp), _long(dst))
        return True
    finally:
        try:
            os.unlink(_long(temp))
        except OSError:
            pass


class _Pace:
    """How a copy goes: lines «Скопировано X из Y (N %), M МБ/с, осталось ≈ T» every :data:`SAY_EVERY` seconds."""

    def __init__(self, progress: Callable, total: int, done: int = 0):
        self.progress, self.total, self.done = progress, max(0, int(total)), int(done)
        self.started, self.last = time.monotonic(), 0.0
        self.base = self.done

    def add(self, size: int) -> None:
        self.done += int(size)
        now = time.monotonic()
        if now - self.last >= SAY_EVERY:
            self.last = now
            self.say()

    def say(self) -> None:
        spent = max(0.001, time.monotonic() - self.started)
        speed = (self.done - self.base) / spent
        left = (self.total - self.done) / speed if speed > 0 else 0
        share = int(100 * self.done / self.total) if self.total else 100
        when = f", осталось ≈ {_minutes(left)}" if speed > 0 and self.done < self.total else ""
        self.progress(f"Скопировано {human(self.done)} из {human(self.total)} ({share} %), "
                      f"{human(speed)}/с{when}")


def _minutes(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{max(1, seconds)} с"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} мин"
    return f"{seconds // 3600} ч {round(seconds % 3600 / 60)} мин"


def copy_job(progress: Callable, stop_check: Callable) -> dict:
    """The first step (a page task): every file of the archive but its bases into the copy; a stopped copy goes on
    later from where it was."""
    order = pending()
    if order is None:
        return {"failed": "Перенос не заказан"}
    root, target, part = source(), Path(order["target"]), _settings_part()
    if not _ours(target, root):
        return {"failed": f"В {target} нет отметки копии этого архива — перенос надо заказать заново"}
    progress(f"Переношу архив: {root} → {target}")
    progress("Считаю файлы архива…")
    files = _files(root, part)
    total = sum(size for size, _t in files.values())
    todo = [(rel, size, mtime) for rel, (size, mtime) in sorted(files.items()) if not _same(target / rel, size, mtime)]
    need = sum(size for _r, size, _t in todo)
    later = sum(size for size, _t in _files(root, part, bases=True).values())    # the bases: at the start
    order.update(total_files=len(files), total_bytes=total, error="")
    progress(f"В архиве {len(files)} файлов, {human(total)}; копировать {len(todo)}, {human(need)}; база — "
             f"{human(later)}, она копируется при перезапуске")
    try:
        free = shutil.disk_usage(target).free
    except OSError as exc:
        return {"failed": f"Диск {target} недоступен: {exc}"}
    if free < need + later + MARGIN:
        return {"failed": f"На диске не хватает места: нужно {human(need + later)} и ещё {human(MARGIN)} про запас, "
                          f"свободно {human(free)}"}
    pace = _Pace(progress, total, total - need)
    saved, errors = time.monotonic(), []
    for n, (rel, size, mtime) in enumerate(todo):
        if stop_check():
            break
        try:
            if not _copy(root / rel, target / rel, stop_check, pace.add):
                break
        except OSError as exc:
            if getattr(exc, "errno", None) == 28 or getattr(exc, "winerror", None) == 112:
                order["error"] = "на диске кончилось место"
                _save(order)
                return {"failed": f"На диске {target} кончилось место"}
            errors.append(f"{rel}: {exc}")
            continue
        if time.monotonic() - saved >= SAVE_EVERY:
            saved = time.monotonic()
            order.update(files=n + 1, bytes=pace.done)
            _save(order)
    pace.say()
    order.update(bytes=pace.done)
    if stop_check():
        _save(order)
        progress("Остановлено — «Продолжить копирование» продолжит с этого места")
        return {"stopped": True}
    if errors:
        order["error"] = f"не скопировались {len(errors)} файлов"
        _save(order)
        for line in errors[:10]:
            progress(f"  не скопирован: {line}")
        return {"failed": f"Не скопировались {len(errors)} файлов (они заняты или недоступны) — «Продолжить "
                          f"копирование» попробует их снова"}
    order.update(phase=COPIED, files=len(files), bytes=total)
    _save(order)
    progress("Всё скопировано. Перезапустите программу: при запуске она докопирует изменённое, перенесёт базу и "
             "откроет архив на новом месте")
    return {"copied": len(todo), "bytes": need}


# ── the paths in the bases and the settings ─────────────────────────────────
class Paths:
    """Every way a text writes the old archive's folder (either slash, JSON's doubled backslash, any letter case) →
    the new one; a folder whose name only begins so (…Archive2) is another folder."""

    def __init__(self, old, new):
        o, n = str(old).rstrip("\\/"), str(new).rstrip("\\/")
        pairs: list = []
        for a, b in ((o.replace("/", "\\"), n.replace("/", "\\")), (o.replace("\\", "/"), n.replace("\\", "/")),
                     (o.replace("/", "\\").replace("\\", "\\\\"), n.replace("/", "\\").replace("\\", "\\\\"))):
            if (a, b) not in pairs:
                pairs.append((a, b))
        self._rules = [(re.compile(re.escape(a) + _END, re.I), b) for a, b in pairs]
        self._any = re.compile("|".join(f"(?:{re.escape(a)}{_END})" for a, _b in pairs), re.I)
        # what SQLite looks for before Python looks closer (``instr(lower(…))``: its lower() knows only Latin
        # letters); a folder with other letters — Python looks at every text (``rewrite_base``)
        self.needles = tuple(a.lower() for a, _b in pairs) if o.isascii() else None

    def has(self, text: str) -> bool:
        return bool(self._any.search(text))

    def text(self, value: str) -> str:
        for rule, new in self._rules:
            value = rule.sub(lambda _m, new=new: new, value)
        return value

    def data(self, value):
        """A JSON value with every path in it (in the keys too) the new one."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.data(v) for v in value]
        if isinstance(value, dict):
            return {self.text(k) if isinstance(k, str) else k: self.data(v) for k, v in value.items()}
        return value


def _texty(declared: str) -> bool:
    kind = str(declared or "").upper()
    return not kind or any(word in kind for word in ("CHAR", "CLOB", "TEXT", "JSON", "STRING"))


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _with_old(column: str, paths: Paths) -> tuple:
    """(SQL condition, its arguments): the column's text may have the old folder in it."""
    if paths.needles is None:
        return f"typeof({_q(column)})='text' AND lb_old_path({_q(column)})", ()
    found = " OR ".join(f"instr(lower({_q(column)}), ?) > 0" for _n in paths.needles)
    return f"typeof({_q(column)})='text' AND ({found})", paths.needles


def rewrite_base(path: Path, paths: Paths) -> dict:
    """Every text of every table of the base at ``path`` with the old archive's folder in it → the new one; the base
    checked (``quick_check``).  {"changed": rows, "left": texts still with the old folder, "ok": the check's word}.
    SQLite picks the texts that may have it (``_with_old``), Python looks closer: a whole base is not read."""
    con = sqlite3.connect(str(path), timeout=30)
    try:
        con.create_function("lb_old_path", 1, lambda value: 1 if isinstance(value, str) and paths.has(value) else 0,
                            deterministic=True)
        con.execute("PRAGMA foreign_keys=OFF")
        tables = con.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()
        virtual = {name for name, sql in tables if str(sql or "").lstrip().upper().startswith("CREATE VIRTUAL")}
        plain = [(name, sql) for name, sql in tables
                 if not (name.startswith("sqlite_") or name in virtual or any(name.startswith(v + "_") for v in virtual))]
        changed = 0
        for name, sql in plain:
            columns = con.execute(f"PRAGMA table_info({_q(name)})").fetchall()
            texts = [c[1] for c in columns if _texty(c[2])]
            if not texts:
                continue
            if "WITHOUT ROWID" in str(sql or "").upper():
                keys = [c[1] for c in sorted(columns, key=lambda c: c[5]) if c[5]]
            else:
                keys = ["rowid"]
            if not keys:
                continue
            key_sql = ", ".join(_q(k) if k != "rowid" else "rowid" for k in keys)
            where = " AND ".join(f"{_q(k) if k != 'rowid' else 'rowid'}=?" for k in keys)
            for column in texts:
                cond, args = _with_old(column, paths)
                updates = []
                for row in con.execute(f"SELECT {key_sql}, {_q(column)} FROM {_q(name)} WHERE {cond}", args).fetchall():
                    value = row[-1]
                    if value and paths.has(value):
                        new = paths.text(value)
                        if new != value:
                            updates.append((new, *row[:-1]))
                for update in updates:
                    con.execute(f"UPDATE {_q(name)} SET {_q(column)}=? WHERE {where}", update)
                changed += len(updates)
        con.commit()
        left = 0
        for name, _sql in plain:
            for column in [c[1] for c in con.execute(f"PRAGMA table_info({_q(name)})").fetchall() if _texty(c[2])]:
                cond, args = _with_old(column, paths)
                for (value,) in con.execute(f"SELECT {_q(column)} FROM {_q(name)} WHERE {cond}", args):
                    if value and paths.has(value):
                        left += 1
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            pass
        verdict = str(con.execute("PRAGMA quick_check").fetchone()[0])
        return {"changed": changed, "left": left, "ok": verdict}
    finally:
        con.close()


def rewrite_json(path: Path, paths: Paths) -> bool:
    """A JSON file of the settings with every path of the old archive the new one; True — it changed."""
    try:
        if path.stat().st_size > JSON_MAX:
            return False
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if not paths.has(text) and not paths.has(text.replace("\\\\", "\\")):
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return False
    new = paths.data(data)
    if new == data:
        return False
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return True


# ── the second step, at the start ──────────────────────────────────────────
def describe(order: dict) -> str:
    """The question asked at the start, in words."""
    when = time.strftime("%d.%m.%Y %H:%M", time.localtime(float(order.get("at") or 0)))
    return (f"Перенос архива ({when}):\n{order.get('source')}\n→ {order.get('target')}\n\nФайлы уже скопированы. Сейчас "
            "программа докопирует то, что изменилось за это время, перенесёт базу и настройки и откроется с архивом "
            "на новом месте. Старая копия останется как есть — её можно будет удалить в «Настройках».\n\n"
            "Закончить перенос сейчас?")


def answer(order: dict, said: str) -> str:
    """The person's answer at the start: "cancel" takes the order back (the copy stays — «Настройки» can remove
    it later only by a new order); "later" leaves everything as it is."""
    if said == "cancel":
        _close()
    return said if said in ANSWERS else "later"


def finish(order: dict, progress: Callable | None = None, log: Callable | None = None) -> dict:
    """The second step: {"done": True, "target": the new archive} — the program must start again (the pointer names
    the new archive); {"done": False, "error": why} — nothing switched, the old archive opens as before (the order
    stays: it is asked again at the next start)."""
    say = progress or (lambda *_a, **_k: None)
    note = log or (lambda _m: None)
    try:
        result = _finish(order, say, note)
    except Exception as exc:  # noqa: BLE001 - whatever it is, the old archive must open as before
        from core.redaction import sanitize_text
        reason = sanitize_text(f"{exc}" if isinstance(exc, MoveError) else f"{type(exc).__name__}: {exc}")
        _LOG.error("archive move not finished: %s", reason, exc_info=not isinstance(exc, MoveError))
        note(f"Перенос архива не закончен: {reason}")
        order = dict(order, error=reason)
        try:
            _save(order)
        except OSError:
            pass
        return {"done": False, "error": reason}
    return result


ASIDE = ".moving"                           # the base's name for a moment while it is checked (``_base_free``)


def recover() -> None:
    """A start after one that ended while the base had its other name for a moment (``_base_free``): its own name
    back.  Called by app.py at every start, before anything opens the base."""
    try:
        from core.paths import DB_DIR
        base = Path(DB_DIR) / MAIN_BASE
        aside = base.with_name(base.name + ASIDE)
        if aside.exists() and not base.exists():
            os.replace(aside, base)
            _LOG.warning("archive move: the base got its name back (%s)", base)
    except OSError as exc:
        _LOG.error("archive move: the base keeps its other name: %s", exc)


def _base_free(base: Path, wait: float = 30.0) -> None:
    """The base is closed by every copy of the program: a running one holds it, and on Windows a file somebody holds
    cannot be renamed.  (On Linux and macOS this tells nothing: the rename always goes.)"""
    if not base.exists():
        return
    aside = base.with_name(base.name + ASIDE)
    deadline = time.monotonic() + wait
    while True:
        try:
            os.replace(base, aside)
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise MoveError("База открыта другой копией программы — закройте её и запустите программу снова") from exc
            time.sleep(1.0)
            continue
        for _try in range(120):             # its name back, whatever holds it for a moment (an antivirus)
            try:
                os.replace(aside, base)
                return
            except OSError:
                time.sleep(0.5)
        raise MoveError(f"База осталась под именем {aside.name} — переименуйте её обратно в {base.name}")


def _finish(order: dict, say: Callable, note: Callable) -> dict:
    from core import paths as P
    root, target, part = source(), Path(order["target"]), _settings_part()
    if root is None or not _ours(target, root):
        raise MoveError(f"В {target} нет отметки копии этого архива — перенос надо заказать заново")
    _base_free(Path(P.DB_DIR) / MAIN_BASE)
    # what changed meanwhile, what is gone
    say("Сверяю файлы архива и копии…", 0, 0)
    files = _files(root, part)
    have = _files(target, part)
    todo = [(rel, size, mtime) for rel, (size, mtime) in sorted(files.items()) if not _same(target / rel, size, mtime)]
    need = sum(size for _r, size, _t in todo)
    gone = [rel for rel in have if rel not in files]
    note(f"перенос архива: докопировать {len(todo)} файлов ({human(need)}), убрать из копии {len(gone)}")
    done = [0]

    def counted(size: int) -> None:
        done[0] += size
        say(f"Докопирую изменённое: {human(done[0])} из {human(need)}", done[0], need)
    for rel, _size, _mtime in todo:
        try:
            _copy(root / rel, target / rel, lambda: False, counted)
        except OSError as exc:
            if not _loose(rel, part):
                raise MoveError(f"Файл {rel} не копируется: {exc}") from exc
            note(f"перенос архива: {rel} не скопирован ({exc})")
    for rel in gone:
        try:
            os.unlink(_long(target / rel))
        except FileNotFoundError:
            pass
    # the bases, as they are now (the program is not running)
    bases = _files(root, part, bases=True)
    for rel in _files(target, part, bases=True):
        if rel not in bases:
            os.unlink(_long(target / rel))
    total = sum(size for size, _t in bases.values())
    done[0] = 0

    def counted_base(size: int) -> None:
        done[0] += size
        say(f"Копирую базу: {human(done[0])} из {human(total)}", done[0], total)
    for rel in sorted(bases):
        _copy(root / rel, target / rel, lambda: False, counted_base)
    # the paths
    paths = Paths(root, target)
    main = target / part / "db" / MAIN_BASE
    for rel in sorted(bases):
        name = rel.name.lower()
        if not name.endswith(BASE_EXTS):
            continue
        say(f"Меняю пути в базе {rel.name}…", 0, 0)
        try:
            result = rewrite_base(target / rel, paths)
        except sqlite3.DatabaseError as exc:
            if _key(target / rel) == _key(main):
                raise MoveError(f"База не читается: {exc}") from exc
            note(f"перенос архива: {rel} — не база SQLite ({exc}), оставлена как есть")
            continue
        if result["ok"] != "ok" or (result["left"] and _key(target / rel) == _key(main)):
            raise MoveError(f"Проверка базы {rel.name} после переноса: {result['ok']}, старых путей осталось "
                            f"{result['left']}")
        note(f"перенос архива: {rel} — путей изменено {result['changed']}")
    changed_json = 0
    for rel in sorted(_files(target, part)):
        if rel.suffix.lower() == ".json" and (part in rel.parents):
            if rewrite_json(target / rel, paths):
                changed_json += 1
    note(f"перенос архива: файлов настроек с новыми путями — {changed_json}")
    # the check: every file there and whole (a settings file has new paths; the journals change while it goes)
    say("Проверяю копию…", 0, 0)
    copied = _files(target, part)
    for rel, (size, _mtime) in files.items():
        if _loose(rel, part) or (rel.suffix.lower() == ".json" and part in rel.parents):
            continue
        got = copied.get(rel)
        if got is None:
            raise MoveError(f"В копии нет файла {rel}")
        if got[0] != size:
            raise MoveError(f"Файл {rel} скопирован не целиком ({got[0]} из {size} байт)")
    if not main.exists():
        raise MoveError("В копии нет базы")
    # the marks, the pointer: from here on the new archive is the one that opens
    new_runtime = target / Path(P.RUNTIME_DIR).relative_to(root)
    _write_json(new_runtime / LAST, {"from": str(root), "to": str(target), "at": time.time(),
                                     "files": len(files), "bytes": sum(s for s, _t in files.values())})
    try:
        _write_json(root / MOVED, {"to": str(target), "at": time.time(),
                                   "note": "Этот архив перенесён — программа открывает его копию на новом месте."})
    except OSError as exc:
        note(f"перенос архива: отметка в старом архиве не записана ({exc})")
    P.choose_next_archive(target / part)
    try:
        (target / MARK).unlink()
    except OSError:
        pass
    _close()
    note(f"Архив перенесён: {root} → {target}")
    return {"done": True, "target": str(target)}


# ── after the move: the old copy ───────────────────────────────────────────
def last_move() -> dict | None:
    """The move that brought the open archive here ({"from", "to", "at", "old_exists"}), None — there was none."""
    from core.paths import RUNTIME_DIR
    record = _read_json(Path(RUNTIME_DIR) / LAST)
    if not record or not record.get("from"):
        return None
    old = Path(record["from"])
    return dict(record, old_exists=old.exists() and not record.get("old_deleted"))


def _old_problem(record: dict, settings: dict) -> str:
    """Why the old copy may not be removed ("" — it may)."""
    old, here = Path(record.get("from") or ""), source()
    if not str(old).strip() or not old.exists():
        return "Старой копии уже нет"
    if here is None or _inside(here, old) or _inside(old, here):
        return "Старая копия — это открытый архив или папка рядом с ним"
    if old.parent == old or len(old.parts) <= 1:
        return "Это корень диска"
    moved = _read_json(old / MOVED)
    if not moved or _key(moved.get("to") or "") != _key(here):
        return f"В {old} нет отметки, что архив перенесён сюда — удалять его программа не будет"
    for key in ("root", "manga_root", "video_root", "output_dir", "light_backup_dir"):
        value = str((settings or {}).get(key) or "").strip()
        if value and _inside(value, old):
            return f"Папка из настроек ({key}) лежит в старой копии: {value}"
    return ""


def delete_old_job(settings: dict) -> Callable:
    """«Удалить старую копию»: a page task's work (the files of the old archive, for good)."""
    def work(progress: Callable, stop_check: Callable) -> dict:
        record = last_move()
        if record is None:
            return {"failed": "Переноса не было"}
        problem = _old_problem(record, settings)
        if problem:
            return {"failed": problem}
        old = Path(record["from"])
        progress(f"Удаляю старую копию архива: {old}")
        result = _remove_tree(old, progress, stop_check)
        if result.get("stopped"):
            return {"stopped": True}
        from core.paths import RUNTIME_DIR
        _write_json(Path(RUNTIME_DIR) / LAST, dict(record, old_deleted=time.time()))
        progress(f"Удалено файлов: {result['files']}, {human(result['bytes'])}")
        return {"deleted": result["files"]}
    return work


def _remove_tree(folder: Path, progress: Callable, stop_check: Callable) -> dict:
    """Every file under ``folder``, then the folders; how it goes every few seconds; a file Windows holds for a
    moment is tried again once.  {"files", "bytes", "stopped", "left"}."""
    files = bytes_ = 0
    last = time.monotonic()
    left: list = []
    for top, dirs, names in os.walk(_long(folder), topdown=False):
        for name in names:
            if stop_check():
                return {"files": files, "bytes": bytes_, "stopped": True, "left": left}
            path = os.path.join(top, name)
            try:
                size = os.stat(path).st_size
                try:
                    os.unlink(path)
                except PermissionError:
                    os.chmod(path, 0o666)
                    os.unlink(path)
                files += 1
                bytes_ += size
            except FileNotFoundError:
                continue
            except OSError as exc:
                left.append(f"{path}: {exc}")
            if time.monotonic() - last >= SAY_EVERY:
                last = time.monotonic()
                progress(f"Удалено файлов: {files}, {human(bytes_)}")
        for name in dirs:
            try:
                os.rmdir(os.path.join(top, name))
            except OSError:
                pass
    try:
        os.rmdir(_long(folder))
    except OSError:
        pass
    for line in left[:10]:
        progress(f"  не удалён: {line}")
    return {"files": files, "bytes": bytes_, "stopped": False, "left": left}
