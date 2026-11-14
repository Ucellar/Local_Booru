"""(v5.44.165) The program lives on the code it started with.

An update is copied into the program's folder while the program runs — the assistant's deploy, «Pull origin» in GitHub
Desktop.  Until this module the running program was then half old and half new: the pages' scripts and stylesheets
were read from the disk at every request, and most imports of the program stand inside functions (2690 of 4798 at
v5.44.143), so a module first needed after the update was the new one among the old ones already loaded — a new page
script calling an API the running server did not have, a new function calling an old one with arguments it did not
take, the exit of the program (its copy of the base, its checkpoint) done by new code on old state.  And nothing told
the owner that a restart was due.

From :func:`install` on (the first lines of ``app.py`` and of ``python -m webui``):

* every file of the folders :data:`KEPT` — the packages ``core``, ``webui`` (its ``static`` with them) and ``ui`` — is
  read into memory as it is at the start: at once, in a thread of its own; a file needed before the thread came to it
  is read then (the same start), and so is a file that could not be read at the start or whose folder could not be
  listed (held by something for the moment);
* the modules of these packages are imported from that memory (:class:`_Finder`, :class:`_Loader`) whenever they are
  first needed, whatever the folder holds by then — a changed file, a removed one;
* the pages' files are given from it (:func:`static`), and the few data files the code reads beside itself
  (:func:`read_bytes`, :func:`read_text`);
* the same thread then looks at the folder's ``core/version.py`` now and then (a page never waits for the disk):
  another version there than the one running — :func:`update_waiting`, and every page says «Установлено обновление …»
  with «Перезапустить» (``webui/html.update_notice``).  A changed file is believed at the look after the one that saw
  it change — an update half copied is not offered;
* from then on a traceback shows the lines of the code that ran, not of the file that replaced it (``linecache`` is
  given them — :func:`_keep_lines`).

The bytecode beside the code (``__pycache__``) is used as always — it is checked against the kept file's own time and
size, so the bytecode of a newer file is never taken for the old one — and written only while the file on the disk is
still the kept one.

Not installed — a check importing the program's modules, the frozen build (its code is inside the executable), or
``LOCAL_BOORU_LIVE_CODE=1`` (work on the pages with a server running) — everything is read from the disk as before;
the functions below answer the same way from there, and no update is ever waiting.

This module is imported before anything else of the program and imports nothing of it at its top.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import linecache
import logging
import os
import re
import stat
import sys
import threading

_LOG = logging.getLogger("local_booru.started_code")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEPT = ("core", "webui", "ui")              # the folders of the program's code, the pages' files inside them too
STATIC = os.path.join(ROOT, "webui", "static")
VERSION_FILE = os.path.join(ROOT, "core", "version.py")
TOO_BIG = 32 * 1024 * 1024                  # a file that large is neither code nor a page's file: left on the disk
LOOK_EVERY = 15.0                           # seconds between two looks at the folder's version

_PREFIXES = tuple(os.path.normcase(os.path.join(ROOT, folder)) + os.sep for folder in KEPT)
_FILES: dict = {}                           # {key: (bytes, mtime, size)} — the files as they were at the start
_UNREAD: set = set()                        # files of the start that could not be read then: tried again when needed
_UNLISTED: list = []                        # folders of the start that could not be listed then: their files — as needed
_ON_DISK: set = set()                       # files too big to keep: read from the disk, as before
_READ = threading.Event()                   # every kept folder has been read through
_STOP = threading.Event()                   # (a check) the looks at the folder end
_STATE = {"installed": False, "stamp": None, "disk": "", "waiting": "", "lines": False}
_VERSION_LINE = re.compile(r'^VERSION\s*=\s*["\']([^"\']+)["\']', re.M)


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _keep(path) -> "tuple | None":
    """Read ``path`` into the memory of the start (the first reading stays); None — no such file, or not read."""
    key = _key(path)
    try:
        with open(path, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):                 # (a device's name on Windows — «con»)
                return None
            if st.st_size > TOO_BIG:
                _ON_DISK.add(key)
                return None
            data = fh.read()
    except FileNotFoundError:
        return None
    except OSError:
        if not os.path.isdir(path):          # held by something for the moment: asked again when it is needed
            _UNREAD.add(key)
        return None
    _UNREAD.discard(key)
    return _FILES.setdefault(key, (data, st.st_mtime, len(data)))


def _get(path) -> "tuple | None":
    """A kept file — (bytes, time, size) — or None: not of the kept folders, or not there when the program started."""
    key = _key(path)
    found = _FILES.get(key)
    if found is None and key.startswith(_PREFIXES) and (
            not _READ.is_set() or key in _UNREAD or (_UNLISTED and key.startswith(tuple(_UNLISTED)))):
        found = _keep(path)                  # needed before the reading thread came to it — the same start
    return found


def _unlisted(exc: OSError) -> None:
    """(``os.walk`` goes on past a folder it could not list.)  A folder that is there and was not listed — held by
    something for the moment — is remembered: its files are read when they are needed, like a file that could not be
    read; without this a page's file of such a folder would be «not there at the start» until the next start.  A
    folder that is not there has no files of the start."""
    folder = getattr(exc, "filename", None)
    if folder and not isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        _UNLISTED.append(_key(folder) + os.sep)
        _LOG.warning("a folder of the program's code was not listed at the start: %s", exc)


def _read_everything() -> None:
    try:
        for folder in KEPT:
            for base, folders, names in os.walk(os.path.join(ROOT, folder), onerror=_unlisted):
                folders[:] = [name for name in folders if name != "__pycache__" and not name.startswith(".")]
                for name in names:
                    if not name.endswith((".pyc", ".pyo")):
                        path = os.path.join(base, name)
                        if _key(path) not in _FILES:
                            _keep(path)
    finally:
        _READ.set()


def changed(path) -> bool:
    """The kept file is another one on the disk now (or gone from it)."""
    found = _get(path)
    if found is None:
        return False
    try:
        st = os.stat(path)
    except OSError:
        return True
    return int(st.st_mtime) != int(found[1]) or st.st_size != found[2]


def _lines(path: str) -> None:
    """``linecache`` gets the kept text of a module: a traceback after an update shows the code that ran.  (It reads
    the file from the disk otherwise — the new lines at the old numbers.)"""
    found = _get(path)
    if found is None:
        return
    try:
        text = importlib.util.decode_source(found[0])
    except (SyntaxError, UnicodeDecodeError):
        return
    lines = text.splitlines(True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    linecache.cache[path] = (len(text), None, lines, path)       # (no time: linecache never checks it against the disk)


def _keep_lines() -> None:
    """The folder is no longer the code that runs: the lines of every module of the program loaded so far go to
    ``linecache`` (those loaded later — as they are loaded, :meth:`_Loader.exec_module`).  Not before: until then the
    disk says the same, and the text of the whole program is not held twice."""
    _STATE["lines"] = True
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if isinstance(path, str) and path.endswith(".py") and _key(path).startswith(_PREFIXES):
            _lines(path)


class _Loader(importlib.machinery.SourceFileLoader):
    """A module of the program from the code kept at the start."""

    def get_data(self, path):
        found = _get(path) if str(path).endswith(".py") else None
        return found[0] if found is not None else super().get_data(path)      # (else — its bytecode, from the disk)

    def path_stats(self, path):
        found = _get(path)
        if found is None:
            return super().path_stats(path)
        return {"mtime": found[1], "size": found[2]}      # the bytecode on the disk is taken only if it is this file's

    def set_data(self, path, data, *, _mode=0o666):
        """Bytecode is written beside a module only while the file there is still the kept one: after an update the
        place belongs to the new file's bytecode."""
        try:
            source = importlib.util.source_from_cache(path)
        except (NotImplementedError, ValueError):
            source = ""
        if source and changed(source):
            return None
        return super().set_data(path, data, _mode=_mode)

    def exec_module(self, module):
        if _STATE["lines"]:
            _lines(self.path)
        return super().exec_module(module)


