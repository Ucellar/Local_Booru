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


class RequestCancelled(InterruptedError):
    """Raised when a queued HTTP request is cancelled before it is sent."""


class SauceNAOQuotaDeferred(RuntimeError):
    """Raised before network I/O when the durable long quota is reserved."""

    def __init__(self, retry_after: int, reason: str, *, remaining: int = -1, reserve: int = 0):
        self.retry_after = int(retry_after or 0)
        self.reason = str(reason or "quota_deferred")
        self.remaining = int(remaining)
        self.reserve = int(reserve)
        super().__init__(
            f"SauceNAO quota deferred until {self.retry_after}: {self.reason}; "
            f"remaining={self.remaining}; reserve={self.reserve}"
        )


def _check_cancelled(settings: dict | None = None) -> None:
    settings = settings or {}
    callback = settings.get("_cancel_callback") if isinstance(settings, dict) else None
    try:
        if callback and callback():
            raise RequestCancelled("HTTP request cancelled by user")
    except RequestCancelled:
        raise
    except Exception:
        return

_LOCK = threading.Lock()
_QUOTA_TLS = threading.local()
_LAST_BY_HOST: dict[str, float] = {}
_WINDOW_BY_HOST: dict[str, deque[float]] = defaultdict(deque)

_DEFAULT_DELAY = 0.75
# rule34.xxx's API takes many requests at once: a client made for it asks it 6 at a time with no pause between
# them and slows down only when it answers 429.  The parser does the same (core/parser/http.py, ``_HostGate``):
# ``_SITE_PARALLEL`` requests at once, a short spacing, no minute budget — a 429 makes the gate go one by one.
_SITE_DELAYS = {
    "saucenao.com": 1.10,
    "danbooru.donmai.us": 1.10,
    "booru.allthefallen.moe": 1.50,
    "rule34.xxx": 0.85,
    "api.rule34.xxx": 0.10,
    "rule34.us": 0.85,
    "gelbooru.com": 1.10,
    "xbooru.com": 0.85,
    "hypnohub.net": 0.85,
    "e621.net": 1.10,
    "e926.net": 1.10,
    "ascii2d.net": 1.10,
    "iqdb.org": 1.10,
}
# Danbooru: «a global rate limit on read requests of 10 per second … for longer sessions, please try to stay at around
# 1 request per second» (help:api); e621: 2 per second at most, «not more than one request per second over a
# sustained period» (its API help).  So about one a second (the spacing above), not one in three seconds (v5.23).
# Gelbooru publishes no number; gallery-dl asks its API with no pause at all.  Asked one file at a time (it does not
# understand md5: inside OR), 30 a minute made «добор новых сайтов» of 1800 files an hour long — one a second, like
# Danbooru, halves that (v5.23.1).  A 429 still makes the parser wait (core/parser/http.py).
_SITE_RPM = {
    "saucenao.com": 4,
    "booru.allthefallen.moe": 10,
    "danbooru.donmai.us": 54,
    "e621.net": 54,
    "e926.net": 54,
    "rule34.xxx": 30,
    "gelbooru.com": 54,
    "xbooru.com": 30,
    "hypnohub.net": 30,
    "ascii2d.net": 5,
    "iqdb.org": 10,
}
_SITE_PARALLEL = {
    "api.rule34.xxx": 6,
}
# What a service told about its own limits (SauceNAO: its account's searches per 30 seconds, in every answer): its
# spacing and minute budget until the program closes — instead of the conservative ones above.  A setting for the host
# still wins; a learned spacing never makes a host faster than the general one the person set.
_LEARNED_DELAY: dict[str, float] = {}
_LEARNED_RPM: dict[str, int] = {}


def learn_pace(host: str, *, interval: float | None = None, rpm: int | None = None) -> None:
    """Remember the pace a service told about itself (None — forget it)."""
    h = (host or "").lower().replace("www.", "")
    with _LOCK:
        if interval and interval > 0:
            _LEARNED_DELAY[h] = float(interval)
        else:
            _LEARNED_DELAY.pop(h, None)
        if rpm and rpm > 0:
            _LEARNED_RPM[h] = int(rpm)
        else:
            _LEARNED_RPM.pop(h, None)




