import json
from pathlib import Path
from core.paths import (
    SETTINGS_FILE, BOOTSTRAP_SETTINGS_FILE, DATA_DIR, USING_SEPARATE_STORAGE,
    prepare_separate_storage, write_workspace_pointer, remove_workspace_pointer,
    activate_portable_workspace, normalize_archive_settings_root, OUTPUT_FOLDER_NAME,
)


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
    "tagger_ui_log_queue_cap": 5000,
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
    "parser_blueprint_enabled": True,
    "parser_blueprint_full_access": True,
    "parser_blueprint_auto_add_sites": True,
    "parser_blueprint_respect_site_enabled": True,
    "parser_blueprint_warn_invalid": True,
    "parser_blueprint_v321_default_attached": True,
    "parser_trust_filename_md5_only_if_matches_real": True,
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
    # Parser performance profile is resolved at runtime from detected RAM.
    # auto: <=18GB low_memory, <=36GB balanced, otherwise performance.
    "tagger_performance_profile": "auto",
    "tagger_reverse_admit_window_files": 64,
    "local_preflight_low_memory_skip_threshold": 5000,
    "tagger_db_startup_lock_wait_seconds": 90,
    "local_tagger_workers": 4,
    "local_thumb_workers": 4,
    "local_thumb_pregen_workers": 1,
    "local_background_workers": 4,
    "visual_nomatch_classify_enabled": True,
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
    "rule34_variant_locator_side_queue_enabled": True,
    "rule34_image_key_locator_mode": "hotlink_only",
    "rule34_image_key_hotlink_redirect_enabled": True,
    "rule34_image_key_hotlink_extensions": "png",
    "rule34_image_key_hotlink_request_timeout": 4.0,
    "rule34_image_key_hotlink_playwright_fallback": True,
    "rule34_image_key_hotlink_playwright_headless": False,
    "rule34_image_key_hotlink_playwright_timeout": 25.0,
    "rule34_image_key_hotlink_playwright_supervisor": True,
    "rule34_image_key_hotlink_playwright_retries": 1,
    "rule34_image_key_hotlink_playwright_ephemeral": True,
    "browser_fallback_disable_gpu": True,
    "parser_never_touch_system_chrome": True,
    "parser_disable_companion_chrome_fetch": False,
    "browser_fallback_launch_watchdog_seconds": 18.0,
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
    # Browser extension companion: localhost-only API used by the Chrome/Chromium
    # extension to hide already-downloaded booru cards visually. Parser/tagger are
    # not affected by this API or its manual hide list.
    "browser_companion_api_enabled": True,
    "browser_companion_api_host": "127.0.0.1",
    "browser_companion_api_port": 47734,
    "browser_companion_use_grabber_hides": True,
    "browser_companion_max_batch": 250,
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
    "network_retry_delay_seconds": 10,
    "sqlite_passive_checkpoint_every": 500,
    "saucenao_cooldown_seconds": 3600,
    "limit_files": 0,
    "enable_md5_lookup": True,
    "enable_saucenao": True,
    "enable_iqdb": True,
    "enable_danbooru_iqdb": False,
    "enable_e621_iqdb": True,
    "e621_browser_api_fallback": True,
    "e621_browser_api_headless": False,
    "e621_browser_api_verify_timeout_seconds": 120,
    "e621_browser_api_backend": "companion_extension",
    "e621_browser_api_companion_timeout_seconds": 120,
    "e621_companion_v342_default_attached": True,
    "e621_browser_api_allow_external_chrome_cdp": False,
    "e621_browser_api_cdp_port": 9222,
    "e621_browser_api_launch_external_chrome": False,
    "e621_iqdb_max_results": 5,
    # Branch-local cooldown only. 429 from e621 IQDB must not stop the whole parser.
    "e621_iqdb_branch_cooldown_seconds": 300,
    "enable_tineye": False,
    "tineye_delay_min": 30,
    "tineye_delay_max": 90,
    "tineye_browser_fallback": True,
    "tineye_browser_headless": False,
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
    "games_root": "",
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
    "interface_module_order": [],
    "interface_extra_collapsed": True,
    # Per-page nested navigation collapsed state.  Key = parent page key,
    # value True means its child pages are hidden until expanded.
    "interface_page_collapsed": {},
    "auto_hide_single_workspace": True,
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
    "enable_fluffle",
    "fluffle_api_key",
    "fluffle_endpoint",
    "fluffle_api_docs_url",
    "fluffle_max_results",
)

