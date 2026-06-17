from pathlib import Path
import json
import re
import mimetypes
import time
import hashlib
from urllib.parse import urlparse, parse_qs, unquote, quote_plus

import requests
from bs4 import BeautifulSoup

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
    QLineEdit, QPlainTextEdit, QSpinBox, QMessageBox, QDialog, QGridLayout, QScrollArea
)

from ui.login_browser import open_br34
from ui.memory_tools import bounded_append
from core.paths import BROWSER_COOKIES_DIR, ensure_output_base
from core.settings import save_settings
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt
try:
    from PIL import Image
    import imagehash
except Exception:
    Image = None
    imagehash = None


DEFAULT_BLOCKLIST = (
    "obese, obesity, overweight, weight_gain, "
    "inflation, inflation_fetish, expansion, expansion_fetish, "
    "pregnant, pregnancy, mpreg, bloated, belly_inflation, "
    "nipple_expansion, huge_nipples, giant_nipples, "
    "cyst, cysts, cystitis, "
    "ai_generated, ai-assisted, ai_assisted, "
    "scat, coprophagia, poop, feces, "
    "necrophilia, corpse, guro, gore, vomit, fart, farting"
)

# Explicit export list is important because page.py imports helpers with
# `from ui.downloader.helpers import *`. Python normally skips names that
# start with `_`, so private helper functions such as `_session_for_url`
# would not be imported and downloader would crash at runtime.
__all__ = [
    "DEFAULT_BLOCKLIST",
    "_mask_sensitive_url",
    "_host",
    "_load_browser_cookie_json",
    "_session_for_url",
    "_ext_from_url_or_type",
    "_safe_name",
    "_file_md5",
    "_visual_hash",
    "_base_without_copy_suffix",
    "_is_copy_suffix",
    "_media_size_text",
    "_posts_from_xml_response",
    "_tag_list_from_post",
    "_groups_from_post",
    "_clean_download_tag",
    "_dedupe_group_dict",
    "_extract_file_url_from_json",
    "_extract_preview_url_from_json",
    "_post_md5_from_json",
    "_posts_from_json_response",
    "_extract_file_url_from_html",
    "_candidate_api_urls",
    "_apt_auth_query",
    "_tag_search_api",
]




def _mask_sensitive_url(url):
    """Hide credentials from URLs before writing them to logs."""
    text = str(url or "")
    if not text:
        return text
    for key in ("api_key", "login", "user_id", "password", "pass", "token"):
        text = re.sub(rf"([?&]{key}=)[^&\s]+", rf"\1***", text, flags=re.IGNORECASE)
    return text


def _host(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _load_browser_cookie_json(host):
    host = (host or "").lower().replace("www.", "")
    candidates = [host]
    parts = host.split(".")
    if len(parts) >= 2:
        candidates.append(".".join(parts[-2:]))

    cookies = []
    user_agent = ""
    for h in dict.fromkeys(candidates):
        p = BROWSER_COOKIES_DIR / f"{h}.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            cookies += data.get("cookies", []) or []
            user_agent = data.get("user_agent") or user_agent
        except Exception:
            pass

    return cookies, user_agent


def _session_for_url(url, log):
    host = _host(url)
    s = requests.Session()
    cookies, ua = _load_browser_cookie_json(host)

    api_host = host in {"danbooru.donmai.us", "donmai.us", "e621.net", "e926.net"}
    if host in {"danbooru.donmai.us", "donmai.us"}:
        user_agent = "LocalBooru/3.2 (local-user)"
    elif host in {"e621.net", "e926.net"}:
        user_agent = "LocalBooru/3.2 (local archive manager)"
    else:
        user_agent = ua or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        )
    s.headers.update({
        "User-Agent": user_agent,
        "Accept": "application/json" if api_host else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": url,
    })

    added = 0
    for c in cookies:
        try:
            name = c.get("name")
            value = c.get("value")
            domain = c.get("domain") or host
            path = c.get("path") or "/"
            if name and value is not None:
                s.cookies.set(name, value, domain=domain, path=path)
                added += 1
        except Exception:
            pass

    if log:
        log(f"COOKIES [{host}]: loaded {added}")

    try:
        from core.network import install_safe_session
        install_safe_session(s, settings={}, log_func=log)
    except Exception:
        pass
    return s


