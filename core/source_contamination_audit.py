"""Read-only audit for cross-file source/metadata contamination (v446).

The v445 parser hotfix prevents new rule34 relay state from leaking between
files.  This module deliberately does not repair old rows.  It inventories
images that have multiple source URLs from one host and, optionally, verifies
remote post MD5 values against the local canonical MD5 and the durable
``image_site_md5_aliases`` table.

No function in this module opens SQLite in write mode or mutates files.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
import re
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from core.database.connection import db, db_path
from core.redaction import sanitize_text


CLASSIFICATIONS = (
    "verified_local_md5",
    "verified_alias_same_image",
    "alias_bound_to_other_image",
    "verified_mismatch",
    "network_unverified",
    "post_missing",
    "parse_error",
)

_RELAY_METHOD_MARKERS = (
    "relay",
    "side_queue",
    "side-queue",
    "variant",
    "rule34_40hex",
    "rule34 40hex",
    "rule34_sha1",
    "pixel_hash",
)

_MD5_RE = re.compile(r"^[0-9a-f]{32}$", re.I)
_POST_PATH_RE = re.compile(r"/(?:posts?|post/show)/([0-9]+)(?:\.[a-z0-9]+)?/?$", re.I)


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _host(value: str) -> str:
    return str(value or "").strip().lower().replace("www.", "")


def _md5(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if _MD5_RE.fullmatch(text) else ""


def _post_id(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
        match = _POST_PATH_RE.search(parsed.path or "")
        if match:
            return match.group(1)
        query = parse_qs(parsed.query or "")
        for key in ("id", "post_id", "post"):
            value = str((query.get(key) or [""])[0] or "").strip()
            if value.isdigit():
                return value
    except Exception:
        pass
    return ""


def _bucket(source_count: int) -> str:
    n = int(source_count or 0)
    if n <= 2:
        return "2"
    if n <= 5:
        return "3-5"
    if n <= 10:
        return "6-10"
    if n <= 25:
        return "11-25"
    return "26+"


def _table_exists(con, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (str(name),),
    ).fetchone()
    return bool(row)


def _site_cfg(settings: dict, host: str) -> dict:
    normalized = _host(host)
    sites = dict((settings or {}).get("sites") or {})
    for key, value in sites.items():
        if _host(key) == normalized and isinstance(value, dict):
            return dict(value)
    for value in list((settings or {}).get("custom_sites") or []):
        if not isinstance(value, dict):
            continue
        candidate = _host(value.get("domain") or value.get("host") or urlparse(str(value.get("base_url") or "")).netloc)
        if candidate == normalized:
            return dict(value)
    return {}


def _api_user_agent(host: str, cfg: dict) -> str:
    configured = str(cfg.get("api_user_agent") or cfg.get("user_agent") or "").strip()
    if configured:
        return configured
    login = str(cfg.get("login") or "").strip()
    identity = f"by {login}" if login else "read-only source audit"
    return f"LocalBooru/4.46 ({identity}; {host})"


def _response_json(response: Any) -> Any:
    try:
        return response.json()
    except Exception:
        text = str(getattr(response, "text", "") or "")
        return json.loads(text)


def _post_from_payload(payload: Any) -> dict:
    if isinstance(payload, dict):
        if isinstance(payload.get("post"), dict):
            return dict(payload["post"])
        if isinstance(payload.get("posts"), list):
            first = payload["posts"][0] if payload["posts"] else None
            return dict(first) if isinstance(first, dict) else {}
        # A Danbooru-compatible concrete post is already the post object.
        if any(key in payload for key in ("id", "md5", "file", "file_url", "tag_string")):
            return dict(payload)
    if isinstance(payload, list):
        first = payload[0] if payload else None
        return dict(first) if isinstance(first, dict) else {}
    return {}


def _post_md5(post: dict) -> str:
    if not isinstance(post, dict):
        return ""
    direct = _md5(post.get("md5") or post.get("hash") or post.get("file_md5"))
    if direct:
        return direct
    file_data = post.get("file")
    if isinstance(file_data, dict):
        direct = _md5(file_data.get("md5") or file_data.get("hash"))
        if direct:
            return direct
    media_asset = post.get("media_asset")
    if isinstance(media_asset, dict):
        direct = _md5(media_asset.get("md5") or media_asset.get("pixel_hash"))
        if direct:
            return direct
    return ""


class RemotePostResolver:
    """Small requests-based resolver used only by the read-only audit."""

    def __init__(self, settings: dict, log: Callable[[str], None] | None = None,
                 stop_check: Callable[[], bool] | None = None):
        self.settings = dict(settings or {})
        self.log = log or (lambda _message: None)
        self.stop_check = stop_check or (lambda: False)
        # The same per-site pace as the parser (core.http_rate_limiter); «Остановить» cuts its waits.
        self._limiter_settings = dict(self.settings)
        self._limiter_settings["_cancel_callback"] = self.stop_check
        self.sessions: dict[str, Any] = {}
        try:
            self.timeout = float(self.settings.get("source_contamination_audit_timeout_seconds", 15) or 15)
        except Exception:
            self.timeout = 15.0
        self.timeout = max(3.0, min(60.0, self.timeout))

    def _session(self, host: str):
        host = _host(host)
        if host not in self.sessions:
            # Import lazily: ordinary library diagnostics must not load the full
            # parser engine unless network verification was explicitly selected.
            from core.tagger import get_session
            self.sessions[host] = get_session(self.settings, self.log, target_host=host)
        return self.sessions[host]

    def _request(self, host: str, url: str, *, params=None, auth=None, headers=None):
        from core.http_rate_limiter import apply_retry_after, wait_for
        from core.net import http_request
        wait_for(url, self._limiter_settings)          # raises RequestCancelled (InterruptedError) on «Остановить»
        response = http_request(
            self._session(host),
            "GET",
            url,
            params=params or None,
            auth=auth,
            headers=headers or None,
            timeout=(min(10.0, self.timeout), self.timeout),
            max_attempts=1,
        )
        apply_retry_after(response, self._limiter_settings)   # 429/503: the site asked to wait
        return response

    def __call__(self, host: str, source_url: str) -> dict[str, Any]:
        host = _host(host)
        source_url = str(source_url or "")
        post_id = _post_id(source_url)
        base = {
            "host": host,
            "source_url": source_url,
            "post_id": post_id,
            "status": "network_unverified",
            "site_md5": "",
            "http_status": 0,
            "reason": "",
        }
        if not post_id:
            base["reason"] = "unsupported_source_url_or_missing_post_id"
            return base
        if self.stop_check():                          # «Остановить»: the rest of the group is not asked
            base["reason"] = "stopped"
            return base
        cfg = _site_cfg(self.settings, host)
        headers = {"Accept": "application/json", "User-Agent": _api_user_agent(host, cfg)}
        request_url = ""
        params: dict[str, Any] = {}
        auth = None
        engine = str(cfg.get("engine") or cfg.get("type") or "").strip().lower()
        try:
            if host in ("e621.net", "e926.net") or engine in ("e621", "e926"):
                request_url = f"https://{host}/posts/{post_id}.json"
                params = {"v2": "true", "mode": "extended"}
                login = str(cfg.get("login") or "").strip()
                api_key = str(cfg.get("api_key") or "").strip()
                auth = (login, api_key) if login and api_key else None
            elif host in ("danbooru.donmai.us", "booru.allthefallen.moe") or engine in ("danbooru", "danbooru2"):
                request_url = f"https://{host}/posts/{post_id}.json"
                login = str(cfg.get("login") or "").strip()
                api_key = str(cfg.get("api_key") or "").strip()
                if login and api_key:
                    auth = (login, api_key)
                else:
                    params = {k: v for k, v in (("login", login), ("api_key", api_key)) if v}
            elif host in ("gelbooru.com", "rule34.xxx", "api.rule34.xxx") or engine in ("gelbooru", "gelbooru_dapi", "rule34"):
                api_host = "rule34.xxx" if host == "api.rule34.xxx" else host
                request_url = f"https://{api_host}/index.php"
                params = {"page": "dapi", "s": "post", "q": "index", "json": "1", "id": post_id, "limit": 1}
                if "rule34" in api_host:
                    for key in ("user_id", "api_key"):
                        value = str(cfg.get(key) or "").strip()
                        if value:
                            params[key] = value
                else:
                    for key in ("user_id", "api_key"):
                        value = str(cfg.get(key) or "").strip()
                        if value:
                            params[key] = value
            else:
                base["reason"] = f"unsupported_host:{host or '?'}"
                return base

            response = self._request(host, request_url, params=params, auth=auth, headers=headers)
            status_code = int(getattr(response, "status_code", 0) or 0)
            base["http_status"] = status_code
            if status_code in (404, 410):
                base.update(status="post_missing", reason=f"http_{status_code}")
                return base
            if status_code in (401, 403, 408, 425, 429, 500, 502, 503, 504, 520, 522, 524) or status_code <= 0:
                base.update(status="network_unverified", reason=f"http_{status_code or 'unknown'}")
                return base
            if status_code >= 400:
                base.update(status="network_unverified", reason=f"http_{status_code}")
                return base
            try:
                payload = _response_json(response)
            except Exception as exc:
                base.update(status="parse_error", reason=f"json:{type(exc).__name__}: {sanitize_text(str(exc))[:240]}")
                return base
            post = _post_from_payload(payload)
            if not post:
                base.update(status="post_missing", reason="empty_post_result")
                return base
            remote_md5 = _post_md5(post)
            if not remote_md5:
                base.update(status="parse_error", reason="post_has_no_usable_md5")
                return base
            base.update(
                status="resolved",
                site_md5=remote_md5,
                resolved_post_id=str(post.get("id") or post_id),
                reason="",
            )
            return base
        except InterruptedError:
            if self.stop_check():                      # stopped while waiting for the site's turn
                base["reason"] = "stopped"
                return base
            raise
        except Exception as exc:
            message = sanitize_text(f"{type(exc).__name__}: {exc}")[:300]
            lowered = message.lower()
            if any(marker in lowered for marker in ("timeout", "connection", "429", "500", "502", "503", "504", "network", "resolve")):
                base.update(status="network_unverified", reason=message)
            else:
                base.update(status="parse_error", reason=message)
            return base


def _classify_source(
    *,
    image_id: int,
    canonical_md5: str,
    remote: dict[str, Any],
    alias_row: dict[str, Any] | None,
    other_canonical_images: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    remote_status = str(remote.get("status") or "network_unverified")
    if remote_status in ("network_unverified", "post_missing", "parse_error"):
        return remote_status, {"reason": str(remote.get("reason") or "")}
    site_md5 = _md5(remote.get("site_md5"))
    local_md5 = _md5(canonical_md5)
    if not site_md5:
        return "parse_error", {"reason": "resolved_response_without_md5"}
    if not local_md5:
        return "network_unverified", {"reason": "canonical_md5_missing", "site_md5": site_md5}
    if site_md5 == local_md5:
        return "verified_local_md5", {"site_md5": site_md5}
    if alias_row:
        alias_image_id = int(alias_row.get("image_id") or 0)
        alias_live = not bool(int(alias_row.get("deleted") or 0))
        if alias_image_id == int(image_id):
            return "verified_alias_same_image", {
                "site_md5": site_md5,
                "alias_image_id": alias_image_id,
                "alias_site": str(alias_row.get("site") or ""),
                "alias_source_url": str(alias_row.get("source_url") or ""),
            }
        if alias_image_id and alias_live:
            return "alias_bound_to_other_image", {
                "site_md5": site_md5,
                "alias_image_id": alias_image_id,
                "alias_path": str(alias_row.get("path") or ""),
                "alias_local_md5": str(alias_row.get("local_md5") or ""),
                "alias_site": str(alias_row.get("site") or ""),
            }
    return "verified_mismatch", {
        "site_md5": site_md5,
        "canonical_md5": local_md5,
        "other_live_canonical_images": other_canonical_images,
        "stale_alias": dict(alias_row or {}) if alias_row else {},
    }


def format_source_contamination_text(report: dict[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    distributions = dict(report.get("distribution") or {})
    classes = dict(report.get("classifications") or {})
    lines = [
        "АУДИТ КОНТАМИНАЦИИ SOURCE — ТОЛЬКО ЧТЕНИЕ",
        f"Создано: {report.get('created_at', '')}",
        f"Сетевая проверка: {'включена' if report.get('network_verification', {}).get('enabled') else 'выключена'}",
        "",
        f"Кандидатов image+host: {summary.get('candidate_groups', 0)}",
        f"Уникальных изображений: {summary.get('candidate_images', 0)}",
        f"Source-связей внутри кандидатов: {summary.get('candidate_sources', 0)}",
        f"Максимум source одного хоста у файла: {summary.get('max_sources_per_host', 0)}",
        f"Source с живыми image_source_tags сейчас: {summary.get('sources_with_current_tag_rows', 0)}",
        f"Source без живых image_source_tags сейчас: {summary.get('sources_without_current_tag_rows', 0)}",
        f"Групп с несколькими текущими tag-owner: {summary.get('groups_with_multiple_current_tag_owners', 0)}",
        f"Текущих tag-owner с relay/side-queue acquisition: {summary.get('relay_marked_current_owner_sources', 0)}",
        "",
        "Распределение групп:",
    ]
    for key in ("2", "3-5", "6-10", "11-25", "26+"):
        lines.append(f"  {key}: {int(distributions.get(key, 0) or 0)}")
    lines += ["", "Классификация source:"]
    for key in CLASSIFICATIONS:
        lines.append(f"  {key}: {int(classes.get(key, 0) or 0)}")
    lines += ["", "По хостам:"]
    for row in list(report.get("hosts") or []):
        lines.append(
            f"  {row.get('host', '')}: groups={row.get('groups', 0)} "
            f"images={row.get('images', 0)} sources={row.get('sources', 0)} "
            f"26+={row.get('distribution', {}).get('26+', 0)}"
        )
    if report.get("cancelled"):
        lines += ["", "Проверка была отменена; отчёт частичный."]
    errors = list(report.get("errors") or [])
    if errors:
        lines += ["", f"Ошибок отчёта: {len(errors)}"]
        lines.extend(f"  {item}" for item in errors[:20])
    lines += [
        "",
        "Никакие source, теги, alias, raw_metadata, файлы или очереди не изменялись.",
    ]
    return "\n".join(lines)


def audit_source_contamination(
    settings: dict,
    *,
    verify_network: bool = False,
    max_network_sources: int = 0,
    progress: Callable[[str], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
    resolver: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Inventory and optionally verify multi-source-per-host groups.

    ``max_network_sources=0`` means no limit.  Candidate discovery and all DB
    evidence are always complete; only remote post verification can be bounded.
    """
    progress = progress or (lambda _message: None)
    stop_check = stop_check or (lambda: False)
    max_network_sources = max(0, int(max_network_sources or 0))
    database_file = db_path(settings)
    report: dict[str, Any] = {
        "version": 446,
        "created_at": _now_text(),
        "read_only": True,
        "candidate_selection": "live images grouped by normalized sources.host; COUNT(DISTINCT source_id) > 1",
        "limitations": [
            "image_source_tags keeps only the newest confirmed tag set per image+host",
            "acquisition is evidence for the current surviving tag owner, not complete historical provenance",
            "DB-only multiplicity is a candidate signal, not proof of contamination",
            "no repair or tag rebuild is performed by v446",
        ],
        "database": {
            "path": str(database_file),
            "exists": database_file.exists(),
            "size_bytes": int(database_file.stat().st_size) if database_file.exists() else 0,
            "schema_version": 0,
        },
        "network_verification": {
            "enabled": bool(verify_network),
            "max_sources": max_network_sources,
            "attempted": 0,
            "cache_hits": 0,
            "skipped_by_limit": 0,
        },
        "summary": {},
        "distribution": {key: 0 for key in ("2", "3-5", "6-10", "11-25", "26+")},
        "classifications": {key: 0 for key in CLASSIFICATIONS},
        "hosts": [],
        "groups": [],
        "errors": [],
        "cancelled": False,
    }
    progress("Source audit: поиск image+host групп с несколькими URL…")
    resolved_cache: dict[tuple[str, str], dict[str, Any]] = {}
    default_resolver = None
    network_attempted = 0
    host_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "groups": 0,
        "images": set(),
        "sources": 0,
        "distribution": Counter(),
        "current_tag_owner_sources": 0,
        "relay_marked_current_owners": 0,
        "classifications": Counter(),
    })

    with db(settings, readonly=True) as con:
        try:
            row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            report["database"]["schema_version"] = int(row[0]) if row else 0
        except Exception:
            report["database"]["schema_version"] = 0
        alias_available = _table_exists(con, "image_site_md5_aliases")
        raw_available = _table_exists(con, "raw_metadata")
        tag_table_available = _table_exists(con, "image_source_tags")
        rows = con.execute(
            """
            SELECT i.id AS image_id, i.path, i.file_name,
                   lower(COALESCE(i.hash_md5,'')) AS canonical_md5,
                   lower(replace(COALESCE(s.host,''),'www.','')) AS host,
                   COUNT(DISTINCT s.id) AS source_count
            FROM images i
            JOIN image_sources isrc ON isrc.image_id=i.id
            JOIN sources s ON s.id=isrc.source_id
            WHERE i.deleted=0
            GROUP BY i.id, lower(replace(COALESCE(s.host,''),'www.',''))
            HAVING COUNT(DISTINCT s.id)>1
            ORDER BY source_count DESC, i.id, host
            """
        ).fetchall()
        groups_rows = [dict(row) for row in rows]
        report["summary"] = {
            "candidate_groups": len(groups_rows),
            "candidate_images": len({int(row.get("image_id") or 0) for row in groups_rows}),
            "candidate_sources": sum(int(row.get("source_count") or 0) for row in groups_rows),
            "max_sources_per_host": max([int(row.get("source_count") or 0) for row in groups_rows] or [0]),
            "sources_with_current_tag_rows": 0,
            "sources_without_current_tag_rows": 0,
            "groups_with_multiple_current_tag_owners": 0,
            "relay_marked_current_owner_sources": 0,
        }

        for index, group_row in enumerate(groups_rows, start=1):
            if stop_check():
                report["cancelled"] = True
                break
            image_id = int(group_row.get("image_id") or 0)
            host = _host(group_row.get("host"))
            source_count = int(group_row.get("source_count") or 0)
            progress(f"Source audit: {index}/{len(groups_rows)} — image={image_id} host={host} sources={source_count}")
            report["distribution"][_bucket(source_count)] += 1
            stat = host_stats[host]
            stat["groups"] += 1
            stat["images"].add(image_id)
            stat["sources"] += source_count
            stat["distribution"][_bucket(source_count)] += 1

            if tag_table_available:
                source_rows = con.execute(
                    """
                    SELECT s.id AS source_id, s.url,
                           COUNT(ist.tag_id) AS current_tag_rows,
                           GROUP_CONCAT(DISTINCT COALESCE(ist.acquisition,'')) AS acquisitions,
                           MAX(COALESCE(ist.updated_at,0)) AS tag_updated_at
                    FROM image_sources isrc
                    JOIN sources s ON s.id=isrc.source_id
                    LEFT JOIN image_source_tags ist
                      ON ist.image_id=isrc.image_id AND ist.source_id=isrc.source_id
                    WHERE isrc.image_id=?
                      AND lower(replace(COALESCE(s.host,''),'www.',''))=?
                    GROUP BY s.id, s.url
                    ORDER BY s.id
                    """,
                    (image_id, host),
                ).fetchall()
            else:
                source_rows = con.execute(
                    """
                    SELECT s.id AS source_id, s.url, 0 AS current_tag_rows,
                           '' AS acquisitions, 0 AS tag_updated_at
                    FROM image_sources isrc JOIN sources s ON s.id=isrc.source_id
                    WHERE isrc.image_id=?
                      AND lower(replace(COALESCE(s.host,''),'www.',''))=?
                    ORDER BY s.id
                    """,
                    (image_id, host),
                ).fetchall()

            current_owner_ids = [int(row["source_id"]) for row in source_rows if int(row["current_tag_rows"] or 0) > 0]
            current_tag_rows = sum(int(row["current_tag_rows"] or 0) for row in source_rows)
            report["summary"]["sources_with_current_tag_rows"] += len(current_owner_ids)
            report["summary"]["sources_without_current_tag_rows"] += max(0, len(source_rows) - len(current_owner_ids))
            if len(current_owner_ids) > 1:
                report["summary"]["groups_with_multiple_current_tag_owners"] += 1
            stat["current_tag_owner_sources"] += len(current_owner_ids)
            raw_metadata = {}
            if raw_available:
                raw_row = con.execute(
                    "SELECT site,post_url,file_url,updated_at FROM raw_metadata WHERE image_id=?",
                    (image_id,),
                ).fetchone()
                raw_metadata = dict(raw_row) if raw_row else {}
            aliases_for_image_host: list[dict[str, Any]] = []
            if alias_available:
                aliases_for_image_host = [dict(row) for row in con.execute(
                    """
                    SELECT site_md5,image_id,site,source_url,local_md5,observed_at
                    FROM image_site_md5_aliases
                    WHERE image_id=? AND lower(replace(COALESCE(site,''),'www.',''))=?
                    ORDER BY observed_at DESC, site_md5
                    """,
                    (image_id, host),
                ).fetchall()]

            group: dict[str, Any] = {
                "image_id": image_id,
                "path": str(group_row.get("path") or ""),
                "file_name": str(group_row.get("file_name") or ""),
                "canonical_md5": _md5(group_row.get("canonical_md5")),
                "host": host,
                "source_count": source_count,
                "distribution_bucket": _bucket(source_count),
                "current_host_tag_rows": current_tag_rows,
                "current_tag_owner_source_ids": current_owner_ids,
                "current_tag_owner_count": len(current_owner_ids),
                "raw_metadata": raw_metadata,
                "aliases_for_same_image_host": aliases_for_image_host,
                "sources": [],
                "classification_counts": {key: 0 for key in CLASSIFICATIONS},
            }

            for source_row_raw in source_rows:
                source_row = dict(source_row_raw)
                source_id = int(source_row.get("source_id") or 0)
                url = str(source_row.get("url") or "")
                acquisitions = [item.strip() for item in str(source_row.get("acquisitions") or "").split(",") if item.strip()]
                relay_marked = any(marker in " ".join(acquisitions).lower() for marker in _RELAY_METHOD_MARKERS)
                if relay_marked and int(source_row.get("current_tag_rows") or 0) > 0:
                    stat["relay_marked_current_owners"] += 1
                    report["summary"]["relay_marked_current_owner_sources"] += 1
                source: dict[str, Any] = {
                    "source_id": source_id,
                    "url": url,
                    "post_id": _post_id(url),
                    "current_tag_rows": int(source_row.get("current_tag_rows") or 0),
                    "is_current_tag_owner": int(source_row.get("current_tag_rows") or 0) > 0,
                    "current_acquisitions": acquisitions,
                    "current_acquisition_is_relay_like": relay_marked,
                    "tag_updated_at": int(source_row.get("tag_updated_at") or 0),
                }
                cache_key = (host, url)
                if verify_network:
                    if cache_key in resolved_cache:
                        remote = dict(resolved_cache[cache_key])
                        report["network_verification"]["cache_hits"] += 1
                    elif max_network_sources and network_attempted >= max_network_sources:
                        remote = {
                            "host": host,
                            "source_url": url,
                            "post_id": _post_id(url),
                            "status": "network_unverified",
                            "site_md5": "",
                            "http_status": 0,
                            "reason": "network_limit_reached",
                        }
                        report["network_verification"]["skipped_by_limit"] += 1
                    else:
                        if resolver is None:
                            if default_resolver is None:
                                default_resolver = RemotePostResolver(settings, lambda message: progress(f"Source audit network: {message}"),
                                                                      stop_check=stop_check)
                            active_resolver = default_resolver
                        else:
                            active_resolver = resolver
                        network_attempted += 1
                        report["network_verification"]["attempted"] = network_attempted
                        try:
                            remote = dict(active_resolver(host, url) or {})
                        except InterruptedError:
                            raise
                        except Exception as exc:
                            remote = {
                                "host": host,
                                "source_url": url,
                                "post_id": _post_id(url),
                                "status": "parse_error",
                                "site_md5": "",
                                "http_status": 0,
                                "reason": sanitize_text(f"resolver:{type(exc).__name__}: {exc}")[:300],
                            }
                        resolved_cache[cache_key] = dict(remote)
                else:
                    remote = {
                        "host": host,
                        "source_url": url,
                        "post_id": _post_id(url),
                        "status": "network_unverified",
                        "site_md5": "",
                        "http_status": 0,
                        "reason": "network_verification_disabled",
                    }

                remote.setdefault("host", host)
                remote.setdefault("source_url", url)
                remote.setdefault("post_id", _post_id(url))
                remote.setdefault("status", "network_unverified")
                remote.setdefault("site_md5", "")
                remote.setdefault("http_status", 0)
                remote.setdefault("reason", "")
                site_md5 = _md5(remote.get("site_md5"))
                alias_row = None
                other_canonical_images: list[dict[str, Any]] = []
                if site_md5:
                    if alias_available:
                        row = con.execute(
                            """
                            SELECT a.site_md5,a.image_id,a.site,a.source_url,a.local_md5,a.observed_at,
                                   i.path,i.deleted,i.lifecycle
                            FROM image_site_md5_aliases a
                            LEFT JOIN images i ON i.id=a.image_id
                            WHERE lower(a.site_md5)=?
                            LIMIT 1
                            """,
                            (site_md5,),
                        ).fetchone()
                        alias_row = dict(row) if row else None
                    other_canonical_images = [dict(row) for row in con.execute(
                        """
                        SELECT id AS image_id,path,hash_md5,lifecycle
                        FROM images
                        WHERE deleted=0 AND id<>? AND lower(COALESCE(hash_md5,''))=?
                        ORDER BY id LIMIT 10
                        """,
                        (image_id, site_md5),
                    ).fetchall()]
                classification, evidence = _classify_source(
                    image_id=image_id,
                    canonical_md5=group["canonical_md5"],
                    remote=remote,
                    alias_row=alias_row,
                    other_canonical_images=other_canonical_images,
                )
                if classification not in CLASSIFICATIONS:
                    classification = "parse_error"
                    evidence = {"reason": "unknown_classification"}
                source["remote"] = remote
                source["classification"] = classification
                source["evidence"] = evidence
                source["alias_for_remote_md5"] = alias_row or {}
                source["other_live_canonical_images"] = other_canonical_images
                group["sources"].append(source)
                group["classification_counts"][classification] += 1
                report["classifications"][classification] += 1
                stat["classifications"][classification] += 1

            group["verified_valid_sources"] = int(group["classification_counts"]["verified_local_md5"]) + int(group["classification_counts"]["verified_alias_same_image"])
            group["strong_mismatch_sources"] = int(group["classification_counts"]["alias_bound_to_other_image"]) + int(group["classification_counts"]["verified_mismatch"])
            group["unverified_sources"] = int(group["classification_counts"]["network_unverified"]) + int(group["classification_counts"]["post_missing"]) + int(group["classification_counts"]["parse_error"])
            report["groups"].append(group)

    report["hosts"] = []
    for host, stat in sorted(host_stats.items(), key=lambda item: (-int(item[1]["groups"]), item[0])):
        report["hosts"].append({
            "host": host,
            "groups": int(stat["groups"]),
            "images": len(stat["images"]),
            "sources": int(stat["sources"]),
            "distribution": {key: int(stat["distribution"].get(key, 0)) for key in ("2", "3-5", "6-10", "11-25", "26+")},
            "current_tag_owner_sources": int(stat["current_tag_owner_sources"]),
            "relay_marked_current_owners": int(stat["relay_marked_current_owners"]),
            "classifications": {key: int(stat["classifications"].get(key, 0)) for key in CLASSIFICATIONS},
        })
    report["summary_text"] = format_source_contamination_text(report)
    progress("Source audit: готово; база и файлы не изменялись.")
    return report


def save_source_contamination_json(report: dict[str, Any], destination: str) -> str:
    from pathlib import Path
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