class _Finder(importlib.abc.MetaPathFinder):
    """The program's own packages are found in the memory of the start, not in the folder.  A module that was not
    there then is left to Python's usual search (nothing of the kept code names it)."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition(".")[0] not in KEPT:
            return None
        base = os.path.join(ROOT, *fullname.split("."))
        for file, folder in ((os.path.join(base, "__init__.py"), base), (base + ".py", None)):
            if _get(file) is not None:
                return importlib.util.spec_from_file_location(
                    fullname, file, loader=_Loader(fullname, file),
                    submodule_search_locations=[folder] if folder else None)
        return None


def install() -> bool:
    """From now on the program is the code it was started with.  ``False`` — not done (see the module's words): the
    frozen build, ``LOCAL_BOORU_LIVE_CODE=1``."""
    if _STATE["installed"]:
        return True
    if getattr(sys, "frozen", False):
        return False
    if os.environ.get("LOCAL_BOORU_LIVE_CODE", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    _STATE["installed"] = True
    sys.meta_path.insert(0, _Finder())
    threading.Thread(target=_watch, name="started-code", daemon=True).start()
    return True


def installed() -> bool:
    return bool(_STATE["installed"])


def read_whole(timeout: float = 60.0) -> bool:
    """Wait until every kept folder has been read (a check; the program never waits for it)."""
    return _READ.wait(timeout) if _STATE["installed"] else True


def _from_disk(path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def read_bytes(path) -> bytes:
    """A file beside the program's code as it was when the program started (a file of the kept folders that was not
    there then — ``FileNotFoundError``); any other file, or not installed, — from the disk."""
    if _STATE["installed"]:
        found = _get(path)
        if found is not None:
            return found[0]
        key = _key(path)
        if key.startswith(_PREFIXES) and key not in _ON_DISK:
            raise FileNotFoundError(2, "Этого файла не было в программе, когда она запустилась", str(path))
    return _from_disk(path)


def read_text(path, encoding: str = "utf-8") -> str:
    return read_bytes(path).decode(encoding)


def static(name: str) -> "bytes | None":
    """A page's file by its address after ``/static/`` — its bytes; None — no such file, or not the name of a file
    under ``webui/static`` (a way out of the folder, a device's name).  (v5.44.166) A name ending with a dot or a
    space is no file's name: Windows drops them, and «booru.js.» was «booru.js» there."""
    parts = str(name or "").split("/")
    if not all(parts) or any(part.endswith((".", " ")) or "\\" in part or ":" in part or "\x00" in part
                             for part in parts):
        return None
    path = os.path.join(STATIC, *parts)
    if _STATE["installed"]:
        found = _get(path)
        if found is not None:
            return found[0]
        if _key(path) not in _ON_DISK:
            return None
    try:
        if not os.path.isfile(path):         # (Windows opens «con», «nul» in any folder as devices)
            return None
        return _from_disk(path)
    except OSError:
        return None


def build_check(names: tuple = ("booru.css", "booru.js")) -> list:
    """(``Local Booru.exe --selfcheck`` — the release build checks its folder so) What this build lacks of what this
    module gives the program, as words: a page's file it does not find (``webui/static`` is beside the executable
    there: :data:`STATIC` is counted from this file's place), the version; [] — nothing."""
    out = [f"webui/static/{name}: файла страниц нет там, где его ищет программа ({STATIC})" for name in names
           if not static(name)]
    try:
        if not running_version():
            out.append("core/version.py: у программы нет версии")
    except ImportError as exc:
        out.append(f"core/version.py: {exc}")
    return out


# ── the folder's version ─────────────────────────────────────────────────────────────────────────────────────────
def disk_version() -> str:
    """The version the folder's ``core/version.py`` says now ("" — not read)."""
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as fh:
            found = _VERSION_LINE.search(fh.read(65536))
    except (OSError, UnicodeDecodeError):
        return ""
    return found.group(1) if found else ""


def running_version() -> str:
    """The version of the code that runs (``core/version.py`` as it was at the start)."""
    from core.version import VERSION
    return VERSION


def _look() -> None:
    """One look at the folder's version.  The file as it was at the last look — its version counts; another file —
    its version is read and counts from the next look (the update may be half copied now)."""
    try:
        st = os.stat(VERSION_FILE)
    except OSError:
        return                                # (the disk away for a moment, the file being replaced): what was known stays
    stamp = (st.st_mtime_ns, st.st_size)
    if stamp != _STATE["stamp"]:
        found = disk_version()
        if found:
            _STATE["stamp"], _STATE["disk"] = stamp, found
        return
    disk = _STATE["disk"]
    waiting = disk if disk and disk != running_version() else ""
    if waiting and not _STATE["lines"]:
        _keep_lines()
    _STATE["waiting"] = waiting


def _watch() -> None:
    try:
        _read_everything()
    except OSError as exc:                    # (a folder that cannot be listed: its files are read when needed)
        _LOG.warning("the program's code was not read whole at the start: %s", exc)
    while not _STOP.is_set():
        try:
            _look()
        except (OSError, ImportError, ValueError) as exc:     # one look less; the next one is due
            _LOG.debug("the folder's version not looked at: %s", exc)
        if _STOP.wait(LOOK_EVERY):
            return


def update_waiting() -> str:
    """The version the program's folder holds when it is not the one running — an update was installed and works from
    the next start; "" — the same one (or not known, or the code is read from the disk as it changes).  From memory:
    the folder is looked at by the thread of :func:`install`."""
    return str(_STATE["waiting"] or "")
