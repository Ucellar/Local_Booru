
from pathlib import Path
import hashlib
import warnings

SAFE_MAX_IMAGE_PIXELS = 300_000_000

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
    key = hashlib.md5(f"{rp}|{stamp}|{width}x{height}".encode("utf-8", "ignore")).hexdigest()
    return _cache_root() / f"{key}.jpg"

def safe_thumbnail_path(path, width=256, height=256, quality=82):
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
