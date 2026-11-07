import json
from pathlib import Path
from core.paths import (
    SETTINGS_FILE, BOOTSTRAP_SETTINGS_FILE, DATA_DIR, USING_SEPARATE_STORAGE, WORKSPACE_UNAVAILABLE,
    prepare_separate_storage, write_workspace_pointer, next_archive, choose_next_archive,
    activate_portable_workspace, normalize_archive_settings_root, OUTPUT_FOLDER_NAME,
)
import logging
import time

_LOG = logging.getLogger("local_booru.settings")


# All known sites grouped by engine type
# Each site: {enabled, type, login, api_key, user_id, login_url, notes}
SITES_BY_ENGINE = {
    "Danbooru": {
        "danbooru.donmai.us":       {"enabled": False, "type": "danbooru",      "login": "", "api_key": "", "user_id": "", "login_url": "https://danbooru.donmai.us",       "notes": "Лучшие теги. API: Basic Auth + честный User-Agent; cookies только для Cloudflare/browser"},
        "booru.allthefallen.moe":   {"enabled": True,  "type": "danbooru",      "login": "", "api_key": "", "user_id": "", "login_url": "https://booru.allthefallen.moe",   "notes": "ATF — Danbooru-compatible API: /posts.json, Basic Auth + честный User-Agent, PoW/cookies только для проверки"},
        "lolibooru.moe":            {"enabled": False,  "type": "danbooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://lolibooru.moe",             "notes": "Danbooru движок"},
        "aibooru.online":           {"enabled": False,  "type": "danbooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://aibooru.online",            "notes": "AI-арт, Danbooru движок"},
    },
    "Gelbooru": {
        "gelbooru.com":             {"enabled": True,   "type": "gelbooru_html","login": "", "api_key": "", "user_id": "", "login_url": "https://gelbooru.com",              "notes": "Официальный DAPI JSON, MD5 через tags=md5:"},
        "rule34.xxx":               {"enabled": True,   "type": "rule34xxx",    "login": "", "api_key": "", "user_id": "", "login_url": "https://rule34.xxx/index.php?page=account&s=options", "notes": "Официальный DAPI: api.rule34.xxx, user_id + api_key, json=1, лимит до 1000"},
        "realbooru.com":            {"enabled": False,  "type": "gelbooru_html","login": "", "api_key": "", "user_id": "", "login_url": "https://realbooru.com",             "notes": "Реальные фото, Gelbooru движок"},
        "xbooru.com":               {"enabled": False,  "type": "gelbooru_html","login": "", "api_key": "", "user_id": "", "login_url": "https://xbooru.com",               "notes": "Gelbooru движок"},
        "hypnohub.net":             {"enabled": False,  "type": "gelbooru_html","login": "", "api_key": "", "user_id": "", "login_url": "https://hypnohub.net",              "notes": "Gelbooru/DAPI API"},
        "tbib.org":                 {"enabled": False,  "type": "gelbooru_html","login": "", "api_key": "", "user_id": "", "login_url": "https://tbib.org",                 "notes": "The Big ImageBoard, Gelbooru движок"},
        "safebooru.org":            {"enabled": False,  "type": "gelbooru_html","login": "", "api_key": "", "user_id": "", "login_url": "https://safebooru.org",            "notes": "SFW, Gelbooru движок"},
    },
    "Moebooru": {
        "konachan.com":             {"enabled": False,  "type": "moebooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://konachan.com",             "notes": "Аниме обои высокого качества"},
        "konachan.net":             {"enabled": False,  "type": "moebooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://konachan.net",             "notes": "SFW версия konachan.com"},
        "yande.re":                 {"enabled": False,  "type": "moebooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://yande.re",                 "notes": "Высокое разрешение, аниме"},
        "rule34.us":                {"enabled": True,   "type": "rule34us",     "login": "", "api_key": "", "user_id": "", "login_url": "https://rule34.us",               "notes": "HTML-поиск с проверкой MD5; подтверждённого API нет"},
    },
    "e621": {
        "e621.net":                 {"enabled": True,   "type": "e621",         "login": "", "api_key": "", "user_id": "", "login_url": "https://e621.net",                 "notes": "Furry. Basic Auth (login:api_key)"},
        "e926.net":                 {"enabled": False,  "type": "e621",         "login": "", "api_key": "", "user_id": "", "login_url": "https://e926.net",                 "notes": "SFW версия e621"},
    },
}

# Flat dict of all sites (for backward compat + engine lookup)
ALL_KNOWN_SITES: dict = {}
for _eng, _sites in SITES_BY_ENGINE.items():
    for _domain, _cfg in _sites.items():
        ALL_KNOWN_SITES[_domain] = {**_cfg, "engine": _eng}

DEFAULT_SITES = {
    d: {k: v for k, v in cfg.items() if k != "notes" and k != "engine"}
    for d, cfg in ALL_KNOWN_SITES.items()
    if cfg.get("enabled", False)
}

