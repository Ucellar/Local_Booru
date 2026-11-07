"""Local browser-extension companion API for booru pages.

The browser companion extension never reads the SQLite database directly. It
collects visible booru cards in the page, then asks this localhost-only API
whether a post/file identity is already present in Local Booru.

Scope is deliberately visual-only: hidden browser/grabber cards do not block the
parser, exact-MD5 source fanout, source merge, or no-match recovery.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import secrets
import logging
import re
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from core.database.connection import db
from core.grabber_exclusions import add_exclusion, compact_identities, is_excluded, normalize_md5
from core.settings import load_settings
from core.browser_import import BrowserImportManager
from core.browser_pairing import PAIRING_HEADER, authenticate as authenticate_pairing, exchange_pairing_code, issue_task_token, validate_task_token, list_extensions, revoke_extension
from core.browser_site_profiles import list_profiles
from core.domain_credentials import store_domain_credentials, list_domain_credentials, revoke_domain

log = logging.getLogger("local_booru.browser_companion")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47734
_COMPANION_HEADER = "X-Local-Booru-Companion"
_ALLOWED_EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "edge-extension://")
_HOST_ALIASES = {
    "www.rule34.xxx": "rule34.xxx",
    "api.rule34.xxx": "rule34.xxx",
    "api-cdn.rule34.xxx": "rule34.xxx",
    "www.gelbooru.com": "gelbooru.com",
    "img2.gelbooru.com": "gelbooru.com",
    "danbooru.donmai.us": "danbooru.donmai.us",
    "cdn.donmai.us": "danbooru.donmai.us",
    "static1.e621.net": "e621.net",
    "static1.e926.net": "e926.net",
    "booru.allthefallen.moe": "booru.allthefallen.moe",
}
_MD5_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", re.I)
_IMPORT_MANAGER: BrowserImportManager | None = None
_MANAGER_LOCK = threading.Lock()


def import_manager(settings: dict | None = None) -> BrowserImportManager | None:
    """The program's browser import (one lane for the extension and the pages: «Скачать» of a missing part of a
    сборка, v5.30) — made here when the extension's API did not start (its port taken) and ``settings`` are given."""
    global _IMPORT_MANAGER
    with _MANAGER_LOCK:
        if (_IMPORT_MANAGER is None or not _IMPORT_MANAGER.alive) and settings is not None:
            _IMPORT_MANAGER = BrowserImportManager(settings, log_fn=lambda m: log.info("%s", m))
        return _IMPORT_MANAGER if _IMPORT_MANAGER is not None and _IMPORT_MANAGER.alive else None


