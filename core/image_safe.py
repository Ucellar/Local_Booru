
from pathlib import Path
import hashlib
import warnings
import subprocess
import shutil

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
        root = Path.cwd() / "Local_Booru_Output" / "preview_cache" / "thumbs"
    root.mkdir(parents=True, exist_ok=True)
    return root

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

def _video_thumbnail_path(p: Path, out: Path, width: int, height: int, quality: int = 94) -> str:
    """Create a JPEG thumbnail for a video.

    Prefer OpenCV because it is already in the project stack; fall back to ffmpeg
    if the user has it installed. Returns empty string on failure.
    """
    try:
        import cv2
        cap = cv2.VideoCapture(str(p))
        if not cap.isOpened():
            return ""
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frames > 8:
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(frames - 1, max(1, frames // 20)))
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return ""
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        from PIL import Image
        im = Image.fromarray(frame)
        im.thumbnail((width, height), Image.Resampling.LANCZOS)
        out.parent.mkdir(parents=True, exist_ok=True)
        im.save(out, "JPEG", quality=quality, optimize=True)
        return str(out)
    except Exception:
        pass

    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return ""
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.jpg")
        cmd = [
            ffmpeg, "-y", "-ss", "00:00:01", "-i", str(p),
            "-frames:v", "1", "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            str(tmp),
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        if tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(out)
            return str(out)
    except Exception:
        return ""
    return ""


def safe_thumbnail_path(path, width=256, height=256, quality=94):
    p = Path(path)
    if not p.exists():
        return ""
    width = max(32, int(width or 256))
    height = max(32, int(height or 256))
    out = _cache_name(p, width, height)
    try:
        if out.exists() and out.stat().st_size > 0:
            return str(out)
    except Exception:
        pass

    if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov", ".avi"}:
        return _video_thumbnail_path(p, out, width, height, quality)

    try:
        from PIL import Image, ImageOps
        configure_pillow()
        with Image.open(p) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((width, height), Image.Resampling.LANCZOS)
            out.parent.mkdir(parents=True, exist_ok=True)
            im.save(out, "JPEG", quality=quality, optimize=True)
            return str(out)
    except Exception:
        return ""

def image_dimensions_safe(path):
    try:
        from PIL import Image
        configure_pillow()
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)
