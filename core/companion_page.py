"""What the extension's panel (0.8.0) asks the program about the site page it is on.

«Подписаться»: the search of a booru site's list page (``/posts?tags=…``, ``index.php?page=post&s=list&tags=…``,
``/post?tags=…``) becomes a subscription of the program — the same as one made on «Подписки» with nothing changed
(every 24 hours, 3 pages) — or this site joins the subscription that already asks the same search elsewhere.  Only
sites a subscription can page through (``subscription_forms.scannable_sites``); the page's address is all the
extension sends, the program reads the site and the search from it.

The blocklist: the program's (``grabber_subscriptions_blocklist``, else ``downloader_blocklist`` — as subscriptions
and the grabber read it), which the extension applies to the cards of the site and keeps for the time the program
is closed.  ``blocklist_rev`` is in the status of the extension's long poll: the settings file is read again only
when it changed, so a blocklist saved in the program reaches open tabs at once.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger(__name__)

MAX_QUERY = 300
EVERY_HOURS = 24          # as «Добавить подписку» on «Подписки» with nothing changed
MAX_PAGES = 3
_LIST_PATHS = {"/", "/posts", "/post", "/post/index", "/index.php"}


def page_search(url: str) -> tuple[str, str, str]:
    """(site, query, why not) of a site page: the query is empty when the page is not a search of a list of posts
    (a post's page, a pool, no tags — or ``tags=all``, Gelbooru's «everything»); «why not» says why a search
    cannot become a subscription."""
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return "", "", ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "", "", ""
    host = parts.hostname.lower()
    host = host[4:] if host.startswith("www.") else host
    path = parts.path.rstrip("/") or "/"
    args = parse_qs(parts.query)
    if path not in _LIST_PATHS:
        return host, "", ""
    if path == "/index.php" and ((args.get("page") or [""])[0] != "post" or (args.get("s") or ["list"])[0] != "list"):
        return host, "", ""                          # Gelbooru's post page keeps the search: …&s=view&id=…&tags=…
    query = " ".join(" ".join(args.get("tags") or []).split())
    if not query or query.lower() == "all":
        return host, "", ""
    if len(query) > MAX_QUERY:
        return host, query[:MAX_QUERY], "запрос длиннее 300 знаков — такую подписку сделайте на «Подписках»"
    from core.subscription_forms import scannable_sites
    if host not in scannable_sites():
        return host, query, f"на {host} подписки не умеют листать посты"
    return host, query, ""


def _brief(sub: dict) -> dict:
    from core.subscriptions import normalize_sites
    return {"id": str(sub.get("id") or ""), "name": str(sub.get("name") or sub.get("query") or ""),
            "enabled": bool(sub.get("enabled", True)), "sites": [str(e.get("site") or "") for e in normalize_sites(sub)],
            "every_hours": int(sub.get("check_interval_hours", EVERY_HOURS) or EVERY_HOURS)}


def subscription_state(url: str) -> dict:
    """What the panel shows for the page: no search — ``search: false``; a search the program cannot subscribe
    to — ``supported: false`` and why; otherwise whether a subscription asks it here (``subscribed``) or on other
    sites only (``other_sites``)."""
    site, query, why = page_search(url)
    if not query:
        return {"ok": True, "search": False}
    out = {"ok": True, "search": True, "site": site, "query": query, "supported": not why, "reason": why,
           "subscribed": False, "other_sites": False, "subscription": None}
    if why:
        return out
    from core.subscriptions import find_search, load_subscriptions
    sub, here = find_search(load_subscriptions(), site, query)
    if sub is not None:
        out.update(subscribed=here, other_sites=not here, subscription=_brief(sub))
    return out


def subscribe(url: str) -> dict:
    """«Подписаться»: the search of the page as a subscription — new, this site added to the subscription to the same
    search, or the one there is; the panel's new state and what to say."""
    site, query, why = page_search(url)
    if not query:
        return {"ok": False, "error": "not_a_search", "message": "Это не страница поиска по тегам"}
    if why:
        return {"ok": False, "error": "not_supported", "message": why}
    from core.download_policy import downloads_enabled
    from core.subscriptions import subscribe_to_search
    sub, done = subscribe_to_search(site, query, check_interval_hours=EVERY_HOURS, max_pages=MAX_PAGES)
    brief = _brief(sub)
    name = brief["name"]
    if done == "exists":
        message = f"Подписка «{name}» уже есть" + ("" if brief["enabled"] else
                                                    " — она выключена, включить можно на «Подписках»")
    else:
        message = (f"Подписка «{name}» на {site} добавлена" if done == "created" else
                   f"{site} добавлен к подписке «{name}»")
        message += (f": Local Booru проверит её сама при ближайшей автопроверке, дальше — раз в "
                    f"{brief['every_hours']} ч")
        if not downloads_enabled():
            message += "; скачивание выключено на этот сеанс — файлы скачаются после перезапуска"
        log.info("Подписка из расширения: %s — «%s» на %s", "новая" if done == "created" else "+ сайт", query, site)
    state = subscription_state(url)
    state.update(created=done == "created", added_site=done == "added_site", message=message)
    return state


# ── the blocklist ─────────────────────────────────────────────────────────────
_REV_LOCK = threading.Lock()
_REV: dict = {"key": None, "rev": ""}


def _rev_of(tags: list) -> str:
    return hashlib.sha1("\n".join(tags).encode("utf-8")).hexdigest()[:16]


def blocklist() -> dict:
    """The program's blocklist as it is now (the settings file, not the copy the API started with)."""
    from core.settings import load_settings
    from core.subscriptions import normalize_blacklist_tags
    s = load_settings()
    tags = normalize_blacklist_tags(s.get("grabber_subscriptions_blocklist") or s.get("downloader_blocklist") or [])
    return {"ok": True, "tags": tags, "rev": _rev_of(tags)}


def blocklist_rev() -> str:
    """The blocklist's revision for the long poll (asked 4 times a second): the settings file is read again only
    when its time, size or file changed."""
    import core.settings as settings_module
    try:
        st = os.stat(settings_module.SETTINGS_FILE)
        key = (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        key = None
    with _REV_LOCK:
        if key is not None and key == _REV["key"]:
            return _REV["rev"]
    rev = blocklist()["rev"]
    with _REV_LOCK:
        _REV.update(key=key, rev=rev)
    return rev
