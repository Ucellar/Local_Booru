import json
from core.paths import SETTINGS_FILE


# All known sites grouped by engine type
# Each site: {enabled, type, login, api_key, user_id, login_url, notes}
SITES_BY_ENGINE = {
    "Danbooru": {
        "danbooru.donmai.us":       {"enabled": False, "type": "danbooru",      "login": "", "api_key": "", "user_id": "", "login_url": "https://danbooru.donmai.us",       "notes": "Лучшие теги. Cloudflare — нужен curl_cffi"},
        "booru.allthefallen.moe":   {"enabled": True,  "type": "danbooru",      "login": "", "api_key": "", "user_id": "", "login_url": "https://booru.allthefallen.moe",   "notes": "ATF — Danbooru движок"},
        "lolibooru.moe":            {"enabled": False,  "type": "danbooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://lolibooru.moe",             "notes": "Danbooru движок"},
        "hypnohub.net":             {"enabled": False,  "type": "danbooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://hypnohub.net",              "notes": "Danbooru движок"},
        "aibooru.online":           {"enabled": False,  "type": "danbooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://aibooru.online",            "notes": "AI-арт, Danbooru движок"},
    },
    "Gelbooru": {
        "gelbooru.com":             {"enabled": True,   "type": "gelbooru_html","login": "", "api_key": "", "user_id": "", "login_url": "https://gelbooru.com",              "notes": "Огромная база, хорошее API"},
        "rule34.xxx":               {"enabled": True,   "type": "rule34xxx",    "login": "", "api_key": "", "user_id": "", "login_url": "https://rule34.xxx",               "notes": "Gelbooru движок"},
        "realbooru.com":            {"enabled": False,  "type": "gelbooru_html","login": "", "api_key": "", "user_id": "", "login_url": "https://realbooru.com",             "notes": "Реальные фото, Gelbooru движок"},
        "xbooru.com":               {"enabled": False,  "type": "gelbooru_html","login": "", "api_key": "", "user_id": "", "login_url": "https://xbooru.com",               "notes": "Gelbooru движок"},
        "tbib.org":                 {"enabled": False,  "type": "gelbooru_html","login": "", "api_key": "", "user_id": "", "login_url": "https://tbib.org",                 "notes": "The Big ImageBoard, Gelbooru движок"},
        "safebooru.org":            {"enabled": False,  "type": "gelbooru_html","login": "", "api_key": "", "user_id": "", "login_url": "https://safebooru.org",            "notes": "SFW, Gelbooru движок"},
    },
    "Moebooru": {
        "konachan.com":             {"enabled": False,  "type": "moebooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://konachan.com",             "notes": "Аниме обои высокого качества"},
        "konachan.net":             {"enabled": False,  "type": "moebooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://konachan.net",             "notes": "SFW версия konachan.com"},
        "yande.re":                 {"enabled": False,  "type": "moebooru",     "login": "", "api_key": "", "user_id": "", "login_url": "https://yande.re",                 "notes": "Высокое разрешение, аниме"},
        "rule34.us":                {"enabled": True,   "type": "rule34us",     "login": "", "api_key": "", "user_id": "", "login_url": "https://rule34.us",               "notes": "Moebooru движок"},
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
    "copy_results_enabled": True,
    "copy_mode": "copy",
    "output_layout": "split_found_nomatch",
    "enable_google_fallback": True,
    "google_fallback_mode": "br34_manual",
    "output_dir": "",
    "google_fallback_enabled": False,
    "columns": 4,
    "rows_per_page": 4,
    "card_height": 220,
    "items_per_page": 16,
    "tags_suffix": ".tags.txt",
    "sources_suffix": ".sources.txt",
    "output_suffix": ".tags.txt",
    "skip_existing": True,
    "tag_only_untagged": True,
    "retry_nomatch": False,
    "mark_no_match": True,
    "delay_seconds": 8.0,
    "request_timeout_seconds": 20,
    "saucenao_cooldown_seconds": 3600,
    "limit_files": 0,
    "enable_md5_lookup": True,
    "enable_saucenao": True,
    "enable_iqdb": True,
    "iqdb_min_similarity": 75.0,
    "saucenao_api_key": "",
    "min_similarity": 85.0,
    "use_browser_auth": False,
    "use_system_browser_cookies": False,
    "browser_auth_url": "https://example.com",
    "browser_auth_wait_seconds": 60,
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
    "sqlite_db_folder": "",
    "task_max_workers": 2,
    "gallery_sql_page_size": 200,
    "thumbnail_worker_enabled": True,
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

def deep_merge(base, data):
    data = _dict_or_empty(data)
    out = base.copy()
    for k, v in data.items():
        if k not in ("sites", "custom_sites"):
            out[k] = v
    out["sites"] = _normalize_sites(data.get("sites", {}))
    out["custom_sites"] = _normalize_custom_sites(data.get("custom_sites", DEFAULT_SETTINGS.get("custom_sites", [])))
    return out

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            return deep_merge(DEFAULT_SETTINGS, json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    settings["items_per_page"] = int(settings.get("columns", 4)) * int(settings.get("rows_per_page", 4))
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(SETTINGS_FILE.suffix + ".tmp")
    data = json.dumps(settings, ensure_ascii=False, indent=2)
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(SETTINGS_FILE)
