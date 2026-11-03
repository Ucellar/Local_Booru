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
import threading
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


SCAN_RETRY_SECONDS = 30 * 60       # every site failed: try again this soon, not after the whole interval
# One change of the subscriptions file at a time: a check ends (checkpoint, counter) while a page
# saves an edit — read, change, write must not interleave, or one of them is lost.
_LOCK = threading.RLock()

# ── Storage ───────────────────────────────────────────────────────────────────

def load_subscriptions() -> list[dict]:
    """The subscriptions; [] only when there are none.  A damaged file is kept aside
    (``subscriptions.json.broken-<time>``) and the last good copy is used: before 5.3.1 a
    damaged file read as «no subscriptions», and the next save wrote that over it."""
    from core.file_safety import read_json_kept
    with _LOCK:          # not while a save replaces the file (on Windows the replace fails on an open file)
        data = read_json_kept(SUBS_FILE, list, what="Subscriptions file")
    return [s for s in data if isinstance(s, dict)] if data else []


def save_subscriptions(subs: list[dict]) -> None:
    """Atomic write; the previous version stays as ``subscriptions.json.bak``."""
    from core.file_safety import write_json_kept
    write_json_kept(SUBS_FILE, json.dumps(subs, ensure_ascii=False, indent=2), list)


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
    with _LOCK:
        subs = load_subscriptions()
        subs.append(sub)
        save_subscriptions(subs)
    return sub


def same_search(a, b) -> bool:
    """Two queries ask the same search: the same words, in any order and case."""
    return sorted(str(a or "").lower().split()) == sorted(str(b or "").lower().split())


def find_search(subs: list[dict], site: str, query: str) -> tuple[dict | None, bool]:
    """The subscription that asks ``query`` — (it, whether it asks it on ``site``).  On a site a subscription may ask
    with its own query (``query_override``); one that asks this search on this site wins over one that asks it
    elsewhere."""
    elsewhere = None
    for sub in subs:
        for entry in normalize_sites(sub):
            if entry.get("site") == site and same_search(entry.get("query_override") or sub.get("query"), query):
                return sub, True
        if elsewhere is None and same_search(sub.get("query"), query):
            elsewhere = sub
    return elsewhere, False


def subscribe_to_search(site: str, query: str, **kwargs) -> tuple[dict, str]:
    """A subscription to ``query`` on ``site`` (the extension's «Подписаться», v5.38): (the subscription, what was
    done — ``"exists"``, ``"added_site"`` — the site joins the subscription to the same search, from where it is
    with a checkpoint of its own — or ``"created"``).  Finding and changing happen under one lock: two clicks make
    one subscription."""
    entry = {"site": site, "priority": DEFAULT_SITE_PRIORITY.get(site, 3)}
    with _LOCK:
        subs = load_subscriptions()
        sub, here = find_search(subs, site, query)
        if sub is not None and here:
            return sub, "exists"
        if sub is not None:
            last = normalize_last_post_ids(sub)          # (an old subscription's single checkpoint is kept)
            oldest = sub.get("oldest_post_ids") if isinstance(sub.get("oldest_post_ids"), dict) else {}
            sub["sites"] = normalize_sites(sub) + [entry]
            sub["last_post_ids"] = {**last, site: 0}
            sub["oldest_post_ids"] = {**oldest, site: 0}
            save_subscriptions(subs)
            return sub, "added_site"
        return add_subscription(query, [entry], query, **kwargs), "created"


def update_subscription(sub_id: str, **fields) -> bool:
    with _LOCK:
        subs = load_subscriptions()
        for i, s in enumerate(subs):
            if s.get("id") == sub_id:
                subs[i].update(fields)
                save_subscriptions(subs)
                return True
    return False


def record_run(sub_id: str, *, downloaded: int = 0, **fields) -> bool:
    """The result of a check, written over the subscription as it is NOW: its counter grows by
    ``downloaded`` and only the given fields (checkpoint, time, error) change — an edit saved on a
    page while the check was going is kept."""
    with _LOCK:
        subs = load_subscriptions()
        for s in subs:
            if s.get("id") == sub_id:
                s.update(fields)
                s["downloaded_count"] = int(s.get("downloaded_count", 0) or 0) + int(downloaded or 0)
                save_subscriptions(subs)
                return True
    return False


def delete_subscription(sub_id: str) -> bool:
    with _LOCK:
        subs = load_subscriptions()
        new = [s for s in subs if s.get("id") != sub_id]
        if len(new) == len(subs):
            return False
        save_subscriptions(new)
    from core.subscription_engine.seed_cache import clear_seeds_for_subscription
    clear_seeds_for_subscription(sub_id)        # its found-post list goes with it
    return True


def next_check_at(sub: dict) -> int:
    """When the automatic check takes this subscription next (0 — at the first one)."""
    last = int(sub.get("last_check", 0) or 0)
    return last + max(1, int(sub.get("check_interval_hours", 24) or 24)) * 3600 if last > 0 else 0