def _ext_from_url_or_type(url, content_type):
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext and len(ext) <= 8:
        return ext
    ct = (content_type or "").split(";")[0].strip().lower()
    guessed = mimetypes.guess_extension(ct)
    return guessed or ".bin"


def _safe_name(name):
    name = unquote(str(name or "")).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180] or "download"


def _file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _visual_hash(path):
    if Image is None or imagehash is None:
        return ""
    try:
        with Image.open(path) as img:
            return str(imagehash.phash(img.convert("RGB")))
    except Exception:
        return ""


def _base_without_copy_suffix(stem):
    return re.sub(r"\s*\((\d+)\)$", "", str(stem)).strip()


def _is_copy_suffix(stem):
    return bool(re.search(r"\s*\((\d+)\)$", str(stem)))


def _media_size_text(path):
    try:
        size = Path(path).stat().st_size
    except Exception:
        size = 0
    px = ""
    if Image is not None:
        try:
            with Image.open(path) as img:
                px = f"{img.width}x{img.height}"
        except Exception:
            px = ""
    return f"{Path(path).name}\n{size} bytes" + (f"\n{px}" if px else "")


def _posts_from_xml_response(text):
    posts = []
    try:
        soup = BeautifulSoup(text or "", "xml")
        for p in soup.find_all("post"):
            d = dict(p.attrs)
            # BeautifulSoup XML attrs can be lists sometimes
            d = {k: (" ".join(v) if isinstance(v, list) else v) for k, v in d.items()}
            posts.append(d)
    except Exception:
        pass
    return posts


def _tag_list_from_post(post):
    if not isinstance(post, dict):
        return []

    out = []

    if post.get("tag_string"):
        out += str(post.get("tag_string")).split()

    if post.get("tags"):
        if isinstance(post.get("tags"), str):
            out += str(post.get("tags")).split()
        elif isinstance(post.get("tags"), dict):
            for v in post["tags"].values():
                if isinstance(v, list):
                    out += [str(x) for x in v]

    for k in ("tag_string_general", "tag_string_character", "tag_string_copyright", "tag_string_artist", "tag_string_meta"):
        out += str(post.get(k, "") or "").split()

    cleaned = []
    seen = set()
    for raw in out:
        tag = _clean_download_tag(raw)
        if not tag:
            continue
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(tag)
    return cleaned


def _groups_from_post(post):
    groups = {
        "artist": [],
        "contributor": [],
        "character": [],
        "copyright": [],
        "species": [],
        "general": [],
        "meta": [],
        "lore": [],
        "invalid": [],
    }
    if not isinstance(post, dict):
        return groups

    if isinstance(post.get("tags"), dict):
        tags = post.get("tags") or {}
        groups["artist"] = [str(x) for x in tags.get("artist", [])]
        groups["contributor"] = [str(x) for x in tags.get("contributor", [])]
        groups["character"] = [str(x) for x in tags.get("character", [])]
        groups["copyright"] = [str(x) for x in tags.get("copyright", [])]
        groups["species"] = [str(x) for x in tags.get("species", [])]
        groups["general"] = [str(x) for x in tags.get("general", [])]
        groups["meta"] = [str(x) for x in tags.get("meta", [])]
        groups["lore"] = [str(x) for x in tags.get("lore", [])]
        groups["invalid"] = [str(x) for x in tags.get("invalid", [])]
        return _dedupe_group_dict(groups)

    groups["artist"] = str(post.get("tag_string_artist", "") or "").split()
    groups["character"] = str(post.get("tag_string_character", "") or "").split()
    groups["copyright"] = str(post.get("tag_string_copyright", "") or "").split()
    groups["species"] = str(post.get("tag_string_species", "") or "").split()
    groups["general"] = str(post.get("tag_string_general", "") or post.get("tags", "") or "").split()
    groups["meta"] = str(post.get("tag_string_meta", "") or "").split()

    # Gelbooru/rule34 flat tags fallback.
    if not any(groups.values()):
        groups["general"] = _tag_list_from_post(post)

    return _dedupe_group_dict(groups)




