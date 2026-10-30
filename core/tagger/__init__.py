"""Tag helpers shared across the app (the parser itself lives in core/parser)."""
from core.tagger.compat import Tagger, get_session
from core.tagger.hashing import (
    VIDEO_EXTS, danbooru_pixel_hash, file_md5, file_phash, is_md5, phash_distance, video_frame_image,
)
from core.tagger.results import (
    append_error_log, cleanup_archived_result, cleanup_preview_cache, copy_result_files, has_copy_suffix,
    output_processed_status, promote_manual_match, result_bucket_name, result_output_base, result_paths_for,
    save_found_metadata,
)
from core.tagger.tag_groups import (
    TAG_CATEGORY_MAP, add_tags_to_groups, empty_tag_groups, filter_numeric_tags, group_from_tag_type,
    groups_to_tags, merge_tag_groups, normalize_tag, tag_is_numeric_symbol_only, unique_keep_order,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MEDIA_EXTS = IMAGE_EXTS | set(VIDEO_EXTS)
