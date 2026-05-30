"""Library invariant checker and small repair layer.

Checks are deliberately conservative: repair only removes orphan rows and marks
missing media as deleted.  It never deletes real media files.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .connection import db
from .repository import cleanup_orphans
from . import journal


def _now() -> int:
    return int(time.time())


def _add_issue(issues: list[dict[str, Any]], issue_type: str, *, severity: str = "warning", image_id: int = 0, path: str = "", details: str = "") -> None:
    issues.append({
        "issue_type": issue_type,
        "severity": severity,
        "image_id": int(image_id or 0),
        "path": str(path or ""),
        "details": str(details or ""),
    })


def check(settings: dict, *, sample_limit: int | None = None, persist: bool = True) -> dict[str, Any]:
    """Return invariant issues without modifying the library."""
    issues: list[dict[str, Any]] = []
    with db(settings, readonly=True) as con:
        sql = "SELECT id, path, deleted FROM images WHERE deleted=0 ORDER BY id"
        args: list[Any] = []
        if sample_limit:
            sql += " LIMIT ?"; args.append(int(sample_limit))
        for r in con.execute(sql, args).fetchall():
            p = Path(r["path"] or "")
            if not p.exists():
                _add_issue(issues, "missing_media_file", severity="error", image_id=r["id"], path=str(p), details="DB image row points to a file that no longer exists")
            elif not p.is_file():
                _add_issue(issues, "media_path_not_file", severity="error", image_id=r["id"], path=str(p), details="DB image row path exists but is not a file")

        queries = [
            ("orphan_image_tags_image", "SELECT COUNT(*) c FROM image_tags WHERE image_id NOT IN (SELECT id FROM images)"),
            ("orphan_image_tags_tag", "SELECT COUNT(*) c FROM image_tags WHERE tag_id NOT IN (SELECT id FROM tags)"),
            ("orphan_image_sources_image", "SELECT COUNT(*) c FROM image_sources WHERE image_id NOT IN (SELECT id FROM images)"),
            ("orphan_image_sources_source", "SELECT COUNT(*) c FROM image_sources WHERE source_id NOT IN (SELECT id FROM sources)"),
            ("orphan_raw_metadata", "SELECT COUNT(*) c FROM raw_metadata WHERE image_id NOT IN (SELECT id FROM images)"),
            ("empty_tags", "SELECT COUNT(*) c FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM image_tags)"),
            ("empty_sources", "SELECT COUNT(*) c FROM sources WHERE id NOT IN (SELECT DISTINCT source_id FROM image_sources)"),
        ]
        for issue_type, q in queries:
            n = int(con.execute(q).fetchone()["c"] or 0)
            if n:
                _add_issue(issues, issue_type, severity="warning", details=f"{n} rows")

    if persist:
        with db(settings, write=True) as con:
            now = _now()
            con.execute("UPDATE integrity_issues SET status='stale' WHERE status='open'")
            for it in issues:
                con.execute(
                    """
                    INSERT INTO integrity_issues(issue_type,severity,image_id,path,details,status,created_at)
                    VALUES(?,?,?,?,?,'open',?)
                    """,
                    (it["issue_type"], it["severity"], it["image_id"], it["path"], it["details"], now),
                )
    by_type: dict[str, int] = {}
    for it in issues:
        by_type[it["issue_type"]] = by_type.get(it["issue_type"], 0) + 1
    return {"ok": not issues, "issues": issues, "counts": by_type, "total": len(issues)}


def repair(settings: dict, *, mark_missing_deleted: bool = True) -> dict[str, Any]:
    """Repair safe invariants. Does not delete actual media files."""
    with journal.operation(settings, "library_repair", target_type="library"):
        before = check(settings, persist=True)
        fixed = {"missing_marked_deleted": 0, "orphan_rows_removed": 0}
        with db(settings, write=True) as con:
            now = _now()
            if mark_missing_deleted:
                rows = con.execute("SELECT id, path FROM images WHERE deleted=0").fetchall()
                missing_ids = [int(r["id"]) for r in rows if r["path"] and not Path(r["path"]).exists()]
                if missing_ids:
                    for i in range(0, len(missing_ids), 500):
                        chunk = missing_ids[i:i+500]
                        ph = ",".join("?" for _ in chunk)
                        con.execute(f"UPDATE images SET deleted=1 WHERE id IN ({ph})", chunk)
                    fixed["missing_marked_deleted"] = len(missing_ids)
            # Manual orphan cleanup with rowcount accounting.
            statements = [
                "DELETE FROM image_tags WHERE image_id NOT IN (SELECT id FROM images)",
                "DELETE FROM image_tags WHERE tag_id NOT IN (SELECT id FROM tags)",
                "DELETE FROM image_sources WHERE image_id NOT IN (SELECT id FROM images)",
                "DELETE FROM image_sources WHERE source_id NOT IN (SELECT id FROM sources)",
                "DELETE FROM raw_metadata WHERE image_id NOT IN (SELECT id FROM images)",
            ]
            removed = 0
            for st in statements:
                cur = con.execute(st)
                removed += max(cur.rowcount or 0, 0)
            cleanup_orphans(con)
            fixed["orphan_rows_removed"] = removed
            con.execute("UPDATE integrity_issues SET status='repaired', repaired_at=? WHERE status='open'", (now,))
        after = check(settings, persist=True)
        return {"before": before, "after": after, "fixed": fixed}