BAD_UI_TAGS = {
    "posts", "post", "all", "video", "videos", "image", "images",
    "comments", "comment", "tags", "tag", "wiki", "help", "login",
    "logout", "register", "account", "upload", "uploads", "random",
    "popular", "favorites", "favorite", "search", "next", "previous",
    "prev", "edit", "delete", "report"
}


def _clean_download_tag(tag):
    tag = unquote(str(tag or "")).strip()
    # Visible tag sidebars sometimes append site-wide counts (e.g. horse 231k).
    # Remove those UI counts before normalising spaces; API tag data stays intact.
    tag = re.sub(r"(?:\s+|_)\d+(?:[.,]\d+)?[kmb]?\s*$", "", tag, flags=re.IGNORECASE)
    tag = tag.replace(" ", "_")
    tag = re.sub(r"_+", "_", tag)
    tag = tag.strip("_")
    if not tag:
        return ""
    low = tag.lower()
    if low in BAD_UI_TAGS:
        return ""
    if low.startswith(("rating:", "sort:", "md5:", "id:", "user:", "source:")):
        return ""
    if any(ch in tag for ch in "/?=&#"):
        return ""
    if re.fullmatch(r"[0-9,]+", tag):
        return ""
    return tag


def _dedupe_group_dict(groups):
    out = {"artist": [], "contributor": [], "character": [], "copyright": [], "species": [], "general": [], "meta": [], "lore": [], "invalid": []}
    seen = set()
    for group in ("artist", "contributor", "character", "copyright", "species", "general", "meta", "lore", "invalid"):
        for raw in (groups or {}).get(group, []) or []:
            tag = _clean_download_tag(raw)
            if not tag:
                continue
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            out[group].append(tag)
    return out

def _extract_file_url_from_json(data):
    if isinstance(data, dict) and isinstance(data.get("post"), dict):
        data = data["post"]
    if isinstance(data, dict) and isinstance(data.get("posts"), list) and data["posts"]:
        data = data["posts"][0]
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return ""

    def abs_url(v):
        if not isinstance(v, str) or not v.strip():
            return ""
        v = v.strip()
        if v.startswith("//"):
            return "https:" + v
        if v.startswith(("http://", "https://")):
            return v
        return ""

    # Never use the metadata/source field as a direct media URL.  On e621 it is
    # usually Pixiv/Twitter/etc., not the file to download/open.
    for key in ("file_url", "large_file_url", "sample_url", "sample_file_url", "jpeg_url", "source_file_url", "original_url"):
        u = abs_url(data.get(key))
        if u:
            return u

    # e621/e926 and some Danbooru-compatible APIs keep URLs nested.  v168 only
    # looked at flat file_url, which made e621 appear empty even when the API
    # returned posts.
    for bucket in ("file", "sample", "preview"):
        item = data.get(bucket)
        if isinstance(item, dict):
            for key in ("url", "file_url", "ext_url"):
                u = abs_url(item.get(key))
                if u:
                    return u

    media = data.get("media_asset")
    if isinstance(media, dict):
        variants = media.get("variants") or []
        if isinstance(variants, list):
            for want in ("original", "sample", "720x720", "360x360"):
                for v in variants:
                    if isinstance(v, dict) and v.get("type") == want:
                        u = abs_url(v.get("url"))
                        if u:
                            return u

    return ""




