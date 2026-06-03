"""NO_MATCH state stored in SQLite.

The old ``nomatch_cache.json`` and ``.nomatch`` markers are accepted only as a
one-time import source. Normal operation reads/writes ``no_match_items``.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from core.paths import CACHE_DIR

NO_MATCH_DB_FILE = CACHE_DIR / "nomatch_cache.json"
_MIGRATION_MARK = CACHE_DIR / "nomatch_cache.migrated_to_sqlite"


def _legacy_items() -> list[dict]:
    try:
        if not NO_MATCH_DB_FILE.exists():
            return []
        data = json.loads(NO_MATCH_DB_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return [dict(x) for x in data.values() if isinstance(x, dict) and x.get("path")]
        if isinstance(data, list):
            return [dict(x) for x in data if isinstance(x, dict) and x.get("path")]
    except Exception:
        pass
    return []


def migrate_legacy_nomatch_cache(settings: dict) -> dict:
    if _MIGRATION_MARK.exists() or not NO_MATCH_DB_FILE.exists():
        return {"imported": 0, "backup": ""}
    items = _legacy_items()
    try:
        from core.database.connection import db
        with db(settings, write=True) as con:
            for item in items:
                path = str(Path(item.get("path", "")))
                if not path:
                    continue
                con.execute(
                    """INSERT INTO no_match_items(original_path,media_path,reason,manual_url,updated_at,active)
                       VALUES(?,?,?,?,?,1)
                       ON CONFLICT(original_path) DO UPDATE SET reason=excluded.reason,
                       manual_url=CASE WHEN excluded.manual_url<>'' THEN excluded.manual_url ELSE no_match_items.manual_url END,
                       updated_at=MAX(no_match_items.updated_at,excluded.updated_at),active=1""",
                    (path, path, str(item.get("reason") or "legacy_cache"), str(item.get("manual_url") or ""), int(float(item.get("ts") or time.time()))),
                )
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = NO_MATCH_DB_FILE.with_name(f"nomatch_cache_{stamp}_legacy_before_sqlite.json.bak")
        shutil.copy2(NO_MATCH_DB_FILE, backup)
        _MIGRATION_MARK.write_text(f"imported={len(items)}\nat={int(time.time())}\nbackup={backup}\n", encoding="utf-8")
        return {"imported": len(items), "backup": str(backup)}
    except Exception as exc:
        return {"imported": 0, "backup": "", "error": str(exc)}


def list_nomatches(root=None, *, settings: dict | None = None):
    if settings is None:
        items = _legacy_items()
    else:
        from core.database.connection import db
        with db(settings, readonly=True) as con:
            rows = con.execute(
                """SELECT original_path,media_path,reason,manual_url,updated_at FROM no_match_items
                   WHERE active=1 ORDER BY updated_at DESC"""
            ).fetchall()
        items = [{"path": str(r["media_path"] or r["original_path"]), "original_path": str(r["original_path"]), "name": Path(str(r["media_path"] or r["original_path"])).name, "reason": str(r["reason"] or "no_match"), "ts": int(r["updated_at"] or 0), "manual_url": str(r["manual_url"] or "")} for r in rows]
    if root:
        try:
            root_s = str(Path(root).resolve()).lower()
            items = [x for x in items if str(x.get("original_path", x.get("path", ""))).lower().startswith(root_s)]
        except Exception:
            pass
    return sorted(items, key=lambda x: float(x.get("ts", 0)), reverse=True)


def upsert_nomatch(path, reason="no_match", *, settings: dict | None = None, media_path: str | Path | None = None):
    p = Path(path)
    if settings is None:
        return
    from core.database.connection import db
    with db(settings, write=True) as con:
        con.execute(
            """INSERT INTO no_match_items(original_path,media_path,reason,manual_url,updated_at,active)
               VALUES(?,?,?,?,?,1)
               ON CONFLICT(original_path) DO UPDATE SET media_path=CASE WHEN excluded.media_path<>'' THEN excluded.media_path ELSE no_match_items.media_path END,
               reason=excluded.reason, updated_at=excluded.updated_at, active=1""",
            (str(p), str(media_path or ""), str(reason or "no_match"), "", int(time.time())),
        )


def remove_nomatch(path, *, settings: dict | None = None):
    if settings is None:
        return
    key = str(Path(path))
    from core.database.connection import db
    with db(settings, write=True) as con:
        con.execute("UPDATE no_match_items SET active=0,updated_at=? WHERE original_path=? OR media_path=?", (int(time.time()), key, key))


def set_manual_url(path, url, *, settings: dict | None = None):
    if settings is None:
        return
    key = str(Path(path))
    from core.database.connection import db
    with db(settings, write=True) as con:
        con.execute(
            """UPDATE no_match_items SET manual_url=?, updated_at=? WHERE original_path=? OR media_path=?""",
            (str(url or ""), int(time.time()), key, key),
        )
