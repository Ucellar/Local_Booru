"""Source-scoped tag aliases/relations imported from configured booru sites."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

from bs4 import BeautifulSoup

from core.database.connection import db
from core.tag_utils import canonical_tag_key, normalize_tag


ALIAS_SOURCES = {
    "rule34.xxx": {
        "page_url": "https://rule34.xxx/index.php?page=alias&s=list&pid=0",
        "kind": "gelbooru_html",
        "page_parameter": "pid",
        "page_step": 50,
    },
    "gelbooru.com": {
        "page_url": "https://gelbooru.com/index.php?page=alias&s=list",
        "kind": "gelbooru_html",
        "page_parameter": "pid",
        "page_step": 50,
    },
    "danbooru.donmai.us": {
        "page_url": "https://danbooru.donmai.us/tag_aliases",
        "json_url": "https://danbooru.donmai.us/tag_aliases.json",
        "kind": "rails_json",
    },
    "e621.net": {
        "page_url": "https://e621.net/tag_aliases",
        "json_url": "https://e621.net/tag_aliases.json",
        "kind": "rails_json",
    },
    "booru.allthefallen.moe": {
        "page_url": "https://booru.allthefallen.moe/tag_aliases",
        "json_url": "https://booru.allthefallen.moe/tag_aliases.json",
        "kind": "rails_json",
    },
}


@dataclass(frozen=True)
class TagRelation:
    source_site: str
    source_tag: str
    target_tag: str
    relation_type: str = "alias"
    source_record_id: str = ""
    reason: str = ""
    status: str = "active"


def canonical_site_for_scope(source_scope: str | None) -> str:
    scope = str(source_scope or "all").strip().lower()
    aliases = {
        "all": "rule34.xxx", "all_sources": "rule34.xxx", "все": "rule34.xxx",
        "r34": "rule34.xxx", "rule34": "rule34.xxx", "rule34.xxx": "rule34.xxx",
        "gel": "gelbooru.com", "gelbooru": "gelbooru.com", "gelbooru.com": "gelbooru.com",
        "dan": "danbooru.donmai.us", "danbooru": "danbooru.donmai.us", "danbooru.donmai.us": "danbooru.donmai.us",
        "e621": "e621.net", "e621.net": "e621.net",
        "atf": "booru.allthefallen.moe", "booru.allthefallen.moe": "booru.allthefallen.moe",
    }
    return aliases.get(scope, scope)


def _clean_pair(source: Any, target: Any) -> tuple[str, str]:
    src = canonical_tag_key(source)
    dst = canonical_tag_key(target)
    if not src or not dst or src == dst:
        return "", ""
    return src, dst


def parse_rails_alias_json(source_site: str, payload: Any) -> list[TagRelation]:
    rows = payload.get("tag_aliases") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    out: list[TagRelation] = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "active").lower()
        # Pending/deleted proposals are not active synonyms.
        if status not in {"active", "approved"}:
            continue
        src, dst = _clean_pair(
            row.get("antecedent_name") or row.get("antecedent") or row.get("name"),
            row.get("consequent_name") or row.get("consequent") or row.get("alias_to"),
        )
        if not src or (src, dst) in seen:
            continue
        seen.add((src, dst))
        out.append(TagRelation(
            source_site=str(source_site), source_tag=src, target_tag=dst,
            source_record_id=str(row.get("id") or ""), reason=str(row.get("reason") or ""), status="active",
        ))
    return out


def parse_gelbooru_alias_html(source_site: str, html: str) -> list[TagRelation]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[TagRelation] = []
    seen = set()
    # Both Gelbooru and rule34 use row-like containers with the antecedent and
    # consequent as the first two tag links. Inputs/navigation links are ignored.
    containers = list(soup.select("tr"))
    if not containers:
        containers = list(soup.select("li, p, div"))
    for container in containers:
        anchors = []
        for anchor in container.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            text = anchor.get_text(" ", strip=True)
            if not text:
                continue
            if "tags=" in href or "page=post" in href or "/posts?tags=" in href:
                anchors.append(text)
        if len(anchors) < 2:
            continue
        src, dst = _clean_pair(anchors[0], anchors[1])
        if not src or (src, dst) in seen:
            continue
        seen.add((src, dst))
        reason = ""
        cells = container.find_all("td")
        if len(cells) >= 3:
            reason = cells[-1].get_text(" ", strip=True)
        out.append(TagRelation(str(source_site), src, dst, reason=reason))
    return out


def upsert_relations(
    settings: dict,
    relations: Iterable[TagRelation],
    *,
    source_url: str = "",
    sync_generation: int = 0,
) -> dict[str, int]:
    rows = list(relations)
    now = int(time.time())
    written = 0
    with db(settings, write=True) as con:
        for relation in rows:
            con.execute(
                """INSERT INTO tag_relations(
                       source_site,relation_type,source_tag,target_tag,source_record_id,reason,
                       status,fetched_at,confidence,sync_generation,valid_to
                   ) VALUES(?,?,?,?,?,?,?, ?,1.0,?,0)
                   ON CONFLICT(source_site,relation_type,source_tag,target_tag) DO UPDATE SET
                     source_record_id=excluded.source_record_id, reason=excluded.reason,
                     status='active', fetched_at=excluded.fetched_at,
                     sync_generation=excluded.sync_generation, valid_to=0""",
                (
                    canonical_site_for_scope(relation.source_site), str(relation.relation_type or "alias"),
                    canonical_tag_key(relation.source_tag), canonical_tag_key(relation.target_tag),
                    str(relation.source_record_id or ""), str(relation.reason or ""),
                    str(relation.status or "active"), now, int(sync_generation or 0),
                ),
            )
            written += 1
        if rows:
            site = canonical_site_for_scope(rows[0].source_site)
            con.execute(
                """INSERT INTO tag_relation_sync_state(source_site,source_url,last_success_at,status,rows_seen,rows_written)
                   VALUES(?,?,?,'ok',?,?)
                   ON CONFLICT(source_site) DO UPDATE SET
                     source_url=CASE WHEN excluded.source_url!='' THEN excluded.source_url ELSE tag_relation_sync_state.source_url END,
                     last_success_at=excluded.last_success_at,status='ok',last_error='',
                     rows_seen=tag_relation_sync_state.rows_seen+excluded.rows_seen,
                     rows_written=tag_relation_sync_state.rows_written+excluded.rows_written""",
                (site, str(source_url or ALIAS_SOURCES.get(site, {}).get("page_url") or ""), now, len(rows), written),
            )
    return {"seen": len(rows), "written": written}


def resolve_tag(con, tag: str, source_scope: str, *, max_depth: int = 16) -> str:
    site = canonical_site_for_scope(source_scope)
    current = canonical_tag_key(tag)
    if not current:
        return ""
    seen = {current}
    for _ in range(max(1, int(max_depth))):
        row = con.execute(
            """SELECT target_tag FROM tag_relations
               WHERE source_site=? AND relation_type='alias' AND source_tag=? AND status='active'
               ORDER BY fetched_at DESC,relation_id DESC LIMIT 1""",
            (site, current),
        ).fetchone()
        if not row:
            break
        target = canonical_tag_key(row[0])
        if not target or target in seen:
            break
        seen.add(target)
        current = target
    return current


def page_url_for(site: str, page: int) -> str:
    site = canonical_site_for_scope(site)
    cfg = ALIAS_SOURCES[site]
    if cfg.get("kind") == "rails_json":
        return f"{cfg['json_url']}?" + urlencode({"search[status]": "active", "limit": 1000, "page": max(1, int(page))})
    parsed = urlparse(str(cfg["page_url"]))
    query = parse_qs(parsed.query)
    param = str(cfg.get("page_parameter") or "pid")
    query[param] = [str(max(0, int(page)) * int(cfg.get("page_step") or 50))]
    flat = [(key, value) for key, values in query.items() for value in values]
    return urlunparse(parsed._replace(query=urlencode(flat)))


def sync_alias_page(
    settings: dict,
    site: str,
    page: int = 0,
    *,
    log=None,
    sync_generation: int = 0,
) -> dict[str, Any]:
    """Fetch one official alias page. Network work is explicit and test-free."""
    log = log or (lambda _msg: None)
    site = canonical_site_for_scope(site)
    if site not in ALIAS_SOURCES:
        raise KeyError(f"unsupported alias source: {site}")
    url = page_url_for(site, page)
    try:
        from core.tagger import get_session
        from core.net import http_request
        session = get_session(settings=settings, log_func=log, target_host=site)
        response = http_request(session, "GET", url, timeout=30)
        if int(getattr(response, "status_code", 0) or 0) >= 400:
            raise RuntimeError(f"alias source status {response.status_code}")
        if ALIAS_SOURCES[site].get("kind") == "rails_json":
            relations = parse_rails_alias_json(site, response.json())
        else:
            relations = parse_gelbooru_alias_html(site, str(getattr(response, "text", "") or ""))
        result = upsert_relations(
            settings, relations, source_url=url, sync_generation=int(sync_generation or 0)
        )
        result.update({"site": site, "page": int(page), "url": url})
        return result
    except Exception as exc:
        try:
            with db(settings, write=True) as con:
                con.execute(
                    """INSERT INTO tag_relation_sync_state(source_site,source_url,status,last_error,retry_after)
                       VALUES(?,?,'error',?,?)
                       ON CONFLICT(source_site) DO UPDATE SET source_url=excluded.source_url,status='error',last_error=excluded.last_error,retry_after=excluded.retry_after""",
                    (site, url, f"{type(exc).__name__}: {exc}"[:4000], int(time.time()) + 300),
                )
        except Exception:
            pass
        raise


def finalize_source_sync(settings: dict, site: str, sync_generation: int) -> int:
    """Deactivate aliases absent from a fully completed source snapshot.

    Partial/error runs never call this function, so an interrupted sync cannot
    accidentally invalidate still-valid relations.
    """
    site = canonical_site_for_scope(site)
    generation = int(sync_generation or 0)
    if generation <= 0:
        return 0
    now = int(time.time())
    with db(settings, write=True) as con:
        cur = con.execute(
            """UPDATE tag_relations
               SET status='inactive', valid_to=?
               WHERE source_site=? AND relation_type='alias' AND status='active'
                 AND sync_generation>0 AND sync_generation!=?""",
            (now, site, generation),
        )
        return int(cur.rowcount or 0)


def sync_all_alias_sources(settings: dict, *, progress=None, stop_check=None, should_stop=None, max_pages_per_site: int | None = None) -> dict[str, Any]:
    """Resume and sync official alias lists for every configured source.

    Cursor state is committed after each page, so interruption/restart does not
    restart a large alias list from page one. A zero-row page marks that source
    complete. This is a manual maintenance operation; normal parsing never
    blocks on it.
    """
    import time as _time

    progress = progress or (lambda _msg: None)
    # TaskManager injects stop_check. Keep should_stop as a compatibility alias
    # for direct callers and older tests.
    cancel = stop_check or should_stop or (lambda: False)
    if max_pages_per_site is None:
        max_pages_per_site = int((settings or {}).get("tag_alias_sync_max_pages_per_run", 0) or 0)
    delay = max(0.0, min(30.0, float((settings or {}).get("tag_alias_sync_page_delay_seconds", 1.10) or 1.10)))
    result: dict[str, Any] = {"sites": {}, "seen": 0, "written": 0, "errors": 0, "stopped": False}
    for site, cfg in ALIAS_SOURCES.items():
        if cancel():
            result["stopped"] = True
            break
        with db(settings, readonly=True) as con:
            row = con.execute(
                "SELECT last_page,cursor,status,generation FROM tag_relation_sync_state WHERE source_site=?",
                (site,),
            ).fetchone()
        last_page = int(row["last_page"] or 0) if row else 0
        prior_status = str(row["status"] or "idle") if row else "idle"
        prior_generation = int(row["generation"] or 0) if row else 0
        # A completed source is resumable on explicit maintenance: start from
        # page zero/one again so changed aliases are refreshed, while UPSERT
        # keeps the operation idempotent.
        start_page = 1 if cfg.get("kind") == "rails_json" else 0
        # Resume only after a page that was actually committed. An error on
        # the very first HTML page leaves cursor empty; blindly using
        # last_page+1 there would skip pid=0 forever.
        committed_cursor = str(row["cursor"] or "") if row else ""
        resuming = prior_status in {"running", "error", "partial"} and prior_generation > 0
        generation = prior_generation if resuming else prior_generation + 1
        if resuming and committed_cursor:
            start_page = max(start_page, last_page + 1)
        site_result = {"pages": 0, "seen": 0, "written": 0, "status": "running", "error": ""}
        result["sites"][site] = site_result
        page = start_page
        try:
            with db(settings, write=True) as con:
                if resuming:
                    con.execute(
                        "UPDATE tag_relation_sync_state SET status='running',last_error='',generation=? WHERE source_site=?",
                        (generation, site),
                    )
                else:
                    con.execute(
                        """UPDATE tag_relation_sync_state
                           SET status='running',last_error='',generation=?,cursor='',last_page=0
                           WHERE source_site=?""",
                        (generation, site),
                    )
            while True:
                if cancel():
                    site_result["status"] = "partial"
                    result["stopped"] = True
                    break
                if max_pages_per_site and site_result["pages"] >= int(max_pages_per_site):
                    site_result["status"] = "partial"
                    break
                progress(f"Синонимы {site}: страница {page}")
                page_result = sync_alias_page(
                    settings, site, page, log=progress, sync_generation=generation
                )
                seen = int(page_result.get("seen", 0) or 0)
                written = int(page_result.get("written", 0) or 0)
                site_result["pages"] += 1
                site_result["seen"] += seen
                site_result["written"] += written
                result["seen"] += seen
                result["written"] += written
                with db(settings, write=True) as con:
                    con.execute(
                        "UPDATE tag_relation_sync_state SET last_page=?,cursor=?,status=?,last_error='' WHERE source_site=?",
                        (int(page), str(page), "complete" if seen == 0 else "running", site),
                    )
                if seen == 0:
                    deactivated = finalize_source_sync(settings, site, generation)
                    site_result["deactivated"] = deactivated
                    site_result["status"] = "complete"
                    break
                page += 1
                if delay:
                    deadline = _time.monotonic() + delay
                    while _time.monotonic() < deadline:
                        if cancel():
                            break
                        _time.sleep(min(0.1, max(0.0, deadline - _time.monotonic())))
            if site_result["status"] == "running":
                site_result["status"] = "partial"
            with db(settings, write=True) as con:
                con.execute("UPDATE tag_relation_sync_state SET status=? WHERE source_site=?", (site_result["status"], site))
        except Exception as exc:
            site_result["status"] = "error"
            site_result["error"] = f"{type(exc).__name__}: {exc}"
            result["errors"] += 1
            progress(f"Синонимы {site}: ERROR {site_result['error']}")
        if result["stopped"]:
            break
    return result


__all__ = [
    "ALIAS_SOURCES", "TagRelation", "canonical_site_for_scope", "parse_rails_alias_json",
    "parse_gelbooru_alias_html", "upsert_relations", "resolve_tag", "page_url_for",
    "sync_alias_page", "finalize_source_sync", "sync_all_alias_sources",
]