DEFAULT_SETTINGS = {
    "root": "C:/Local_Booru_Input",
    "theme_title": "Local Booru",
    "logo_path": "",
    "logo_fit": "crop",
    "appearance": "dark",
    "show_search_preview": True,
    # v339: parser can produce tens of thousands of low-level lines per run.
    # Keep UI history compact; full forensic logs still go through the file logger.
    "max_console_lines": 1000,
    "tagger_ui_log_throttle": True,
    "tagger_ui_log_summary_interval_seconds": 5.0,
    "tagger_ui_log_flush_interval_ms": 120,
    "tagger_ui_log_flush_batch": 250,
    "tagger_ui_log_flush_budget_ms": 6.0,
    "tagger_ui_log_queue_cap": 5000,
    "parser_orchestration_log_enabled": True,
    "parser_orchestration_log_queue_size": 20000,
    "parser_orchestration_log_max_mb": 20,
    "parser_orchestration_log_backups": 5,
    "parser_orchestration_log_close_timeout_seconds": 5,
    "background_task_waiting_stall_minutes": 60,
    "tagger_ui_site_activity_flush_interval_ms": 200,
    "tagger_ui_activity_thumb_interval_seconds": 1.0,
    "tagger_ui_saucenao_refresh_debounce_ms": 750,
    # v375: emergency RAM guard for mass parser runs.  If a leak/regression
    # starts eating the system, stop parser queues before Windows falls over.
    "tagger_ram_safe_mode": True,
    "tagger_ram_guard_enabled": True,
    "tagger_ram_guard_check_interval_seconds": 5.0,
    "tagger_ram_soft_limit_mb": 12288,
    "tagger_ram_min_free_mb": 3072,
    "tagger_ram_system_load_limit_percent": 94,
    "tagger_ram_safe_console_lines": 120,
    "tagger_ram_safe_log_queue_cap": 300,
    "tagger_ram_safe_log_flush_batch": 40,
    "tagger_ram_compact_match_logs": True,
    "tagger_ram_safe_hard_limit_mb": 12288,
    "tagger_ram_trim_at_mb": 8192,
    "tagger_ram_safe_disable_activity_thumbs": False,
    "tagger_ram_safe_disable_parser_preview": False,
    "gallery_block_heavy_tasks_while_parser": True,
    # v471: Gallery can switch to a live Booru Corpus Builder database without
    # importing or duplicating canonical files. Corpus access is always read-only.
    "gallery_data_source": "local",
    "booru_corpus_path": "",
    "enable_error_console": True,
    "developer_preload_md5_index": True,
    "grabber_exact_md5_fanout": True,
    "grabber_visual_hash_merge": True,
    "grabber_visual_hash_distance": 4,
    "grabber_preview_prefetch_originals": False,
    "grabber_preview_prefetch_protected_originals": False,
    "grabber_preview_stream_cards": False,
    "grabber_include_protected_sites": False,
    "grabber_preview_threads": 2,
    # Persistent disk metadata cache for parser/tagger exact-MD5 shortcut.
    # Legacy key kept because existing code/settings already use it.
    "developer_grabber_md5_cache_enabled": True,
    "grabber_disk_metadata_cache_enabled": True,
    # Parser/tagger exact lookup must trust real file bytes, not filenames.
    # The byte MD5 is cached by path+size+mtime under settings/cache.
    "parser_real_file_hash_cache_enabled": True,
    "parser_trust_filename_md5_only_if_matches_real": True,
    # New parser (core/parser). See core/parser/engine.py for the algorithm.
    "parser_workers": 3,                          # files processed at the same time
    "parser_visual_max_distance": 10,             # pHash distance for "same picture"
    "parser_local_visual_match": True,            # reuse tags of a visually identical gallery image
    "parser_local_visual_distance": 4,
    "parser_trust_unverified_filename_md5": True, # booru MD5 in the file name, preview unavailable
    "parser_tag_categories_enabled": True,        # Gelbooru-like sites: ask the tag API for categories
    "parser_tag_category_single_lookups": 30,
    "e621_iqdb_min_similarity": 80.0,
    # Developer thread menu: global ceiling and per-service local/offline queues.
    # Network site lanes still use their own rate limits and are not counted here.
    "local_total_workers": 8,
    "local_scan_workers": 2,
    "local_hash_workers": 4,
    "local_image_workers": 4,
    "local_video_workers": 2,
    "local_db_read_workers": 2,
    "local_preflight_enabled": True,
    "local_preflight_phash": True,
    # Decode guards for pHash/video-frame preflight. Header dimensions are
    # checked before full Pillow decode; huge GIFs use their first frame.
    "local_preflight_max_file_mb": 64,
    "local_preflight_max_megapixels": 64,
    "local_preflight_max_gif_frames": 300,
    # Parser performance profile is resolved at runtime from detected RAM.
    # auto: <=18GB low_memory, <=36GB balanced, otherwise performance.
    "tagger_performance_profile": "auto",
    # Network-efficiency controls. Full provenance preserves every enabled
    # source bundle; fast mode lets an authoritative Danbooru/e621 hit stop
    # later MD5 requests for that file while keeping skipped rows replayable.
    "tagger_provenance_mode": "full",
    "tagger_fast_authoritative_hosts": ["danbooru.donmai.us", "e621.net"],
    "tagger_md5_batch_enabled": True,
    "tagger_md5_batch_size": 50,
    # v5.2 parser: batched MD5 (core/parser/batch.py), reverse-search workers, retry of deferred files
    # in the same run, «добор» of new sites for processed files (site journal).
    "parser_md5_batch_enabled": True,
    "parser_md5_batch_size": 40,
    "parser_reverse_workers": 2,
    "parser_retry_deferred_minutes": 15,
    "parser_recheck_new_sites": False,
    "parser_recheck_nomatch_only": False,       # «добор» only for the files in Брак (the found ones are left alone)
    # rule34.xxx exact-MD5 foreground lane uses the official DAPI only by
    # default. The much slower HTML locator remains opt-in; image-key/SHA1
    # already run in their isolated side queue.
    "rule34_foreground_html_locator_enabled": False,
    "tagger_site_journal_batch_size": 100,
    "tagger_site_journal_flush_seconds": 30.0,
    # Reverse upload optimization: perceptual services receive a cached compact
    # JPEG instead of the original multi-megabyte PNG/GIF/frame.
    "reverse_upload_downscale": True,
    "reverse_upload_max_edge": 512,
    "reverse_upload_jpeg_quality": 85,
    "reverse_upload_reuse_small_jpeg_kb": 256,
    "reverse_upload_cache_max_files": 5000,
    "reverse_upload_cache_max_age_days": 30,
    "tagger_local_phash_match_enabled": True,
    "tagger_local_phash_max_distance": 4,
    "tagger_local_phash_min_similarity": 93.75,
    "tagger_reverse_retry_drain_enabled": True,
    # Keep a parser run alive while relevant durable retries are cooling down;
    # the nearest service branch is resumed automatically at retry_after.
    "tagger_reverse_retry_wait_enabled": True,
    "tagger_reverse_retry_drain_interval_seconds": 600,
    "tagger_reverse_retry_drain_limit": 200,
    "tagger_reverse_retry_scan_page_size": 500,
    # Safety net when a due deadline wakes the scheduler but no row can be
    # admitted. Prevents a zero-delay WAKE/drain CPU spin and log flood.
    "tagger_reverse_retry_empty_wake_backoff_seconds": 5,
    # SauceNAO searches every current/future index by default (`db=999`).
    # Advanced users may explicitly narrow the request with `saucenao_dbs`
    # (serialized as the documented `dbs[]`) or `saucenao_dbmask`.
    "saucenao_dbs": [],
    "saucenao_dbmask": "",
    "saucenao_db": 999,
    "saucenao_dedupe": 1,
    "saucenao_hide": 0,
    "tagger_reverse_staged_scheduler_enabled": True,
    "tagger_reverse_admit_window_files": 64,
    "local_preflight_low_memory_skip_threshold": 5000,
    "tagger_db_startup_lock_wait_seconds": 90,
    "local_tagger_workers": 4,
    "local_thumb_workers": 4,
    "local_thumb_pregen_workers": 1,
    "local_background_workers": 4,
    "maintenance_idle_seconds": 5,
    # Optional v439 backend for large disposable derived caches. main.db remains authoritative.
    "derived_cache_external_enabled": False,
    "visual_nomatch_classify_enabled": True,
    # Брак, «фото или рисунок» (core/visual_models.py): auto | anime_real_fast | anime_real_precise |
    # anime_class_fast | both_fast | clip | heuristic; «auto» — the fast deepghs model when onnxruntime is installed.
    "visual_nomatch_model": "auto",
    "visual_nomatch_min_confidence": 75,
    "visual_nomatch_threads": 2,
    "visual_nomatch_backend": "clip_local",
    "visual_nomatch_clip_model_dir": "",
    "visual_nomatch_auto_download_model": True,
    "visual_nomatch_device": "auto",
    "visual_nomatch_ai_min_confidence": 0.56,
    "visual_nomatch_ai_min_margin": 0.08,
    "visual_nomatch_ai_fallback_heuristic": False,
    "visual_nomatch_real_threshold": 0.34,
    "visual_nomatch_workers": 2,
    "rule34_sha1_async_locator_enabled": True,
    "rule34_sha1_async_locator_workers": 4,
    "rule34_sha1_branch_cooldown_seconds": 900,
    "rule34_sha1_network_defer_seconds": 300,
    "rule34_variant_locator_side_queue_enabled": True,
    "rule34_image_key_locator_mode": "hotlink_only",
    "rule34_image_key_hotlink_redirect_enabled": True,
    "rule34_image_key_hotlink_extensions": "png",
    "rule34_image_key_hotlink_request_timeout": 4.0,
    # v453: Cloudflare/browser work is delegated to the user's ordinary browser
    # through the localhost companion extension. Local Booru owns no Chromium.
    "external_browser_resolver_enabled": True,
    "external_browser_mode": "default",
    "external_browser_executable": "",
    "external_browser_arguments": "",
    "external_browser_auto_open": True,
    "external_browser_resolver_timeout_seconds": 120,
    "external_browser_retry_seconds": 300,
    "external_browser_upload_max_mb": 12,
    "parser_disable_companion_browser_fetch": False,
    "rule34_image_key_bucket_probe_enabled": False,
    "rule34_image_key_bucket_probe_sequence": "",
    "rule34_image_key_bucket_probe_max": 9999,
    "rule34_image_key_bucket_probe_step": 100,
    "rule34_image_key_bucket_request_timeout": 3.0,
    "rule34_image_key_bucket_total_timeout": 90.0,
    "atf_exact_md5_enabled": True,
    "atf_exact_md5_accept_missing_md5_from_api": True,
    "atf_pixel_hash_after_exact_md5_miss": False,
    "atf_pixel_hash_after_reverse_miss": True,
    "atf_pixel_hash_locator_enabled": True,
    "atf_pixel_hash_workers": 2,
    "atf_pixel_hash_delay_ms": 1100,
    "atf_pixel_hash_max_assets": 5,
    # RAM-only working cache for the online grabber UI. Does not affect parser.
    "grabber_metadata_ram_cache_mb": 128,
    "grabber_image_ram_cache_mb": 256,
    "grabber_open_quality": "medium_50",
    "developer_filesystem_duplicate_fallback": False,
    "workspace": "tagger",
    "gallery_filter": "all",
    "gallery_source": "output",
    "output_layout": "split_found_nomatch",
    "enable_google_fallback": True,
    "google_fallback_mode": "br34_manual",
    "output_dir": "",
    "google_fallback_enabled": False,
    "columns": 8,
    "grabber_columns": 8,
    "grabber_cache_limit_mb": 200,
    "grabber_rows": 4,
    "rows_per_page": 4,
    "card_height": 220,
    "items_per_page": 32,
    "grabber_preview_columns": 8,
    "grabber_preview_rows": 4,
    "grabber_preview_limit": 32,
    "grabber_preview_prefetch_pages": 4,
    "grabber_preview_sites": "rule34.xxx, gelbooru.com, e621.net, booru.allthefallen.moe, danbooru.donmai.us",
    "grabber_preview_hide_existing": True,
    "grabber_preview_merge_existing_sources": True,
    "grabber_preview_manual_exclusions": True,
    # Browser extension companion: localhost-only API used by the extension in
    # the user's normal browser to hide already-downloaded cards visually. Parser/tagger are
    # not affected by this API or its manual hide list.
    # Per-process master gate selected at startup. This is not a cloud or
    # account preference: it controls whether this Local Booru process may
    # fetch any new media bytes. Metadata-only operations remain available.
    "session_media_downloads_prompt": False,    # (v5.28) the question at every start — off: asked in «Настройки»
    "session_media_downloads_default": True,
    "download_policy_create_intents": True,
    "background_lane_page_size": 500,
    "background_lane_empty_backoff_seconds": 5,
    "background_lane_stale_running_seconds": 3600,
    "tag_alias_sync_page_delay_seconds": 1.10,
    "tag_alias_sync_max_pages_per_run": 0,
    "upgrade_verification_max_samples": 7,
    "upgrade_verification_ram_reserve_mb": 2048,
    "upgrade_verification_job_cap_mb": 1024,
    "browser_companion_api_enabled": True,
    "browser_companion_api_host": "127.0.0.1",
    "browser_companion_api_port": 47734,
    "browser_companion_use_grabber_hides": True,
    "browser_companion_max_batch": 250,
    "browser_companion_pairing_required": True,
    "browser_companion_pairing_ttl_seconds": 600,
    "browser_companion_event_wait_seconds": 20,
    "browser_companion_profiles_enabled": True,
    "browser_companion_cookie_handoff_enabled": True,
    "automatic_quality_upgrade_enabled": True,   # a better copy takes a card's place: the extension's, and (v5.44.13) a
                                                 # post's original for a site's reduced copy (core/reduced_copies.py)
    "automatic_family_detection_enabled": True,
    "automatic_family_completion_enabled": True,
    "family_completion_max_items": 100,
    "nomatch_local_models_enabled": False,
    "nomatch_compact_classifier_onnx": "",
    "nomatch_text_detector_onnx": "",
    "nomatch_compact_input_size": 224,
    "nomatch_text_input_size": 640,
    "nomatch_compact_min_confidence": 0.60,
    "nomatch_text_min_confidence": 0.55,
    "grabber_subscriptions_blocklist": "",
    "grabber_tag_download_limit": 500,
    "skip_existing": True,
    "tag_only_untagged": True,
    "retry_nomatch": False,
    "delay_seconds": 0.0,
    # Per-site conveyor: every enabled site advances independently through the
    # file queue while one global result writer serializes SQLite commits.
    # Low-power mode keeps this journal/conveyor but reduces its active window to one file.
    # Flat-tag sources collect quickly; categories are recovered later in a durable low-priority pass.
    "tagger_background_tag_groups": True,
    "tagger_background_rule34_categories": True,  # legacy alias
    "tagger_category_overlay_429_cooldown_seconds": 900,
    "tagger_gelbooru_category_single_tag_fallback": True,
    "tagger_low_power_mode": False,
    "tagger_site_interval_seconds": 1.10,
    "tagger_conveyor_window": 100,
    # v373: true per-site cursors: every site scans its own pending files and never waits for ATF/other sites.
    "tagger_true_per_site_cursors": True,
    # v374: reverse branches also have independent queues after MD5-all-sites-miss.
    "tagger_true_per_reverse_branch_queues": True,
    "tagger_reverse_branch_no_nomatch_until_all_done": True,
    # v371: keep fast exact-MD5 lanes fed even when ATF/reverse/CF sites are slow.
    "tagger_md5_lane_min_state_limit": 4096,
    "tagger_force_atf_md5_lane": True,
    "tagger_site_activity_column_widths": [150, 165, 250, 275],
    "request_timeout_seconds": 30,
    "request_connect_timeout_seconds": 10,
    "request_read_timeout_seconds": 30,
    "network_retry_attempts": 3,
    "network_retry_base_delay_seconds": 1.0,
    "network_retry_max_delay_seconds": 4.0,
    # A reverse IQDB hit must not monopolize the single e621 branch while
    # fetching its post JSON. The endpoint is only metadata verification; a
    # failed bounded fetch is deferred through the durable reverse retry queue.
    "e621_iqdb_post_timeout_seconds": 15,
    "e621_iqdb_post_max_attempts": 1,
    "network_retry_delay_seconds": 10,
    "sqlite_passive_checkpoint_every": 500,
    # Supplement the parser's every-N-site-check PASSIVE checkpoint for all
    # other writers. It runs only while idle and only above the WAL threshold.
    "sqlite_idle_checkpoint_enabled": True,
    "sqlite_idle_checkpoint_interval_seconds": 300,
    "sqlite_idle_checkpoint_threshold_mb": 64,
    "sqlite_idle_checkpoint_min_idle_seconds": 15,
    "saucenao_cooldown_seconds": 3600,
    "saucenao_transient_error_cooldown_seconds": 300,
    # v441: persistent long-quota admission. The fixed 4/minute host limit
    # still applies; this gate spreads scarce daily budget and reserves probes.
    "saucenao_quota_governor_enabled": True,
    "saucenao_quota_reserve": 10,
    "saucenao_quota_spread_seconds": 3600,
    "saucenao_quota_probe_interval_seconds": 3600,
    "limit_files": 0,
    "enable_md5_lookup": True,
    "enable_saucenao": True,
    "enable_iqdb": True,
    "reverse_saucenao_first": False,        # (v5.44.3) reverse search: iqdb.org and the others first, SauceNAO after them
                                            # (its day's searches for what they did not find); True — SauceNAO first
    "reverse_order_v5443": True,            # the order of v5.44.3 is in (load_settings: a file without it is older)
    "enable_danbooru_iqdb": False,
    "enable_e621_iqdb": True,
    "enable_atf_iqdb": True,                # (v5.44.39) ATF's own search of similar pictures (core/parser/reverse)
    "enable_fluffle": True,                 # (v5.44.14) Fluffle: e621, FurAffinity, Twitter, Bluesky… (core/parser/reverse)
    "parser_reverse_remember": True,        # (v5.44.18) a service that answered about a file is not asked again
                                            # (core/parser/reverse_journal.py; the old program's reverse_branch_status)
    "e621_export_enabled": False,           # (v5.44.25) MD5 on e621 from its daily export (core/e621_export.py, ~2 GB)
    "e621_export_refresh_days": 30,         # …downloaded again after this many days (new posts: every hour by the API)
    "e621_iqdb_v54414": True,               # e621 IQDB is on again once (load_settings: a file without it is older)
    "e621_browser_api_fallback": True,
    "e621_browser_api_verify_timeout_seconds": 120,
    "e621_browser_api_backend": "companion_extension",
    "e621_browser_api_companion_timeout_seconds": 120,
    "e621_companion_v342_default_attached": True,
    "e621_iqdb_max_results": 5,
    # Branch-local fallback only. A valid server Retry-After overrides it;
    # repeated failed canaries double the previous delay up to one hour.
    "e621_iqdb_branch_cooldown_seconds": 300,
    "enable_tineye": False,
    "tineye_delay_min": 30,
    "tineye_delay_max": 90,
    "tineye_browser_fallback": True,
    "tineye_browser_timeout_seconds": 60,
    "tineye_api_docs_url": "https://api.tineye.com/",
    "tineye_max_results": 10,
    "iqdb_min_similarity": 75.0,
    "saucenao_api_key": "",
    "min_similarity": 85.0,
    "language": "ru",
    "language_selected_once": False,
    "tag_sort": "count_desc",
    "gallery_tag_sort": "count_desc",
    "all_tags_sort": "count_desc",
    "group_tags_sort": "count_desc",
    "ignore_numeric_tags": False,
    "media_muted": True,
    "media_volume": 50,
    "manga_root": "",
    "manhwa_root": "",                     # (v5.44.59) the manhwa folder; "" — «manhwa» in the archive
    "manga_site_addresses": {},            # (v5.44.78) the owner's addresses of the sites of manga that moved
    "manga_language": "ru",                # (v5.44.79) MangaDex's chapters: "ru", "en", "original" (the card picks others)
    "manhwa_watch_auto": True,             # (v5.44.116) «Новые главы — скачивать самой»: the nightly round
    "manga_archive_auto": True,            # (v5.44.81) «Из архива»: the titles of the folders known by themselves, in the background
    "parser_skip_manga": True,             # (v5.44.84) the parser leaves the manga of its folder to «Из архива»;
                                           # (v5.44.125) «Мангу из исходной папки — проверять и класть в «Мангу»»
    "parser_manga_fill": True,             # (v5.44.125) «Докачивать недостающие главы» of that manga (core/parser_manga)
    "manga_archive_pictures": True,        # (v5.44.81) …and by their pictures (SauceNAO, the parser's key) when nothing else tells
    "manga_archive_pictures_per_day": 40,  # (v5.44.81) SauceNAO's searches a day for the archive at most;
                                           # (v5.44.125) the manga of the parser's folder too — one count
    "video_root": "",                   # the folder of series for the tab «Видео» ("" — «video» in the archive)
    "video_tab_seconds": 60,            # a video card longer than this is on the tab «Видео», not in the gallery
    "video_av1_own": "",                # (v5.44) the program's own player shows AV1 here: "yes", "no" (learnt), "" — not known
    "video_source_aniliberty": True,    # «Скачать с сайта»: a title without video of its own (YummyAnime) — on AniLiberty
    "aniliberty_url": "https://aniliberty.top",   # its address (core/video_sources; anilibria.top is its mirror)
    "video_source_dubs": True,          # «Скачать с сайта»: YummyAnime's title — every voice-over from Kodik and CVH
    "yummy_api_url": "https://api.yani.tv",       # YummyAnime's API (core/video_dubs)
    "video_dub_merge": True,            # … the voice-overs of each episode into one MKV (core/video_merge): the page's tick
    "post_sets_enabled": True,          # the сборка over a card's picture (webui/pages/post_sets.py)
    "card_sets_tiles": True,            # a сборка is one tile of the gallery (core/card_sets.py)
    "card_sets_auto": True,             # the program puts сборки together by itself
    "comic_manga_auto": True,           # (v5.44.111) comics of the gallery and manga of the parser's folder → manga folder
    "card_sets_ask_sites": True,        # the sites asked once about the posts of the cards from before (core/site_posts.py)
    "card_sets_remote": True,           # (v5.30) a parent post's сборка shows its parts the library has not («api»)
    "show_reaction_counter": False,
    "manga_url_tags_enabled": True,
    "manga_columns": 5,
    "manga_reader_layout": "bottom",
    "manga_sort": "title",
    "manga_separate_tags": True,
    "sites": DEFAULT_SITES,
    "custom_sites": [],
    # Parser site-manager UI state (v131): presets can be removed and rows keep a manual scan order.
    "deleted_builtin_sites": [],
    "site_manual_order": [],
    "use_sqlite_index": True,
    "sqlite_auto_index_on_gallery_open": False,
    "sqlite_compute_md5_on_index": False,
    "sqlite_compute_phash_on_index": True,
    "sqlite_db_folder": "",
    # Safety: after the DB was initialized once, a missing sqlite file means
    # disconnected/wrong archive path until the user explicitly overrides it.
    "db_initialized_once": False,
    "sqlite_allow_recreate_missing_db": False,
    "task_max_workers": 4,
    "gallery_sql_page_size": 200,
    "thumbnail_worker_enabled": True,
    "thumbs_pregen_on_index": False,
    "thumb_pregen_workers": 1,
    "thumb_cache_w": 256,
    "thumb_cache_h": 256,
    "thumb_cache_card_w": 240,
    "thumb_cache_card_h": 220,
    "sqlite_connection_pool": True,
    # SQLite cache is per connection. Keep the default conservative because the
    # parser, gallery and background workers may each own a connection.
    "sqlite_cache_mb": 40,
    "sqlite_temp_store": "FILE",
    "sqlite_wal_limit_mb": 512,
    "sqlite_checkpoint_on_exit": True,
    "db_batch_commit_size": 100,
    "watch_filesystem": False,
    "watch_poll_seconds": 15,
    "tagger_parallel_workers": 4,
    "http_min_interval_seconds": 1.0,
    "http_min_interval_by_host": {},
    # Library lifecycle / safety
    "imports_to_inbox": True,
    "inbox_auto_archive_hours": 24,
    "deleted_reimport_policy": "skip",
    "trash_auto_purge_days": 0,
    "thumb_cleanup_on_exit": True,
    "thumb_keep_recent": 500,
    # Gallery performance controls; safe defaults match the existing v115 behaviour.
    "thumb_quality_scale": 2,
    "thumb_memory_items": 120,
    "thumb_threads": 1,
    "thumb_prefetch_pages": False,
    "gallery_defer_sidebar_counts_while_parser": True,
    "gallery_sidebar_refresh_delay_ms": 7000,
    "pixmap_cache_mb": 128,
    "performance_slow_ms": 100,
    "separate_settings_storage": False,
    "settings_storage_dir": "",
    "large_download_warning_count": 1000,
    "disk_free_reserve_gb": 2.0,
    "tag_group_order": ["artist", "contributor", "character", "copyright", "species", "general", "meta", "lore", "invalid", "parody", "language", "category", "pages"],
    "tag_group_colors": {"artist": "#ff3838", "contributor": "#e67e22", "character": "#00a000", "copyright": "#ff54a7", "species": "#22a6b3", "general": "#004cff", "meta": "#ff9900", "lore": "#9b59b6", "invalid": "#7f8c8d", "parody": "#ff54a7", "language": "#cc8800", "category": "#00aaaa", "pages": "#888888"},
    "tag_colors": {},  # individual visible tag colour overrides, keyed by normalized tag
    "hide_single_char_tags": True,
    "hide_technical_tags": True,
    "hide_meta_tags": False,
    "hide_rating_tags": False,
    "hotkeys": {"previous": "A", "next": "D", "favorite": "F", "fit": "W", "volume": "E", "back": "Q", "fullscreen": "F11", "zoom_in": "+", "zoom_out": "-", "zoom_reset": "0"},
    # Sidebar modules: free page tree.  Legacy 4-workspace filtering can be
    # disabled; by default v326 lets the user put any page inside any page.
    "interface_free_navigation": True,
    "interface_modules": {},
    # The program window: "web" — the web pages in the whole window (ui/web_shell.py), "browser" — the pages in the
    # usual browser and a small window that keeps the program running (where Qt WebEngine does not work; chosen by the
    # program itself after a failure too).  Read at start (ui/program_window.py).  "classic" (the old window) — gone.
    "ui_shell": "web",
    "window_maximized": True,               # (v5.28) the window opens maximized unless the person left it smaller
    # How the window's built-in browser (Qt WebEngine) draws: "auto" — with the graphics card, "off" — without it
    # (Chromium's --disable-gpu; for drivers that leave half-drawn frames).  Read at start, before the application
    # object (ui/program_window.browser_flags).
    "webview_gpu": "auto",
    # New web gallery (webui): 0 = any free port on 127.0.0.1; pictures per page.
    "webui_port": 0,
    "phone_access": False,             # (v5.44.64) the owner's phone let in through Tailscale (core/phone_access)
    "phone_name": "",                  # the computer's name in the tailnet then
    "phone_login": "",                 # the Tailscale account of the computer then
    # (v5.44.71) or through the owner's own server (core/phone_relay): on, the server for SSH, the phone's https address,
    # the server's own key at the setup (another one — refused); the password of the setup is never kept
    "phone_relay": False,
    "phone_relay_host": "",
    "phone_relay_ssh_port": 22,
    "phone_relay_address": "",
    "phone_relay_host_key": "",
    "webui_posts_per_page": 0,          # 0: as many as fit the window (counted by the page)
    "interface_module_order": [],
    "interface_extra_collapsed": True,
    # Per-page nested navigation collapsed state.  Key = parent page key,
    # value True means its child pages are hidden until expanded.
    "interface_page_collapsed": {},
    "auto_hide_single_workspace": True,
    # Database copy before deletions: reused for this many minutes, only the newest N kept.
    "backup_operation_min_interval_minutes": 10,
    "backup_operation_keep": 5,
    # Lightweight backup: copies SQLite/config/manual metadata only, never media/cache.
    "light_backup_enabled": False,
    "light_backup_dir": "",
    "light_backup_on_exit": True,
    "light_backup_interval_hours": 24,
    "light_backup_keep_last": 10,
    "light_backup_include_cookies": False,
    "light_backup_last_at": 0,
}


