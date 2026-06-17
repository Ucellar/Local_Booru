"""Persistent real-file hash cache for parser/tagger input files.

Filename hashes are only hints.  The parser must use byte hashes for exact MD5
lookups, even when a file is named ``photo_2022-...`` or when a user renamed a
file to a stale MD5 from another image.  This cache stores path/stat -> md5/phash
under the shared settings/cache/cache.sqlite so expensive byte hashing is paid once per unchanged file.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from core.paths import CACHE_DIR
from core.cache_db import connect as _cache_connect
from core.media_utils import file_md5 as _byte_md5

_MD5_RE = re.compile(r"^[0-9a-f]{32}$", re.I)
_MIGRATED_PATHS: set[str] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS parser_file_hash_cache (
    path TEXT PRIMARY KEY,
    file_name TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    hash_md5 TEXT NOT NULL DEFAULT '',
    hash_phash TEXT NOT NULL DEFAULT '',
    computed_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_parser_file_hash_cache_md5 ON parser_file_hash_cache(hash_md5);
CREATE INDEX IF NOT EXISTS idx_parser_file_hash_cache_name ON parser_file_hash_cache(file_name);
CREATE INDEX IF NOT EXISTS idx_parser_file_hash_cache_stat ON parser_file_hash_cache(size_bytes, mtime_ns);
"""


def enabled(settings: dict | None) -> bool:
    try:
        return bool((settings or {}).get("parser_real_file_hash_cache_enabled", True))
    except Exception:
        return True


def legacy_cache_path(settings: dict | None = None) -> Path:
    return CACHE_DIR / "parser_file_hash_cache" / "parser_file_hash_cache.sqlite"


def cache_path(settings: dict | None = None) -> Path:
    # v258: parser hash cache shares one auxiliary cache DB instead of opening
    # its own SQLite/WAL files on every lookup.  The old per-feature DB remains
    # a migration source only.
    from core.cache_db import cache_path as _shared_cache_path
    return _shared_cache_path(settings)


def _migrate_legacy_if_needed(con: sqlite3.Connection) -> None:
    try:
        legacy = legacy_cache_path(None)
        if not legacy.is_file():
            return
        row = con.execute("SELECT COUNT(*) FROM parser_file_hash_cache").fetchone()
        if row and int(row[0] or 0) > 0:
            return
        con.execute("ATTACH DATABASE ? AS old_parser_hash_cache", (str(legacy),))
        try:
            con.execute(
                "INSERT OR IGNORE INTO parser_file_hash_cache(path,file_name,size_bytes,mtime_ns,hash_md5,hash_phash,computed_at) "
                "SELECT path,file_name,size_bytes,mtime_ns,hash_md5,hash_phash,computed_at "
                "FROM old_parser_hash_cache.parser_file_hash_cache"
            )
            con.commit()
        finally:
            con.execute("DETACH DATABASE old_parser_hash_cache")
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass


def _connect(settings: dict | None = None) -> sqlite3.Connection:
    con = _cache_connect(settings, schema=_SCHEMA)
    key = str(cache_path(settings))
    if key not in _MIGRATED_PATHS:
        _migrate_legacy_if_needed(con)
        _MIGRATED_PATHS.add(key)
    return con


def _stat(path: str | Path) -> tuple[int, int, str]:
    p = Path(path)
    st = p.stat()
    return int(st.st_size), int(st.st_mtime_ns), p.name


def normalize_md5(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if _MD5_RE.fullmatch(value) else ""


def filename_md5(path: str | Path) -> str:
    try:
        stem = Path(path).stem.strip().lower()
        return stem if _MD5_RE.fullmatch(stem) else ""
    except Exception:
        return ""


def lookup(settings: dict | None, path: str | Path) -> dict | None:
    """Return cached hashes only when path, size and mtime still match."""
    if not enabled(settings):
        return None
    try:
        p = Path(path)
        size, mtime_ns, name = _stat(p)
        with _connect(settings) as con:
            row = con.execute(
                "SELECT path,file_name,size_bytes,mtime_ns,hash_md5,hash_phash,computed_at "
                "FROM parser_file_hash_cache WHERE path=? LIMIT 1",
                (str(p),),
            ).fetchone()
        if not row:
            return None
        if int(row[2] or 0) != size or int(row[3] or 0) != mtime_ns:
            return None
        return {
            "path": str(row[0] or p),
            "file_name": str(row[1] or name),
            "size_bytes": size,
            "mtime_ns": mtime_ns,
            "hash_md5": normalize_md5(row[4]),
            "hash_phash": str(row[5] or ""),
            "computed_at": int(row[6] or 0),
            "cache_hit": True,
        }
    except Exception:
        return None


def upsert(settings: dict | None, path: str | Path, *, hash_md5: str = "", hash_phash: str = "") -> bool:
    if not enabled(settings):
        return False
    md5 = normalize_md5(hash_md5)
    phash = str(hash_phash or "").strip().lower()
    if not md5 and not phash:
        return False
    try:
        p = Path(path)
        size, mtime_ns, name = _stat(p)
        now = int(time.time())
        with _connect(settings) as con:
            con.execute(
                "INSERT INTO parser_file_hash_cache(path,file_name,size_bytes,mtime_ns,hash_md5,hash_phash,computed_at) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET "
                "file_name=excluded.file_name, size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns, "
                "hash_md5=CASE WHEN excluded.hash_md5<>'' THEN excluded.hash_md5 ELSE parser_file_hash_cache.hash_md5 END, "
                "hash_phash=CASE WHEN excluded.hash_phash<>'' THEN excluded.hash_phash ELSE parser_file_hash_cache.hash_phash END, "
                "computed_at=excluded.computed_at",
                (str(p), name, size, mtime_ns, md5, phash, now),
            )
        return True
    except Exception:
        return False


def get_or_compute_md5(settings: dict | None, path: str | Path) -> tuple[str, bool]:
    """Return (real byte MD5, cache_hit). Never uses the filename as truth."""
    cached = lookup(settings, path)
    if cached and cached.get("hash_md5"):
        return str(cached["hash_md5"]), True
    md5 = _byte_md5(Path(path)).lower()
    upsert(settings, path, hash_md5=md5)
    return md5, False


def get_or_compute_phash(settings: dict | None, path: str | Path, compute_func) -> tuple[str, bool]:
    """Return (pHash, cache_hit) using caller's safe image/video-frame function."""
    cached = lookup(settings, path)
    if cached and cached.get("hash_phash"):
        return str(cached["hash_phash"]), True
    phash = str(compute_func(path) or "").strip().lower()
    if phash:
        upsert(settings, path, hash_phash=phash)
    return phash, False


def bind_image_row_md5(settings: dict | None, image_id: int, path: str | Path) -> str:
    """Backward-compatible helper: compute/cache real MD5 only.

    v258 deliberately stopped writing to the main ``images`` table from this
    cache module.  Main DB mutation belongs to database/library lifecycle code,
    not to a parser hash-cache helper; callers that need to update image rows
    should do so explicitly after this function returns the MD5.
    """
    md5, _hit = get_or_compute_md5(settings, path)
    return md5


def count(settings: dict | None = None) -> int:
    try:
        with _connect(settings) as con:
            row = con.execute("SELECT COUNT(*) FROM parser_file_hash_cache").fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0
