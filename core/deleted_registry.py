
from __future__ import annotations

import json
import time
from pathlib import Path
from core.paths import SETTINGS_DIR
from core.media_utils import file_md5

DELETED_FILES_FILE = SETTINGS_DIR / "deleted_files_ignore.json"
_CACHE = None
_CACHE_MTIME = None


def _load() -> dict:
    global _CACHE, _CACHE_MTIME
    try:
        mtime = DELETED_FILES_FILE.stat().st_mtime_ns if DELETED_FILES_FILE.exists() else 0
        if _CACHE is not None and _CACHE_MTIME == mtime:
            return _CACHE
        if DELETED_FILES_FILE.exists():
            data = json.loads(DELETED_FILES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("items", [])
                _CACHE, _CACHE_MTIME = data, mtime
                return data
    except Exception:
        pass
    _CACHE, _CACHE_MTIME = {"version": 1, "items": []}, 0
    return _CACHE


def _save(data: dict) -> None:
    global _CACHE, _CACHE_MTIME
    try:
        DELETED_FILES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DELETED_FILES_FILE.with_suffix(DELETED_FILES_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(DELETED_FILES_FILE)
        _CACHE = data
        _CACHE_MTIME = DELETED_FILES_FILE.stat().st_mtime_ns
    except Exception:
        pass


def file_md5_quick(path: Path) -> str:
    return file_md5(path)


def record_deleted_file(path: Path, *, reason: str = "duplicate_delete", md5: str = "", size: int | None = None, pixels=None) -> None:
    try:
        path = Path(path)
        if not md5 and path.exists():
            try:
                md5 = file_md5_quick(path)
            except Exception:
                md5 = ""
        if size is None:
            try:
                size = path.stat().st_size if path.exists() else 0
            except Exception:
                size = 0
        data = _load()
        item = {
            "name": path.name,
            "stem": path.stem,
            "suffix": path.suffix.lower(),
            "md5": str(md5 or "").lower(),
            "size": int(size or 0),
            "pixels": list(pixels or []),
            "path": str(path),
            "reason": reason,
            "deleted_at": int(time.time()),
        }
        items = data.setdefault("items", [])
        key = (item["name"].lower(), item["md5"], item["size"])
        items[:] = [x for x in items if (str(x.get("name","")).lower(), str(x.get("md5","")).lower(), int(x.get("size") or 0)) != key]
        items.append(item)
        if len(items) > 10000:
            del items[:-10000]
        _save(data)
    except Exception:
        pass


def should_skip_deleted_file(path: Path, *, md5: str = "", size: int | None = None) -> bool:
    try:
        path = Path(path)
        if size is None:
            try:
                size = path.stat().st_size
            except Exception:
                size = 0
        name = path.name.lower()
        data = _load()
        possible = [x for x in data.get("items", []) if str(x.get("name", "")).lower() == name]
        if not possible:
            return False
        md5 = str(md5 or "").lower()
        for item in possible:
            if int(item.get("size") or 0) != int(size or 0):
                continue
            item_md5 = str(item.get("md5") or "").lower()
            if item_md5:
                if md5 and md5 == item_md5:
                    return True
                continue
            return True
    except Exception:
        pass
    return False


def has_deleted_record_for_name(path: Path) -> bool:
    try:
        name = Path(path).name.lower()
        data = _load()
        return any(str(x.get("name", "")).lower() == name for x in data.get("items", []))
    except Exception:
        return False


def mark_deleted(path: Path, *, reason: str = "duplicate_delete", md5: str = "", size: int | None = None, pixels=None) -> None:
    record_deleted_file(path, reason=reason, md5=md5, size=size, pixels=pixels)
