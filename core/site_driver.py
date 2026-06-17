"""JSON-driven booru site driver loader.

Site MD5 lookup contracts live in the user workspace, not in tagger code.
Runtime location in portable mode:
    Local_Booru_Archive/settings/sites/*.json

The bundled defaults below are only templates used to seed the workspace on
first run. After that, the loader reads JSON from settings/sites only; fixing a
broken endpoint or adding a compatible site does not require editing Python.
"""
from __future__ import annotations

import copy
import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.paths import DATA_DIR

log = logging.getLogger("local_booru.site_driver")

# User-editable canonical site config directory.  In the agreed portable layout
# DATA_DIR is Local_Booru_Archive/settings.
SITE_CONFIG_DIR = DATA_DIR / "sites"

_DEFAULT_SITE_CONFIGS: dict[str, dict[str, Any]] = {
    "danbooru.json": {
        "engine": "danbooru",
        "description": "Danbooru 2.x API: exact MD5 search through tags=md5:<hash>",
        "known_hosts": ["danbooru.donmai.us", "donmai.us"],
        "requires_auth": False,
        "cf_protected": ["danbooru.donmai.us"],
        "strict_json_only": True,
        "accept_missing_md5_on_exact_query": True,
        "md5_search": [
            {"path": "/posts.json", "params": {"tags": "md5:{md5}", "limit": 1}, "format": "json"}
        ],
        "post_fields": {
            "id": ["id"],
            "md5": ["md5", "file_md5", "file.md5"],
            "file_url": ["file_url", "large_file_url", "file.url"],
            "source": ["source"],
            "rating": ["rating"],
            "tags": ["tag_string", "tags"],
            "tag_artist": ["tag_string_artist"],
            "tag_character": ["tag_string_character"],
            "tag_copyright": ["tag_string_copyright"],
            "tag_general": ["tag_string_general"],
            "tag_meta": ["tag_string_meta"]
        },
        "post_list_path": ["posts", None],
        "post_url": "{root}/posts/{id}"
    },
    "atf.json": {
        "engine": "danbooru",
        "description": "AllTheFallen / Danbooru-compatible API; JSON-only exact MD5, no HTML rescue",
        "known_hosts": ["booru.allthefallen.moe"],
        "requires_auth": False,
        "cf_protected": ["booru.allthefallen.moe"],
        "strict_json_only": True,
        "accept_missing_md5_on_exact_query": False,
        "md5_search": [
            {"path": "/posts.json", "params": {"tags": "md5:{md5}", "limit": 1}, "format": "json"}
        ],
        "post_fields": {
            "id": ["id"],
            "md5": ["md5", "file_md5", "file.md5"],
            "file_url": ["file_url", "large_file_url", "file.url"],
            "source": ["source"],
            "rating": ["rating"],
            "tags": ["tag_string", "tags"],
            "tag_artist": ["tag_string_artist"],
            "tag_character": ["tag_string_character"],
            "tag_copyright": ["tag_string_copyright"],
            "tag_general": ["tag_string_general"],
            "tag_meta": ["tag_string_meta"]
        },
        "post_list_path": ["posts", None],
        "post_url": "{root}/posts/{id}"
    },
    "gelbooru.json": {
        "engine": "gelbooru",
        "description": "Gelbooru-compatible DAPI exact MD5 search",
        "known_hosts": ["gelbooru.com", "xbooru.com", "realbooru.com", "tbib.org", "safebooru.org"],
        "requires_auth": False,
        "cf_protected": [],
        "strict_json_only": True,
        "health_probe_on_miss": True,
        "tag_category_mode": "gelbooru_tag_list_api",
        "md5_search": [
            {"path": "/index.php", "params": {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "md5:{md5}", "limit": 1}, "format": "json"}
        ],
        "post_fields": {
            "id": ["id"],
            "md5": ["md5", "hash"],
            "file_url": ["file_url", "image"],
            "source": ["source"],
            "rating": ["rating"],
            "tags": ["tags", "tag_string"]
        },
        "post_list_path": ["post", "@post", None],
        "post_url": "{root}/index.php?page=post&s=view&id={id}"
    },
    "hypnohub.json": {
        "engine": "hypnohub",
        "description": "HypnoHub Gelbooru-compatible DAPI exact MD5 search",
        "known_hosts": ["hypnohub.net"],
        "requires_auth": False,
        "cf_protected": [],
        "strict_json_only": True,
        "health_probe_on_miss": True,
        "md5_search": [
            {"path": "/index.php", "params": {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "md5:{md5}", "limit": 1}, "format": "json"}
        ],
        "post_fields": {
            "id": ["id"],
            "md5": ["md5", "hash"],
            "file_url": ["file_url", "image"],
            "source": ["source"],
            "rating": ["rating"],
            "tags": ["tags", "tag_string"]
        },
        "post_list_path": ["post", "@post", None],
        "post_url": "{root}/index.php?page=post&s=view&id={id}"
    },
    "rule34xxx.json": {
        "engine": "rule34xxx",
        "description": "rule34.xxx official DAPI: api.rule34.xxx JSON, user_id+api_key, flat tags only",
        "known_hosts": ["rule34.xxx", "api.rule34.xxx"],
        "requires_auth": True,
        "cf_protected": ["rule34.xxx"],
        "strict_json_only": True,
        "fast_flat_tags": True,
        "image_key_locator": True,
        "html_md5_locator": False,
        "md5_search": [
            {"url": "https://api.rule34.xxx/index.php", "params": {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "md5:{md5}", "limit": 1}, "format": "json"}
        ],
        "post_fields": {
            "id": ["id"],
            "md5": ["md5", "hash"],
            "file_url": ["file_url", "image"],
            "source": ["source"],
            "rating": ["rating"],
            "tags": ["tags", "tag_string"]
        },
        "post_list_path": ["post", "@post", None],
        "post_url": "{root}/index.php?page=post&s=view&id={id}"
    },
    "e621.json": {
        "engine": "e621",
        "description": "e621/e926 public JSON API: exact MD5 through tags=md5:<hash>",
        "known_hosts": ["e621.net", "e926.net"],
        "requires_auth": False,
        "cf_protected": [],
        "user_agent_required": True,
        "strict_json_only": True,
        "md5_search": [
            {"path": "/posts.json", "params": {"tags": "md5:{md5}", "limit": 1, "v2": "true", "mode": "extended"}, "format": "json"},
            {"path": "/posts.json", "params": {"tags": "md5:{md5}", "limit": 1}, "format": "json"}
        ],
        "post_fields": {
            "id": ["id"],
            "md5": ["file.md5", "files.meta.md5", "md5"],
            "file_url": ["file.url", "files.original.url", "file_url"],
            "preview_url": ["preview.url", "files.preview.webp", "files.preview.jpg", "sample.url", "files.sample.webp", "files.sample.jpg"],
            "source": ["sources", "source"],
            "rating": ["rating"],
            "tags": ["tags.general", "tags.artist", "tags.contributor", "tags.character", "tags.copyright", "tags.species", "tags.meta", "tags.lore", "tags.invalid", "tags"],
            "tag_artist": ["tags.artist"],
            "tag_character": ["tags.character"],
            "tag_copyright": ["tags.copyright"],
            "tag_general": ["tags.general"],
            "tag_species": ["tags.species"],
            "tag_meta": ["tags.meta"],
            "tag_contributor": ["tags.contributor"],
            "tag_lore": ["tags.lore"],
            "tag_invalid": ["tags.invalid"]
        },
        "post_list_path": ["posts", None],
        "post_url": "{root}/posts/{id}",
        "reverse_image_search": {
            "name": "e621_iqdb",
            "description": "e621 internal IQDB endpoint; POST multipart file upload, login/api-key required",
            "method": "POST",
            "path": "/iqdb_queries.json",
            "file_field": "file",
            "auth": "basic",
            "requires_auth": True,
            "format": "json",
            "params": {"v2": "true", "mode": "extended"},
            "result_list_path": [None, "results", "posts", "iqdb_queries", "matches"],
            "post_id_fields": ["post_id", "post.id", "id"],
            "score_fields": ["similarity", "score", "distance", "rank"],
            "post_url": "{root}/posts/{post_id}",
            "max_results": 5
        }
    },
    "moebooru.json": {
        "engine": "moebooru",
        "description": "Moebooru API: exact MD5 search through JSON post listing",
        "known_hosts": ["konachan.com", "konachan.net", "yande.re"],
        "requires_auth": False,
        "cf_protected": [],
        "strict_json_only": False,
        "md5_search": [
            {"path": "/post/index.json", "params": {"tags": "md5:{md5}", "limit": 1}, "format": "json"},
            {"path": "/posts.json", "params": {"tags": "md5:{md5}", "limit": 1}, "format": "json"}
        ],
        "post_fields": {
            "id": ["id"],
            "md5": ["md5"],
            "file_url": ["file_url"],
            "source": ["source"],
            "rating": ["rating"],
            "tags": ["tags"]
        },
        "post_list_path": [None],
        "post_url": "{root}/post/show/{id}"
    },
    "rule34us.json": {
        "engine": "rule34us",
        "description": "rule34.us has no confirmed JSON MD5 API; strict HTML fallback only",
        "known_hosts": ["rule34.us"],
        "requires_auth": False,
        "cf_protected": ["rule34.us"],
        "strict_json_only": False,
        "html_fallback": "rule34us_strict",
        "md5_search": [],
        "post_fields": {
            "id": ["id"],
            "md5": ["md5"],
            "file_url": ["file_url"],
            "source": ["source"],
            "rating": ["rating"],
            "tags": ["tags"]
        },
        "post_list_path": [None],
        "post_url": "{root}/index.php?page=post&s=view&id={id}"
    }
}