def _extract_preview_url_from_json(data):
    """Return the best lightweight preview URL for online grabber cards.

    Different booru APIs use different shapes:
    - Gelbooru/rule34 DAPI: preview_url / sample_url / file_url
    - Danbooru: preview_file_url / large_file_url / file_url
    - e621: preview.url / sample.url / file.url
    - Danbooru-compatible forks may wrap the post in {post:{...}}.
    """
    if isinstance(data, dict) and isinstance(data.get("post"), dict):
        data = data["post"]
    if isinstance(data, dict) and isinstance(data.get("posts"), list) and data["posts"]:
        data = data["posts"][0]
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return ""

    for key in (
        "preview_url", "preview_file_url", "sample_url", "sample_file_url",
        "large_file_url", "file_url", "jpeg_url",
    ):
        v = data.get(key)
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            return v

    for bucket in ("preview", "sample", "file"):
        item = data.get(bucket)
        if isinstance(item, dict):
            v = item.get("url") or item.get("file_url")
            if isinstance(v, str) and v.startswith(("http://", "https://")):
                return v

    media = data.get("media_asset")
    if isinstance(media, dict):
        variants = media.get("variants") or []
        if isinstance(variants, list):
            for want in ("180x180", "360x360", "720x720", "sample", "original"):
                for v in variants:
                    if isinstance(v, dict) and v.get("type") == want and isinstance(v.get("url"), str):
                        return v["url"]
    return _extract_file_url_from_json(data)


