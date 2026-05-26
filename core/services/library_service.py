from __future__ import annotations

from core.database import repository


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
    rows = repository.find_images_by_tag(settings, tag, scope=scope, limit=None)
    return repository.delete_images(settings, rows, delete_files=delete_files, reason="delete_by_tag", tag_or_source=tag)


def delete_by_source(settings, source, scope="all", delete_files=True):
    rows = repository.find_images_by_source(settings, source, scope=scope, limit=None)
    return repository.delete_images(settings, rows, delete_files=delete_files, reason="delete_by_source", tag_or_source=source)
