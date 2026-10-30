"""NO_MATCH state stored in SQLite.

The old ``nomatch_cache.json`` and ``.nomatch`` markers are accepted only as a
one-time import source. Normal operation reads/writes ``no_match_items``.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from core.paths import CACHE_DIR

NO_MATCH_DB_FILE = CACHE_DIR / "nomatch_cache.json"
_MIGRATION_MARK = CACHE_DIR / "nomatch_cache.migrated_to_sqlite"


def _ensure_source_only_columns(con) -> None:
    """Keep source-only fields available even when an older DB was opened before v157 migration."""
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(no_match_items)").fetchall()}
        add = {
            "source_url": "TEXT NOT NULL DEFAULT ''",
            "source_label": "TEXT NOT NULL DEFAULT ''",
            "source_host": "TEXT NOT NULL DEFAULT ''",
            "source_similarity": "REAL NOT NULL DEFAULT 0",
            "last_error": "TEXT NOT NULL DEFAULT ''",
            "visual_status": "TEXT NOT NULL DEFAULT ''",
            "visual_confidence": "REAL NOT NULL DEFAULT 0",
            "visual_model": "TEXT NOT NULL DEFAULT ''",
            "visual_checked_at": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, ddl in add.items():
            if name not in cols:
                con.execute(f"ALTER TABLE no_match_items ADD COLUMN {name} {ddl}")
    except Exception:
        pass


def _column_names(con, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def ensure_nomatch_schema(settings: dict | None) -> None:
    """Best-effort writable migration for NO_MATCH optional columns.

    v285 added visual_status fields.  If the UI opens an old DB through a
    readonly connection first, ALTER TABLE is impossible and SELECTing the new
    columns makes the Брак page look empty.  Run the migration explicitly with
    a write connection before readonly listing.
    """
    if settings is None:
        return
    try:
        from core.database.connection import db
        with db(settings, write=True) as con:
            _ensure_source_only_columns(con)
    except Exception:
        pass


def _host_from_url(url: str) -> str:
    from core.media_utils import host_from_url
    return host_from_url(url)


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
        batch_size = int((settings or {}).get("legacy_nomatch_migration_batch_size", 500) or 500)
    except Exception:
        batch_size = 500
    batch_size = max(100, min(5000, batch_size))
    imported = 0
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
                imported += 1
                # Release SQLite's writer lock periodically. On a deliberately
                # rebuilt DB the legacy NO_MATCH cache can contain thousands of
                # rows; one huge transaction makes startup health/lifecycle
                # probes wait until busy_timeout.
                if imported % batch_size == 0:
                    con.commit()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = NO_MATCH_DB_FILE.with_name(f"nomatch_cache_{stamp}_legacy_before_sqlite.json.bak")
        shutil.copy2(NO_MATCH_DB_FILE, backup)
        _MIGRATION_MARK.write_text(f"imported={imported}\nat={int(time.time())}\nbackup={backup}\n", encoding="utf-8")
        return {"imported": imported, "backup": str(backup)}
    except Exception as exc:
        return {"imported": 0, "backup": "", "error": str(exc)}


def _live_nomatch_item(
    original_path: str,
    media_path: str,
    reason: str,
    manual_url: str,
    updated_at: int,
    source_url: str = "",
    source_label: str = "",
    source_host: str = "",
    source_similarity: float = 0.0,
    last_error: str = "",
    visual_status: str = "",
    visual_confidence: float = 0.0,
    visual_model: str = "",
    visual_checked_at: int = 0,
) -> dict:
    """Choose an existing file and expose the real triage reason for the UI."""
    original = str(original_path or "")
    media = str(media_path or "")
    media_exists = bool(media and Path(media).exists())
    original_exists = bool(original and Path(original).exists())
    visible = media if media_exists else (original if original_exists else (media or original))
    source_url = str(source_url or "")
    return {
        "path": visible,
        "original_path": original,
        "media_path": media,
        "name": Path(visible).name if visible else "",
        "reason": str(reason or "no_match"),
        "ts": int(updated_at or 0),
        "manual_url": str(manual_url or ""),
        "source_url": source_url,
        "source_label": str(source_label or ""),
        "source_host": str(source_host or _host_from_url(source_url)),
        "source_similarity": float(source_similarity or 0.0),
        "last_error": str(last_error or ""),
        "visual_status": str(visual_status or ""),
        "visual_confidence": float(visual_confidence or 0.0),
        "visual_model": str(visual_model or ""),
        "visual_checked_at": int(visual_checked_at or 0),
        "media_missing": bool(media and not media_exists),
        "fallback_to_original": bool(media and not media_exists and original_exists),
        "file_missing": not bool(media_exists or original_exists),
    }


def list_nomatches(root=None, *, settings: dict | None = None):
    if settings is None:
        items = _legacy_items()
    else:
        from core.database.connection import db
        # Ensure v285 visual/source-only columns exist before readonly SELECT.
        # If this fails for any reason, the SELECT below still uses safe aliases
        # for missing columns instead of failing and emptying the UI.
        ensure_nomatch_schema(settings)
        with db(settings, readonly=True) as con:
            cols = _column_names(con, "no_match_items")
            _ensure_source_only_columns(con)

            def col(name: str, default_sql: str):
                return name if name in cols else f"{default_sql} AS {name}"

            rows = con.execute(
                f"""SELECT original_path,media_path,reason,manual_url,updated_at,
                          {col('source_url', "''")},
                          {col('source_label', "''")},
                          {col('source_host', "''")},
                          {col('source_similarity', '0')},
                          {col('last_error', "''")},
                          {col('visual_status', "''")},
                          {col('visual_confidence', '0')},
                          {col('visual_model', "''")},
                          {col('visual_checked_at', '0')}
                   FROM no_match_items
                   WHERE active=1 ORDER BY updated_at DESC"""
            ).fetchall()
        items = [_live_nomatch_item(
            str(r["original_path"] or ""), str(r["media_path"] or ""), str(r["reason"] or "no_match"),
            str(r["manual_url"] or ""), int(r["updated_at"] or 0),
            str(r["source_url"] or ""), str(r["source_label"] or ""), str(r["source_host"] or ""),
            float(r["source_similarity"] or 0.0), str(r["last_error"] or ""),
            str(r["visual_status"] or ""), float(r["visual_confidence"] or 0.0),
            str(r["visual_model"] or ""), int(r["visual_checked_at"] or 0),
        ) for r in rows]
    if root:
        try:
            root_s = str(Path(root).resolve()).lower()
            def _inside_root(x: dict) -> bool:
                for k in ("original_path", "media_path", "path"):
                    v = str(x.get(k) or "")
                    if not v:
                        continue
                    try:
                        if str(Path(v).resolve()).lower().startswith(root_s):
                            return True
                    except Exception:
                        if v.lower().startswith(root_s):
                            return True
                return False
            items = [x for x in items if _inside_root(x)]
        except Exception:
            pass
    return sorted(items, key=lambda x: float(x.get("ts", 0)), reverse=True)


def entry_for_path(settings: dict, path) -> dict | None:
    """The active Брак entry of one file — its copy in the library or the input file — as ``list_nomatches``
    gives it; None when there is none.  Read-only (a page asks it for every Брак card it shows)."""
    import os
    from core.database.connection import db
    key = str(Path(path))
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT * FROM no_match_items WHERE +active=1 AND (media_path=? OR original_path=?) "
                          "ORDER BY updated_at DESC LIMIT 1", (key, key)).fetchone()
        if row is None and os.name == "nt":       # the same file written in another case
            wanted = os.path.normcase(key)
            row = next((r for r in con.execute("SELECT * FROM no_match_items WHERE active=1 ORDER BY updated_at DESC")
                        if wanted in {os.path.normcase(str(r["media_path"] or "")),
                                      os.path.normcase(str(r["original_path"] or ""))}), None)
    if row is None:
        return None
    r = dict(row)
    return _live_nomatch_item(
        str(r.get("original_path") or ""), str(r.get("media_path") or ""), str(r.get("reason") or "no_match"),
        str(r.get("manual_url") or ""), int(r.get("updated_at") or 0),
        str(r.get("source_url") or ""), str(r.get("source_label") or ""), str(r.get("source_host") or ""),
        float(r.get("source_similarity") or 0.0), str(r.get("last_error") or ""),
        str(r.get("visual_status") or ""), float(r.get("visual_confidence") or 0.0),
        str(r.get("visual_model") or ""), int(r.get("visual_checked_at") or 0),
    )


def upsert_nomatch(
    path,
    reason="no_match",
    *,
    settings: dict | None = None,
    media_path: str | Path | None = None,
    source_url: str = "",
    source_label: str = "",
    source_similarity: float = 0.0,
    last_error: str = "",
    visual_status: str = "",
    visual_confidence: float = 0.0,
    visual_model: str = "",
    visual_checked_at: int = 0,
):
    p = Path(path)
    if settings is None:
        return
    from core.database.connection import db
    source_url = str(source_url or "")
    source_host = _host_from_url(source_url)
    with db(settings, write=True) as con:
        _ensure_source_only_columns(con)
        con.execute(
            """INSERT INTO no_match_items(
                    original_path,media_path,reason,manual_url,source_url,source_label,source_host,
                    source_similarity,last_error,visual_status,visual_confidence,visual_model,visual_checked_at,updated_at,active
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
               ON CONFLICT(original_path) DO UPDATE SET
                    media_path=CASE WHEN excluded.media_path<>'' THEN excluded.media_path ELSE no_match_items.media_path END,
                    reason=excluded.reason,
                    source_url=excluded.source_url,
                    source_label=excluded.source_label,
                    source_host=excluded.source_host,
                    source_similarity=excluded.source_similarity,
                    last_error=excluded.last_error,
                    visual_status=CASE WHEN no_match_items.visual_model='manual' OR excluded.visual_status='' THEN no_match_items.visual_status ELSE excluded.visual_status END,
                    visual_confidence=CASE WHEN no_match_items.visual_model='manual' OR excluded.visual_status='' THEN no_match_items.visual_confidence ELSE excluded.visual_confidence END,
                    visual_checked_at=CASE WHEN no_match_items.visual_model='manual' OR excluded.visual_checked_at<=0 THEN no_match_items.visual_checked_at ELSE excluded.visual_checked_at END,
                    visual_model=CASE WHEN no_match_items.visual_model='manual' OR excluded.visual_model='' THEN no_match_items.visual_model ELSE excluded.visual_model END,
                    updated_at=excluded.updated_at,
                    active=1""",
            (str(p), str(media_path or ""), str(reason or "no_match"), "", source_url,
             str(source_label or ""), source_host, float(source_similarity or 0.0), str(last_error or ""),
             str(visual_status or ""), float(visual_confidence or 0.0), str(visual_model or ""),
             int(visual_checked_at or 0), int(time.time())),
        )
    # Local-only triage is a side lane and must never hold the parser write
    # transaction or alter NO_MATCH admission.
    try:
        from core.local_nomatch_models import enqueue_local_nomatch_analysis
        enqueue_local_nomatch_analysis(settings, media_path or p)
    except Exception:
        pass


MANUAL = "manual"                 # visual_model of a mark the person set («Это фото» / «Это рисунок»)


def set_visual_status(
    path,
    visual_status: str,
    visual_confidence: float = 0.0,
    visual_model: str = "",
    visual_checked_at: int | None = None,
    *,
    settings: dict | None = None,
) -> int:
    """Update only the local visual triage flag for an active NO_MATCH row; a model's answer never replaces the
    person's mark (``visual_model`` «manual», ``set_manual_visual``).  Returns the rows changed."""
    if settings is None:
        return 0
    key = str(Path(path))
    status = str(visual_status or "").strip().lower()
    if status not in {"real", "booru", "unknown", "unreadable"}:     # unreadable: no model can see the file
        return 0
    checked = int(visual_checked_at or time.time())
    keep_mark = "" if str(visual_model or "") == MANUAL else " AND visual_model<>'manual'"
    from core.database.connection import db
    with db(settings, write=True) as con:
        _ensure_source_only_columns(con)
        cur = con.execute(
            f"""UPDATE no_match_items
               SET visual_status=?, visual_confidence=?, visual_model=?, visual_checked_at=?
               WHERE +active=1 AND (original_path=? OR media_path=?){keep_mark}""",
            (status, float(visual_confidence or 0.0), str(visual_model or ""), checked, key, key),
        )
        return int(cur.rowcount or 0)


def set_manual_visual(path, status: str | None, *, settings: dict) -> int:
    """The person's mark on a Брак file: «real» (фото), «booru» (рисунок), or None — the mark taken off (the
    model decides again).  Returns the rows changed."""
    if status is None:
        from core.database.connection import db
        key = str(Path(path))
        with db(settings, write=True) as con:
            cur = con.execute(
                """UPDATE no_match_items SET visual_status='', visual_confidence=0, visual_model='', visual_checked_at=0
                   WHERE +active=1 AND (original_path=? OR media_path=?) AND visual_model='manual'""", (key, key))
            return int(cur.rowcount or 0)
    if status not in ("real", "booru"):
        raise ValueError(f"mark {status!r}")
    return set_visual_status(path, status, 1.0, MANUAL, settings=settings)


def update_nomatch_media_path(original_path: str | Path, media_path: str | Path, *, settings: dict | None = None):
    """Rebind an active NO_MATCH entry after rebuilding its managed copy."""
    if settings is None:
        return
    original = str(Path(original_path))
    media = str(Path(media_path))
    from core.database.connection import db
    with db(settings, write=True) as con:
        con.execute(
            """UPDATE no_match_items SET media_path=?, updated_at=?
               WHERE active=1 AND original_path=?""",
            (media, int(time.time()), original),
        )


def remove_nomatch(path, *, settings: dict | None = None):
    if settings is None:
        return
    key = str(Path(path))
    from core.database.connection import db
    with db(settings, write=True) as con:
        con.execute("UPDATE no_match_items SET active=0,updated_at=? WHERE original_path=? OR media_path=?", (int(time.time()), key, key))



def deactivate_promoted_exact_match(
    *,
    settings: dict | None,
    md5: str = "",
    original_path: str | Path | None = None,
    promoted_path: str | Path | None = None,
) -> dict:
    """Close the Брак entries of exactly these bytes after they became FOUND.

    Touched are only:
      * Брак rows of the same input file (original_path / promoted_path, exact paths);
      * NO_MATCH copies whose bytes have the same MD5 (an older Брак copy of the same picture).
    A file is never matched by a similar name alone: finding "1.png" must not close "11.png"
    or "x_1.png" (that was a LIKE '%1.png' bug that hid other files from Брак and the gallery).
    The promoted (now FOUND) path itself is never deleted.  Only managed output/no_match copies
    are unlinked; source archive files are never touched.
    """
    result = {"rows_deactivated": 0, "image_rows_removed": 0, "files_removed": 0, "errors": 0}
    if settings is None:
        return result
    value = str(md5 or "").strip().lower()
    if len(value) != 32:
        value = ""
    exact_keys: set[str] = set()
    for raw in (original_path, promoted_path):
        if raw:
            exact_keys.add(_norm_path(raw))
    promoted_key = _norm_path(promoted_path) if promoted_path else ""
    try:
        from core.database.connection import db
        from core.source_protection import require_managed_media_mutation
        from core.services.media_storage_service import unlink_managed, delete_bucket_artifacts
        from core.paths import result_output_base
        from core.media_utils import file_md5
        now = int(time.time())
        verified: set[str] = set()          # paths proven to hold the same picture

        def same_bytes(path_text: str) -> bool:
            if not value:
                return False
            try:
                p = Path(path_text)
                return p.is_file() and file_md5(p).lower() == value
            except OSError:
                return False

        with db(settings, write=True) as con:
            _ensure_source_only_columns(con)
            # 1. Брак rows of this very input file.  («+active»: by the indexes of the two paths, not by ``active`` —
            # that read every row of Брак, and every save of the parser comes here.)
            for key in sorted(exact_keys):
                for r in con.execute(
                    """SELECT original_path, media_path FROM no_match_items
                       WHERE +active=1 AND (original_path=? OR media_path=?)""",
                    (key, key),
                ).fetchall():
                    verified.update(v for v in (str(r["original_path"] or ""), str(r["media_path"] or "")) if v)
            # 2. NO_MATCH gallery rows of the same bytes (MD5 recorded by the library).
            if value:
                for r in con.execute(     # by the MD5's index (core/database/storage.media_path_by_md5)
                    """SELECT path FROM images
                       WHERE +deleted=0 AND hash_md5 IN (?,?)
                         AND bucket IN ('no_match','downloaded_no_match')""",
                    (value, value.upper()),
                ).fetchall():
                    if r["path"]:
                        verified.add(str(r["path"]))
            # 3. Older Брак rows of the same file name: accepted only when their copy has the same bytes.
            names = {Path(k).name for k in exact_keys if Path(k).name}
            for name in sorted(names):
                pattern = "%" + _like_escape(name)
                for r in con.execute(
                    """SELECT original_path, media_path FROM no_match_items
                       WHERE active=1 AND (original_path LIKE ? ESCAPE '\\' OR media_path LIKE ? ESCAPE '\\')""",
                    (pattern, pattern),
                ).fetchall():
                    orig, media = str(r["original_path"] or ""), str(r["media_path"] or "")
                    if Path(orig).name != name and Path(media).name != name:
                        continue
                    if same_bytes(media) or same_bytes(orig):
                        verified.update(v for v in (orig, media) if v)
            for alias in sorted(verified | exact_keys):
                cur = con.execute(
                    """UPDATE no_match_items SET active=0, updated_at=?
                       WHERE +active=1 AND (original_path=? OR media_path=?)""",
                    (now, alias, alias),
                )
                result["rows_deactivated"] += int(cur.rowcount or 0)

        # Stale managed copies with the same name in output/no_match/media whose DB row is gone — looked for by name
        # in each folder there (``_folders_of``), not by listing every picture of Брак on every save (``rglob``).
        try:
            roots = [result_output_base(settings) / "no_match" / "media",
                     result_output_base(settings) / "downloads" / "no_match" / "media"]
            for name in sorted(names):
                for root in roots:
                    if not root.is_dir():
                        continue
                    found = 0
                    for place in _folders_of(root):
                        candidate = place / name
                        if not candidate.is_file():
                            continue
                        found += 1
                        if found > 64:
                            break
                        if same_bytes(str(candidate)):
                            verified.add(str(candidate))
        except OSError:
            result["errors"] += 1

        removable: list[Path] = []
        for raw in sorted(verified):
            p = Path(raw)
            if _norm_path(p) == promoted_key:
                continue
            if not p.is_file() or "no_match" not in {x.lower() for x in p.parts}:
                continue
            if not require_managed_media_mutation(settings, p, "nomatch.promote_cleanup"):
                continue
            if value and not same_bytes(raw):
                continue
            removable.append(p)
        for p in removable:
            try:
                if unlink_managed(settings, p, operation="nomatch.promote_cleanup"):
                    result["files_removed"] += 1
                delete_bucket_artifacts(settings, p, operation="nomatch.promote_cleanup_artifacts")
            except OSError:
                result["errors"] += 1

        # Drop the NO_MATCH gallery rows of the removed copies (never the promoted file's own row).  Every save of
        # the parser comes here: the orphan sweep reads every card's tags — only when a card went.
        gone = [raw for raw in sorted(verified) if _norm_path(raw) != promoted_key
                and not (Path(raw).exists() and Path(raw) not in removable)]
        if gone:
            with db(settings, write=True) as con:
                for raw in gone:            # (a copy still on disk — locked or not managed — keeps its card)
                    key = _norm_path(raw)
                    cur = con.execute(
                        "DELETE FROM images WHERE path=? AND bucket IN ('no_match','downloaded_no_match')", (key,))
                    result["image_rows_removed"] += int(cur.rowcount or 0)
                    con.execute("DELETE FROM processed_files WHERE media_path=?", (key,))
                if result["image_rows_removed"]:
                    from core.database.repository import cleanup_orphans
                    cleanup_orphans(con)
    except Exception as exc:  # cleanup is best effort, but never silent
        result["errors"] += 1
        _log_cleanup_error(exc)
    return result


def _norm_path(value) -> str:
    try:
        return str(Path(value))
    except (TypeError, ValueError):
        return str(value or "")


_FOLDERS_LOCK = threading.Lock()
_FOLDERS: dict = {}           # a folder of Брак copies -> (when listed, trusted, ((each folder in it, its time), …))
FOLDERS_MAX = 2000
FOLDERS_FRESH = 600.0         # listed again at least this often (a disk whose folders keep their time — FAT)
RACY_NS = 2_000_000_000       # a folder changed this close to its listing may change again with the same time


def _folders_of(root: Path) -> list:
    """``root`` and every folder in it (Брак keeps its copies in the session folders too), listed again only when
    one of them changed — a folder's time changes when something is put into it or taken out — or ``FOLDERS_FRESH``
    went by.  Every save of the parser looks there for old copies of its file by name: listing thousands of pictures
    of Брак each time cost more than the save itself.  A folder's time comes from a coarse clock (Windows: about
    16 ms, FAT: 2 s): a listing just after a change is not trusted — a second change in the same tick keeps the
    time — and is made again until the folders have been still for ``RACY_NS``."""
    key = str(root)
    with _FOLDERS_LOCK:
        listed_at, trusted, known = _FOLDERS.get(key, (0.0, False, ()))
    if known and trusted and time.monotonic() - listed_at < FOLDERS_FRESH:
        try:
            if all(os.stat(folder).st_mtime_ns == stamp for folder, stamp in known):
                return [Path(folder) for folder, _stamp in known]
        except OSError:           # a folder went: listed again
            pass
    listed_at, started_ns = time.monotonic(), time.time_ns()
    found, todo = [], [key]
    while todo and len(found) < FOLDERS_MAX:
        folder = todo.pop()
        try:
            stamp = os.stat(folder).st_mtime_ns          # before the listing: a change after it is seen next time
            with os.scandir(folder) as entries:
                inside = [e.path for e in entries if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        found.append((folder, stamp))
        todo.extend(sorted(inside, reverse=True))
    trusted = all(stamp < started_ns - RACY_NS for _folder, stamp in found)
    with _FOLDERS_LOCK:
        _FOLDERS[key] = (listed_at, trusted, tuple(found))
    return [Path(folder) for folder, _stamp in found]


def _like_escape(text: str) -> str:
    """Escape LIKE wildcards so a file name is matched literally."""
    return str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _log_cleanup_error(exc: Exception) -> None:
    import logging
    logging.getLogger("local_booru.nomatch").warning("NO_MATCH promote cleanup failed: %s: %s", type(exc).__name__, exc)


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


def deactivate_for_paths(paths, *, settings: dict | None = None) -> int:
    """Deactivate NO_MATCH rows when their managed result is deleted/trashed.

    This does not delete the original source file. It only prevents the triage
    page from showing stale NO_MATCH records after the user removes parser
    results or the no_match bucket.
    """
    if settings is None:
        return 0
    values = [str(Path(p)) for p in (paths or []) if str(p or "").strip()]
    if not values:
        return 0
    from core.database.connection import db
    now = int(time.time())
    changed = 0
    with db(settings, write=True) as con:
        for start in range(0, len(values), 250):
            chunk = values[start:start + 250]
            ph = ",".join("?" for _ in chunk)
            cur = con.execute(
                f"""UPDATE no_match_items SET active=0, updated_at=?
                    WHERE +active=1 AND (original_path IN ({ph}) OR media_path IN ({ph}))""",
                [now] + chunk + chunk,
            )
            changed += int(cur.rowcount or 0)
    return changed
