
import json
import time
from pathlib import Path
from core.paths import CACHE_DIR

NO_MATCH_DB_FILE = CACHE_DIR / "nomatch_cache.json"
_CACHE = None
_CACHE_MTIME = None


def _load_raw():
    global _CACHE, _CACHE_MTIME
    try:
        mtime = NO_MATCH_DB_FILE.stat().st_mtime_ns if NO_MATCH_DB_FILE.exists() else 0
        if _CACHE is not None and _CACHE_MTIME == mtime:
            return _CACHE
        if NO_MATCH_DB_FILE.exists():
            data = json.loads(NO_MATCH_DB_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                data = {str(x.get("path")): x for x in data if isinstance(x, dict) and x.get("path")}
            if isinstance(data, dict):
                _CACHE, _CACHE_MTIME = data, mtime
                return data
    except Exception:
        pass
    _CACHE, _CACHE_MTIME = {}, 0
    return _CACHE


def _save_raw(data):
    global _CACHE, _CACHE_MTIME
    NO_MATCH_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = NO_MATCH_DB_FILE.with_suffix(NO_MATCH_DB_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(NO_MATCH_DB_FILE)
    _CACHE = data
    _CACHE_MTIME = NO_MATCH_DB_FILE.stat().st_mtime_ns


def list_nomatches(root=None):
    data = _load_raw()
    items = list(data.values())
    if root:
        try:
            root_s = str(Path(root).resolve()).lower()
            items = [x for x in items if str(x.get("path", "")).lower().startswith(root_s)]
        except Exception:
            pass
    items.sort(key=lambda x: float(x.get("ts", 0)), reverse=True)
    return items


def upsert_nomatch(path, reason="no_match"):
    p = Path(path)
    key = str(p.resolve())
    data = _load_raw()
    data[key] = {"path": key, "name": p.name, "reason": reason, "ts": time.time(), "manual_url": data.get(key, {}).get("manual_url", "")}
    _save_raw(data)


def remove_nomatch(path):
    key = str(Path(path).resolve())
    data = _load_raw()
    if key in data:
        data.pop(key, None)
        _save_raw(data)


def set_manual_url(path, url):
    key = str(Path(path).resolve())
    data = _load_raw()
    item = data.get(key, {"path": key, "name": Path(path).name, "ts": time.time(), "reason": "manual"})
    item["manual_url"] = url
    data[key] = item
    _save_raw(data)
