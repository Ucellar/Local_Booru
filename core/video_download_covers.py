"""A bounded cache of the posters found by the video downloader.

Cards outlive the current title search. Keep only image bytes and opaque keys,
so a waiting or restored download can show its poster without refetching it.
"""
from __future__ import annotations

import hashlib
import io
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from core import title_covers
from core.file_safety import atomic_write_bytes

MAX_POSTERS = 256
MAX_BYTES = 256 * 1024
MAX_SIDE = 960
_KEY = re.compile(r"[0-9a-f]{64}")
_LOCK = threading.Lock()


def _directory() -> Path:
    from core.paths import CACHE_DIR

    return CACHE_DIR / "video_download_covers"


def address_key(address: str) -> str:
    """Stable image key without retaining a source address as text."""
    try:
        parts = urlsplit(address.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ""
        netloc = parts.hostname.removeprefix("www.")
        if ":" in netloc:
            netloc = f"[{netloc}]"
        if parts.port not in (None, 80 if parts.scheme == "http" else 443):
            netloc += f":{parts.port}"
        if "@" in parts.netloc:
            netloc = parts.netloc.rsplit("@", 1)[0] + "@" + netloc
        name = parts._replace(netloc=netloc, path=parts.path.rstrip("/"), fragment="").geturl()
    except ValueError:
        return ""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _fit(data: bytes) -> bytes:
    """Keep small originals and scale larger images for the banner."""
    from PIL import Image

    if not title_covers.picture_ext(data):
        return b""
    with Image.open(io.BytesIO(data)) as poster:
        if poster.width * poster.height > 24_000_000:
            return b""
        if len(data) <= MAX_BYTES and max(poster.size) <= MAX_SIDE:
            return data
        poster.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        resized = io.BytesIO()
        poster.convert("RGB").save(resized, "JPEG", quality=82)
    data = resized.getvalue()
    return data if len(data) <= MAX_BYTES else b""


def _prune(folder: Path) -> None:
    entries = []
    for path in folder.glob("*.img"):
        if _KEY.fullmatch(path.stem):
            try:
                entries.append((path.stat().st_mtime_ns, path))
            except OSError:
                continue
    entries.sort(reverse=True)
    for _mtime, old in entries[MAX_POSTERS:]:
        try:
            old.unlink(missing_ok=True)
        except OSError:
            continue


def remember(address: str, poster: tuple) -> str:
    """Keep a poster already obtained during Find and return its opaque key."""
    try:
        data = _fit(bytes(poster[0]))
        if not data:
            return ""
        key = address_key(address) or hashlib.sha256(data).hexdigest()
        folder = _directory()
        with _LOCK:
            atomic_write_bytes(folder / f"{key}.img", data)
            _prune(folder)
        return key
    except Exception:  # noqa: BLE001 - caching a poster must never fail the search for downloadable files
        return ""


def picture(address: str = "", *, key: str = "") -> tuple[bytes, str] | None:
    """Read a cached image locally. Old queue entries can resolve by address."""
    number = key or address_key(address)
    if not _KEY.fullmatch(number):
        return None
    path = _directory() / f"{number}.img"
    try:
        if path.is_symlink():
            return None
        with path.open("rb") as file:
            data = file.read(MAX_BYTES + 1)
    except OSError:
        return None
    ext = title_covers.picture_ext(data)
    return (data, title_covers.content_type(ext)) if ext and len(data) <= MAX_BYTES else None