def _post_md5_from_json(data):
    """Extract an exact remote MD5 from common booru JSON shapes.

    Some sites/forks do not expose an explicit ``md5`` field even though the
    CDN/original URL contains the hash as the filename, for example
    ``/data/original/11/24/<md5>.jpg`` or Danbooru/e621-style file URLs.
    The grabber uses this value as the card merge key, so URL fallback is
    required to merge the same exact file returned by multiple sites.
    """
    if isinstance(data, dict) and isinstance(data.get("post"), dict):
        data = data["post"]
    if not isinstance(data, dict):
        return ""

    def valid(v):
        v = str(v or "").strip().lower()
        return v if re.fullmatch(r"[0-9a-f]{32}", v) else ""

    for key in ("md5", "hash", "file_md5", "file_hash"):
        got = valid(data.get(key))
        if got:
            return got

    for bucket in ("file", "media_asset", "image"):
        item = data.get(bucket)
        if isinstance(item, dict):
            for key in ("md5", "hash", "file_md5", "file_hash"):
                got = valid(item.get(key))
                if got:
                    return got

    def md5_from_url(u):
        if not isinstance(u, str) or not u.strip():
            return ""
        try:
            path = unquote(urlparse(u).path or "")
        except Exception:
            path = str(u)
        # Accept a 32-hex filename with any normal media extension, and also
        # URLs where the CDN strips the extension.
        name = Path(path).name.lower()
        stem = Path(name).stem.lower() if "." in name else name
        got = valid(stem)
        if got:
            return got
        m = re.search(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", path.lower())
        return valid(m.group(1)) if m else ""

    url_keys = (
        "file_url", "large_file_url", "sample_url", "sample_file_url",
        "jpeg_url", "source_file_url", "original_url", "preview_url",
        "preview_file_url",
    )
    for key in url_keys:
        got = md5_from_url(data.get(key))
        if got:
            return got

    for bucket in ("file", "sample", "preview"):
        item = data.get(bucket)
        if isinstance(item, dict):
            for key in ("url", "file_url", "ext_url"):
                got = md5_from_url(item.get(key))
                if got:
                    return got

    media = data.get("media_asset")
    if isinstance(media, dict):
        variants = media.get("variants") or []
        if isinstance(variants, list):
            for v in variants:
                if isinstance(v, dict):
                    got = md5_from_url(v.get("url"))
                    if got:
                        return got

    return ""


def _posts_from_json_response(r):
    try:
        data = r.json()
    except Exception:
        # rule34/gelbooru may return XML/HTML even when json=1 fails.
        return _posts_from_xml_response(getattr(r, "text", "") or "")

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    if isinstance(data, dict):
        if isinstance(data.get("post"), list):
            return [x for x in data["post"] if isinstance(x, dict)]
        if isinstance(data.get("post"), dict):
            return [data["post"]]
        if isinstance(data.get("posts"), list):
            return [x for x in data["posts"] if isinstance(x, dict)]
        if data.get("id") or data.get("file_url") or data.get("tags") or data.get("file_url"):
            return [data]

    return []


def _extract_file_url_from_html(html, base_url):
    soup = BeautifulSoup(html or "", "html.parser")
    for sel in [
        "a#image-download-link",
        "a[href*='/data/original/']",
        "a[href*='samples/']",
        "source[src]",
        "video source[src]",
        "img#image",
        "img#post-image",
        "img.image",
        "meta[property='og:image']",
        "meta[name='twitter:image']",
    ]:
        el = soup.select_one(sel)
        if not el:
            continue
        val = el.get("href") or el.get("src") or el.get("content")
        if val:
            return requests.compat.urljoin(base_url, val)

    m = re.search(r'https?://[^"\']+\.(?:jpg|jpeg|png|webp|gif|mp4|webm|mov)(?:\?[^"\']*)?', html or "", re.I)
    if m:
        return m.group(0)
    m = re.search(r'["\']([^"\']+/data/(?:original|sample)/[^"\']+)["\']', html or "", re.I)
    if m:
        return requests.compat.urljoin(base_url, m.group(1))
    return ""


def _candidate_api_urls(post_url):
    u = urlparse(post_url)
    host = u.netloc.lower().replace("www.", "")
    q = parse_qs(u.query)
    out = []

    post_id = (q.get("id") or [""])[0]

    if host in ("rule34.xxx", "api.rule34.xxx") and post_id:
        out += [
            f"https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&id={post_id}",
            f"https://rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&id={post_id}",
        ]
    # rule34.us has no confirmed JSON API; direct post URLs are read as HTML.
    if host == "gelbooru.com" and post_id:
        out += [f"https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1&id={post_id}"]

    m = re.search(r"/posts/(\d+)", u.path)
    if m:
        pid = m.group(1)
        if "allthefallen" in host:
            out += [f"https://{host}/posts/{pid}.json"]
        elif "danbooru" in host or "donmai" in host:
            out += [f"https://danbooru.donmai.us/posts/{pid}.json"]
        elif host in ("e621.net", "e926.net"):
            out += [f"https://{host}/posts/{pid}.json"]

    return out


def _apt_auth_query(settings, host):
    """Use the same login/api_key/user_id saved for APT/Tagger."""
    host = (host or "").lower().replace("www.", "")
    sites = (settings or {}).get("sites", {}) or {}

    aliases = [host]
    if host == "api.rule34.xxx":
        aliases.append("rule34.xxx")

    cfg = {}
    for h in aliases:
        if h in sites:
            cfg = sites.get(h) or {}
            break

    # Custom sites by domain/base_url.
    if not cfg:
        for site in (settings or {}).get("custom_sites", []) or []:
            base = str(site.get("base_url") or site.get("url") or site.get("domain") or "").lower()
            domain = str(site.get("domain") or "").lower()
            if host in base or host == domain.replace("www.", ""):
                cfg = site
                break

    params = []
    for key in ("login", "api_key", "user_id"):
        val = str(cfg.get(key, "") or "").strip()
        if val:
            params.append(f"{key}={quote_plus(val)}")

    return ("&" + "&".join(params)) if params else ""


def _tag_search_api(base_url, tags, page=0, limit=20, settings=None):
    host = _host(base_url)
    tags_q = quote_plus(tags.strip())
    auth = _apt_auth_query(settings or {}, host)

    # Use APT saved auth. api.rule34.xxx requires auth for tag search now.
    if host in ("rule34.xxx", "api.rule34.xxx"):
        return f"https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&tags={tags_q}&pid={page}&limit={limit}{auth}"

    if host == "rule34.us":
        # Search syntax exists in the website, but no verified JSON endpoint.
        # Do not pretend tag-download can paginate an API that is not available.
        return ""

    if host == "gelbooru.com":
        return f"https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1&tags={tags_q}&pid={page}&limit={limit}{auth}"

    if "allthefallen" in host:
        return f"https://{host}/posts.json?tags={tags_q}&page={page + 1}&limit={limit}{auth}"

    if "danbooru" in host or "donmai" in host:
        return f"https://danbooru.donmai.us/posts.json?tags={tags_q}&page={page + 1}&limit={limit}{auth}"

    if host in ("e621.net", "e926.net"):
        return f"https://{host}/posts.json?tags={tags_q}&page={page + 1}&limit={limit}{auth}"

    return ""
