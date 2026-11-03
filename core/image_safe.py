
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
VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
# (v5.44.37) the preview of a single video (a card: the gallery, «Длинные видео», the post page) is the frame Danbooru
# makes its previews of — the one whose colours are closest to the average of the first 300 frames
# (core/tagger/site_frames.py; the owner: «превью видео (одиночных а не сериалов) надо сделать таким … «средним»»),
# not a frame at a twentieth of the video.  Its own name in the cache: the old pictures are not taken for it.  The
# frames of the «Видео» tab's series stay as they were (``video_library.episode_frame``: flavour "").
AVERAGE = "average300"

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

def _cache_name(path, width, height, flavor: str = ""):
    p = Path(path)
    try:
        st = p.stat()
        stamp = f"{st.st_mtime_ns}_{st.st_size}"
        rp = p.resolve()
    except Exception:
        stamp = "0_0"
        rp = p
    extra = f"|{flavor}" if flavor else ""
    key = hashlib.md5(f"{THUMB_CACHE_VERSION}|{rp}|{stamp}|{width}x{height}{extra}".encode("utf-8", "ignore")).hexdigest()
    return _cache_root() / f"{key}.jpg"


def card_flavor(path) -> str:
    """The cache flavour of a card's preview: :data:`AVERAGE` for a video, "" for a picture."""
    return AVERAGE if Path(path).suffix.lower() in VIDEO_SUFFIXES else ""

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
    :data:`READ_ON` frames.  (v5.44.36) A video OpenCV cannot decode at all — AV1 (its FFmpeg has no AV1 decoder of
    its own: «Your platform doesn't support hardware accelerated AV1 decoding»; a video of the owner's Брак had no
    frame for the parser, no thumbnail in the gallery) — is read with PyAV (:func:`pyav_frame`).  ImportError — no
    OpenCV."""
    import cv2
    number = max(0, int(number))
    frame = _opencv_frame(cv2, path, number)
    if frame is None:
        frame = pyav_frame(path, number)
    return frame


def _fourcc(cv2, cap) -> str:
    code = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
    return "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4)).strip("\0 ").lower()


def _opencv_frame(cv2, path, number: int):
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        if _fourcc(cv2, cap) == "av01":     # AV1: PyAV straight away (OpenCV fails on it, a line of errors a frame)
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


def pyav_frame(path, number: int):
    """(v5.44.36) The ``number``-th frame (BGR) read with PyAV — FFmpeg's own decoders, AV1 too; a place past the
    start by its time (``number`` / the frame rate) from the key frame before it.  None — no PyAV, no video in the
    file, or unreadable for it too."""
    try:
        import av
    except ImportError:
        return None
    number = max(0, int(number))
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                return None
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            rate = float(stream.average_rate or stream.guessed_rate or 0) or 25.0
            base = stream.time_base
            start = float(stream.start_time * base) if stream.start_time is not None and base else 0.0
            target = start + number / rate
            if number and base:
                container.seek(int(target / base), stream=stream, backward=True, any_frame=False)
            last = None
            for frame in container.decode(stream):
                last = frame
                when = frame.time
                if not number or when is None or when + 0.5 / rate >= target:
                    return _upright_bgr(frame)
            return _upright_bgr(last) if last is not None else None
    except Exception as exc:  # unreadable for PyAV too: the caller has no frame, as before
        _LOG.debug("video frame by PyAV failed for %s: %s", path, exc)
        return None


def _upright_bgr(frame):
    """A PyAV frame as BGR, turned as FFmpeg and OpenCV turn it (a phone's video «lying on its side»)."""
    import numpy as np
    from core.tagger.site_frames import turn_of
    picture = frame.to_ndarray(format="bgr24")
    turns = turn_of(frame) // 90
    return np.ascontiguousarray(np.rot90(picture, k=-turns)) if turns else picture


def _video_thumbnail_path(p: Path, out: Path, width: int, height: int, quality: int = 94, *,
                          average: bool = False) -> str:
    """Create a JPEG thumbnail for a video.  ``average`` (v5.44.37: a card's) — the frame Danbooru makes its preview
    of (:data:`AVERAGE`); else, or when it cannot be read, a frame at a twentieth of the video.

    Prefer OpenCV because it is already in the project stack; fall back to ffmpeg
    if the user has it installed. Returns empty string on failure.  The picture is written to a
    temporary file first, so nobody (another thread, the browser cache) ever gets half of it.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_name(out)
    if average:
        try:
            from PIL import Image

            from core.tagger.site_frames import DANBOORU, site_frame
            picture = site_frame(p, DANBOORU)
            if picture is not None:
                picture.thumbnail((width, height), Image.Resampling.LANCZOS)
                picture.save(tmp, "JPEG", quality=quality, optimize=True)
                return str(out) if swap_in(tmp, out) else ""
        except Exception as exc:  # unreadable that way: the frame at a twentieth below
            _LOG.debug("average video thumbnail failed for %s: %s", p, exc)
            with contextlib.suppress(OSError):
                tmp.unlink()
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


def cached_thumbnail_path(path, width=256, height=256, flavor=None):
    """The thumbnail if it is already in the cache, else "" (never makes one).  ``flavor`` — of the cache name; None —
    a card's (:func:`card_flavor`: a video's preview is its average frame)."""
    p = Path(path)
    width = max(32, int(width or 256))
    height = max(32, int(height or 256))
    out = _cache_name(p, width, height, card_flavor(p) if flavor is None else flavor)
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
    out = _cache_name(p, width, height, card_flavor(p))

    if p.suffix.lower() in VIDEO_SUFFIXES:          # (v5.44.37) a card's preview: the average frame
        made = _video_thumbnail_path(p, out, width, height, quality, average=True)
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
