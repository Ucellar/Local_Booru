"""NO_MATCH state stored in SQLite.

The old ``nomatch_cache.json`` and ``.nomatch`` markers are accepted only as a
one-time import source. Normal operation reads/writes ``no_match_items``.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse
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
    try:
        return urlparse(str(url or "")).netloc.lower().replace("www.", "")
    except Exception:
        return ""


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
                    visual_status=CASE WHEN excluded.visual_status<>'' THEN excluded.visual_status ELSE no_match_items.visual_status END,
                    visual_confidence=CASE WHEN excluded.visual_status<>'' THEN excluded.visual_confidence ELSE no_match_items.visual_confidence END,
                    visual_model=CASE WHEN excluded.visual_model<>'' THEN excluded.visual_model ELSE no_match_items.visual_model END,
                    visual_checked_at=CASE WHEN excluded.visual_checked_at>0 THEN excluded.visual_checked_at ELSE no_match_items.visual_checked_at END,
                    updated_at=excluded.updated_at,
                    active=1""",
            (str(p), str(media_path or ""), str(reason or "no_match"), "", source_url,
             str(source_label or ""), source_host, float(source_similarity or 0.0), str(last_error or ""),
             str(visual_status or ""), float(visual_confidence or 0.0), str(visual_model or ""),
             int(visual_checked_at or 0), int(time.time())),
        )