def _dict_or_empty(value):
    return value if isinstance(value, dict) else {}

def _list_or_empty(value):
    return value if isinstance(value, list) else []

def _normalize_sites(saved, deleted_builtin_sites=None):
    saved = _dict_or_empty(saved)
    deleted = {str(x) for x in _list_or_empty(deleted_builtin_sites)}
    # A built-in may have had its domain/base URL changed in the site editor.
    # builtin_id keeps it tied to the original template and prevents the old
    # default domain from silently reappearing beside the edited row.
    replaced_builtin_ids = {
        str(cfg.get("builtin_id")) for cfg in saved.values()
        if isinstance(cfg, dict) and cfg.get("builtin_id")
    }
    out = {}
    for domain, defaults in DEFAULT_SITES.items():
        if domain in deleted or (domain in replaced_builtin_ids and domain not in saved):
            continue
        current = saved.get(domain, {})
        out[domain] = {**defaults, **_dict_or_empty(current)}
    # Keep unknown/new built-in/custom-like site configs without crashing older settings.
    for domain, cfg in saved.items():
        if domain not in out and isinstance(cfg, dict):
            builtin_id = str(cfg.get("builtin_id") or "")
            if domain not in deleted and builtin_id not in deleted:
                out[domain] = cfg
    return out

def _normalize_custom_sites(saved):
    out = []
    for item in _list_or_empty(saved):
        if isinstance(item, dict):
            out.append(item)
    return out

