"""The base's file read through in a row, once after a start (v5.44.152).

The owner (10.11.2026): «некоторые страницы открываются по десять лет».  A page of the gallery reads hundreds of
places of the base, scattered over its file, and the check of the base the program runs behind its window after
every start (``stability.run_deferred_db_health_check``: PRAGMA quick_check) reads all of it — 150 thousand reads for
a base of 500 MB, in the order of its trees, not of the file.  From the system's memory all of that is a second.  From
a disk that has not given the file yet — the computer or the program has just started, another program took the
memory — every place is a question to the disk, and a hard disk answers 100–200 of them a second: the check held the
disk for minutes while the first pages waited for the same disk.

Read in a row, the same file comes at the disk's full speed (a second or two a gigabyte from an SSD, ten from a hard
disk), and the system keeps what was read: the check and the pages after it read the base from memory.  So the file
is read through first — before that check (it calls :func:`read_base`), and as a duty of its own where the check does
not run (``background_duties``: «base»).  Nothing is done with what is read.

The reading stands in nobody's way:

- the pages' own questions to the disk go first (Windows: the file is opened with a low priority of its reads —
  ``FileIoPriorityHintInfo``; the thread's own priority is not touched: a thread of Python held back by the system
  while it has the interpreter would hold every other thread of the program);
- the file is opened for each piece and closed again, and shared for writing, renaming and deleting (Windows: a file
  held open the usual way is not renamed — «Перенести архив», a copy of the base put back);
- not more than half of the memory free now is read (:func:`most`: what does not fit pushes out itself), and not
  longer than :data:`LONGEST` (the check that follows may be what the program's writes wait for).

Once a run of the program: a second call waits for the first and returns what it measured (``READ``; «Проверка →
Медленные страницы» shows it: how fast the disk gave the base).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

_LOG = logging.getLogger("local_booru.base_warm")

PIECE = 1024 * 1024                  # bytes read at a time: a page's question waits behind one piece at most
MOST = 8 * 1024 ** 3                 # bytes read through at most
MOST_UNKNOWN = 2 * 1024 ** 3         # …when the system does not say how much memory is free
LONGEST = 120.0                      # seconds the reading may take
READ: dict = {}                      # what the reading took: {"bytes", "size", "seconds", "at", "way"}
_ONCE = threading.Lock()
_PLAIN = {"only": os.name != "nt"}   # the usual ``open`` (not Windows; Windows' shared one failed once)

_GENERIC_READ = 0x80000000
_SHARE_ALL = 0x1 | 0x2 | 0x4         # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
_OPEN_EXISTING = 3
_SEQUENTIAL_SCAN = 0x08000000
_IO_PRIORITY_HINT = 12               # FILE_INFO_BY_HANDLE_CLASS.FileIoPriorityHintInfo
_IO_LOW = 1                          # PRIORITY_HINT.IoPriorityHintLow: after the usual reads, never held back


def most() -> int:
    """How much of the base is worth reading through now: half of the memory that is free, :data:`MOST` at most."""
    try:
        from core.memory_guard import process_memory_snapshot
        free = int(process_memory_snapshot().get("available_bytes") or 0)
    except Exception as exc:                             # the system does not say: a careful guess
        _LOG.debug("free memory not known: %s", exc)
        free = 0
    return min(MOST, free // 2) if free > 0 else min(MOST, MOST_UNKNOWN)


_WINDOWS: dict = {}                  # (Windows) the system's functions with their types, made once


def _windows() -> dict:
    """``CreateFileW``, ``CloseHandle`` and ``SetFileInformationByHandle`` of the system, typed — and the one
    ``FILE_IO_PRIORITY_HINT_INFO`` every opening passes (nothing writes to it)."""
    if not _WINDOWS:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)   # its own copy: the types set here are its own
        create = kernel32.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                           wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel32.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        set_info = kernel32.SetFileInformationByHandle
        set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        set_info.restype = wintypes.BOOL

        class Hint(ctypes.Structure):
            _fields_ = [("PriorityHint", ctypes.c_int)]
        _WINDOWS.update(kernel32=kernel32, create=create, close=close, set_info=set_info, hint=Hint(_IO_LOW),
                        invalid=ctypes.c_void_p(-1).value)
    return _WINDOWS


def _open_shared(path: str):
    """(Windows) ``path`` opened for reading so that nobody is refused anything meanwhile — it may be written,
    renamed, deleted — and its reads wait behind everyone else's."""
    import ctypes
    import msvcrt
    w = _windows()
    handle = w["create"](path, _GENERIC_READ, _SHARE_ALL, None, _OPEN_EXISTING, _SEQUENTIAL_SCAN, None)
    if handle is None or handle == w["invalid"]:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        hint = w["hint"]
        w["set_info"](handle, _IO_PRIORITY_HINT, ctypes.byref(hint), ctypes.sizeof(hint))    # refused: read as usual
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except BaseException:
        w["close"](handle)
        raise
    try:
        return os.fdopen(fd, "rb", buffering=0)         # closing it closes the handle
    except BaseException:
        os.close(fd)
        raise


def _piece(path: str, at: int) -> bytes:
    """``PIECE`` bytes of ``path`` from ``at`` (b"" — its end); the file is not held between two pieces."""
    refused = None
    if not _PLAIN["only"]:
        try:
            with _open_shared(path) as fh:
                fh.seek(at)
                return fh.read(PIECE)
        except Exception as exc:                         # the file is not there — or the shared way is not
            refused = exc
    with open(path, "rb", buffering=0) as fh:           # (an error of the file itself goes to the caller from here)
        fh.seek(at)
        piece = fh.read(PIECE)
    if refused is not None:                              # the usual way read what the shared one did not: it is
        _LOG.info("the base is read the usual way (%s: %s)", type(refused).__name__, refused)      # used from now on
        _PLAIN["only"] = True
    return piece


def read_base(settings: dict, stop: Callable[[], bool] | None = None) -> dict:
    """The file of the base, and its journal, read from the beginning to the end (once a run of the program).
    -> {"bytes": read, "size": of the two files, "seconds", "at", "way": "shared" | "plain"} — kept in ``READ``."""
    with _ONCE:
        if READ:
            return dict(READ)
        from core.database.connection import db_path
        path = str(db_path(settings))
        limit, done, size, began = most(), 0, 0, time.monotonic()
        for name in (path, path + "-wal"):
            try:
                size += os.path.getsize(name)
            except OSError:
                continue                                     # no journal now; no base yet: nothing to read
            at = 0
            while done < limit and time.monotonic() - began < LONGEST and not (stop is not None and stop()):
                try:
                    piece = _piece(name, at)
                except OSError as exc:                       # a disk that went away, a file taken: the pages go on
                    _LOG.info("the base not read through: %s: %s", type(exc).__name__, exc)
                    break
                if not piece:
                    break
                at += len(piece)
                done += len(piece)
        took = time.monotonic() - began
        READ.update({"bytes": done, "size": size, "seconds": round(took, 2), "at": time.time(),
                     "way": "plain" if _PLAIN["only"] else "shared"})
        if done:
            _LOG.info("the base read through at the start: %.0f MB of %.0f in %.1f s", done / 1048576, size / 1048576, took)
        return dict(READ)


def forget() -> None:
    """Read again next time (the checks)."""
    with _ONCE:
        READ.clear()