def set_visual_status(
    path,
    visual_status: str,
    visual_confidence: float = 0.0,
    visual_model: str = "",
    visual_checked_at: int | None = None,
    *,
    settings: dict | None = None,
):
    """Update only the local visual triage flag for an active NO_MATCH row."""
    if settings is None:
        return
    key = str(Path(path))
    status = str(visual_status or "").strip().lower()
    if status not in {"real", "booru", "unknown"}:
        return
    checked = int(visual_checked_at or time.time())
    from core.database.connection import db
    with db(settings, write=True) as con:
        _ensure_source_only_columns(con)
        con.execute(
            """UPDATE no_match_items
               SET visual_status=?, visual_confidence=?, visual_model=?, visual_checked_at=?, updated_at=?
               WHERE active=1 AND (original_path=? OR media_path=?)""",
            (status, float(visual_confidence or 0.0), str(visual_model or ""), checked, int(time.time()), key, key),
        )


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
    """Remove obsolete NO_MATCH rows/copies after the same bytes become FOUND.

    Retrying a NO_MATCH file can promote it through rule34 image-key/MD5 relay.
    The old no_match copy is disposable generated output and must not remain as
    a separate gallery/Брак card.  Only managed Local Booru output files are
    unlinked; source archive files are never touched.
    """
    result = {"rows_deactivated": 0, "image_rows_removed": 0, "files_removed": 0, "errors": 0}
    if settings is None:
        return result
    value = str(md5 or "").strip().lower()
    keys = set()
    for raw in (original_path, promoted_path):
        if raw:
            try:
                keys.add(str(Path(raw)))
            except Exception:
                keys.add(str(raw))
    try:
        from core.database.connection import db
        from core.source_protection import require_managed_media_mutation
        from core.services.media_storage_service import unlink_managed, delete_bucket_artifacts
        from core.paths import result_output_base
        now = int(time.time())
        candidate_paths: set[str] = set()
        with db(settings, write=True) as con:
            _ensure_source_only_columns(con)
            # Direct row match first: covers retry-queue temp paths and current
            # no_match media paths even if their MD5 was not indexed correctly.
            if keys:
                for key in sorted(keys):
                    rows = con.execute(
                        """SELECT original_path, media_path FROM no_match_items
                           WHERE active=1 AND (original_path=? OR media_path=?)""",
                        (key, key),
                    ).fetchall()
                    for r in rows:
                        for col in ("original_path", "media_path"):
                            val = str(r[col] or "")
                            if val:
                                candidate_paths.add(val)
            # Exact-MD5 no_match image rows are obsolete once the same bytes are
            # stored as FOUND/PARTIAL.  This is the important part for rechecks
            # of old Брак rows: old session copies are in different folders, so
            # basename cleanup alone is insufficient.
            if value:
                rows = con.execute(
                    """SELECT path FROM images
                       WHERE deleted=0
                         AND lower(COALESCE(hash_md5,''))=?
                         AND bucket IN ('no_match','downloaded_no_match')""",
                    (value,),
                ).fetchall()
                for r in rows:
                    val = str(r["path"] or "")
                    if val:
                        candidate_paths.add(val)
            # Also catch active NO_MATCH rows whose media path still points to a
            # stale managed copy with the same basename in output/no_match/media.
            # The expensive hash check below keeps this safe.
            basename_keys = set()
            for key in list(keys):
                try:
                    name = Path(key).name
                except Exception:
                    name = ""
                if not name:
                    continue
                basename_keys.add(name)
                rows = con.execute(
                    """SELECT original_path, media_path FROM no_match_items
                       WHERE active=1 AND (original_path LIKE ? OR media_path LIKE ?)""",
                    ("%" + name, "%" + name),
                ).fetchall()
                for r in rows:
                    for col in ("original_path", "media_path"):
                        val = str(r[col] or "")
                        if val:
                            candidate_paths.add(val)

            # v308: old builds could leave physical output/no_match copies while
            # the current DB no longer has the old no_match row (wrong/new DB,
            # deleted rows, or a previous cleanup bug).  In that case there is
            # nothing to deactivate in SQLite, but the gallery/scan can still
            # rediscover those generated copies later.  Add same-basename
            # managed no_match files as cleanup candidates; the MD5 check below
            # keeps this conservative and source originals are never touched.
            try:
                no_match_roots = [
                    result_output_base(settings) / "no_match" / "media",
                    result_output_base(settings) / "downloads" / "no_match" / "media",
                ]
                for name in sorted(basename_keys):
                    for root in no_match_roots:
                        if not root.exists() or not root.is_dir():
                            continue
                        found = 0
                        for candidate in root.rglob(name):
                            candidate_paths.add(str(candidate))
                            found += 1
                            if found >= 64:
                                break
            except Exception:
                result["errors"] += 1

            # Deactivate durable NO_MATCH UI rows by all known aliases.
            aliases = set(candidate_paths) | keys
            for alias in sorted(x for x in aliases if x):
                cur = con.execute(
                    """UPDATE no_match_items SET active=0, updated_at=?
                       WHERE active=1 AND (original_path=? OR media_path=?)""",
                    (now, alias, alias),
                )
                result["rows_deactivated"] += int(cur.rowcount or 0)

        promoted_resolved = None
        try:
            promoted_resolved = Path(promoted_path).resolve() if promoted_path else None
        except Exception:
            promoted_resolved = None

        removable: list[Path] = []
        # File-system cleanup is intentionally conservative: only generated
        # output/no_match copies are removed, and only when MD5 matches if a
        # target MD5 is known.
        for raw in sorted(candidate_paths):
            try:
                p = Path(raw)
                if promoted_resolved is not None:
                    try:
                        if p.resolve() == promoted_resolved:
                            continue
                    except Exception:
                        pass
                if not p.exists() or not p.is_file():
                    continue
                parts = {x.lower() for x in p.parts}
                if "no_match" not in parts:
                    continue
                if not require_managed_media_mutation(settings, p, "nomatch.promote_cleanup"):
                    continue
                if value:
                    try:
                        from core.media_utils import file_md5
                        if file_md5(p).lower() != value:
                            continue
                    except Exception:
                        continue
                removable.append(p)
            except Exception:
                result["errors"] += 1

        for p in removable:
            try:
                if unlink_managed(settings, p, operation="nomatch.promote_cleanup"):
                    result["files_removed"] += 1
                try:
                    delete_bucket_artifacts(settings, p, operation="nomatch.promote_cleanup_artifacts")
                except Exception:
                    pass
            except Exception:
                result["errors"] += 1

        # Hide/delete no_match image rows after the physical cleanup.  This
        # prevents the gallery from showing the same file once as FOUND and once
        # as stale NO_MATCH even when Windows kept the old file locked briefly.
        with db(settings, write=True) as con:
            for raw in sorted(candidate_paths):
                try:
                    cur = con.execute(
                        """DELETE FROM images
                           WHERE path=? AND bucket IN ('no_match','downloaded_no_match')""",
                        (str(Path(raw)),),
                    )
                    result["image_rows_removed"] += int(cur.rowcount or 0)
                    con.execute("DELETE FROM processed_files WHERE media_path=?", (str(Path(raw)),))
                except Exception:
                    result["errors"] += 1
            try:
                from core.database.storage import cleanup_orphan_rows
                # Use the public wrapper with its own connection outside this
                # write transaction would be slower; do minimal local cleanup.
                con.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM image_tags)")
                con.execute("DELETE FROM sources WHERE id NOT IN (SELECT DISTINCT source_id FROM image_sources)")
            except Exception:
                pass
    except Exception:
        result["errors"] += 1
    return result

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
                    WHERE active=1 AND (original_path IN ({ph}) OR media_path IN ({ph}))""",
                [now] + chunk + chunk,
            )
            changed += int(cur.rowcount or 0)
    return changed
