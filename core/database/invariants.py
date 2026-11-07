"""Library invariant checker and small repair layer.

Checks are deliberately conservative: repair only removes orphan rows and marks
missing media as deleted.  It never deletes real media files.
"""
from __future__ import annotations

import time
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


def check(settings: dict, *, sample_limit: int | None = None, persist: bool = True, files: bool = True) -> dict[str, Any]:
    """Return invariant issues without modifying the library.  ``files`` — also whether each card's file is on the disk
    ((v5.44.118) «Обслуживание → Проверить целостность базы» checks the rows only: the files are «Диагностика →
    Проверить файлы», ``core/file_check.py``)."""
    issues: list[dict[str, Any]] = []
    with db(settings, readonly=True) as con:
        sql = "SELECT id, path, deleted FROM images WHERE deleted=0 ORDER BY id"
        args: list[Any] = []
        if sample_limit:
            sql += " LIMIT ?"; args.append(int(sample_limit))
        from core.storage_roots import probe_media_path
        root_cache = {}
        offline_roots: dict[tuple[str, str], dict[str, Any]] = {}
        for pos, r in enumerate(con.execute(sql, args).fetchall() if files else [], start=1):
            if (pos - 1) % 250 == 0:
                root_cache = {}
            path = str(r["path"] or "")
            probe = probe_media_path(settings, path, root_cache=root_cache)
            status = str(probe.get("status") or "path_error")
            if status == "missing":
                _add_issue(issues, "missing_media_file", severity="error", image_id=r["id"], path=path, details="DB image row points to a file that no longer exists")
            elif status == "offline_root":
                key = (str(probe.get("root_path") or ""), str(probe.get("root_status") or ""))
                entry = offline_roots.setdefault(key, {"count": 0, "sample": path})
                entry["count"] += 1
            elif status == "path_error":
                _add_issue(issues, "media_path_error", severity="error", image_id=r["id"], path=path, details=str(probe.get("error") or "path access failed"))
        for (root_path, root_status), entry in offline_roots.items():
            _add_issue(
                issues,
                "storage_root_unavailable",
                severity="warning",
                path=root_path,
                details=f"root_status={root_status}; affected_files={entry['count']}; sample={entry['sample']}",
            )

        queries = [
            ("orphan_image_tags_image", "SELECT COUNT(*) c FROM image_tags WHERE image_id NOT IN (SELECT id FROM images)"),
            ("orphan_image_tags_tag", "SELECT COUNT(*) c FROM image_tags WHERE tag_id NOT IN (SELECT id FROM tags)"),
            ("orphan_image_sources_image", "SELECT COUNT(*) c FROM image_sources WHERE image_id NOT IN (SELECT id FROM images)"),
            ("orphan_image_sources_source", "SELECT COUNT(*) c FROM image_sources WHERE source_id NOT IN (SELECT id FROM sources)"),
            ("orphan_raw_metadata", "SELECT COUNT(*) c FROM raw_metadata WHERE image_id NOT IN (SELECT id FROM images)"),
            ("empty_tags", "SELECT COUNT(*) c FROM tags WHERE id NOT IN (SELECT tag_id FROM image_tags)"
                           " AND id NOT IN (SELECT tag_id FROM image_source_tags)"),
            ("empty_sources", "SELECT COUNT(*) c FROM sources WHERE id NOT IN (SELECT source_id FROM image_sources)"
                              " AND id NOT IN (SELECT source_id FROM image_source_tags)"
                              " AND id NOT IN (SELECT source_id FROM image_source_roles)"),
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


def _moved_here(settings: dict, path: str) -> bool:
    """A card of the old place of a moved archive whose file is here, at the same place inside output/:
    moved, not gone («Переписать пути» moves the card to it)."""
    from core.library_lifecycle import _relative_after_output_branch
    from pathlib import Path
    from core.storage_roots import configured_output_root, path_is_within_root
    output = configured_output_root(settings)
    rel = _relative_after_output_branch(path)
    if not rel or path_is_within_root(path, output):
        return False
    return (Path(output) / rel).is_file()


def repair(settings: dict, *, mark_missing_deleted: bool = True) -> dict[str, Any]:
    """Repair safe invariants. Does not delete actual media files.

    The files are probed before the base is locked (minutes on a big library); cards are marked in short
    transactions, each card only if it is still live at the probed place and its file is still gone.  Left
    alone: cards whose file moved with the archive (``_moved_here``), and everything when most cards look
    gone (``storage.MASS_MISSING_SHARE``: an emptied or replaced folder, not losses)."""
    from .storage import MASS_MISSING_SHARE
    from core.storage_roots import probe_media_path
    with journal.operation(settings, "library_repair", target_type="library"):
        before = check(settings, persist=True, files=mark_missing_deleted)
        fixed = {"missing_marked_deleted": 0, "orphan_rows_removed": 0, "missing_refused": 0, "moved_left": 0}
        if mark_missing_deleted:
            with db(settings, readonly=True) as con:
                rows = [(int(r["id"]), str(r["path"] or "")) for r in
                        con.execute("SELECT id, path FROM images WHERE deleted=0 ORDER BY id").fetchall()]
            missing: list[tuple[int, str]] = []
            root_cache = {}
            for pos, (image_id, path) in enumerate(rows, start=1):
                if (pos - 1) % 250 == 0:
                    root_cache = {}
                if probe_media_path(settings, path, root_cache=root_cache).get("status") == "missing":
                    missing.append((image_id, path))
            moved = {image_id for image_id, path in missing if _moved_here(settings, path)}
            fixed["moved_left"] = len(moved)
            missing = [(image_id, path) for image_id, path in missing if image_id not in moved]
            if len(rows) >= 20 and len(missing) > MASS_MISSING_SHARE * len(rows):
                fixed["missing_refused"] = len(missing)
                missing = []
            fixed["missing_marked_deleted"] = mark_missing(settings, missing)
        with db(settings, write=True) as con:
            now = _now()
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
            try:
                from .storage import refresh_effective_tag_categories
                refresh_effective_tag_categories(con, None)
            except Exception:
                pass
            fixed["orphan_rows_removed"] = removed
            con.execute("UPDATE integrity_issues SET status='repaired', repaired_at=? WHERE status='open'", (now,))
        after = check(settings, persist=True, files=mark_missing_deleted)
        return {"before": before, "after": after, "fixed": fixed}


def mark_missing(settings: dict, items, *, hold=None) -> int:
    """The cards ``items`` — (card, the path it was probed at) — whose files are gone: into the trash as «файл пропал»
    (``lifecycle='missing'``, a line of ``delete_log``), in short transactions; each card only if it is still live at
    that path and its file is still gone.  ``hold(*paths)`` — a context around each portion (the import lock, the
    files' streams).  How many were marked.  (v5.44.118: :func:`repair` and «Починить файлы», ``core/file_check``.)"""
    from contextlib import nullcontext
    from core.storage_roots import probe_media_path
    marked = 0
    items = [(int(image_id), str(path or "")) for image_id, path in items or []]
    for i in range(0, len(items), 500):
        part = items[i:i + 500]
        with (hold(*[path for _id, path in part]) if hold is not None else nullcontext()):
            with db(settings, write=True) as con:
                now = _now()
                marks = ",".join("?" for _ in part)
                current = {int(r[0]): str(r[1] or "") for r in con.execute(
                    f"SELECT id, path FROM images WHERE deleted=0 AND id IN ({marks})", [x[0] for x in part])}
                ids = [image_id for image_id, path in part if current.get(image_id) == path
                       and probe_media_path(settings, path).get("status") == "missing"]
                if not ids:
                    continue
                # Not a zombie (deleted=1 and seen nowhere): the card shows in Trash as «файл пропал»,
                # can be purged there, and a re-parse that meets the same bytes gives it a file again.
                ph = ",".join("?" for _ in ids)
                con.execute(
                    f"""INSERT INTO delete_log(path,file_name,reason,tag_or_source,deleted_at)
                        SELECT path, file_name, 'missing_file', '', ? FROM images WHERE id IN ({ph})""",
                    [now] + ids)
                con.execute(
                    f"""UPDATE images SET deleted=1, lifecycle='missing', trashed_at=?, inbox_until=0,
                           original_media_path=CASE WHEN COALESCE(original_media_path,'')='' THEN path ELSE original_media_path END
                        WHERE id IN ({ph})""", [now] + ids)
                marked += len(ids)
    return marked
