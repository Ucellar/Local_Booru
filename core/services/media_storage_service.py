"""The only destructive filesystem gateway for managed library media.

Original archive bytes are immutable.  This service centralises move/delete
operations on generated output so future UI/service code does not call
``unlink`` or ``shutil.move`` on media paths directly.
"""
from __future__ import annotations

import contextlib
import errno
import glob
import os
import re
import shutil
import time
from pathlib import Path
from core.source_protection import require_managed_media_mutation, is_managed_media_path
from core.file_safety import atomic_copy2
from core.media_utils import file_md5
from core.stability import swallow


_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-zА-Яа-я._()\-\[\] ]+")


def _safe_stem(value: str, *, max_len: int = 80) -> str:
    stem = Path(str(value or "media")).stem.strip().strip(".")
    stem = _SAFE_NAME_RE.sub("_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._")
    if not stem:
        stem = "media"
    if len(stem) > max_len:
        stem = stem[:max_len].rstrip(" ._") or "media"
    return stem


def content_addressed_name(original_name: str, md5: str, *, ext: str | None = None) -> str:
    """Return a stable collision-proof display filename for managed media.

    The original filename is not unique enough for Telegram/Discord/booru dumps:
    many unrelated files may be called ``48.jpg`` or ``photo_2022...jpg``.  The
    managed library filename therefore always contains the real file MD5.  The
    user-friendly part is kept only as decoration; SQLite remains keyed by the
    row id and exact hash.
    """
    md5 = str(md5 or "").strip().lower()
    if not _MD5_RE.match(md5):
        raise ValueError("content_addressed_name requires a real 32-char MD5")
    original = Path(str(original_name or "media"))
    suffix = str(ext if ext is not None else original.suffix or "").lower()
    if suffix and not suffix.startswith("."):
        suffix = "." + suffix
    stem = _safe_stem(original.stem)
    # Already content-addressed: keep it stable instead of appending twice.
    if stem.lower() == md5 or stem.lower().endswith("__" + md5[:12]) or stem.lower().endswith("__" + md5):
        return stem + suffix
    return f"{stem}__{md5[:12]}{suffix}"


def content_addressed_path(destination: str | Path, md5: str, *, original_name: str | None = None) -> Path:
    dest = Path(destination)
    name = content_addressed_name(original_name or dest.name, md5, ext=dest.suffix)
    return dest.with_name(name)


def _path_md5(path: Path) -> str:
    try:
        return file_md5(path).lower()
    except Exception:
        return ""


def _first_free_variant(dest: Path, md5: str) -> Path:
    """Find a deterministic free path without overwriting different bytes.

    In normal operation the MD5 suffix makes collisions impossible.  This is a
    final guard for pathological cases (case-insensitive filesystems, changed
    extension, manual user files in output, etc.).
    """
    if not dest.exists():
        return dest
    if _path_md5(dest) == str(md5 or "").lower():
        return dest
    base, ext = dest.stem, dest.suffix
    for n in range(1, 10000):
        candidate = dest.with_name(f"{base}_{n}{ext}")
        if not candidate.exists():
            return candidate
        if _path_md5(candidate) == str(md5 or "").lower():
            return candidate
    raise FileExistsError(f"Could not allocate a safe managed filename near {dest}")

def can_mutate(settings: dict, path: str | Path, operation: str) -> bool:
    return require_managed_media_mutation(settings, Path(path), operation)


def _retry_windows_lock(action, *, attempts: int = 6):
    """Retry transient Windows sharing violations after viewers release media.

    Qt's QMovie and external previewers can keep a GIF/video file open for a
    fraction of a second while a delete/move operation begins. Other failures
    are re-raised immediately.
    """
    last_error = None
    for attempt in range(max(1, int(attempts))):
        try:
            return action()
        except OSError as exc:
            last_error = exc
            winerror = getattr(exc, "winerror", None)
            if winerror not in (32, 33) and getattr(exc, "errno", None) not in (13,):
                raise
            if attempt >= attempts - 1:
                raise
            time.sleep(0.08 * (attempt + 1))
    if last_error:
        raise last_error


def _cross_device(exc: OSError) -> bool:
    return getattr(exc, "errno", None) == errno.EXDEV or getattr(exc, "winerror", None) == 17   # ERROR_NOT_SAME_DEVICE


