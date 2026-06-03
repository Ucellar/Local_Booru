"""Favorites stored in SQLite, with one-time import from legacy JSON."""
from __future__ import annotations

import json
from pathlib import Path
from core.paths import FAVORITES_FILE


def _legacy_set() -> set[str]:
    if FAVORITES_FILE.exists():
        try:
            return {str(x) for x in json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return set()


def _migrate_once(settings) -> None:
    if not settings:
        return
    try:
        from core.database.connection import db
        with db(settings, write=True) as con:
            done = con.execute("SELECT value FROM app_state WHERE key='favorites_json_migrated'").fetchone()
            if done:
                return
            for path in _legacy_set():
                con.execute("UPDATE images SET favorite=1 WHERE path=?", (path,))
            con.execute("INSERT OR REPLACE INTO app_state(key,value) VALUES('favorites_json_migrated','1')")
    except Exception:
        pass


def load_favorites(settings=None):
    if not settings:
        return _legacy_set()
    _migrate_once(settings)
    try:
        from core.database.connection import db
        with db(settings, readonly=True) as con:
            return {str(r["path"]) for r in con.execute("SELECT path FROM images WHERE favorite=1 AND deleted=0").fetchall()}
    except Exception:
        return _legacy_set()


def is_favorite(settings, path: str | Path) -> bool:
    return str(path) in load_favorites(settings)


def set_favorite(settings, path: str | Path, enabled: bool) -> None:
    if settings:
        try:
            from core.database.connection import db
            with db(settings, write=True) as con:
                con.execute("UPDATE images SET favorite=? WHERE path=?", (1 if enabled else 0, str(path)))
            return
        except Exception:
            pass
    favs = _legacy_set()
    if enabled:
        favs.add(str(path))
    else:
        favs.discard(str(path))
    FAVORITES_FILE.write_text(json.dumps(sorted(favs), ensure_ascii=False, indent=2), encoding="utf-8")


def save_favorites(favs, settings=None):
    # Compatibility for old callers; new UI should call set_favorite.
    if settings:
        current = load_favorites(settings)
        for p in current - set(favs):
            set_favorite(settings, p, False)
        for p in set(favs) - current:
            set_favorite(settings, p, True)
        return
    FAVORITES_FILE.write_text(json.dumps(sorted(list(favs)), ensure_ascii=False, indent=2), encoding="utf-8")
