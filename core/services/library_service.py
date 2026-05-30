from __future__ import annotations

"""Single high-level library service.

UI code should prefer this layer over direct repository calls for operations that
change library state.  The repository stays as the fast SQL/data-access layer;
this service adds journaling and invariant-aware orchestration.
"""

from core.database import repository
from core.database import journal


def page(settings, query="", source="all", bucket="all", limit=120, offset=0, order="path", enrich=True):
    rows = repository.search_items(settings, query=query, source=source, bucket=bucket, limit=limit, offset=offset, order=order)
    if enrich:
        rows = repository.enrich_items(settings, rows)
    return rows


def total(settings, query="", source="all", bucket="all"):
    return repository.count_search_items(settings, query=query, source=source, bucket=bucket)


def counters(settings):
    return repository.counts(settings)


def delete_by_tag(settings, tag, scope="all", delete_files=True):
    with journal.operation(settings, "delete_by_tag", target_type="tag", target_id=str(tag), payload={"scope": scope, "delete_files": bool(delete_files)}):
        rows = repository.find_images_by_tag(settings, tag, scope=scope, limit=None)
        res = repository.delete_images(settings, rows, delete_files=delete_files, reason="delete_by_tag", tag_or_source=tag)
        return res


def delete_by_source(settings, source, scope="all", delete_files=True):
    with journal.operation(settings, "delete_by_source", target_type="source", target_id=str(source), payload={"scope": scope, "delete_files": bool(delete_files)}):
        rows = repository.find_images_by_source(settings, source, scope=scope, limit=None)
        res = repository.delete_images(settings, rows, delete_files=delete_files, reason="delete_by_source", tag_or_source=source)
        return res


def delete_by_buckets(settings, buckets, delete_files=True):
    bucket_list = [str(b) for b in (buckets or []) if str(b)]
    with journal.operation(settings, "delete_by_buckets", target_type="bucket", target_id=",".join(bucket_list), payload={"delete_files": bool(delete_files)}):
        rows = repository.find_images_by_buckets(settings, bucket_list, limit=None)
        return repository.delete_images(settings, rows, delete_files=delete_files, reason="delete_by_buckets", tag_or_source=",".join(bucket_list))


def check_integrity(settings, *, sample_limit=None):
    from core.database.invariants import check
    return check(settings, sample_limit=sample_limit, persist=True)


def repair_integrity(settings):
    from core.database.invariants import repair
    return repair(settings)


def unfinished_operations(settings, limit=100):
    return journal.unfinished(settings, limit=limit)
