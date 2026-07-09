"""Persistent re-import policy for deliberately deleted exact media.

SQLite is the live source of truth.  The historical JSON file is imported once
for compatibility, then retained only as a backup artifact; automatic dedupe
rows never become active content blocks.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from core.paths import SETTINGS_DIR
from core.media_utils import file_md5

DELETED_FILES_FILE = SETTINGS_DIR / "deleted_files_ignore.json"
_MIGRATION_MARK = SETTINGS_DIR / "deleted_files_ignore.migrated_to_sqlite"
_CACHE = None
_CACHE_MTIME = None
AUTO_TOKENS = ("auto", "duplicate", "dedupe", "exact_md5", "скле", "нормализ", "cleanup")
MANUAL_REASONS = {"delete", "gallery_context_delete", "post_context_delete", "delete_by_tag", "delete_by_source", "sqlite_delete"}


def _is_manual_reason(reason: str) -> bool:
    value = str(reason or "").strip().lower()
    if value in MANUAL_REASONS:
        return True
    return not any(token in value for token in AUTO_TOKENS)


def _load_legacy() -> dict:
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


def _save_legacy(data: dict) -> None:
    global _CACHE, _CACHE_MTIME
    DELETED_FILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DELETED_FILES_FILE.with_suffix(DELETED_FILES_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DELETED_FILES_FILE)
    _CACHE = data
    _CACHE_MTIME = DELETED_FILES_FILE.stat().st_mtime_ns


def file_md5_quick(path: Path) -> str:
    return file_md5(path)


def migrate_legacy_registry(settings: dict) -> dict:
    """Import the pre-SQLite JSON registry once; never activate auto-delete rows."""
    if _MIGRATION_MARK.exists() or not DELETED_FILES_FILE.exists():
        return {"imported": 0, "active": 0, "backup": ""}
    data = _load_legacy()
    items = [x for x in data.get("items", []) if isinstance(x, dict)]
    imported = active = 0
    try:
        batch_size = int((settings or {}).get("legacy_deleted_migration_batch_size", 1000) or 1000)
    except Exception:
        batch_size = 1000
    batch_size = max(100, min(5000, batch_size))
    try:
        from core.database.connection import db
        with db(settings, write=True) as con:
            for item in items:
                md5 = str(item.get("md5") or "").strip().lower()
                if not md5:
                    continue
                reason = str(item.get("reason") or "legacy_json_import")
                manual = int(_is_manual_reason(reason))
                con.execute(
                    """INSERT INTO deleted_media_rules(md5,active,manual_delete,reason,path,file_name,size_bytes,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(md5) DO UPDATE SET
                         active=MAX(deleted_media_rules.active,excluded.active),
                         manual_delete=MAX(deleted_media_rules.manual_delete,excluded.manual_delete),
                         reason=CASE WHEN excluded.manual_delete=1 THEN excluded.reason ELSE deleted_media_rules.reason END,
                         updated_at=excluded.updated_at""",
                    (md5, manual, manual, reason, str(item.get("path") or ""), str(item.get("name") or ""), int(item.get("size") or 0), int(item.get("deleted_at") or time.time()), int(time.time())),
                )
                imported += 1
                active += manual
                if imported % batch_size == 0:
                    con.commit()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = DELETED_FILES_FILE.with_name(f"deleted_files_ignore_{stamp}_legacy_before_sqlite.json.bak")
        shutil.copy2(DELETED_FILES_FILE, backup)
        _MIGRATION_MARK.write_text(f"imported={imported}\nactive={active}\nat={int(time.time())}\nbackup={backup}\n", encoding="utf-8")
        return {"imported": imported, "active": active, "backup": str(backup)}
    except Exception as exc:
        return {"imported": 0, "active": 0, "backup": "", "error": str(exc)}


def record_deleted_file(path: Path, *, reason: str = "duplicate_delete", md5: str = "", size: int | None = None, pixels=None, settings: dict | None = None, manual_delete: bool | None = None) -> None:
    path = Path(path)
    if not md5 and path.exists():
        try: md5 = file_md5_quick(path)
        except Exception: md5 = ""
    if size is None:
        try: size = path.stat().st_size if path.exists() else 0
        except Exception: size = 0
    manual = bool(_is_manual_reason(reason) if manual_delete is None else manual_delete)
    value = str(md5 or "").strip().lower()
    if settings is not None and value:
        from core.database.connection import db
        with db(settings, write=True) as con:
            con.execute(
                """INSERT INTO deleted_media_rules(md5,active,manual_delete,reason,path,file_name,size_bytes,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(md5) DO UPDATE SET active=excluded.active, manual_delete=excluded.manual_delete,
                   reason=excluded.reason,path=excluded.path,file_name=excluded.file_name,size_bytes=excluded.size_bytes,updated_at=excluded.updated_at""",
                (value, int(manual), int(manual), str(reason or ""), str(path), path.name, int(size or 0), int(time.time()), int(time.time())),
            )
        return
    # Compatibility for external/old calls without settings; new application paths pass settings.
    data = _load_legacy()
    item = {"name": path.name, "stem": path.stem, "suffix": path.suffix.lower(), "md5": value, "size": int(size or 0), "pixels": list(pixels or []), "path": str(path), "reason": reason, "deleted_at": int(time.time())}
    items = data.setdefault("items", [])
    key = (item["name"].lower(), item["md5"], item["size"])
    items[:] = [x for x in items if (str(x.get("name","")).lower(), str(x.get("md5","")).lower(), int(x.get("size") or 0)) != key]
    items.append(item)
    if len(items) > 10000: del items[:-10000]
    _save_legacy(data)


def has_deleted_md5(md5: str, *, settings: dict | None = None) -> bool:
    value = str(md5 or "").strip().lower()
    if not value: return False
    if settings is not None:
        from core.database.connection import db
        with db(settings, readonly=True) as con:
            row = con.execute("SELECT 1 FROM deleted_media_rules WHERE md5=? AND active=1 AND manual_delete=1 LIMIT 1", (value,)).fetchone()
        return row is not None
    return any(str(x.get("md5", "")).strip().lower() == value and _is_manual_reason(str(x.get("reason") or "")) for x in _load_legacy().get("items", []))


def forget_deleted_md5(md5: str, *, settings: dict | None = None) -> int:
    value = str(md5 or "").strip().lower()
    if not value: return 0
    sql_removed = 0
    if settings is not None:
        from core.database.connection import db
        with db(settings, write=True) as con:
            cur = con.execute("UPDATE deleted_media_rules SET active=0, updated_at=? WHERE md5=? AND active=1", (int(time.time()), value))
            sql_removed = int(cur.rowcount or 0)
        # Transitional cleanup: a pre-SQLite JSON registry may still exist if a
        # library is opened directly without the normal startup migrator. It is
        # never queried by live code, but an exact live merge should clear the
        # stale legacy block rather than leave contradictory state behind.
    data = _load_legacy(); items = list(data.get("items", []))
    kept = [x for x in items if str(x.get("md5", "")).strip().lower() != value]
    removed = len(items)-len(kept)
    if removed: data["items"] = kept; _save_legacy(data)
    return sql_removed + removed


def should_skip_deleted_file(path: Path, *, md5: str = "", size: int | None = None, settings: dict | None = None) -> bool:
    value = str(md5 or "").strip().lower()
    if value:
        return has_deleted_md5(value, settings=settings)
    # No name/size suppression in the live SQLite model: only exact MD5 can block content.
    if settings is not None:
        return False
    path = Path(path); name = path.name.lower()
    if size is None:
        try: size = path.stat().st_size
        except Exception: size = 0
    return any(str(x.get("name", "")).lower()==name and int(x.get("size") or 0)==int(size or 0) and not str(x.get("md5") or "").strip() for x in _load_legacy().get("items", []))


def has_deleted_record_for_name(path: Path, *, settings: dict | None = None) -> bool:
    if settings is not None:
        # Live filtering is exact-MD5 only; never pay hash cost based on stale filenames.
        return False
    name = Path(path).name.lower()
    return any(str(x.get("name", "")).lower() == name for x in _load_legacy().get("items", []))


def mark_deleted(path: Path, *, reason: str = "duplicate_delete", md5: str = "", size: int | None = None, pixels=None, settings: dict | None = None, manual_delete: bool | None = None) -> None:
    record_deleted_file(path, reason=reason, md5=md5, size=size, pixels=pixels, settings=settings, manual_delete=manual_delete)
