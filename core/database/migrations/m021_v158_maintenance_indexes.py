from __future__ import annotations

NAME = "v158_maintenance_indexes_wal_tag_category_cache"


def _has_table(con, table: str) -> bool:
    try:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)).fetchone() is not None
    except Exception:
        return False


def _columns(con, table: str) -> set[str]:
    if not _has_table(con, table):
        return set()
    try:
        return {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _exec_if(con, table: str, required: tuple[str, ...], sql: str) -> None:
    cols = _columns(con, table)
    if cols and set(required).issubset(cols):
        con.execute(sql)


def apply(con) -> None:
    """v158: harden large-library performance without rewriting user data."""
    con.executescript("""
    CREATE TABLE IF NOT EXISTS tag_category_cache (
        site_key TEXT NOT NULL,
        tag_name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'general',
        source_method TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(site_key, tag_name)
    );
    CREATE INDEX IF NOT EXISTS idx_tag_category_cache_site_category
        ON tag_category_cache(site_key, category, tag_name);

    CREATE TABLE IF NOT EXISTS gallery_tag_counts_cache (
        cache_key TEXT NOT NULL,
        source_filter TEXT NOT NULL DEFAULT 'all',
        bucket TEXT NOT NULL DEFAULT 'all',
        category TEXT NOT NULL DEFAULT 'general',
        tag_id INTEGER NOT NULL DEFAULT 0,
        tag_name TEXT NOT NULL DEFAULT '',
        count INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(cache_key, category, tag_id)
    );
    CREATE INDEX IF NOT EXISTS idx_gallery_tag_counts_cache_lookup
        ON gallery_tag_counts_cache(cache_key, category, count DESC, tag_name);
    """)

    # Images: exact duplicates and pHash lookup use different left-most columns.
    _exec_if(con, "images", ("hash_md5",), "CREATE INDEX IF NOT EXISTS idx_v158_images_hash_md5 ON images(hash_md5) WHERE hash_md5 IS NOT NULL")
    _exec_if(con, "images", ("hash_phash",), "CREATE INDEX IF NOT EXISTS idx_v158_images_hash_phash ON images(hash_phash) WHERE hash_phash IS NOT NULL")
    _exec_if(con, "images", ("deleted", "bucket", "hash_md5"), "CREATE INDEX IF NOT EXISTS idx_v158_images_live_bucket_md5 ON images(deleted, bucket, hash_md5)")
    _exec_if(con, "images", ("deleted", "mtime_ns", "id"), "CREATE INDEX IF NOT EXISTS idx_v158_images_live_mtime_id ON images(deleted, mtime_ns DESC, id DESC)")

    # Source-scoped tags: cover post page, gallery search, source filter and facet counts.
    _exec_if(con, "image_source_tags", ("image_id", "source_id"), "CREATE INDEX IF NOT EXISTS idx_v158_ist_image_source ON image_source_tags(image_id, source_id)")
    _exec_if(con, "image_source_tags", ("tag_id", "image_id"), "CREATE INDEX IF NOT EXISTS idx_v158_ist_tag_image ON image_source_tags(tag_id, image_id)")
    _exec_if(con, "image_source_tags", ("source_id", "tag_id", "image_id"), "CREATE INDEX IF NOT EXISTS idx_v158_ist_source_tag_image ON image_source_tags(source_id, tag_id, image_id)")
    _exec_if(con, "image_source_tags", ("image_id", "tag_id"), "CREATE INDEX IF NOT EXISTS idx_v158_ist_image_tag ON image_source_tags(image_id, tag_id)")
    _exec_if(con, "image_source_tags", ("category", "tag_id", "image_id"), "CREATE INDEX IF NOT EXISTS idx_v158_ist_category_tag_image ON image_source_tags(category, tag_id, image_id)")

    _exec_if(con, "image_tags", ("tag_id", "image_id"), "CREATE INDEX IF NOT EXISTS idx_v158_image_tags_tag_image ON image_tags(tag_id, image_id)")
    _exec_if(con, "image_tags", ("image_id", "tag_id"), "CREATE INDEX IF NOT EXISTS idx_v158_image_tags_image_tag ON image_tags(image_id, tag_id)")

    _exec_if(con, "image_sources", ("image_id", "source_id"), "CREATE INDEX IF NOT EXISTS idx_v158_image_sources_image_source ON image_sources(image_id, source_id)")
    _exec_if(con, "image_sources", ("source_id", "image_id"), "CREATE INDEX IF NOT EXISTS idx_v158_image_sources_source_image ON image_sources(source_id, image_id)")
    _exec_if(con, "sources", ("host", "id"), "CREATE INDEX IF NOT EXISTS idx_v158_sources_host_id ON sources(host, id)")
    _exec_if(con, "sources", ("url",), "CREATE INDEX IF NOT EXISTS idx_v158_sources_url_nocase ON sources(url COLLATE NOCASE)")
    _exec_if(con, "tags", ("normalized_name", "id"), "CREATE INDEX IF NOT EXISTS idx_v158_tags_norm_id ON tags(normalized_name, id)")

    _exec_if(con, "site_scan_status", ("scan_revision", "original_path", "site_key", "outcome"), "CREATE INDEX IF NOT EXISTS idx_v158_site_scan_cover ON site_scan_status(scan_revision, original_path, site_key, outcome)")
    _exec_if(con, "site_scan_status", ("site_key", "outcome", "checked_at"), "CREATE INDEX IF NOT EXISTS idx_v158_site_scan_site_outcome_time ON site_scan_status(site_key, outcome, checked_at)")
    _exec_if(con, "site_scan_status", ("outcome", "checked_at"), "CREATE INDEX IF NOT EXISTS idx_v158_site_scan_outcome_time ON site_scan_status(outcome, checked_at)")

    _exec_if(con, "reverse_retry_queue", ("service", "retry_after", "queued_at"), "CREATE INDEX IF NOT EXISTS idx_v158_reverse_retry_due ON reverse_retry_queue(service, retry_after, queued_at)")
    _exec_if(con, "tag_enrichment_queue", ("job_key", "status", "retry_after", "queued_at"), "CREATE INDEX IF NOT EXISTS idx_v158_tag_enrichment_due ON tag_enrichment_queue(job_key, status, retry_after, queued_at)")
    _exec_if(con, "tag_enrichment_queue", ("status", "job_key"), "CREATE INDEX IF NOT EXISTS idx_v158_tag_enrichment_status_key ON tag_enrichment_queue(status, job_key)")

    _exec_if(con, "no_match_items", ("active", "reason", "updated_at"), "CREATE INDEX IF NOT EXISTS idx_v158_no_match_reason_active ON no_match_items(active, reason, updated_at)")
    _exec_if(con, "no_match_items", ("active", "source_host", "updated_at"), "CREATE INDEX IF NOT EXISTS idx_v158_no_match_source_host ON no_match_items(active, source_host, updated_at)")

    # Keep future WAL growth bounded.  The limit is best-effort; TRUNCATE checkpoints
    # at STOP/DONE/exit still do the real shrink.
    try:
        con.execute("PRAGMA journal_size_limit=536870912")
    except Exception:
        pass
