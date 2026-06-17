"""Persistent manual exclusions for the online grabber preview only.

A user can right-click a grabber card and hide it forever from future online
grabber searches.  Store several stable identities for the same card (MD5, post
URL, file URL and fallback key) so the same post is hidden even when it is
returned from another booru mirror.

These exclusions are deliberately not parser/tagger policy.  They must not be
consulted by MD5 parsing, source collection, no-match/brak handling, or metadata
merge code outside the grabber preview UI.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Any

from core.paths import CACHE_DIR
from core.cache_db import connect as _cache_connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS grabber_preview_exclusions (
    identity_type TEXT NOT NULL,
    identity_value TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    site TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(identity_type, identity_value)
);
CREATE INDEX IF NOT EXISTS idx_grabber_preview_exclusions_active
    ON grabber_preview_exclusions(active, identity_type, identity_value);
CREATE INDEX IF NOT EXISTS idx_grabber_preview_exclusions_created_at
    ON grabber_preview_exclusions(created_at);
"""

_ALLOWED_TYPES = {"md5", "post_url", "file_url", "key", "visual_hash"}
_MIGRATED_PATHS: set[str] = set()


def legacy_db_path(settings: dict | None = None) -> Path:
    return CACHE_DIR / "grabber_preview_exclusions" / "grabber_preview_exclusions.sqlite3"


def _db_path(settings: dict | None = None) -> Path:
    # v258: exclusions share the auxiliary settings/cache/cache.sqlite with
    # other lightweight caches instead of maintaining a separate SQLite/WAL file.
    from core.cache_db import cache_path as _shared_cache_path
    return _shared_cache_path(settings)


def _migrate_legacy_if_needed(con: sqlite3.Connection) -> None:
    try:
        legacy = legacy_db_path(None)
        if not legacy.is_file():
            return
        row = con.execute("SELECT COUNT(*) FROM grabber_preview_exclusions").fetchone()
        if row and int(row[0] or 0) > 0:
            return
        con.execute("ATTACH DATABASE ? AS old_grabber_exclusions", (str(legacy),))
        try:
            con.execute(
                "INSERT OR IGNORE INTO grabber_preview_exclusions"
                "(identity_type, identity_value, created_at, reason, query, site, note, active) "
                "SELECT identity_type, identity_value, created_at, reason, query, site, note, active "
                "FROM old_grabber_exclusions.grabber_preview_exclusions"
            )
            con.commit()
        finally:
            con.execute("DETACH DATABASE old_grabber_exclusions")
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass


def _connect(settings: dict | None = None) -> sqlite3.Connection:
    con = _cache_connect(settings, schema=_SCHEMA)
    key = str(_db_path(settings))
    if key not in _MIGRATED_PATHS:
        _migrate_legacy_if_needed(con)
        _MIGRATED_PATHS.add(key)
    return con


def normalize_md5(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"[0-9a-f]{32}", value) else ""


def normalize_identity(identity_type: str, value: Any) -> tuple[str, str] | None:
    typ = str(identity_type or "").strip().lower()
    if typ not in _ALLOWED_TYPES:
        return None
    raw = str(value or "").strip()
    if not raw:
        return None
    if typ == "md5":
        raw = normalize_md5(raw)
        if not raw:
            return None
    elif typ in {"post_url", "file_url"}:
        raw = raw.rstrip("/")
        if not raw.lower().startswith(("http://", "https://")):
            return None
    elif typ == "visual_hash":
        raw = raw.lower()
    return typ, raw


def compact_identities(identities: Iterable[tuple[str, Any]] | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for typ, value in identities or []:
        norm = normalize_identity(typ, value)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def add_exclusion(
    settings: dict | None,
    identities: Iterable[tuple[str, Any]] | None,
    *,
    reason: str = "manual",
    query: str = "",
    site: str = "",
    note: str = "",
) -> int:
    """Persist identities and return the number of inserted/reactivated rows."""
    rows = compact_identities(identities)
    if not rows:
        return 0
    now = int(time.time())
    changed = 0
    with _connect(settings) as con:
        for typ, value in rows:
            cur = con.execute(
                """INSERT INTO grabber_preview_exclusions
                   (identity_type, identity_value, created_at, reason, query, site, note, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(identity_type, identity_value) DO UPDATE SET
                     active=1,
                     reason=excluded.reason,
                     query=CASE WHEN excluded.query<>'' THEN excluded.query ELSE grabber_preview_exclusions.query END,
                     site=CASE WHEN excluded.site<>'' THEN excluded.site ELSE grabber_preview_exclusions.site END,
                     note=CASE WHEN excluded.note<>'' THEN excluded.note ELSE grabber_preview_exclusions.note END,
                     created_at=grabber_preview_exclusions.created_at""",
                (typ, value, now, str(reason or "manual"), str(query or ""), str(site or ""), str(note or "")),
            )
            changed += int(cur.rowcount or 0)
        con.commit()
    return changed


def is_excluded(settings: dict | None, identities: Iterable[tuple[str, Any]] | None) -> bool:
    rows = compact_identities(identities)
    if not rows:
        return False
    # One indexed query for all identities.  A card can have md5/post_url/file_url
    # identities; doing a SELECT per identity multiplied badly on large grabber
    # pages.
    clauses = " OR ".join(["(identity_type=? AND identity_value=?)" for _ in rows])
    params: list[str] = []
    for typ, value in rows:
        params.extend([typ, value])
    with _connect(settings) as con:
        row = con.execute(
            f"SELECT 1 FROM grabber_preview_exclusions WHERE active=1 AND ({clauses}) LIMIT 1",
            params,
        ).fetchone()
    return bool(row)


def active_identity_set(settings: dict | None = None) -> set[str]:
    """Return compact string keys like 'md5:abc...' for fast in-memory checks."""
    out: set[str] = set()
    with _connect(settings) as con:
        for typ, value in con.execute(
            "SELECT identity_type, identity_value FROM grabber_preview_exclusions WHERE active=1"
        ).fetchall():
            norm = normalize_identity(str(typ or ""), str(value or ""))
            if norm:
                out.add(f"{norm[0]}:{norm[1]}")
    return out


def deactivate(settings: dict | None, identities: Iterable[tuple[str, Any]] | None) -> int:
    rows = compact_identities(identities)
    if not rows:
        return 0
    clauses = " OR ".join(["(identity_type=? AND identity_value=?)" for _ in rows])
    params: list[str] = []
    for typ, value in rows:
        params.extend([typ, value])
    with _connect(settings) as con:
        cur = con.execute(
            f"UPDATE grabber_preview_exclusions SET active=0 WHERE {clauses}",
            params,
        )
        con.commit()
        return int(cur.rowcount or 0)
