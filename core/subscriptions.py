"""Per-author subscription system for Local Booru.

Subscriptions = автоматическое скачивание по автору/тегу.

Хранение: data/settings/subscriptions.json
Каждая подписка:
  {
    "id":   "sub_001",
    "name": "seraziel",
    "sites": [
      {"site": "danbooru.donmai.us", "priority": 5},
      {"site": "rule34.xxx",         "priority": 4},
      {"site": "gelbooru.com",       "priority": 3}
    ],
    "query":               "artist:seraziel",
    "enabled":             true,
    "blacklist_tags":      [],
    "last_post_ids":       {"danbooru.donmai.us": 12345, "rule34.xxx": 0},
    "last_check":          1716800000,
    "check_interval_hours": 24,
    "max_pages":           3,
    "downloaded_count":    0,
    "created_at":          1716800000
  }

Backward compat: старые подписки с одиночным полем "site" читаются корректно.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from core.paths import SETTINGS_DIR

SUBS_FILE = SETTINGS_DIR / "subscriptions.json"

# Default site priorities (used when creating new subscriptions)
DEFAULT_SITE_PRIORITY: dict[str, int] = {
    "danbooru.donmai.us":       5,
    "e621.net":                 5,
    "e926.net":                 5,
    "booru.allthefallen.moe":   4,
    "gelbooru.com":             4,
    "rule34.xxx":               3,
    "rule34.us":                3,
    "hypnohub.net":             3,
    "xbooru.com":               2,
}


# ── Normalization helpers ─────────────────────────────────────────────────────

def normalize_sites(sub: dict) -> list[dict]:
    """Return sites list sorted by priority desc, with backward compat."""
    sites = sub.get("sites")
    if sites and isinstance(sites, list) and all(isinstance(s, dict) for s in sites):
        return sorted(sites, key=lambda x: -int(x.get("priority", 1)))
    # Backward compat: old single "site" field
    site = sub.get("site", "")
    if site:
        return [{"site": site, "priority": DEFAULT_SITE_PRIORITY.get(site, 3)}]
    return []


def normalize_last_post_ids(sub: dict) -> dict[str, int]:
    """Return per-site last_post_ids dict with backward compat."""
    ids = sub.get("last_post_ids")
    if ids and isinstance(ids, dict):
        return {k: int(v) for k, v in ids.items()}
    # Backward compat: single "last_post_id"
    old_id = int(sub.get("last_post_id", 0))
    sites = normalize_sites(sub)
    return {s["site"]: old_id for s in sites}


def normalize_blacklist_tags(values) -> list[str]:
    """Accept comma/newline/semicolon separated blacklist values from old and new UI."""
    import re
    if isinstance(values, str):
        raw = values
    else:
        raw = "\n".join(str(v) for v in (values or []))
    out: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[,;\s]+", raw):
        tag = token.strip().lower()
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


# ── Storage ───────────────────────────────────────────────────────────────────

def load_subscriptions() -> list[dict]:
    try:
        return json.loads(SUBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_subscriptions(subs: list[dict]) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SUBS_FILE.write_text(
        json.dumps(subs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_subscription(sub_id: str) -> dict | None:
    for s in load_subscriptions():
        if s.get("id") == sub_id:
            return s
    return None


def add_subscription(name: str, sites: list[dict], query: str, **kwargs) -> dict:
    """sites = [{"site": "danbooru.donmai.us", "priority": 5}, ...]"""
    sub = {
        "id":                   f"sub_{uuid.uuid4().hex[:8]}",
        "name":                 name.strip(),
        "sites":                sites,
        "query":                query.strip(),
        "enabled":              True,
        "blacklist_tags":       kwargs.get("blacklist_tags", []),
        "last_post_ids":        {s["site"]: 0 for s in sites},
        "oldest_post_ids":      {s["site"]: 0 for s in sites},
        "run_mode":             kwargs.get("run_mode", "all"),
        "run_direction":        kwargs.get("run_direction", "newest_to_oldest"),
        "last_check":           0,
        "check_interval_hours": kwargs.get("check_interval_hours", 24),
        "max_pages":            kwargs.get("max_pages", 3),
        "downloaded_count":     0,
        "created_at":           int(time.time()),
    }
    subs = load_subscriptions()
    subs.append(sub)
    save_subscriptions(subs)
    return sub


def update_subscription(sub_id: str, **fields) -> bool:
    subs = load_subscriptions()
    for i, s in enumerate(subs):
        if s.get("id") == sub_id:
            subs[i].update(fields)
            save_subscriptions(subs)
            return True
    return False


def delete_subscription(sub_id: str) -> bool:
    subs = load_subscriptions()
    new = [s for s in subs if s.get("id") != sub_id]
    if len(new) < len(subs):
        save_subscriptions(new)
        return True
    return False


def due_subscriptions() -> list[dict]:
    now = int(time.time())
    result = []
    for s in load_subscriptions():
        if not s.get("enabled", True):
            continue
        interval_sec = int(s.get("check_interval_hours", 24)) * 3600
        last = int(s.get("last_check", 0))
        if now - last >= interval_sec:
            result.append(s)
    return result


# ── Runner ────────────────────────────────────────────────────────────────────

def _extract_post_tags(post: dict) -> list[str]:
    """Extract flat tag list from any booru post dict."""
    # ATF/modern Danbooru uses tag_string; older APIs use tags
    raw = post.get("tag_string") or post.get("tags", "")
    if isinstance(raw, str):
        tags = raw.split()
    elif isinstance(raw, dict):
        # e621: {"general": [...], "artist": [...], ...}
        tags = []
        for v in raw.values():
            if isinstance(v, list):
                tags.extend(str(t) for t in v)
    elif isinstance(raw, list):
        tags = [str(t) for t in raw]
    else:
        tags = []
    return [t.strip() for t in tags if t.strip() and not t.strip().startswith("-")]


def _extract_post_groups(post: dict) -> dict[str, list[str]]:
    """Preserve grouped API tags, especially official e621/e926 categories."""
    out = {
        "artist": [], "contributor": [], "character": [], "copyright": [],
        "species": [], "general": [], "meta": [], "lore": [], "invalid": [],
    }
    if not isinstance(post, dict):
        return out
    raw = post.get("tags")
    if isinstance(raw, dict):
        for group in out:
            vals = raw.get(group, [])
            if isinstance(vals, list):
                out[group].extend(str(v).strip() for v in vals if str(v).strip())
        return out
    field_map = {
        "artist": "tag_string_artist", "character": "tag_string_character",
        "copyright": "tag_string_copyright", "species": "tag_string_species",
        "general": "tag_string_general", "meta": "tag_string_meta",
        "lore": "tag_string_lore", "invalid": "tag_string_invalid",
    }
    for group, field in field_map.items():
        value = post.get(field, "")
        if isinstance(value, str):
            out[group].extend(value.split())
    if not any(out.values()):
        out["general"] = _extract_post_tags(post)
    return out


def _post_source_url(site: str, post: dict) -> str:
    pid = post.get("id", "")
    return f"https://{site}/posts/{pid}" if pid else ""


def _candidate_file_url(site: str, post: dict) -> str:
    try:
        from core.downloader_utils import _file_url
        return _file_url(post, site) or ""
    except Exception:
        return _post_media_url(site, post) or ""


def _index_and_tag(dest_path, candidates: list, settings: dict, log) -> None:
    """Write merged tags + all post/file sources to SQLite.

    A subscription group is parser-like: the file is downloaded once from the
    best working source, but metadata from every same-MD5 candidate is kept.
    This prevents rule34/e621/ATF metadata from being lost just because another
    site had the better download priority.
    """
    from pathlib import Path
    from core.import_pipeline import register_media_import

    ordered = sorted(candidates, key=lambda x: -x[0])

    # Merge tags from all candidate sites while preserving real API categories.
    all_groups = {
        "artist": [], "contributor": [], "character": [], "copyright": [],
        "species": [], "general": [], "meta": [], "lore": [], "invalid": [],
    }
    all_tags: list[str] = []
    seen_tags: set[str] = set()
    for _priority, _site, post in ordered:
        groups = _extract_post_groups(post)
        for group, values in groups.items():
            for tag in values:
                key = tag.lower()
                if key not in seen_tags:
                    seen_tags.add(key)
                    all_tags.append(tag)
                    all_groups[group].append(tag)

    # Preserve all sources, not only the site that successfully downloaded.
    all_sources: list[str] = []
    seen_sources: set[str] = set()
    metadata_sites: list[str] = []
    for _priority, site, post in ordered:
        if site and site not in metadata_sites:
            metadata_sites.append(site)
        for url in (_post_page_url(site, post), _candidate_file_url(site, post)):
            if url and url not in seen_sources:
                seen_sources.add(url)
                all_sources.append(url)

    if not all_tags:
        log(f"  TAG WARN: no tags found for {Path(dest_path).name}, candidates={[s for _,s,_ in candidates]}")

    # Extract MD5 from best candidate.
    best_md5 = ""
    for _, _, post in ordered:
        best_md5 = _post_md5_from(post)
        if best_md5:
            break

    source_text = "\n".join(all_sources)
    post_url = all_sources[0] if all_sources else ""
    file_url = all_sources[1] if len(all_sources) > 1 else ""

    try:
        result = register_media_import(
            settings,
            Path(dest_path),
            tags=all_tags,
            groups=all_groups,
            sources=all_sources,
            status="tagged",
            post_url=post_url,
            file_url=file_url,
            site=metadata_sites[0] if metadata_sites else "",
            hash_md5=best_md5 or None,
            merge_existing=True,
            origin="subscription",
            raw={
                "subscription_metadata_sites": metadata_sites,
                "subscription_candidates": [
                    {"site": site, "post_id": _post_id_from(post), "md5": _post_md5_from(post)}
                    for _priority, site, post in ordered
                ],
            },
        )
        if result.get("action") == "skip_deleted":
            log("  SKIP DELETED: exact MD5 was permanently removed earlier")
            return
        log(
            f"  TAGGED: {Path(dest_path).name} ({len(all_tags)} tags; "
            f"sources={', '.join(metadata_sites) or 'none'})"
        )
    except Exception as e:
        log(f"  TAG ERROR: {e}")


def _post_id_from(post: dict) -> int:
    try:
        return int(post.get("id") or post.get("post_id") or 0)
    except Exception:
        return 0


def _post_media_url(site: str, post: dict, settings: dict | None = None) -> str:
    import re as _re
    try:
        from core.downloader_utils import _file_url
        url = _file_url(post, site)
        if url:
            return url
    except Exception:
        pass
    # ATF: deleted/invisible posts lack file_url (visible?=false).
    # Use cached session (has PoW+auth cookies) to fetch post HTML.
    try:
        host = str(site).lower().replace("https://","").replace("http://","").replace("www.","").split("/")[0]
        if "allthefallen" not in host:
            return ""
        pid = _post_id_from(post)
        if not pid:
            return ""
        from core.downloader_utils import _get_or_create_session, _rate_limited_get
        s = _get_or_create_session(host, settings or {}, log=None)
        page_url = "https://" + host + "/posts/" + str(pid)
        r = _rate_limited_get(s, page_url, settings=settings or {}, timeout=15, headers={"Accept": "text/html"})
        if not r.ok:
            return ""
        html = r.text
        ALL_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm")
        STRIP = ".,;)> \t"
        for u in _re.findall(r"https://\S+", html):
            u = u.strip(STRIP)
            if any(u.lower().endswith(ext) for ext in ALL_EXTS):
                if "/data/" in u or "static." in u:
                    return u
    except Exception:
        pass
    return ""

def _post_page_url(site: str, post: dict) -> str:
    pid = _post_id_from(post)
    if not pid:
        return ""
    host = str(site).lower().replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
    if "gelbooru" in host or host in {"rule34.xxx", "rule34.us", "xbooru.com", "hypnohub.net"}:
        return f"https://{host}/index.php?page=post&s=view&id={pid}"
    if "e621" in host or "e926" in host:
        return f"https://{host}/posts/{pid}"
    return f"https://{host}/posts/{pid}"


def run_subscription(sub: dict, settings: dict,
                     log=None, progress=None, stop_flag=None,
                     on_file_ready=None, run_mode: str = "all",
                     confirm_plan=None) -> int:
    """Run one subscription using a Hydrus-like seed-cache/import pipeline.

    Pipeline:
      1. Scan gallery/API pages through the same parser/downloader site helpers.
      2. Store every candidate in a durable seed cache.
      3. Build import groups from pending seeds, grouped by MD5 or site/post id.
      4. Download through the shared downloader, validate bytes, dedup, tag.
      5. Mark seed status: downloaded / skipped_duplicate / failed.

    This avoids the old fragile behavior where a subscription found a post and
    immediately forgot all context after a failed or interrupted download.
    """
    from core.downloader_utils import fetch_posts_for_query, download_post_file, clear_session_cache
    from core.subscription_engine.seed_cache import (
        upsert_seed, candidate_seeds, mark_seed, mark_many, stats_for_subscription,
        start_run, finish_run,
    )
    from core.imports.subscription_queue import build_import_groups

    log = log or (lambda m: None)
    sites_cfg = normalize_sites(sub)
    last_ids = normalize_last_post_ids(sub)
    oldest_ids = sub.get("oldest_post_ids") if isinstance(sub.get("oldest_post_ids"), dict) else {}
    query = sub.get("query", "")
    max_pages = int(sub.get("max_pages", 3))
    global_blacklist = normalize_blacklist_tags((settings or {}).get("grabber_subscriptions_blocklist") or (settings or {}).get("downloader_blocklist") or [])
    local_blacklist = normalize_blacklist_tags(sub.get("blacklist_tags") or [])
    blacklist = list(dict.fromkeys(global_blacklist + local_blacklist))
    run_mode = run_mode or sub.get("run_mode", "all") or "all"
    direction = sub.get("run_direction", "newest_to_oldest") or "newest_to_oldest"
    sub_id = sub.get("id") or ""
    sub_name = sub.get("name") or sub_id or "subscription"

    if not sites_cfg:
        log(f"SUB [{sub_name}]: no sites configured, skipping")
        return 0

    from datetime import datetime as _dt
    clear_session_cache()
    session_ts = _dt.now().strftime("%Y-%m-%d_%H-%M")
    log(f"SUB [{sub_name}]: scan → seed cache → import queue; mode={run_mode}, direction={direction}, query='{query}'")
    run_id = start_run(sub_id, sub_name, run_mode, direction)

    new_last_ids: dict[str, int] = dict(last_ids)
    new_oldest_ids: dict[str, int] = {str(k): int(v or 0) for k, v in oldest_ids.items()}
    discovered = 0

    # 1) Scan sites and write seeds.
    for site_cfg in sites_cfg:
        site = site_cfg["site"]
        priority = int(site_cfg.get("priority", 1))
        since_id = int(last_ids.get(site, 0) or 0)
        if run_mode == "old":
            since_id = int(new_oldest_ids.get(site, 0) or since_id or 0)

        if stop_flag and getattr(stop_flag, "_stop_requested", False):
            log("  SUB: остановка по запросу")
            break

        site_query = site_cfg.get("query_override", "").strip() or query
        if site_query != query:
            log(f"  SCAN [{site}] priority={priority}, checkpoint={since_id}, query='{site_query}'")
        else:
            log(f"  SCAN [{site}] priority={priority}, checkpoint={since_id}")
        try:
            posts, highest = fetch_posts_for_query(
                site=site,
                query=site_query,
                settings=settings,
                since_post_id=since_id,
                max_pages=max_pages,
                blacklist_tags=None,  # filtered after same-file metadata is merged
                run_mode=run_mode,
                log=log,
            )
            if not posts:
                log(f"  SCAN [{site}]: 0 новых постов найдено (query={query!r})")
        except Exception as e:
            log(f"  SCAN [{site}] ERROR: {e}")
            continue

        # New-only first run is baseline-only.  It must not import old history.
        if run_mode == "new" and int(last_ids.get(site, 0) or 0) <= 0:
            if highest > 0:
                new_last_ids[site] = highest
                log(f"  BASELINE [{site}]: newest post id = {highest}; старые посты не качаю")
            continue

        if highest > int(new_last_ids.get(site, 0) or 0):
            new_last_ids[site] = highest

        real_ids = [_post_id_from(p) for p in posts if _post_id_from(p) > 0]
        if real_ids:
            old_min = int(new_oldest_ids.get(site, 0) or 0)
            page_min = min(real_ids)
            if old_min <= 0 or page_min < old_min:
                new_oldest_ids[site] = page_min

        for post in posts:
            post_id = _post_id_from(post)
            md5 = _post_md5_from(post)
            file_url = _post_media_url(site, post, settings=settings)
            post_url = _post_page_url(site, post)
            if not file_url:
                seed = upsert_seed(
                    subscription_id=sub_id, subscription_name=sub_name, site=site,
                    query=query, post=post, priority=priority, file_url="",
                    post_url=post_url, md5=md5, post_id=post_id,
                )
                # Only log first occurrence — don't spam on every re-scan
                if seed.get("status") != "skipped_no_file":
                    log(f"  SKIP [{site}]: no media URL for post {post_id}")
                mark_seed(seed["key"], "skipped_no_file", error="no media url")
                continue
            seed = upsert_seed(
                subscription_id=sub_id, subscription_name=sub_name, site=site,
                query=query, post=post, priority=priority, file_url=file_url,
                post_url=post_url, md5=md5, post_id=post_id,
            )
            if seed.get("status") == "pending":
                discovered += 1

    stats = stats_for_subscription(sub_id)
    log("  SEED CACHE: " + ", ".join(f"{k}={v}" for k, v in sorted(stats.items())) if stats else "  SEED CACHE: empty")

    downloaded = 0
    skipped = 0
    failed = 0
    queued_total = 0
    batch_no = 0
    batch_limit = max(1000, max_pages * 200)
    scan_was_stopped = bool(stop_flag and getattr(stop_flag, "_stop_requested", False))
    stop_import = False

    # Large-import preflight: warn once after scanning, before the first download.
    # Counts refer to grouped files, so the same MD5 found on several sites is
    # not presented as several downloads.
    try:
        from core.preflight import build_large_download_plan, format_bytes
        _ready = candidate_seeds(sub_id, include_failed=True, limit=0)
        _all_groups = build_import_groups(_ready, direction=direction)
        _plan = build_large_download_plan(settings, _all_groups)
        if _plan.get("warn"):
            _disk = _plan.get("disk", {})
            log(
                f"  PREFLIGHT: files={_plan.get('groups', 0)}, "
                f"known_size={format_bytes(_plan.get('known_bytes', 0))} "
                f"for {_plan.get('known_files', 0)} file(s), "
                f"disk_free={format_bytes(_disk.get('free', 0))}"
            )
            if confirm_plan is not None and not bool(confirm_plan(_plan)):
                log("  SUB: массовая загрузка отменена пользователем до скачивания файлов")
                finish_run(run_id, status="cancelled", found=discovered, queued=len(_all_groups), downloaded=0, skipped=0, failed=0, note="preflight cancelled")
                return 0
    except Exception as _preflight_error:
        log(f"  PREFLIGHT WARN: {_preflight_error}")

    # Keep memory bounded but process all ready candidates in this same run.
    # Previously only the first queue slice was imported on very large tags.
    while not stop_import:
        seeds = candidate_seeds(sub_id, include_failed=True, limit=batch_limit)
        groups = build_import_groups(seeds, direction=direction)
        if not groups:
            if scan_was_stopped and batch_no == 0:
                log("  SUB: остановка — import пуст, seeds сохранены для следующего запуска")
            break
        batch_no += 1
        queued_total += len(groups)
        log(f"  IMPORT QUEUE batch {batch_no}: {len(groups)} grouped candidate(s) from {len(seeds)} seed(s)")
        try:
            by_site: dict[str, int] = {}
            by_group_site: dict[str, int] = {}
            merged_groups = 0
            for group in groups:
                sites_in_group = sorted({str(s.get("site") or "") for s in group if s.get("site")})
                if len(sites_in_group) > 1:
                    merged_groups += 1
                for seed in group:
                    site_name = str(seed.get("site") or "unknown")
                    by_site[site_name] = by_site.get(site_name, 0) + 1
                best_site = str(group[0].get("site") or "unknown") if group else "unknown"
                by_group_site[best_site] = by_group_site.get(best_site, 0) + 1
            if by_site:
                log("  QUEUE SEEDS BY SITE: " + ", ".join(f"{k}={v}" for k, v in sorted(by_site.items())))
                log("  QUEUE DOWNLOAD PLAN: " + ", ".join(f"{k}={v}" for k, v in sorted(by_group_site.items())))
                log(f"  QUEUE MERGED GROUPS: {merged_groups}")
        except Exception:
            pass

        for group in groups:
            if stop_flag and getattr(stop_flag, "_stop_requested", False):
                log("  SUB: остановка по запросу; оставшиеся seeds продолжатся при следующем запуске")
                stop_import = True
                break

            best = group[0]
            real_md5 = str(best.get("md5") or "").lower()
            group_keys = [g.get("key") for g in group if g.get("key")]
            candidates = [
                (int(s.get("priority") or 1), s.get("site") or "", s.get("post") or {})
                for s in group
            ]

            # Apply blacklist after all same-file metadata sources are merged.
            # Otherwise an untagged copy from another site can bypass an excluded tag.
            if blacklist:
                merged_tags = {t.lower() for _p, _site, post in candidates for t in _extract_post_tags(post)}
                blocked = sorted(merged_tags.intersection(set(blacklist)))
                if blocked:
                    log(f"  SKIP (blacklist merged: {', '.join(blocked)}; sources={', '.join(sorted({site for _p, site, _post in candidates if site}))})")
                    skipped += 1
                    mark_many(group_keys, "skipped_blacklist", error="blocked tag: " + ", ".join(blocked))
                    continue

            if real_md5:
                try:
                    from core.database.storage import found_media_path_by_md5
                    existing_path = found_media_path_by_md5(settings, real_md5)
                    if existing_path:
                        # The file is already downloaded, but a new site may provide
                        # tags or source links that are not in the library yet.
                        _index_and_tag(existing_path, candidates, settings, log)
                        log(f"  METADATA MERGED (already in library md5={real_md5[:8]}…)")
                        skipped += 1
                        mark_many(group_keys, "skipped_duplicate", error="md5 already in library; metadata merged", path=existing_path)
                        continue
                except Exception as e:
                    log(f"  METADATA MERGE WARN: {e}")

            imported = False
            last_error = ""
            for seed in group:
                site = seed.get("site") or ""
                post = seed.get("post") or {}
                key = seed.get("key") or ""
                if key:
                    mark_seed(key, "downloading")
                try:
                    ok, dest_path = download_post_file(
                        site=site,
                        post=post,
                        settings=settings,
                        query=query,
                        session_folder=session_ts,
                        log=log,
                    )
                except Exception as e:
                    ok, dest_path = False, None
                    last_error = str(e)

                if ok and dest_path:
                    downloaded += 1
                    imported = True
                    if progress:
                        progress(downloaded)
                    try:
                        sites_joined = sorted({site for _prio, site, _post in candidates if site})
                        if len(sites_joined) > 1:
                            log(f"  GROUP METADATA: download_source={site}; metadata_sources={', '.join(sites_joined)}")
                    except Exception:
                        pass
                    _index_and_tag(dest_path, candidates, settings, log)
                    mark_many(group_keys, "downloaded", path=str(dest_path))
                    if on_file_ready:
                        on_file_ready(str(dest_path))
                    break
                elif dest_path:
                    # File exists on disk but may be missing from the index or may
                    # have gained metadata from another site. Do not throw that
                    # information away just because download itself was skipped.
                    try:
                        _index_and_tag(dest_path, candidates, settings, log)
                    except Exception as e:
                        log(f"  METADATA MERGE WARN (existing disk file): {e}")
                    log(f"  SKIP (already on disk; metadata merged): {dest_path.name}")
                    skipped += 1
                    mark_many(group_keys, "skipped_duplicate", error="file already on disk; metadata merged", path=str(dest_path))
                    imported = True  # don't try lower-priority fallbacks
                    break
                else:
                    last_error = last_error or "download failed or duplicate in output"
                    if key:
                        status = "auth_required" if ("401" in last_error or "403" in last_error or "auth" in last_error.lower()) else "failed_temp"
                        mark_seed(key, status, error=last_error)

            if not imported and last_error:
                failed += 1
                log(f"  IMPORT FAIL: {last_error}")

        if stop_flag and getattr(stop_flag, "_stop_requested", False):
            stop_import = True

    finish_run(
        run_id,
        status="done",
        found=discovered,
        queued=queued_total,
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        note="",
    )

    update_subscription(
        sub_id,
        last_post_ids=new_last_ids,
        oldest_post_ids=new_oldest_ids,
        last_check=int(time.time()),
        downloaded_count=sub.get("downloaded_count", 0) + downloaded,
        run_mode=run_mode,
        run_direction=direction,
    )
    log(f"SUB [{sub_name}]: done, {downloaded} new files")
    return downloaded

def _post_md5_from(post: dict) -> str:
    v = post.get("md5") or post.get("hash") or ""
    if v and isinstance(v, str):
        return v.strip().lower()
    f = post.get("file", {})
    if isinstance(f, dict):
        v = f.get("md5", "")
        if v:
            return str(v).strip().lower()
    return ""