def due_subscriptions() -> list[dict]:
    now = int(time.time())
    return [s for s in load_subscriptions() if s.get("enabled", True) and next_check_at(s) <= now]


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
        # (v5.44.11) rule34.xxx sends the category of every tag with the post (``fields=tag_info``: the grabber's and
        # the subscriptions' searches ask for it) — before, every tag of a download from there was «Общие»
        kinds = _tag_info_kinds(post)
        for tag in _extract_post_tags(post):
            group = kinds.get(tag, "general")
            out[group if group in out else "general"].append(tag)
    return out


def _tag_info_kinds(post: dict) -> dict[str, str]:
    """{tag: its category} of a rule34.xxx post's ``tag_info`` ([{"tag", "type", "count"}]); {} — none sent."""
    rows = post.get("tag_info") if isinstance(post, dict) else None
    if not isinstance(rows, list):
        return {}
    from core.tagger.tag_groups import group_from_tag_type
    kinds: dict[str, str] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("type") is not None:
            name = str(row.get("tag") or row.get("name") or "").strip()
            if name:
                kinds[name] = group_from_tag_type(row.get("type"))
    return kinds


def _post_source_url(site: str, post: dict) -> str:
    pid = post.get("id", "")
    return f"https://{site}/posts/{pid}" if pid else ""