_RETIRED_LIVE_SETTINGS = {
    "copy_results_enabled", "copy_mode", "tags_suffix", "sources_suffix", "output_suffix",
    "mark_no_match", "tagger_site_conveyor_enabled", "use_browser_auth",
    "use_system_browser_cookies", "browser_auth_url", "browser_auth_wait_seconds",
    # removed in 5.0: node editor, games page, Huihui integration
    "parser_blueprint_enabled", "parser_blueprint_full_access", "parser_blueprint_auto_add_sites",
    "parser_blueprint_respect_site_enabled", "parser_blueprint_warn_invalid",
    "parser_blueprint_v321_default_attached", "games_root",
    "huihui_integration_enabled", "huihui_gateway_url", "huihui_model_id",
    "huihui_request_timeout_seconds", "huihui_generation_timeout_seconds",
    "huihui_auto_import_results", "huihui_ui_url", "huihui_allow_remote_gateway",
    "huihui_bridge_api_enabled", "huihui_bridge_api_token", "huihui_database_timeout_seconds",
    "huihui_min_schema_version", "huihui_reference_limit", "huihui_reference_min_side",
    "huihui_expand_threshold", "huihui_expand_exclude_categories",
}

def deep_merge(base, data):
    data = _dict_or_empty(data)
    out = base.copy()
    for k, v in data.items():
        if k not in ("sites", "custom_sites") and k not in _RETIRED_LIVE_SETTINGS:
            out[k] = v
    deleted_builtin_sites = _list_or_empty(data.get("deleted_builtin_sites", DEFAULT_SETTINGS.get("deleted_builtin_sites", [])))
    out["deleted_builtin_sites"] = deleted_builtin_sites
    out["sites"] = _normalize_sites(data.get("sites", {}), deleted_builtin_sites)
    out["custom_sites"] = _normalize_custom_sites(data.get("custom_sites", DEFAULT_SETTINGS.get("custom_sites", [])))
    out["site_manual_order"] = _list_or_empty(data.get("site_manual_order", DEFAULT_SETTINGS.get("site_manual_order", [])))
    return out

