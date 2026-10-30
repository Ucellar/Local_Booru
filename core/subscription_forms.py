"""Subscriptions as pages make them: a subscription from a form (checked, numbers within their
limits), the sites a subscription can search by tag, and «Скачать тег» — a subscription for one run
that is not saved."""
from __future__ import annotations

import hashlib
import math

RUN_MODES = ("all", "new", "old")
DIRECTIONS = ("newest_to_oldest", "oldest_to_newest")
TAG_DOWNLOAD_PREFIX = "tag_"


def scannable_sites() -> list[str]:
    """Known sites a subscription can search by tag (rule34.us has no API to page through)."""
    from core.downloader_utils import _posts_api_url
    from core.settings import SITES_BY_ENGINE
    return sorted(domain for sites in SITES_BY_ENGINE.values() for domain in sites
                  if _posts_api_url(domain, "tag", 0)[0])


def _int_in(value, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(float(value))))
    except (TypeError, ValueError):
        return default


def clean_form(form: dict, *, known_sites: list[str] | None = None) -> tuple[dict, str]:
    """A subscription from a page form: (fields, "") or ({}, what is wrong).  Numbers are kept
    within their limits, a site not on the list is refused."""
    form = form if isinstance(form, dict) else {}
    query = " ".join(str(form.get("query") or "").split())
    if not query:
        return {}, "Укажите тег или запрос"
    allowed = set(known_sites if known_sites is not None else scannable_sites())
    sites, seen = [], set()
    for row in form.get("sites") or []:
        if not isinstance(row, dict):
            continue
        site = str(row.get("site") or "").strip().lower()
        if not site or site in seen:
            continue
        if site not in allowed:
            return {}, f"Сайт {site} не умеет искать по тегам"
        seen.add(site)
        entry = {"site": site, "priority": _int_in(row.get("priority"), 3, 1, 5)}
        override = " ".join(str(row.get("query_override") or "").split())
        if override:
            entry["query_override"] = override
        sites.append(entry)
    if not sites:
        return {}, "Выберите хотя бы один сайт"
    run_mode = str(form.get("run_mode") or "all")
    direction = str(form.get("run_direction") or "newest_to_oldest")
    return {
        "name": query, "query": query, "sites": sites,
        "check_interval_hours": _int_in(form.get("check_interval_hours"), 24, 1, 720),
        "max_pages": _int_in(form.get("max_pages"), 3, 1, 50),
        "run_mode": run_mode if run_mode in RUN_MODES else "all",
        "run_direction": direction if direction in DIRECTIONS else "newest_to_oldest",
        "enabled": bool(form.get("enabled", True)),
    }, ""


def tag_download(query: str, sites: list[dict], max_posts: int) -> dict:
    """«Скачать тег»: a subscription for one run that is not saved.  Its list of found posts lives
    under an id of the query and the sites, so a stopped download continues where it stopped."""
    query = " ".join(str(query or "").split())
    max_posts = max(1, int(max_posts or 1))
    key = hashlib.md5("\n".join([query] + sorted(str(s.get("site")) for s in sites)).encode("utf-8")).hexdigest()[:12]
    return {"id": TAG_DOWNLOAD_PREFIX + key, "name": f"Скачать тег: {query}", "query": query, "sites": sites,
            "max_posts": max_posts, "max_pages": max(1, math.ceil(max_posts / 100)), "run_mode": "all",
            "run_direction": "newest_to_oldest", "one_off": True}


GRAB_PREFIX = "grab_"


def grab_download(query: str, cards: list, site_order: list) -> dict:
    """The grabber's «Скачать»: a subscription for one run over the posts of the chosen cards (no
    scan).  Sites in ``site_order`` — the first one is downloaded from first; the tags of every
    site of a card are kept."""
    posts: dict = {}
    for card in cards:
        for item in card["entries"]:
            posts.setdefault(item["site"], []).append(item["raw"])
    order = [h for h in site_order if h in posts] + [h for h in posts if h not in site_order]
    sites = [{"site": host, "priority": max(1, 5 - i)} for i, host in enumerate(order)]
    key = hashlib.md5("\n".join(sorted(c["key"] for c in cards)).encode("utf-8")).hexdigest()[:12]
    count = len(cards)
    return {"id": GRAB_PREFIX + key, "name": f"Граббер: {query or 'выбранное'} ({count})",
            "query": " ".join(str(query or "grabber").split()), "sites": sites, "given_posts": posts,
            "max_pages": 1, "run_mode": "all", "run_direction": "newest_to_oldest", "one_off": True}

