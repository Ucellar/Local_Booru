"""JSON-driven site configuration loader.

Inspired by imgbrd-grabber's JSON site configs.
Each engine has a JSON file in core/sites/ that describes:
  - API endpoints for MD5 search
  - Response field mappings
  - Known hosts
  - CF protection status

Usage:
    driver = SiteDriver.for_engine("danbooru")
    attempts = driver.md5_attempts(root, md5, auth_params)
    # → [(url, params, format), ...]

    driver = SiteDriver.for_host("gelbooru.com")
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("local_booru.site_driver")

_SITES_DIR = Path(__file__).parent / "sites"


@lru_cache(maxsize=None)
def _load_config(json_path: str) -> dict:
    return json.loads(Path(json_path).read_text(encoding="utf-8"))


def _all_configs() -> list[dict]:
    configs = []
    for p in _SITES_DIR.glob("*.json"):
        try:
            configs.append(_load_config(str(p)))
        except Exception as e:
            log.warning("Failed to load site config %s: %s", p.name, e)
    return configs


def _resolve_field(post: dict, field_path: str) -> Any:
    """Resolve a dotted field path like 'file.md5' from nested dict."""
    parts = field_path.split(".")
    val = post
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)
        else:
            return None
    return val


class SiteDriver:
    """Wraps a JSON site config and provides MD5 search attempt generation."""

    def __init__(self, config: dict):
        self.cfg = config

    @property
    def engine(self) -> str:
        return self.cfg.get("engine", "unknown")

    @property
    def known_hosts(self) -> list[str]:
        return self.cfg.get("known_hosts", [])

    @property
    def requires_auth(self) -> bool:
        return self.cfg.get("requires_auth", False)

    @property
    def cf_protected(self) -> list[str]:
        return self.cfg.get("cf_protected", [])

    def is_cf_protected(self, host: str) -> bool:
        h = host.lower().replace("www.", "")
        return any(cf in h for cf in self.cf_protected)

    def md5_attempts(self, root: str, md5: str,
                     auth: dict | None = None) -> list[tuple[str, dict, str]]:
        """Build list of (url, params, format) for MD5 lookup.

        Returns empty list if requires_auth and no auth provided.
        """
        if self.requires_auth and not auth:
            return []

        auth = auth or {}
        result = []
        for attempt in self.cfg.get("md5_search", []):
            path = attempt["path"]
            raw_params = attempt.get("params", {})
            fmt = attempt.get("format", "json")

            # Substitute {md5} placeholder
            params = {}
            for k, v in raw_params.items():
                params[k] = str(v).replace("{md5}", md5)

            params.update(auth)
            result.append((root.rstrip("/") + path, params, fmt))

        return result

    def extract_fields(self, post: dict) -> dict[str, Any]:
        """Extract standardized fields from a raw post dict."""
        mappings = self.cfg.get("post_fields", {})
        result = {}
        for field, paths in mappings.items():
            for path in paths:
                val = _resolve_field(post, path)
                if val is not None:
                    result[field] = val
                    break
        return result

    def extract_post_list(self, data: Any) -> list[dict]:
        """Extract post list from API response."""
        paths = self.cfg.get("post_list_path", [None])
        for path in paths:
            if path is None:
                if isinstance(data, list):
                    return data
            elif isinstance(data, dict):
                val = data.get(path)
                if isinstance(val, list):
                    return val
                if isinstance(val, dict):
                    return [val]
        return []

    def post_url(self, root: str, post_id: Any) -> str:
        template = self.cfg.get("post_url", "{root}/posts/{id}")
        return template.replace("{root}", root.rstrip("/")).replace("{id}", str(post_id))

    @classmethod
    def for_engine(cls, engine_name: str) -> "SiteDriver | None":
        """Get driver by engine name."""
        for cfg in _all_configs():
            if cfg.get("engine") == engine_name:
                return cls(cfg)
        return None

    @classmethod
    def for_host(cls, host: str) -> "SiteDriver | None":
        """Get driver by hostname."""
        h = host.lower().replace("www.", "")
        for cfg in _all_configs():
            for known in cfg.get("known_hosts", []):
                if known in h or h in known:
                    return cls(cfg)
        return None

    @classmethod
    def all_drivers(cls) -> list["SiteDriver"]:
        return [cls(cfg) for cfg in _all_configs()]
