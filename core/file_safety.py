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
    except Exception:
        pass


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
        try:
            part.unlink(missing_ok=True)
        except Exception:
            pass
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
        try:
            part.unlink(missing_ok=True)
        except Exception:
            pass
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
        try:
            part.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def cleanup_partial_files(root: str | Path) -> int:
    root = Path(root)
    if not root.exists() or not root.is_dir():
        return 0
    removed = 0
    for part in root.rglob("*.part"):
        try:
            if part.is_file():
                part.unlink()
                removed += 1
        except Exception:
            pass
    return removed
