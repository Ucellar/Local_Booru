import json
from core.paths import SETTINGS_FILE, BOOTSTRAP_SETTINGS_FILE, prepare_separate_storage


# All known sites grouped by engine type
# Each site: {enabled, type, login, api_key, user_id, login_url, notes}
SITES_BY_ENGINE = {
    "Danbooru": {
        "danbooru.donmai.us":       {"enabled": False, "type": "danbooru",      "login": "", "api_key": "", "user_id": "", "login_url": "https://danbooru.donmai.us",       "notes": "Лучшие теги. Cloudflare — нужен curl_cffi"},
        "booru.allthefallen.moe":   {"enabled": True,  "type": "danbooru",      "login": "", "api_key": "", "user_id": "", "login_url": "https://booru.allthefallen.moe",   "notes": "ATF — Danbooru движок"},
        "lolibooru.moe":            {"enabled": False,  "type": "danbooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://lolibooru.moe",             "notes": "Danbooru движок"},
        "aibooru.online":           {"enabled": False,  "type": "danbooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://aibooru.online",            "notes": "AI-арт, Danbooru движок"},
    },
    "Gelbooru": {
        "gelbooru.com":             {"enabled": True,   "type": "gelbooru_html","login": "", "api_key": "", "user_id": "", "login_url": "https://gelbooru.com",              "notes": "Официальный DAPI JSON, MD5 через tags=md5:"},
        "rule34.xxx":               {"enabled": True,   "type": "rule34xxx",    "login": "", "api_key": "", "user_id": "", "login_url": "https://rule34.xxx",               "notes": "Gelbooru движок"},
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
    "max_console_lines": 2500,
    "enable_error_console": True,
    "workspace": "tagger",
    "gallery_filter": "all",
    "gallery_source": "output",
    "output_layout": "split_found_nomatch",
    "enable_google_fallback": True,
    "google_fallback_mode": "br34_manual",
    "output_dir": "",
    "google_fallback_enabled": False,
    "columns": 4,
    "rows_per_page": 4,
    "card_height": 220,
    "items_per_page": 16,
    "skip_existing": True,
    "tag_only_untagged": True,
    "retry_nomatch": False,
    "delay_seconds": 8.0,
    # Per-site conveyor: every enabled site advances independently through the
    # file queue while one global result writer serializes SQLite commits.
    # Low-power mode keeps this journal/conveyor but reduces its active window to one file.
    # Flat-tag sources collect quickly; categories are recovered later in a durable low-priority pass.
    "tagger_background_tag_groups": True,
    "tagger_background_rule34_categories": True,  # legacy alias
    "tagger_low_power_mode": False,
    "tagger_site_interval_seconds": 1.10,
    "tagger_conveyor_window": 32,
    "request_timeout_seconds": 20,
    "network_retry_attempts": 2,
    "network_retry_delay_seconds": 10,
    "saucenao_cooldown_seconds": 3600,
    "limit_files": 0,
    "enable_md5_lookup": True,
    "enable_saucenao": True,
    "enable_iqdb": True,
    "iqdb_min_similarity": 75.0,
    "saucenao_api_key": "",
    "min_similarity": 85.0,
    "language": "ru",
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
    "use_sqlite_index": True,
    "sqlite_auto_index_on_gallery_open": False,
    "sqlite_compute_md5_on_index": False,
    "sqlite_compute_phash_on_index": True,
    "sqlite_db_folder": "",
    "task_max_workers": 2,
    "gallery_sql_page_size": 200,
    "thumbnail_worker_enabled": True,
    "thumbs_pregen_on_index": True,
    "thumb_pregen_workers": 2,
    "thumb_cache_w": 256,
    "thumb_cache_h": 256,
    "thumb_cache_card_w": 240,
    "thumb_cache_card_h": 220,
    "sqlite_connection_pool": True,
    "db_batch_commit_size": 100,
    "watch_filesystem": False,
    "watch_poll_seconds": 15,
    "tagger_parallel_workers": 1,
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
    "thumb_memory_items": 400,
    "thumb_threads": 3,
    "thumb_prefetch_pages": True,
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
    # Sidebar modules: pages may be hidden or moved to another workspace.
    "interface_modules": {},
    "interface_module_order": [],
    "interface_extra_collapsed": True,
    "auto_hide_single_workspace": True,
}

def _dict_or_empty(value):
    return value if isinstance(value, dict) else {}

def _list_or_empty(value):
    return value if isinstance(value, list) else []

def _normalize_sites(saved):
    saved = _dict_or_empty(saved)
    out = {}
    for domain, defaults in DEFAULT_SITES.items():
        current = saved.get(domain, {})
        out[domain] = {**defaults, **_dict_or_empty(current)}
    # Keep unknown/new built-in/custom-like site configs without crashing older settings.
    for domain, cfg in saved.items():
        if domain not in out and isinstance(cfg, dict):
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
    out["sites"] = _normalize_sites(data.get("sites", {}))
    out["custom_sites"] = _normalize_custom_sites(data.get("custom_sites", DEFAULT_SETTINGS.get("custom_sites", [])))
    return out

def load_settings():
    for candidate in (SETTINGS_FILE, BOOTSTRAP_SETTINGS_FILE):
        if candidate.exists():
            try:
                return deep_merge(DEFAULT_SETTINGS, json.loads(candidate.read_text(encoding="utf-8")))
            except Exception:
                pass
    return DEFAULT_SETTINGS.copy()

def _atomic_write_settings(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)

def save_settings(settings):
    for retired in _RETIRED_LIVE_SETTINGS:
        settings.pop(retired, None)
    settings["items_per_page"] = int(settings.get("columns", 4)) * int(settings.get("rows_per_page", 4))
    data = json.dumps(settings, ensure_ascii=False, indent=2)
    _atomic_write_settings(SETTINGS_FILE, data)
    # The bootstrap copy is intentionally retained in the legacy location.
    # It tells the next launch where separately stored db/cache/config live.
    _atomic_write_settings(BOOTSTRAP_SETTINGS_FILE, data)
    if bool(settings.get("separate_settings_storage", False)):
        target = prepare_separate_storage(settings)
        if target is not None:
            _atomic_write_settings(target / "config" / "app_settings.json", data)