def _drop_removed_reverse_services(settings):
    """v204: Fluffle/FuzzySearch removed after false-positive tagging."""
    fixed = dict(settings or {})
    for key in _REMOVED_REVERSE_SERVICES_V204:
        fixed.pop(key, None)
    return fixed

def load_settings():
    # SETTINGS_FILE points at Local_Booru_Archive/settings/config whenever a
    # portable workspace is configured.  A pre-v135 Documents copy is read
    # only as a migration fallback and is retired after the portable config is
    # confirmed to exist.
    for candidate in (SETTINGS_FILE, BOOTSTRAP_SETTINGS_FILE):
        if candidate.exists():
            try:
                raw_loaded = json.loads(candidate.read_text(encoding="utf-8"))
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
                # v321: the parser blueprint is the normal/default parser plan.
                # Existing settings from v319/v320 may contain False because the
                # editor was experimental there; turn it on once, then respect
                # later manual toggles.
                if not loaded.get("parser_blueprint_v321_default_attached"):
                    loaded["parser_blueprint_enabled"] = True
                    loaded["parser_blueprint_full_access"] = True
                    loaded["parser_blueprint_auto_add_sites"] = True
                    loaded["parser_blueprint_respect_site_enabled"] = True
                    loaded["parser_blueprint_v321_default_attached"] = True
                # v342: v341 overcorrected and forced e621 away from the
                # companion extension.  Restore the intended mode: use the
                # already-open Chrome/e621 tab through the companion bridge,
                # but keep auto-launch/CDP control of system Chrome disabled.
                if not (isinstance(raw_loaded, dict) and raw_loaded.get("e621_companion_v342_default_attached")):
                    loaded["e621_browser_api_backend"] = "companion_extension"
                    loaded["parser_disable_companion_chrome_fetch"] = False
                    loaded["e621_browser_api_allow_external_chrome_cdp"] = False
                    loaded["e621_browser_api_launch_external_chrome"] = False
                    loaded["e621_companion_v342_default_attached"] = True
                if candidate == SETTINGS_FILE:
                    activate_portable_workspace()
                return loaded
            except Exception:
                pass
    return _drop_removed_reverse_services(_lock_to_active_portable_workspace(DEFAULT_SETTINGS.copy()))

def _atomic_write_settings(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)

def save_settings(settings):
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
        normalized = _canonicalize_new_portable_workspace(settings, switch_target)
        normalized["_workspace_switch_pending"] = True
        settings.update(normalized)
        # Only update the locators.  The target archive already has its own
        # app_settings.json/database/cookies and must not be overwritten by the
        # old currently-loaded settings.  Restart will load the target config.
        write_workspace_pointer(switch_target)
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
            data = json.dumps(settings, ensure_ascii=False, indent=2)
            canonical = target / "config" / "app_settings.json"
            _atomic_write_settings(canonical, data)
            # The locator contains only paths.  Never mirror API keys, cookies or
            # user configuration into Documents when a portable archive is active.
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
            # Local_Booru_Archive.  A connected archive cannot fall back here.
            data = json.dumps(settings, ensure_ascii=False, indent=2)
            remove_workspace_pointer()
            _atomic_write_settings(BOOTSTRAP_SETTINGS_FILE, data)

    if _original_settings_ref is not None and _original_settings_ref is not settings:
        _original_settings_ref.clear()
        _original_settings_ref.update(settings)