def _lock_to_active_portable_workspace(settings):
    """Force config fields to agree with the workspace selected at bootstrap.

    v135/v136 could correctly resolve Local_Booru_Archive/settings through a
    pointer but then load stale fields copied from the old Documents config
    (for example output_dir=Local_Booru_Output and separate_settings_storage=
    false).  Saving that stale object removed the pointer and sent the next
    launch back to Documents.  Once this process is bootstrapped from a
    portable workspace, that workspace is authoritative.
    """
    if not USING_SEPARATE_STORAGE:
        return settings
    locked = dict(settings or {})
    archive_root = DATA_DIR.parent
    output = archive_root / "output"
    output.mkdir(parents=True, exist_ok=True)
    locked["separate_settings_storage"] = True
    locked["settings_storage_dir"] = str(DATA_DIR)
    locked["output_dir"] = str(output)
    return locked

def _canonicalize_new_portable_workspace(settings, target):
    """Keep a new portable selection inside one Local_Booru_Archive root."""
    fixed = dict(settings or {})
    target = Path(target).expanduser().resolve()
    archive_root = target.parent if target.name.lower() == "settings" else target
    output = archive_root / "output"
    output.mkdir(parents=True, exist_ok=True)
    fixed["separate_settings_storage"] = True
    fixed["settings_storage_dir"] = str(target)
    fixed["output_dir"] = str(output)
    return fixed