def _deepcopy_default(cfg: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(cfg)


def _merge_missing(default: Any, current: Any) -> tuple[Any, bool]:
    """Return current with keys missing from default added, preserving edits."""
    if isinstance(default, dict) and isinstance(current, dict):
        changed = False
        merged = dict(current)
        for key, value in default.items():
            if key not in merged:
                merged[key] = copy.deepcopy(value)
                changed = True
            else:
                new_value, child_changed = _merge_missing(value, merged[key])
                if child_changed:
                    merged[key] = new_value
                    changed = True
        return merged, changed
    return current, False


def _append_unique_paths(cfg: dict[str, Any], field: str, additions: list[str]) -> bool:
    post_fields = cfg.setdefault("post_fields", {})
    if not isinstance(post_fields, dict):
        cfg["post_fields"] = {}
        post_fields = cfg["post_fields"]
    current = post_fields.get(field, [])
    if isinstance(current, str) or current is None:
        current = [current]
    elif not isinstance(current, list):
        current = []
    changed = False
    for path in additions:
        if path not in current:
            current.append(path)
            changed = True
    if changed or post_fields.get(field) != current:
        post_fields[field] = current
        changed = True
    return changed


def _upgrade_e621_v2_compat(cfg: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Preserve user edits while adding e621 post API v2 compatibility knobs.

    Older workspaces already have settings/sites/e621.json.  A plain
    merge-missing pass cannot update lists like md5_search or post_fields, so
    add only the specific v2-safe paths/params we need without removing custom
    endpoints.
    """
    if not isinstance(cfg, dict):
        return cfg, False
    known = [str(v).lower().replace("www.", "") for v in (cfg.get("known_hosts") or [])]
    if str(cfg.get("engine") or "").lower() != "e621" and not any(h in ("e621.net", "e926.net") for h in known):
        return cfg, False
    changed = False

    changed |= _append_unique_paths(cfg, "md5", ["files.meta.md5"])
    changed |= _append_unique_paths(cfg, "file_url", ["files.original.url"])
    changed |= _append_unique_paths(cfg, "preview_url", ["files.preview.webp", "files.preview.jpg", "files.sample.webp", "files.sample.jpg"])
    changed |= _append_unique_paths(cfg, "tags", ["tags.general", "tags.artist", "tags.contributor", "tags.character", "tags.copyright", "tags.species", "tags.meta", "tags.lore", "tags.invalid", "tags"])
    for group in ("artist", "character", "copyright", "general", "species", "meta", "contributor", "lore", "invalid"):
        changed |= _append_unique_paths(cfg, f"tag_{group}", [f"tags.{group}"])

    attempts = cfg.get("md5_search")
    if isinstance(attempts, list):
        has_v2 = False
        for attempt in attempts:
            if isinstance(attempt, dict):
                params = attempt.get("params") or {}
                if isinstance(params, dict) and str(params.get("v2") or "").lower() == "true":
                    has_v2 = True
                    break
        if not has_v2:
            base = None
            for attempt in attempts:
                if isinstance(attempt, dict):
                    base = copy.deepcopy(attempt)
                    break
            if base is None:
                base = {"path": "/posts.json", "params": {"tags": "md5:{md5}", "limit": 1}, "format": "json"}
            params = base.get("params") if isinstance(base.get("params"), dict) else {}
            params = dict(params)
            params.update({"v2": "true", "mode": "extended"})
            base["params"] = params
            attempts.insert(0, base)
            cfg["md5_search"] = attempts
            changed = True

    reverse = cfg.get("reverse_image_search")
    if isinstance(reverse, dict):
        params = reverse.get("params") if isinstance(reverse.get("params"), dict) else {}
        if params.get("v2") != "true" or params.get("mode") != "extended":
            params = dict(params)
            params.update({"v2": "true", "mode": "extended"})
            reverse["params"] = params
            changed = True
    return cfg, changed



def _upgrade_rule34xxx_md5_param(cfg: dict[str, Any], *, file_name: str = "") -> tuple[dict[str, Any], bool]:
    """Normalize rule34.xxx drivers to the official DAPI contract.

    v311 stops treating the browser/main host and XML fallback as normal API
    probes. The documented endpoint is api.rule34.xxx/index.php with json=1
    and user_id+api_key query credentials. Returned posts are still accepted
    only when their explicit remote MD5/hash equals the local MD5.
    """
    if not isinstance(cfg, dict):
        return cfg, False
    name = str(file_name or cfg.get("_config_file") or cfg.get("id") or cfg.get("name") or "").lower()
    engine = str(cfg.get("engine") or "").lower()
    known = [str(v or "").lower().replace("www.", "") for v in (cfg.get("known_hosts") or [])]
    text = " ".join([name, engine, " ".join(known)])
    if "rule34xxx" not in text and "rule34.xxx" not in text and "api.rule34.xxx" not in text:
        return cfg, False

    changed = False

    def set_if(key, value):
        nonlocal changed
        if cfg.get(key) != value:
            cfg[key] = value
            changed = True

    set_if("engine", "rule34xxx")
    if cfg.get("known_hosts") != ["rule34.xxx", "api.rule34.xxx"]:
        cfg["known_hosts"] = ["rule34.xxx", "api.rule34.xxx"]
        changed = True
    set_if("strict_json_only", True)
    set_if("fast_flat_tags", True)
    set_if("requires_auth", True)

    wanted = [
        {"url": "https://api.rule34.xxx/index.php", "params": {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": "md5:{md5}", "limit": 1}, "format": "json"},
    ]
    if cfg.get("md5_search") != wanted:
        cfg["md5_search"] = copy.deepcopy(wanted)
        changed = True
    return cfg, changed


def _upgrade_atf_api_first(cfg: dict[str, Any], *, file_name: str = "") -> tuple[dict[str, Any], bool]:
    """Force legacy ATF site-driver files back to the documented JSON API path.

    Older portable workspaces may already contain settings/sites/atf.json.  The
    normal merge-missing upgrade intentionally preserves user edits, but here
    preserving an old HTML/DAPI/custom endpoint is unsafe: ATF is a Danbooru-like
    site and automatic MD5 metadata must come only from /posts.json?tags=md5:<hash>.
    Auth stays in the user site settings, not in this driver file.
    """
    if not isinstance(cfg, dict):
        return cfg, False

    name = str(file_name or cfg.get("_config_file") or cfg.get("id") or cfg.get("name") or "").lower()
    engine = str(cfg.get("engine") or "").lower()
    known = [str(v or "").lower().replace("www.", "") for v in (cfg.get("known_hosts") or [])]
    text = " ".join([name, engine, " ".join(known), json.dumps(cfg, ensure_ascii=False).lower()])
    if "allthefallen" not in text and "atf" not in name:
        return cfg, False

    changed = False

    forced_values = {
        "engine": "danbooru",
        "description": "AllTheFallen / Danbooru-compatible API; API-first exact MD5 via /posts.json, no automatic HTML rescue",
        "known_hosts": ["booru.allthefallen.moe"],
        "requires_auth": False,
        "cf_protected": ["booru.allthefallen.moe"],
        "strict_json_only": True,
        "accept_missing_md5_on_exact_query": False,
        "md5_search": [
            {"path": "/posts.json", "params": {"tags": "md5:{md5}", "limit": 1}, "format": "json"}
        ],
        "post_list_path": ["posts", None],
        "post_url": "{root}/posts/{id}",
    }
    for key, value in forced_values.items():
        if cfg.get(key) != value:
            cfg[key] = copy.deepcopy(value)
            changed = True

    default_fields = copy.deepcopy(_DEFAULT_SITE_CONFIGS["atf.json"].get("post_fields") or {})
    if cfg.get("post_fields") != default_fields:
        cfg["post_fields"] = default_fields
        changed = True

    return cfg, changed


def ensure_site_configs() -> Path:
    """Create settings/sites and seed missing default JSON configs.

    Existing files are not overwritten.  When a later build adds new config keys
    to a default file, only missing keys are merged in so user endpoint edits stay
    intact while the driver can still use new JSON-controlled flags.
    """
    SITE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for name, cfg in _DEFAULT_SITE_CONFIGS.items():
        path = SITE_CONFIG_DIR / name
        try:
            if path.exists():
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    broken = path.with_name(f"{path.name}.broken-{int(time.time())}")
                    try:
                        path.replace(broken)
                    except Exception:
                        broken = None
                    seeded = copy.deepcopy(cfg)
                    seeded, _ = _upgrade_e621_v2_compat(seeded)
                    seeded, _ = _upgrade_atf_api_first(seeded, file_name=name)
                    seeded, _ = _upgrade_rule34xxx_md5_param(seeded, file_name=name)
                    path.write_text(json.dumps(seeded, ensure_ascii=False, indent=2), encoding="utf-8")
                    _load_config.cache_clear()
                    if broken:
                        log.warning("Recreated broken site config %s from default; backup=%s; error=%s", path, broken, e)
                    else:
                        log.warning("Recreated broken site config %s from default; backup failed; error=%s", path, e)
                    continue
                if isinstance(current, dict):
                    merged, changed = _merge_missing(cfg, current)
                    merged, upgraded_e621 = _upgrade_e621_v2_compat(merged)
                    merged, upgraded_atf = _upgrade_atf_api_first(merged, file_name=name)
                    merged, upgraded_rule34 = _upgrade_rule34xxx_md5_param(merged, file_name=name)
                    if changed or upgraded_e621 or upgraded_atf or upgraded_rule34:
                        path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
                        _load_config.cache_clear()
                else:
                    broken = path.with_name(f"{path.name}.broken-{int(time.time())}")
                    try:
                        path.replace(broken)
                    except Exception:
                        broken = None
                    seeded = copy.deepcopy(cfg)
                    seeded, _ = _upgrade_e621_v2_compat(seeded)
                    seeded, _ = _upgrade_atf_api_first(seeded, file_name=name)
                    seeded, _ = _upgrade_rule34xxx_md5_param(seeded, file_name=name)
                    path.write_text(json.dumps(seeded, ensure_ascii=False, indent=2), encoding="utf-8")
                    _load_config.cache_clear()
                    log.warning("Recreated non-object site config %s from default; backup=%s", path, broken or "failed")
                continue
            seeded = copy.deepcopy(cfg)
            seeded, _ = _upgrade_e621_v2_compat(seeded)
            seeded, _ = _upgrade_atf_api_first(seeded, file_name=name)
            seeded, _ = _upgrade_rule34xxx_md5_param(seeded, file_name=name)
            path.write_text(json.dumps(seeded, ensure_ascii=False, indent=2), encoding="utf-8")
            _load_config.cache_clear()
        except Exception as e:
            log.warning("Failed to seed/update site config %s: %s", path, e)
    return SITE_CONFIG_DIR


@lru_cache(maxsize=None)
def _load_config(json_path: str) -> dict[str, Any]:
    return json.loads(Path(json_path).read_text(encoding="utf-8"))


def clear_site_config_cache() -> None:
    _load_config.cache_clear()


def _all_configs() -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    try:
        ensure_site_configs()
        paths = sorted(SITE_CONFIG_DIR.glob("*.json"))
    except Exception:
        paths = []
    for p in paths:
        try:
            cfg = _load_config(str(p))
            if isinstance(cfg, dict):
                cfg = dict(cfg)
                cfg.setdefault("_config_file", str(p))
                configs.append(cfg)
        except Exception as e:
            log.warning("Failed to load site config %s: %s", p.name, e)
    if configs:
        return configs
    # Emergency fallback when settings/sites cannot be created/read.
    return [_deepcopy_default(v) for v in _DEFAULT_SITE_CONFIGS.values()]


def _resolve_field(value: Any, field_path: str | None) -> Any:
    """Resolve a dotted field path like 'file.md5' from nested dict/list data."""
    if field_path is None:
        return value
    cur = value
    for part in str(field_path).split("."):
        if part == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                idx = int(part)
                cur = cur[idx]
            except Exception:
                return None
        else:
            return None
    return cur


def _flatten_tag_values(value: Any) -> list[str]:
    out: list[str] = []
    def add(v: Any) -> None:
        if v is None:
            return
        if isinstance(v, str):
            out.extend(v.replace(",", " ").split())
        elif isinstance(v, (list, tuple, set)):
            for item in v:
                add(item)
        elif isinstance(v, dict):
            for group in v.values():
                add(group)
    add(value)
    # Keep order, do not normalize here; engine.py owns normalization rules.
    seen = set()
    uniq = []
    for tag in out:
        tag = str(tag).strip()
        if tag and tag not in seen:
            seen.add(tag)
            uniq.append(tag)
    return uniq


def _render_template(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        result = value
        for key, replacement in variables.items():
            result = result.replace("{" + key + "}", str(replacement))
        return result
    if isinstance(value, list):
        return [_render_template(v, variables) for v in value]
    if isinstance(value, dict):
        return {str(k): _render_template(v, variables) for k, v in value.items()}
    return value


class SiteDriver:
    """Wraps one JSON site config and provides MD5 lookup helpers."""

    def __init__(self, config: dict[str, Any]):
        self.cfg = config if isinstance(config, dict) else {}

    @property
    def engine(self) -> str:
        return str(self.cfg.get("engine") or "unknown")

    @property
    def known_hosts(self) -> list[str]:
        return [str(v).lower().replace("www.", "") for v in self.cfg.get("known_hosts", [])]

    @property
    def requires_auth(self) -> bool:
        return bool(self.cfg.get("requires_auth", False))

    @property
    def cf_protected(self) -> list[str]:
        return [str(v).lower().replace("www.", "") for v in self.cfg.get("cf_protected", [])]

    def is_cf_protected(self, host: str) -> bool:
        h = str(host or "").lower().replace("www.", "")
        return any(cf == h or h.endswith("." + cf) or cf in h for cf in self.cf_protected)

    def md5_attempts(self, root: str, md5: str, auth: dict | None = None, site: dict | None = None) -> list[tuple[str, dict, str]]:
        """Build list of (url, params, format) from JSON md5_search entries."""
        if self.requires_auth and not auth:
            return []
        root = str(root or "").rstrip("/")
        auth = dict(auth or {})
        site = site if isinstance(site, dict) else {}
        variables = {
            "root": root,
            "md5": str(md5 or ""),
            "domain": str(site.get("domain") or ""),
            "host": str(site.get("domain") or ""),
            "login": str(site.get("login") or ""),
            "api_key": str(site.get("api_key") or ""),
            "user_id": str(site.get("user_id") or ""),
        }
        result: list[tuple[str, dict, str]] = []
        for attempt in self.cfg.get("md5_search", []) or []:
            if not isinstance(attempt, dict):
                continue
            raw_url = attempt.get("url") or attempt.get("endpoint")
            path = attempt.get("path", "")
            if raw_url:
                url = str(_render_template(raw_url, variables)).strip()
            else:
                path_s = str(_render_template(path, variables) or "").strip()
                if path_s.startswith(("http://", "https://")):
                    url = path_s
                else:
                    if path_s and not path_s.startswith("/"):
                        path_s = "/" + path_s
                    url = root + path_s
            if not url:
                continue
            params = _render_template(attempt.get("params", {}) or {}, variables)
            if not isinstance(params, dict):
                params = {}
            params.update(auth)
            fmt = str(attempt.get("format") or "json").lower()
            result.append((url, params, fmt))
        return result

    def extract_field(self, post: dict, field: str) -> Any:
        mappings = self.cfg.get("post_fields", {}) or {}
        paths = mappings.get(field, [])
        if isinstance(paths, str) or paths is None:
            paths = [paths]
        for path in paths:
            val = _resolve_field(post, path)
            if val not in (None, "", [], {}):
                return val
        return None

    def extract_fields(self, post: dict) -> dict[str, Any]:
        mappings = self.cfg.get("post_fields", {}) or {}
        return {field: self.extract_field(post, field) for field in mappings if self.extract_field(post, field) is not None}

    def extract_tags(self, post: dict) -> list[str]:
        mappings = self.cfg.get("post_fields", {}) or {}
        paths = mappings.get("tags", [])
        if isinstance(paths, str) or paths is None:
            paths = [paths]
        tags: list[str] = []
        for path in paths:
            tags.extend(_flatten_tag_values(_resolve_field(post, path)))
        seen = set()
        uniq = []
        for tag in tags:
            if tag not in seen:
                seen.add(tag)
                uniq.append(tag)
        return uniq

    def extract_tag_groups(self, post: dict) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        mappings = self.cfg.get("post_fields", {}) or {}
        for field, paths in mappings.items():
            field_s = str(field)
            if not field_s.startswith("tag_"):
                continue
            group = field_s[4:]
            if isinstance(paths, str) or paths is None:
                paths = [paths]
            values: list[str] = []
            for path in paths:
                values.extend(_flatten_tag_values(_resolve_field(post, path)))
            if values:
                groups[group] = values
        return groups

    def extract_post_list(self, data: Any) -> list[dict]:
        paths = self.cfg.get("post_list_path", [None])
        if isinstance(paths, (str, type(None))):
            paths = [paths]
        for path in paths:
            val = _resolve_field(data, path)
            if isinstance(val, list):
                return [v for v in val if isinstance(v, dict)]
            if isinstance(val, dict):
                return [val]
        return []

    def post_url(self, root: str, post_id: Any) -> str:
        if post_id in (None, ""):
            return ""
        template = str(self.cfg.get("post_url") or "{root}/posts/{id}")
        return template.replace("{root}", str(root or "").rstrip("/")).replace("{id}", str(post_id))

    @classmethod
    def from_site_dict(cls, site: dict) -> "SiteDriver | None":
        if isinstance(site, dict) and isinstance(site.get("md5_search"), list):
            return cls(site)
        return None

    @classmethod
    def for_site(cls, site: dict | None = None, engine_name: str = "", host: str = "") -> "SiteDriver | None":
        site = site if isinstance(site, dict) else {}
        inline = cls.from_site_dict(site)
        if inline is not None:
            return inline
        requested = str(site.get("site_config") or site.get("driver") or "").strip().lower()
        h = str(host or site.get("domain") or "").lower().replace("www.", "")
        e = str(engine_name or site.get("engine") or site.get("type") or "").strip().lower()
        configs = _all_configs()
        if requested:
            for cfg in configs:
                names = [str(cfg.get("engine") or "").lower(), str(cfg.get("name") or "").lower(), str(cfg.get("id") or "").lower()]
                if requested in names:
                    return cls(cfg)
        # Host-specific config wins over generic engine config.  This is what
        # lets rule34.xxx use its own API host while other Gelbooru DAPI sites
        # keep the normal /index.php path without code branches.
        if h:
            for cfg in configs:
                for known in cfg.get("known_hosts", []) or []:
                    known_s = str(known or "").lower().replace("www.", "")
                    if h == known_s or h.endswith("." + known_s) or known_s in h:
                        return cls(cfg)
        if e:
            for cfg in configs:
                if str(cfg.get("engine") or "").lower() == e:
                    return cls(cfg)
        return None

    @classmethod
    def for_engine(cls, engine_name: str) -> "SiteDriver | None":
        return cls.for_site({}, engine_name=engine_name, host="")

    @classmethod
    def for_host(cls, host: str) -> "SiteDriver | None":
        return cls.for_site({}, engine_name="", host=host)

    @classmethod
    def all_drivers(cls) -> list["SiteDriver"]:
        return [cls(cfg) for cfg in _all_configs()]
