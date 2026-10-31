"""Settings changed by a page: only the keys pages may change, applied where the settings live.

In the program window the settings are the window's dict in memory — the window applies the change
in its own thread (``AppBridge``, action ``save_settings``) and the page waits until it is done,
so the next page already shows it.  Without a window (``python -m webui``) the settings file is
changed directly.
"""
from __future__ import annotations

import threading

# Keys a page may change; anything else in a request is dropped (ui/web_bridge.py checks it again).
PAGE_KEYS = frozenset({
    "root", "parser_recheck_new_sites", "parser_recheck_nomatch_only",
    "sites", "custom_sites", "site_manual_order", "deleted_builtin_sites",
    "enable_iqdb", "enable_danbooru_iqdb", "enable_e621_iqdb", "enable_ascii2d", "enable_saucenao",
    "saucenao_api_key", "min_similarity", "iqdb_min_similarity", "saucenao_quota_spread_seconds", "reverse_saucenao_first",
    "parser_workers", "parser_reverse_workers", "parser_md5_batch_enabled", "parser_md5_batch_size",
    "parser_retry_deferred_minutes", "skip_existing",
    "grabber_subscriptions_blocklist", "downloader_blocklist", "grabber_preview_hide_existing",
    "duplicate_visual_threshold",
    # «Настройки» (webui/pages/settings.py; tools/dev_checks/webui_settings.py checks that every field is here)
    "ui_shell", "webview_gpu", "webui_posts_per_page", "imports_to_inbox", "inbox_auto_archive_hours", "deleted_reimport_policy", "trash_auto_purge_days",
    "hide_single_char_tags", "hide_technical_tags", "hide_meta_tags", "hide_rating_tags", "ignore_numeric_tags",
    "light_backup_enabled", "light_backup_include_cookies", "light_backup_interval_hours", "light_backup_keep_last",
    "light_backup_dir", "light_backup_last_at", "light_backup_on_exit",
    "thumb_cleanup_on_exit", "thumb_keep_recent",
    "large_download_warning_count", "disk_free_reserve_gb", "grabber_cache_limit_mb",
    "session_media_downloads_default", "session_media_downloads_prompt",
    "external_browser_mode", "external_browser_executable",
    "performance_slow_ms", "sqlite_cache_mb", "sqlite_checkpoint_on_exit", "sqlite_idle_checkpoint_enabled",
    "sqlite_idle_checkpoint_threshold_mb", "sqlite_idle_checkpoint_interval_seconds", "derived_cache_external_enabled", "manga_root",
    "visual_nomatch_classify_enabled", "visual_nomatch_auto_download_model", "visual_nomatch_model",
    "visual_nomatch_min_confidence", "video_root", "video_tab_seconds", "video_source_aniliberty", "aniliberty_url",
    "video_source_dubs", "yummy_api_url", "video_dub_merge",
    "post_sets_enabled",
    "card_sets_tiles", "card_sets_auto", "card_sets_ask_sites", "card_sets_remote",
})
# Settings whose value a page may set by asking the window for a folder or a file (``choose_path``):
# key -> (folder or file, the dialog's title, the file filter).
PATH_KEYS = {
    "light_backup_dir": ("folder", "Папка для резервных копий", ""),
    "external_browser_executable": ("file", "Браузер", "Программы (*.exe);;Все файлы (*)"),
    "manga_root": ("folder", "Папка с мангой", ""),
    "video_root": ("folder", "Папка видео: одна папка — один сериал", ""),
}
_FILE_LOCK = threading.Lock()


def for_task(settings: dict, **extra) -> dict:
    """A copy of the settings for work a page starts in a thread — without the running program's «_…»
    state: a copy keeps the moment's ``_parser_running`` forever, and the maintenance lane then waits
    for a parser that ended long ago."""
    out = {k: v for k, v in dict(settings or {}).items() if not str(k).startswith("_")}
    out.update(extra)
    return out


def save(changes: dict, *, wait: float = 5.0) -> bool:
    """Apply ``changes`` (allowed keys only); True when they are in place."""
    changes = {k: v for k, v in dict(changes or {}).items() if k in PAGE_KEYS}
    if not changes:
        return True
    from webui import service
    outcome = service.call_window("save_settings", {"changes": changes}, wait=wait, finish_wait=30.0)
    if outcome != "no_window":
        return outcome == "done"           # not taken in time: dropped, the window will not apply it later
    from core.settings import load_settings, save_settings
    with _FILE_LOCK:
        current = load_settings()
        current.update(changes)
        save_settings(current)
    return True
