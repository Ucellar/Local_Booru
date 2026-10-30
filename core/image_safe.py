
from pathlib import Path
import contextlib
import hashlib
import logging
import os
import threading
import time
import warnings
import subprocess
import shutil

_LOG = logging.getLogger("local_booru.thumbs")

SAFE_MAX_IMAGE_PIXELS = 300_000_000
THUMB_CACHE_VERSION = "v2_q94_hires"

def configure_pillow():
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = SAFE_MAX_IMAGE_PIXELS
        try:
            warnings.simplefilter("default", Image.DecompressionBombWarning)
        except Exception:
            pass
    except Exception:
        pass

configure_pillow()

def _cache_root():
    try:
        from core.paths import CACHE_DIR
        root = Path(CACHE_DIR) / "thumbs"
    except Exception:
        root = Path.cwd() / "Local_Booru_Archive" / "settings" / "cache" / "thumbs"
    root.mkdir(parents=True, exist_ok=True)
    return root

def _thumb_source_marker(out: Path) -> Path:
    return out.with_suffix(out.suffix + ".src")

def _remember_thumb_source(out: Path, source: Path) -> None:
    """Persist reverse lookup for targeted cache deletion on permanent media purge."""
    try:
        marker = _thumb_source_marker(out)
        if marker.exists():
            return  # the cache name already encodes the source path: an existing marker is right
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_suffix(marker.suffix + ".tmp")
        tmp.write_text(str(source.resolve()), encoding="utf-8")
        tmp.replace(marker)
    except Exception:
        pass

def _cache_name(path, width, height):
    p = Path(path)
    try:
        st = p.stat()
        stamp = f"{st.st_mtime_ns}_{st.st_size}"
        rp = p.resolve()
    except Exception:
        stamp = "0_0"
        rp = p
    key = hashlib.md5(f"{THUMB_CACHE_VERSION}|{rp}|{stamp}|{width}x{height}".encode("utf-8", "ignore")).hexdigest()
    return _cache_root() / f"{key}.jpg"

def _temp_name(out: Path) -> Path:
    return out.with_name(f"{out.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")


def swap_in(tmp: Path, out: Path) -> bool:
    """Put a finished thumbnail in place.  On Windows the swap fails while another thread sends the
    same thumbnail (made by it a moment earlier): then that one is used and ours is dropped."""
    for attempt in range(3):
        try:
            tmp.replace(out)
            return True
        except OSError:
            if out.is_file() and out.stat().st_size > 0:
                break
            time.sleep(0.05 * (attempt + 1))
    with contextlib.suppress(OSError):
        tmp.unlink()
    return out.is_file() and out.stat().st_size > 0


READ_ON = 900       # frames read one after another to reach a place OpenCV cannot seek to


