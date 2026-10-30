"""Prepare compact JPEG payloads for perceptual reverse-image services.

IQDB/SauceNAO/e621 IQDB do not need the original byte-perfect media.  Uploading
an 8-20 MB PNG wastes bandwidth and keeps a branch occupied for seconds.  This
module creates a cached, bounded 512 px JPEG keyed by the original byte MD5.
The cache stores only derived JPEGs under settings/cache and never touches the
user's source archive.
"""
from __future__ import annotations

import os
import time
import threading
from contextlib import nullcontext
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from core.file_hash_cache import get_or_compute_md5
from core.paths import CACHE_DIR
from core.tagger.hashing import file_md5

_DEFAULT_MAX_EDGE = 512
_DEFAULT_QUALITY = 85
_DEFAULT_REUSE_MAX_KB = 256
_DEFAULT_CACHE_FILES = 5000
_DEFAULT_CACHE_AGE_DAYS = 30
_DEFAULT_MAX_MEGAPIXELS = 64.0
_UPLOAD_LOCKS = tuple(threading.RLock() for _ in range(64))


def _emit(log: Callable[[str], None] | None, message: str) -> None:
    if not log:
        return
    try:
        log(str(message))
    except Exception:
        pass


def _setting_float(settings, key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float((settings or {}).get(key, default) or default)
    except Exception:
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


def _setting_int(settings, key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int((settings or {}).get(key, default) or default)
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _cache_dir() -> Path:
    target = Path(CACHE_DIR) / "reverse_upload"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _cleanup_cache(settings=None) -> None:
    """Cheap bounded cleanup; runs at most once per process every five minutes."""
    now = time.time()
    last = float(getattr(_cleanup_cache, "_last", 0.0) or 0.0)
    if now - last < 300.0:
        return
    _cleanup_cache._last = now
    root = _cache_dir()
    max_files = _setting_int(settings, "reverse_upload_cache_max_files", _DEFAULT_CACHE_FILES, 100, 100000)
    max_age_days = _setting_int(settings, "reverse_upload_cache_max_age_days", _DEFAULT_CACHE_AGE_DAYS, 1, 3650)
    cutoff = now - max_age_days * 86400
    rows = []
    try:
        for p in root.glob("*.jpg"):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                try:
                    p.unlink()
                except OSError:
                    pass
                continue
            rows.append((st.st_mtime, p))
        if len(rows) > max_files:
            rows.sort(key=lambda item: item[0])
            for _mtime, p in rows[: len(rows) - max_files]:
                try:
                    p.unlink()
                except OSError:
                    pass
    except Exception:
        return


def _safe_source_md5(path: Path, settings=None) -> str:
    try:
        value, _cached = get_or_compute_md5(settings, path)
        return str(value or "").strip().lower()
    except Exception:
        try:
            return file_md5(path).lower()
        except Exception:
            return ""


def _upload_lock(source_md5: str):
    try:
        index = int(str(source_md5 or "0")[:2], 16) % len(_UPLOAD_LOCKS)
    except Exception:
        index = 0
    return _UPLOAD_LOCKS[index]


def reverse_upload_image(path, settings=None, log=None) -> Path:
    """Return an original or cached compact JPEG suitable for reverse upload.

    The function is intentionally conservative: if Pillow cannot safely prepare
    a JPEG, it returns the original path and lets the existing branch behavior
    decide how to handle the service response.
    """
    source = Path(path)
    if not bool((settings or {}).get("reverse_upload_downscale", True)):
        return source
    if not source.exists() or not source.is_file():
        return source

    max_edge = _setting_int(settings, "reverse_upload_max_edge", _DEFAULT_MAX_EDGE, 128, 4096)
    quality = _setting_int(settings, "reverse_upload_jpeg_quality", _DEFAULT_QUALITY, 50, 95)
    reuse_max_kb = _setting_int(settings, "reverse_upload_reuse_small_jpeg_kb", _DEFAULT_REUSE_MAX_KB, 16, 4096)
    max_megapixels = _setting_float(
        settings,
        "local_preflight_max_megapixels",
        _DEFAULT_MAX_MEGAPIXELS,
        1.0,
        10000.0,
    )

    # Read dimensions from the image header before any full decode.  The reverse
    # upload path must obey the same megapixel safety limit as pHash preflight;
    # otherwise a moderately compressed 100+ MP image can transiently allocate
    # hundreds of megabytes while being resized.  Returning the original keeps
    # the pre-v419 service behavior without risking a local decode spike.
    header_size = None
    try:
        with Image.open(source) as probe:
            header_size = tuple(int(v) for v in probe.size)
        width, height = header_size
        megapixels = max(0, width) * max(0, height) / 1_000_000.0
        if megapixels > max_megapixels:
            _emit(
                log,
                f"REVERSE UPLOAD FALLBACK BIG IMAGE: {source} "
                f"dimensions={width}x{height} megapixels={megapixels:.2f} "
                f"limit={max_megapixels:.0f}",
            )
            return source
    except Exception:
        # Malformed/non-image media follows the existing fallback path below.
        header_size = None

    # A small, already-bounded JPEG is cheaper to upload directly than to decode.
    try:
        if (
            header_size
            and source.suffix.lower() in {".jpg", ".jpeg"}
            and source.stat().st_size <= reuse_max_kb * 1024
            and max(header_size) <= max_edge
        ):
            return source
    except Exception:
        pass

    source_md5 = _safe_source_md5(source, settings=settings)
    if not source_md5:
        return source
    target = _cache_dir() / f"{source_md5}_{max_edge}_q{quality}.jpg"
    # Stage-1 reverse branches may request the same upload concurrently. Use a
    # fixed striped lock table so exactly one branch decodes/encodes the source
    # while siblings reuse the completed JPEG without an unbounded lock cache.
    with _upload_lock(source_md5):
        try:
            if target.exists() and target.stat().st_size > 0:
                try:
                    os.utime(target, None)
                except OSError:
                    pass
                return target
        except OSError:
            pass

        tmp = target.with_suffix(f".tmp-{os.getpid()}-{threading.get_ident()}-{int(time.time() * 1000)}.jpg")
        working = None
        rgb = None
        try:
            with Image.open(source) as image:
                # Reverse services operate on a still image.  For animated files
                # use frame zero; video callers already pass a cached JPEG frame.
                try:
                    if bool(getattr(image, "is_animated", False)):
                        image.seek(0)
                except Exception:
                    pass
                try:
                    working = ImageOps.exif_transpose(image)
                except Exception:
                    working = image.copy()
                if working is image:
                    working = image.copy()
                try:
                    working.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                except Exception:
                    working.thumbnail((max_edge, max_edge))
                if working.mode in {"RGBA", "LA"} or (working.mode == "P" and "transparency" in getattr(working, "info", {})):
                    background = Image.new("RGB", working.size, "white")
                    alpha = working.convert("RGBA")
                    try:
                        background.paste(alpha, mask=alpha.getchannel("A"))
                    finally:
                        alpha.close()
                    rgb = background
                else:
                    rgb = working.convert("RGB")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                rgb.save(tmp, format="JPEG", quality=quality, optimize=True, progressive=True)
            os.replace(tmp, target)
            _cleanup_cache(settings)
            try:
                original_kb = source.stat().st_size / 1024.0
                upload_kb = target.stat().st_size / 1024.0
                _emit(log, f"REVERSE UPLOAD PREPARED: {source.name} {original_kb:.0f}KB -> {upload_kb:.0f}KB max={max_edge}px q={quality}")
            except Exception:
                pass
            return target
        except Exception as exc:
            _emit(log, f"REVERSE UPLOAD FALLBACK: {source.name}: {type(exc).__name__}: {exc}")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return source
        finally:
            for obj in (rgb, working):
                try:
                    if obj is not None:
                        obj.close()
                except Exception:
                    pass


__all__ = ["reverse_upload_image"]
