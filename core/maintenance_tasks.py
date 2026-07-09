"""Small background-friendly maintenance jobs for the local library."""
from __future__ import annotations

from pathlib import Path
from typing import Callable
from collections import defaultdict
from queue import Queue, Empty
from threading import Lock, Thread
import time

from core.database.connection import db


def _live_paths(settings: dict) -> list[Path]:
    with db(settings, readonly=True) as con:
        return [Path(r["path"]) for r in con.execute("SELECT path FROM images WHERE deleted=0 ORDER BY indexed_at DESC").fetchall()]


def repair_missing_thumbnails(settings: dict, progress: Callable[[str], None] | None = None, stop_check=None) -> dict:
    from core.image_safe import safe_thumbnail_path
    paths = _live_paths(settings)
    created = errors = skipped = 0
    w = max(256, int(settings.get("thumb_cache_card_w", 240) or 240) * 2)
    h = max(256, int(settings.get("thumb_cache_card_h", 220) or 220) * 2)
    for idx, p in enumerate(paths, 1):
        if stop_check and stop_check():
            break
        if not p.exists():
            skipped += 1
            continue
        try:
            safe_thumbnail_path(str(p), w, h)
            created += 1
        except Exception:
            errors += 1
        if progress and idx % 100 == 0:
            progress(f"Превью: {idx}/{len(paths)}")
    return {"checked": len(paths), "created": created, "missing": skipped, "errors": errors}


def validate_recent_media(settings: dict, limit: int = 1000, progress: Callable[[str], None] | None = None) -> dict:
    from core.stability import check_recent_media_after_crash
    return check_recent_media_after_crash(settings, log=progress, limit=int(limit))


CATEGORY_RECHECK_HOSTS = (
    "danbooru.donmai.us",
    "booru.allthefallen.moe",
    "gelbooru.com",
    "rule34.xxx",
    "e621.net",
    "e926.net",
    "xbooru.com",
    "hypnohub.net",
)
CATEGORY_RECHECK_JOB_KEY = "site-categories::tag-groups-v10-maintenance-general-only"


def _norm_host(host: str) -> str:
    return str(host or "").strip().lower().replace("www.", "")


def _category_recheck_host_aliases(hosts=None) -> list[str]:
    out: list[str] = []
    for host in (hosts or CATEGORY_RECHECK_HOSTS):
        h = _norm_host(host)
        if not h:
            continue
        aliases = [h]
        if h == "rule34.xxx":
            aliases.append("api.rule34.xxx")
        elif h == "danbooru.donmai.us":
            aliases.append("donmai.us")
        elif h == "booru.allthefallen.moe":
            aliases.append("allthefallen.moe")
        for alias in aliases:
            if alias not in out:
                out.append(alias)
    return out


