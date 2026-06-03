import json
import html
import re
from core.tag_utils import normalize_tag, canonical_tag_key
from pathlib import Path
from urllib.parse import urlparse
from collections import Counter, defaultdict

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
GROUP_ORDER = ["artist", "contributor", "character", "copyright", "species", "general", "meta", "lore", "invalid", "parody", "language", "category", "pages"]
from core.paths import CACHE_FILE
from core.paths import result_output_base
NUMERIC_RE = re.compile(r"^[\d\W_]+$")

# SQLite index is the new source of truth for large galleries. Old JSON cache remains as fallback.
USE_SQLITE_INDEX = True

def _sqlite_enabled(settings):
    return bool(settings.get("use_sqlite_index", True))


def is_video(path):
    return Path(path).suffix.lower() in VIDEO_EXTS


def should_skip_tag(tag, settings):
    if settings.get("ignore_numeric_tags") and NUMERIC_RE.match(str(tag)):
        return True
    return False

def clean_tags(tags, settings):
    out = []
    seen = set()
    for t in tags or []:
        t = normalize_tag(t)
        if not t or should_skip_tag(t, settings):
            continue
        key = canonical_tag_key(t)
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out

# Legacy sidecar readers were removed from live gallery code.
# Old metadata is imported only via core.services.index_service.import_legacy_sidecar_metadata.

def scan_library(settings, force=False):
    """Return gallery counters from SQLite only.

    Legacy sidecar folders are no longer silently interpreted as the live
    library. Use the explicit maintenance import to migrate old metadata.
    """
    try:
        from core.database.indexer import index_library
        from core.database.repository import counts
        if settings.get("sqlite_auto_index_on_gallery_open", False):
            index_library(settings, force=force, import_legacy_sidecars=False)
        tc, sc, ttf = counts(settings)
        return [], tc, sc, ttf
    except Exception as e:
        print("SQLITE GALLERY READ ERROR:", e)
        return [], {}, {}, {}


def search_library_sql(settings, query="", source="all", bucket="all", limit=None, offset=0, order="path"):
    if not _sqlite_enabled(settings):
        return None
    try:
        from core.services.library_service import page
        return page(settings, query=query, source=source, bucket=bucket, limit=limit, offset=offset, order=order, enrich=False)
    except Exception as e:
        print("SQLITE SEARCH FALLBACK:", e)
        return None


def count_library_sql(settings, query="", source="all", bucket="all"):
    if not _sqlite_enabled(settings):
        return None
    try:
        from core.services.library_service import total
        return total(settings, query=query, source=source, bucket=bucket)
    except Exception as e:
        print("SQLITE COUNT FALLBACK:", e)
        return None


def parse_query(q):
    plus=[]; minus=[]
    for raw in q.replace(",", " ").split():
        raw = normalize_tag(raw)
        if raw.startswith("-") and len(raw)>1: minus.append(normalize_tag(raw[1:]))
        elif raw: plus.append(raw)
    return plus, minus

def item_matches(item, plus, minus, source="all"):
    tags=set(item.get("tags", []))
    if plus and not all(t in tags for t in plus): return False
    if minus and any(t in tags for t in minus): return False
    if source != "all" and source not in item.get("source_hosts", []): return False
    return True

def sort_tag_items(items, mode):
    if mode == "alpha": return sorted(items, key=lambda x: x[0].lower())
    if mode == "alpha_desc": return sorted(items, key=lambda x: x[0].lower(), reverse=True)
    if mode == "count_asc": return sorted(items, key=lambda x: (x[1], x[0].lower()))
    return sorted(items, key=lambda x: (-x[1], x[0].lower()))