def _candidate_file_url(site: str, post: dict) -> str:
    from core.downloader_utils import original_media_urls
    urls = original_media_urls(post, site)
    return urls[0] if urls else ""


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
    # One tag set per site, each under its own post: the card shows every site's tags, and a
    # later merge of one site replaces only that site's set.
    source_tag_groups = []
    for _priority, site, post in ordered:
        page = _post_page_url(site, post)
        groups = _extract_post_groups(post)
        if page and any(groups.values()):
            source_tag_groups.append({"url": page, "groups": groups, "method": "subscription"})

    # (v5.44.49) a site that sends no categories (Gelbooru's posts, Moebooru): its tags are written «general», as it said
    # — the card's page shows them in the categories most sites give them (5.44.11 wrote the library's categories into
    # the set, and a wrong category of the library went into every download with them)

    from core.site_posts import rows_from_candidates
    try:
        site_posts = rows_from_candidates(settings, ordered)
    except Exception as exc:  # a lookup in the site table: the card is written without them (asked later)
        log(f"  SETS WARN: {type(exc).__name__}: {exc}")
        site_posts = []
    try:
        result = register_media_import(
            settings,
            Path(dest_path),
            site_posts=site_posts,
            tags=all_tags,
            groups=all_groups,
            sources=all_sources,
            status="tagged",
            post_url=post_url,
            file_url=file_url,
            site=metadata_sites[0] if metadata_sites else "",
            hash_md5=best_md5 or None,
            source_tag_groups=source_tag_groups or None,
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
        if result.get("action") == "skip_trashed":
            log("  SKIP: this file is in Trash (restore it there to bring it back)")
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


_REDUCED_PATH = ("/sample/", "/preview/", "/180x180/", "/360x360/", "/720x720/", "/thumbnails/", "/samples/")


def _post_media_url(site: str, post: dict, settings: dict | None = None) -> str:
    """The original file of a post; "" when the site gives only a sample or preview (such a post
    is not downloaded: a sample under the post's MD5 would stand in for the original for good)."""
    import re as _re
    from core.downloader_utils import original_media_urls
    urls = original_media_urls(post, site)
    if urls:
        return urls[0]
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
                if ("/data/" in u or "static." in u) and not any(part in u.lower() for part in _REDUCED_PATH):
                    return u
    except Exception:
        pass
    return ""

def _post_page_url(site: str, post: dict) -> str:
    """The post's page on its site, as the parser builds it (moebooru ``/post/show/N``, rule34.us
    ``?r=posts/view``…); ``/posts/N`` for a site of an unknown engine."""
    from core.parser.sites import post_page_url
    pid = _post_id_from(post)
    if not pid:
        return ""
    host = str(site).lower().replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
    return post_page_url(host, pid) or f"https://{host}/posts/{pid}"


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
    from core.downloader_utils import fetch_posts_for_query, download_post_file, clear_session_cache, original_media_urls
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
    max_posts = int(sub.get("max_posts", 0) or 0)       # «Скачать тег»: at most this many posts per site
    given = sub.get("given_posts") if isinstance(sub.get("given_posts"), dict) else None   # {site: [post]}
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
    scan_failed: dict[str, str] = {}        # site -> why it did not answer at all

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
        fetch_status: dict = {}
        try:
            if given is not None:             # the grabber: the posts the user chose, no scan
                posts = [p for p in given.get(site) or [] if isinstance(p, dict)]
                highest = max((_post_id_from(p) for p in posts), default=0)
                fetch_status["pages_ok"] = 1
            else:
                posts, highest = fetch_posts_for_query(
                    site=site,
                    query=site_query,
                    settings=settings,
                    since_post_id=since_id,
                    max_pages=max_pages,
                    blacklist_tags=None,  # filtered after same-file metadata is merged
                    run_mode=run_mode,
                    log=log,
                    status=fetch_status,
                    max_posts=max_posts,
                )
        except Exception as e:
            log(f"  SCAN [{site}] ERROR: {e}")
            scan_failed[site] = str(e)
            continue
        if fetch_status.get("error") and not fetch_status.get("pages_ok"):
            log(f"  SCAN [{site}]: сайт не ответил ({fetch_status['error']}) — это не «0 новых»")
            scan_failed[site] = str(fetch_status["error"])
            continue
        if not posts:
            log(f"  SCAN [{site}]: 0 новых постов найдено (query={query!r})")

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
                if md5 and seed.get("status") == "pending":
                    # No original here, but the same file may come from another site: the seed joins
                    # its MD5 group so its tags and post page are kept (the import marks it when it
                    # gets there).  It used to be dropped here, and with it the site's tags.
                    continue
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
    _plan: dict = {}
    _all_groups: list = []
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
    except Exception as _preflight_error:      # the size estimate is a courtesy: the import still runs
        log(f"  PREFLIGHT WARN: {_preflight_error}")
        _plan = {}
    declined = False
    if _plan.get("warn") and confirm_plan is not None and not scan_was_stopped:
        declined = not bool(confirm_plan(_plan))
        if declined and stop_flag and getattr(stop_flag, "_stop_requested", False):
            declined = False        # «Остановить» while asked: a stop (the found posts wait), not «no»
    if declined:
        log("  SUB: массовая загрузка отменена до скачивания файлов")
        finish_run(run_id, status="cancelled", found=discovered, queued=len(_all_groups), downloaded=0, skipped=0, failed=0, note="preflight cancelled")
        # Checked: the automatic check asks again after the interval, not a minute later (it used
        # to scan the sites and ask again every minute while nobody answered).  The found posts wait.
        record_run(sub_id, last_check=int(time.time()),
                   last_error=f"большая загрузка ({_plan.get('groups', 0)} файлов) не подтверждена — спрошу при следующей проверке")
        return 0

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
            tried = 0
            for seed in group:
                site = seed.get("site") or ""
                post = seed.get("post") or {}
                key = seed.get("key") or ""
                if not original_media_urls(post, site):
                    # only a sample/preview (seeds found before 5.3.1): never saved under the post's MD5
                    if key:
                        mark_seed(key, "skipped_no_file", error="no original file URL (only sample/preview)")
                    continue
                tried += 1
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
                        operation="subscription_download",
                    )
                except Exception as e:
                    ok, dest_path = False, None
                    try:
                        from core.download_policy import DownloadBlockedByPolicy
                        if isinstance(e, DownloadBlockedByPolicy):
                            last_error = "blocked_session_policy"
                            log(f"  DOWNLOAD BLOCKED BY SESSION POLICY: site={site} intent={e.decision.intent_id or 0}")
                            if key:
                                mark_seed(key, "blocked_session_policy", error=last_error)
                            continue
                    except Exception:
                        pass
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

            if not imported and not tried:
                skipped += 1
                log("  SKIP: у поста только уменьшенная копия (sample/preview), оригинала нет — не сохраняю")
            elif not imported and last_error:
                failed += 1
                log(f"  IMPORT FAIL: {last_error}")

        if stop_flag and getattr(stop_flag, "_stop_requested", False):
            stop_import = True

    stopped = bool(stop_flag and getattr(stop_flag, "_stop_requested", False))
    finish_run(
        run_id,
        status="stopped" if stopped else "done",
        found=discovered,
        queued=queued_total,
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        note="",
    )

    from core.redaction import sanitize_text
    if stopped:
        # Stopped: not a finished check — its time stays as it was, so the automatic check takes it
        # again after its rest (it waited the whole interval).  The found posts wait in the list.
        record_run(sub_id, downloaded=downloaded, last_post_ids=new_last_ids, oldest_post_ids=new_oldest_ids)
        log(f"SUB [{sub_name}]: остановлено, скачано {downloaded}; проверка не закончена — повторится")
        return downloaded
    now = int(time.time())
    last_check = now
    last_error = ""
    if sites_cfg and len(scan_failed) == len(sites_cfg):
        # No site answered: this run proves nothing about new posts.  The next attempt comes
        # in half an hour instead of after the whole interval (24 h by default).
        interval = max(1, int(sub.get("check_interval_hours", 24) or 24)) * 3600
        last_check = now - interval + min(interval, SCAN_RETRY_SECONDS)
        last_error = sanitize_text("; ".join(f"{k}: {v}" for k, v in scan_failed.items()))[:500]
        log(f"SUB [{sub_name}]: ни один сайт не ответил — повтор через {min(interval, SCAN_RETRY_SECONDS) // 60} мин")
    record_run(
        sub_id,
        downloaded=downloaded,
        last_post_ids=new_last_ids,
        oldest_post_ids=new_oldest_ids,
        last_check=last_check,
        last_error=last_error,
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
