"""FlareSolverr client for Local Booru.

FlareSolverr is a local proxy server that uses real Chrome to bypass
Cloudflare JS challenges. It runs on http://localhost:8191 by default.

Install:
  - Windows: download from https://github.com/FlareSolverr/FlareSolverr/releases
    Extract, run flaresolverr.exe
  - Docker: docker run -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest

Usage in Local Booru:
  - Enable in Settings → FlareSolverr URL
  - Used automatically for ascii2d.net and other CF-protected sites
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("local_booru.flaresolverr")

DEFAULT_URL = "http://localhost:8191"
_TIMEOUT = 60  # FlareSolverr needs time for JS challenge


class FlareSolverrError(Exception):
    pass


class FlareSolverrClient:
    """Simple FlareSolverr v1 API client."""

    def __init__(self, base_url: str = DEFAULT_URL):
        self.base_url = base_url.rstrip("/")
        self._session_id: str | None = None

    def _post(self, payload: dict) -> dict:
        import requests as _req
        try:
            r = _req.post(
                f"{self.base_url}/v1",
                json=payload,
                timeout=_TIMEOUT + 5,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "ok":
                raise FlareSolverrError(f"FlareSolverr error: {data.get('message', data)}")
            return data
        except _req.exceptions.ConnectionError:
            raise FlareSolverrError(
                f"Cannot connect to FlareSolverr at {self.base_url}. "
                "Make sure it's running (flaresolverr.exe or Docker)."
            )

    def is_running(self) -> bool:
        """Check if FlareSolverr is running."""
        try:
            import requests as _req
            r = _req.get(f"{self.base_url}/", timeout=3)
            return r.status_code in (200, 405)
        except Exception:
            return False

    def get(self, url: str, cookies: list | None = None) -> dict:
        """GET request through FlareSolverr Chrome.

        Returns solution dict with:
          - response: page HTML
          - cookies: list of {name, value, domain, ...}
          - userAgent: Chrome UA string
          - status: HTTP status code
        """
        payload: dict = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": _TIMEOUT * 1000,
        }
        if cookies:
            payload["cookies"] = cookies

        data = self._post(payload)
        return data.get("solution", {})

    def get_cookies_for(self, url: str) -> tuple[list[dict], str]:
        """Get CF-bypass cookies for a URL.

        Returns (cookies_list, user_agent).
        cookies_list: [{name, value, domain, path, ...}, ...]
        """
        sol = self.get(url)
        cookies = sol.get("cookies", [])
        ua = sol.get("userAgent", "")
        log.info("FlareSolverr got %d cookies for %s", len(cookies), url)
        return cookies, ua

    def session_create(self) -> str:
        """Create a persistent Chrome session (keeps cookies between requests)."""
        data = self._post({"cmd": "sessions.create"})
        self._session_id = data.get("session", "")
        return self._session_id

    def session_destroy(self) -> None:
        if self._session_id:
            try:
                self._post({"cmd": "sessions.destroy", "session": self._session_id})
            except Exception:
                pass
            self._session_id = None


# ── Integration helpers ───────────────────────────────────────────────────────

def make_cffi_session_with_flaresolverr(
    fs_url: str,
    target_url: str,
    log_func=None,
) -> tuple[Any, bool]:
    """
    Use FlareSolverr to get CF cookies, then build a curl_cffi session
    with those cookies pre-loaded.

    Returns (session, success: bool).
    """
    log_func = log_func or log.info

    client = FlareSolverrClient(fs_url)

    if not client.is_running():
        log_func(f"  FlareSolverr: not running at {fs_url}")
        return None, False

    try:
        log_func(f"  FlareSolverr: solving CF for {target_url}...")
        cookies, ua = client.get_cookies_for(target_url)

        # Find cf_clearance
        cf = next((c for c in cookies if c.get("name") == "cf_clearance"), None)
        if cf:
            log_func(f"  FlareSolverr: got cf_clearance! Building bypass session...")
        else:
            log_func(f"  FlareSolverr: no cf_clearance in cookies, may still work")

        # Build curl_cffi session with these cookies
        try:
            from curl_cffi import requests as cffi_req
            s = cffi_req.Session(impersonate="chrome120")
        except ImportError:
            import requests as s_mod
            s = s_mod.Session()

        # Set cookies
        from urllib.parse import urlparse
        domain = urlparse(target_url).netloc
        for c in cookies:
            try:
                s.cookies.set(
                    c.get("name", ""),
                    c.get("value", ""),
                    domain=c.get("domain", domain),
                )
            except Exception:
                pass

        # Use FlareSolverr's UA (matches the Chrome that solved the challenge)
        s.headers.update({
            "User-Agent": ua or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": target_url,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        })

        return s, True

    except FlareSolverrError as e:
        log_func(f"  FlareSolverr ERROR: {e}")
        return None, False
    except Exception as e:
        log_func(f"  FlareSolverr unexpected error: {e}")
        return None, False


def flaresolverr_url_from_settings(settings: dict) -> str | None:
    """Get FlareSolverr URL from settings, or None if disabled."""
    url = settings.get("flaresolverr_url", "").strip()
    return url if url else None