def take_saucenao_admission_serial() -> int:
    """Return and clear the admission token for the current request thread."""
    try:
        value = int(getattr(_QUOTA_TLS, "saucenao_admission_serial", 0) or 0)
    except Exception:
        value = 0
    try:
        _QUOTA_TLS.saucenao_admission_serial = 0
    except Exception:
        pass
    return value


def _set_saucenao_admission_serial(value: int) -> None:
    try:
        _QUOTA_TLS.saucenao_admission_serial = int(value or 0)
    except Exception:
        pass

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
    base = float(_SITE_DELAYS.get(h, _DEFAULT_DELAY))
    try:
        if custom is not None and str(custom).strip() != "":
            base = max(0.0, float(custom))
    except Exception:
        pass
    learned = _LEARNED_DELAY.get(h)
    return max(base, learned) if learned else base


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
    return _LEARNED_RPM.get(h) or _SITE_RPM.get(h)


def parallel_for(host: str, settings: dict | None = None) -> int:
    """How many requests the parser may have open to ``host`` at once (1: one after another).  Hosts above 1 go
    through the parser's adaptive gate (core/parser/http.py), which drops to one at a time after HTTP 429."""
    settings = settings or {}
    h = (host or "").lower().replace("www.", "")
    overrides = settings.get("http_parallel_by_host") or {}
    try:
        if isinstance(overrides, dict) and h in overrides:
            return max(1, min(12, int(overrides[h])))
    except (TypeError, ValueError):
        pass
    return int(_SITE_PARALLEL.get(h, 1))


def wait_for(url_or_host: str, settings: dict | None = None) -> None:
    """Block until all host and service quota gates admit a request.

    SauceNAO's long-quota governor is durable and separate from the ordinary
    process-local interval/RPM windows.  Exhausted reserve is raised to the
    caller immediately so the file can enter the durable reverse retry queue
    without occupying a worker or touching the network.
    """
    settings = settings or {}
    _check_cancelled(settings)
    host = _host_from_url(url_or_host)
    delay = delay_for(host, settings)
    rpm = requests_per_minute_for(host, settings)
    try:
        from core.services.saucenao_quota import is_saucenao_api_request, enabled as _quota_enabled
        quota_request = bool(is_saucenao_api_request(url_or_host) and _quota_enabled(settings))
    except Exception:
        quota_request = False
    if quota_request:
        _set_saucenao_admission_serial(0)
    if delay <= 0 and not rpm and not quota_request:
        return
    while True:
        _check_cancelled(settings)
        with _LOCK:
            now = time.monotonic()
            window = _WINDOW_BY_HOST[host]
            while window and now - window[0] >= 60.0:
                window.popleft()
            wait_interval = max(0.0, delay - (now - _LAST_BY_HOST.get(host, 0.0))) if delay > 0 else 0.0
            wait_window = max(0.0, 60.0 - (now - window[0])) if rpm and len(window) >= rpm and window else 0.0
            wait = max(wait_interval, wait_window)
            admitted = not quota_request
            if wait <= 0 and quota_request:
                from core.services.saucenao_quota import admission as _quota_admission
                decision = _quota_admission(settings)
                if decision.deferred:
                    raise SauceNAOQuotaDeferred(
                        decision.retry_after,
                        decision.reason,
                        remaining=decision.remaining_before,
                        reserve=decision.reserve,
                    )
                wait = max(wait, float(decision.wait_seconds or 0.0))
                admitted = bool(decision.admitted)
            if wait <= 0 and admitted:
                _check_cancelled(settings)
                if quota_request:
                    _set_saucenao_admission_serial(int(getattr(decision, "admission_serial", 0) or 0))
                _LAST_BY_HOST[host] = now
                if rpm:
                    window.append(now)
                return
        # Sleep the complete calculated delay while checking cancellation in
        # short slices. Re-entering the outer loop every 100 ms would reopen a
        # write transaction for the same quota-spacing decision thousands of
        # times during a long adaptive interval.
        end = time.monotonic() + max(0.0, wait)
        while True:
            _check_cancelled(settings)
            left = end - time.monotonic()
            if left <= 0:
                break
            time.sleep(min(left, 0.10))


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
        end = time.monotonic() + wait
        while True:
            _check_cancelled(settings)
            left = end - time.monotonic()
            if left <= 0:
                break
            time.sleep(min(left, 0.10))
    except Exception:
        return
