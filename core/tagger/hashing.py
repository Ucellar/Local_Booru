from __future__ import annotations

import gc
import hashlib
import threading
from pathlib import Path
from typing import Callable

try:
    import imagehash
except Exception:  # pragma: no cover - optional dependency for non-phash tests
    imagehash = None
from PIL import Image, ImageOps

try:
    from PIL import ImageCms
except Exception:  # pragma: no cover - optional Pillow component
    ImageCms = None

from core.paths import CACHE_DIR
from core.tagger.site_frames import START   # the search picture of a video: its start (v5.44.36)

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
_DEFAULT_MAX_FILE_MB = 64.0
_DEFAULT_MAX_MEGAPIXELS = 64.0
_DEFAULT_MAX_GIF_FRAMES = 300


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


def _emit(log: Callable[[str], None] | None, message: str) -> None:
    if not log:
        return
    try:
        log(str(message))
    except Exception:
        pass


def _size_bytes(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except Exception:
        return 0


def _skip_big_file(path: Path, settings=None, log=None) -> bool:
    max_mb = _setting_float(settings, "local_preflight_max_file_mb", _DEFAULT_MAX_FILE_MB, 1.0, 102400.0)
    size = _size_bytes(path)
    if size > int(max_mb * 1024 * 1024):
        _emit(log, f"PHASH SKIP BIG FILE: {path} size={size / (1024 * 1024):.2f}MB limit={max_mb:.0f}MB")
        return True
    return False


def _skip_big_dimensions(path: Path, width: int, height: int, settings=None, log=None) -> bool:
    max_mp = _setting_float(settings, "local_preflight_max_megapixels", _DEFAULT_MAX_MEGAPIXELS, 1.0, 10000.0)
    megapixels = max(0, int(width)) * max(0, int(height)) / 1_000_000.0
    if megapixels > max_mp:
        _emit(
            log,
            f"PHASH SKIP BIG IMAGE: {path} dimensions={int(width)}x{int(height)} "
            f"megapixels={megapixels:.2f} limit={max_mp:.0f}",
        )
        return True
    return False


def phash_input_allowed(path, settings=None, log=None) -> bool:
    """Cheap preflight gate using file stat and image header only.

    Videos are not rejected by total file size: only one frame is decoded, so
    a long/high-bitrate file is not intrinsically more expensive than a short
    one. Their frame dimensions are checked after VideoCapture opens in
    :func:`video_frame_image`.
    """
    path = Path(path)
    if path.suffix.lower() in VIDEO_EXTS:
        return True
    if _skip_big_file(path, settings, log):
        return False
    try:
        with Image.open(path) as img:
            width, height = img.size
        return not _skip_big_dimensions(path, width, height, settings, log)
    except Exception:
        # Let the normal decoder report/fallback behavior handle malformed media.
        return True


def is_md5(text) -> bool:
    text = str(text or "")
    if len(text) != 32:
        return False
    try:
        int(text, 16)
        return True
    except ValueError:
        return False


def file_md5(path) -> str:
    """Return byte MD5 without allocating a fresh large chunk per read."""
    path = Path(path)
    last_memory_error = None
    for chunk_size in (256 * 1024, 64 * 1024, 16 * 1024, 4 * 1024):
        try:
            h = hashlib.md5()
            with path.open("rb", buffering=0) as f:
                buf = bytearray(chunk_size)
                view = memoryview(buf)
                while True:
                    read_n = f.readinto(buf)
                    if not read_n:
                        break
                    h.update(view[:read_n])
            return h.hexdigest()
        except MemoryError as exc:
            last_memory_error = exc
            try:
                gc.collect()
            except Exception:
                pass
            continue
    raise MemoryError(f"not enough memory to hash file with 4 KiB buffer: {path}") from last_memory_error


def file_phash(path, settings=None, log=None) -> str:
    """Memory-safe perceptual hash with file-size and megapixel guards."""
    if imagehash is None:
        return ""
    path = Path(path)
    if _skip_big_file(path, settings, log):
        return ""
    try:
        with Image.open(path) as img:
            width, height = img.size
            if _skip_big_dimensions(path, width, height, settings, log):
                return ""
            working = img
            try:
                try:
                    working = ImageOps.exif_transpose(img)
                except Exception:
                    working = img
                try:
                    working.thumbnail((512, 512), Image.Resampling.LANCZOS)
                except Exception:
                    working.thumbnail((512, 512))
                small = working.convert("RGB")
                try:
                    return str(imagehash.phash(small))
                finally:
                    try:
                        small.close()
                    except Exception:
                        pass
            finally:
                if working is not img:
                    try:
                        working.close()
                    except Exception:
                        pass
    except Exception:
        return ""


WINDOW_STEPS = (0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0)


def window_phashes(path, aspect: float, settings=None, log=None) -> list:
    """(v5.44.15) [(pHash, where)] of the parts of a still picture shaped as a site's preview (``aspect`` =
    width / height): windows along the picture's long side.  e621 makes the preview of a picture wider than 2:1 or
    taller than 1:2 of a part of it (31.10.2026: 1233×441 → 512×256, 1169×2964 → 256×512) — its pHash is then far
    from the whole picture's (d=18…28 for the very picture).  [] — the picture has the preview's shape (within 6 %),
    is narrower than it, is a video or a GIF, or cannot be read."""
    if imagehash is None or not aspect or aspect <= 0:
        return []
    path = Path(path)
    if path.suffix.lower() in VIDEO_EXTS or path.suffix.lower() == ".gif" or _skip_big_file(path, settings, log):
        return []
    try:
        with Image.open(path) as img:
            if _skip_big_dimensions(path, img.size[0], img.size[1], settings, log):
                return []
            img.seek(0)
            try:
                working = ImageOps.exif_transpose(img)
            except Exception:
                working = img
            working.thumbnail((1024, 1024))
            picture = working.convert("RGB")
        w, h = picture.size
        if not w or not h:
            return []
        own = w / h
        if own > aspect * 1.06:                       # wider than the preview: windows across
            part = max(1, round(h * aspect))
            boxes = [(round((w - part) * f), 0, round((w - part) * f) + part, h) for f in WINDOW_STEPS]
        elif own < aspect / 1.06:                     # taller: windows down
            part = max(1, round(w / aspect))
            boxes = [(0, round((h - part) * f), w, round((h - part) * f) + part) for f in WINDOW_STEPS]
        else:
            return []
        out = []
        for box in boxes:
            value = str(imagehash.phash(picture.crop(box))).strip().lower()
            if value and value not in [v for v, _w in out]:
                out.append((value, "часть картинки"))
        return out
    except Exception:
        return []


def danbooru_pixel_hash(path) -> str:
    """Return Danbooru/ATF-style pixel_hash for static images."""
    path = Path(path)
    try:
        ext = path.suffix.lower()
        if ext in VIDEO_EXTS:
            return file_md5(path).lower()
        with Image.open(path) as img:
            try:
                if bool(getattr(img, "is_animated", False)) and int(getattr(img, "n_frames", 1) or 1) > 1:
                    return file_md5(path).lower()
            except Exception:
                pass
            try:
                converted = ImageOps.exif_transpose(img)
                if converted is not img:
                    img = converted
            except Exception:
                pass
            try:
                icc = img.info.get("icc_profile") if hasattr(img, "info") else None
                if icc and ImageCms is not None:
                    src_profile = ImageCms.ImageCmsProfile(__import__('io').BytesIO(icc))
                    dst_profile = ImageCms.createProfile("sRGB")
                    img = ImageCms.profileToProfile(img, src_profile, dst_profile, outputMode="RGBA")
                else:
                    img = img.convert("RGBA")
            except Exception:
                img = img.convert("RGBA")
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            width, height = img.size
            header = (
                "P7\n"
                f"WIDTH {int(width)}\n"
                f"HEIGHT {int(height)}\n"
                "DEPTH 4\n"
                "MAXVAL 255\n"
                "TUPLTYPE RGB_ALPHA\n"
                "ENDHDR\n"
            )
            h = hashlib.md5()
            h.update(header.encode("ascii"))
            h.update(img.tobytes())
            return h.hexdigest().lower()
    except Exception:
        return ""


def phash_distance(a, b) -> int:
    if imagehash is None:
        return 999
    try:
        return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)
    except Exception:
        return 999


