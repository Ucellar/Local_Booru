"""Crash-safe filesystem operations for downloaded and copied media.

Media is first written to a sibling ``.part`` file and only moved into its
final name after the write has completed and the data is flushed to disk.
A power loss may leave a disposable .part file, but should not leave a
truncated file pretending to be a finished download.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable, Iterable, Optional
from core.stability import swallow


def _part_path(destination: str | Path) -> Path:
    dest = Path(destination)
    return dest.with_name(dest.name + ".part")


def _replace_after_flush(part: Path, destination: Path) -> None:
    os.replace(str(part), str(destination))
    try:
        # Best effort: make the directory entry durable on platforms that allow it.
        flags = getattr(os, "O_DIRECTORY", 0)
        if flags:
            fd = os.open(str(destination.parent), flags)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except Exception as exc:
        with swallow("file_safety.fsync_parent_directory"):
            raise exc


def atomic_write_bytes(destination: str | Path, data: bytes) -> Path:
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = _part_path(dest)
    try:
        with part.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_after_flush(part, dest)
        return dest
    except Exception:
        with swallow("file_safety.cleanup_partial_write"):
            part.unlink(missing_ok=True)
        raise


def atomic_copy2(source: str | Path, destination: str | Path) -> Path:
    src = Path(source)
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = _part_path(dest)
    try:
        shutil.copy2(src, part)
        with part.open("rb+") as fh:
            os.fsync(fh.fileno())
        if src.stat().st_size != part.stat().st_size:
            raise IOError(f"Incomplete copy: {src.name}")
        _replace_after_flush(part, dest)
        return dest
    except Exception:
        with swallow("file_safety.cleanup_partial_copy"):
            part.unlink(missing_ok=True)
        raise


def atomic_write_chunks(
    destination: str | Path,
    chunks: Iterable[bytes],
    *,
    should_stop: Optional[Callable[[], bool]] = None,
    before_chunk: Optional[Callable[[], None]] = None,
) -> tuple[Path, int]:
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = _part_path(dest)
    total = 0
    try:
        with part.open("wb") as fh:
            for chunk in chunks:
                if before_chunk is not None:
                    before_chunk()
                if should_stop is not None and should_stop():
                    raise InterruptedError("Download cancelled")
                if chunk:
                    fh.write(chunk)
                    total += len(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_after_flush(part, dest)
        return dest, total
    except Exception:
        with swallow("file_safety.cleanup_partial_chunks"):
            part.unlink(missing_ok=True)
        raise


def cleanup_partial_files(root: str | Path, *, settings: dict | None = None) -> int:
    """Delete the «*.part» leftovers of interrupted downloads under ``root``; with ``settings``, only those in the
    program's own places (a source folder kept in the output folder is the user's — its files are not touched)."""
    root = Path(root)
    if not root.exists() or not root.is_dir():
        return 0
    if settings is not None:
        from core.source_protection import is_managed_media_path
    removed = 0
    for part in root.rglob("*.part"):
        if settings is not None and not is_managed_media_path(settings, part):
            continue
        try:
            if part.is_file():
                part.unlink()
                removed += 1
        except Exception as exc:
            with swallow("file_safety.cleanup_stale_part"):
                raise exc
    return removed


# ── small JSON files the user cannot rebuild (settings, subscriptions) ─────────
def read_json_kept(path: str | Path, expected: type, *, what: str = "file"):
    """Parsed JSON of ``path`` when it is an ``expected`` (dict, list), else None.

    A damaged file (power cut while writing, disk error) is renamed to ``<name>.broken-<time>``
    and never overwritten; the last good copy ``<name>.bak`` (kept by :func:`write_json_kept`)
    takes its place, so the next read and the next save start from it, not from nothing.
    """
    import json
    import logging
    import time
    log = logging.getLogger("local_booru.file_safety")
    path = Path(path)
    if not path.exists():
        return None
    text = _read_text_retrying(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, expected):
            return data
        raise ValueError(f"not a JSON {expected.__name__}")
    except ValueError as exc:
        broken = path.with_name(path.name + time.strftime(".broken-%Y%m%d-%H%M%S"))
        try:
            path.replace(broken)
        except OSError:
            broken = path
        log.error("%s is damaged (%s); kept as %s", what, exc, broken)
    backup = path.with_name(path.name + ".bak")
    try:
        text = backup.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, ValueError):
        return None
    if not isinstance(data, expected):
        return None
    try:
        if broken != path:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
    except OSError as exc:
        log.warning("%s: last good copy not put back (%s)", what, exc)
    log.warning("%s restored from the last good copy %s", what, backup)
    return data


_BUSY_TRIES = 20            # × 0.1 s: how long a file locked by someone else (a save, an antivirus) is waited for


def _read_text_retrying(path: Path) -> str | None:
    """The text of ``path``; None when it is gone.  A file that cannot be opened for a moment (on
    Windows: another thread replacing it, an antivirus scanning it) is read again a little later
    and, if it stays locked, the error goes up — it is NOT damaged: taking it for damaged put the
    previous version (``.bak``) in its place and lost the last change."""
    import time
    last: OSError | None = None
    for _ in range(_BUSY_TRIES):
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            last = exc
            time.sleep(0.1)
    raise last if last is not None else OSError(f"cannot read {path}")


def _replace_retrying(src: Path, dst: Path) -> None:
    """``src.replace(dst)``, waiting a little while ``dst`` is held open by someone (Windows)."""
    import time
    for attempt in range(_BUSY_TRIES):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if attempt == _BUSY_TRIES - 1:
                raise
            time.sleep(0.1)


def write_json_kept(path: str | Path, text: str, expected: type) -> None:
    """Write ``text`` to ``path`` atomically; the previous file, when it parses as ``expected``,
    becomes ``<name>.bak`` (a damaged one never replaces a good copy)."""
    import json
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous = path.read_text(encoding="utf-8")
        if isinstance(json.loads(previous), expected):
            backup = path.with_name(path.name + ".bak")
            tmp_backup = backup.with_suffix(backup.suffix + ".tmp")
            tmp_backup.write_text(previous, encoding="utf-8")
            _replace_retrying(tmp_backup, backup)
    except (OSError, ValueError):
        pass        # no previous file, or it is damaged
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    _replace_retrying(tmp, path)
