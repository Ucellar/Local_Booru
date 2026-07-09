"""Public compatibility facade for Local Booru tagger helpers.

Keep this package import light.  Importing ``core.tagger.hashing`` or
``core.tagger.tag_groups`` must not pull the full network-heavy engine and its
optional dependencies.  Engine-only names are loaded lazily via ``__getattr__``.
"""
from __future__ import annotations

from .tag_groups import (
    TAG_CATEGORY_MAP,
    tag_is_numeric_symbol_only,
    filter_numeric_tags,
    unique_keep_order,
    empty_tag_groups,
    normalize_tag,
    group_from_tag_type,
    add_tags_to_groups,
    merge_tag_groups,
    groups_to_tags,
)
from .hashing import is_md5, file_md5, file_phash, phash_distance, video_frame_image, VIDEO_EXTS
from .atf_html import atf_find_post_view_url_from_html, atf_parse_post_view_html
from .cookies_io import (
    cookie_file_for_url,
    load_netscape_cookie_file,
    load_txt_cookiejar_for_host,
    load_cookie_bundle_for_host,
    load_system_cookiejar_for_host,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS

_ENGINE_NAMES = {
    "Tagger", "DEFAULT_SETTINGS", "load_settings", "save_settings", "has_copy_suffix",
    "get_session", "safe_json_response", "debug_enabled", "result_output_base", "result_bucket_name",
    "result_paths_for", "output_processed_status", "copy_result_files", "cleanup_archived_result",
    "save_found_metadata", "promote_manual_match", "append_error_log", "do_browser_login",
    "extract_r34_urls_from_text", "cleanup_preview_cache",
}

__all__ = [
    "Tagger", "DEFAULT_SETTINGS", "IMAGE_EXTS", "VIDEO_EXTS", "MEDIA_EXTS", "has_copy_suffix",
    "TAG_CATEGORY_MAP", "load_settings", "save_settings", "is_md5", "file_md5", "file_phash",
    "phash_distance", "tag_is_numeric_symbol_only", "atf_find_post_view_url_from_html",
    "atf_parse_post_view_html", "filter_numeric_tags", "video_frame_image", "unique_keep_order",
    "empty_tag_groups", "normalize_tag", "group_from_tag_type", "add_tags_to_groups",
    "merge_tag_groups", "groups_to_tags", "cookie_file_for_url", "load_netscape_cookie_file",
    "load_txt_cookiejar_for_host", "load_cookie_bundle_for_host", "load_system_cookiejar_for_host",
    "get_session", "safe_json_response", "debug_enabled", "result_output_base", "result_bucket_name",
    "result_paths_for", "output_processed_status", "copy_result_files", "cleanup_archived_result",
    "save_found_metadata", "promote_manual_match", "append_error_log", "do_browser_login",
    "extract_r34_urls_from_text", "cleanup_preview_cache",
]


def __getattr__(name: str):
    if name in _ENGINE_NAMES:
        from . import engine as _engine
        value = getattr(_engine, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'core.tagger' has no attribute {name!r}")