def _requested_existing_workspace(settings):
    """Detect an explicit request to switch to another existing archive.

    When the app is already running from a portable archive, normal saving must
    keep paths locked to the active DATA_DIR.  The exception is when the user
    selected an existing Local_Booru_Archive through the reconnect action or by
    choosing its output folder.  In that case saving must update only the small
    workspace pointer and must not overwrite the target archive's config with
    the currently loaded old archive settings.
    """
    if not USING_SEPARATE_STORAGE:
        return None
    data = settings or {}
    candidates = []
    explicit = str(data.get("settings_storage_dir", "") or "").strip()
    if explicit:
        candidates.append(explicit)
    output = str(data.get("output_dir", "") or "").strip()
    if output:
        try:
            op = Path(output).expanduser()
            if op.name.lower() == "output" and op.parent.name.lower() == OUTPUT_FOLDER_NAME.lower():
                candidates.append(op.parent)
            elif op.name.lower() == OUTPUT_FOLDER_NAME.lower():
                candidates.append(op)
        except Exception:
            candidates.append(output)
    for candidate in candidates:
        target = normalize_archive_settings_root(candidate)
        if target is None:
            continue
        try:
            if target.resolve() == DATA_DIR.resolve():
                continue
        except Exception:
            pass
        return target
    return None

_REMOVED_REVERSE_SERVICES_V204 = (
    "enable_fuzzysearch",
    "fuzzysearch_api_key",
    "fuzzysearch_endpoint",
    "fuzzysearch_api_docs_url",
    "fuzzysearch_max_results",
    "fluffle_api_key",
    "fluffle_endpoint",
    "fluffle_api_docs_url",
    "fluffle_max_results",
)

