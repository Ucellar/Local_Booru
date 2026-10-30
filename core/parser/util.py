"""Small pure helpers shared by the parser: hosts, URLs, MD5 values, API bodies."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_ANY_MD5_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])")


def clean_host(value: Any) -> str:
    """'https://www.Example.com:443/x' -> 'example.com'; plain hosts pass through."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        text = urlparse(text).netloc or text
    text = text.split("/", 1)[0].split("@")[-1].split(":", 1)[0]
    if text.startswith("www."):
        text = text[4:]
    return text


def host_of(url: Any) -> str:
    try:
        return clean_host(urlparse(str(url or "")).netloc)
    except ValueError:
        return ""


def is_md5(value: Any) -> bool:
    return bool(MD5_RE.match(str(value or "").strip().lower()))


def norm_md5(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if MD5_RE.match(text) else ""


def md5_from_filename(name: str) -> str:
    """Return an MD5 embedded in a file name (booru downloads keep it), else ''.

    Accepts '<md5>.jpg', '__tags__<md5>.png', 'sample_<md5>.jpg', '<md5>_p0.png'.
    A name with two different 32-hex runs is ambiguous and ignored.
    """
    stem = str(name or "").rsplit(".", 1)[0]
    found = {m.lower() for m in _ANY_MD5_RE.findall(stem)}
    return found.pop() if len(found) == 1 else ""


def unique(items: Iterable[Any]) -> list:
    seen = set()
    out = []
    for item in items:
        if item in seen or item in (None, ""):
            continue
        seen.add(item)
        out.append(item)
    return out


def query_value(url: str, key: str) -> str:
    try:
        return (parse_qs(urlparse(url).query).get(key) or [""])[0]
    except ValueError:
        return ""


def post_id_from_url(url: str) -> str:
    """Extract a numeric post id from danbooru/gelbooru/moebooru/e621/rule34.us post URLs."""
    text = str(url or "")
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    path = parsed.path or ""
    for pattern in (r"/posts?/(\d+)", r"/post/show/(\d+)", r"/post/view/(\d+)"):
        m = re.search(pattern, path)
        if m:
            return m.group(1)
    qs = parse_qs(parsed.query)
    page = (qs.get("page") or [""])[0].lower()
    route = (qs.get("r") or [""])[0].lower()
    if (page == "post" and (qs.get("s") or [""])[0].lower() == "view") or route == "posts/view":
        value = (qs.get("id") or [""])[0]
        if value.isdigit():
            return value
    return ""


def is_post_url(url: str) -> bool:
    """True only for concrete post pages, never for search/list/random pages."""
    low = str(url or "").lower()
    if any(x in low for x in ("/posts/random", "/post/random", "s=list", "r=posts/index", "tags=md5", "search[md5]")):
        return False
    return bool(post_id_from_url(url))


def json_body(response) -> Any:
    """Parsed JSON body or None (never raises, never returns a raw string)."""
    try:
        data = response.json()
    except ValueError:
        data = None
    except Exception:  # some clients raise their own decode errors
        data = None
    if isinstance(data, str):
        raw = data.strip()
        if raw[:1] in "[{":
            try:
                data = json.loads(raw)
            except ValueError:
                return None
        else:
            return None
    if data is None:
        raw = (getattr(response, "text", "") or "").strip()
        if raw[:1] in "[{":
            try:
                data = json.loads(raw)
            except ValueError:
                return None
    return data if isinstance(data, (dict, list)) else None


def failure_envelope(data: Any) -> tuple[str, str]:
    """('auth_required'|'search_failed', reason) for {"success": false, ...} bodies, else ('', '')."""
    if not isinstance(data, dict):
        return "", ""
    raw = data.get("success", None)
    failed = raw is False or raw == 0 or str(raw).strip().lower() in {"false", "failed", "error"}
    if not failed:
        return "", ""
    reason = ""
    for key in ("reason", "message", "error", "errors", "detail"):
        value = data.get(key)
        if value not in (None, "", [], {}):
            reason = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            break
    reason = (reason or "API success=false").strip()
    low = reason.lower()
    if any(t in low for t in ("authentication", "api key", "api_key", "unauthorized", "forbidden", "login required")):
        return "auth_required", reason
    return "search_failed", reason


def xml_elements(text: str, tag: str) -> list[dict]:
    """Attribute dicts of every <tag .../> in an XML API body ([] for HTML/garbage).

    Uses the standard library: lxml (needed by BeautifulSoup's "xml" mode) is
    not installed in the app environment.
    """
    raw = (text or "").strip()
    low = raw[:300].lower()
    if not (low.startswith("<?xml") or low.startswith("<" + tag) or f"<{tag}s" in low):
        return []
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    return [dict(node.attrib) for node in root.iter(tag) if node.attrib]


def posts_from_xml(text: str) -> list[dict]:
    """<post .../> attribute dicts from a DAPI XML body; HTML pages give []."""
    return xml_elements(text, "post")


def post_dicts(data: Any, keys: Iterable[str] = ("posts", "post", "data", "results")) -> list[dict]:
    """Normalize the usual booru JSON shapes to a list of post dicts."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict) and value:
                return [value]
        if "id" in data and ("tags" in data or "tag_string" in data or "md5" in data):
            return [data]
    return []


def looks_like_cloudflare(response) -> bool:
    try:
        status = int(getattr(response, "status_code", 0) or 0)
        ctype = str(response.headers.get("content-type", "")).lower()
        head = (getattr(response, "text", "") or "")[:4000].lower()
    except Exception:
        return False
    if "html" not in ctype and not head.lstrip().startswith("<"):
        return False
    markers = ("cf-browser-verification", "challenge-platform", "cf_chl_", "just a moment", "attention required! | cloudflare")
    blocked = status in (403, 429, 503) and any(m in head for m in markers)
    return blocked or "challenge-platform" in head


def short(text: Any, limit: int = 160) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.I | re.S)
_HIDDEN_RE = re.compile(r"<(script|style|head|noscript)\b.*?</\1\s*>|<!--.*?-->", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]*>")


def page_words(html: Any, limit: int = 160) -> str:
    """What a page says, for the journal: its title and the beginning of its text — not the beginning of its markup
    (``<!DOCTYPE HTML PUBLIC …`` said nothing about why a service answered with another page)."""
    from html import unescape
    text = str(html or "")
    found = _TITLE_RE.search(text)
    title = " ".join(unescape(found.group(1)).split()) if found else ""
    body = " ".join(unescape(_TAG_RE.sub(" ", _HIDDEN_RE.sub(" ", text))).split())
    if title and body.startswith(title):
        body = body[len(title):].strip()
    words = " — ".join(part for part in (title, body) if part)
    return short(words or "пустая страница", limit)
