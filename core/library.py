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
GROUP_ORDER = ["artist", "character", "copyright", "general", "meta", "parody", "language", "category", "pages"]
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

def find_sidecar(path: Path, suffix: str, kind: str):
    candidates = [path.with_suffix(suffix)]
    if kind == "tags":
        candidates += [path.with_suffix(".tags.txt"), path.with_suffix(".txt"), Path(str(path)+".txt"), Path(str(path)+".tags.txt")]
    else:
        candidates += [path.with_suffix(".sources.txt"), Path(str(path)+".sources.txt")]

    # Output layout support:
    #   output/found/media/a.jpg -> output/found/tags/a.tags.txt
    #   output/found/media/a.jpg -> output/found/source/a.sources.txt
    try:
        if path.parent.name == "media" and path.parent.parent.name in ("found", "partial_match", "no_match"):
            bucket = path.parent.parent
            if kind == "tags":
                candidates += [bucket / "tags" / (path.stem + ".tags.json"), bucket / "tags" / (path.stem + ".tags.txt")]
            else:
                candidates += [bucket / "source" / (path.stem + ".sources.txt")]
    except Exception:
        pass

    for c in candidates:
        if c.exists():
            return c
    return candidates[0]

def read_tag_groups(path: Path, settings):
    candidates = [path.with_suffix(".tags.json"), Path(str(path)+".tags.json")]
    try:
        if path.parent.name == "media" and path.parent.parent.name in ("found", "partial_match", "no_match"):
            candidates.append(path.parent.parent / "tags" / (path.stem + ".tags.json"))
    except Exception:
        pass
    for c in candidates:
        if c.exists():
            try:
                d = json.loads(c.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    if isinstance(d.get("groups"), dict):
                        return {k: clean_tags(v, settings) for k, v in d["groups"].items() if isinstance(v, list)}
                    return {k: clean_tags(v, settings) for k, v in d.items() if isinstance(v, list)}
            except Exception:
                pass
    return None

def read_tags(path: Path, suffix: str, settings):
    f = find_sidecar(path, suffix, "tags")
    if not f.exists():
        return []
    try:
        text = f.read_text(encoding="utf-8", errors="ignore").replace("\n", ",")
    except Exception:
        return []
    return clean_tags([x.strip() for x in text.split(",") if x.strip()], settings)

def read_sources(path: Path, suffix: str):
    f = find_sidecar(path, suffix, "sources")
    if not f.exists():
        return []
    out=[]
    try:
        lines=f.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    for line in lines:
        parts=line.strip().split()
        urls=[p for p in parts if p.startswith("http://") or p.startswith("https://")]
        if urls:
            u=urls[-1]
            out.append({"host": urlparse(u).netloc.lower().replace("www.",""), "url": u})
        else:
            for p in parts:
                if "." in p and not p.startswith("md5"):
                    out.append({"host": p, "url": ""}); break
    seen=set(); uniq=[]
    for x in out:
        k=(x["host"], x["url"])
        if k not in seen:
            seen.add(k); uniq.append(x)
    return uniq

def file_state(img: Path, settings):
    paths = [img, find_sidecar(img, settings.get("tags_suffix", ".tags.txt"), "tags"), find_sidecar(img, settings.get("sources_suffix", ".sources.txt"), "sources"), img.with_suffix(".tags.json")]
    state=[]
    for p in paths:
        if p.exists():
            try:
                st=p.stat(); state.append([str(p), st.st_mtime, st.st_size])
            except Exception:
                state.append([str(p), 0, 0])
        else:
            state.append([str(p), None, None])
    return state

def load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_cache(cache):
    try:
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def library_bucket_for_path(path: Path):
    """Return gallery bucket: found / no_match / downloaded / partial_match / other."""
    parts = [x.lower() for x in path.parts]
    if "downloads" in parts:
        if "no_match" in parts:
            return "downloaded_no_match"
        return "downloaded"
    if "no_match" in parts:
        return "no_match"
    if "found" in parts:
        return "found"
    if "partial_match" in parts:
        return "partial_match"
    return "other"

def scan_library(settings, force=False):
    """Return gallery items and tag/source counters.

    New path: SQLite index.
    Old path: rglob + sidecar JSON cache fallback.

    The SQLite path still scans folder names, but it only rereads changed files by mtime/size.
    Tag filtering is handled by SQL through search_library_sql().
    """
    if _sqlite_enabled(settings):
        try:
            from core.database.indexer import index_library
            from core.database.repository import counts

            # SQL-only gallery must never materialize 100k-500k image rows on open.
            # It only loads tag/source counters here. Real image rows are fetched
            # page-by-page by search_library_sql().
            if settings.get("sqlite_auto_index_on_gallery_open", False):
                index_library(settings, force=force)

            tc, sc, ttf = counts(settings)
            return [], tc, sc, ttf
        except Exception as e:
            print("SQLITE READ FALLBACK:", e)

    # By default, gallery scans archived output, not the original input folder.
    if settings.get("gallery_source", "output") == "original":
        scan_roots = [Path(settings.get("root", ""))]
    else:
        out = result_output_base(settings)
        scan_roots = [
            out / "found" / "media",
            out / "partial_match" / "media",
            out / "no_match" / "media",
            out / "downloads" / "found" / "media",
            out / "downloads" / "partial_match" / "media",
            out / "downloads" / "no_match" / "media",
        ]
        if not any(r.exists() for r in scan_roots):
            # First run fallback: old projects may not have output yet.
            scan_roots = [Path(settings.get("root", ""))]

    items=[]; tc=Counter(); sc=Counter(); ttf=defaultdict(list)
    if not any(r.exists() for r in scan_roots):
        return [], {}, {}, {}

    cache = {} if force else load_cache()
    new_cache = {}

    for scan_root in scan_roots:
        if not scan_root.exists():
            continue
        for img in scan_root.rglob("*"):
            if img.suffix.lower() not in MEDIA_EXTS:
                continue
            if img.name.lower().endswith((".tags.txt", ".sources.txt", ".tags.json")):
                continue

            key=str(img)
            state=file_state(img, settings)
            cached=cache.get(key)
            if cached and cached.get("state") == state:
                item=cached.get("item")
            else:
                groups=read_tag_groups(img, settings)
                tags=read_tags(img, settings.get("tags_suffix", ".tags.txt"), settings)
                if groups:
                    tags=[]
                    for xs in groups.values():
                        tags += xs
                    tags=clean_tags(tags, settings)
                sources=read_sources(img, settings.get("sources_suffix", ".sources.txt"))
                hosts=[s["host"] for s in sources]
                try:
                    mtime_ns = img.stat().st_mtime_ns
                except Exception:
                    mtime_ns = 0
                item={"path":str(img), "tags":tags, "tag_groups":groups, "sources":sources, "source_hosts":hosts, "is_video": is_video(img), "mtime_ns": mtime_ns, "bucket": library_bucket_for_path(img)}

            if not item:
                continue
            if "mtime_ns" not in item:
                try:
                    item["mtime_ns"] = img.stat().st_mtime_ns
                except Exception:
                    item["mtime_ns"] = 0
            if "bucket" not in item:
                item["bucket"] = library_bucket_for_path(img)
            new_cache[key] = {"state": state, "item": item}
            items.append(item)
            for t in item.get("tags", []):
                tc[t]+=1
                ttf[t].append(str(img))
            for h in item.get("source_hosts", []):
                sc[h]+=1

    items.sort(key=lambda x:x["path"].lower())
    save_cache(new_cache)
    return items, dict(tc), dict(sc), dict(ttf)


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