def _drop_removed_reverse_services(settings):
    """v204: Fluffle/FuzzySearch removed after false-positive tagging.  (v5.44.14) Fluffle is back
    (core/parser/reverse/fluffle.py) — its hits are never taken on its word: the post's preview or the source's posts
    are compared with the file — so ``enable_fluffle`` is kept; its old keys still go."""
    fixed = dict(settings or {})
    for key in _REMOVED_REVERSE_SERVICES_V204:
        fixed.pop(key, None)
    return fixed

def _read_settings_file(path: Path):
    """Parsed settings dict, or None.  A damaged file is kept aside, never overwritten.

    When the main file cannot be parsed (power cut while writing, disk error), it is
    renamed to ``app_settings.json.broken-<time>`` and the last good copy
    (``app_settings.json.bak``, written on every save) is used instead, so API keys
    and site settings survive.  Old behaviour: silently start with defaults and
    overwrite the damaged file on the next save.
    """
    from core.file_safety import read_json_kept
    return read_json_kept(path, dict, what="Settings file")


def load_settings():
    # SETTINGS_FILE points at Local_Booru_Archive/settings/config whenever a
    # portable workspace is configured.  A pre-v135 Documents copy is read
    # only as a migration fallback and is retired after the portable config is
    # confirmed to exist.
    for candidate in (SETTINGS_FILE, BOOTSTRAP_SETTINGS_FILE):
        raw_loaded = _read_settings_file(candidate)
        if raw_loaded is not None:
            try:
                loaded = deep_merge(DEFAULT_SETTINGS, raw_loaded)
                loaded = _drop_removed_reverse_services(_lock_to_active_portable_workspace(loaded))
                # v160: switch the default gallery/grabber page shape from the
                # old 4x4 test layout to a 16:9-friendly 8x4 layout.  Only
                # touch untouched old defaults; manual user layouts remain as-is.
                if not loaded.get("gallery_layout_v160_initialized"):
                    try:
                        if int(loaded.get("columns", 4) or 4) == 4 and int(loaded.get("rows_per_page", 4) or 4) == 4:
                            loaded["columns"] = 8
                            loaded["rows_per_page"] = 4
                            loaded["items_per_page"] = 32
                    except Exception:
                        pass
                    loaded["gallery_layout_v160_initialized"] = True
                # v5.17: pictures per page of the web gallery — as many as fit the window instead of the old fixed 42.
                if not loaded.get("webui_posts_per_page_v517"):
                    try:
                        if int(loaded.get("webui_posts_per_page") or 0) == 42:
                            loaded["webui_posts_per_page"] = 0
                    except (TypeError, ValueError):
                        loaded["webui_posts_per_page"] = 0
                    loaded["webui_posts_per_page_v517"] = True
                # v5.25: SauceNAO's daily quota was spread over a whole day (an account with ~4000 searches left made
                # one search in ~20 s, and the whole reverse search waited for it): over an hour now — such an
                # account goes at its 30-second limit, a small one still lasts the hour.  Only the old default.
                if not loaded.get("saucenao_spread_v525"):
                    try:
                        if int(loaded.get("saucenao_quota_spread_seconds") or 0) == 86400:
                            loaded["saucenao_quota_spread_seconds"] = 3600
                    except (TypeError, ValueError):
                        loaded["saucenao_quota_spread_seconds"] = 3600
                    loaded["saucenao_spread_v525"] = True
                # v5.40: the old window (ui_shell "classic") is gone: the program window (or, chosen, the browser).
                if str(loaded.get("ui_shell") or "").strip().lower() not in ("web", "browser"):
                    loaded["ui_shell"] = "web"
                # v5.28: «Разрешить скачивание новых медиа в этой сессии?» at every start — the person asked it gone:
                # once off; the switch is in «Настройки» (downloads stay allowed, as the question's default was).
                if not loaded.get("session_downloads_prompt_v528"):
                    loaded["session_media_downloads_prompt"] = False
                    loaded["session_downloads_prompt_v528"] = True
                # v5.44.3: reverse search — IQDB first, SauceNAO after it, as the old program's stages (the person:
                # «очередью iqdb>snao»: the day's few searches of SauceNAO only for what IQDB did not find).  Once:
                # «Сайты и ключи → Порядок» can turn it round again.
                if not (isinstance(raw_loaded, dict) and raw_loaded.get("reverse_order_v5443")):
                    loaded["reverse_saucenao_first"] = False
                    loaded["reverse_order_v5443"] = True
                # v5.44.14: e621's search of similar pictures works without an account — it counted as off without
                # the e621 login and key, silently, and the person turned it off as «bringing nothing».  On again
                # once, and Fluffle (new, a word of a far older version may be left) with it; «Сайты и ключи» can
                # turn them off again.
                if not (isinstance(raw_loaded, dict) and raw_loaded.get("e621_iqdb_v54414")):
                    loaded["enable_e621_iqdb"] = True
                    loaded["enable_fluffle"] = True
                    loaded["e621_iqdb_v54414"] = True
                # v5.1: the new gallery and manga go into the menu right before the old ones.
                if not loaded.get("webui_menu_v51_placed"):
                    order = list(loaded.get("interface_module_order") or [])
                    for new_key, old_key in (("Booru", "Gallery"), ("BooruManga", "Manga")):
                        if order and new_key not in order:
                            order.insert(order.index(old_key) if old_key in order else 0, new_key)
                    if order:
                        loaded["interface_module_order"] = order
                    loaded["webui_menu_v51_placed"] = True
                # v342: keep e621 JSON fallback on the companion extension.
                if not (isinstance(raw_loaded, dict) and raw_loaded.get("e621_companion_v342_default_attached")):
                    loaded["e621_browser_api_backend"] = "companion_extension"
                    loaded["e621_companion_v342_default_attached"] = True
                # v453: retire every embedded-browser/CDP setting. The ordinary
                # user browser is selected only for opening a task URL; all
                # page interaction is performed by the companion extension.
                retired_browser_keys = {
                    "rule34_image_key_hotlink_playwright_fallback",
                    "rule34_image_key_hotlink_playwright_headless",
                    "rule34_image_key_hotlink_playwright_timeout",
                    "rule34_image_key_hotlink_playwright_supervisor",
                    "rule34_image_key_hotlink_playwright_retries",
                    "rule34_image_key_hotlink_playwright_ephemeral",
                    "browser_fallback_disable_gpu",
                    "browser_fallback_launch_watchdog_seconds",
                    "parser_never_touch_system_chrome",
                    "e621_browser_api_headless",
                    "e621_browser_api_allow_external_chrome_cdp",
                    "e621_browser_api_launch_external_chrome",
                    "e621_browser_api_cdp_port",
                    "tineye_browser_headless",
                }
                if "parser_disable_companion_chrome_fetch" in loaded:
                    loaded["parser_disable_companion_browser_fetch"] = bool(
                        loaded.get("parser_disable_companion_chrome_fetch")
                    )
                    retired_browser_keys.add("parser_disable_companion_chrome_fetch")
                for _key in retired_browser_keys:
                    loaded.pop(_key, None)
                loaded["e621_browser_api_backend"] = "companion_extension"
                loaded["external_browser_v453_migrated"] = True
                if candidate == SETTINGS_FILE:
                    activate_portable_workspace()
                return loaded
            except Exception as exc:  # a bug in the migrations above must be visible, not lose settings
                _LOG.exception("Settings post-processing failed for %s: %s", candidate, exc)
                return deep_merge(DEFAULT_SETTINGS, raw_loaded)
    return _drop_removed_reverse_services(_lock_to_active_portable_workspace(DEFAULT_SETTINGS.copy()))

