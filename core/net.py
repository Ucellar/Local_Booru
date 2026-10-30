from __future__ import annotations

from typing import Any

DEFAULT_TIMEOUT = (10, 60)


def _normalise_timeout(timeout: Any):
    if timeout is None:
        return DEFAULT_TIMEOUT
    try:
        if isinstance(timeout, (int, float)) and float(timeout) <= 0:
            return DEFAULT_TIMEOUT
    except Exception:
        pass
    return timeout


def http_request(session, method: str, url: str, *, timeout=DEFAULT_TIMEOUT, max_attempts: int | None = None, **kwargs):
    """Requests-compatible HTTP wrapper with mandatory timeout and safe retries.

    Multipart POST uploads default to one attempt: retrying after an ambiguous
    timeout can upload the same image repeatedly and consume remote API quota.
    Other requests retain the normal three-attempt policy unless explicitly
    overridden by the caller.
    """
    from core.network import assert_url_allowed
    assert_url_allowed(url)
    kwargs.setdefault("timeout", _normalise_timeout(timeout))
    method_upper = str(method or "GET").upper()
    if max_attempts is None:
        max_attempts = 1 if method_upper == "POST" and "files" in kwargs else 3
    else:
        max_attempts = max(1, int(max_attempts))
    try:
        from core.shutdown import is_shutting_down
        if is_shutting_down():
            raise InterruptedError("application is shutting down")
    except InterruptedError:
        raise
    except Exception:
        pass

    try:
        from core.stability import retry_request
        # Sessions created by Local Booru already own the bounded retry loop.
        # Calling retry_request() around that wrapper multiplied a nominal
        # three-attempt request into as many as nine network attempts.  Pass
        # the per-call budget into the installed session instead and execute
        # exactly one retry layer.
        if bool(getattr(session, "_local_booru_safe_network", False)):
            kwargs["_local_booru_max_attempts"] = int(max_attempts)
            return getattr(session, method.lower())(url, **kwargs)
        return retry_request(session, method, url, max_attempts=max_attempts, **kwargs)
    except InterruptedError:
        raise
