"""Declarative, versioned extraction profiles served to the Companion.

Profiles contain data-only selectors/attributes/regexes.  Remote JavaScript,
eval and executable expressions are intentionally unsupported.  Built-in
profiles are bundled with Local Booru; conservative generic profiles are also
generated for user-configured custom sites so Companion permissions and
content-script registration follow the application's site list.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from urllib.parse import urlsplit

from core.database.connection import db

SCHEMA_VERSION = 1
_GENERATED_FROM_CUSTOM_SITES = "custom_sites"
_ALLOWED_TOP_LEVEL = {
    "schema_version", "revision", "site_id", "origins", "post_url_patterns",
    "selectors", "family", "notes", "generated_from",
}
_ALLOWED_FAMILY_FIELDS = {"kind", "pool_links", "parent_links"}
_ALLOWED_NOTES_FIELDS = {"selector", "body_selector"}
_BUILTIN_SITE_IDS = {
    "rule34.xxx", "gelbooru.com", "danbooru.donmai.us", "e621.net",
    "booru.allthefallen.moe", "pixiv.net", "generic",
}


def _profile(site_id: str, origins: list[str], post_patterns: list[str], *, media: list[dict], tags: list[dict], post_id: list[dict], md5: list[dict], family: dict | None = None, notes: dict | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "site_id": site_id,
        "origins": origins,
        "post_url_patterns": post_patterns,
        "selectors": {"media": media, "tags": tags, "post_id": post_id, "md5": md5},
        "family": family or {},
        "notes": notes or {},
    }


BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    "rule34.xxx": _profile(
        "rule34.xxx", ["https://rule34.xxx/*", "https://*.rule34.xxx/*"], ["*page=post*s=view*id=*"],
        media=[{"selector": "[data-large-file-url]", "attribute": "data-large-file-url"}, {"selector": "#image", "attribute": "src"}, {"selector": "a[href*='//api-cdn.rule34.xxx/']", "attribute": "href"}],
        tags=[{"selector": "a.search-tag, #tag-sidebar a[href*='tags=']", "attribute": "textContent"}],
        post_id=[{"source": "url", "pattern": "[?&]id=(?<value>\\d+)"}],
        md5=[{"selector": "[data-md5]", "attribute": "data-md5"}, {"source": "html", "pattern": "\\b(?<value>[a-f0-9]{32})\\b"}],
        family={"pool_links": "a[href*='page=pool']", "parent_links": "a[href*='id=']"},
        notes={"selector": ".note-box, .note-box-inner", "body_selector": ".note-body, .note-box-text"},
    ),
    "gelbooru.com": _profile(
        "gelbooru.com", ["https://gelbooru.com/*", "https://*.gelbooru.com/*"], ["*page=post*s=view*id=*"],
        media=[{"selector": "[data-original-url]", "attribute": "data-original-url"}, {"selector": "#image", "attribute": "src"}],
        tags=[{"selector": "#tag-list a[href*='tags='], a.search-tag", "attribute": "textContent"}],
        post_id=[{"source": "url", "pattern": "[?&]id=(?<value>\\d+)"}],
        md5=[{"source": "html", "pattern": "\\b(?<value>[a-f0-9]{32})\\b"}],
        family={"pool_links": "a[href*='page=pool']"}, notes={"selector": ".note-box", "body_selector": ".note-body"},
    ),
    "danbooru.donmai.us": _profile(
        "danbooru.donmai.us", ["https://danbooru.donmai.us/*"], ["*/posts/*"],
        media=[{"selector": "[data-file-url]", "attribute": "data-file-url"}, {"selector": "#image", "attribute": "src"}],
        tags=[{"selector": "#tag-list a.search-tag, [data-tag-name]", "attribute": "data-tag-name"}],
        post_id=[{"source": "url", "pattern": "/posts/(?<value>\\d+)"}],
        md5=[{"selector": "[data-md5]", "attribute": "data-md5"}],
        family={"pool_links": "a[href*='/pools/']", "parent_links": "a[href*='/posts/']"}, notes={"selector": ".note-box", "body_selector": ".note-body"},
    ),
    "e621.net": _profile(
        "e621.net", ["https://e621.net/*", "https://e926.net/*"], ["*/posts/*"],
        media=[{"selector": "[data-file-url]", "attribute": "data-file-url"}, {"selector": "#image", "attribute": "src"}],
        tags=[{"selector": "#tag-list [data-tag-name], a.search-tag", "attribute": "data-tag-name"}],
        post_id=[{"source": "url", "pattern": "/posts/(?<value>\\d+)"}], md5=[{"selector": "[data-md5]", "attribute": "data-md5"}],
        family={"pool_links": "a[href*='/pools/']", "parent_links": "a[href*='/posts/']"}, notes={"selector": ".note-box", "body_selector": ".note-body"},
    ),
    "booru.allthefallen.moe": _profile(
        "booru.allthefallen.moe", ["https://booru.allthefallen.moe/*"], ["*/posts/*"],
        media=[{"selector": "[data-file-url]", "attribute": "data-file-url"}, {"selector": "#image", "attribute": "src"}],
        tags=[{"selector": "#tag-list [data-tag-name], a.search-tag", "attribute": "data-tag-name"}],
        post_id=[{"source": "url", "pattern": "/posts/(?<value>\\d+)"}], md5=[{"selector": "[data-md5]", "attribute": "data-md5"}],
        family={"pool_links": "a[href*='/pools/']", "parent_links": "a[href*='/posts/']"}, notes={"selector": ".note-box", "body_selector": ".note-body"},
    ),
    "pixiv.net": _profile(
        "pixiv.net", ["https://www.pixiv.net/*", "https://pixiv.net/*"], ["*/artworks/*"],
        media=[{"selector": "meta[property='og:image']", "attribute": "content"}, {"selector": "img[src*='pximg.net']", "attribute": "src"}],
        tags=[{"selector": "a[href*='/tags/']", "attribute": "textContent"}],
        post_id=[{"source": "url", "pattern": "/artworks/(?<value>\\d+)"}], md5=[],
        family={"kind": "pixiv_work"},
    ),
    "generic": _profile(
        "generic", ["http://*/*", "https://*/*"], [],
        media=[{"selector": "meta[property='og:image']", "attribute": "content"}, {"selector": "meta[name='twitter:image']", "attribute": "content"}, {"selector": "img", "attribute": "src"}],
        tags=[], post_id=[], md5=[],
    ),
}


def _clean_text_mapping(value: object, allowed: set[str], *, limit: int = 2000) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: str(value.get(key) or "")[:limit]
        for key in sorted(allowed)
        if value.get(key)
    }


def _validate(profile: dict[str, Any]) -> dict[str, Any]:
    cloned = json.loads(json.dumps(profile, ensure_ascii=False))
    raw = {key: cloned.get(key) for key in _ALLOWED_TOP_LEVEL if key in cloned}
    if int(raw.get("schema_version") or 0) != SCHEMA_VERSION:
        raise ValueError("unsupported profile schema")
    site_id = str(raw.get("site_id") or "").strip().lower()
    if not site_id or len(site_id) > 160:
        raise ValueError("invalid site_id")
    raw["site_id"] = site_id
    raw["schema_version"] = SCHEMA_VERSION
    raw["revision"] = max(0, int(raw.get("revision") or 0))
    raw["origins"] = [str(x)[:500] for x in raw.get("origins") or [] if str(x).startswith(("http://", "https://"))][:64]
    raw["post_url_patterns"] = [str(x)[:500] for x in raw.get("post_url_patterns") or []][:64]
    selectors = raw.get("selectors") if isinstance(raw.get("selectors"), dict) else {}
    clean: dict[str, list[dict[str, str]]] = {}
    for kind in ("media", "tags", "post_id", "md5"):
        values = []
        for item in selectors.get(kind) or []:
            if not isinstance(item, dict):
                continue
            entry = {k: str(item.get(k) or "")[:1000] for k in ("selector", "attribute", "source", "pattern") if item.get(k)}
            if entry:
                values.append(entry)
        clean[kind] = values[:64]
    raw["selectors"] = clean
    raw["family"] = _clean_text_mapping(raw.get("family"), _ALLOWED_FAMILY_FIELDS)
    raw["notes"] = _clean_text_mapping(raw.get("notes"), _ALLOWED_NOTES_FIELDS)
    generated_from = str(raw.get("generated_from") or "")[:64]
    if generated_from:
        raw["generated_from"] = generated_from
    else:
        raw.pop("generated_from", None)
    encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 256_000:
        raise ValueError("profile too large")
    return raw


def _host_from_site_config(value: dict[str, Any], fallback: str = "") -> tuple[str, str]:
    domain = str(value.get("domain") or value.get("host") or "").strip().lower()
    candidate = str(value.get("base_url") or value.get("login_url") or value.get("url") or domain or fallback).strip()
    parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
    host = (parsed.hostname or domain or fallback).strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    scheme = parsed.scheme.lower() if parsed.scheme.lower() in {"http", "https"} else "https"
    return host, scheme


def _iter_custom_site_configs(settings: dict | None):
    settings = settings or {}
    yielded: set[str] = set()
    custom = settings.get("custom_sites") if isinstance(settings.get("custom_sites"), list) else []
    for value in custom:
        if not isinstance(value, dict):
            continue
        host, scheme = _host_from_site_config(value)
        if not host or host in _BUILTIN_SITE_IDS or host in yielded:
            continue
        yielded.add(host)
        yield host, scheme, value

    sites = settings.get("sites") if isinstance(settings.get("sites"), dict) else {}
    for key, value in sites.items():
        if not isinstance(value, dict):
            continue
        host, scheme = _host_from_site_config(value, str(key))
        builtin_id = str(value.get("builtin_id") or "").strip().lower()
        if not host or host in _BUILTIN_SITE_IDS or builtin_id in _BUILTIN_SITE_IDS or host in yielded:
            continue
        yielded.add(host)
        yield host, scheme, value


def _custom_profile(host: str, scheme: str, value: dict[str, Any]) -> dict[str, Any]:
    # Conservative selectors cover the common Gelbooru/Danbooru-derived HTML
    # conventions without embedding site-specific executable code.
    include_subdomains = bool(value.get("companion_include_subdomains", False))
    origins = [f"{scheme}://{host}/*"]
    if include_subdomains:
        origins.append(f"{scheme}://*.{host}/*")
    profile = {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "site_id": host,
        "origins": origins,
        "post_url_patterns": ["*page=post*s=view*id=*", "*/posts/*", "*/post/show/*"],
        "selectors": {
            "media": [
                {"selector": "[data-file-url]", "attribute": "data-file-url"},
                {"selector": "[data-original-url]", "attribute": "data-original-url"},
                {"selector": "[data-large-file-url]", "attribute": "data-large-file-url"},
                {"selector": "#image", "attribute": "src"},
                {"selector": "meta[property='og:image']", "attribute": "content"},
            ],
            "tags": [
                {"selector": "[data-tag-name]", "attribute": "data-tag-name"},
                {"selector": "#tag-list a[href*='tags='], #tag-sidebar a[href*='tags='], a.search-tag", "attribute": "textContent"},
            ],
            "post_id": [
                {"source": "url", "pattern": "[?&]id=(?<value>\\d+)"},
                {"source": "url", "pattern": "/posts?/(?<value>\\d+)"},
                {"source": "url", "pattern": "/post/show/(?<value>\\d+)"},
            ],
            "md5": [
                {"selector": "[data-md5]", "attribute": "data-md5"},
                {"source": "html", "pattern": "\\b(?<value>[a-fA-F0-9]{32})\\b"},
            ],
        },
        "family": {
            "pool_links": "a[href*='page=pool'], a[href*='/pools/']",
            "parent_links": "a[href*='parent_id='], a[href*='/posts/'], a[href*='page=post'][href*='id=']",
        },
        "notes": {"selector": ".note-box, .note-box-inner", "body_selector": ".note-body, .note-box-text"},
        "generated_from": _GENERATED_FROM_CUSTOM_SITES,
    }
    seed = json.dumps(_validate(profile), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    profile["revision"] = max(1, int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF)
    return _validate(profile)


def ensure_builtin_profiles(settings: dict | None) -> None:
    now = int(time.time())
    with db(settings, write=True) as con:
        for site_id, profile in BUILTIN_PROFILES.items():
            clean = _validate(profile)
            raw = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            con.execute(
                """INSERT INTO browser_site_profiles(site_id,schema_version,revision,profile_json,profile_hash,enabled,builtin,updated_at,last_error)
                   VALUES(?,?,?,?,?,1,1,?,'')
                   ON CONFLICT(site_id) DO UPDATE SET
                     schema_version=excluded.schema_version,
                     revision=MAX(browser_site_profiles.revision,excluded.revision),
                     profile_json=CASE WHEN browser_site_profiles.builtin=1 THEN excluded.profile_json ELSE browser_site_profiles.profile_json END,
                     profile_hash=CASE WHEN browser_site_profiles.builtin=1 THEN excluded.profile_hash ELSE browser_site_profiles.profile_hash END,
                     enabled=CASE WHEN browser_site_profiles.builtin=1 THEN 1 ELSE browser_site_profiles.enabled END,
                     updated_at=CASE WHEN browser_site_profiles.builtin=1 THEN excluded.updated_at ELSE browser_site_profiles.updated_at END""",
                (site_id, SCHEMA_VERSION, int(clean["revision"]), raw, digest, now),
            )


def ensure_custom_profiles(settings: dict | None) -> None:
    now = int(time.time())
    active_hosts: set[str] = set()
    generated: list[tuple[str, dict[str, Any]]] = []
    for host, scheme, value in _iter_custom_site_configs(settings):
        if not bool(value.get("enabled", True)):
            continue
        active_hosts.add(host)
        generated.append((host, _custom_profile(host, scheme, value)))

    with db(settings, write=True) as con:
        # Disable only profiles that were generated by this synchronizer and
        # whose custom site was removed/disabled. Hand-authored non-builtin
        # profiles are never overwritten or disabled here.
        rows = con.execute(
            "SELECT site_id,profile_json FROM browser_site_profiles WHERE builtin=0"
        ).fetchall()
        for row in rows:
            try:
                existing = json.loads(str(row["profile_json"] or "{}"))
            except Exception:
                continue
            if existing.get("generated_from") == _GENERATED_FROM_CUSTOM_SITES and str(row["site_id"]) not in active_hosts:
                con.execute(
                    "UPDATE browser_site_profiles SET enabled=0,updated_at=? WHERE site_id=?",
                    (now, str(row["site_id"])),
                )

        for site_id, clean in generated:
            raw = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            existing = con.execute(
                "SELECT builtin,profile_json FROM browser_site_profiles WHERE site_id=?",
                (site_id,),
            ).fetchone()
            if existing is not None:
                if int(existing["builtin"] or 0) == 1:
                    continue
                try:
                    old_profile = json.loads(str(existing["profile_json"] or "{}"))
                except Exception:
                    old_profile = {}
                if old_profile.get("generated_from") != _GENERATED_FROM_CUSTOM_SITES:
                    # Respect a manually authored profile with the same site_id.
                    continue
            con.execute(
                """INSERT INTO browser_site_profiles(site_id,schema_version,revision,profile_json,profile_hash,enabled,builtin,updated_at,last_error)
                   VALUES(?,?,?,?,?,1,0,?,'')
                   ON CONFLICT(site_id) DO UPDATE SET
                     schema_version=excluded.schema_version,
                     revision=excluded.revision,
                     profile_json=excluded.profile_json,
                     profile_hash=excluded.profile_hash,
                     enabled=1,
                     updated_at=excluded.updated_at,
                     last_error=''""",
                (site_id, SCHEMA_VERSION, int(clean["revision"]), raw, digest, now),
            )


def ensure_profiles(settings: dict | None) -> None:
    ensure_builtin_profiles(settings)
    ensure_custom_profiles(settings)


def list_profiles(settings: dict | None) -> dict[str, Any]:
    ensure_profiles(settings)
    with db(settings, readonly=True) as con:
        rows = con.execute("SELECT site_id,schema_version,revision,profile_json,profile_hash,enabled,builtin,updated_at,last_error FROM browser_site_profiles WHERE enabled=1 ORDER BY builtin DESC,site_id").fetchall()
    profiles = []
    hashes: list[str] = []
    for row in rows:
        try:
            profile = _validate(json.loads(str(row["profile_json"] or "{}")))
        except Exception:
            continue
        digest = str(row["profile_hash"] or "")
        profile["hash"] = digest
        profile["builtin"] = bool(row["builtin"])
        profiles.append(profile)
        hashes.append(f"{profile['site_id']}:{digest}")
    global_digest = hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest() if hashes else "0"
    global_revision = int(global_digest[:8], 16) & 0x7FFFFFFF if hashes else 0
    return {"schema_version": SCHEMA_VERSION, "revision": global_revision, "profiles": profiles}
