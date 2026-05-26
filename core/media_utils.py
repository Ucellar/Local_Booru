
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import hashlib
import re
from typing import Iterable, Dict, List, Tuple

from core.tag_utils import normalize_tag, canonical_tag_key

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
SIDECAR_EXTS = {".txt", ".json"}
COPY_SUFFIX_RE = re.compile(r"\s*\((\d+)\)$")


def is_image(path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def is_video(path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def is_media(path) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTS


def has_copy_suffix(path) -> bool:
    try:
        return bool(COPY_SUFFIX_RE.search(Path(path).stem))
    except Exception:
        return False


def base_name_without_copy_suffix(path) -> str:
    try:
        return COPY_SUFFIX_RE.sub("", Path(path).stem)
    except Exception:
        return Path(path).stem


def file_md5(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.md5()
    with Path(path).open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def safe_stat(path: Path) -> Tuple[int, int]:
    try:
        st = Path(path).stat()
        return int(st.st_size), int(st.st_mtime_ns)
    except Exception:
        return 0, 0


def image_size(path: Path) -> Tuple[int, int]:
    if is_video(path):
        return (0, 0)
    try:
        from core.image_safe import image_dimensions_safe
        return image_dimensions_safe(path)
    except Exception:
        return (0, 0)


def clean_tags(tags: Iterable[str] | None, settings: dict | None = None) -> List[str]:
    out: List[str] = []
    seen = set()
    ignore_numeric = bool((settings or {}).get("ignore_numeric_tags"))
    numeric_re = re.compile(r"^[\d\W_]+$")
    for raw in tags or []:
        t = normalize_tag(str(raw))
        if not t:
            continue
        if ignore_numeric and numeric_re.match(t):
            continue
        key = canonical_tag_key(t)
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def host_from_url(url: str) -> str:
    try:
        return urlparse(str(url)).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def bucket_for_path(path: Path) -> str:
    parts = [p.lower() for p in Path(path).parts]
    if "downloads" in parts:
        if "found" in parts:
            return "downloaded_found"
        if "partial_match" in parts:
            return "downloaded_partial_match"
        if "no_match" in parts:
            return "downloaded_no_match"
        return "downloaded"
    if "found" in parts:
        return "found"
    if "partial_match" in parts:
        return "partial_match"
    if "no_match" in parts:
        return "no_match"
    return "original"


def scan_roots(settings: dict) -> list[Path]:
    from core.paths import result_output_base
    if settings.get("gallery_source", "output") == "original":
        root = Path(settings.get("root", ""))
        return [root] if str(root) else []
    out = result_output_base(settings)
    roots = [
        out / "found" / "media",
        out / "partial_match" / "media",
        out / "no_match" / "media",
        out / "downloads" / "found" / "media",
        out / "downloads" / "partial_match" / "media",
        out / "downloads" / "no_match" / "media",
    ]
    if not any(r.exists() for r in roots):
        root = Path(settings.get("root", ""))
        roots = [root] if str(root) else []
    return roots


def iter_media_files(roots: Iterable[Path], stop_check=None):
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if stop_check and stop_check():
                return
            if path.is_file() and is_media(path):
                yield path
