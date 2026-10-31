"""The parser's site table as one ordered list of rows, read and written the same way by the web
page «Сайты» and (until v5.40) the old window's Qt table (ui/sites_widget.py).

Settings keep it in four keys: ``sites`` (built-in sites by their current domain, including
switched-off ones), ``custom_sites`` (the user's own, each with ``site_id = "custom:<hex>"``),
``site_manual_order`` (row ids: ``builtin:<original domain>`` / ``custom:<hex>``) and
``deleted_builtin_sites`` (original domains of built-in sites the user removed).  A row is the
site's settings plus ``_id`` and ``_custom``.
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from urllib.parse import urlparse

ENGINE_TYPES = {"Danbooru": "danbooru", "Gelbooru": "gelbooru", "Moebooru": "moebooru", "e621": "e621", "Свой": "custom"}
ROW_FIELDS = ("enabled", "name", "domain", "base_url", "login_url", "engine_label", "login", "api_key", "user_id",
              "description")


def clean_domain(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        raw = urlparse(raw).netloc
    return raw.lower().replace("www.", "").strip("/")


def engine_label(cfg: dict, fallback: str = "Свой") -> str:
    shown = str(cfg.get("engine_label") or "").strip()
    if shown in ENGINE_TYPES:
        return shown
    raw = str(cfg.get("engine") or cfg.get("type") or fallback).strip().lower()
    if raw in ("danbooru", "danbooru2", "danbooru_html"):
        return "Danbooru"
    if raw in ("gelbooru", "gelbooru_html", "rule34xxx", "rule34.xxx", "dapi", "hypnohub"):
        return "Gelbooru"
    if raw in ("moebooru", "rule34us", "rule34.us"):
        return "Moebooru"
    if raw in ("e621", "e926"):
        return "e621"
    return fallback if fallback in ENGINE_TYPES else "Свой"


def load(settings: dict) -> tuple[dict, list, set]:
    """(rows by id, order of ids, removed built-in domains) from the settings."""
    from core.settings import SITES_BY_ENGINE
    entries: dict = {}
    deleted = {str(x) for x in settings.get("deleted_builtin_sites", []) or [] if str(x)}
    saved_sites = settings.get("sites", {}) if isinstance(settings.get("sites"), dict) else {}
    saved_custom = settings.get("custom_sites", []) if isinstance(settings.get("custom_sites"), list) else []
    used: set = set()
    for engine, sites in SITES_BY_ENGINE.items():
        for builtin_domain, defaults in sites.items():
            if builtin_domain in deleted:
                continue
            key = next((k for k, v in saved_sites.items()
                        if k == builtin_domain or (isinstance(v, dict) and v.get("builtin_id") == builtin_domain)), None)
            saved = saved_sites.get(key, {}) if key else {}
            if key:
                used.add(key)
            cfg = {**defaults, **(saved if isinstance(saved, dict) else {})}
            cfg.setdefault("domain", clean_domain(cfg.get("base_url") or builtin_domain) or builtin_domain)
            cfg.setdefault("base_url", cfg.get("login_url") or f"https://{cfg['domain']}")
            cfg.setdefault("description", cfg.get("notes", ""))
            cfg.setdefault("engine_label", engine)
            cfg.update({"builtin_id": builtin_domain, "_custom": False, "_id": f"builtin:{builtin_domain}"})
            entries[cfg["_id"]] = cfg
    # Unrecognised flat configs are kept as the user's own sites instead of being lost.
    for domain, value in saved_sites.items():
        if domain in used or not isinstance(value, dict):
            continue
        if domain in deleted or str(value.get("builtin_id") or "") in deleted:
            continue
        cfg = deepcopy(value)
        cfg.setdefault("domain", domain)
        _custom_defaults(cfg)
        entries[cfg["_id"]] = cfg
    for value in saved_custom:
        if isinstance(value, dict):
            cfg = deepcopy(value)
            cfg.setdefault("domain", clean_domain(cfg.get("base_url") or ""))
            _custom_defaults(cfg)
            entries[cfg["_id"]] = cfg
    saved_order = [str(x) for x in settings.get("site_manual_order", []) or [] if str(x) in entries]
    order = saved_order + [k for k in entries if k not in saved_order]
    return entries, order, deleted


def _custom_defaults(cfg: dict) -> None:
    cfg.setdefault("base_url", cfg.get("login_url") or (f"https://{cfg['domain']}" if cfg.get("domain") else ""))
    cfg.setdefault("description", cfg.get("notes", ""))
    cfg["_custom"] = True
    cfg["_id"] = str(cfg.get("site_id") or f"custom:{uuid.uuid4().hex}")
    cfg["site_id"] = cfg["_id"]


def collect(entries: dict, order: list) -> tuple[dict, list]:
    """(``sites``, ``custom_sites``) in table order, from rows."""
    sites: dict = {}
    custom: list = []
    for entry_id in order:
        cfg = deepcopy(entries.get(entry_id, {}))
        if not cfg or not cfg.get("domain"):
            continue
        cfg.pop("_id", None)
        is_custom = bool(cfg.pop("_custom", False))
        domain = clean_domain(cfg.get("domain"))
        cfg["domain"] = domain
        cfg.setdefault("base_url", cfg.get("login_url") or f"https://{domain}")
        if is_custom:
            cfg.setdefault("site_id", entry_id)
            custom.append(cfg)
        else:
            sites[domain] = cfg
    return sites, custom


def changes(settings: dict, entries: dict, order: list, deleted: set) -> dict:
    """The settings keys of the table, keeping fields the table does not show (API endpoints and the
    like) from the stored configs, as the old parser page did."""
    sites, custom = collect(entries, order)
    old_sites = settings.get("sites") if isinstance(settings.get("sites"), dict) else {}
    merged_sites = {d: {**(old_sites.get(d) if isinstance(old_sites.get(d), dict) else {}), **cfg} for d, cfg in sites.items()}
    old_custom = {}
    for item in settings.get("custom_sites") or []:
        if isinstance(item, dict):
            key = (item.get("site_id") or item.get("domain") or item.get("base_url") or "").strip()
            if key:
                old_custom[key] = item
    merged_custom = [{**old_custom.get(cfg.get("site_id") or cfg.get("domain") or "", {}), **cfg} for cfg in custom]
    return {"sites": merged_sites, "custom_sites": merged_custom, "deleted_builtin_sites": sorted(deleted),
            "site_manual_order": [k for k in order if k in entries]}


def apply_edit(cfg: dict, form: dict) -> dict:
    """A row after the edit form (``form`` — the page's fields); the engine type is rewritten only
    when the engine family changes (rule34.xxx / rule34.us keep their special paths)."""
    out = deepcopy(cfg)
    old_label = engine_label(out, "Свой")
    label = str(form.get("engine_label") or old_label)
    if label not in ENGINE_TYPES:
        label = old_label
    domain = clean_domain(form.get("domain") or form.get("base_url") or out.get("domain") or "")
    base_url = str(form.get("base_url") or "").strip() or f"https://{domain}"
    out.update({
        "enabled": bool(form.get("enabled", out.get("enabled", True))),
        "name": str(form.get("name") or "").strip() or domain,
        "domain": domain, "base_url": base_url,
        "login_url": str(form.get("login_url") or "").strip() or base_url,
        "engine_label": label, "engine": ENGINE_TYPES[label],
        "login": str(form.get("login") or "").strip(),
        "user_id": str(form.get("user_id") or "").strip(),
        "description": str(form.get("description") or "").strip(),
        "notes": str(form.get("description") or "").strip(),
    })
    if "api_key" in form:                       # an empty field from the page = «no change» unless cleared on purpose
        key = str(form.get("api_key") or "").strip()
        if key or form.get("api_key_clear"):
            out["api_key"] = key
    if old_label != label or not out.get("type"):
        out["type"] = ENGINE_TYPES[label]
    return out


def new_custom(form: dict) -> dict:
    entry_id = f"custom:{uuid.uuid4().hex}"
    cfg = {"enabled": True, "engine_label": "Свой", "engine": "custom", "type": "custom", "api_key": ""}
    cfg = apply_edit(cfg, form)
    cfg.update({"_custom": True, "_id": entry_id, "site_id": entry_id})
    return cfg


def restore_builtins(entries: dict, order: list, deleted: set) -> int:
    """Bring removed built-in sites back (as their templates); returns how many came back."""
    from core.settings import SITES_BY_ENGINE
    back = 0
    for engine, sites in SITES_BY_ENGINE.items():
        for builtin_domain, defaults in sites.items():
            if builtin_domain not in deleted:
                continue
            entry_id = f"builtin:{builtin_domain}"
            if entry_id not in entries:
                cfg = deepcopy(defaults)
                cfg.update({"domain": builtin_domain, "base_url": cfg.get("login_url") or f"https://{builtin_domain}",
                            "description": cfg.get("notes", ""), "engine_label": engine,
                            "builtin_id": builtin_domain, "_custom": False, "_id": entry_id})
                entries[entry_id] = cfg
                order.append(entry_id)
                back += 1
    deleted.clear()
    return back


def check(settings: dict, cfg: dict) -> dict:
    """Ask the site what the parser would ask (an exact-MD5 search for an MD5 nobody has), with
    the parser's own client, keys, cookies and User-Agent: {"ok", "message"}."""
    from core.parser.context import ParserContext
    from core.parser.http import HttpClient, HttpFailure
    from core.parser.sites import SiteUnavailable, detect_engine, engine_class

    class _OneTry(HttpClient):
        def request(self, method, url, **kwargs):
            kwargs["attempts"] = 1
            return super().request(method, url, **kwargs)

    domain = clean_domain(cfg.get("domain") or cfg.get("base_url") or "")
    if not domain:
        return {"ok": False, "message": "не указан домен"}
    run = dict(settings, request_timeout_seconds=15, http_rate_limit_enabled=False)
    lines: list = []
    http = _OneTry(run, log=lines.append, cancel_check=lambda: False, pause_wait=lambda: None)
    try:
        ctx = ParserContext(run, http, lines.append, lambda *_a: None)
        cls = engine_class(cfg, domain)
        note = ""
        if cls is None:
            cls = detect_engine(ctx, domain)
            if cls is None:
                return {"ok": False, "message": "движок сайта не распознан — выберите его в настройках сайта"}
            note = f" (движок определён: {cls.engine})"
        site = cls(domain, cfg, ctx)
        site.preflight()
        if site.disabled_reason:
            return {"ok": False, "message": site.disabled_reason}
        try:
            site.search_md5("0" * 32)
        except SiteUnavailable as exc:
            return {"ok": False, "message": site.disabled_reason or str(exc)}
        except HttpFailure as exc:
            return {"ok": False, "message": f"сеть: {exc.detail}"}
        if site.disabled_reason:
            return {"ok": False, "message": site.disabled_reason}
        return {"ok": True, "message": "API отвечает" + note}
    except Exception as exc:          # a check reports, it never takes the page down
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    finally:
        http.close_all()
