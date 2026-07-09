from __future__ import annotations

import gc
import hashlib
from pathlib import Path

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

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}


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


def file_phash(path) -> str:
    """Memory-safe perceptual hash for parser/preflight."""
    if imagehash is None:
        return ""
    try:
        with Image.open(path) as img:
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            try:
                img.thumbnail((512, 512), Image.Resampling.LANCZOS)
            except Exception:
                img.thumbnail((512, 512))
            small = img.convert("RGB")
            try:
                return str(imagehash.phash(small))
            finally:
                try:
                    small.close()
                except Exception:
                    pass
    except Exception:
        return ""


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
                img = ImageOps.exif_transpose(img)
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


def video_frame_image(path):
    """Extract a searchable jpg frame from videos/gifs. Returns temp jpg path or original path."""
    path = Path(path)
    suffix = path.suffix.lower()

    def make_temp_frame_path(src_path):
        tmp_dir = Path(CACHE_DIR) / "preview_cache" / "local_booru_frames"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        safe_name = hashlib.md5(str(src_path).encode("utf-8")).hexdigest()
        return tmp_dir / f"{safe_name}.jpg"

    if suffix == ".gif":
        try:
            with Image.open(path) as img:
                try:
                    frames = getattr(img, "n_frames", 1)
                    img.seek(max(0, frames // 2))
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

    if suffix not in VIDEO_EXTS:
        return path

    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        try:
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if frames > 5:
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frames // 2))
            ok, frame = cap.read()
        finally:
            try:
                cap.release()
            except Exception:
                pass
        if not ok or frame is None:
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
