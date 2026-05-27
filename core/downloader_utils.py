"""Shared download utilities used by both Grabber and Subscriptions."""
from __future__ import annotations

import time
from urllib.parse import urlparse

try:
    from curl_cffi import requests
    _CURL = True
except ImportError:
    import requests
    _CURL = False

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/136.0.0.0 Safari/537.36")

_CF_HOSTS = {
    "danbooru.donmai.us", "booru.allthefallen.moe",
    "rule34.xxx", "rule34.us",
}


def _session(host: str):
    h = host.lower().replace("www.", "")
    if _CURL and any(h.endswith(cf) for cf in _CF_HOSTS):
        try:
            s = requests.Session(impersonate="chrome120")
        except Exception:
            s = __import__("requests").Session()
    else:
        s = __import__("requests").Session()
    s.headers["User-Agent"] = _UA
    s.headers["Accept"] = "application/json, */*"
    return s


def _posts_api_url(site: str, query: str, page: int) -> tuple[str, dict]:
    """Return (api_url, params) for the site."""
    host = site.lower().replace("www.", "").replace("https://", "").replace("http://", "")
    base = f"https://{host}"

    # Gelbooru-style
    if any(h in host for h in ["gelbooru", "rule34.xxx", "xbooru", "realbooru", "tbib", "safebooru"]):
        return f"{base}/index.php", {
            "page": "dapi", "s": "post", "q": "index",
            "json": "1", "tags": query, "limit": 20, "pid": page,
        }

    # Danbooru-style
    if any(h in host for h in ["danbooru", "allthefallen", "lolibooru", "hypnohub", "aibooru"]):
        return f"{base}/posts.json", {
            "tags": query, "limit": 20, "page": page + 1,
        }

    # Moebooru-style
    if any(h in host for h in ["konachan", "yande.re", "rule34.us"]):
        return f"{base}/post/index.json", {
            "tags": query, "limit": 20, "page": page + 1,
        }

    # e621
    if "e621" in host or "e926" in host:
        return f"{base}/posts.json", {
            "tags": query, "limit": 20, "page": page + 1,
        }

    # Generic fallback
    return f"{base}/posts.json", {"tags": query, "limit": 20, "page": page + 1}


def _extract_posts(data) -> list[dict]:
    """Normalize API response to list of post dicts."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("post", "posts", "data"):
            v = data.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                return [v]
    return []


def _post_id(post: dict) -> int:
    try:
        return int(post.get("id") or post.get("post_id") or 0)
    except Exception:
        return 0


def _file_url(post: dict) -> str:
    for key in ("file_url", "large_file_url", "download_url", "source", "image"):
        v = post.get(key)
        if v and isinstance(v, str) and v.startswith("http"):
            return v
    # e621 nested
    f = post.get("file", {})
    if isinstance(f, dict):
        return f.get("url", "")
    return ""


def download_posts_for_query(
    site: str,
    query: str,
    settings: dict,
    since_post_id: int = 0,
    max_pages: int = 3,
    log=None,
    progress=None,
) -> tuple[int, int]:
    """Download new posts matching query on site.

    Returns (downloaded_count, highest_post_id_seen).
    Only downloads posts with ID > since_post_id.
    """
    log = log or (lambda m: None)
    host = urlparse(f"https://{site}").netloc.lower()
    s = _session(host)

    # Load cookies if available
    try:
        from core.tagger.engine import load_cookie_bundle_for_host
        cookies, _ = load_cookie_bundle_for_host(host)
        if cookies:
            for c in cookies:
                try:
                    name = c.name if hasattr(c, "name") else c.get("name", "")
                    value = c.value if hasattr(c, "value") else c.get("value", "")
                    if name and value:
                        s.cookies.set(name, value, domain=host)
                except Exception:
                    pass
    except Exception:
        pass

    from pathlib import Path
    import hashlib, os, mimetypes

    out_dir = Path(settings.get("output_dir", "")) / "subscriptions" / _safe(site) / _safe(query)
    out_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    highest_id = since_post_id
    stop = False

    for page in range(max_pages):
        if stop:
            break

        api_url, params = _posts_api_url(site, query, page)
        log(f"  SUB PAGE {page+1}: {api_url} {params}")

        try:
            r = s.get(api_url, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, str):
                log(f"  SUB: HTML response (Cloudflare?), stopping")
                break
            posts = _extract_posts(data)
        except Exception as e:
            log(f"  SUB PAGE ERROR: {e}")
            break

        if not posts:
            log(f"  SUB: no more posts at page {page+1}")
            break

        for post in posts:
            pid = _post_id(post)
            if pid > highest_id:
                highest_id = pid

            # Skip already downloaded
            if since_post_id > 0 and pid <= since_post_id:
                stop = True  # posts are sorted by ID desc, can stop
                break

            file_url = _file_url(post)
            if not file_url:
                continue

            # Download the file
            fname = _safe(Path(urlparse(file_url).path).name) or f"{pid}.jpg"
            dest = out_dir / fname
            if dest.exists():
                continue

            try:
                fr = s.get(file_url, timeout=60)
                fr.raise_for_status()
                dest.write_bytes(fr.content)
                downloaded += 1
                log(f"  SUB DL: {fname}")
                if progress:
                    progress(downloaded)
                time.sleep(0.5)
            except Exception as e:
                log(f"  SUB DL ERROR {fname}: {e}")

        time.sleep(1)

    return downloaded, highest_id


def _safe(s: str) -> str:
    """Make string safe for filesystem."""
    import re
    return re.sub(r'[<>:"/\\|?*]', '_', str(s))[:60]
