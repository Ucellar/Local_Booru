from __future__ import annotations

"""Read-only gallery data-source dispatcher.

The normal Local Booru repository remains authoritative for local media.  When
``gallery_data_source=corpus`` the same Gallery/Post UI reads Booru Corpus
Builder through core.external_corpus without importing/copying corpus bytes.
"""

from core.database import repository as _local
from core import external_corpus as _corpus


def backend(settings) -> str:
    value = str((settings or {}).get("gallery_data_source") or "local").strip().lower()
    return "corpus" if value == "corpus" else "local"


def is_corpus(settings) -> bool:
    return backend(settings) == "corpus"


def _repo(settings):
    return _corpus if is_corpus(settings) else _local


def search_items(settings, query="", source="all", bucket="all", limit=None, offset=0, order="path", extra_where=None, extra_params=None):
    return _repo(settings).search_items(
        settings, query=query, source=source, bucket=bucket, limit=limit,
        offset=offset, order=order, extra_where=extra_where, extra_params=extra_params,
    )


def count_search_items(settings, query="", source="all", bucket="all", extra_where=None, extra_params=None):
    return _repo(settings).count_search_items(
        settings, query=query, source=source, bucket=bucket,
        extra_where=extra_where, extra_params=extra_params,
    )


def enrich_items(settings, items, tag_source="all"):
    # External items can remain open in PostPage even if the Gallery selector
    # has already been switched back to Local Booru. Route by item provenance.
    rows = list(items or [])
    if rows and all(bool(row.get("_external_corpus")) for row in rows):
        return _corpus.enrich_items(settings, rows, tag_source=tag_source)
    return _repo(settings).enrich_items(settings, rows, tag_source=tag_source)


def candidate_tags(settings, scope="all"):
    return _repo(settings).candidate_tags(settings, scope)


def candidate_sources(settings, scope="all"):
    return _repo(settings).candidate_sources(settings, scope)


def counts(settings):
    return _repo(settings).counts(settings)


def source_unique_image_count(settings):
    return _repo(settings).source_unique_image_count(settings)


def tag_group_counts(settings, source="all"):
    return _repo(settings).tag_group_counts(settings, source=source)