def video_frame_image(path, settings=None, log=None):
    """Extract a searchable jpg frame from videos/GIFs with decode limits."""
    path = Path(path)
    suffix = path.suffix.lower()

    def make_temp_frame_path(src_path):
        tmp_dir = Path(CACHE_DIR) / "preview_cache" / "local_booru_frames"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        safe_name = hashlib.md5(str(src_path).encode("utf-8")).hexdigest()
        return tmp_dir / f"{safe_name}.jpg"

    if suffix not in VIDEO_EXTS and suffix != ".gif":
        return path
    # Animated images still require decoding the source container and keep the
    # ordinary file-size guard. Videos decode one frame through VideoCapture,
    # so total container size is not a useful RAM predictor.
    if suffix == ".gif" and _skip_big_file(path, settings, log):
        return path

    if suffix == ".gif":
        try:
            with Image.open(path) as img:
                width, height = img.size
                if _skip_big_dimensions(path, width, height, settings, log):
                    return path
                try:
                    frames = int(getattr(img, "n_frames", 1) or 1)
                except Exception:
                    frames = 1
                max_frames = _setting_int(settings, "local_preflight_max_gif_frames", _DEFAULT_MAX_GIF_FRAMES, 1, 1000000)
                target = 0 if frames > max_frames else max(0, frames // 2)
                if frames > max_frames:
                    _emit(log, f"PHASH GIF FIRST FRAME: {path} frames={frames} limit={max_frames}")
                try:
                    img.seek(target)
                except Exception:
                    try:
                        img.seek(0)
                    except Exception:
                        pass
                tmp = make_temp_frame_path(path)
                frame = img.convert("RGB")
                try:
                    frame.save(tmp, "JPEG", quality=95)
                finally:
                    try:
                        frame.close()
                    except Exception:
                        pass
                return tmp
        except Exception:
            return path

    try:
        import cv2
        from core.image_safe import video_frame
        cap = cv2.VideoCapture(str(path))
        try:
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            if width and height and _skip_big_dimensions(path, width, height, settings, log):
                return path
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        finally:
            try:
                cap.release()
            except Exception:
                pass
        # (v5.44.36) through video_frame: a video OpenCV cannot decode (AV1) is read with PyAV
        frame = video_frame(path, max(0, frames // 2) if frames > 5 else 0)
        if frame is None:
            return path
        tmp = make_temp_frame_path(path)
        try:
            cv2.imwrite(str(tmp), frame)
        finally:
            try:
                del frame
            except Exception:
                pass
        return tmp
    except Exception:
        return path


# ── frames of a video or a GIF to compare with a site's preview (core/parser/verify.py) ─────────────────────────────
FLAT_STDDEV = 5.0            # brightness that hardly varies (a black or one-colour frame): such a picture proves no likeness
FRAME_SIDE = 512             # the frames are made this small (the pHash of a full frame is the same, and memory stays low)


def flat(img) -> bool:
    """A picture whose brightness hardly varies — a black frame, a one-colour preview: it looks like every other such
    picture, so it proves no likeness."""
    from PIL import ImageStat
    try:
        return ImageStat.Stat(img.convert("L")).stddev[0] < FLAT_STDDEV
    except Exception:  # an odd picture: compared as it is
        return False


def _small_rgb(img):
    picture = img.convert("RGB")
    picture.thumbnail((FRAME_SIDE, FRAME_SIDE))
    return picture


_FRAMES_KEPT: dict = {}      # (v5.44.36) (path, size, mtime_ns) -> the frames of a video: its pictures for the services
_FRAMES_KEEP = 8             # are made of them one style after another (engine.reverse_prepare), the check reads them too
_FRAMES_LOCK = threading.Lock()


def video_frames(path, settings=None, log=None) -> list:
    """Frames of a video or a GIF to compare with a site's preview: [(where, RGB picture of at most 512 px)].

    A GIF: its start comes first — when it is black or one colour, the first frame with a picture — then a quarter and
    the middle.  A video: the start (a black one — a second or two in), a quarter, the middle, and (v5.44.36) the
    frames e621 and Danbooru make its preview of (core/tagger/site_frames.py; most videos' previews there are none of
    the others).  One-colour frames are left out.  [] — not a video or a GIF, too big for the limits, or nothing
    readable."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".gif":
        return _gif_frames(path, settings, log)
    if suffix not in VIDEO_EXTS:
        return []
    try:
        st = path.stat()
        key = (str(path), int(st.st_size), int(st.st_mtime_ns))
    except OSError:
        key = None
    with _FRAMES_LOCK:
        known = _FRAMES_KEPT.get(key) if key else None
    if known is not None:
        return list(known)
    found = _video_frames(path, settings, log)
    if key:
        with _FRAMES_LOCK:
            _FRAMES_KEPT[key] = tuple(found)
            while len(_FRAMES_KEPT) > _FRAMES_KEEP:
                _FRAMES_KEPT.pop(next(iter(_FRAMES_KEPT)), None)
    return found


def _video_frames(path: Path, settings=None, log=None) -> list:
    try:
        import cv2
        from core.image_safe import video_frame
    except ImportError:
        return []
    cap = cv2.VideoCapture(str(path))
    try:
        opened = cap.isOpened()
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0) if opened else 0
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0) if opened else 0
        count = max(0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)) if opened else 0
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) if opened else 0.0
    finally:
        cap.release()
    if not opened:          # (v5.44.36) OpenCV cannot open it: what PyAV reads of its start
        return _site_frames(path, [], settings, log)
    if width and height and _skip_big_dimensions(path, width, height, settings, log):
        return []
    second = int(round(fps)) if 1.0 <= fps <= 240.0 else 25
    out: list = []
    taken: set = set()

    def take(where: str, number: int) -> bool:
        if number in taken or (count and number >= count):
            return False
        taken.add(number)
        frame = video_frame(path, number)
        if frame is None:
            return False
        picture = _small_rgb(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        if flat(picture):
            return False
        out.append((where, picture))
        return True

    started = take("начало", 0)
    if not take("1 с", second) and not started:
        take("2 с", 2 * second)                       # a black start of a second or more
    if count:
        take("четверть", count // 4)
        take("середина", count // 2)
    return _site_frames(path, out, settings, log)


def _site_frames(path: Path, out: list, settings=None, log=None) -> list:
    """(v5.44.36) ``out`` and the frames e621 and Danbooru make the video's preview of (core/tagger/site_frames.py:
    ffmpeg's «thumbnail» over the first 100 / 300 frames) — most videos' previews on those sites are neither the start
    nor a second in."""
    from core.tagger.site_frames import WHERE, site_frames
    for style, picture in site_frames(path, settings, log).items():
        small = _small_rgb(picture)
        if not flat(small):
            out.append((WHERE[style], small))
    return out


def _gif_frames(path: Path, settings=None, log=None) -> list:
    if _skip_big_file(path, settings, log):
        return []
    out: list = []
    try:
        with Image.open(path) as img:
            if _skip_big_dimensions(path, img.size[0], img.size[1], settings, log):
                return []
            frames = max(1, int(getattr(img, "n_frames", 1) or 1))
            limit = _setting_int(settings, "local_preflight_max_gif_frames", _DEFAULT_MAX_GIF_FRAMES, 1, 1000000)
            spots = [("начало", 0)] + ([("начало", n) for n in range(1, min(frames, 6))] if frames <= limit else [])
            if frames <= limit:
                spots += [("четверть", frames // 4), ("середина", frames // 2)]
            started = False
            taken: set = set()
            for where, number in spots:
                if number in taken or (where == "начало" and started):
                    continue
                taken.add(number)
                try:
                    img.seek(number)
                except EOFError:
                    continue
                picture = _small_rgb(img)
                if flat(picture):
                    continue
                started = started or where == "начало"
                out.append((where, picture))
    except Exception as exc:  # an odd GIF: compared by the single frame of video_frame_image
        _emit(log, f"PHASH GIF FRAMES: {path}: {type(exc).__name__}: {exc}")
        return []
    return out


def search_frame_image(path, settings=None, log=None, style: str = START):
    """The picture of a video or a GIF to send to reverse search, saved as a temporary JPEG.  ``style`` — whose
    previews the service compares it with (v5.44.36, core/tagger/site_frames.py): :data:`START` — the start (the first
    frame with a picture of :func:`video_frames`: pixiv, Twitter, Gelbooru…), ``"e621"`` / ``"danbooru"`` — the
    frame that site makes a video's preview of (a GIF: always its start).  When none can be read — the middle frame
    of :func:`video_frame_image`; any other file as it is."""
    path = Path(path)
    if path.suffix.lower() not in VIDEO_EXTS and path.suffix.lower() != ".gif":
        return path
    frames = video_frames(path, settings, log)      # (too big for the limits — none: the middle frame below)
    if not frames:
        return video_frame_image(path, settings, log)
    from core.tagger.site_frames import WHERE
    wanted = WHERE.get(style, "")
    picture = next((p for where, p in frames if wanted and where == wanted), None)
    if picture is None:                             # the start (a one-colour frame of the site's — the start too)
        style, picture = START, frames[0][1]
    tmp_dir = Path(CACHE_DIR) / "preview_cache" / "local_booru_frames"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{hashlib.md5(str(path).encode('utf-8')).hexdigest()}-{style}.jpg"
    picture.save(tmp, "JPEG", quality=95)
    return tmp
