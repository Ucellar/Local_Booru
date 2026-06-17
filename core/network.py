"""HTTP stability helpers for Local Booru.

One small wrapper is used by parser/downloader sessions so temporary network
failures are retried with hard timeouts and then surfaced as transient errors.
A transient error is never evidence that a site was checked successfully.
"""
from __future__ import annotations

import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

TRANSIENT_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}
TRANSIENT_ERROR_MARKERS = (
    "read timed out", "connect timed out", "connection timed out", "timeouterror",
    "timeout error", "connection aborted", "connection reset", "failed to resolve",
    "getaddrinfo failed", "nameresolutionerror", "temporary failure in name resolution",
    "network is unreachable", "connection refused", "max retries exceeded", "ssleoferror",
    "unexpected_eof_while_reading", "remote end closed connection", "remotedisconnected",
    "network temporary failure", "http 408", "http 425", "http 429", "http 500",
    "http 502", "http 503", "http 504",
)


def is_transient_network_error(value: object) -> bool:
    text = str(value or "").lower()
    return any(marker in text for marker in TRANSIENT_ERROR_MARKERS)


def host_from_url(url: str) -> str:
    try:
        return urlparse(str(url or "")).netloc.lower().replace("www.", "") or "network"
    except Exception:
        return "network"


def _setting_int(settings: dict | None, name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(float((settings or {}).get(name, default) or default))
    except Exception:
        value = default
    return max(low, min(high, value))


def _setting_float(settings: dict | None, name: str, default: float, low: float, high: float) -> float:
    try:
        value = float((settings or {}).get(name, default) or default)
    except Exception:
        value = default
    return max(low, min(high, value))


def timeout_tuple(settings: dict | None, timeout=None):
    """Return (connect_timeout, read_timeout). Existing larger read timeouts stay honoured."""
    connect = _setting_float(settings, "request_connect_timeout_seconds", 10.0, 1.0, 120.0)
    default_read = _setting_float(settings, "request_read_timeout_seconds", float((settings or {}).get("request_timeout_seconds", 30) or 30), 5.0, 300.0)
    if timeout is None:
        return (connect, default_read)
    if isinstance(timeout, tuple):
        try:
            return (float(timeout[0]), float(timeout[1]))
        except Exception:
            return (connect, default_read)
    try:
        read = float(timeout)
        return (connect, max(default_read, read))
    except Exception:
        return (connect, default_read)


def _retry_after_seconds(response) -> float:
    try:
        raw = str(response.headers.get("Retry-After", "") or "").strip()
        if not raw:
            return 0.0
        if raw.isdigit():
            return float(raw)
        dt = parsedate_to_datetime(raw)
        return max(0.0, dt.timestamp() - time.time())
    except Exception:
        return 0.0


def _safe_log(log_func, message: str) -> None:
    if callable(log_func):
        try:
            log_func(message)
        except Exception:
            pass


def request_with_retries(raw_call, method: str, url: str, *, settings: dict | None = None, log_func=None, cancel_callback=None, **kwargs):
    """Run one HTTP request with timeout + retry/backoff.

    All temporary network failures are raised after the retry budget is exhausted.
    Callers must treat that as pending/deferred, not as a normal miss.
    """
    settings = settings or {}
    attempts = _setting_int(settings, "network_retry_attempts", 3, 1, 8)
    base = _setting_float(settings, "network_retry_base_delay_seconds", 1.0, 0.0, 60.0)
    max_delay = _setting_float(settings, "network_retry_max_delay_seconds", 4.0, 0.0, 300.0)
    host = host_from_url(url)
    kwargs["timeout"] = timeout_tuple(settings, kwargs.get("timeout"))
    last_exc = None

    for attempt in range(1, attempts + 1):
        if callable(cancel_callback):
            try:
                if cancel_callback():
                    raise InterruptedError("request cancelled")
            except InterruptedError:
                raise
            except Exception:
                pass
        try:
            response = raw_call(url, **kwargs)
            status = int(getattr(response, "status_code", 0) or 0)
            if status in TRANSIENT_HTTP_STATUS:
                if attempt >= attempts:
                    try:
                        response.raise_for_status()
                    except Exception as exc:
                        last_exc = exc
                        _safe_log(log_func, f"NETWORK TEMPORARY FAILURE [{host}]: HTTP {status} after {attempts} attempt(s): {exc}")
                        raise
                    last_exc = RuntimeError(f"HTTP {status}: {url}")
                    _safe_log(log_func, f"NETWORK TEMPORARY FAILURE [{host}]: HTTP {status} after {attempts} attempt(s)")
                    raise last_exc
                # Retry-After is meaningful for rate limits (429).  Some
                # unstable reverse-search services incorrectly attach a long
                # Retry-After to 5xx responses; treating 502/503/504 like a
                # quota pause stalls the whole fallback chain and prevents the
                # next service from being tried.  For server errors, use the
                # normal short exponential backoff instead.
                wait = _retry_after_seconds(response) if status == 429 else 0.0
                if wait <= 0:
                    wait = min(max_delay, base * (2 ** (attempt - 1)))
                _safe_log(log_func, f"NETWORK RETRY [{host}]: HTTP {status}; attempt {attempt}/{attempts}; wait {wait:.1f}s")
                _sleep_interruptible(wait, cancel_callback)
                continue
            return response
        except InterruptedError:
            raise
        except Exception as exc:
            last_exc = exc
            if not is_transient_network_error(exc):
                raise
            if attempt >= attempts:
                _safe_log(log_func, f"NETWORK TEMPORARY FAILURE [{host}]: {type(exc).__name__}: {exc}")
                raise
            wait = min(max_delay, base * (2 ** (attempt - 1)))
            _safe_log(log_func, f"NETWORK RETRY [{host}]: {type(exc).__name__}; attempt {attempt}/{attempts}; wait {wait:.1f}s")
            _sleep_interruptible(wait, cancel_callback)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"request failed without response: {url}")


def _sleep_interruptible(seconds: float, cancel_callback=None) -> None:
    end = time.time() + max(0.0, float(seconds or 0))
    while time.time() < end:
        if callable(cancel_callback):
            try:
                if cancel_callback():
                    raise InterruptedError("request cancelled")
            except InterruptedError:
                raise
            except Exception:
                pass
        time.sleep(min(0.25, max(0.0, end - time.time())))


def install_safe_session(session, settings: dict | None = None, log_func=None, cancel_callback=None):
    """Patch a requests-like session in place. Idempotent."""
    if getattr(session, "_local_booru_safe_network", False):
        return session
    orig_get = session.get
    orig_post = session.post

    def safe_get(url, **kwargs):
        return request_with_retries(orig_get, "GET", url, settings=settings, log_func=log_func, cancel_callback=cancel_callback, **kwargs)

    def safe_post(url, **kwargs):
        return request_with_retries(orig_post, "POST", url, settings=settings, log_func=log_func, cancel_callback=cancel_callback, **kwargs)

    session.get = safe_get
    session.post = safe_post
    session._local_booru_safe_network = True
    return session
