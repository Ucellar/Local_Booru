from __future__ import annotations

"""Single high-level library service.

UI code should prefer this layer over direct repository calls for operations that
change library state.  The repository stays as the fast SQL/data-access layer;
this service adds journaling and invariant-aware orchestration.
"""

from core.database import repository
from core.database import journal


def page(settings, query="", source="all", bucket="all", limit=120, offset=0, order="path", enrich=True, extra_where=None, extra_params=None):
    rows = repository.search_items(settings, query=query, source=source, bucket=bucket, limit=limit, offset=offset, order=order, extra_where=extra_where, extra_params=extra_params)
    if enrich:
        rows = repository.enrich_items(settings, rows)
    return rows


def total(settings, query="", source="all", bucket="all", extra_where=None, extra_params=None):
    return repository.count_search_items(settings, query=query, source=source, bucket=bucket, extra_where=extra_where, extra_params=extra_params)


def counters(settings):
    return repository.counts(settings)


def delete_by_tag(settings, tag, scope="all", delete_files=True):
    with journal.operation(settings, "delete_by_tag", target_type="tag", target_id=str(tag), payload={"scope": scope, "delete_files": bool(delete_files)}):
        rows = repository.find_images_by_tag(settings, tag, scope=scope, limit=None)
        if delete_files:
            from core.library_lifecycle import move_to_trash
            return move_to_trash(settings, rows, reason="delete_by_tag", tag_or_source=tag, make_backup=True)
        return repository.delete_images(settings, rows, delete_files=False, reason="delete_by_tag", tag_or_source=tag)


def delete_by_source(settings, source, scope="all", delete_files=True):
    with journal.operation(settings, "delete_by_source", target_type="source", target_id=str(source), payload={"scope": scope, "delete_files": bool(delete_files)}):
        rows = repository.find_images_by_source(settings, source, scope=scope, limit=None)
        if delete_files:
            from core.library_lifecycle import move_to_trash
            return move_to_trash(settings, rows, reason="delete_by_source", tag_or_source=source, make_backup=True)
        return repository.delete_images(settings, rows, delete_files=False, reason="delete_by_source", tag_or_source=source)


def delete_by_buckets(settings, buckets, delete_files=True):
    bucket_list = [str(b) for b in (buckets or []) if str(b)]
    with journal.operation(settings, "delete_by_buckets", target_type="bucket", target_id=",".join(bucket_list), payload={"delete_files": bool(delete_files)}):
        rows = repository.find_images_by_buckets(settings, bucket_list, limit=None)
        if delete_files:
            from core.library_lifecycle import move_to_trash
            return move_to_trash(settings, rows, reason="delete_by_buckets", tag_or_source=",".join(bucket_list), make_backup=True)
        return repository.delete_images(settings, rows, delete_files=False, reason="delete_by_buckets", tag_or_source=",".join(bucket_list))


def check_integrity(settings, *, sample_limit=None):
    from core.database.invariants import check
    return check(settings, sample_limit=sample_limit, persist=True)


def repair_integrity(settings):
    from core.database.invariants import repair
    return repair(settings)


def unfinished_operations(settings, limit=100):
    return journal.unfinished(settings, limit=limit)


def trash_duplicate_paths(settings, deleted_paths, kept_path="", keep_path=""):
    if keep_path and not kept_path:
        kept_path = keep_path
    """Move duplicate choices to Trash; merge useful metadata into kept file first."""
    from core.database.connection import db
    from core.library_lifecycle import move_to_trash
    paths = [str(p) for p in (deleted_paths or []) if str(p)]
    if not paths:
        return {"deleted_files": 0, "errors": 0, "deleted_records": 0}
    with db(settings, readonly=True) as con:
        ph = ",".join("?" for _ in paths)
        rows = [dict(r) for r in con.execute(f"SELECT id,path,file_name,bucket,size_bytes FROM images WHERE path IN ({ph}) AND deleted=0", paths).fetchall()]
        keep = con.execute("SELECT id FROM images WHERE path=? AND deleted=0", (str(kept_path or ""),)).fetchone() if kept_path else None
    if keep and rows:
        keep_id = int(keep["id"])
        with db(settings, write=True) as con:
            for row in rows:
                source_id = int(row["id"])
                con.execute("INSERT OR IGNORE INTO image_tags(image_id,tag_id) SELECT ?,tag_id FROM image_tags WHERE image_id=?", (keep_id, source_id))
                con.execute("INSERT OR IGNORE INTO image_sources(image_id,source_id) SELECT ?,source_id FROM image_sources WHERE image_id=?", (keep_id, source_id))
                score = con.execute("SELECT rating,favorite FROM images WHERE id=?", (source_id,)).fetchone()
                if score:
                    con.execute("UPDATE images SET rating=MAX(COALESCE(rating,0),?), favorite=MAX(COALESCE(favorite,0),?) WHERE id=?", (int(score["rating"] or 0), int(score["favorite"] or 0), keep_id))
    return move_to_trash(settings, rows, reason="duplicate_delete", make_backup=True)


def remember_duplicate_relation(settings, infos, relation="not_duplicate") -> int:
    """Remember exact MD5 pairs only; never hides a future unknown similar file."""
    from core.database.connection import db
    md5s = sorted({str(i.get("md5") or "").lower() for i in (infos or []) if str(i.get("md5") or "").strip()})
    now = __import__("time").time()
    count = 0
    with db(settings, write=True) as con:
        for pos, a in enumerate(md5s):
            for b in md5s[pos + 1:]:
                con.execute("INSERT OR REPLACE INTO ignored_duplicate_pairs(md5_a,md5_b,relation,created_at) VALUES(?,?,?,?)", (a, b, relation, int(now)))
                count += 1
    return count


def ignored_duplicate_pair(settings, md5_a, md5_b) -> bool:
    a, b = sorted((str(md5_a or "").lower(), str(md5_b or "").lower()))
    if not a or not b or a == b:
        return False
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        return con.execute("SELECT 1 FROM ignored_duplicate_pairs WHERE md5_a=? AND md5_b=? LIMIT 1", (a, b)).fetchone() is not None


# UI-facing read operations: widgets depend on the service boundary, not SQL modules.
def search_items(settings, **kwargs):
    return repository.search_items(settings, **kwargs)

def count_search_items(settings, **kwargs):
    return repository.count_search_items(settings, **kwargs)

def enrich_items(settings, items):
    return repository.enrich_items(settings, items)

def candidate_tags(settings, scope="all"):
    return repository.candidate_tags(settings, scope)

def candidate_sources(settings, scope="all"):
    return repository.candidate_sources(settings, scope)

def counts(settings):
    return repository.counts(settings)

def source_unique_image_count(settings):
    return repository.source_unique_image_count(settings)

def tag_group_counts(settings):
    return repository.tag_group_counts(settings)

def find_images_by_tag(settings, tag, scope="all", limit=None):
    return repository.find_images_by_tag(settings, tag, scope=scope, limit=limit)

def find_images_by_source(settings, source, scope="all", limit=None):
    return repository.find_images_by_source(settings, source, scope=scope, limit=limit)


def cleanup_missing_records(settings):
    """Remove database rows whose managed media no longer exists."""
    from core.database.storage import cleanup_missing
    return cleanup_missing(settings)
