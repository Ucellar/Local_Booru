"""«Удалить скачанные» of a subscription: its download sessions — the folders
``output/subscriptions/<site>/<query>/<time of the check>/`` (``downloader_utils.download_post_file``) — are listed
with their files, and the chosen ones go to the trash (``library_lifecycle.trash_media_paths``: a card of every file,
recoverable; a copy of the base first).  Only picture and video files move; a folder is removed only when it is
empty afterwards.  A subscription being checked is refused (its session is being written), as is a time when
«Обслуживание» has the library.

The checkpoint of the subscription is reset after «всё» (the old page did the same): the seed cache and the trash
keep what was downloaded from being downloaded again.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

_LOG = logging.getLogger("local_booru.subscriptions")
FLAT = "(файлы без папки сессии)"


class CleanupError(Exception):
    """Said to the person as it is."""


def _base(settings: dict) -> Path:
    from core.paths import result_output_base
    return result_output_base(settings) / "subscriptions"


def _media(folder: Path, *, flat: bool) -> list:
    from core.media_utils import is_media
    try:
        items = folder.iterdir() if flat else folder.rglob("*")
        return sorted(p for p in items if p.is_file() and is_media(p))
    except OSError as exc:  # a folder gone meanwhile: nothing of it is listed
        _LOG.warning("subscription session %s not read: %s", folder, exc)
        return []


def sessions(settings: dict, sub: dict) -> list:
    """The download sessions of ``sub``, oldest first per site: {"key", "site", "name", "files", "bytes", "flat"}.
    ``key`` is the folder relative to «subscriptions» (what the page sends back)."""
    from core.downloader_utils import _safe
    from core.subscriptions import normalize_sites
    base = _base(settings)
    out, seen = [], set()
    for cfg in normalize_sites(sub):
        site = str(cfg.get("site") or "")
        queries = [str(sub.get("query") or "")]             # the folder the downloads go to
        override = str(cfg.get("query_override") or "").strip()
        if override and override not in queries:            # and one an older version may have used
            queries.append(override)
        for query in queries:
            folder = base / _safe(site) / _safe(query or "misc")
            if not folder.is_dir() or folder in seen or not _own(base, folder):
                continue
            seen.add(folder)
            flat_files = _media(folder, flat=True)
            if flat_files:
                out.append(_session(base, folder, site, FLAT, flat_files, flat=True))
            for child in sorted(p for p in folder.iterdir() if p.is_dir()):
                out.append(_session(base, child, site, child.name, _media(child, flat=False), flat=False))
    return out


def _own(base: Path, folder: Path) -> bool:
    """``folder`` is a query folder two levels inside «subscriptions» (``<site>/<query>``), whatever its name says."""
    try:
        return folder.resolve().parent.parent == base.resolve()
    except OSError:
        return False


def _session(base: Path, folder: Path, site: str, name: str, files: list, *, flat: bool) -> dict:
    size = 0
    for f in files:
        try:
            size += f.stat().st_size
        except OSError:
            pass
    key = folder.relative_to(base).as_posix() + ("/." if flat else "")
    return {"key": key, "site": site, "name": name, "files": len(files), "bytes": size, "flat": flat}


def chosen(settings: dict, sub: dict, *, keys: list | None = None, mode: str = "") -> list:
    """The sessions to empty: ``mode`` «all», «keep_last» (all but the newest of each site) — or the listed ``keys``
    (only keys of this subscription's own sessions count)."""
    every = sessions(settings, sub)
    if mode == "all":
        return every
    if mode == "keep_last":
        newest: dict = {}
        for item in every:                                  # a session's name is the time of its check
            if not item["flat"] and (item["site"] not in newest or item["name"] > newest[item["site"]]["name"]):
                newest[item["site"]] = item
        kept = {item["key"] for item in newest.values()}
        return [item for item in every if item["key"] not in kept]
    wanted = {str(k) for k in keys or []}
    return [item for item in every if item["key"] in wanted]


def busy_reason(sub_id: str) -> str:
    """Why the subscription's downloads cannot be touched now ("" — they can).  Any check of subscriptions and any
    download of the grabber or «Скачать тег» counts: they can write into the same folders (a query of the same
    words, a long query cut to the same name)."""
    from core import subscription_jobs
    from core.nomatch_manual import library_busy
    running = subscription_jobs.running_ids()
    if str(sub_id) in running:
        return "Эта подписка сейчас проверяется — остановите проверку или дождитесь конца"
    if running:
        return ("Сейчас идёт проверка подписок или скачивание граббера — его файлы могут писаться в эти же папки; "
                "дождитесь конца")
    return library_busy()


def _folder_keys(sub: dict) -> set:
    from core.downloader_utils import _safe
    from core.subscriptions import normalize_sites
    out = set()
    for cfg in normalize_sites(sub):
        site = str(cfg.get("site") or "")
        for query in {str(sub.get("query") or ""), str(cfg.get("query_override") or "").strip()} - {""} or {""}:
            out.add(os.path.normcase(f"{_safe(site)}/{_safe(query or 'misc')}"))
    return out


def sharing(sub: dict) -> list:
    """Other subscriptions whose downloads land in this one's folders (the same query on the same site, or a long
    query cut to the same name): their files in these sessions go too."""
    from core.subscriptions import load_subscriptions
    mine = _folder_keys(sub)
    return [str(other.get("query") or other.get("name") or other.get("id")) for other in load_subscriptions()
            if str(other.get("id")) != str(sub.get("id")) and _folder_keys(other) & mine]


PORTION = 200          # files one write of the base takes: the rest of the program waits for one portion, not all


def trash_sessions(settings: dict, sub: dict, picked: list, *, reset_checkpoint: bool = False,
                   hold=None, progress: Callable[[str], None] | None = None,
                   stop_check: Callable[[], bool] | None = None) -> dict:
    """Move the media files of the ``picked`` sessions (from ``chosen``) to the trash, in portions (``stop_check`` —
    between them); remove the folders left empty.  ``hold(paths)`` — a context in which nothing sends those files
    (the web server's ``FILE_STREAMS.holding``).  Returns {"moved", "files", "kept_folders", "errors", "stopped"} or
    raises CleanupError."""
    import contextlib
    from core.library_lifecycle import trash_media_paths
    say = progress or (lambda _m: None)
    why = busy_reason(str(sub.get("id") or ""))
    if why:
        raise CleanupError(why)
    base = _base(settings).resolve()
    files: list = []
    folders: list = []
    for item in picked:
        folder = (_base(settings) / item["key"].removesuffix("/.")).resolve()
        if base not in folder.parents:                       # never anything outside «subscriptions»
            raise CleanupError(f"Папка {folder} не в папке подписок — не трогаю")
        files += _media(folder, flat=item["flat"])
        if not item["flat"]:
            folders.append(folder)
    if not files:
        raise CleanupError("В выбранных сессиях нет картинок и видео")
    say(f"В корзину: {len(files)} файлов из {len(picked)} сессий (сначала — копия базы)")
    moved = errors = 0
    stopped = False
    for start in range(0, len(files), PORTION):
        if start and stop_check is not None and stop_check():
            stopped = True
            say(f"Остановлено: перенесено {moved}, остальные {len(files) - start} файлов остались на месте")
            break
        part = files[start:start + PORTION]
        with (hold(*part) if hold is not None else contextlib.nullcontext()):
            # the copy of the base is made once: the next portions reuse it (it is minutes old)
            result = trash_media_paths(settings, part, reason="subscription_session_cleanup", make_backup=True)
        if result.get("error"):
            raise CleanupError(f"Не перенесено: {result['error']}" + (f" (перенесено до этого: {moved})" if moved else ""))
        if not start and result.get("backup"):
            say(f"Копия базы: {result['backup']}")
        moved += int(result.get("trashed_files", 0) or 0)
        errors += int(result.get("errors", 0) or 0)
        if len(files) > PORTION:
            say(f"  {min(start + PORTION, len(files))} из {len(files)}")
    kept = 0
    for folder in folders:
        for child in sorted((p for p in folder.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                child.rmdir()
            except OSError:
                pass                                         # not empty: something that is not a picture stays
        try:
            folder.rmdir()
        except OSError:
            kept += 1
    if reset_checkpoint and not stopped:
        from core.subscriptions import update_subscription
        update_subscription(str(sub.get("id") or ""), downloaded_count=0, last_post_ids={})
        say("Счётчик и место, с которого проверять, сброшены; скачанное не скачается снова: оно в корзине и в "
            "списке подписки")
    say(f"Перенесено в корзину: {moved} из {len(files)}" + (f"; папок осталось (в них не картинки): {kept}" if kept else ""))
    return {"moved": moved, "files": len(files), "kept_folders": kept, "errors": errors, "stopped": stopped}
