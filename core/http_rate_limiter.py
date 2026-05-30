"""Process-wide HTTP throttle used by parser, tagger and subscriptions.

Every network session made by the parser is wrapped through this module.  It
combines a small minimum interval with per-minute host budgets so parallel
workers cannot accidentally hammer SauceNAO or booru APIs.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

_LOCK = threading.Lock()
_LAST_BY_HOST: dict[str, float] = {}
_WINDOW_BY_HOST: dict[str, deque[float]] = defaultdict(deque)

_DEFAULT_DELAY = 0.75
_SITE_DELAYS = {
    "saucenao.com": 1.10,
    "danbooru.donmai.us": 1.10,
    "booru.allthefallen.moe": 1.50,
    "rule34.xxx": 0.85,
    "api.rule34.xxx": 0.85,
    "rule34.us": 0.85,
    "gelbooru.com": 0.85,
    "e621.net": 1.10,
    "ascii2d.net": 1.10,
    "iqdb.org": 1.10,
}
_SITE_RPM = {
    "saucenao.com": 4,
    "booru.allthefallen.moe": 10,
    "danbooru.donmai.us": 20,
    "e621.net": 20,
    "rule34.xxx": 30,
    "api.rule34.xxx": 30,
    "gelbooru.com": 30,
    "ascii2d.net": 5,
    "iqdb.org": 10,
}


def _host_from_url(url: str) -> str:
    try:
        return (urlparse(str(url)).netloc or str(url)).lower().replace("www.", "")
    except Exception:
        return str(url or "").lower().replace("www.", "")


def delay_for(host: str, settings: dict | None = None) -> float:
    settings = settings or {}
    if settings.get("http_rate_limit_enabled", True) is False:
        return 0.0
    h = (host or "").lower().replace("www.", "")
    by_host = settings.get("http_min_interval_by_host") or {}
    try:
        if isinstance(by_host, dict) and h in by_host:
            return max(0.0, float(by_host[h]))
    except Exception:
        pass
    custom = settings.get("http_rate_limit_seconds", settings.get("http_min_interval_seconds", None))
    try:
        if custom is not None and str(custom).strip() != "":
            return max(0.0, float(custom))
    except Exception:
        pass
    return float(_SITE_DELAYS.get(h, _DEFAULT_DELAY))


def requests_per_minute_for(host: str, settings: dict | None = None) -> int | None:
    settings = settings or {}
    if settings.get("http_rate_limit_enabled", True) is False:
        return None
    h = (host or "").lower().replace("www.", "")
    overrides = settings.get("http_requests_per_minute_by_host") or {}
    try:
        if isinstance(overrides, dict) and h in overrides:
            value = int(overrides[h])
            return value if value > 0 else None
    except Exception:
        pass
    return _SITE_RPM.get(h)


def wait_for(url_or_host: str, settings: dict | None = None) -> None:
    """Block until both interval and per-minute limits allow one request."""
    host = _host_from_url(url_or_host)
    delay = delay_for(host, settings)
    rpm = requests_per_minute_for(host, settings)
    if delay <= 0 and not rpm:
        return
    while True:
        with _LOCK:
            now = time.monotonic()
            window = _WINDOW_BY_HOST[host]
            while window and now - window[0] >= 60.0:
                window.popleft()
            wait_interval = max(0.0, delay - (now - _LAST_BY_HOST.get(host, 0.0))) if delay > 0 else 0.0
            wait_window = max(0.0, 60.0 - (now - window[0])) if rpm and len(window) >= rpm and window else 0.0
            wait = max(wait_interval, wait_window)
            if wait <= 0:
                _LAST_BY_HOST[host] = now
                if rpm:
                    window.append(now)
                return
        time.sleep(min(wait, 0.25))


def apply_retry_after(response, settings: dict | None = None) -> None:
    """Respect Retry-After for 429/503 and delay later requests to that host."""
    try:
        code = int(getattr(response, "status_code", 0) or 0)
        if code not in (429, 503):
            return
        ra = getattr(response, "headers", {}).get("Retry-After")
        try:
            wait = min(max(float(ra), 1.0), 300.0) if ra is not None else 30.0
        except Exception:
            wait = 30.0
        url = getattr(response, "url", "")
        host = _host_from_url(url)
        if host:
            with _LOCK:
                _LAST_BY_HOST[host] = time.monotonic() + wait
        time.sleep(wait)
    except Exception:
        return