def find_general_only_category_sources(settings: dict, *, hosts=None, limit: int = 0) -> list[dict]:
    """Find source-specific tag bundles where every stored tag category is general.

    The scan is source-scoped: one image can have good Danbooru categories and a
    broken Gelbooru/rule34 source bundle at the same time.  Only the broken
    source bundle is returned for reclassification.
    """
    aliases = _category_recheck_host_aliases(hosts)
    if not aliases:
        return []
    host_terms = []
    args: list[object] = []
    for host in aliases:
        # s.host is normalized when sources are stored; use the indexed host
        # column here.  The old URL LIKE '%host%' full-scan made category
        # maintenance unnecessarily heavy on large libraries.
        host_terms.append("lower(replace(COALESCE(s.host,''),'www.',''))=?")
        args.append(host)
    where_hosts = " OR ".join(host_terms)
    requested_limit = int(limit or 0)
    limit_sql = ""
    query_args = list(args)
    if requested_limit > 0:
        # Positive limit means an explicit user/developer batch cap.  The UI
        # maintenance button passes 0 so the old accidental 10k ceiling is not
        # applied.
        max_rows = max(1, min(1000000, requested_limit))
        limit_sql = " LIMIT ?"
        query_args.append(max_rows)
    with db(settings, readonly=True) as con:
        rows = con.execute(f"""
            SELECT
                i.id AS image_id,
                i.path AS media_path,
                COALESCE(NULLIF(p.original_path,''), NULLIF(i.original_media_path,''), i.path) AS original_path,
                s.id AS source_id,
                s.host AS source_host,
                s.url AS source_url,
                COUNT(*) AS tag_count,
                SUM(CASE WHEN lower(COALESCE(NULLIF(ist.category,''),'general')) <> 'general' THEN 1 ELSE 0 END) AS non_general_count
            FROM image_source_tags ist
            JOIN images i ON i.id=ist.image_id AND i.deleted=0
            JOIN sources s ON s.id=ist.source_id
            LEFT JOIN processed_files p ON p.media_path=i.path
            WHERE ({where_hosts})
              AND COALESCE(s.url,'') <> ''
            GROUP BY i.id, s.id
            HAVING tag_count > 0 AND non_general_count = 0
            ORDER BY lower(COALESCE(s.host,'')), i.indexed_at DESC, i.path COLLATE NOCASE
            {limit_sql}
        """, tuple(query_args)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["source_host"] = _norm_host(item.get("source_host"))
        out.append(item)
    return out


def _canonical_category_recheck_host(host_or_url: str) -> str:
    text = str(host_or_url or "").strip().lower()
    if "://" in text:
        try:
            from urllib.parse import urlparse
            text = urlparse(text).netloc.lower()
        except Exception:
            pass
    text = text.replace("www.", "")
    if text in ("api.rule34.xxx",):
        return "rule34.xxx"
    if text in ("donmai.us",):
        return "danbooru.donmai.us"
    if text in ("allthefallen.moe",):
        return "booru.allthefallen.moe"
    return text


def _source_tag_rows(settings: dict, image_id: int, source_id: int) -> list[dict]:
    """Return tags for one image/source bundle with global category hints."""
    try:
        with db(settings, readonly=True) as con:
            rows = con.execute("""
                SELECT
                    t.normalized_name AS name,
                    COALESCE(NULLIF(t.category,''),'general') AS global_category,
                    COALESCE(NULLIF(ist.category,''),'general') AS source_category
                FROM image_source_tags ist
                JOIN tags t ON t.id=ist.tag_id
                WHERE ist.image_id=? AND ist.source_id=?
            """, (int(image_id), int(source_id))).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _groups_from_tag_category_map(mapping: dict[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for tag, cat in dict(mapping or {}).items():
        name = str(tag or "").strip()
        category = str(cat or "general").strip().lower() or "general"
        if not name or category == "general":
            continue
        out.setdefault(category, []).append(name)
    return out


def _still_source_all_general(settings: dict, image_id: int, source_id: int) -> bool:
    try:
        with db(settings, readonly=True) as con:
            row = con.execute("""
                SELECT
                    COUNT(*) AS c,
                    SUM(CASE WHEN lower(COALESCE(NULLIF(category,''),'general')) <> 'general' THEN 1 ELSE 0 END) AS ng
                FROM image_source_tags
                WHERE image_id=? AND source_id=?
            """, (int(image_id), int(source_id))).fetchone()
        if not row or int(row["c"] or 0) <= 0:
            return False
        return int(row["ng"] or 0) <= 0
    except Exception:
        return True


def _fast_refine_general_only_from_local_hints(settings: dict, row: dict, *, method: str = "maintenance_general_only_fast_local") -> dict:
    """Fix obvious general-only bundles without network.

    First uses global tag categories already learned from other sources, then
    site-scoped tag_category_cache.  If either gives at least one informative
    category, the source bundle stops being all-general and does not need the
    slow post/category request in this maintenance pass.
    """
    from core.database.storage import cached_tag_categories, refine_source_tag_categories
    image_id = int(row.get("image_id") or 0)
    source_id = int(row.get("source_id") or 0)
    media_path = str(row.get("media_path") or "")
    source_url = str(row.get("source_url") or "")
    host = _canonical_category_recheck_host(row.get("source_host") or source_url)
    tag_rows = _source_tag_rows(settings, image_id, source_id)
    if not tag_rows:
        return {"updated": 0, "source": "none", "still_general": True}
    mapping: dict[str, str] = {}
    for tr in tag_rows:
        name = str(tr.get("name") or "").strip()
        cat = str(tr.get("global_category") or "general").strip().lower() or "general"
        if name and cat != "general":
            mapping[name] = cat
    cache_hits = cached_tag_categories(settings, host, [r.get("name") for r in tag_rows])
    for name, cat in dict(cache_hits or {}).items():
        category = str(cat or "general").strip().lower() or "general"
        # Source-specific cache should win over generic global category when it
        # has an informative value; never use it to demote anything to general.
        if category != "general":
            mapping[str(name)] = category
    groups = _groups_from_tag_category_map(mapping)
    if not groups:
        return {"updated": 0, "source": "none", "still_general": True}
    refined = refine_source_tag_categories(settings, media_path, source_url, groups, method=method)
    updated = int((refined or {}).get("updated", 0) or 0)
    return {"updated": updated, "source": "local_cache", "still_general": _still_source_all_general(settings, image_id, source_id)}


def recheck_general_only_tag_categories(
    settings: dict,
    *,
    hosts=None,
    limit: int = 0,
    create_backup: bool = True,
    progress: Callable[[str], None] | None = None,
    stop_check=None,
) -> dict:
    """Re-classify existing source bundles stuck entirely in general.

    v401 makes this maintenance action fast enough for large libraries:
    1) one cheap DB scan finds only all-general source bundles;
    2) local/global category hints and tag_category_cache are applied first;
    3) only bundles that remain all-general go to network category lookup;
    4) slow network lookup is split into independent per-site queues/workers.
    """
    hosts = tuple(_category_recheck_host_aliases(hosts))
    def emit(msg: str):
        if progress:
            progress(str(msg))

    emit("Поиск source-наборов, где все теги лежат в general...")
    candidates = find_general_only_category_sources(settings, hosts=hosts, limit=limit)
    if not candidates:
        return {"found": 0, "processed": 0, "fixed": 0, "updated": 0, "ignored_new": 0, "no_classified": 0, "missing": 0, "errors": 0, "backup": ""}

    by_host_scan: dict[str, int] = defaultdict(int)
    for item in candidates:
        host = _canonical_category_recheck_host(item.get("source_host") or item.get("source_url") or "")
        by_host_scan[host or "unknown"] += 1
    scan_summary = ", ".join(f"{h}={n}" for h, n in sorted(by_host_scan.items()))
    emit(f"Найдено general-only наборов: {len(candidates)}; по сайтам: {scan_summary}")

    backup = ""
    if create_backup:
        cap_note = "" if int(limit or 0) <= 0 else f" (лимит={int(limit)})"
        emit(f"Создаю backup SQLite перед массовой перераскладкой{cap_note}...")
        try:
            from core.library_lifecycle import force_backup_database
            backup = str(force_backup_database(settings, "recheck_general_only_tag_categories") or "")
        except Exception as exc:
            return {"found": len(candidates), "processed": 0, "fixed": 0, "updated": 0, "ignored_new": 0, "no_classified": 0, "missing": 0, "errors": 1, "backup": "", "error": f"Не удалось создать backup SQLite: {exc}"}
        if not backup:
            return {"found": len(candidates), "processed": 0, "fixed": 0, "updated": 0, "ignored_new": 0, "no_classified": 0, "missing": 0, "errors": 1, "backup": "", "error": "Не удалось создать backup SQLite. Операция отменена."}

    worker_settings = dict(settings or {})
    worker_settings["_background_category_worker"] = True
    worker_settings["tagger_background_category_dapi_fallback"] = True
    worker_settings["tagger_gelbooru_category_single_tag_fallback"] = True
    worker_settings["_cancel_callback"] = stop_check or (lambda: False)
    by_host = dict(worker_settings.get("http_min_interval_by_host") or {})
    # These are maintenance floors, not UI settings.  Per-site workers run in
    # parallel, but individual hostile sites still get a cooldown.
    by_host["rule34.xxx"] = max(5.0, float(by_host.get("rule34.xxx", 0.0) or 0.0))
    by_host["api.rule34.xxx"] = max(5.0, float(by_host.get("api.rule34.xxx", 0.0) or 0.0))
    by_host["gelbooru.com"] = max(1.2, float(by_host.get("gelbooru.com", 0.0) or 0.0))
    by_host["xbooru.com"] = max(1.5, float(by_host.get("xbooru.com", 0.0) or 0.0))
    by_host["hypnohub.net"] = max(1.5, float(by_host.get("hypnohub.net", 0.0) or 0.0))
    by_host["danbooru.donmai.us"] = max(1.2, float(by_host.get("danbooru.donmai.us", 0.0) or 0.0))
    by_host["e621.net"] = max(1.5, float(by_host.get("e621.net", 0.0) or 0.0))
    by_host["e926.net"] = max(1.5, float(by_host.get("e926.net", 0.0) or 0.0))
    by_host["booru.allthefallen.moe"] = max(2.0, float(by_host.get("booru.allthefallen.moe", 0.0) or 0.0))
    worker_settings["http_min_interval_by_host"] = by_host

    log_samples: list[str] = []
    log_lock = Lock()
    write_lock = Lock()
    counter_lock = Lock()
    last_emit = {"t": 0.0}
    counters = {
        "fast_checked": 0,
        "fast_fixed": 0,
        "network_total": 0,
        "processed": 0,
        "fixed": 0,
        "updated": 0,
        "ignored_new": 0,
        "no_classified": 0,
        "missing": 0,
        "errors": 0,
        "queued_network": 0,
    }

    def log(msg: str):
        text = str(msg).strip()
        if "TAG CATEGORY" in text or "ERROR" in text or "429" in text:
            with log_lock:
                if len(log_samples) < 16:
                    log_samples.append(text)

    def maybe_emit(force: bool = False):
        if not progress:
            return
        now = time.time()
        with counter_lock:
            done = counters["fast_checked"] + counters["missing"] + counters["errors"]
            total = len(candidates)
            net_done = counters["network_total"]
            net_total = counters["queued_network"]
            msg = (
                f"Перераскладка general-only: БД={min(done, total)}/{total}; "
                f"сеть={net_done}/{net_total}; "
                f"быстро исправлено={counters['fast_fixed']}; "
                f"исправлено={counters['fixed']}; "
                f"обновлено тегов={counters['updated']}; "
                f"без категорий={counters['no_classified']}; ошибок={counters['errors']}"
            )
            if not force and now - last_emit["t"] < 1.0 and done % 25 != 0:
                return
            last_emit["t"] = now
        emit(msg)

    # Stage 1: cheap local/cache pass.  This is deliberately single-threaded-ish
    # on DB writes to avoid SQLite write contention, but it does no network and
    # eliminates a large share of old general-only rows immediately.
    emit("Быстрая проверка: сначала применяю уже известные категории без сетевых запросов...")
    network_by_host: dict[str, list[dict]] = defaultdict(list)
    for idx, row in enumerate(candidates, 1):
        if stop_check and stop_check():
            emit("Остановлено пользователем")
            break
        media_path = str(row.get("media_path") or "")
        if not media_path or not Path(media_path).exists():
            with counter_lock:
                counters["missing"] += 1
            continue
        try:
            with write_lock:
                fast = _fast_refine_general_only_from_local_hints(worker_settings, row)
            updated = int(fast.get("updated", 0) or 0)
            with counter_lock:
                counters["fast_checked"] += 1
                if updated > 0:
                    counters["fast_fixed"] += 1
                    counters["fixed"] += 1
                    counters["updated"] += updated
            if updated > 0 and not bool(fast.get("still_general", True)):
                maybe_emit()
                continue
            host = _canonical_category_recheck_host(row.get("source_host") or row.get("source_url") or "") or "unknown"
            network_by_host[host].append(row)
        except Exception as exc:
            with counter_lock:
                counters["errors"] += 1
            with log_lock:
                if len(log_samples) < 16:
                    log_samples.append(f"fast-local: {type(exc).__name__}: {exc}")
        if idx == 1 or idx % 100 == 0:
            maybe_emit(True)

    with counter_lock:
        counters["queued_network"] = sum(len(v) for v in network_by_host.values())
    if not network_by_host:
        maybe_emit(True)
        return {
            "found": len(candidates),
            "processed": counters["fast_checked"] + counters["missing"],
            "fixed": counters["fixed"],
            "updated": counters["updated"],
            "ignored_new": counters["ignored_new"],
            "no_classified": counters["no_classified"],
            "missing": counters["missing"],
            "errors": counters["errors"],
            "backup": backup,
            "samples": log_samples,
            "fast_fixed": counters["fast_fixed"],
            "network_checked": 0,
            "stopped": bool(stop_check and stop_check()),
        }

    net_summary = ", ".join(f"{h}={len(v)}" for h, v in sorted(network_by_host.items()))
    emit(f"Сетевая перераскладка нужна только для оставшихся all-general: {sum(len(v) for v in network_by_host.values())}; по сайтам: {net_summary}")

    from core.tagger import Tagger
    from core.database.storage import refine_source_tag_categories, enqueue_tag_enrichment, complete_tag_enrichment

    def process_network_row(host: str, row: dict, tagger: Tagger):
        media_path = str(row.get("media_path") or "")
        original_path = str(row.get("original_path") or media_path)
        source_url = str(row.get("source_url") or "")
        if not media_path or not Path(media_path).exists():
            with counter_lock:
                counters["missing"] += 1
            return
        try:
            # Benefit from categories cached by another worker a moment earlier.
            with write_lock:
                fast = _fast_refine_general_only_from_local_hints(worker_settings, row, method="maintenance_general_only_fast_cache_after_network")
            updated_fast = int(fast.get("updated", 0) or 0)
            if updated_fast > 0 and not bool(fast.get("still_general", True)):
                with counter_lock:
                    counters["fast_fixed"] += 1
                    counters["fixed"] += 1
                    counters["updated"] += updated_fast
                    counters["processed"] += 1
                return
            with write_lock:
                enqueue_tag_enrichment(worker_settings, original_path, media_path, source_url, job_key=CATEGORY_RECHECK_JOB_KEY)
            groups = tagger.grouped_tags_from_url(source_url)
            classified = sum(len(groups.get(k, []) or []) for k in ("artist", "contributor", "character", "copyright", "species", "meta", "lore", "invalid", "parody", "language", "category", "pages")) if groups else 0
            if classified <= 0:
                with write_lock:
                    complete_tag_enrichment(worker_settings, original_path, source_url, job_key=CATEGORY_RECHECK_JOB_KEY, status="done", error="source provides no classified tags")
                with counter_lock:
                    counters["processed"] += 1
                    counters["network_total"] += 1
                    counters["no_classified"] += 1
                return
            with write_lock:
                refined = refine_source_tag_categories(worker_settings, media_path, source_url, groups, method="maintenance_general_only_parallel_recheck_v401")
                complete_tag_enrichment(worker_settings, original_path, source_url, job_key=CATEGORY_RECHECK_JOB_KEY, status="done")
            updated = int((refined or {}).get("updated", 0) or 0)
            ignored = int((refined or {}).get("ignored", 0) or 0)
            with counter_lock:
                counters["processed"] += 1
                counters["network_total"] += 1
                counters["updated"] += updated
                counters["ignored_new"] += ignored
                if updated > 0:
                    counters["fixed"] += 1
        except Exception as exc:
            with counter_lock:
                counters["errors"] += 1
            try:
                from core.database.storage import retry_tag_enrichment
                with write_lock:
                    retry_tag_enrichment(worker_settings, original_path, source_url, job_key=CATEGORY_RECHECK_JOB_KEY, delay_seconds=300, error=str(exc))
            except Exception:
                pass
            with log_lock:
                if len(log_samples) < 16:
                    log_samples.append(f"{host or 'source'}: {type(exc).__name__}: {exc}")

    # Per-site queues.  Same-site parallelism is intentionally small; cross-site
    # parallelism is where the big win is, because rule34/ATF must not block
    # Gelbooru/Danbooru/e621 maintenance.
    default_workers = {
        "rule34.xxx": 1,
        "gelbooru.com": 2,
        "danbooru.donmai.us": 2,
        "booru.allthefallen.moe": 1,
        "e621.net": 2,
        "e926.net": 2,
        "xbooru.com": 1,
        "hypnohub.net": 1,
    }
    try:
        max_total_workers = int(worker_settings.get("maintenance_category_recheck_workers", 8) or 8)
    except Exception:
        max_total_workers = 8
    max_total_workers = max(1, min(16, max_total_workers))
    worker_plan: list[tuple[str, list[dict], int]] = []
    remaining_worker_budget = max_total_workers
    for host, rows in sorted(network_by_host.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        desired = int(default_workers.get(host, 1) or 1)
        desired = max(1, min(desired, len(rows), remaining_worker_budget))
        worker_plan.append((host, rows, desired))
        remaining_worker_budget -= desired
        if remaining_worker_budget <= 0:
            # Remaining hosts still get one worker by sharing the last budget via
            # later serial processing in their own thread group is not possible;
            # keep one to avoid starving.  Collapse extras to one below.
            remaining_worker_budget = 0
    # If budget was exhausted before adding every host, normalize: the loop above
    # still appended all hosts with at least one worker because desired clamps to
    # 1 only when budget >0. Add any missing hosts serially if necessary.
    planned_hosts = {h for h, _, _ in worker_plan}
    for host, rows in sorted(network_by_host.items()):
        if host not in planned_hosts:
            worker_plan.append((host, rows, 1))

    plan_text = ", ".join(f"{h}: jobs={len(rows)} workers={workers}" for h, rows, workers in worker_plan)
    emit(f"Параллельная перераскладка по сайтам: {plan_text}")

    threads: list[Thread] = []
    queues: dict[str, Queue] = {}
    sentinel = object()

    def site_worker(host: str, q: Queue):
        local_settings = dict(worker_settings)
        tagger = Tagger(local_settings, log)
        tagger.cancel_callback = stop_check or (lambda: False)
        while True:
            if stop_check and stop_check():
                return
            try:
                item = q.get(timeout=0.25)
            except Empty:
                continue
            try:
                if item is sentinel:
                    return
                process_network_row(host, item, tagger)
                maybe_emit()
            finally:
                try:
                    q.task_done()
                except Exception:
                    pass

    for host, rows, workers in worker_plan:
        q: Queue = Queue()
        queues[host] = q
        for row in rows:
            q.put(row)
        for _ in range(max(1, int(workers or 1))):
            q.put(sentinel)
            t = Thread(target=site_worker, args=(host, q), daemon=True, name=f"category-recheck-{host}")
            threads.append(t)
            t.start()

    for t in threads:
        t.join()
    maybe_emit(True)

    with counter_lock:
        result_counters = dict(counters)
    return {
        "found": len(candidates),
        "processed": int(result_counters.get("fast_checked", 0)) + int(result_counters.get("missing", 0)),
        "fixed": result_counters["fixed"],
        "updated": result_counters["updated"],
        "ignored_new": result_counters["ignored_new"],
        "no_classified": result_counters["no_classified"],
        "missing": result_counters["missing"],
        "errors": result_counters["errors"],
        "backup": backup,
        "samples": log_samples,
        "fast_fixed": result_counters["fast_fixed"],
        "network_checked": result_counters["network_total"],
        "network_queued": result_counters["queued_network"],
        "stopped": bool(stop_check and stop_check()),
    }
