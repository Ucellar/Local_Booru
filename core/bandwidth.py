"""Per-domain bandwidth throttling (inspired by Hydrus ClientNetworkingBandwidth).

Tracks requests per second and per minute per domain.
Applies delays when limits are exceeded.
"""
from __future__ import annotations

import logging
import time
import threading
from collections import defaultdict, deque
from urllib.parse import urlparse

log = logging.getLogger("local_booru.bandwidth")

# Default limits per domain
_LIMITS: dict[str, tuple[int, int]] = {
    # domain: (requests_per_second, requests_per_minute)
    "saucenao.com":          (1,  4),    # 4/min on free tier
    "danbooru.donmai.us":    (2, 20),
    "gelbooru.com":          (2, 30),
    "rule34.xxx":            (2, 30),
    "xbooru.com":            (2, 30),
    "e621.net":              (2, 20),
    "booru.allthefallen.moe":(1, 10),
    "ascii2d.net":           (1,  5),
    "iqdb.org":              (1, 10),
    "__default__":           (3, 60),
}


class BandwidthTracker:
    """Thread-safe per-domain request rate tracker."""

    def __init__(self):
        self._lock = threading.Lock()
        # domain → deque of timestamps
        self._per_second: dict[str, deque] = defaultdict(deque)
        self._per_minute: dict[str, deque] = defaultdict(deque)

    def _get_limits(self, domain: str) -> tuple[int, int]:
        d = domain.lower().replace("www.", "")
        for key, limits in _LIMITS.items():
            if key in d:
                return limits
        return _LIMITS["__default__"]

    def wait_if_needed(self, url_or_domain: str) -> None:
        """Block until it's safe to make a request to this domain."""
        domain = urlparse(url_or_domain).netloc or url_or_domain
        domain = domain.lower().replace("www.", "")
        rps, rpm = self._get_limits(domain)

        while True:
            now = time.time()
            with self._lock:
                # Clean old entries
                sec_q = self._per_second[domain]
                min_q = self._per_minute[domain]
                while sec_q and now - sec_q[0] > 1.0:
                    sec_q.popleft()
                while min_q and now - min_q[0] > 60.0:
                    min_q.popleft()

                # Check limits
                if len(sec_q) < rps and len(min_q) < rpm:
                    sec_q.append(now)
                    min_q.append(now)
                    return  # OK to proceed

                # Calculate wait time
                wait = 0.0
                if len(sec_q) >= rps and sec_q:
                    wait = max(wait, 1.0 - (now - sec_q[0]))
                if len(min_q) >= rpm and min_q:
                    wait = max(wait, 60.0 - (now - min_q[0]))

            if wait > 0:
                log.debug("Bandwidth: waiting %.2fs for %s", wait, domain)
                time.sleep(min(wait + 0.1, 5.0))

    def record(self, url_or_domain: str) -> None:
        """Record a request (call after wait_if_needed if not using it)."""
        domain = urlparse(url_or_domain).netloc or url_or_domain
        now = time.time()
        with self._lock:
            self._per_second[domain].append(now)
            self._per_minute[domain].append(now)


# Global singleton
_tracker = BandwidthTracker()


def wait_for_domain(url: str) -> None:
    """Global call before any HTTP request."""
    _tracker.wait_if_needed(url)


def get_tracker() -> BandwidthTracker:
    return _tracker