def video_frame_count(path) -> int:
    """How many frames OpenCV counts in a video (0 — it cannot tell, or cannot open it)."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        return max(0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)) if cap.isOpened() else 0
    finally:
        cap.release()


def video_frame(path, number: int):
    """The ``number``-th frame of a video (BGR, as OpenCV gives it), or None — the video unreadable, or the place out
    of reach.  OpenCV cannot seek in some files (an MPEG-TS whose clock starts past zero: every read after a seek
    fails — by its number or by its time alike): then the place is reached by reading on from the start, up to
    :data:`READ_ON` frames.  ImportError — no OpenCV."""
    import cv2
    number = max(0, int(number))
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        if number:
            cap.set(cv2.CAP_PROP_POS_FRAMES, number)
        ok, frame = cap.read()
        if ok and frame is not None:
            return frame
    finally:
        cap.release()
    if not number or number > READ_ON:
        return None
    cap = cv2.VideoCapture(str(path))
    try:
        for _ in range(number):
            if not cap.grab():
                return None
        ok, frame = cap.read()
        return frame if ok and frame is not None else None
    finally:
        cap.release()


def _video_thumbnail_path(p: Path, out: Path, width: int, height: int, quality: int = 94) -> str:
    """Create a JPEG thumbnail for a video.

    Prefer OpenCV because it is already in the project stack; fall back to ffmpeg
    if the user has it installed. Returns empty string on failure.  The picture is written to a
    temporary file first, so nobody (another thread, the browser cache) ever gets half of it.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_name(out)
    try:
        import cv2
        frames = video_frame_count(p)
        place = min(frames - 1, max(1, frames // 20)) if frames > 8 else 0
        frame = video_frame(p, place)
        if frame is None and place:
            frame = video_frame(p, 0)          # a place out of its reach: its first frame
        if frame is not None:
            from PIL import Image
            im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            im.thumbnail((width, height), Image.Resampling.LANCZOS)
            im.save(tmp, "JPEG", quality=quality, optimize=True)
            return str(out) if swap_in(tmp, out) else ""
    except Exception as exc:  # OpenCV missing or the file unreadable for it: try ffmpeg
        _LOG.debug("video thumbnail by OpenCV failed for %s: %s", p, exc)
        with contextlib.suppress(OSError):
            tmp.unlink()

    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return ""
        cmd = [
            ffmpeg, "-y", "-ss", "00:00:01", "-i", str(p),
            "-frames:v", "1", "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            "-f", "image2", str(tmp),
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        if tmp.exists() and tmp.stat().st_size > 0:
            return str(out) if swap_in(tmp, out) else ""
    except (OSError, subprocess.SubprocessError) as exc:
        _LOG.debug("video thumbnail by ffmpeg failed for %s: %s", p, exc)
    with contextlib.suppress(OSError):
        tmp.unlink()
    return ""


def card_thumbnail_size(settings) -> tuple[int, int]:
    """Size of the thumbnail made for every card at import; galleries reuse it instead of making another."""
    settings = settings or {}
    w = int(settings.get("thumb_cache_card_w", settings.get("thumb_cache_w", 256)) or 256)
    h = int(settings.get("thumb_cache_card_h", settings.get("thumb_cache_h", 256)) or 256)
    return max(256, w * 2), max(256, h * 2)


_TOUCH_EVERY = 24 * 3600   # seconds; "last used" for cache trimming needs no finer clock


def cached_thumbnail_path(path, width=256, height=256):
    """The thumbnail if it is already in the cache, else "" (never makes one)."""
    p = Path(path)
    width = max(32, int(width or 256))
    height = max(32, int(height or 256))
    out = _cache_name(p, width, height)
    try:
        st = out.stat()
    except OSError:
        return ""
    if st.st_size <= 0:
        return ""
    if time.time() - st.st_mtime > _TOUCH_EVERY:
        try:
            out.touch()  # last-used marker for shutdown cache trimming
        except OSError:
            pass
    _remember_thumb_source(out, p)
    return str(out)


def safe_thumbnail_path(path, width=256, height=256, quality=94):
    p = Path(path)
    if not p.exists():
        return ""
    width = max(32, int(width or 256))
    height = max(32, int(height or 256))
    cached = cached_thumbnail_path(p, width, height)
    if cached:
        return cached
    out = _cache_name(p, width, height)

    if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov", ".avi"}:
        made = _video_thumbnail_path(p, out, width, height, quality)
        if made:
            _remember_thumb_source(out, p)
        return made

    # Two threads may make the same thumbnail at once: each writes its own temporary file and
    # swaps it in, so nobody ever reads a half-written JPEG.
    tmp = _temp_name(out)
    try:
        from PIL import Image, ImageOps
        configure_pillow()
        with Image.open(p) as im:
            # JPEG can decode straight at a fraction of its size: much faster for big photos.
            side = max(width, height) * 2
            im.draft("RGB", (side, side))
            im = ImageOps.exif_transpose(im)
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((width, height), Image.Resampling.LANCZOS)
            out.parent.mkdir(parents=True, exist_ok=True)
            im.save(tmp, "JPEG", quality=quality, optimize=True)
        if not swap_in(tmp, out):
            return ""
        _remember_thumb_source(out, p)
        return str(out)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return ""

def image_dimensions_safe(path):
    try:
        from PIL import Image
        configure_pillow()
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)