def move_file(src: Path, dest: Path) -> None:
    """Move one file and never leave two copies of it.

    ``shutil.move`` copies and then deletes whenever the rename fails — also when it fails because
    the file is open (Windows: a viewer, the gallery sending it).  Then the copy succeeds, the delete
    fails, and every retry left one more full copy in the trash or the library.  Here the rename is
    the move; only a move to another drive copies, and a copy whose source cannot be deleted is
    removed again.  An existing destination is never overwritten.
    """
    src, dest = Path(src), Path(dest)
    if dest.exists():
        raise FileExistsError(errno.EEXIST, "the destination already exists", str(dest))
    try:
        os.rename(src, dest)
        return
    except OSError as exc:
        if not _cross_device(exc):
            raise                 # a locked file: the caller retries; nothing was copied
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.moving")
    try:
        shutil.copy2(src, tmp)
        os.rename(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    try:
        os.unlink(src)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(dest)        # the source stays where it was: one copy, as before the move
        raise


def unlink_managed(settings: dict, path: str | Path, *, operation: str) -> bool:
    p = Path(path)
    if not can_mutate(settings, p, operation):
        return False
    if p.exists() and p.is_file():
        _retry_windows_lock(lambda: p.unlink(missing_ok=True))
    return True


def move_managed(settings: dict, source: str | Path, destination: str | Path, *, operation: str) -> bool:
    src, dest = Path(source), Path(destination)
    if not can_mutate(settings, src, operation):
        return False
    if not is_managed_media_path(settings, dest):
        require_managed_media_mutation(settings, dest, operation + '.destination')
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    _retry_windows_lock(lambda: move_file(src, dest))
    return True


def copy_into_managed(settings: dict, source: str | Path, destination: str | Path, *, operation: str = 'copy_into_library', hash_md5: str | None = None) -> Path:
    """Copy immutable input bytes to output under a collision-proof filename.

    Old builds used the original basename (``48.jpg`` -> ``48.jpg``).  That is
    unsafe because unrelated media from Telegram/Discord/booru dumps often share
    the same filename.  New imports use ``original__md5prefix.ext`` and only
    reuse an existing physical file when the bytes are exact-MD5 identical.
    """
    src = Path(source)
    dest = Path(destination)
    if not is_managed_media_path(settings, dest):
        require_managed_media_mutation(settings, dest, operation + '.destination')
        raise PermissionError(f'Destination outside working library: {dest}')
    md5 = str(hash_md5 or "").strip().lower()
    if not md5:
        md5 = _path_md5(src)
    if md5:
        dest = content_addressed_path(dest, md5, original_name=src.name)
    if dest.exists():
        matched_existing = False
        with swallow("media_storage.verify_existing_destination_md5"):
            if md5 and _path_md5(dest) == md5:
                matched_existing = True
            elif not md5 and file_md5(src).lower() == file_md5(dest).lower():
                matched_existing = True
        if matched_existing:
            return dest
        if md5:
            dest = _first_free_variant(dest, md5)
        else:
            base, ext = dest.stem, dest.suffix
            n = 1
            while dest.exists():
                candidate = dest.with_name(f"{base}_{n}{ext}")
                if candidate.exists():
                    matched_candidate = False
                    with swallow("media_storage.verify_collision_candidate_md5"):
                        matched_candidate = file_md5(src).lower() == file_md5(candidate).lower()
                    if matched_candidate:
                        return candidate
                    n += 1
                    continue
                dest = candidate
                break
    copied = atomic_copy2(src, dest)
    if md5:
        # The copy has the source's bytes: remember its MD5 so the import does not read it again.
        with swallow("media_storage.remember_copy_md5"):
            from core.file_hash_cache import upsert as _remember_md5
            _remember_md5(settings, copied, hash_md5=md5)
    return copied


def normalize_managed_content_name(settings: dict, path: str | Path, md5: str, *, operation: str = 'normalize_content_filename', original_name: str | None = None) -> Path:
    """Rename an existing managed media file to ``original__md5.ext``.

    Used after downloads, where the exact content hash is known only after the
    temporary download has finished.  Never overwrites different content.
    """
    src = Path(path)
    md5 = str(md5 or "").strip().lower()
    if not md5 or not src.exists() or not src.is_file():
        return src
    if not can_mutate(settings, src, operation):
        return src
    target = content_addressed_path(src, md5, original_name=original_name or src.name)
    target = _first_free_variant(target, md5)
    try:
        if src.resolve() == target.resolve():
            return src
    except Exception:
        if str(src) == str(target):
            return src
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and _path_md5(target) == md5:
        # Same bytes already exist at the safe name.  The current file is a
        # transient duplicate inside managed output, so discard it.
        _retry_windows_lock(lambda: src.unlink(missing_ok=True))
        return target
    _retry_windows_lock(lambda: move_file(src, target))
    return target

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
        # Exactly this file's sidecars: "<stem>.tags.txt", "<stem>.searched.json"...
        # (a bare "<stem>*" also matched other files: "1.jpg" -> "10.json").
        for item in d.glob(glob.escape(p.stem) + '.*'):
            if item.is_file() and is_managed_media_path(settings, item):
                item.unlink(missing_ok=True)
                removed += 1
    return removed
