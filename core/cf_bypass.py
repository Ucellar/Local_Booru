"""Automatic Cloudflare cf_clearance cookie scraper.

Methods (in priority order):
  1. DrissionPage / cf-clearance-scraper — real Chrome, silent, reliable
  2. patchright — undetected playwright fork
  3. playwright — standard headless browser

Usage:
    from core.cf_bypass import get_cf_clearance, make_cf_session
    
    cookies = get_cf_clearance("https://danbooru.donmai.us")
    if cookies:
        session = make_cf_session(cookies, "https://danbooru.donmai.us")
        r = session.get("https://danbooru.donmai.us/posts.json?tags=md5:abc123&limit=1")

Install:
    pip install DrissionPage          # method 1 (recommended)
    pip install patchright && python -m patchright install chromium  # method 2
    pip install playwright && playwright install chromium              # method 3
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("local_booru.cf_bypass")

# Cache: domain → (cookies_list, user_agent, timestamp)
_CACHE: dict[str, tuple[list, str, float]] = {}
_CACHE_TTL = 25 * 60  # 25 minutes (cf_clearance lasts ~30 min)


def _cached(domain: str) -> tuple[list, str] | None:
    """Return cached (cookies, ua) if still valid."""
    if domain in _CACHE:
        cookies, ua, ts = _CACHE[domain]
        if time.time() - ts < _CACHE_TTL:
            log.debug("CF cache hit for %s (%.0fm remaining)",
                      domain, (_CACHE_TTL - (time.time() - ts)) / 60)
            return cookies, ua
        del _CACHE[domain]
    return None


def _store_cache(domain: str, cookies: list, ua: str) -> None:
    _CACHE[domain] = (cookies, ua, time.time())
    log.info("CF bypass: cached cf_clearance for %s", domain)


def _extract_cf_cookies(raw_cookies) -> list[dict]:
    """Normalize cookies from any source to list of {name, value, domain}."""
    result = []
    for c in raw_cookies:
        if hasattr(c, "name"):  # requests.cookies.RequestsCookieJar or similar
            result.append({"name": c.name, "value": c.value,
                           "domain": getattr(c, "domain", "")})
        elif isinstance(c, dict):
            result.append(c)
    return result


# ── Method 1: DrissionPage ────────────────────────────────────────────────────

def _get_via_drission(url: str, log_fn) -> tuple[list, str] | None:
    """Use patchright with system Chrome (off-screen window) - undetectable by CF.
    
    headless=False + window off-screen = real Chrome session, CF cannot detect.
    This is the most reliable approach when system Chrome is installed.
    """
    try:
        from patchright.sync_api import sync_playwright as _pr
        log_fn(f"  CF: patchright+Chrome (off-screen) solving {url}...")

        with _pr() as pw:
            # Use system Chrome - already installed, matches real browser fingerprint
            browser = None
            for kwargs in [
                # Method 1: system Chrome, headed but off-screen (most reliable)
                {"headless": False, "channel": "chrome",
                 "args": ["--no-sandbox", "--window-position=-2000,-2000",
                          "--window-size=1280,800",
                          "--disable-blink-features=AutomationControlled"]},
                # Method 2: system Chrome headless
                {"headless": True, "channel": "chrome",
                 "args": ["--no-sandbox","--disable-blink-features=AutomationControlled"]},
                # Method 3: bundled chromium, headed off-screen
                {"headless": False,
                 "args": ["--no-sandbox", "--window-position=-2000,-2000",
                          "--window-size=1280,800",
                          "--disable-blink-features=AutomationControlled"]},
            ]:
                try:
                    browser = pw.chromium.launch(**kwargs)
                    log_fn(f"  CF: launched Chrome ({kwargs.get('channel','chromium')}, headless={kwargs.get('headless')})")
                    break
                except Exception as le:
                    log_fn(f"  CF: launch failed ({le}), trying next method...")
                    continue

            if not browser:
                log_fn("  CF: could not launch any Chrome browser")
                return None

            ctx = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/142.0.0.0 Safari/537.36",
            )
            page = ctx.new_page()
            try:
                page.goto(url, timeout=30000, wait_until="load")
                
                deadline = time.time() + 45
                while time.time() < deadline:
                    cookies = ctx.cookies()
                    _cf_names = {"cf_clearance", "cf_chl_rc_ni", "cf_chl_3"}

                    cf = next((c for c in cookies if c.get("name","") in _cf_names), None)
                    if cf:
                        ua = page.evaluate("navigator.userAgent") or ""
                        log_fn("  CF: patchright+Chrome got cf_clearance ✓")
                        browser.close()
                        return cookies, str(ua)
                    time.sleep(1.5)

                log_fn("  CF: patchright+Chrome timeout (45s)")
                browser.close()
                return None
            except Exception as e:
                log_fn(f"  CF: page error: {e}")
                try: browser.close()
                except Exception: pass
                return None

    except ImportError:
        log_fn("  CF: patchright not installed → pip install patchright && python -m patchright install chromium")
        return None
    except Exception as e:
        log_fn(f"  CF: patchright+Chrome error: {type(e).__name__}: {e}")
        return None


# ── Method 2: patchright ─────────────────────────────────────────────────────

def _get_via_patchright(url: str, log_fn) -> tuple[list, str] | None:
    """Use patchright (undetected Playwright) to get CF cookies."""
    try:
        from patchright.sync_api import sync_playwright
        log_fn(f"  CF: patchright solving {url}...")
        
        with sync_playwright() as pw:
            # Try system Chrome first (faster), then bundled chromium
            browser = None
            for launch_kwargs in [
                {"headless": True, "channel": "chrome",
                 "args": ["--no-sandbox","--disable-blink-features=AutomationControlled"]},
                {"headless": True,
                 "args": ["--no-sandbox","--disable-blink-features=AutomationControlled"]},
            ]:
                try:
                    browser = pw.chromium.launch(**launch_kwargs)
                    break
                except Exception:
                    continue
            if not browser:
                log_fn("  CF: patchright could not launch browser")
                return None
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            page.goto(url, timeout=30000, wait_until="load")
            
            # Poll for cf_clearance
            deadline = time.time() + 45
            while time.time() < deadline:
                cookies = ctx.cookies()
                _cf_names = {"cf_clearance", "cf_chl_rc_ni", "cf_chl_3"}

                cf = next((c for c in cookies if c.get("name","") in _cf_names), None)
                if cf:
                    ua = page.evaluate("navigator.userAgent")
                    browser.close()
                    log_fn("  CF: patchright got cf_clearance ✓")
                    return cookies, ua or ""
                time.sleep(1.5)
            
            browser.close()
            log_fn("  CF: patchright timeout (45s)")
            return None
    except ImportError:
        log_fn("  CF: patchright not installed → pip install patchright && python -m patchright install chromium")
        return None
    except Exception as e:
        log_fn(f"  CF: patchright error: {e}")
        return None


# ── Method 3: playwright ──────────────────────────────────────────────────────

def _get_via_playwright(url: str, log_fn) -> tuple[list, str] | None:
    """Use regular playwright as last resort."""
    try:
        from playwright.sync_api import sync_playwright
        log_fn(f"  CF: playwright solving {url}...")
        
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context()
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = ctx.new_page()
            page.goto(url, timeout=30000, wait_until="load")
            
            deadline = time.time() + 30
            while time.time() < deadline:
                cookies = ctx.cookies()
                _cf_names = {"cf_clearance", "cf_chl_rc_ni", "cf_chl_3"}

                cf = next((c for c in cookies if c.get("name","") in _cf_names), None)
                if cf:
                    ua = page.evaluate("navigator.userAgent")
                    browser.close()
                    log_fn("  CF: playwright got cf_clearance ✓")
                    return cookies, ua or ""
                time.sleep(1)
            
            browser.close()
            log_fn("  CF: playwright timeout")
            return None
    except ImportError:
        log_fn("  CF: playwright not installed → pip install playwright && playwright install chromium")
        return None
    except Exception as e:
        log_fn(f"  CF: playwright error: {e}")
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def get_cf_clearance(url: str,
                     log_fn=None,
                     force: bool = False) -> tuple[list[dict], str] | None:
    """Get cf_clearance cookies for a URL.
    
    Returns (cookies_list, user_agent) or None on failure.
    Results are cached for 25 minutes.
    
    Args:
        url: target URL (e.g. "https://danbooru.donmai.us")
        log_fn: logging callback
        force: ignore cache and re-solve
    """
    log_fn = log_fn or log.info
    domain = urlparse(url).netloc.lower().replace("www.", "")
    
    if not force:
        cached = _cached(domain)
        if cached:
            log_fn(f"  CF: using cached cookies for {domain}")
            return cached
    
    # Try methods in order
    for method in [_get_via_drission, _get_via_patchright, _get_via_playwright]:
        result = method(url, log_fn)
        if result:
            cookies, ua = result
            cf = next((c for c in cookies
                       if (c.get("name") if isinstance(c, dict) else getattr(c, "name", ""))
                       == "cf_clearance"), None)
            if cf:
                _store_cache(domain, cookies, ua)
                return cookies, ua
            else:
                log_fn(f"  CF: {method.__name__} got cookies but no cf_clearance")
    
    log_fn(f"  CF: all methods failed for {domain}")
    return None


def make_cf_session(cookies: list[dict], url: str, ua: str = ""):
    """Build a requests session pre-loaded with CF cookies.
    
    Returns requests.Session with cf_clearance and matching User-Agent.
    """
    domain = urlparse(url).netloc.lower().replace("www.", "")
    
    try:
        from curl_cffi import requests as cffi_req
        session = cffi_req.Session(impersonate="chrome120")
    except ImportError:
        import requests as req_mod
        session = req_mod.Session()
    
    ua_str = ua or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Safari/537.36"
    )
    session.headers.update({
        "User-Agent": ua_str,
        "Accept": "application/json, text/html, */*;q=0.9",
        "Accept-Language": "en-US,en;q=0.8",
        "Referer": url,
    })
    
    for c in cookies:
        try:
            name  = c.get("name", "")  if isinstance(c, dict) else getattr(c, "name", "")
            value = c.get("value", "") if isinstance(c, dict) else getattr(c, "value", "")
            cdom  = c.get("domain", domain) if isinstance(c, dict) else getattr(c, "domain", domain)
            if name and value:
                session.cookies.set(name, value, domain=cdom.lstrip("."))
        except Exception:
            pass
    
    return session


def save_cookies_to_file(cookies: list[dict], domain: str) -> Path | None:
    """Save CF cookies to browser_cookies directory for persistent use."""
    try:
        from core.paths import BROWSER_COOKIES_DIR
        BROWSER_COOKIES_DIR.mkdir(parents=True, exist_ok=True)
        
        txt_path = BROWSER_COOKIES_DIR / f"{domain}.txt"
        lines = ["# Netscape HTTP Cookie File", "# Auto-generated by Local Booru CF bypass", ""]
        
        for c in cookies:
            name  = c.get("name", "")  if isinstance(c, dict) else getattr(c, "name", "")
            value = c.get("value", "") if isinstance(c, dict) else getattr(c, "value", "")
            cdom  = c.get("domain", f".{domain}") if isinstance(c, dict) else getattr(c, "domain", f".{domain}")
            path  = c.get("path", "/") if isinstance(c, dict) else getattr(c, "path", "/")
            secure = "TRUE" if c.get("secure") else "FALSE"
            # Use far future expiry
            lines.append(f"{cdom}\tTRUE\t{path}\t{secure}\t9999999999\t{name}\t{value}")
        
        txt_path.write_text("\n".join(lines), encoding="utf-8")
        log.info("CF cookies saved to %s", txt_path)
        return txt_path
    except Exception as e:
        log.error("Failed to save CF cookies: %s", e)
        return None
