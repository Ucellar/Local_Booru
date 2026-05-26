"""Reactor scraper — fapreactor.com / joyreactor.com / reactor.cc

Supports:
- Download posts by tag (grabber mode)
- Extract tags from post pages
- Pagination via ?offset=N

Sites all use same Reactor engine. Tag pages:
  https://fapreactor.com/tag/TAG_NAME
  https://joyreactor.com/tag/TAG_NAME
  
Post page:
  https://fapreactor.com/post/POST_ID
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin, urlparse, quote

try:
    from curl_cffi import requests
    _CURL = True
except ImportError:
    import requests
    _CURL = False

from bs4 import BeautifulSoup

REACTOR_SITES = {
    "fapreactor.com":  "https://fapreactor.com",
    "joyreactor.com":  "https://joyreactor.com",
    "reactor.cc":      "https://reactor.cc",
    "pornreactor.cc":  "https://pornreactor.cc",
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def _session(site: str):
    if _CURL:
        s = requests.Session(impersonate="chrome120")
    else:
        s = requests.Session()
    s.headers.update({
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "Accept-Language": "ru,en;q=0.8",
        "Referer": f"https://{site}/",
    })
    return s


# ── Tag extraction ────────────────────────────────────────────────────────────

def extract_tags_from_post(html: str, site: str = "fapreactor.com") -> dict[str, list[str]]:
    """Parse tags from a Reactor post page.
    
    Returns dict like {"general": [...], "character": [...], ...}
    Reactor doesn't have category system so all go to "general".
    """
    soup = BeautifulSoup(html, "html.parser")
    tags: list[str] = []
    
    # Tags in <a class="tag">
    for el in soup.select("a.tag"):
        t = el.get_text(strip=True).lower().replace(" ", "_")
        if t and len(t) > 1:
            tags.append(t)
    
    # Fallback: tags in .taglist, .post-tags, [data-tags]
    if not tags:
        for el in soup.select(".taglist a, .post-tags a, .tags a"):
            t = el.get_text(strip=True).lower().replace(" ", "_")
            if t and len(t) > 1:
                tags.append(t)
    
    # Creator/source as artist tag
    artist_tags: list[str] = []
    for el in soup.select(".post_content .username, .creator a, .post-creator a"):
        name = el.get_text(strip=True).lower().replace(" ", "_")
        if name:
            artist_tags.append(name)
    
    return {
        "general": list(dict.fromkeys(tags)),
        "artist":  list(dict.fromkeys(artist_tags)),
    }


def tags_from_post_url(url: str, log=None) -> dict[str, list[str]]:
    """Fetch post page and return tags."""
    host = urlparse(url).netloc.lower().replace("www.", "")
    s = _session(host)
    try:
        r = s.get(url, timeout=20)
        r.raise_for_status()
        tags = extract_tags_from_post(r.text, host)
        if log:
            total = sum(len(v) for v in tags.values())
            log(f"  REACTOR TAGS [{host}]: {total} tags from {url}")
        return tags
    except Exception as e:
        if log:
            log(f"  REACTOR TAG ERROR: {e}")
        return {}


# ── Post listing ──────────────────────────────────────────────────────────────

def _extract_posts_from_page(html: str, base_url: str) -> list[dict]:
    """Extract post data from a tag/page listing."""
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    
    for article in soup.select("article.post, .post, .postContainer"):
        post: dict = {}
        
        # Post ID
        pid = article.get("id", "") or article.get("data-postid", "")
        if pid:
            post["id"] = re.sub(r"\D", "", str(pid))
        
        # Image URL
        img = (
            article.select_one("img.image") or
            article.select_one(".post_content img") or
            article.select_one("img[src*='/images/']") or
            article.select_one("img")
        )
        if img:
            src = img.get("src") or img.get("data-src") or ""
            if src.startswith("//"):
                src = "https:" + src
            if src:
                post["image_url"] = src
        
        # Full-size link
        for a in article.select("a[href]"):
            href = a.get("href", "")
            if re.search(r"\.(jpg|jpeg|png|gif|webp)(\?|$)", href, re.I):
                if href.startswith("//"):
                    href = "https:" + href
                post["image_url"] = href
                break
        
        # Post URL
        post_link = article.select_one("a.link[href*='/post/']") or article.select_one("a[href*='/post/']")
        if post_link:
            href = post_link.get("href", "")
            if href:
                post["post_url"] = urljoin(base_url, href)
        
        # Tags inline
        tags = []
        for t in article.select("a.tag"):
            tag = t.get_text(strip=True).lower().replace(" ", "_")
            if tag:
                tags.append(tag)
        if tags:
            post["tags"] = tags
        
        if post.get("image_url"):
            posts.append(post)
    
    return posts


def iter_tag_posts(
    site: str,
    tag: str,
    max_posts: int = 100,
    log=None,
) -> Iterator[dict]:
    """Iterate posts for a tag on a Reactor site.
    
    Yields dicts with: image_url, post_url, tags, id
    """
    base = REACTOR_SITES.get(site, f"https://{site}")
    tag_encoded = quote(tag.replace("_", "+"), safe="")
    s = _session(site)
    
    fetched = 0
    page = 0
    
    while fetched < max_posts:
        # Reactor pagination: /tag/TAG or /tag/TAG?offset=N (multiples of 20)
        offset = page * 20
        if offset == 0:
            url = f"{base}/tag/{tag_encoded}"
        else:
            url = f"{base}/tag/{tag_encoded}?offset={offset}"
        
        if log:
            log(f"  REACTOR [{site}] page {page+1}: {url}")
        
        try:
            r = s.get(url, timeout=20)
            r.raise_for_status()
        except Exception as e:
            if log:
                log(f"  REACTOR ERROR: {e}")
            break
        
        posts = _extract_posts_from_page(r.text, base)
        if not posts:
            if log:
                log(f"  REACTOR: no more posts at page {page+1}")
            break
        
        for p in posts:
            if fetched >= max_posts:
                break
            yield p
            fetched += 1
            time.sleep(0.3)  # polite delay
        
        page += 1
        time.sleep(1.0)
