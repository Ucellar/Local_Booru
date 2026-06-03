"""The only destructive filesystem gateway for managed library media.

Original archive bytes are immutable.  This service centralises move/delete
operations on generated output so future UI/service code does not call
``unlink`` or ``shutil.move`` on media paths directly.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from core.source_protection import require_managed_media_mutation, is_managed_media_path
from core.file_safety import atomic_copy2
from core.media_utils import file_md5


def can_mutate(settings: dict, path: str | Path, operation: str) -> bool:
    return require_managed_media_mutation(settings, Path(path), operation)


def unlink_managed(settings: dict, path: str | Path, *, operation: str) -> bool:
    p = Path(path)
    if not can_mutate(settings, p, operation):
        return False
    if p.exists() and p.is_file():
        p.unlink(missing_ok=True)
    return True


def move_managed(settings: dict, source: str | Path, destination: str | Path, *, operation: str) -> bool:
    src, dest = Path(source), Path(destination)
    if not can_mutate(settings, src, operation):
        return False
    if not is_managed_media_path(settings, dest):
        require_managed_media_mutation(settings, dest, operation + '.destination')
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return True


def copy_into_managed(settings: dict, source: str | Path, destination: str | Path, *, operation: str = 'copy_into_library') -> Path:
    """Copy immutable input bytes to output without ever overwriting other media.

    Same-name/same-byte input reuses the already written output path. A same-name
    but different media file receives a deterministic ``_1``/``_2`` suffix.
    """
    src = Path(source)
    dest = Path(destination)
    if not is_managed_media_path(settings, dest):
        require_managed_media_mutation(settings, dest, operation + '.destination')
        raise PermissionError(f'Destination outside working library: {dest}')
    if dest.exists():
        try:
            if file_md5(src).lower() == file_md5(dest).lower():
                return dest
        except Exception:
            pass
        base, ext = dest.stem, dest.suffix
        n = 1
        while dest.exists():
            candidate = dest.with_name(f"{base}_{n}{ext}")
            if candidate.exists():
                try:
                    if file_md5(src).lower() == file_md5(candidate).lower():
                        return candidate
                except Exception:
                    pass
                n += 1
                continue
            dest = candidate
            break
    return atomic_copy2(src, dest)


def delete_bucket_artifacts(settings: dict, media_path: str | Path, *, operation: str = 'delete_side_artifacts') -> int:
    p = Path(media_path)
    if not can_mutate(settings, p, operation):
        return 0
    bucket = p.parent.parent if p.parent.name == 'media' else p.parent
    removed = 0
    for sub in ('cache', 'searched', 'tags', 'source'):
        d = bucket / sub
        if not d.exists():
            continue
        for item in d.glob(p.stem + '*'):
            if item.is_file() and is_managed_media_path(settings, item):
                item.unlink(missing_ok=True)
                removed += 1
    return removed
