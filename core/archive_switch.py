"""«Настройки → Папки»: which archive the program opens at its next start.

An archive (``Local_Booru_Archive``) is two folders: ``settings`` (the base, the settings, the cache) and ``output``
(the library's files).  The archive open now is fixed for the whole run (``core.paths.DATA_DIR``); another one is
named by the small pointer file and opens at the next start (``core.paths.choose_next_archive``).

Until then the program works with its own archive as before: its settings, base and folders do not change — and its
settings are saved to it as usual, the pointer staying on the chosen archive (``core.settings.save_settings``).  An
earlier way wrote the chosen archive's folders into the live settings: the rest of the run then downloaded, trashed
and queued into the other archive while its base stayed here.

* :func:`connect` — an existing archive (its settings, base and cookies are its own: nothing is written over them);
  choosing the archive open now takes a pending choice back (:func:`stay`).
* A new archive while the data is in «Документах» — «Создать архив» (``core/archive_create.py``: made at the next
  start); choosing an existing one takes such an order back.
"""
from __future__ import annotations

import os
from pathlib import Path

NOT_FOUND = ("В этой папке нет архива Local Booru. Подойдёт сама папка архива (Local_Booru_Archive, даже "
             "переименованная), её папка output или settings, или папка, в которой лежит Local_Booru_Archive; внутри "
             "нужен settings/config/app_settings.json или settings/db/local_booru_index.sqlite3")


def _same(a, b) -> bool:
    """One folder, however written (Windows: any letter case, either slash)."""
    def key(value) -> str:
        try:
            return os.path.normcase(str(Path(value).resolve()))
        except OSError:
            return os.path.normcase(str(Path(value).absolute()))
    return key(a) == key(b)


def open_archive() -> Path:
    """The archive of this run (the folder holding ``settings`` and ``output``; in «Документах» — that folder)."""
    from core import paths
    return paths.DATA_DIR.parent if paths.USING_SEPARATE_STORAGE else paths.DATA_DIR


def portable() -> bool:
    """The program runs from an archive (not from «Документы»)."""
    from core import paths
    return bool(paths.USING_SEPARATE_STORAGE)


def pending() -> Path | None:
    """The archive (its ``settings`` folder) chosen in this run for the next start — known without looking at any
    disk: shown also while that archive's drive is unplugged."""
    from core import paths
    return paths.next_archive()


def connect(chosen: str | Path) -> dict:
    """Make the archive in ``chosen`` the one the next start opens.  {"ok": True, "archive": …} or
    {"ok": False, "error": …}; the archive open now — {"ok": True, "stayed": True} (a pending choice is taken back)."""
    from core import paths
    target = paths.normalize_archive_settings_root(chosen)
    if target is None:
        return {"ok": False, "error": NOT_FOUND}
    if _same(target, paths.DATA_DIR):
        return stay()
    paths.choose_next_archive(target)
    from core import archive_create
    archive_create.cancel()                  # this one opens next: an archive ordered from «Документов» is not made
    return {"ok": True, "archive": str(target.parent)}


def stay() -> dict:
    """The next start opens the archive open now (a choice made in this run is taken back)."""
    from core import paths
    was = paths.next_archive()
    paths.choose_next_archive(None)
    return {"ok": True, "stayed": True, "was": str(was.parent) if was is not None else ""}
