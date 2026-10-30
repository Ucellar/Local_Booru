"""HTTP layer of the parser.

One requests.Session per (worker thread, host), created once and reused for the
whole run.  Every request goes through the process-wide host throttle
(core.http_rate_limiter), is cancellable, retries only transient failures, and
never retries a file upload (an ambiguous timeout would spend API quota twice).

A host that takes many requests at once (rule34.xxx's API, ``parallel_for``) goes through a gate
(``_HostGate``): at most that many requests at a time; an HTTP 429 pauses the host shortly
(1 → 2 → 4 → 8 → 16 s, one pause per burst), then the requests go one at a time and one more
with every answer — the way a client made for that API asks it.  Such a 429 is asked again after the pause
and is not a failure of the site.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import requests

from core.parser.util import host_of

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


class Cancelled(Exception):
    """The user pressed Stop (or the app is closing)."""


class HttpFailure(Exception):
    """Request failed: network error after retries (transient) or an unusable answer.

    transient=True  — DNS, timeout, reset, HTTP 429/5xx: try again later;
    transient=False — the answer exists but is unusable (404, HTML instead of media...).
    """

    def __init__(self, host: str, detail: str, *, status: int = 0, transient: bool = True):
        self.host = host
        self.detail = detail
        self.status = int(status or 0)
        self.transient = transient
        super().__init__(f"[{host}] {detail}")


_GATE_TRIES = 6             # a 429 of a gated host is asked again after the pause, this many times in all


class _HostGate:
    """Requests in flight to one host that takes many at once.  ``most`` at a time while it answers; HTTP 429: a
    pause of the host (doubling 1 → 16 s, one per burst: the 429 of requests sent before it do not lengthen it), then
    one request at a time and one more after every answer, up to ``most`` again.  Waiting happens outside the lock,
    with the run's stop and pause checked."""

    def __init__(self, most: int):
        self.most = max(1, int(most))
        self.cap = self.most
        self.busy = 0
        self.until = 0.0          # time.monotonic(): the pause after a 429 lasts until then
        self.back = 0.0           # the length of the last pause
        self.epoch = 0            # grows with every 429: an answer to a request sent before it adds no place
        self._cond = threading.Condition()

    def enter(self, check_cancel: Callable[[], None]) -> int:
        while True:
            check_cancel()
            with self._cond:
                now = time.monotonic()
                if now >= self.until and self.busy < self.cap:
                    self.busy += 1
                    return self.epoch
                wait = self.until - now if now < self.until else 0.2
                self._cond.wait(min(0.2, max(0.01, wait)))

    def leave(self, epoch: int, status: int | None, retry_after: float = 0.0) -> tuple[float, bool]:
        """A request is done (``status`` None: no answer).  Returns (seconds to wait before asking again, True when
        this 429 started a new pause)."""
        with self._cond:
            self.busy = max(0, self.busy - 1)
            now = time.monotonic()
            fresh = False
            if status == 429:
                if self.until <= now:
                    self.back = min(16.0, max(1.0, self.back * 2))
                    self.until = now + max(self.back, min(60.0, float(retry_after or 0.0)))
                    fresh = True
                self.cap = 1
                self.epoch += 1
            elif status is not None and status < 500 and epoch == self.epoch:
                if self.back and self.until <= now:
                    self.back = self.back / 2 if self.back > 1.0 else 0.0
                self.cap = min(self.most, self.cap + 1)
            self._cond.notify_all()
            return max(0.0, self.until - now), fresh


_GATES: dict = {}
_GATES_LOCK = threading.Lock()


def _gate_for(host: str, settings: dict) -> _HostGate | None:
    """The process-wide gate of ``host`` (None: an ordinary host, one request after another)."""
    from core.http_rate_limiter import parallel_for
    most = parallel_for(host, settings)
    if most <= 1:
        return None
    with _GATES_LOCK:
        gate = _GATES.get(host)
        if gate is None or gate.most != most:
            gate = _GATES[host] = _HostGate(most)
        return gate