def _atomic_write_settings(path, data):
    """Write settings atomically and keep the previous good version as ``.bak``."""
    from core.file_safety import write_json_kept
    write_json_kept(path, data, dict)


def _settings_json(settings) -> str:
    """Settings as written to disk: runtime keys ("_parser_running", callbacks…) never are."""
    return json.dumps({k: v for k, v in settings.items() if not str(k).startswith("_")},
                      ensure_ascii=False, indent=2)


def write_archive_settings(settings, target) -> Path:
    """The settings of an archive made from «Документы» (``core/archive_create.py``, at a start, before the base opens):
    ``settings`` with the archive's own folders (``separate_settings_storage``, ``settings_storage_dir`` — ``target``,
    ``output_dir`` — the ``output`` beside it) as ``<target>/config/app_settings.json``.  Nothing else is written: the
    settings in «Документах» stay as they were."""
    fixed = _drop_removed_reverse_services(settings)
    for retired in _RETIRED_LIVE_SETTINGS:
        fixed.pop(retired, None)
    fixed = _canonicalize_new_portable_workspace(fixed, target)
    canonical = Path(target) / "config" / "app_settings.json"
    _atomic_write_settings(canonical, _settings_json(fixed))
    return canonical


def _existing_archive_settings_root(settings):
    """settings/ of an existing archive the user asked to switch to, or None for a new location."""
    explicit = str((settings or {}).get("settings_storage_dir", "") or "").strip()
    target = normalize_archive_settings_root(explicit) if explicit else None
    if target is None:
        return None
    if (target / "config" / "app_settings.json").is_file() or (target / "db" / "local_booru_index.sqlite3").is_file():
        return target
    return None

def save_settings(settings):
    # The archive chosen by the user is unreachable (drive not connected): nothing is
    # written, so the pointer to it and its settings survive (app.py asks to connect it).
    if WORKSPACE_UNAVAILABLE:
        _LOG.warning("Settings not saved: the archive %s is not available", WORKSPACE_UNAVAILABLE)
        return
    # Keep a reference to the caller-owned dict.  Some UI/tests rely on
    # save_settings() normalizing paths back into the same object.
    _original_settings_ref = settings if isinstance(settings, dict) else None
    settings = _drop_removed_reverse_services(settings)
    for retired in _RETIRED_LIVE_SETTINGS:
        settings.pop(retired, None)
    settings["items_per_page"] = int(settings.get("columns", 8)) * int(settings.get("rows_per_page", 4))

    # A process opened through Local_Booru_Archive/settings must never detach
    # itself merely because stale config fields changed.  However, when the user
    # explicitly points the UI at another existing Local_Booru_Archive, saving
    # must not snap the workspace pointer back to the old DATA_DIR.
    switch_target = _requested_existing_workspace(settings)
    if switch_target is not None:
        # Another existing archive named by these settings (the old settings page): it opens at the next start
        # (the pointer; its own settings, base and cookies are never written over).  This run keeps its archive:
        # the branch below puts the open archive's folders back into these settings and saves them to it — with the
        # other archive's folders in the live settings, the rest of the run downloaded and trashed into it.
        choose_next_archive(switch_target)
        settings["_workspace_switch_pending"] = True
    if not USING_SEPARATE_STORAGE and bool(settings.get("separate_settings_storage", False)) \
            and _existing_archive_settings_root(settings) is not None:
        # Legacy (Documents) process + «Подключить существующий архив»: the archive keeps its
        # own database and config.  Only the pointer is written; the restart opens the archive.
        # (Old behaviour copied the Documents database and settings over the archive.)
        choose_next_archive(_existing_archive_settings_root(settings))
        settings["_workspace_switch_pending"] = True
    else:
        portable_requested = USING_SEPARATE_STORAGE or bool(settings.get("separate_settings_storage", False))
        if portable_requested:
            if USING_SEPARATE_STORAGE:
                target = DATA_DIR
                normalized = _lock_to_active_portable_workspace(settings)
            else:
                target = prepare_separate_storage(settings)
                if target is None:
                    raise OSError("Не удалось определить Local_Booru_Archive/settings")
                normalized = _canonicalize_new_portable_workspace(settings, target)
            settings.update(normalized)
            data = _settings_json(settings)
            canonical = target / "config" / "app_settings.json"
            _atomic_write_settings(canonical, data)
            # The locator contains only paths.  Never mirror API keys, cookies or
            # user configuration into Documents when a portable archive is active.
            # Another archive chosen in this run for the next start keeps the pointer (core.archive_switch).
            if next_archive() is None:
                write_workspace_pointer(target)
            # Once the active process itself is portable, safely remove any obsolete
            # full settings copy from Documents.
            try:
                if USING_SEPARATE_STORAGE and BOOTSTRAP_SETTINGS_FILE.exists() and BOOTSTRAP_SETTINGS_FILE.resolve() != canonical.resolve():
                    BOOTSTRAP_SETTINGS_FILE.unlink(missing_ok=True)
            except Exception:
                pass
        else:
            # Legacy mode is retained only until the user selects/creates a portable
            # Local_Booru_Archive.  Saving never deletes the pointer files: forgetting an
            # archive is an explicit user action (core.paths.remove_workspace_pointer).
            _atomic_write_settings(BOOTSTRAP_SETTINGS_FILE, _settings_json(settings))

    if _original_settings_ref is not None and _original_settings_ref is not settings:
        # In place, never empty for a moment: the web server's threads read the window's settings meanwhile.
        for key in [k for k in _original_settings_ref if k not in settings]:
            _original_settings_ref.pop(key, None)
        _original_settings_ref.update(settings)
