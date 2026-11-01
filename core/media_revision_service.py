"""Revision metadata and canonical-MD5 alias invariants.

Transactional revision metadata primitives shared by the v459 physical upgrade state machine.
"""
from __future__ import annotations

import time
from typing import Any


def create_revision_plan(
    con,
    *,
    image_id: int,
    old_md5: str,
    old_path: str = "",
    new_md5: str = "",
    new_path: str = "",
    replacement_reason: str = "quality_upgrade",
    identity_basis: str = "",
    quality_basis: str = "",
    source_site: str = "",
    source_post_id: str = "",
    old_width: int = 0,
    old_height: int = 0,
    old_file_size: int = 0,
    old_format: str = "",
    new_width: int = 0,
    new_height: int = 0,
    new_file_size: int = 0,
    new_format: str = "",
    staging_path: str = "",
    final_path: str = "",
    verification_json: str = "{}",
    swap_state: str = "planned",
    identity_confidence: float = 0.0,
    quality_confidence: float = 0.0,
    candidate_source_url: str = "",
) -> int:
    now = int(time.time())
    cur = con.execute(
        """INSERT INTO media_revisions(
               image_id,status,old_md5,old_path,old_width,old_height,old_file_size,old_format,
               new_md5,new_path,new_width,new_height,new_file_size,new_format,replacement_reason,
               identity_basis,quality_basis,source_site,source_post_id,created_at,
               staging_path,final_path,verification_json,swap_state,identity_confidence,
               quality_confidence,candidate_source_url
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            int(image_id), "planned", str(old_md5 or "").lower(), str(old_path or ""),
            int(old_width or 0), int(old_height or 0), int(old_file_size or 0), str(old_format or ""),
            str(new_md5 or "").lower(), str(new_path or ""), int(new_width or 0), int(new_height or 0),
            int(new_file_size or 0), str(new_format or ""), str(replacement_reason or "quality_upgrade"),
            str(identity_basis or ""), str(quality_basis or ""), str(source_site or ""),
            str(source_post_id or ""), now, str(staging_path or ""), str(final_path or ""),
            str(verification_json or "{}"), str(swap_state or "planned"),
            float(identity_confidence or 0), float(quality_confidence or 0),
            str(candidate_source_url or ""),
        ),
    )
    return int(cur.lastrowid)


def rebind_media_paths(con, *, image_id: int, old_path: str, new_path: str, new_md5: str = "") -> None:
    """The journals that name a card's file by its path follow the file to ``new_path`` (inside the caller's
    transaction; ``images`` itself is the caller's): the source files it came from, the parser's journal, the queue of
    tag enrichment, Брак, the last check of the file's presence."""
    old, new = str(old_path), str(new_path)
    if not old or old == new:
        return
    con.execute("UPDATE processed_files SET media_path=? WHERE media_path=?", (new, old))
    from core.database.storage import rebind_source_originals
    rebind_source_originals(con, old, new, new_md5=str(new_md5 or ""))
    con.execute("UPDATE tag_enrichment_queue SET media_path=? WHERE media_path=?", (new, old))
    con.execute("UPDATE no_match_items SET media_path=? WHERE media_path=?", (new, old))
    import sqlite3
    try:
        con.execute("UPDATE image_file_integrity SET checked_path=? WHERE image_id=? AND checked_path=?",
                    (new, int(image_id), old))
    except sqlite3.OperationalError:     # a base from before migration 027: no such journal
        pass


def rebind_site_md5_aliases(
    con,
    *,
    image_id: int,
    new_local_md5: str,
    revision_id: int,
    rebound_at: int | None = None,
) -> int:
    """Atomically point every site-MD5 alias snapshot at the new canonical bytes."""
    canonical = str(new_local_md5 or "").strip().lower()
    if not canonical:
        raise ValueError("new_local_md5 is required")
    now = int(rebound_at or time.time())
    cur = con.execute(
        """UPDATE image_site_md5_aliases
           SET local_md5=?, revision_id=?, rebound_at=?
           WHERE image_id=?""",
        (canonical, int(revision_id or 0), now, int(image_id)),
    )
    return int(cur.rowcount or 0)


def commit_revision_metadata(
    con,
    *,
    revision_id: int,
    image_id: int,
    new_md5: str,
    new_path: str | None = None,
    tombstone_old: bool = True,
    physical_deleted: bool = False,
) -> dict[str, Any]:
    """Commit canonical metadata and alias snapshots in one SQLite transaction.

    The caller owns the transaction. This function never deletes media bytes.
    """
    row = con.execute(
        "SELECT * FROM media_revisions WHERE revision_id=? AND image_id=?",
        (int(revision_id), int(image_id)),
    ).fetchone()
    if not row:
        raise KeyError("media revision not found")
    canonical = str(new_md5 or row["new_md5"] or "").strip().lower()
    if not canonical:
        raise ValueError("new canonical MD5 is required")
    now = int(time.time())
    if new_path is None:
        con.execute("UPDATE images SET hash_md5=?, current_revision_id=? WHERE id=?",
                    (canonical, int(revision_id), int(image_id)))
    else:
        target_path = str(new_path)
        try:
            from pathlib import Path as _Path
            _p = _Path(target_path)
            _st = _p.stat() if _p.exists() else None
            _size = int(_st.st_size) if _st else int(row["new_file_size"] or 0)
            _mtime = int(_st.st_mtime_ns) if _st else 0
        except Exception:
            _size = int(row["new_file_size"] or 0); _mtime = 0
        con.execute(
            """UPDATE images SET hash_md5=?, path=?, file_name=?, size_bytes=?, width=?, height=?,
                   mtime_ns=?, is_video=?, indexed_at=?, current_revision_id=? WHERE id=?""",
            (
                canonical, target_path, target_path.replace('\\', '/').rsplit('/', 1)[-1],
                _size, int(row["new_width"] or 0), int(row["new_height"] or 0), _mtime,
                1 if str(row["new_format"] or "").lower() in {"mp4","webm","mkv","mov","avi","m4v"} else 0,
                now, int(revision_id), int(image_id),
            ),
        )
        old_path = str(row["old_path"] or "")
        if old_path and old_path != target_path:
            # Path-bearing caches/journals are rebound inside the same transaction.
            rebind_media_paths(con, image_id=int(image_id), old_path=old_path, new_path=target_path,
                               new_md5=str(canonical or ""))
    aliases = rebind_site_md5_aliases(
        con, image_id=image_id, new_local_md5=canonical, revision_id=revision_id, rebound_at=now
    )
    con.execute(
        """UPDATE media_revisions SET status='committed',swap_state='db_committed',new_md5=?,
               new_path=COALESCE(NULLIF(?,''),new_path),final_path=COALESCE(NULLIF(?,''),final_path),
               committed_at=?,old_file_deleted=? WHERE revision_id=?""",
        (canonical, str(new_path or ""), str(new_path or ""), now, 1 if physical_deleted else 0, int(revision_id)),
    )
    if tombstone_old:
        con.execute(
            """INSERT INTO retired_media_tombstones(
                   image_id,revision_id,old_path,old_md5,replaced_by_md5,reason,retired_at,physical_deleted
               ) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(old_path,old_md5) DO UPDATE SET
                   replaced_by_md5=excluded.replaced_by_md5,
                   revision_id=excluded.revision_id,
                   retired_at=excluded.retired_at,
                   physical_deleted=excluded.physical_deleted""",
            (
                int(image_id), int(revision_id), str(row["old_path"] or ""),
                str(row["old_md5"] or "").lower(), canonical,
                str(row["replacement_reason"] or "quality_upgrade"), now,
                1 if physical_deleted else 0,
            ),
        )
    return {"revision_id": int(revision_id), "image_id": int(image_id), "new_md5": canonical, "aliases_rebound": aliases}


def alias_snapshot_mismatches(con, image_id: int | None = None) -> list[dict[str, Any]]:
    sql = """SELECT a.site_md5,a.image_id,a.local_md5,i.hash_md5
             FROM image_site_md5_aliases a JOIN images i ON i.id=a.image_id
             WHERE lower(COALESCE(a.local_md5,'')) != lower(COALESCE(i.hash_md5,''))"""
    params: tuple[Any, ...] = ()
    if image_id is not None:
        sql += " AND a.image_id=?"
        params = (int(image_id),)
    rows = con.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
