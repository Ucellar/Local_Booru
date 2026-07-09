"""Durable parser workflow state exposed to UI workers.

The parser page orchestrates a user-visible scan, but it should not know table
names or import storage primitives directly. This service is the boundary for
site-lane journals, reverse retry jobs and background tag enrichment.
"""
from core.database.storage import (
    processed_records_many,
    mark_site_scanned,
    site_scan_status_many,
    enqueue_tag_enrichment,
    seed_background_tag_enrichment,
    pending_tag_enrichments,
    complete_tag_enrichment,
    retry_tag_enrichment,
    enqueue_reverse_retry,
    remove_reverse_retry,
    pending_reverse_retry_paths,
    mark_reverse_branch_status,
    reverse_branch_status_many,
    record_task_event,
)

__all__ = [
    "processed_records_many", "mark_site_scanned", "site_scan_status_many",
    "enqueue_tag_enrichment", "seed_background_tag_enrichment", "pending_tag_enrichments",
    "complete_tag_enrichment", "retry_tag_enrichment", "enqueue_reverse_retry",
    "remove_reverse_retry", "pending_reverse_retry_paths",
    "mark_reverse_branch_status", "reverse_branch_status_many", "record_task_event",
]