def normalize_host(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        try:
            raw = urlparse(raw).netloc.lower()
        except Exception:
            pass
    raw = raw.split("@")[-1].split(":")[0].strip().lstrip(".")
    if raw.startswith("www."):
        raw = raw[4:]
    return _HOST_ALIASES.get(raw, raw)


def normalize_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or not raw.lower().startswith(("http://", "https://")):
        return ""
    return raw.rstrip("/")


def extract_md5_from_url(value: Any) -> str:
    text = str(value or "")
    m = _MD5_RE.search(text)
    return normalize_md5(m.group(1)) if m else ""


def extract_post_id(site: str, post_url: str, explicit: Any = "") -> str:
    explicit_s = str(explicit or "").strip()
    if explicit_s:
        return explicit_s
    url = normalize_url(post_url)
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if "id" in query and query["id"]:
            return str(query["id"][0]).strip()
        parts = [p for p in parsed.path.split("/") if p]
        if "posts" in parts:
            idx = parts.index("posts")
            if idx + 1 < len(parts):
                return parts[idx + 1].strip()
    except Exception:
        return ""
    return ""


def identities_for_item(item: dict[str, Any]) -> list[tuple[str, str]]:
    site = normalize_host(item.get("site") or item.get("host") or item.get("post_url") or item.get("file_url"))
    post_url = normalize_url(item.get("post_url"))
    file_url = normalize_url(item.get("file_url"))
    md5 = normalize_md5(item.get("md5")) or extract_md5_from_url(file_url) or extract_md5_from_url(post_url)
    post_id = extract_post_id(site, post_url, item.get("post_id"))
    rows: list[tuple[str, str]] = []
    if md5:
        rows.append(("md5", md5))
    if post_url:
        rows.append(("post_url", post_url))
    if file_url:
        rows.append(("file_url", file_url))
    if site and post_id:
        rows.append(("key", f"{site}:{post_id}"))
    return compact_identities(rows)


def _source_url_candidates(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("post_url", "file_url", "sample_url", "preview_url"):
        u = normalize_url(item.get(key))
        if u and u not in out:
            out.append(u)
    return out


def _source_post_url_candidates(item: dict[str, Any]) -> list[str]:
    """Return exact post URL candidates for a site/post_id pair.

    v258 removed the old leading-wildcard ``s.url LIKE`` fallback from the
    companion status check.  Exact URLs can use the existing sources.url index;
    ``LIKE '%id%'`` cannot and becomes a full scan on large libraries.
    """
    site = normalize_host(item.get("site") or item.get("host") or item.get("post_url"))
    post_id = extract_post_id(site, str(item.get("post_url") or ""), item.get("post_id"))
    if not (site and post_id):
        return []
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", post_id):
        return []
    out: list[str] = []
    def add(url: str) -> None:
        u = normalize_url(url)
        if u and u not in out:
            out.append(u)
    if site == "rule34.xxx":
        for host in ("rule34.xxx", "www.rule34.xxx"):
            for scheme in ("https", "http"):
                add(f"{scheme}://{host}/index.php?page=post&s=view&id={post_id}")
    elif site == "gelbooru.com":
        for host in ("gelbooru.com", "www.gelbooru.com"):
            for scheme in ("https", "http"):
                add(f"{scheme}://{host}/index.php?page=post&s=view&id={post_id}")
    elif site == "danbooru.donmai.us":
        add(f"https://danbooru.donmai.us/posts/{post_id}")
        add(f"http://danbooru.donmai.us/posts/{post_id}")
    elif site in {"e621.net", "e926.net"}:
        add(f"https://{site}/posts/{post_id}")
        add(f"http://{site}/posts/{post_id}")
    elif site == "booru.allthefallen.moe":
        add(f"https://booru.allthefallen.moe/posts/{post_id}")
        add(f"http://booru.allthefallen.moe/posts/{post_id}")
    return out


def _row_to_status(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    lifecycle = str(row["lifecycle"] or "archive") if "lifecycle" in row.keys() else "archive"
    bucket = str(row["bucket"] or "") if "bucket" in row.keys() else ""
    return {
        "status": "downloaded",
        "action": "hide",
        "image_id": int(row["id"]),
        "md5": str(row["hash_md5"] or ""),
        "bucket": bucket,
        "lifecycle": lifecycle,
        "file_name": str(row["file_name"] or ""),
        "path": str(row["path"] or "") if "path" in row.keys() else "",
        "width": int(row["width"] or 0) if "width" in row.keys() else 0,
        "height": int(row["height"] or 0) if "height" in row.keys() else 0,
        "file_size": int(row["size_bytes"] or 0) if "size_bytes" in row.keys() else 0,
        "reason": "local_md5",
    }


def _card_identity(item: dict[str, Any]) -> bool:
    """The extension (0.6.1+) describes each card by itself, so its post URL and id are taken as sent."""
    try:
        return int(item.get("identity_v") or 0) >= 2
    except (TypeError, ValueError):
        return False


def check_one(settings: dict | None, item: dict[str, Any]) -> dict[str, Any]:
    from core.browser_companion_index import resolve_post_identity
    item = item if isinstance(item, dict) else {}
    site = normalize_host(item.get("site") or item.get("host") or item.get("post_url") or item.get("file_url"))
    post_url, explicit_id = resolve_post_identity(
        site, normalize_url(item.get("post_url")), item.get("post_id"),
        trusted=_card_identity(item), is_post_page=bool(item.get("is_post_page")))
    file_url = normalize_url(item.get("file_url"))
    md5 = normalize_md5(item.get("md5")) or extract_md5_from_url(file_url) or extract_md5_from_url(post_url)
    post_id = extract_post_id(site, post_url, explicit_id)

    result = {
        "key": str(item.get("key") or item.get("element_id") or ""),
        "site": site,
        "post_id": post_id,
        "post_url": post_url,
        "file_url": file_url,
        "md5": md5,
        "status": "new",
        "action": "show",
        "reason": "unknown",
        "known_sites": [],
    }

    identities = identities_for_item({**item, "site": site, "post_url": post_url, "file_url": file_url, "md5": md5, "post_id": post_id})
    try:
        if bool((settings or {}).get("browser_companion_use_grabber_hides", True)) and is_excluded(settings, identities):
            result.update({"status": "hidden", "action": "hide", "reason": "manual_visual_hide"})
            return result
    except Exception as exc:
        log.debug("browser companion exclusion check failed: %s", exc)

    try:
        with db(settings, readonly=True) as con:
            row = None
            if md5:
                row = con.execute(
                    """SELECT * FROM images
                       WHERE deleted=0 AND lower(COALESCE(hash_md5,''))=?
                       ORDER BY id DESC LIMIT 1""",
                    (md5,),
                ).fetchone()
                if row:
                    result.update(_row_to_status(row))
                    result["reason"] = "local_md5"
                else:
                    # A browser import may have downloaded transformed bytes
                    # while the booru post exposed the original-file MD5.  v25
                    # stores that explicit remote-to-local binding, so the same
                    # post is still recognized without pretending the local
                    # canonical hash equals the site's hash.
                    try:
                        row = con.execute(
                            """SELECT i.*
                               FROM image_site_md5_aliases a
                               JOIN images i ON i.id=a.image_id
                               WHERE i.deleted=0 AND lower(a.site_md5)=?
                               ORDER BY i.id DESC LIMIT 1""",
                            (md5,),
                        ).fetchone()
                    except Exception:
                        row = None
                    if row:
                        result.update(_row_to_status(row))
                        result["reason"] = "site_md5_alias"
                        result["site_md5"] = md5
                    else:
                        deleted_rule = con.execute(
                            "SELECT md5, reason FROM deleted_media_rules WHERE active=1 AND lower(md5)=? LIMIT 1",
                            (md5,),
                        ).fetchone()
                        if deleted_rule:
                            result.update({"status": "deleted_rule", "action": "hide", "reason": str(deleted_rule["reason"] or "deleted_media_rule")})
                            return result
            if result["status"] == "new":
                urls = _source_url_candidates({**item, "post_url": post_url, "file_url": file_url})
                if urls:
                    ph = ",".join(["?"] * len(urls))
                    row = con.execute(
                        f"""SELECT i.*, s.host AS source_host
                            FROM sources s
                            JOIN image_sources isrc ON isrc.source_id=s.id
                            JOIN images i ON i.id=isrc.image_id
                            WHERE i.deleted=0 AND s.url IN ({ph})
                            ORDER BY i.id DESC LIMIT 1""",
                        urls,
                    ).fetchone()
                    if row:
                        existing_md5 = str(row["hash_md5"] or "").lower()
                        result.update(_row_to_status(row))
                        if md5 and existing_md5 and md5 != existing_md5:
                            result.update({
                                "status": "post_different_file",
                                "action": "show",
                                "reason": "source_post_different_md5",
                                "existing_md5": existing_md5,
                            })
                        else:
                            result["reason"] = "source_url"
            if result["status"] == "new" and site and post_id:
                urls = _source_post_url_candidates({**item, "site": site, "post_url": post_url, "post_id": post_id})
                if urls:
                    ph = ",".join(["?"] * len(urls))
                    row = con.execute(
                        f"""SELECT i.*, s.host AS source_host
                            FROM sources s
                            JOIN image_sources isrc ON isrc.source_id=s.id
                            JOIN images i ON i.id=isrc.image_id
                            WHERE i.deleted=0 AND s.host=? AND s.url IN ({ph})
                            ORDER BY i.id DESC LIMIT 1""",
                        (site, *urls),
                    ).fetchone()
                    if row:
                        existing_md5 = str(row["hash_md5"] or "").lower()
                        result.update(_row_to_status(row))
                        if md5 and existing_md5 and md5 != existing_md5:
                            result.update({
                                "status": "post_different_file",
                                "action": "show",
                                "reason": "source_post_different_md5",
                                "existing_md5": existing_md5,
                            })
                        else:
                            result["reason"] = "source_post_id"
            image_id = int(result.get("image_id") or 0)
            if image_id:
                sites = [str(r[0] or "") for r in con.execute(
                    """SELECT DISTINCT s.host FROM sources s
                        JOIN image_sources isrc ON isrc.source_id=s.id
                        WHERE isrc.image_id=? AND COALESCE(s.host,'')<>''
                        ORDER BY s.host""",
                    (image_id,),
                ).fetchall()]
                result["known_sites"] = sites
                try:
                    family = con.execute(
                        """SELECT mf.family_id,mf.family_type,mf.family_source,mf.title,mf.total_items,mf.completion_status,mfi.family_index,
                                  SUM(CASE WHEN all_items.image_id IS NOT NULL AND all_items.is_missing=0 THEN 1 ELSE 0 END) AS present_count
                           FROM media_family_items mfi
                           JOIN media_families mf ON mf.family_id=mfi.family_id
                           JOIN media_family_items all_items ON all_items.family_id=mf.family_id
                           WHERE mfi.image_id=? AND mf.family_type<>'multi_attachment'
                           GROUP BY mf.family_id,mfi.family_index
                           ORDER BY mf.order_confidence DESC,mf.family_id LIMIT 1""",
                        (image_id,),
                    ).fetchone()
                    if family:
                        result.update({
                            "family_id": int(family["family_id"] or 0),
                            "family_type": str(family["family_type"] or ""),
                            "family_source": str(family["family_source"] or ""),
                            "family_title": str(family["title"] or ""),
                            "family_index": int(family["family_index"] or 0),
                            "family_total": max(0, int(family["total_items"] or 0)),
                            "family_present": max(0, int(family["present_count"] or 0)),
                            "family_status": str(family["completion_status"] or "unknown"),
                        })
                except Exception:
                    pass
    except Exception as exc:
        # Do not log every companion DB miss/error here. During heavy parser runs
        # SQLite can briefly return disk I/O/busy errors, and RotatingFileHandler
        # can itself fail if the log file is locked. The extension should simply
        # show cards instead of spamming traceback/logging errors.
        result.update({"status": "error", "action": "show", "reason": str(exc)[:180]})
    # The badge is no longer binary.  When the browser reports larger source
    # dimensions for a strong post identity, mark it as an automatic upgrade
    # candidate; the content script submits it to BrowserImport immediately.
    if result.get("status") == "downloaded":
        remote_w = max(0, int(item.get("width") or 0)); remote_h = max(0, int(item.get("height") or 0))
        local_w = max(0, int(result.get("width") or 0)); local_h = max(0, int(result.get("height") or 0))
        strong_identity = str(result.get("reason") or "") in {"site_md5_alias", "source_url", "source_post_id", "local_md5"}
        if strong_identity and remote_w > 0 and remote_h > 0 and local_w > 0 and local_h > 0 and (remote_w * remote_h) > (local_w * local_h):
            result.update({
                "status": "upgrade_available", "action": "show", "reason": "remote_dimensions_better",
                "local_width": local_w, "local_height": local_h,
                "remote_width": remote_w, "remote_height": remote_h,
                "auto_upgrade": bool((settings or {}).get("automatic_quality_upgrade_enabled", True)),
            })
    return result


def open_card(settings: dict | None, image_id: Any) -> dict[str, Any]:
    """«Уже есть» clicked on a site (v5.31): the card in the program's window, the window in front.  Only a live card
    of the library; the window answers within a few seconds or it is said."""
    try:
        number = int(image_id or 0)
    except (TypeError, ValueError):
        number = 0
    if number <= 0:
        return {"ok": False, "error": "no_card"}
    try:
        from core.database.connection import db
        with db(settings or {}, readonly=True) as con:
            row = con.execute("SELECT deleted FROM images WHERE id=?", (number,)).fetchone()
    except Exception as exc:  # the base busy or gone: said, not raised into the HTTP thread
        return {"ok": False, "error": f"base: {type(exc).__name__}"}
    if row is None:
        return {"ok": False, "error": "Такой карточки больше нет"}
    from webui import service
    answer = service.call_window("open_card", {"id": number}, wait=5.0, finish_wait=5.0)
    if answer == "done":
        return {"ok": True}
    words = {"no_window": "окно программы не открыто", "refused": "окно этого не умеет — обновите программу"}
    return {"ok": False, "error": words.get(answer, "окно программы не ответило")}


def check_items(settings: dict | None, items: list[dict[str, Any]]) -> dict[str, Any]:
    max_items = int((settings or {}).get("browser_companion_max_batch", 250) or 250)
    max_items = max(1, min(1000, max_items))
    items = [x for x in (items or []) if isinstance(x, dict)][:max_items]
    results: list[dict[str, Any]] = []
    for item in items:
        result = check_one(settings, item)
        results.append(result)
        # Tags already visible in the current page are authoritative enough to
        # refresh the source-scoped record without an extra site request.  This
        # is deliberately background/deduplicated so scrolling cannot block the
        # extension status batch or the Qt UI.
        if (
            _IMPORT_MANAGER is not None
            and result.get("status") in {"downloaded", "post_different_file"}
            and result.get("path")
            and (item.get("groups") or item.get("tags") or item.get("is_post_page"))
            and item.get("post_url")
        ):
            try:
                _IMPORT_MANAGER.refresh_visible_metadata(item, str(result.get("path") or ""))
            except Exception:
                pass
    for result in results:              # the extension never gets paths of the archive
        result.pop("path", None)
    return {
        "ok": True,
        "items": results,
        "checked": len(items),
        "server_time": int(time.time()),
    }



# --- e621 browser-extension API bridge --------------------------------------
# The app cannot reliably defeat Cloudflare with a freshly launched automation
# browser.  The companion extension can fetch e621 JSON from the user's normal
# ordinary browser session after the user has passed the verification page. This
# remains JSON API access; it is not HTML scraping and it does not expose the
# local archive to the extension.
_E621_TASK_COND = threading.Condition()
_E621_TASKS: dict[str, dict[str, Any]] = {}
_E621_TASK_SEQ = 0


def _bridge_task_deadline(task: dict[str, Any]) -> float:
    """Return the active deadline for a queued or claimed Companion task.

    Queue waiting and browser execution are separate phases.  The execution
    timeout starts only when the extension actually claims the task, otherwise
    a task can expire while waiting behind another lane on the shared worker tab.
    """
    state = str((task or {}).get("state") or "pending")
    if state == "inflight":
        return float((task or {}).get("deadline") or 0.0)
    return float((task or {}).get("queue_deadline") or (task or {}).get("deadline") or 0.0)


def _bridge_task_expired(task: dict[str, Any], now: float | None = None) -> bool:
    current = float(time.time() if now is None else now)
    deadline = _bridge_task_deadline(task)
    return bool(deadline and deadline < current)


def _bridge_task_claim(task: dict[str, Any], now: float | None = None) -> None:
    current = float(time.time() if now is None else now)
    if str((task or {}).get("state") or "pending") != "inflight":
        timeout_s = max(1.0, float((task or {}).get("timeout_seconds") or 120.0))
        task["deadline"] = current + timeout_s
    task["state"] = "inflight"
    task["inflight_at"] = current


def enqueue_e621_browser_fetch(url: str, *, auth_header: str = "", timeout_s: float = 120.0) -> dict[str, Any] | None:
    """Ask the installed browser companion extension to fetch e621/e926 JSON.

    The queue timeout covers waiting for the shared worker.  Once the extension
    claims the task, a fresh execution timeout begins.
    """
    global _E621_TASK_SEQ
    raw_url = str(url or "").strip()
    if not raw_url.startswith(("https://e621.net/", "https://e926.net/")):
        return None
    # A browser cannot set the User-Agent: e621's rules ask such a request to carry it in ``_client`` (v5.44.22).
    from core import site_agent
    try:
        settings = load_settings()
    except Exception:  # the request goes without the login in it
        settings = None
    raw_url = site_agent.with_client(raw_url, settings)
    try:
        timeout_s = max(5.0, min(600.0, float(timeout_s or 120.0)))
    except Exception:
        timeout_s = 120.0
    with _E621_TASK_COND:
        _E621_TASK_SEQ += 1
        now = time.time()
        task_id = f"e621-{int(now)}-{_E621_TASK_SEQ}"
        _E621_TASKS[task_id] = {
            "id": task_id,
            "url": raw_url,
            "authorization": str(auth_header or ""),
            "created_at": now,
            "queue_deadline": now + timeout_s,
            "deadline": 0.0,
            "timeout_seconds": timeout_s,
            "state": "pending",
            "result": None,
        }
        _E621_TASK_COND.notify_all()
        while True:
            task = _E621_TASKS.get(task_id)
            if not task:
                return None
            if task.get("state") in {"done", "error"}:
                result = task.get("result") if isinstance(task.get("result"), dict) else None
                _E621_TASKS.pop(task_id, None)
                return result
            deadline = _bridge_task_deadline(task)
            now = time.time()
            if not deadline or now >= deadline:
                _E621_TASKS.pop(task_id, None)
                _E621_TASK_COND.notify_all()
                return None
            _E621_TASK_COND.wait(timeout=min(1.0, max(0.1, deadline - now)))


def _e621_bridge_next_task() -> dict[str, Any]:
    now = time.time()
    with _E621_TASK_COND:
        for tid, task in list(_E621_TASKS.items()):
            if _bridge_task_expired(task, now):
                _E621_TASKS.pop(tid, None)
                continue
            if task.get("state") in {"pending", "inflight"}:
                # A stale inflight lease may be reclaimed after an MV3 worker
                # restart, but reclaiming never extends the execution deadline.
                inflight_at = float(task.get("inflight_at") or 0)
                if task.get("state") == "inflight" and now - inflight_at < 15.0:
                    continue
                _bridge_task_claim(task, now)
                return {
                    "ok": True,
                    "has_task": True,
                    "task": {
                        "id": tid,
                        "url": str(task.get("url") or ""),
                        "authorization": str(task.get("authorization") or ""),
                        "timeout_seconds": int(float(task.get("timeout_seconds") or 120)),
                    },
                }
        return {"ok": True, "has_task": False, "pending": len(_E621_TASKS)}


def _e621_bridge_store_result(data: dict[str, Any]) -> dict[str, Any]:
    tid = str((data or {}).get("id") or "").strip()
    if not tid:
        return {"ok": False, "error": "missing_id"}
    result = {
        "status": int((data or {}).get("status") or 0),
        "url": str((data or {}).get("url") or ""),
        "headers": (data or {}).get("headers") if isinstance((data or {}).get("headers"), dict) else {},
        "text": str((data or {}).get("text") or ""),
        "error": str((data or {}).get("error") or ""),
        "bridge_mode": str((data or {}).get("bridge_mode") or ""),
        "page_url": str((data or {}).get("page_url") or ""),
        "page_title": str((data or {}).get("page_title") or ""),
        "page_probe": str((data or {}).get("page_probe") or ""),
        "page_fetch_error": str((data or {}).get("page_fetch_error") or ""),
    }
    with _E621_TASK_COND:
        task = _E621_TASKS.get(tid)
        if not task:
            return {"ok": False, "error": "unknown_or_expired_task"}
        task["state"] = "error" if result.get("error") else "done"
        task["result"] = result
        _E621_TASK_COND.notify_all()
    return {"ok": True}


def e621_bridge_status() -> dict[str, Any]:
    with _E621_TASK_COND:
        pending = sum(1 for t in _E621_TASKS.values() if t.get("state") == "pending")
        inflight = sum(1 for t in _E621_TASKS.values() if t.get("state") == "inflight")
    return {"pending": pending, "inflight": inflight}



# --- Pixiv browser-extension API bridge -------------------------------------
# Pixiv source relay uses the user's already-open browser/Pixiv session for the
# AJAX pages endpoint. Local Booru owns no browser process for this. The
# extension returns JSON/text only; original images are still downloaded by the
# app with the discovered original URL + Referer and then MD5 is computed locally.
_PIXIV_TASK_COND = threading.Condition()
_PIXIV_TASKS: dict[str, dict[str, Any]] = {}
_PIXIV_TASK_SEQ = 0


def enqueue_pixiv_browser_fetch(url: str, *, referer: str = "", timeout_s: float = 30.0) -> dict[str, Any] | None:
    """Ask the companion extension to fetch Pixiv JSON or artwork HTML.

    Pixiv's managed-tab fallback alone can take more than twelve seconds, so
    old 6-8 second settings are clamped to a safe minimum.  The execution timer
    begins only after the extension claims the task.
    """
    global _PIXIV_TASK_SEQ
    raw_url = str(url or "").strip()
    if not raw_url.startswith(("https://www.pixiv.net/", "https://pixiv.net/")):
        return None
    try:
        timeout_s = max(30.0, min(180.0, float(timeout_s or 30.0)))
    except Exception:
        timeout_s = 30.0
    with _PIXIV_TASK_COND:
        _PIXIV_TASK_SEQ += 1
        now = time.time()
        task_id = f"pixiv-{int(now)}-{_PIXIV_TASK_SEQ}"
        _PIXIV_TASKS[task_id] = {
            "id": task_id,
            "url": raw_url,
            "referer": str(referer or ""),
            "created_at": now,
            "queue_deadline": now + timeout_s,
            "deadline": 0.0,
            "timeout_seconds": timeout_s,
            "state": "pending",
            "result": None,
        }
        _PIXIV_TASK_COND.notify_all()
        while True:
            task = _PIXIV_TASKS.get(task_id)
            if not task:
                return None
            if task.get("state") in {"done", "error"}:
                result = task.get("result") if isinstance(task.get("result"), dict) else None
                _PIXIV_TASKS.pop(task_id, None)
                return result
            deadline = _bridge_task_deadline(task)
            now = time.time()
            if not deadline or now >= deadline:
                _PIXIV_TASKS.pop(task_id, None)
                _PIXIV_TASK_COND.notify_all()
                return None
            _PIXIV_TASK_COND.wait(timeout=min(0.75, max(0.1, deadline - now)))


def _pixiv_bridge_next_task() -> dict[str, Any]:
    now = time.time()
    with _PIXIV_TASK_COND:
        for tid, task in list(_PIXIV_TASKS.items()):
            if _bridge_task_expired(task, now):
                _PIXIV_TASKS.pop(tid, None)
                continue
            if task.get("state") in {"pending", "inflight"}:
                inflight_at = float(task.get("inflight_at") or 0)
                if task.get("state") == "inflight" and now - inflight_at < 15.0:
                    continue
                _bridge_task_claim(task, now)
                return {
                    "ok": True,
                    "has_task": True,
                    "task": {
                        "id": tid,
                        "url": str(task.get("url") or ""),
                        "referer": str(task.get("referer") or ""),
                        "timeout_seconds": int(float(task.get("timeout_seconds") or 30)),
                    },
                }
        return {"ok": True, "has_task": False, "pending": len(_PIXIV_TASKS)}


def _pixiv_bridge_store_result(data: dict[str, Any]) -> dict[str, Any]:
    tid = str((data or {}).get("id") or "").strip()
    if not tid:
        return {"ok": False, "error": "missing_id"}
    result = {
        "status": int((data or {}).get("status") or 0),
        "url": str((data or {}).get("url") or ""),
        "headers": (data or {}).get("headers") if isinstance((data or {}).get("headers"), dict) else {},
        "text": str((data or {}).get("text") or ""),
        "error": str((data or {}).get("error") or ""),
        "bridge_mode": str((data or {}).get("bridge_mode") or ""),
        "page_url": str((data or {}).get("page_url") or ""),
        "page_title": str((data or {}).get("page_title") or ""),
        "page_fetch_error": str((data or {}).get("page_fetch_error") or ""),
    }
    with _PIXIV_TASK_COND:
        task = _PIXIV_TASKS.get(tid)
        if not task:
            return {"ok": False, "error": "unknown_or_expired_task"}
        task["state"] = "error" if result.get("error") else "done"
        task["result"] = result
        _PIXIV_TASK_COND.notify_all()
    return {"ok": True}


def pixiv_bridge_status() -> dict[str, Any]:
    with _PIXIV_TASK_COND:
        pending = sum(1 for t in _PIXIV_TASKS.values() if t.get("state") == "pending")
        inflight = sum(1 for t in _PIXIV_TASKS.values() if t.get("state") == "inflight")
    return {"pending": pending, "inflight": inflight}


def pixiv_bridge_status_detail() -> dict[str, Any]:
    now = time.time()
    with _PIXIV_TASK_COND:
        pending = sum(1 for t in _PIXIV_TASKS.values() if t.get("state") == "pending")
        inflight = sum(1 for t in _PIXIV_TASKS.values() if t.get("state") == "inflight")
        oldest_age = 0.0
        for t in _PIXIV_TASKS.values():
            try:
                oldest_age = max(oldest_age, now - float(t.get("created_at") or now))
            except Exception:
                pass
    return {"pending": pending, "inflight": inflight, "oldest_age": round(oldest_age, 1)}


# Generic resolver for protected pages in the user's ordinary browser.
_EXTERNAL_BROWSER_TASK_COND = threading.Condition()
_EXTERNAL_BROWSER_TASKS: dict[str, dict[str, Any]] = {}
_EXTERNAL_BROWSER_TASK_SEQ = 0
_EXTERNAL_BROWSER_ACTIVE_ID = ""
_EXTERNAL_BROWSER_BOOTSTRAP_ID = ""
_EXTERNAL_BROWSER_HEARTBEAT: dict[str, Any] = {
    "last_seen": 0.0, "browser": "", "version": "", "user_agent": "", "page_url": "",
}
_EXTERNAL_BROWSER_ALLOWED_HOSTS = {
    "rule34.xxx", "hl.rule34.xxx", "gelbooru.com", "www.gelbooru.com",
    "danbooru.donmai.us", "e621.net", "e926.net", "booru.allthefallen.moe",
    "tineye.com", "www.tineye.com", "ascii2d.net", "www.ascii2d.net",
    "pixiv.net", "www.pixiv.net",
}


def _external_browser_host_allowed(url: str) -> bool:
    try:
        host = str(urlparse(str(url or "")).hostname or "").lower().strip(".")
    except Exception:
        return False
    return bool(host and (host in _EXTERNAL_BROWSER_ALLOWED_HOSTS or any(host.endswith("." + h) for h in _EXTERNAL_BROWSER_ALLOWED_HOSTS)))


def external_browser_heartbeat(data: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = data if isinstance(data, dict) else {}
    with _EXTERNAL_BROWSER_TASK_COND:
        _EXTERNAL_BROWSER_HEARTBEAT.update({
            "last_seen": time.time(),
            "browser": str(payload.get("browser") or "")[:80],
            "version": str(payload.get("version") or "")[:80],
            "user_agent": str(payload.get("user_agent") or "")[:500],
            "page_url": str(payload.get("page_url") or "")[:1000],
        })
        _EXTERNAL_BROWSER_TASK_COND.notify_all()
    return {"ok": True}


def external_browser_bridge_status() -> dict[str, Any]:
    now = time.time()
    with _EXTERNAL_BROWSER_TASK_COND:
        last_seen = float(_EXTERNAL_BROWSER_HEARTBEAT.get("last_seen") or 0.0)
        return {
            "pending": sum(1 for t in _EXTERNAL_BROWSER_TASKS.values() if t.get("state") == "pending"),
            "inflight": sum(1 for t in _EXTERNAL_BROWSER_TASKS.values() if t.get("state") == "inflight"),
            "connected": bool(last_seen and now - last_seen <= 15.0),
            "last_seen_age": round(max(0.0, now - last_seen), 1) if last_seen else None,
            "browser": str(_EXTERNAL_BROWSER_HEARTBEAT.get("browser") or ""),
            "version": str(_EXTERNAL_BROWSER_HEARTBEAT.get("version") or ""),
            "page_url": str(_EXTERNAL_BROWSER_HEARTBEAT.get("page_url") or ""),
        }



def _external_browser_connected_for_task_locked(task: dict[str, Any], now: float | None = None) -> bool:
    current = float(now if now is not None else time.time())
    last_seen = float(_EXTERNAL_BROWSER_HEARTBEAT.get("last_seen") or 0.0)
    connected = bool(last_seen and current - last_seen <= 15.0)
    preferred = str(task.get("preferred_browser") or "")
    if connected and preferred:
        try:
            from core.external_browser import browser_family_matches
            connected = browser_family_matches(preferred, str(_EXTERNAL_BROWSER_HEARTBEAT.get("browser") or ""))
        except Exception:
            connected = True
    return connected


def _external_browser_prepare_bootstrap_locked() -> tuple[str, str, dict[str, Any]] | None:
    """Assign at most one Python-opened bootstrap tab across the whole queue."""
    global _EXTERNAL_BROWSER_BOOTSTRAP_ID
    now = time.time()
    if _EXTERNAL_BROWSER_BOOTSTRAP_ID:
        current = _EXTERNAL_BROWSER_TASKS.get(_EXTERNAL_BROWSER_BOOTSTRAP_ID)
        if current and current.get("state") in {"pending", "inflight"} and not _bridge_task_expired(current, now):
            return None
        _EXTERNAL_BROWSER_BOOTSTRAP_ID = ""

    for tid, task in _EXTERNAL_BROWSER_TASKS.items():
        if task.get("state") != "pending":
            continue
        if _bridge_task_expired(task, now):
            continue
        if not bool(task.get("auto_open", False)):
            continue
        last_attempt = float(task.get("bootstrap_last_attempt") or 0.0)
        if last_attempt and now - last_attempt < 15.0:
            # Preserve queue order and avoid shell-open retry storms.
            return None
        if _external_browser_connected_for_task_locked(task, now):
            # A live matching Companion will create/reuse its managed worker tab.
            return None
        marker = str(task.get("claim_marker") or "") or secrets.token_urlsafe(12)
        task["claim_marker"] = marker
        raw_url = str(task.get("url") or "")
        joiner = "&" if "#" in raw_url else "#"
        task["launch_url"] = f"{raw_url}{joiner}local-booru-resolver={marker}"
        task["bootstrap_last_attempt"] = now
        _EXTERNAL_BROWSER_BOOTSTRAP_ID = tid
        return tid, str(task["launch_url"]), dict(task.get("_open_settings") or {})
    return None


def _external_browser_open_bootstrap_locked(request: tuple[str, str, dict[str, Any]] | None) -> None:
    global _EXTERNAL_BROWSER_BOOTSTRAP_ID
    if not request:
        return
    tid, launch_url, open_settings = request
    try:
        from core.external_browser import open_external_browser_url
        opened = bool(open_external_browser_url(launch_url, open_settings, log_fn=log.info))
    except Exception:
        opened = False
    if not opened and _EXTERNAL_BROWSER_BOOTSTRAP_ID == tid:
        # Do not leave a phantom bootstrap owner. A waiting task can retry or a
        # later task can be promoted without opening multiple tabs at once.
        _EXTERNAL_BROWSER_BOOTSTRAP_ID = ""


def enqueue_external_browser_resolve(
    url: str,
    *,
    settings: dict[str, Any] | None = None,
    timeout_s: float = 120.0,
    action: str = "navigate",
    upload_path: str | None = None,
    file_selectors: list[str] | None = None,
    submit_selectors: list[str] | None = None,
) -> dict[str, Any] | None:
    global _EXTERNAL_BROWSER_TASK_SEQ, _EXTERNAL_BROWSER_BOOTSTRAP_ID
    raw_url = str(url or "").strip()
    if not raw_url.startswith(("http://", "https://")) or not _external_browser_host_allowed(raw_url):
        return None
    action = str(action or "navigate").strip().lower()
    if action not in {"navigate", "upload_form"}:
        return None
    try:
        timeout_s = max(5.0, min(600.0, float(timeout_s or 120.0)))
    except Exception:
        timeout_s = 120.0

    file_b64 = ""
    file_name = ""
    file_mime = ""
    if action == "upload_form":
        try:
            media = Path(str(upload_path or ""))
            if not media.is_file():
                return None
            raw = media.read_bytes()
            max_bytes = int((settings or {}).get("external_browser_upload_max_mb", 12) or 12) * 1024 * 1024
            max_bytes = max(1024 * 1024, min(32 * 1024 * 1024, max_bytes))
            if len(raw) > max_bytes:
                return {"status": 0, "error": "external_browser_upload_too_large", "url": raw_url}
            file_b64 = base64.b64encode(raw).decode("ascii")
            file_name = media.name[:240]
            file_mime = mimetypes.guess_type(media.name)[0] or "application/octet-stream"
        except Exception as exc:
            return {"status": 0, "error": f"external_browser_upload_read_failed:{type(exc).__name__}", "url": raw_url}

    with _EXTERNAL_BROWSER_TASK_COND:
        _EXTERNAL_BROWSER_TASK_SEQ += 1
        task_id = f"browser-{int(time.time())}-{_EXTERNAL_BROWSER_TASK_SEQ}-{secrets.token_hex(8)}"
        task = {
            "id": task_id,
            "token": secrets.token_urlsafe(24),
            "url": raw_url,
            "action": action,
            "created_at": time.time(),
            "queue_deadline": time.time() + timeout_s,
            "deadline": 0.0,
            "timeout_seconds": timeout_s,
            "state": "pending",
            "result": None,
            "file_b64": file_b64,
            "file_name": file_name,
            "file_mime": file_mime,
            "file_selectors": [str(x)[:200] for x in (file_selectors or ["input[type='file']"]) if str(x).strip()][:12],
            "submit_selectors": [str(x)[:200] for x in (submit_selectors or []) if str(x).strip()][:12],
            "preferred_browser": "",
            "claim_marker": "",
            "launch_url": raw_url,
        }
        try:
            from core.external_browser import preferred_browser_family
            task["preferred_browser"] = preferred_browser_family(settings)
        except Exception:
            task["preferred_browser"] = ""
        auto_open = bool((settings or {}).get("external_browser_auto_open", True))
        task["auto_open"] = auto_open
        task["_open_settings"] = dict(settings or {})

        # A live paired Companion owns navigation through one reusable worker
        # tab. When no matching Companion heartbeat exists, only the oldest
        # pending task may bootstrap one explicitly marked browser tab.
        _EXTERNAL_BROWSER_TASKS[task_id] = task
        bootstrap = _external_browser_prepare_bootstrap_locked()
        _EXTERNAL_BROWSER_TASK_COND.notify_all()
        _external_browser_open_bootstrap_locked(bootstrap)

        while True:
            current = _EXTERNAL_BROWSER_TASKS.get(task_id)
            if not current:
                return None
            if current.get("state") in {"done", "error"}:
                result = current.get("result") if isinstance(current.get("result"), dict) else None
                _EXTERNAL_BROWSER_TASKS.pop(task_id, None)
                if _EXTERNAL_BROWSER_BOOTSTRAP_ID == task_id:
                    _EXTERNAL_BROWSER_BOOTSTRAP_ID = ""
                bootstrap = _external_browser_prepare_bootstrap_locked()
                _EXTERNAL_BROWSER_TASK_COND.notify_all()
                _external_browser_open_bootstrap_locked(bootstrap)
                return result
            deadline = _bridge_task_deadline(current)
            now = time.time()
            if not deadline or now >= deadline:
                break
            _EXTERNAL_BROWSER_TASK_COND.wait(timeout=min(1.0, max(0.1, deadline - now)))
            bootstrap = _external_browser_prepare_bootstrap_locked()
            _external_browser_open_bootstrap_locked(bootstrap)
        _EXTERNAL_BROWSER_TASKS.pop(task_id, None)
        if _EXTERNAL_BROWSER_BOOTSTRAP_ID == task_id:
            _EXTERNAL_BROWSER_BOOTSTRAP_ID = ""
        bootstrap = _external_browser_prepare_bootstrap_locked()
        _EXTERNAL_BROWSER_TASK_COND.notify_all()
        _external_browser_open_bootstrap_locked(bootstrap)
        return {"status": 0, "url": raw_url, "error": "external_browser_timeout", "challenge": True}


def _external_browser_next_task(client: dict[str, Any] | None = None) -> dict[str, Any]:
    global _EXTERNAL_BROWSER_ACTIVE_ID, _EXTERNAL_BROWSER_BOOTSTRAP_ID
    now = time.time()
    client_browser = str((client or {}).get("browser") or "")
    client_page_url = str((client or {}).get("page_url") or "")
    try:
        from core.external_browser import browser_family_matches
    except Exception:
        browser_family_matches = lambda _preferred, _actual: True
    with _EXTERNAL_BROWSER_TASK_COND:
        for tid, task in list(_EXTERNAL_BROWSER_TASKS.items()):
            if _bridge_task_expired(task, now):
                _EXTERNAL_BROWSER_TASKS.pop(tid, None)
                if _EXTERNAL_BROWSER_ACTIVE_ID == tid:
                    _EXTERNAL_BROWSER_ACTIVE_ID = ""
                if _EXTERNAL_BROWSER_BOOTSTRAP_ID == tid:
                    _EXTERNAL_BROWSER_BOOTSTRAP_ID = ""
        if _EXTERNAL_BROWSER_ACTIVE_ID:
            active = _EXTERNAL_BROWSER_TASKS.get(_EXTERNAL_BROWSER_ACTIVE_ID)
            if active and active.get("state") == "inflight" and not _bridge_task_expired(active, now):
                return {"ok": True, "has_task": False, "busy": True, "pending": len(_EXTERNAL_BROWSER_TASKS)}
            _EXTERNAL_BROWSER_ACTIVE_ID = ""
        for tid, task in _EXTERNAL_BROWSER_TASKS.items():
            if task.get("state") not in {"pending", "inflight"}:
                continue
            preferred = str(task.get("preferred_browser") or "")
            if preferred and not browser_family_matches(preferred, client_browser):
                continue
            claim_marker = str(task.get("claim_marker") or "")
            if claim_marker and claim_marker not in client_page_url:
                continue
            _bridge_task_claim(task, now)
            _EXTERNAL_BROWSER_ACTIVE_ID = tid
            return {
                "ok": True,
                "has_task": True,
                "task": {
                    "id": tid,
                    "token": str(task.get("token") or ""),
                    "url": str(task.get("url") or ""),
                    "action": str(task.get("action") or "navigate"),
                    "timeout_seconds": int(float(task.get("timeout_seconds") or 120)),
                    "file_b64": str(task.get("file_b64") or ""),
                    "file_name": str(task.get("file_name") or ""),
                    "file_mime": str(task.get("file_mime") or ""),
                    "file_selectors": list(task.get("file_selectors") or []),
                    "submit_selectors": list(task.get("submit_selectors") or []),
                    "preferred_browser": str(task.get("preferred_browser") or ""),
                    "claim_marker": str(task.get("claim_marker") or ""),
                },
            }
        return {"ok": True, "has_task": False, "pending": len(_EXTERNAL_BROWSER_TASKS)}


def _external_browser_store_result(data: dict[str, Any]) -> dict[str, Any]:
    global _EXTERNAL_BROWSER_ACTIVE_ID, _EXTERNAL_BROWSER_BOOTSTRAP_ID
    tid = str((data or {}).get("id") or "").strip()
    token = str((data or {}).get("token") or "").strip()
    if not tid or not token:
        return {"ok": False, "error": "missing_id_or_token"}
    with _EXTERNAL_BROWSER_TASK_COND:
        task = _EXTERNAL_BROWSER_TASKS.get(tid)
        if not task or not secrets.compare_digest(token, str(task.get("token") or "")):
            return {"ok": False, "error": "unknown_expired_or_bad_token"}
        result_url = str((data or {}).get("url") or task.get("url") or "")[:4000]
        if result_url and not _external_browser_host_allowed(result_url):
            result_url = str(task.get("url") or "")
        result = {
            "status": int((data or {}).get("status") or 0),
            "url": result_url,
            "headers": (data or {}).get("headers") if isinstance((data or {}).get("headers"), dict) else {},
            "text": str((data or {}).get("text") or "")[:4_500_000],
            "error": str((data or {}).get("error") or "")[:500],
            "challenge": bool((data or {}).get("challenge", False)),
            "bridge_mode": str((data or {}).get("bridge_mode") or "external_browser_extension")[:120],
            "page_title": str((data or {}).get("page_title") or "")[:500],
            "page_probe": str((data or {}).get("page_probe") or "")[:2000],
            "browser": str((data or {}).get("browser") or "")[:80],
            "upload_applied": bool((data or {}).get("upload_applied", False)),
            "submit_clicked": bool((data or {}).get("submit_clicked", False)),
        }
        task["state"] = "error" if result.get("error") else "done"
        task["result"] = result
        if _EXTERNAL_BROWSER_ACTIVE_ID == tid:
            _EXTERNAL_BROWSER_ACTIVE_ID = ""
        if _EXTERNAL_BROWSER_BOOTSTRAP_ID == tid:
            _EXTERNAL_BROWSER_BOOTSTRAP_ID = ""
        _EXTERNAL_BROWSER_TASK_COND.notify_all()
    return {"ok": True}

@dataclass
class CompanionServerHandle:
    server: ThreadingHTTPServer
    thread: threading.Thread
    host: str
    port: int
    importer: BrowserImportManager | None = None

    def stop(self) -> None:
        try:
            self.server.shutdown()
        except Exception:
            pass
        try:
            self.server.server_close()
        except Exception:
            pass
        if self.importer is not None:
            try:
                self.importer.stop()
            except Exception:
                pass


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _origin_allowed(origin: str) -> bool:
    origin = str(origin or "").strip()
    # Empty Origin is not a browser-extension proof.  Local curl/Python requests
    # can set the companion header, so POST/OPTIONS must require an extension
    # origin in addition to the X-Local-Booru-Companion marker.
    if not origin:
        return False
    return origin.startswith(_ALLOWED_EXTENSION_ORIGINS)


class _Handler(BaseHTTPRequestHandler):
    server_version = "LocalBooruCompanion/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # keep normal console clean
        log.debug("browser companion: " + fmt, *args)

    @property
    def settings(self) -> dict:
        return getattr(self.server, "local_booru_settings", {}) or {}

    def _send_json(self, status: int, payload: Any, *, cors: bool = True) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin", "")
        if cors and _origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", f"Content-Type,{_COMPANION_HEADER},{PAIRING_HEADER},X-Local-Booru-Task-Token")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The browser went away mid-answer (closed, reloaded the extension, a long poll was cut):
            # nothing to answer to, and not an error of the program.
            self.close_connection = True
            log.debug("companion client disconnected before the answer: %s", self.path)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = min(int(self.headers.get("Content-Length", "0") or 0), 5_000_000)
        except Exception:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {}

    def _protocol_marker_ok(self) -> bool:
        return _origin_allowed(self.headers.get("Origin", "")) and self.headers.get(_COMPANION_HEADER, "").strip() == "1"

    def _paired_client(self):
        if not self._protocol_marker_ok():
            return None
        if not bool(self.settings.get("browser_companion_pairing_required", True)):
            from types import SimpleNamespace
            return SimpleNamespace(extension_id="legacy", display_name="Legacy Companion", origin=self.headers.get("Origin", ""))
        return authenticate_pairing(
            self.settings,
            token=self.headers.get(PAIRING_HEADER, "").strip(),
            origin=self.headers.get("Origin", ""),
        )

    def _authorized_companion(self) -> bool:
        return self._paired_client() is not None

    def _authorized_post(self) -> bool:
        return self._authorized_companion()

    def _authorized_get(self) -> bool:
        return self._authorized_companion()

    def _attach_task_token(self, payload: dict[str, Any], lane: str, client) -> dict[str, Any]:
        task = payload.get("task") if isinstance(payload.get("task"), dict) else None
        if payload.get("has_task") and task and task.get("id"):
            task["task_token"] = issue_task_token(
                self.settings, task_id=str(task["id"]), extension_id=str(client.extension_id), lane=lane,
                ttl_seconds=max(300, int(task.get("timeout_seconds") or 300) + 180),
            )
        return payload

    def _valid_result_task(self, data: dict[str, Any], lane: str, client) -> bool:
        return validate_task_token(
            self.settings, task_id=str(data.get("id") or ""), extension_id=str(client.extension_id), lane=lane,
            token=str(data.get("task_token") or self.headers.get("X-Local-Booru-Task-Token", "")), consume=True,
        )

    def _status_payload(self) -> dict[str, Any]:
        profile_payload = list_profiles(self.settings)
        return {
            "ok": True,
            "service": "Local Booru Browser Companion",
            "version": 466,
            "enabled": bool(self.settings.get("browser_companion_api_enabled", True)),
            "pairing_required": bool(self.settings.get("browser_companion_pairing_required", True)),
            "paired_extensions": len([x for x in list_extensions(self.settings) if not int(x.get("revoked_at") or 0)]),
            "site_profiles": {"schema_version": 1, "endpoint": "/extension/profiles", "count": len(profile_payload.get("profiles") or []), "revision": int(profile_payload.get("revision") or 0)},
            "domain_credentials": [{k: row.get(k) for k in ("domain","cookie_count","received_at","last_verified_at","last_error","revoked_at")} for row in list_domain_credentials(self.settings)],
            "download_policy": __import__("core.download_policy", fromlist=["session_status"]).session_status(self.settings),
            "tag_alias_sources": {site: cfg.get("page_url", "") for site, cfg in __import__("core.tag_relations", fromlist=["ALIAS_SOURCES"]).ALIAS_SOURCES.items()},
            "e621_bridge": e621_bridge_status(),
            "pixiv_bridge": pixiv_bridge_status(),
            "external_browser": external_browser_bridge_status(),
            "browser_import": (_IMPORT_MANAGER.status() if _IMPORT_MANAGER is not None else {"queued": 0, "active": 0, "recent": []}),
            # (0.8.0) the program's blocklist changed → the long poll wakes, the extension takes the new one
            "blocklist_rev": __import__("core.companion_page", fromlist=["blocklist_rev"]).blocklist_rev(),
            # (v5.44.100, 0.10.0) «Сайты манги» asked the extension (or a site's address changed) → it wakes too
            "manga_signin": __import__("core.manhwa_access", fromlist=["browser_rev"]).browser_rev(),
        }

    def do_OPTIONS(self) -> None:
        if _origin_allowed(self.headers.get("Origin", "")):
            self._send_json(204, {}, cors=True)
        else:
            self._send_json(403, {"ok": False, "error": "origin_not_allowed"}, cors=False)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if not bool(self.settings.get("browser_companion_api_enabled", True)):
            self._send_json(503, {"ok": False, "error": "browser_companion_disabled"}, cors=False)
            return
        if not self._authorized_get():
            if self._protocol_marker_ok() and path in {"/", "/extension/status", "/extension/health"}:
                self._send_json(401, {"ok": False, "error": "pairing_required", "version": 466, "pairing_required": True})
            else:
                self._send_json(403, {"ok": False, "error": "forbidden"}, cors=False)
            return
        if path in {"/", "/extension/status", "/extension/health"}:
            self._send_json(200, self._status_payload())
            return
        if path == "/extension/profiles":
            self._send_json(200, {"ok": True, **list_profiles(self.settings)})
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not bool(self.settings.get("browser_companion_api_enabled", True)):
            self._send_json(503, {"ok": False, "error": "browser_companion_disabled"})
            return
        try:
            data = self._read_json()
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": "bad_json", "message": str(exc)[:120]})
            return
        if path == "/extension/pair":
            if not self._protocol_marker_ok():
                self._send_json(403, {"ok": False, "error": "forbidden"}, cors=False)
                return
            result = exchange_pairing_code(
                self.settings, code=str(data.get("code") or ""), origin=self.headers.get("Origin", ""),
                display_name=str(data.get("display_name") or "Browser Companion"),
            )
            self._send_json(200 if result.get("ok") else 401, result)
            return
        client = self._paired_client()
        if client is None:
            self._send_json(401 if self._protocol_marker_ok() else 403, {"ok": False, "error": "pairing_required" if self._protocol_marker_ok() else "forbidden"}, cors=self._protocol_marker_ok())
            return
        try:                                  # (v5.44.100) «Сайты манги» shows whether the extension is there
            from core import manhwa_access
            manhwa_access.browser_seen(str(data.get("version") or ""), manga=path.startswith("/extension/manga/"))
        except Exception:                     # noqa: BLE001 — a note for a page, never the extension's answer
            pass
        if path in {"/extension/status", "/extension/health"}:
            self._send_json(200, self._status_payload())
            return
        if path == "/extension/manga/sites":
            # (v5.44.100, extension 0.10.0) the sites of manga: their domains (whose cookies are theirs), the address
            # «Открыть сайт» opens, the localStorage key of a Lib site's token
            from core import manhwa_access
            self._send_json(200, {"ok": True, "sites": manhwa_access.browser_sites(), "rev": manhwa_access.browser_rev()})
            return
        if path == "/extension/manga/signin":
            # …what the owner's browser has of one of them (its page loaded, its cookies changed, the program asked)
            from core import manhwa_access
            asked = str(data.get("asked") or "0")
            try:
                got = manhwa_access.take_from_browser(
                    str(data.get("site") or "")[:40], data.get("cookies") if isinstance(data.get("cookies"), list) else [],
                    str(data.get("token") or ""), asked=int(asked) if asked.isdigit() else 0,
                    missing=data.get("missing") if isinstance(data.get("missing"), list) else [])
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": "unknown_site", "message": str(exc)[:200]})
                return
            self._send_json(200, {"ok": True, **got})
            return
        if path == "/extension/manga/next":
            # …the asks of «Сайты манги»: «Открыть сайт», «Обновить вход»
            from core import manhwa_access
            self._send_json(200, {"ok": True, "asks": manhwa_access.browser_asks(), "rev": manhwa_access.browser_rev()})
            return
        if path == "/extension/open":
            self._send_json(200, open_card(self.settings, data.get("image_id")))
            return
        if path == "/extension/pairing/revoke_self":
            revoked = revoke_extension(self.settings, str(client.extension_id))
            self._send_json(200, {"ok": True, "revoked": bool(revoked)})
            return
        if path == "/extension/browser/heartbeat":
            self._send_json(200, external_browser_heartbeat(data))
            return
        if path == "/extension/browser/next":
            external_browser_heartbeat(data)
            self._send_json(200, self._attach_task_token(_external_browser_next_task(data), "browser", client))
            return
        if path == "/extension/browser/result":
            external_browser_heartbeat(data)
            if not validate_task_token(
                self.settings, task_id=str(data.get("id") or ""), extension_id=str(client.extension_id),
                lane="browser", token=str(data.get("task_token") or self.headers.get("X-Local-Booru-Task-Token", "")),
                consume=False,
            ):
                self._send_json(403, {"ok": False, "error": "bad_task_token"})
            else:
                stored = _external_browser_store_result(data)
                if stored.get("ok"):
                    validate_task_token(
                        self.settings, task_id=str(data.get("id") or ""), extension_id=str(client.extension_id),
                        lane="browser", token=str(data.get("task_token") or self.headers.get("X-Local-Booru-Task-Token", "")),
                        consume=True,
                    )
                self._send_json(200, stored)
            return
        if path == "/extension/e621/next":
            self._send_json(200, self._attach_task_token(_e621_bridge_next_task(), "e621", client))
            return
        if path == "/extension/e621/result":
            if not validate_task_token(
                self.settings, task_id=str(data.get("id") or ""), extension_id=str(client.extension_id),
                lane="e621", token=str(data.get("task_token") or self.headers.get("X-Local-Booru-Task-Token", "")),
                consume=False,
            ):
                self._send_json(403, {"ok": False, "error": "bad_task_token"})
            else:
                stored = _e621_bridge_store_result(data)
                if stored.get("ok"):
                    validate_task_token(
                        self.settings, task_id=str(data.get("id") or ""), extension_id=str(client.extension_id),
                        lane="e621", token=str(data.get("task_token") or self.headers.get("X-Local-Booru-Task-Token", "")),
                        consume=True,
                    )
                self._send_json(200, stored)
            return
        if path == "/extension/pixiv/next":
            self._send_json(200, self._attach_task_token(_pixiv_bridge_next_task(), "pixiv", client))
            return
        if path == "/extension/pixiv/result":
            if not validate_task_token(
                self.settings, task_id=str(data.get("id") or ""), extension_id=str(client.extension_id),
                lane="pixiv", token=str(data.get("task_token") or self.headers.get("X-Local-Booru-Task-Token", "")),
                consume=False,
            ):
                self._send_json(403, {"ok": False, "error": "bad_task_token"})
            else:
                stored = _pixiv_bridge_store_result(data)
                if stored.get("ok"):
                    validate_task_token(
                        self.settings, task_id=str(data.get("id") or ""), extension_id=str(client.extension_id),
                        lane="pixiv", token=str(data.get("task_token") or self.headers.get("X-Local-Booru-Task-Token", "")),
                        consume=True,
                    )
                self._send_json(200, stored)
            return
        if path == "/extension/profiles":
            self._send_json(200, {"ok": True, **list_profiles(self.settings)})
            return
        if path == "/extension/session/credentials":
            if not bool(self.settings.get("browser_companion_cookie_handoff_enabled", True)):
                self._send_json(403, {"ok": False, "error": "cookie_handoff_disabled"})
            else:
                try:
                    result = store_domain_credentials(
                        self.settings, domain=str(data.get("domain") or ""),
                        cookies=data.get("cookies") if isinstance(data.get("cookies"), list) else [],
                        user_agent=str(data.get("user_agent") or ""),
                    )
                    self._send_json(200, result)
                except Exception as exc:
                    self._send_json(400, {"ok": False, "error": "credential_store_failed", "message": str(exc)[:200]})
            return
        if path == "/extension/session/revoke":
            self._send_json(200, {"ok": True, "revoked": revoke_domain(self.settings, str(data.get("domain") or ""))})
            return
        if path == "/extension/events/wait":
            before = str(data.get("signature") or "")
            deadline = time.monotonic() + max(1.0, min(25.0, float(self.settings.get("browser_companion_event_wait_seconds", 20) or 20)))
            payload = self._status_payload()
            def signature(p):
                compact = {k: p.get(k) for k in ("e621_bridge","pixiv_bridge","external_browser","browser_import","download_policy","blocklist_rev","manga_signin")}
                return hashlib.sha256(_json_bytes(compact)).hexdigest()
            current = signature(payload)
            while before and current == before and time.monotonic() < deadline:
                time.sleep(0.25)
                payload = self._status_payload(); current = signature(payload)
            self._send_json(200, {"ok": True, "changed": current != before, "signature": current, "status": payload})
            return
        if path == "/extension/import_batch":
            if _IMPORT_MANAGER is None:
                self._send_json(503, {"ok": False, "error": "browser_import_not_ready"})
            else:
                items = data.get("items") if isinstance(data.get("items"), list) else []
                limit = max(1, min(int(self.settings.get("browser_companion_max_batch", 250) or 250), 1000))
                results = [_IMPORT_MANAGER.submit(item) for item in items[:limit] if isinstance(item, dict)]
                self._send_json(202, {"ok": True, "submitted": len(results), "results": results})
            return
        if path == "/extension/import":
            if _IMPORT_MANAGER is None:
                self._send_json(503, {"ok": False, "error": "browser_import_not_ready"})
            else:
                item = data.get("item") if isinstance(data.get("item"), dict) else data
                self._send_json(202, _IMPORT_MANAGER.submit(item if isinstance(item, dict) else {}))
            return
        if path == "/extension/check":
            self._send_json(200, check_items(self.settings, data.get("items") or []))
            return
        if path == "/extension/manga":
            # (v5.44, extension 0.9.0) the galleries of a manga site's page: the manga folder has this one, or
            # another version of its work (another language, another upload)
            from core.manga_works import check as manga_check
            self._send_json(200, manga_check(self.settings, data.get("items") or []))
            return
        if path == "/extension/index":
            # The extension's copy of «what the library has», for marking cards while the program is closed.
            from core.browser_companion_index import build_index
            self._send_json(200, build_index(self.settings, data.get("cursor")))
            return
        if path in {"/extension/subscription", "/extension/subscribe"}:
            # (0.8.0) «Подписаться» in the panel: the search of the site page (its address) as a subscription
            from core import companion_page
            act = companion_page.subscribe if path == "/extension/subscribe" else companion_page.subscription_state
            self._send_json(200, act(str(data.get("url") or "")[:2000]))
            return
        if path == "/extension/blocklist":
            from core.companion_page import blocklist
            self._send_json(200, blocklist())
            return
        if path == "/extension/hide":
            item = data.get("item") if isinstance(data.get("item"), dict) else data
            identities = identities_for_item(item if isinstance(item, dict) else {})
            changed = add_exclusion(
                self.settings,
                identities,
                reason="browser_manual_hide",
                query=str(data.get("query") or ""),
                site=normalize_host((item or {}).get("site") if isinstance(item, dict) else ""),
                note="Hidden from browser companion; parser/tagger still allowed.",
            )
            self._send_json(200, {"ok": True, "changed": changed, "identities": [f"{t}:{v}" for t, v in identities]})
            return
        self._send_json(404, {"ok": False, "error": "not_found"})


def start_browser_companion_api(settings: dict | None = None, *, log_fn=None) -> CompanionServerHandle | None:
    settings = dict(settings or load_settings() or {})
    if not bool(settings.get("browser_companion_api_enabled", True)):
        if log_fn:
            log_fn("Browser companion API disabled")
        return None
    host = str(settings.get("browser_companion_api_host", DEFAULT_HOST) or DEFAULT_HOST).strip() or DEFAULT_HOST
    # Never expose this API on LAN by accident.  It reads library metadata and can
    # write visual hide rows, so it must stay loopback-only.
    if host not in {"127.0.0.1", "localhost", "::1"}:
        host = DEFAULT_HOST
    try:
        port = int(settings.get("browser_companion_api_port", DEFAULT_PORT) or DEFAULT_PORT)
    except Exception:
        port = DEFAULT_PORT
    port = max(1024, min(65535, port))
    try:
        global _IMPORT_MANAGER
        with _MANAGER_LOCK:                   # one lane: a page may have made it before the API started
            if _IMPORT_MANAGER is None or not _IMPORT_MANAGER.alive:
                _IMPORT_MANAGER = BrowserImportManager(settings, log_fn=log_fn)
            importer = _IMPORT_MANAGER
        server = ThreadingHTTPServer((host, port), _Handler)
        server.local_booru_settings = settings  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, name="browser-companion-api", daemon=True)
        thread.start()
        if log_fn:
            log_fn(f"Browser companion API: http://{host}:{port}/extension/status")
        return CompanionServerHandle(server=server, thread=thread, host=host, port=port, importer=importer)
    except OSError as exc:
        msg = f"Browser companion API failed on {host}:{port}: {exc}"
        if log_fn:
            log_fn(msg)
        else:
            log.warning(msg)
        return None