class HttpClient:
    def __init__(self, settings: dict, *, log: Callable[[str], None] | None = None,
                 cancel_check: Callable[[], bool] | None = None,
                 pause_wait: Callable[[], None] | None = None):
        self.settings = settings
        self.log = log or (lambda _m: None)
        # cancel_check must return at once: the shared throttle calls it while holding
        # its process-wide lock.  Waiting while paused happens in pause_wait, outside it.
        self.cancel_check = cancel_check or (lambda: False)
        self.pause_wait = pause_wait or (lambda: None)
        try:
            read_timeout = float(settings.get("request_timeout_seconds", 30) or 30)
        except (TypeError, ValueError):
            read_timeout = 30.0
        self.timeout = (10.0, max(5.0, min(300.0, read_timeout)))
        # The throttle reads host intervals and the SauceNAO quota governor from
        # the full settings; it also needs our (non-blocking) cancel hook while it sleeps.
        self._limiter_settings = dict(settings)
        self._limiter_settings["_cancel_callback"] = self.cancel_check
        self._local = threading.local()
        self._registry_lock = threading.Lock()
        self._sessions: list[requests.Session] = []
        self._cookie_logged: set[str] = set()
        self._cookie_round: dict[str, int] = {}     # host -> how many times its cookies were renewed in this run

    # ── sessions ────────────────────────────────────────────────────────────
    def session(self, host: str) -> requests.Session:
        cache = getattr(self._local, "sessions", None)
        if cache is None:
            cache = {}
            self._local.sessions = cache
        wanted = self._cookie_round.get(host, 0)
        sess, loaded = cache.get(host, (None, 0))
        if sess is None:
            sess = requests.Session()
            sess.headers.update({
                "User-Agent": BROWSER_UA,
                "Accept": "application/json,text/html,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            })
            self._load_cookies(sess, host)
            cache[host] = (sess, wanted)
            with self._registry_lock:
                self._sessions.append(sess)
        elif loaded != wanted:                   # the person renewed this site's cookies: this thread takes them now
            sess.cookies.clear()
            sess.headers["User-Agent"] = BROWSER_UA
            self._load_cookies(sess, host)
            cache[host] = (sess, wanted)
        return sess

    def renew_cookies(self, host: str) -> None:
        """The person renewed the cookies of ``host`` (in a browser, Companion wrote them): every thread's session of
        that host loads them again before its next request (page «Парсер», «Проверить снова»)."""
        with self._registry_lock:
            self._cookie_round[host] = self._cookie_round.get(host, 0) + 1
            self._cookie_logged.discard(host)

    def _load_cookies(self, sess: requests.Session, host: str) -> None:
        from core.tagger.cookies_io import (
            _normalize_cookie_records, load_cookie_bundle_for_host, load_txt_cookiejar_for_host,
        )
        added = 0
        cookies, user_agent = load_cookie_bundle_for_host(host)
        if cookies:
            # Cloudflare clearance cookies are bound to the browser that got them.
            if user_agent:
                sess.headers["User-Agent"] = user_agent
            for c in _normalize_cookie_records(cookies):
                added += _set_cookie(sess, c.get("name"), c.get("value"), c.get("domain"), c.get("path") or "/")
        jar, _info = load_txt_cookiejar_for_host(host)
        for c in jar or []:
            added += _set_cookie(sess, getattr(c, "name", None), getattr(c, "value", None),
                                 getattr(c, "domain", None), getattr(c, "path", "/") or "/")
        if added and host not in self._cookie_logged:
            self._cookie_logged.add(host)
            self.log(f"COOKIES [{host}]: загружено {added}")

    def close_all(self) -> None:
        with self._registry_lock:
            sessions, self._sessions = self._sessions, []
        for sess in sessions:
            try:
                sess.close()
            except Exception as exc:  # closing must never break shutdown
                self.log(f"HTTP close warning: {exc}")

    # ── requests ────────────────────────────────────────────────────────────
    def check_cancel(self) -> None:
        """Hold here while the run is paused; raise Cancelled after Stop."""
        self.pause_wait()
        if self.cancel_check():
            raise Cancelled()

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, float(seconds))
        while True:
            self.check_cancel()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(left, 0.2))

    def request(self, method: str, url: str, *, params=None, data=None, json=None, files=None,
                headers=None, auth=None, timeout=None, allow_redirects=True, stream=False,
                attempts: int = 3, session_host: str = "") -> requests.Response:
        """``session_host``: use that host's session (its cookies) — e.g. rule34.xxx for hl.rule34.xxx."""
        from core.http_rate_limiter import RequestCancelled, apply_retry_after, wait_for
        from core.network import assert_url_allowed

        host = host_of(url)
        assert_url_allowed(url)
        attempts = max(1, int(attempts))
        gate = _gate_for(host, self.settings)
        last_error = ""
        attempt = 0
        limited = 0                     # 429 answers of a gated host (they do not use up ``attempts``)
        while attempt < attempts:
            attempt += 1
            self.check_cancel()
            ticket = gate.enter(self.check_cancel) if gate is not None else 0
            try:
                try:
                    wait_for(url, self._limiter_settings)
                except RequestCancelled as exc:
                    raise Cancelled() from exc
                response = self.session(session_host or host).request(
                    method, url, params=params, data=data, json=json, files=files,
                    headers=headers, auth=auth, timeout=timeout or self.timeout,
                    allow_redirects=allow_redirects, stream=stream,
                )
            except requests.RequestException as exc:
                if gate is not None:
                    gate.leave(ticket, None)
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < attempts:
                    self.log(f"  СЕТЬ [{host}]: {type(exc).__name__}, повтор {attempt}/{attempts - 1}")
                    self.sleep(min(10.0, 2.0 * attempt))
                    continue
                raise HttpFailure(host, last_error) from exc
            except BaseException:
                if gate is not None:
                    gate.leave(ticket, None)
                raise
            status = response.status_code
            if gate is not None:
                pause, fresh = gate.leave(ticket, status, _retry_after_seconds(response, 0.0) if status == 429 else 0.0)
                if status == 429:
                    limited += 1
                    if limited < _GATE_TRIES:
                        if fresh:
                            self.log(f"  ЛИМИТ [{host}]: HTTP 429 — пауза {pause:.0f} с, потом по одному запросу "
                                     f"и снова быстрее")
                        response.close()
                        attempt -= 1        # asked again after the pause (``enter`` waits for it)
                        continue
                    return response
            if status in (429, 503) and attempt < attempts:
                wait = _retry_after_seconds(response, default=20.0)
                if wait <= 90:
                    self.log(f"  ЛИМИТ [{host}]: HTTP {status}, жду {wait:.0f} с")
                    apply_retry_after(response, self._limiter_settings)
                    continue
                return response
            if status >= 500 and status != 503 and attempt < attempts:
                self.sleep(3.0 * attempt)
                continue
            return response
        raise HttpFailure(host, last_error or "no response")

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def post_file(self, url: str, path: str | Path, *, field: str = "file", data=None, params=None,
                  headers=None, auth=None, timeout=None) -> requests.Response:
        """Multipart upload, exactly one attempt."""
        p = Path(path)
        with p.open("rb") as fh:
            return self.request(
                "POST", url, files={field: (p.name, fh)}, data=data, params=params,
                headers=headers, auth=auth, timeout=timeout or (10.0, max(60.0, self.timeout[1])),
                attempts=1,
            )

    def get_bytes(self, url: str, *, max_bytes: int = 8 * 1024 * 1024, headers=None) -> bytes:
        """Download a small media file (previews for visual checks)."""
        response = self.get(url, stream=True, headers=headers or {"Accept": "image/*,*/*"}, attempts=2)
        try:
            status = response.status_code
            if status >= 400:
                raise HttpFailure(host_of(url), f"HTTP {status}", status=status,
                                  transient=status == 429 or status >= 500)
            ctype = str(response.headers.get("content-type", "")).lower()
            if "text/html" in ctype:
                raise HttpFailure(host_of(url), "HTML instead of media", status=status, transient=False)
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise HttpFailure(host_of(url), "media too large for a preview", transient=False)
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()


def _set_cookie(sess: requests.Session, name, value, domain, path) -> int:
    if not name or value is None:
        return 0
    try:
        str(name).encode("latin-1")
        str(value).encode("latin-1")
    except UnicodeEncodeError:
        return 0
    kwargs = {"path": path or "/"}
    if domain:
        kwargs["domain"] = domain
    sess.cookies.set(str(name), str(value), **kwargs)
    return 1


def _retry_after_seconds(response, default: float) -> float:
    raw = response.headers.get("Retry-After") if response is not None else None
    try:
        return max(1.0, float(raw)) if raw is not None else default
    except (TypeError, ValueError):
        return default
