from __future__ import annotations

NAME = "performance_indexes_v150"


def _columns(con, name: str) -> set[str]:
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)).fetchone() is None:
            return set()
        return {str(r[1]) for r in con.execute(f"PRAGMA table_info({name})").fetchall()}
    except Exception:
        return set()


def _exec_if(con, table: str, required: tuple[str, ...], sql: str) -> None:
    cols = _columns(con, table)
    if cols and set(required).issubset(cols):
        con.execute(sql)


def apply(con):
    """Add covering indexes for source-scoped tags and large gallery queries.

    No schema rewrite, no data mutation.  Every index is guarded so ancient
    pre-migration databases can still be baselined and then upgraded safely.
    """
    _exec_if(con, "image_source_tags", ("source_id", "category", "tag_id", "image_id"), "CREATE INDEX IF NOT EXISTS idx_v150_ist_source_category_tag_image ON image_source_tags(source_id, category, tag_id, image_id)")
    _exec_if(con, "image_source_tags", ("image_id", "source_id", "category", "tag_id"), "CREATE INDEX IF NOT EXISTS idx_v150_ist_image_source_category_tag ON image_source_tags(image_id, source_id, category, tag_id)")
    _exec_if(con, "image_source_tags", ("tag_id", "source_id", "image_id"), "CREATE INDEX IF NOT EXISTS idx_v150_ist_tag_source_image ON image_source_tags(tag_id, source_id, image_id)")
    _exec_if(con, "image_tags", ("tag_id", "image_id"), "CREATE INDEX IF NOT EXISTS idx_v150_image_tags_tag_image ON image_tags(tag_id, image_id)")
    _exec_if(con, "image_tags", ("image_id", "tag_id"), "CREATE INDEX IF NOT EXISTS idx_v150_image_tags_image_tag ON image_tags(image_id, tag_id)")
    _exec_if(con, "images", ("deleted", "bucket", "id"), "CREATE INDEX IF NOT EXISTS idx_v150_images_live_bucket_id ON images(deleted, bucket, id)")
    _exec_if(con, "images", ("deleted", "lifecycle", "id"), "CREATE INDEX IF NOT EXISTS idx_v150_images_live_lifecycle_id ON images(deleted, lifecycle, id)")
    _exec_if(con, "images", ("deleted", "mtime_ns", "id"), "CREATE INDEX IF NOT EXISTS idx_v150_images_live_mtime_id ON images(deleted, mtime_ns DESC, id DESC)")
    _exec_if(con, "processed_files", ("original_path", "status", "media_path", "processed_at"), "CREATE INDEX IF NOT EXISTS idx_v150_processed_original_status_media ON processed_files(original_path, status, media_path, processed_at DESC)")
    _exec_if(con, "site_scan_status", ("scan_revision", "original_path", "site_key", "outcome"), "CREATE INDEX IF NOT EXISTS idx_v150_site_scan_cover ON site_scan_status(scan_revision, original_path, site_key, outcome)")
    _exec_if(con, "sources", ("host", "id"), "CREATE INDEX IF NOT EXISTS idx_v150_sources_host_id ON sources(host, id)")
    _exec_if(con, "tags", ("normalized_name", "id"), "CREATE INDEX IF NOT EXISTS idx_v150_tags_norm_id ON tags(normalized_name, id)")
