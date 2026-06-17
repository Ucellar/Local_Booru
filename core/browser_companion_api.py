"""Local browser-extension companion API for booru pages.

The Chrome/Chromium extension never reads the SQLite database directly.  It
collects visible booru cards in the page, then asks this localhost-only API
whether a post/file identity is already present in Local Booru.

Scope is deliberately visual-only: hidden browser/grabber cards do not block the
parser, exact-MD5 source fanout, source merge, or no-match recovery.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from core.database.connection import db
from core.grabber_exclusions import add_exclusion, compact_identities, is_excluded, normalize_md5
from core.settings import load_settings

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
        "reason": "local_md5",
    }


def check_one(settings: dict | None, item: dict[str, Any]) -> dict[str, Any]:
    item = item if isinstance(item, dict) else {}
    site = normalize_host(item.get("site") or item.get("host") or item.get("post_url") or item.get("file_url"))
    post_url = normalize_url(item.get("post_url"))
    file_url = normalize_url(item.get("file_url"))
    md5 = normalize_md5(item.get("md5")) or extract_md5_from_url(file_url) or extract_md5_from_url(post_url)
    post_id = extract_post_id(site, post_url, item.get("post_id"))

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
                    """SELECT id, file_name, hash_md5, bucket, lifecycle FROM images
                       WHERE deleted=0 AND lower(COALESCE(hash_md5,''))=?
                       ORDER BY id DESC LIMIT 1""",
                    (md5,),
                ).fetchone()
                if row:
                    result.update(_row_to_status(row))
                    result["reason"] = "local_md5"
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
                        f"""SELECT i.id, i.file_name, i.hash_md5, i.bucket, i.lifecycle, s.host
                            FROM sources s
                            JOIN image_sources isrc ON isrc.source_id=s.id
                            JOIN images i ON i.id=isrc.image_id
                            WHERE i.deleted=0 AND s.url IN ({ph})
                            ORDER BY i.id DESC LIMIT 1""",
                        urls,
                    ).fetchone()
                    if row:
                        result.update(_row_to_status(row))
                        result["reason"] = "source_url"
            if result["status"] == "new" and site and post_id:
                urls = _source_post_url_candidates({**item, "site": site, "post_url": post_url, "post_id": post_id})
                if urls:
                    ph = ",".join(["?"] * len(urls))
                    row = con.execute(
                        f"""SELECT i.id, i.file_name, i.hash_md5, i.bucket, i.lifecycle, s.host
                            FROM sources s
                            JOIN image_sources isrc ON isrc.source_id=s.id
                            JOIN images i ON i.id=isrc.image_id
                            WHERE i.deleted=0 AND s.host=? AND s.url IN ({ph})
                            ORDER BY i.id DESC LIMIT 1""",
                        (site, *urls),
                    ).fetchone()
                    if row:
                        result.update(_row_to_status(row))
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
    except Exception as exc:
        log.warning("browser companion DB check failed: %s", exc)
        result.update({"status": "error", "action": "show", "reason": str(exc)[:180]})
    return result


def check_items(settings: dict | None, items: list[dict[str, Any]]) -> dict[str, Any]:
    max_items = int((settings or {}).get("browser_companion_max_batch", 250) or 250)
    max_items = max(1, min(1000, max_items))
    items = [x for x in (items or []) if isinstance(x, dict)][:max_items]
    return {
        "ok": True,
        "items": [check_one(settings, x) for x in items],
        "checked": len(items),
        "server_time": int(time.time()),
    }



# --- e621 browser-extension API bridge --------------------------------------
# The app cannot reliably defeat Cloudflare with a freshly launched automation
# browser.  The companion extension can fetch e621 JSON from the user's normal
# Chrome/Edge session after the user has passed the verification page.  This
# remains JSON API access; it is not HTML scraping and it does not expose the
# local archive to the extension.
_E621_TASK_COND = threading.Condition()
_E621_TASKS: dict[str, dict[str, Any]] = {}
_E621_TASK_SEQ = 0


def enqueue_e621_browser_fetch(url: str, *, auth_header: str = "", timeout_s: float = 120.0) -> dict[str, Any] | None:
    """Ask the installed browser companion extension to fetch an e621/e926 JSON URL.

    Returns a small response-like dictionary with status/url/headers/text, or
    None if no companion extension answers in time.  The extension must be
    installed and an e621/e926 tab must be open in the user's normal browser.
    """
    global _E621_TASK_SEQ
    raw_url = str(url or "").strip()
    if not raw_url.startswith(("https://e621.net/", "https://e926.net/")):
        return None
    try:
        timeout_s = max(1.0, float(timeout_s or 120.0))
    except Exception:
        timeout_s = 120.0
    with _E621_TASK_COND:
        _E621_TASK_SEQ += 1
        task_id = f"e621-{int(time.time())}-{_E621_TASK_SEQ}"
        _E621_TASKS[task_id] = {
            "id": task_id,
            "url": raw_url,
            "authorization": str(auth_header or ""),
            "created_at": time.time(),
            "deadline": time.time() + timeout_s,
            "state": "pending",
            "result": None,
        }
        _E621_TASK_COND.notify_all()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            remain = max(0.1, deadline - time.time())
            _E621_TASK_COND.wait(timeout=min(1.0, remain))
            task = _E621_TASKS.get(task_id)
            if not task:
                return None
            if task.get("state") == "done":
                result = task.get("result") if isinstance(task.get("result"), dict) else None
                _E621_TASKS.pop(task_id, None)
                return result
            if task.get("state") == "error":
                result = task.get("result") if isinstance(task.get("result"), dict) else None
                _E621_TASKS.pop(task_id, None)
                return result
        _E621_TASKS.pop(task_id, None)
        _E621_TASK_COND.notify_all()
        return None


def _e621_bridge_next_task() -> dict[str, Any]:
    now = time.time()
    with _E621_TASK_COND:
        for tid, task in list(_E621_TASKS.items()):
            if float(task.get("deadline") or 0) < now:
                _E621_TASKS.pop(tid, None)
                continue
            if task.get("state") in {"pending", "inflight"}:
                # Allow another poller to retry an old inflight task after a few seconds.
                inflight_at = float(task.get("inflight_at") or 0)
                if task.get("state") == "inflight" and now - inflight_at < 8.0:
                    continue
                task["state"] = "inflight"
                task["inflight_at"] = now
                return {
                    "ok": True,
                    "has_task": True,
                    "task": {
                        "id": tid,
                        "url": str(task.get("url") or ""),
                        "authorization": str(task.get("authorization") or ""),
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


@dataclass
class CompanionServerHandle:
    server: ThreadingHTTPServer
    thread: threading.Thread
    host: str
    port: int

    def stop(self) -> None:
        try:
            self.server.shutdown()
        except Exception:
            pass
        try:
            self.server.server_close()
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
            self.send_header("Access-Control-Allow-Headers", f"Content-Type,{_COMPANION_HEADER}")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

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

    def _authorized_post(self) -> bool:
        if not _origin_allowed(self.headers.get("Origin", "")):
            return False
        return self.headers.get(_COMPANION_HEADER, "").strip() == "1"

    def do_OPTIONS(self) -> None:
        if _origin_allowed(self.headers.get("Origin", "")):
            self._send_json(204, {}, cors=True)
        else:
            self._send_json(403, {"ok": False, "error": "origin_not_allowed"}, cors=False)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/extension/status", "/extension/health"}:
            self._send_json(200, {
                "ok": True,
                "service": "Local Booru Browser Companion",
                "version": 315,
                "enabled": bool(self.settings.get("browser_companion_api_enabled", True)),
                "e621_bridge": e621_bridge_status(),
            })
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if not bool(self.settings.get("browser_companion_api_enabled", True)):
            self._send_json(503, {"ok": False, "error": "browser_companion_disabled"})
            return
        if not self._authorized_post():
            self._send_json(403, {"ok": False, "error": "forbidden"}, cors=False)
            return
        path = urlparse(self.path).path
        try:
            data = self._read_json()
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": "bad_json", "message": str(exc)[:120]})
            return
        if path == "/extension/e621/next":
            self._send_json(200, _e621_bridge_next_task())
            return
        if path == "/extension/e621/result":
            self._send_json(200, _e621_bridge_store_result(data))
            return
        if path == "/extension/check":
            self._send_json(200, check_items(self.settings, data.get("items") or []))
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
        server = ThreadingHTTPServer((host, port), _Handler)
        server.local_booru_settings = settings  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, name="browser-companion-api", daemon=True)
        thread.start()
        if log_fn:
            log_fn(f"Browser companion API: http://{host}:{port}/extension/status")
        return CompanionServerHandle(server=server, thread=thread, host=host, port=port)
    except OSError as exc:
        msg = f"Browser companion API failed on {host}:{port}: {exc}"
        if log_fn:
            log_fn(msg)
        else:
            log.warning(msg)
        return None
