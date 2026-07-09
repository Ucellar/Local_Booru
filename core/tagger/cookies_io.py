from __future__ import annotations

import json
from http.cookiejar import MozillaCookieJar
from urllib.parse import urlparse

try:
    import browser_cookie3
except Exception:  # pragma: no cover - optional dependency
    browser_cookie3 = None

from core.paths import BROWSER_COOKIES_DIR


def cookie_file_for_url(url):
    host = urlparse(url).netloc.lower().replace("www.", "")
    if not host:
        host = "default"
    safe = host.replace(":", "_").replace("/", "_")
    return BROWSER_COOKIES_DIR / f"{safe}.json"


def load_netscape_cookie_file(path):
    jar = MozillaCookieJar()
    try:
        jar.load(str(path), ignore_discard=True, ignore_expires=True)
        return jar
    except Exception:
        return None


def load_txt_cookiejar_for_host(host):
    try:
        host = (host or "").lower().replace("www.", "")
        if not host:
            return None, "empty host"
        txt_path = BROWSER_COOKIES_DIR / f"{host}.txt"
        if not txt_path.exists():
            return None, "txt not found"
        jar = load_netscape_cookie_file(txt_path)
        if not jar:
            return None, "failed parse"
        return jar, f"txt:{txt_path.name}"
    except Exception as e:
        return None, str(e)


def _normalize_cookie_records(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        if isinstance(raw.get("cookies"), list):
            return [c for c in raw.get("cookies", []) if isinstance(c, dict)]
        out = []
        for name, value in raw.items():
            if name in ("user_agent", "headers", "meta"):
                continue
            if isinstance(value, dict):
                c = dict(value)
                c.setdefault("name", name)
                out.append(c)
            elif isinstance(value, (str, int, float, bool)):
                out.append({"name": str(name), "value": str(value), "domain": "", "path": "/"})
        return out
    return []


def load_cookie_bundle_for_host(host):
    host = (host or "").lower().replace("www.", "")
    path = BROWSER_COOKIES_DIR / f"{host}.json"
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return _normalize_cookie_records(data), data.get("user_agent")
        return _normalize_cookie_records(data), None
    except Exception:
        return [], None


def load_system_cookiejar_for_host(host):
    if browser_cookie3 is None:
        return None, "browser-cookie3 is not installed"

    host = (host or "").lower().replace("www.", "")
    domains = [host]
    if host.startswith("booru."):
        domains.append(host.replace("booru.", "", 1))

    loaders = [
        ("edge", getattr(browser_cookie3, "edge", None)),
        ("chrome", getattr(browser_cookie3, "chrome", None)),
        ("firefox", getattr(browser_cookie3, "firefox", None)),
        ("chromium", getattr(browser_cookie3, "chromium", None)),
    ]

    last_error = None
    for browser_name, loader in loaders:
        if loader is None:
            continue
        for domain in domains:
            try:
                jar = loader(domain_name=domain)
                if jar:
                    return jar, browser_name
            except Exception as e:
                last_error = e
    return None, str(last_error) if last_error else "no cookies found"
