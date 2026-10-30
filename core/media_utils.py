
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
# Every picture or video type a card may hold.  The gallery opens, copies and plays only these:
# a downloaded ``.hta`` / ``.exe`` must never reach the program Windows starts for it.
CARD_FILE_EXTS = MEDIA_EXTS | {".avif", ".bmp", ".jxl", ".tif", ".tiff", ".m4v", ".jfif"}
CARD_VIDEO_EXTS = VIDEO_EXTS | {".m4v"}
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
    if is_video(path) or Path(path).suffix.lower() == ".m4v":
        # the picture size from the file's own headers (core.video_probe; the length is read with it)
        from core.video_probe import probe
        info = probe(path)
        return (info.width, info.height) if info is not None else (0, 0)
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


def normalize_host(value) -> str:
    """'https://www.Example.com:443/x' -> 'example.com'.  The one host spelling stored in the library.

    Only a leading "www." is dropped (``replace("www.", "")`` also cut it out of the middle of names).
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        try:
            text = urlparse(text).netloc or text
        except ValueError:
            return ""
    text = text.split("/", 1)[0].split("@")[-1].split(":", 1)[0].strip(".")
    while text.startswith("www."):
        text = text[4:]
    return text


def host_from_url(url: str) -> str:
    """Host of a URL ("" for anything that is not a URL with a scheme)."""
    text = str(url or "")
    return normalize_host(text) if "://" in text else ""


def bucket_for_path(path: Path) -> str:
    """The library section of a file, from the folders the program put it in.

    What the grabber, the subscriptions and the extension download lies in
    ``output/subscriptions/<site>/<query>/<time>/`` — «downloaded» (it used to be «original», so the gallery's
    «Скачанные» never showed it).  Otherwise the nearest known folder decides: a library kept under a folder
    named «Downloads» is not «downloaded» as a whole, and ``downloads/found`` of the old downloader is."""
    folders = [p.lower() for p in Path(path).parts[:-1]]
    if len(folders) >= 4 and folders[-4] == "subscriptions":
        return "downloaded"
    for i in range(len(folders) - 1, -1, -1):
        name = folders[i]
        if name in ("found", "partial_match", "no_match"):
            return ("downloaded_" + name) if i > 0 and folders[i - 1] == "downloads" else name
        if name == "downloads":
            return "downloaded"
    return "original"


def scan_roots(settings: dict) -> list[Path]:
    # configured_output_root never creates folders: result_output_base() would recreate an
    # empty output/ on a renamed or unplugged archive, and the indexer would then take
    # every library file for "deleted" (docs/map, traps 10 and 22).
    from core.storage_roots import configured_output_root
    if settings.get("gallery_source", "output") == "original":
        root = Path(settings.get("root", ""))
        return [root] if str(root) else []
    out = Path(configured_output_root(settings))
    # Include the subscription download root itself.  Do NOT rglob every
    # timestamp/query folder on every gallery/index open; iter_media_files is
    # already recursive and one stable root is enough.  The old leaf-directory
    # expansion made startup/indexing O(number_of_subscription_folders) before
    # a single media file was even inspected.
    subs_root = Path(settings.get("output_dir", str(out))) / "subscriptions"
    subs_roots = [subs_root] if subs_root.exists() else []

    roots = [
        out / "found" / "media",
        out / "partial_match" / "media",
        out / "no_match" / "media",
        out / "downloads" / "found" / "media",
        out / "downloads" / "partial_match" / "media",
        out / "downloads" / "no_match" / "media",
    ] + subs_roots
    # No fallback to settings["root"]: that is the user's source archive.  Indexing it
    # as the library (when output was renamed or its drive was offline) used to replace
    # the whole library with source files (docs/map, trap 10).
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
