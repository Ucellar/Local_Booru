import hashlib
import json
import time
import mimetypes
import shutil
import re
import html
import imagehash
from PIL import Image
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urljoin, unquote

try:
    from curl_cffi import requests
    _CURL_CFFI = True
except ImportError:
    import requests
    _CURL_CFFI = False
from http.cookiejar import MozillaCookieJar
from bs4 import BeautifulSoup

try:
    import browser_cookie3
except Exception:
    browser_cookie3 = None

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

from core.paths import SETTINGS_FILE, BROWSER_PROFILE_DIR, BROWSER_COOKIES_DIR, CACHE_DIR, ERROR_LOG_FILE, ensure_output_base
from core.nomatch_db import upsert_nomatch, remove_nomatch
from core.tag_utils import normalize_tag as _shared_normalize_tag, canonical_tag_key
GALLERY_SETTINGS_FILE = SETTINGS_FILE
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS

COPY_SUFFIX_RE = re.compile(r"\s*\((\d+)\)$")


def has_copy_suffix(path):
    try:
        return bool(COPY_SUFFIX_RE.search(Path(path).stem))
    except Exception:
        return False


DEFAULT_SETTINGS = {
    "root": "C:/Local_Booru_Input",
    "output_suffix": ".tags.txt",
    "sources_suffix": ".sources.txt",
    "saucenao_api_key": "",
    "min_similarity": 85.0,
    "delay_seconds": 8.0,
    "request_timeout_seconds": 20,
    "saucenao_cooldown_seconds": 3600,
    "enable_md5_lookup": True,
    "enable_saucenao": True,
    "enable_iqdb": True,
    "iqdb_min_similarity": 75.0,
    "r34_fuzzy_min_similarity": 60.0,
    "strict_atf_md5": True,
    "enable_atf_auto_tags": False,
    "max_preview_cache_files": 1000,
    "preview_cache_max_age_days": 14,
    "skip_existing": True,
    "tag_only_untagged": True,
    "skip_copy_suffix_files": True,
    "retry_nomatch": False,
    "mark_no_match": True,
    "limit_files": 0,
    "use_browser_auth": False,
    "use_system_browser_cookies": False,
    "enable_curl_cffi": False,
    "browser_auth_url": "https://example.com",
    "browser_auth_wait_seconds": 60,
    "sites": {
        "rule34.xxx": {"enabled": True, "type": "rule34xxx", "login": "", "api_key": "", "user_id": "", "login_url": "https://rule34.xxx/index.php?page=account&s=login"},
        "rule34.us": {"enabled": True, "type": "rule34us", "login": "", "api_key": "", "user_id": "", "login_url": "https://rule34.us/index.php?page=account&s=login"},
        "danbooru.donmai.us": {"enabled": True, "type": "danbooru", "login": "", "api_key": "", "user_id": "", "login_url": "https://danbooru.donmai.us/session/new"},
        "gelbooru.com": {"enabled": True, "type": "gelbooru_html", "login": "", "api_key": "", "user_id": "", "login_url": "https://gelbooru.com/index.php?page=account&s=login"},
        "e621.net": {"enabled": True, "type": "e621", "login": "", "api_key": "", "user_id": "", "login_url": "https://e621.net/session/new"},
    },
    "custom_sites": []
}


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            merged = DEFAULT_SETTINGS.copy()
            merged.update(data)
            merged["sites"] = {**DEFAULT_SETTINGS["sites"], **data.get("sites", {})}
            merged["custom_sites"] = data.get("custom_sites", [])
            return merged
        except Exception:
            return DEFAULT_SETTINGS.copy()
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")




def redact_sensitive_url(text):
    """Hide API credentials in log lines and saved debug messages."""
    try:
        value = str(text)
    except Exception:
        return text
    value = re.sub(r'((?:api_key|apikey|key|token|login|user_id|password|pass)=)([^&\s]+)', r'\1***', value, flags=re.I)
    value = re.sub(r'((?:api_key|apikey|key|token|login|user_id|password|pass)%5D=)([^&\s]+)', r'\1***', value, flags=re.I)
    return value


def is_md5(text):
    if len(text) != 32:
        return False
    try:
        int(text, 16)
        return True
    except ValueError:
        return False


def file_md5(path):
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_phash(path):
    try:
        img = Image.open(path).convert("RGB")
        return str(imagehash.phash(img))
    except Exception:
        return ""


def phash_distance(a, b):
    try:
        return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)
    except Exception:
        return 999




def tag_is_numeric_symbol_only(tag):
    return bool(re.match(r"^[\d\W_]+$", str(tag)))



def atf_find_post_view_url_from_html(html_text, base_url="https://booru.allthefallen.moe", md5_hash=""):
    """
    ATF search pages can show the real post card in several forms:
    /posts/123, escaped /posts\\/123, urlencoded %2Fposts%2F123,
    data-id/data-post-id, or JSON blobs.
    Full grouped tags are on /posts/ID?q=md5%3AHASH.
    """
    base_url = (base_url or "https://booru.allthefallen.moe").rstrip("/")
    html_text = html_text or ""

    def build_url(post_id):
        post_id = str(post_id).strip()
        if not post_id or not post_id.isdigit():
            return ""
        url = f"{base_url}/posts/{post_id}"
        if md5_hash:
            url += "?q=md5%3A" + str(md5_hash)
        return url

    try:
        import html as _html_mod
        from urllib.parse import unquote as _url_unquote

        variants = []
        for v in [html_text, _html_mod.unescape(html_text), _url_unquote(html_text)]:
            if v and v not in variants:
                variants.append(v)

        candidates = []

        for variant in variants:
            soup = BeautifulSoup(variant, "html.parser")

            # Direct links.
            for a in soup.find_all("a", href=True):
                href = str(a.get("href", ""))
                m = re.search(r"/posts/(\d+)(?:[/?#][^\"\' <]*)?", href)
                if m:
                    candidates.append(m.group(1))

            # Image/thumb links can carry parent post id in parent blocks.
            for el in soup.find_all(True):
                attrs = getattr(el, "attrs", {}) or {}
                cls = " ".join(attrs.get("class", [])) if isinstance(attrs.get("class"), list) else str(attrs.get("class", ""))
                for key, val in attrs.items():
                    k = str(key).lower()
                    v = str(val)

                    if "post" in k and "id" in k:
                        m = re.search(r"\d{2,}", v)
                        if m:
                            candidates.append(m.group(0))

                    if k in {"data-id", "id"} or "post" in cls.lower():
                        m = re.search(r"(?:post[_-]?)?(\d{2,})", v)
                        if m:
                            candidates.append(m.group(1))

            raw_patterns = [
                r"/posts/(\d+)(?:[/?#][^\"\' <]*)?",
                r"\\/posts\\/(\d+)",
                r"%2Fposts%2F(\d+)",
                r"posts\\?/(\d+)",
                r"post[_-]?id[\"\'\s:=]+(\d{2,})",
                r"data-post-id[\"\'\s:=]+(\d{2,})",
                r"data-id[\"\'\s:=]+(\d{2,})",
                r"id=[\"']post[_-](\d{2,})[\"']",
                r"post\D{0,20}(\d{5,})",
            ]
            for pat in raw_patterns:
                for m in re.finditer(pat, variant, flags=re.I):
                    candidates.append(m.group(1))

        # Preserve order, avoid obviously wrong numbers.
        seen = set()
        for cid in candidates:
            cid = str(cid).strip()
            if not cid.isdigit():
                continue
            if int(cid) < 100:
                continue
            if cid in seen:
                continue
            seen.add(cid)
            return build_url(cid)

    except Exception:
        pass

    return ""



def atf_parse_post_view_html(html_text):
    """
    Parse grouped ATF/Danbooru-style tag sidebar from a single post page.
    Returns (tags, groups).
    """
    groups = {
        "artist": [],
        "character": [],
        "copyright": [],
        "general": [],
        "meta": [],
    }

    try:
        soup = BeautifulSoup(html_text or "", "html.parser")

        category_map = {
            "0": "general",
            "1": "artist",
            "3": "copyright",
            "4": "character",
            "5": "meta",
        }

        # Danbooru-style classes: category-0, category-1...
        for cls_num, group_name in category_map.items():
            for el in soup.select(f".category-{cls_num} a.search-tag, .category-{cls_num} a[href*='tags='], .category-{cls_num} a[href*='/posts?tags=']"):
                tag = el.get_text(" ", strip=True)
                tag = re.sub(r"\s+", " ", tag).strip()
                tag = tag.replace(" ", "_")
                tag = html.unescape(tag)
                if tag and tag not in groups[group_name]:
                    groups[group_name].append(tag)

        # ATF/Danbooru often has tag-list li with category classes.
        for li in soup.find_all(["li", "div"], class_=True):
            cls = " ".join(li.get("class", []))
            m = re.search(r"category-(\d+)", cls)
            if not m:
                continue
            group_name = category_map.get(m.group(1), "general")
            a = li.find("a", class_=re.compile(r"search-tag|tag"))
            if not a:
                # choose last useful link in the row
                links = li.find_all("a")
                a = links[-1] if links else None
            if not a:
                continue
            tag = a.get_text(" ", strip=True)
            tag = re.sub(r"\s+", " ", tag).strip().replace(" ", "_")
            tag = html.unescape(tag)
            if tag and tag not in groups[group_name]:
                groups[group_name].append(tag)

        # Header-based fallback: Artist / Copyrights / Characters / General / Meta
        current = None
        header_map = {
            "artist": "artist",
            "artists": "artist",
            "copyright": "copyright",
            "copyrights": "copyright",
            "character": "character",
            "characters": "character",
            "general": "general",
            "tag": "general",
            "tags": "general",
            "meta": "meta",
            "metadata": "meta",
        }

        for node in soup.find_all(["h3", "h4", "h5", "li", "div", "a"]):
            text = node.get_text(" ", strip=True).lower()
            key = re.sub(r"[^a-z]", "", text)
            if key in header_map:
                current = header_map[key]
                continue

            if current and node.name == "a":
                href = str(node.get("href", ""))
                if "tags=" not in href and "/posts?tags=" not in href:
                    continue
                tag = node.get_text(" ", strip=True)
                tag = re.sub(r"\s+", " ", tag).strip().replace(" ", "_")
                tag = html.unescape(tag)
                if tag and tag not in groups[current] and tag.lower() not in {"?", "posts", "all"}:
                    groups[current].append(tag)

        all_tags = []
        for g in ["artist", "copyright", "character", "general", "meta"]:
            for t in groups[g]:
                if t not in all_tags:
                    all_tags.append(t)

        groups = {k: v for k, v in groups.items() if v}
        return all_tags, groups

    except Exception:
        return [], {}


def filter_numeric_tags(tags, enabled):
    if not enabled:
        return tags
    return [t for t in tags if not tag_is_numeric_symbol_only(t)]


def video_frame_image(path):
    """Extract a searchable jpg frame from videos/gifs. Returns temp jpg path or original path.

    Uses a short md5 filename instead of the original basename so Windows does not
    fail on long paths, Cyrillic names, or punctuation-heavy video names.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    def make_temp_frame_path(src_path):
        import tempfile
        tmp_dir = Path(str((Path.cwd() / "Local_Booru_Output" / "preview_cache"))) / "local_booru_frames"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        safe_name = hashlib.md5(str(src_path).encode("utf-8")).hexdigest()
        return tmp_dir / f"{safe_name}.jpg"

    if suffix == ".gif":
        try:
            img = Image.open(path)
            try:
                frames = getattr(img, "n_frames", 1)
                img.seek(max(0, frames // 2))
            except Exception:
                pass
            tmp = make_temp_frame_path(path)
            img.convert("RGB").save(tmp, "JPEG", quality=95)
            return tmp
        except Exception:
            return path

    if suffix not in VIDEO_EXTS:
        return path

    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frames > 5:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frames // 2))
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return path
        tmp = make_temp_frame_path(path)
        cv2.imwrite(str(tmp), frame)
        return tmp
    except Exception:
        return path

def unique_keep_order(items):
    seen = set()
    out = []
    for x in items:
        x = normalize_tag(x)
        key = canonical_tag_key(x)
        if x and key not in seen:
            seen.add(key)
            out.append(x)
    return out

def empty_tag_groups():
    return {
        "artist": [],
        "character": [],
        "copyright": [],
        "general": [],
        "meta": [],
        "parody": [],
        "language": [],
        "category": [],
        "pages": []
    }


TAG_CATEGORY_MAP = {
    "0": "general", 0: "general", "general": "general",
    "1": "artist", 1: "artist", "artist": "artist",
    "3": "copyright", 3: "copyright", "copyright": "copyright", "series": "copyright",
    "4": "character", 4: "character", "character": "character",
    "5": "meta", 5: "meta", "metadata": "meta", "meta": "meta",
}

def normalize_tag(tag):
    return _shared_normalize_tag(tag)

def group_from_tag_type(value):
    if value is None:
        return "general"
    key = str(value).strip().lower()
    return TAG_CATEGORY_MAP.get(key, "general")

def add_tags_to_groups(groups, group, tags):
    if group not in groups:
        group = "general"
    for tag in tags or []:
        t = normalize_tag(str(tag))
        if t and t not in groups[group]:
            groups[group].append(t)


def merge_tag_groups(groups_list):
    merged = empty_tag_groups()

    for groups in groups_list:
        if not isinstance(groups, dict):
            continue

        for key in merged:
            merged[key] += groups.get(key, [])

    for key in merged:
        merged[key] = unique_keep_order(merged[key])

    return merged


def groups_to_tags(groups):
    tags = []

    for key in ["artist", "character", "copyright", "general", "meta", "parody", "language", "category", "pages"]:
        tags += groups.get(key, [])

    return unique_keep_order(tags)

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
    """Return a safe list of cookie dicts from several saved formats.

    Older/local cookie files can be either:
    - {"cookies": [{"name": ..., "value": ...}], "user_agent": ...}
    - [{"name": ..., "value": ...}]
    - {"cookie_name": "cookie_value"}

    The previous code assumed every item was a dict and crashed with
    "'str' object has no attribute 'get'" when iterating a dict.
    """
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
    """
    Load cookies from the user's real Chrome/Edge/Firefox profiles.
    This avoids Playwright/Cloudflare problems. Browser may need to be closed
    on some systems if the cookie DB is locked.
    """
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


_CF_HOSTS = {
    "danbooru.donmai.us", "donmai.us",
    "booru.allthefallen.moe", "allthefallen.moe",
    "rule34.xxx", "rule34.us",
}

# Import standard requests separately for file uploads
try:
    import requests as _std_requests
except ImportError:
    _std_requests = None

def _make_plain_session(target_host=None):
    """Standard requests session — always works, used for file uploads."""
    import importlib
    std_req = importlib.import_module("requests")
    s = std_req.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Accept": "application/json,text/html,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://{target_host}/" if target_host else "https://danbooru.donmai.us/",
    })
    return s

def get_session(settings=None, log_func=None, target_host=None):
    # curl_cffi was introduced for Cloudflare-like sites, but in real runs it can
    # return/handle internal cookie/header objects differently and trigger
    # legacy code paths that crash with: "'str' object has no attribute 'get'".
    # Stability first: use plain requests by default. Enable curl_cffi only via
    # settings["enable_curl_cffi"] after the whole network layer is audited.
    use_curl_cffi = bool(isinstance(settings, dict) and settings.get("enable_curl_cffi", False))
    needs_cf = bool(use_curl_cffi and target_host and any(str(target_host).endswith(h) for h in _CF_HOSTS))
    
    if _CURL_CFFI and needs_cf:
        try:
            # curl_cffi session — Chrome TLS fingerprint for Cloudflare bypass
            # NOTE: only use for GET requests. File uploads must use _make_plain_session().
            s = requests.Session(impersonate="chrome120")
            if log_func:
                log_func(f"  SESSION: curl_cffi Chrome-impersonation for {target_host}")
            # Wrap get() to ensure JSON is properly parsed
            _orig_get = s.get
            def _safe_get(url, **kw):
                r = _orig_get(url, **kw)
                # Ensure response has working json() like standard requests
                if not hasattr(r, "_json_fixed"):
                    _orig_json = r.json
                    def _json_safe(**jkw):
                        try:
                            return _orig_json(**jkw)
                        except Exception:
                            import json as _json
                            return _json.loads(r.text)
                    r.json = _json_safe
                    r._json_fixed = True
                return r
            s.get = _safe_get
        except Exception:
            s = _make_plain_session(target_host)
    else:
        s = _make_plain_session(target_host)
    
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Accept": "application/json,text/html,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer": f"https://{target_host}/" if target_host else "https://danbooru.donmai.us/",
    })

    def _add_cookie(name, value, domain=None, path="/"):
        try:
            if not name or value is None:
                return False
            name = str(name)
            value = str(value)
            # requests/cookie headers cannot contain non latin-1 characters
            name.encode("latin-1")
            value.encode("latin-1")
            kwargs = {"path": path or "/"}
            if domain:
                kwargs["domain"] = domain
            s.cookies.set(name, value, **kwargs)
            return True
        except Exception:
            return False

    def _add_jar(jar):
        added = 0
        if not jar:
            return 0
        for c in jar:
            try:
                # Support both cookie objects (c.name) and dicts (c["name"])
                if hasattr(c, "name"):
                    name, value, domain, path = c.name, c.value, c.domain, c.path
                elif isinstance(c, dict):
                    name, value, domain, path = c.get("name"), c.get("value"), c.get("domain"), c.get("path", "/")
                else:
                    continue
                if _add_cookie(name, value, domain, path):
                    added += 1
            except Exception:
                pass
        return added

    if settings and target_host and (settings.get("use_browser_auth") or settings.get("use_system_browser_cookies")):
        total_added = 0
        sources = []

        # 1) Cookies saved by embedded Qt WebEngine login browser (.json).
        # Important: do NOT return here. Danbooru/Cloudflare may need cookies from txt/system too.
        cookies, user_agent = load_cookie_bundle_for_host(target_host)
        if cookies:
            if user_agent:
                s.headers.update({"User-Agent": user_agent})

            added = 0
            for c in _normalize_cookie_records(cookies):
                if _add_cookie(c.get("name"), c.get("value"), c.get("domain"), c.get("path") or "/"):
                    added += 1
            total_added += added
            sources.append(f"app-json:{added}")

        # 2) Netscape cookies from data/runtime/browser_cookies/<host>.txt.
        # This is useful for cookies exported from real Chrome/Edge extensions.
        txt_jar, txt_info = load_txt_cookiejar_for_host(target_host)
        if txt_jar:
            added = _add_jar(txt_jar)
            total_added += added
            sources.append(f"{txt_info}:{added}")

        # 3) Optional fallback: real Chrome/Edge/Firefox cookies.
        if settings.get("use_system_browser_cookies"):
            jar, info = load_system_cookiejar_for_host(target_host)
            if jar:
                added = _add_jar(jar)
                total_added += added
                sources.append(f"system-{info}:{added}")
            elif info:
                sources.append(f"system:0({info})")

        if log_func:
            if total_added:
                cookie_names = []
                try:
                    for c in s.cookies:
                        if hasattr(c, "name"):
                            cookie_names.append(str(c.name))
                        elif isinstance(c, dict):
                            cookie_names.append(str(c.get("name", "?")))
                        else:
                            cookie_names.append(str(c))
                except Exception:
                    try:
                        cookie_names = [str(x) for x in s.cookies.keys()]
                    except Exception:
                        cookie_names = []
                names = sorted(set(cookie_names))
                preview = ", ".join(names[:12])
                more = "..." if len(names) > 12 else ""
                log_func(f"COOKIES [{target_host}]: loaded: {total_added} ({'; '.join(sources)}) [{preview}{more}]")
                if target_host == "danbooru.donmai.us" and "cf_clearance" not in names:
                    log_func("  DANBOORU WARNING: cf_clearance missing; Cloudflare 403 is likely. Use external Chrome/Edge export to data/runtime/browser_cookies/danbooru.donmai.us.txt")
            else:
                log_func(f"COOKIES [{target_host}]: 0 ({'; '.join(sources) if sources else 'no sources'})")
                if target_host == "danbooru.donmai.us":
                    log_func("  DANBOORU WARNING: no cookies loaded; Cloudflare/login pages will probably fail.")

    return s


def _post_with_file(session, url, file_path, file_field="file", extra_data=None, extra_params=None, timeout=60):
    """POST a file upload — always uses standard requests (curl_cffi has incompatible API)."""
    import io, importlib
    std_req = importlib.import_module("requests")
    
    # Always use a plain requests session for file uploads
    plain = std_req.Session()
    # Copy cookies and headers from original session
    try:
        plain.cookies.update(session.cookies)
    except Exception:
        pass
    try:
        plain.headers.update(dict(session.headers))
    except Exception:
        pass
    
    file_path_str = str(file_path) if not hasattr(file_path, 'read') else None
    filename = (
        getattr(file_path, 'name', '').split('/')[-1].split('\\')[-1]
        or (file_path_str or '').split('/')[-1].split('\\')[-1]
        or "image.jpg"
    )
    
    with (open(file_path_str, 'rb') if file_path_str else file_path) as f:
        file_bytes = f.read()
    
    return plain.post(
        url,
        files={file_field: (filename, io.BytesIO(file_bytes))},
        data=extra_data or {},
        params=extra_params or {},
        timeout=timeout,
    )


def safe_json_response(r, source="HTTP"):
    """Return parsed JSON as dict/list, never as a raw string.

    Some sessions (especially curl_cffi / Cloudflare paths) may return:
    - a normal dict/list;
    - a JSON string containing HTML/text;
    - compressed/binary-looking text when the server used an unsupported encoding;
    - an HTML verification/login page with status 200.

    Callers expect dict/list and often use .get(), so returning str is unsafe.
    """
    import json as _json

    def _reject_text(text, ct=""):
        snippet = (text or "")[:180].replace("\n", " ").replace("\r", " ")
        raise Exception(
            f"{source} non-json status={getattr(r,'status_code','?')} "
            f"ct={ct} body={snippet!r}"
        )

    ct = ""
    try:
        ct = r.headers.get("content-type", "")
    except Exception:
        pass

    # First try the library's json() method.
    try:
        data = r.json()
    except Exception:
        data = None

    # json() should yield dict/list for our API use. If it yielded a JSON string,
    # parse once more, but still reject if the final value is not dict/list.
    if isinstance(data, str):
        raw = data.strip()
        if raw.startswith(("{", "[")):
            try:
                data = _json.loads(raw)
            except Exception:
                _reject_text(raw, ct)
        else:
            _reject_text(raw, ct)

    if isinstance(data, (dict, list)):
        return data

    # Fallback: parse response text manually.
    text = getattr(r, "text", "") or ""
    raw = text.strip()
    if raw.startswith(("{", "[")):
        try:
            data = _json.loads(raw)
            if isinstance(data, (dict, list)):
                return data
        except Exception:
            pass

    _reject_text(text, ct)


def debug_enabled(settings):
    try:
        return bool((settings or {}).get("debug_logging", False))
    except Exception:
        return False

def result_output_base(settings):
    return ensure_output_base(settings.get("output_dir"), settings.get("root"))


def result_bucket_name(status):
    if status in ("tagged", "found"):
        return "found"
    if status in ("partial", "partial_match"):
        return "partial_match"
    return "no_match"


def result_paths_for(settings, img, status):
    base_out = result_output_base(settings)
    bucket = result_bucket_name(status)
    bucket_dir = base_out / bucket
    return {
        "base": base_out,
        "bucket": bucket_dir,
        "media": bucket_dir / "media",
        "tags": bucket_dir / "tags",
        "source": bucket_dir / "source",
        "searched": bucket_dir / "searched",
        "cache": bucket_dir / "cache",
        "media_file": bucket_dir / "media" / img.name,
        "searched_file": bucket_dir / "searched" / (img.stem + ".searched.json"),
    }


def output_processed_status(settings, img):
    """Return processed status using SQLite as the main storage.

    Old .searched.json files are ignored in this branch: the DB is the source of truth.
    """
    if not settings.get("copy_results_enabled", True):
        return None
    try:
        from core.database.storage import processed_status
        return processed_status(settings, img)
    except Exception:
        return None


def copy_result_files(settings, img, status):
    """Archive processed media into output and register it in SQLite.

    This SQLite branch does not create .tags.txt/.sources.txt/.tags.json/.searched.json.
    Metadata belongs to the database; files on disk are only media/cache.
    """
    if not settings.get("copy_results_enabled", True):
        return

    img = Path(img)
    paths = result_paths_for(settings, img, status)
    for d in (paths["media"], paths["cache"]):
        d.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copy2(img, paths["media_file"])
    except Exception:
        pass

    try:
        from core.database.storage import mark_processed
        mark_processed(settings, paths["media_file"], status=result_bucket_name(status), original_path=str(img))
    except Exception:
        pass


def cleanup_archived_result(settings, img, statuses=("nomatch",)):
    """Remove a file and sidecars from output buckets after it was promoted.

    Used when a no_match item receives manual tags and must not reappear in
    no_match on the next run.
    """
    img = Path(img)
    names = {img.name}
    stem = img.stem
    for st in statuses:
        paths = result_paths_for(settings, img, st)
        for key in ("media",):
            try:
                f = paths[key] / img.name
                if f.exists():
                    f.unlink()
            except Exception:
                pass
        for dkey in ("tags", "source", "cache", "searched"):
            d = paths[dkey]
            try:
                for f in d.glob(stem + "*"):
                    try:
                        f.unlink()
                    except Exception:
                        pass
            except Exception:
                pass


def write_sidecar_tags(settings, img, tags, source_url="", groups=None, status="tagged"):
    """Store tags/source in SQLite only.

    Historical name is kept for compatibility with old call sites.
    No .txt/.json sidecar files are written in the SQLite-main branch.
    """
    img = Path(img)
    paths = result_paths_for(settings, img, status)
    paths["media"].mkdir(parents=True, exist_ok=True)
    paths["cache"].mkdir(parents=True, exist_ok=True)
    if not groups or not groups_to_tags(groups):
        groups = {"artist": [], "character": [], "copyright": [], "general": list(tags or []), "meta": []}
    try:
        from core.database.storage import upsert_media_metadata
        upsert_media_metadata(
            settings,
            paths["media_file"],
            tags=tags or [],
            groups=groups,
            source_text=source_url or "",
            status=result_bucket_name(status),
            original_path=str(img),
        )
    except Exception:
        pass
    return None, None, None

def promote_manual_match(settings, img, tags, source_url="", groups=None):
    """Promote a NO_MATCH file to found after manual URL tag extraction."""
    img = Path(img)
    tags = unique_keep_order(tags)
    write_sidecar_tags(settings, img, tags, source_url, groups, status="tagged")
    try:
        nm = img.with_suffix(".nomatch")
        if nm.exists():
            nm.unlink()
    except Exception:
        pass
    remove_nomatch(img)
    # Archive as found first, then remove old no_match/partial copies.
    copy_result_files(settings, img, "tagged")
    cleanup_archived_result(settings, img, ("nomatch", "partial"))
    return True

def append_error_log(msg):
    try:
        ERROR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def do_browser_login(auth_url, wait_seconds=60):
    raise RuntimeError("Browser login is not available in desktop v3 yet")



def cleanup_preview_cache(settings=None):
    """Keep generated preview/frame files bounded by age and count."""
    settings = settings or {}
    max_files = int(settings.get("max_preview_cache_files", 1000) or 1000)
    max_age_days = int(settings.get("preview_cache_max_age_days", 14) or 14)
    roots = [
        Path.cwd() / "Local_Booru_Output" / "preview_cache",
        CACHE_DIR / "preview_cache",
    ]
    now = time.time()
    max_age = max_age_days * 86400
    files = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            try:
                if p.is_file():
                    st = p.stat()
                    files.append((p, st.st_mtime, st.st_size))
            except Exception:
                pass

    # Delete old files first.
    for p, mtime, _size in list(files):
        try:
            if max_age_days > 0 and now - mtime > max_age:
                p.unlink(missing_ok=True)
        except Exception:
            pass

    files2 = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            try:
                if p.is_file():
                    st = p.stat()
                    files2.append((p, st.st_mtime))
            except Exception:
                pass

    if max_files > 0 and len(files2) > max_files:
        for p, _mtime in sorted(files2, key=lambda x: x[1])[: len(files2) - max_files]:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


class Tagger:
    def __init__(self, settings, log_func):
        self.settings = settings
        self.session = get_session(settings, log_func)
        self.log = log_func
        self.timeout = max(5, int(float(settings.get("request_timeout_seconds", 20))))
        self.saucenao_state_file = CACHE_DIR / "saucenao_state.json"
        self._partial_match_found = False
        self._partial_match_reason = ""
        self.cancel_callback = None
        # Per-Tagger session/request caches.  Network code must not reload
        # cookies or re-run anti-bot verification for every fallback branch.
        self._session_cache = {}
        self._request_cache = {}
        self._lookup_cache_enabled = False
        try:
            cleanup_preview_cache(self.settings)
        except Exception:
            pass

    def session_for_host(self, host):
        host = (host or "").lower().replace("www.", "")
        if not host:
            host = "__default__"
        if host not in self._session_cache:
            self._session_cache[host] = get_session(self.settings, self.log, None if host == "__default__" else host)
        return self._session_cache[host]

    def _request_cache_key(self, method, url, params=None):
        try:
            items = tuple(sorted((str(k), str(v)) for k, v in (params or {}).items()))
        except Exception:
            items = tuple()
        return (str(method).upper(), str(url), items)

    def _http_get_cached(self, session, url, *, params=None, timeout=None, headers=None):
        """GET with per-file cache.

        APT/booru lookup commonly tries JSON, XML and HTML fallbacks for the
        same page.  Without this cache one file can hit the same domain 5-10
        times, reloading cookies and re-running Cloudflare/PoW checks.  The
        cache is active only during one MD5 lookup, so it cannot return stale
        data for later files.
        """
        key = self._request_cache_key("GET", url, params)
        if self._lookup_cache_enabled and key in self._request_cache:
            return self._request_cache[key]
        r = session.get(url, params=params, timeout=timeout, headers=headers)
        if self._lookup_cache_enabled:
            self._request_cache[key] = r
        return r

    def _atf_get_cached(self, session, url, host, **kwargs):
        key = self._request_cache_key("ATFGET", url, kwargs.get("params"))
        if self._lookup_cache_enabled and key in self._request_cache:
            return self._request_cache[key]
        r = self._atf_get(session, url, host, **kwargs)
        if self._lookup_cache_enabled:
            self._request_cache[key] = r
        return r

    def _is_atf_verification_html(self, text):
        text = text or ""
        head = text[:20000].lower()
        return (
            "booru.allthefallen.moe | verification" in head
            or "x-verification-challenge" in head
            or "powseed" in head
            or "challenge-checkbox" in head
        )

    def _extract_js_const(self, text, name):
        m = re.search(
            r"const\s+" + re.escape(name) + r"\s*=\s*[\"']([^\"']*)[\"']",
            text or "",
        )
        return m.group(1) if m else ""

    def _solve_atf_pow(self, seed, prefix, max_nonce=20000000):
        import hashlib

        seed = seed or ""
        prefix = prefix or ""
        for nonce in range(int(max_nonce)):
            candidate = f"{seed}:{nonce}"
            h = hashlib.sha1(candidate.encode("utf-8")).hexdigest()
            if h.startswith(prefix):
                return str(nonce), h
        return "", ""

    def _pass_atf_verification(self, session, url, html_text, host):
        """
        ATF verification page contains JS challenge values. Reproduce:
        solve SHA1 PoW, wait page delay, POST JSON to the same relative
        endpoint used by xhr.open("POST", post_to, true).
        """
        try:
            challenge_id = self._extract_js_const(html_text, "challenge_id")
            challenge_generated = self._extract_js_const(html_text, "challenge_generated")
            challenge_cookie_expires = self._extract_js_const(html_text, "challenge_cookie_expires")
            pow_seed = self._extract_js_const(html_text, "powSeed")
            post_to = self._extract_js_const(html_text, "post_to") or host

            m = re.search(
                r"powPrefix\\s*=\\s*[\\\"']0[\\\"']\\.repeat\\((\\d+)\\)",
                html_text or ""
            )
            prefix_len = int(m.group(1)) if m else 5
            pow_prefix = "0" * prefix_len

            dm = re.search(r"const\\s+delay\\s*=\\s*(\\d+)", html_text or "")
            delay_seconds = int(dm.group(1)) if dm else 5

            if not (challenge_id and challenge_generated and challenge_cookie_expires and pow_seed):
                if self.log:
                    self.log("  ATF VERIFY ERROR: missing challenge fields")
                return False

            if self.log:
                self.log(f"  ATF VERIFY: solving PoW prefix={prefix_len}")

            nonce, h = self._solve_atf_pow(pow_seed, pow_prefix)

            if not nonce:
                if self.log:
                    self.log("  ATF VERIFY ERROR: PoW not solved")
                return False

            payload = {
                "challenge_id": challenge_id,
                "challenge_generated": challenge_generated,
                "challenge_cookie_expires": challenge_cookie_expires,
                "pow_nonce": nonce,
                "pow_hash": h,
            }

            if post_to.strip().lower().replace("www.", "") == host:
                verify_url = f"https://{host}/"
            else:
                verify_url = urljoin(url, post_to)

            try:
                time.sleep(max(0, min(delay_seconds, 10)) + 0.25)
            except Exception:
                pass

            resp = session.post(
                verify_url,
                json=payload,
                timeout=self.timeout,
                headers={
                    "Accept": "*/*",
                    "Content-Type": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "SAMEORIGIN",
                    "X-Verification-Challenge": "1",
                    "Referer": url,
                    "Origin": f"https://{host}",
                },
            )

            if self.log:
                self.log(
                    f"  ATF VERIFY POST: status={resp.status_code} "
                    f"url={verify_url} cookies={len(session.cookies)} "
                    f"body={(resp.text or '')[:80]!r}"
                )

            return resp.status_code == 200

        except Exception as e:
            if self.log:
                self.log(f"  ATF VERIFY ERROR: {type(e).__name__}: {e}")
            return False

    def _atf_get(self, session, url, host, **kwargs):
        r = session.get(url, **kwargs)
        if self._is_atf_verification_html(r.text):
            if self.log:
                self.log("  ATF VERIFY PAGE DETECTED")
            if self._pass_atf_verification(session, url, r.text, host):
                r = session.get(url, **kwargs)
        return r

    def enabled_domains(self):
        domains = set()
        sites = self.settings.get("sites", {}) if isinstance(self.settings, dict) else {}
        if isinstance(sites, dict):
            for d, cfg in sites.items():
                if not isinstance(cfg, dict) or cfg.get("enabled", True):
                    domains.add(str(d).lower().replace("www.", ""))
        custom_sites = self.settings.get("custom_sites", []) if isinstance(self.settings, dict) else []
        if isinstance(custom_sites, list):
            for site in custom_sites:
                if not isinstance(site, dict):
                    continue
                if site.get("enabled") and site.get("domain"):
                    domains.add(str(site["domain"]).lower().replace("www.", ""))
        return domains

    def tags_from_url(self, url):
        host = urlparse(url).netloc.lower().replace("www.", "")
        try:
            # Direct post URLs. Keep these before custom-sites so broken
            # custom templates cannot steal common booru hosts.
            if host in ("rule34.xxx", "api.rule34.xxx"):
                return self.rule34xxx_tags(url)
            if host == "rule34.us":
                return self.rule34us_tags(url)
            if host in ("danbooru.donmai.us", "donmai.us"):
                return self.danbooru_tags(url)
            if host == "gelbooru.com":
                return self.gelbooru_tags(url)
            if host == "e621.net":
                return self.e621_tags(url)
            custom_sites = self.settings.get("custom_sites", [])
            if isinstance(custom_sites, list):
                for site in custom_sites:
                    if not isinstance(site, dict):
                        continue
                    if site.get("enabled") and host == str(site.get("domain", "")).lower().replace("www.", ""):
                        return self.custom_tags_from_url(site, url)
        except Exception as e:
            msg = f"  URL TAG ERROR [{host}]: {type(e).__name__}: {e}"
            self.log(msg)
            append_error_log(msg)
            return []
        return []

    def grouped_tags_from_url(self, url):
        host = urlparse(url).netloc.lower().replace("www.", "")

        try:
            if host in ("rule34.xxx", "api.rule34.xxx", "rule34.us"):
                try:
                    html = self.session_for_host("rule34.xxx" if "rule34.xxx" in host else "rule34.us").get(url, timeout=self.timeout).text
                    groups = self.booru_groups_from_html(html)
                    if groups_to_tags(groups):
                        return groups
                except Exception:
                    pass
                tags = self.tags_from_url(url)
                return self._categorize_flat_tags("rule34.xxx" if "rule34.xxx" in host else "rule34.us", tags)

            if host == "danbooru.donmai.us":
                parts = urlparse(url).path.strip("/").split("/")

                if len(parts) >= 3 and parts[0] == "post" and parts[1] == "show":
                    post_id = parts[2]
                elif len(parts) >= 2 and parts[0] == "posts":
                    post_id = parts[1]
                else:
                    post_id = parts[-1]

                s = self.session_for_host("danbooru.donmai.us")
                r = s.get(
                    f"https://danbooru.donmai.us/posts/{post_id}.json",
                    params=self.auth_params(self.site_cfg("danbooru.donmai.us")),
                    timeout=self.timeout
                )

                try:
                    data = r.json()
                except Exception:
                    return empty_tag_groups()

                return {
                    "artist": data.get("tag_string_artist", "").split(),
                    "character": data.get("tag_string_character", "").split(),
                    "copyright": data.get("tag_string_copyright", "").split(),
                    "general": data.get("tag_string_general", "").split(),
                    "meta": data.get("tag_string_meta", "").split(),
                }

            if host == "gelbooru.com":
                q = parse_qs(urlparse(url).query)
                post_id = q.get("id", [None])[0]
                if q.get("s", [""])[0] == "list" and q.get("md5"):
                    posts = self.gelbooru_dapi_posts({"tags": f"md5:{q['md5'][0]}", "limit": 1})
                    if posts:
                        return self.gelbooru_groups_from_post(posts[0])
                if post_id:
                    posts = self.gelbooru_dapi_posts({"id": post_id})
                    if posts:
                        return self.gelbooru_groups_from_post(posts[0])
                    html = self.session.get(url, timeout=self.timeout).text
                    return self.gelbooru_groups_from_html(html)

            if host == "e621.net":
                post_id = urlparse(url).path.strip("/").split("/")[-1]
                data = safe_json_response(self.session.get(
                    f"https://e621.net/posts/{post_id}.json",
                    params=self.auth_params(self.site_cfg("e621.net")),
                    timeout=self.timeout
                ), "e621")
                post = data.get("post", {}) if isinstance(data, dict) else {}
                groups = empty_tag_groups()
                tag_map = post.get("tags", {}) if isinstance(post, dict) else {}
                if isinstance(tag_map, dict):
                    groups["artist"] = tag_map.get("artist", [])
                    groups["character"] = tag_map.get("character", [])
                    groups["copyright"] = tag_map.get("copyright", [])
                    groups["general"] = tag_map.get("general", [])
                    groups["meta"] = tag_map.get("meta", []) + tag_map.get("species", []) + tag_map.get("invalid", []) + tag_map.get("lore", [])
                return groups

        except Exception:
            pass

        return empty_tag_groups()

    def rule34xxx_tags(self, url):
        post_id = parse_qs(urlparse(url).query).get("id", [None])[0]
        if not post_id:
            return []
        api = "https://api.rule34.xxx/index.php"
        params = {"page": "dapi", "s": "post", "q": "index", "json": "1", "id": post_id}
        params.update(self.auth_params(self.site_cfg("rule34.xxx")))
        session = self.session_for_host("rule34.xxx")
        data = safe_json_response(session.get(api, params=params, timeout=self.timeout), "rule34.xxx")
        if isinstance(data, list) and data:
            return data[0].get("tags", "").split()
        if isinstance(data, dict):
            p = data.get("post")
            if isinstance(p, list) and p:
                return p[0].get("tags", "").split()
            if isinstance(p, dict):
                return p.get("tags", "").split()
        return []

    def rule34us_tags(self, url):
        post_id = parse_qs(urlparse(url).query).get("id", [None])[0]
        if not post_id:
            return []
        api = "https://rule34.us/index.php"
        params = {"page": "dapi", "s": "post", "q": "index", "json": "1", "id": post_id}
        params.update(self.auth_params(self.site_cfg("rule34.us")))
        session = self.session_for_host("rule34.us")
        r = session.get(api, params=params, timeout=self.timeout)
        try:
            data = safe_json_response(r, "rule34.us")
        except Exception:
            return []
        post = None
        if isinstance(data, list) and data:
            post = data[0]
        elif isinstance(data, dict):
            p = data.get("post")
            post = p[0] if isinstance(p, list) and p else p if isinstance(p, dict) else None
        return post.get("tags", "").split() if post else []

    def danbooru_tags(self, url):
        parts = urlparse(url).path.strip("/").split("/")

        if len(parts) >= 3 and parts[0] == "post" and parts[1] == "show":
            post_id = parts[2]
        elif len(parts) >= 2 and parts[0] == "posts":
            post_id = parts[1]
        else:
            post_id = parts[-1]

        r = self.session.get(
            f"https://danbooru.donmai.us/posts/{post_id}.json",
            params=self.auth_params(self.site_cfg("danbooru.donmai.us")),
            timeout=self.timeout
        )

        if r.status_code != 200:
            raise Exception(f"Danbooru status {r.status_code}: {r.text[:120]}")

        try:
            data = r.json()
        except Exception:
            raise Exception(f"Danbooru non-json: {r.text[:120]}")

        tags = []
        for field in [
            "tag_string_general",
            "tag_string_character",
            "tag_string_copyright",
            "tag_string_artist",
            "tag_string_meta",
        ]:
            tags += data.get(field, "").split()

        return unique_keep_order(tags)

    

    def _clean_booru_tag_candidate(self, txt="", href=""):
        """Extract a clean tag name from booru sidebar links.

        Rule34/Gelbooru pages often show tag text as "tag 12345" or use
        ?tags=tag in href.  This helper avoids returning counts/actions as tags.
        """
        vals = []
        if txt:
            vals.append(html.unescape(str(txt)))
        try:
            q = parse_qs(urlparse(href or "").query)
            for raw in q.get("tags", []):
                vals += html.unescape(str(raw)).replace("+", " ").split()
        except Exception:
            pass

        out = []
        for t in vals:
            t = html.unescape(str(t)).strip()
            t = re.sub(r"\s+\d[\d,]*\s*$", "", t).strip()
            t = t.replace(" ", "_")
            if not t or t in {"?", "+", "-", "edit", "wiki", "posts", "post", "login", "remove"}:
                continue
            if "?" in t or "=" in t or "/" in t:
                continue
            if t.startswith(("rating:", "sort:", "md5:", "user:", "score:")):
                continue
            if re.fullmatch(r"[0-9,]+", t):
                continue
            out.append(t)
        return unique_keep_order(out)

    def _guess_group_from_heading_text(self, text):
        t = str(text or "").strip().lower().replace(":", "")
        aliases = {
            "artist": "artist", "artists": "artist", "автор": "artist", "авторы": "artist",
            "character": "character", "characters": "character", "персонаж": "character", "персонажи": "character",
            "copyright": "copyright", "copyrights": "copyright", "series": "copyright", "серия": "copyright", "копирайт": "copyright",
            "general": "general", "tag": "general", "tags": "general", "теги": "general", "общие": "general",
            "meta": "meta", "metadata": "meta", "мета": "meta",
        }
        return aliases.get(t)

    def _groups_from_sidebar_sections(self, soup):
        """Fallback for old Rule34-style sidebars with text headings.

        Some pages do not put tag-type-* classes on <li>, but render a heading
        like "Artist" / "Character" and then a run of tag links. This parser walks
        visible sidebar-ish elements in order and assigns links to the last heading.
        """
        groups = empty_tag_groups()
        containers = soup.select("#tag-sidebar, #tag-list, .tag-list, .sidebar, aside, div[id*=tag], div[class*=tag]")
        if not containers:
            containers = [soup]

        for cont in containers:
            current = None
            # Include headings, list items and links in document order.
            for node in cont.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "b", "strong", "li", "a", "span", "div"], recursive=True):
                text = node.get_text(" ", strip=True)
                guessed = self._guess_group_from_heading_text(text)
                if guessed and node.name != "a":
                    current = guessed
                    continue

                cls = " ".join(node.get("class", [])).lower() if hasattr(node, "get") else ""
                for key in ("artist", "character", "copyright", "general", "meta"):
                    if key in cls or (key == "meta" and "metadata" in cls):
                        current = key
                        break

                if node.name == "a" and "tags=" in (node.get("href", "") or ""):
                    group = current or "general"
                    for tag in self._clean_booru_tag_candidate(node.get_text(" ", strip=True), node.get("href", "") or ""):
                        groups[group].append(tag)

        for k in groups:
            groups[k] = unique_keep_order(groups[k])
        return groups

    def booru_groups_from_html(self, html):
        """Generic booru sidebar tag grouping for rule34/gelbooru-like HTML."""
        soup = BeautifulSoup(html or "", "html.parser")
        groups = empty_tag_groups()
        selector_map = {
            # Gelbooru/Rule34 style classes
            "artist": ["li.tag-type-artist a", ".tag-type-artist a", "li[class*='artist'] a", "a.tag-type-artist",
                       # Danbooru style numeric categories: 1 = artist
                       "li.category-1 a", ".category-1 a", "li[class*='category-1'] a"],
            "character": ["li.tag-type-character a", ".tag-type-character a", "li[class*='character'] a", "a.tag-type-character",
                          # Danbooru: 4 = character
                          "li.category-4 a", ".category-4 a", "li[class*='category-4'] a"],
            "copyright": ["li.tag-type-copyright a", "li.tag-type-copyrights a", ".tag-type-copyright a", ".tag-type-copyrights a", "li[class*='copyright'] a", "a.tag-type-copyright",
                          # Danbooru: 3 = copyright
                          "li.category-3 a", ".category-3 a", "li[class*='category-3'] a"],
            "general": ["li.tag-type-general a", ".tag-type-general a", "li[class*='general'] a", "a.tag-type-general",
                        # Danbooru: 0 = general
                        "li.category-0 a", ".category-0 a", "li[class*='category-0'] a"],
            "meta": ["li.tag-type-metadata a", "li.tag-type-meta a", ".tag-type-metadata a", ".tag-type-meta a", "li[class*='metadata'] a", "li[class*='meta'] a", "a.tag-type-metadata", "a.tag-type-meta",
                     # Danbooru: 5 = meta
                     "li.category-5 a", ".category-5 a", "li[class*='category-5'] a"],
        }

        for group, selectors in selector_map.items():
            for sel in selectors:
                for a in soup.select(sel):
                    for t in self._clean_booru_tag_candidate(a.get_text(" ", strip=True), a.get("href", "") or ""):
                        groups[group].append(t)

        section_groups = self._groups_from_sidebar_sections(soup)
        for k in groups:
            groups[k] += section_groups.get(k, [])

        # Fallback: many booru pages expose tag links but no group classes/headings.
        if not groups_to_tags(groups):
            for a in soup.select("a[href*='tags=']"):
                for t in self._clean_booru_tag_candidate(a.get_text(" ", strip=True), a.get("href", "") or ""):
                    groups["general"].append(t)

        for k in groups:
            groups[k] = unique_keep_order(groups[k])
        return groups


    def _filter_recovered_gelbooru_tags(self, tags):
        """Remove Gelbooru UI/navigation words from partial HTML recovery.

        Some deleted/list pages expose UI links like "Posts" and "All" via
        the same query format as real tag links. These must not be treated as
        recovered tags.
        """
        bad = {
            "posts", "post", "all", "gelbooru", "image", "images",
            "comments", "comment", "forum", "forums", "wiki", "help",
            "pool", "pools", "popular", "random", "login", "logout",
            "register", "account", "favorites", "favorite", "upload",
            "uploads", "edit", "delete", "report", "search", "tags",
            "tag", "artists", "artist", "characters", "character",
            "copyrights", "copyright", "metadata", "meta", "general",
        }
        out = []
        for t in tags or []:
            nt = normalize_tag(str(t))
            if not nt:
                continue
            low = nt.lower()
            if low in bad:
                continue
            if low.startswith(("page:", "sort:", "rating:", "md5:", "user:", "id:")):
                continue
            if any(ch in nt for ch in ("/", "?", "=", "&", "#")):
                continue
            if re.fullmatch(r"[0-9,]+", nt):
                continue
            out.append(nt)
        return unique_keep_order(out)

    def gelbooru_tags_from_html(self, html):
        """Best-effort Gelbooru tag recovery from HTML.

        This is useful when IQDB points to a Gelbooru post/list page whose post is
        deleted or hidden from DAPI, but the rendered HTML still contains the tag
        sidebar or tag search links.
        """
        soup = BeautifulSoup(html or "", "html.parser")
        tags = []

        selectors = [
            "li.tag-type-general a",
            "li.tag-type-character a",
            "li.tag-type-copyright a",
            "li.tag-type-artist a",
            "li.tag-type-metadata a",
            "li.tag-type-meta a",
            "ul#tag-list a[href*='tags=']",
            "#tag-list a[href*='tags=']",
            "aside a[href*='tags=']",
            "a[href*='page=post'][href*='tags=']",
        ]

        for sel in selectors:
            for a in soup.select(sel):
                href = a.get("href", "") or ""
                text = a.get_text(" ", strip=True)
                candidates = []
                if text:
                    candidates.append(text)
                try:
                    q = parse_qs(urlparse(href).query)
                    for raw in q.get("tags", []):
                        candidates += str(raw).replace("+", " ").split()
                except Exception:
                    pass

                for t in candidates:
                    t = str(t).strip()
                    if not t:
                        continue
                    if t in {"?", "+", "-", "edit", "wiki", "posts", "post", "login"}:
                        continue
                    if "?" in t or "=" in t or "/" in t:
                        continue
                    if t.startswith(("rating:", "sort:", "md5:")):
                        continue
                    # Gelbooru sidebar sometimes has counts/actions mixed in.
                    if re.fullmatch(r"[0-9,]+", t):
                        continue
                    tags.append(t)

        if not tags:
            soup2 = BeautifulSoup(html or "", "html.parser")
            for meta in soup2.select("meta[name='keywords']"):
                content = meta.get("content", "")
                for t in re.split(r"[,\s]+", content):
                    t = normalize_tag(t)
                    if t and t.lower() not in {"posts", "post", "all", "gelbooru", "image", "images"}:
                        tags.append(t)
        return self._filter_recovered_gelbooru_tags(unique_keep_order(tags))

    def gelbooru_groups_from_html(self, html):
        """Recover Gelbooru tag groups from rendered HTML/sidebar."""
        soup = BeautifulSoup(html or "", "html.parser")
        groups = empty_tag_groups()
        selectors = {
            "artist": ["li.tag-type-artist a"],
            "character": ["li.tag-type-character a"],
            "copyright": ["li.tag-type-copyright a", "li.tag-type-copyrights a"],
            "general": ["li.tag-type-general a"],
            "meta": ["li.tag-type-metadata a", "li.tag-type-meta a"],
        }
        for group, sels in selectors.items():
            for sel in sels:
                for a in soup.select(sel):
                    txt = a.get_text(" ", strip=True)
                    href = a.get("href", "") or ""
                    candidates = []
                    if txt:
                        candidates.append(txt)
                    try:
                        q = parse_qs(urlparse(href).query)
                        for raw in q.get("tags", []):
                            candidates += str(raw).replace("+", " ").split()
                    except Exception:
                        pass
                    for t in candidates:
                        t = str(t).strip()
                        if not t or t.lower() in {"?", "+", "-", "edit", "wiki", "posts", "post", "login", "all", "help", "comments", "favorite", "favorites", "random"}:
                            continue
                        if "?" in t or "=" in t or "/" in t or re.fullmatch(r"[0-9,]+", t):
                            continue
                        if t.startswith(("rating:", "sort:", "md5:")):
                            continue
                        groups[group].append(t)
        for k in groups:
            groups[k] = unique_keep_order(groups[k])
        return groups

    def _categorize_tags_via_dapi(self, host, tags):
        """Return grouped tags using booru tag API.

        DAPI post endpoints on Gelbooru/Rule34 often return only one flat
        "tags" string.  The tag-index endpoint can return tag category/type,
        so we query it in chunks and rebuild Artist/Character/Copyright/Meta.
        """
        clean = [normalize_tag(t) for t in (tags or []) if normalize_tag(t)]
        clean = unique_keep_order(clean)
        groups = empty_tag_groups()
        if not clean:
            return groups

        if host == "gelbooru.com":
            base = "https://gelbooru.com/index.php"
            session_host = "gelbooru.com"
        elif host == "rule34.xxx":
            base = "https://api.rule34.xxx/index.php"
            session_host = "rule34.xxx"
        elif host == "rule34.us":
            base = "https://rule34.us/index.php"
            session_host = "rule34.us"
        else:
            groups["general"] = clean
            return groups

        remaining = set(clean)
        s = self.session_for_host(session_host)

        # Small chunks keep URLs below browser/server limits.
        for i in range(0, len(clean), 50):
            chunk = clean[i:i + 50]
            params = {
                "page": "dapi",
                "s": "tag",
                "q": "index",
                "json": "1",
                "names": " ".join(chunk),
            }
            params.update(self.auth_params(self.site_cfg(session_host)))
            try:
                r = s.get(base, params=params, timeout=self.timeout)
                data = None
                try:
                    data = r.json()
                except Exception:
                    data = None

                rows = []
                if isinstance(data, list):
                    rows = data
                elif isinstance(data, dict):
                    tag_data = data.get("tag") or data.get("tags") or data.get("post")
                    if isinstance(tag_data, list):
                        rows = tag_data
                    elif isinstance(tag_data, dict):
                        rows = [tag_data]
                    elif data.get("name"):
                        rows = [data]

                # XML fallback, because some DAPI installs ignore json=1.
                if not rows:
                    try:
                        soup = BeautifulSoup(r.text or "", "xml")
                        for node in soup.find_all("tag"):
                            rows.append(dict(node.attrs))
                    except Exception:
                        pass

                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    name = normalize_tag(row.get("name") or row.get("tag") or "")
                    if not name:
                        continue
                    typ = row.get("type", row.get("category", row.get("tag_type")))
                    group = group_from_tag_type(typ)
                    add_tags_to_groups(groups, group, [name])
                    remaining.discard(name)
            except Exception as e:
                self.log(f"    TAG CATEGORY ERROR [{host}]: {e}")

        # Some old DAPI implementations do not support names=<many tags>.
        # Fallback: ask exact tag info one-by-one for the still unknown tags.
        for tag in list(remaining):
            for key in ("name", "name_pattern"):
                try:
                    params = {"page": "dapi", "s": "tag", "q": "index", "json": "1", key: tag}
                    params.update(self.auth_params(self.site_cfg(session_host)))
                    r = s.get(base, params=params, timeout=self.timeout)
                    rows = []
                    try:
                        data = r.json()
                    except Exception:
                        data = None
                    if isinstance(data, list):
                        rows = data
                    elif isinstance(data, dict):
                        td = data.get("tag") or data.get("tags")
                        if isinstance(td, list):
                            rows = td
                        elif isinstance(td, dict):
                            rows = [td]
                        elif data.get("name"):
                            rows = [data]
                    if not rows:
                        try:
                            soup = BeautifulSoup(r.text or "", "xml")
                            rows = [dict(node.attrs) for node in soup.find_all("tag")]
                        except Exception:
                            rows = []
                    found = False
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        name = normalize_tag(row.get("name") or row.get("tag") or "")
                        if name != tag:
                            continue
                        typ = row.get("type", row.get("category", row.get("tag_type")))
                        add_tags_to_groups(groups, group_from_tag_type(typ), [tag])
                        remaining.discard(tag)
                        found = True
                        break
                    if found:
                        break
                except Exception:
                    pass

        # Do not lose unknown tags.
        add_tags_to_groups(groups, "general", sorted(remaining))
        return groups

    def _categorize_flat_tags(self, source_host, tags):
        source_host = (source_host or "").lower().replace("www.", "")
        if source_host in ("gelbooru.com", "rule34.xxx", "rule34.us"):
            groups = self._categorize_tags_via_dapi(source_host, tags)
            if groups_to_tags(groups):
                return groups
        groups = empty_tag_groups()
        groups["general"] = unique_keep_order([normalize_tag(t) for t in tags or [] if normalize_tag(t)])
        return groups

    def gelbooru_groups_from_post(self, post):
        groups = empty_tag_groups()
        if not isinstance(post, dict):
            return groups

        # Newer booru JSON may expose split tag fields.
        split_mapping = {
            "artist": ["tag_string_artist", "tags_artist"],
            "character": ["tag_string_character", "tags_character"],
            "copyright": ["tag_string_copyright", "tags_copyright"],
            "general": ["tag_string_general", "tags_general"],
            "meta": ["tag_string_meta", "tags_meta", "tags_metadata"],
        }
        for group, fields in split_mapping.items():
            for field in fields:
                val = post.get(field)
                if isinstance(val, str):
                    add_tags_to_groups(groups, group, val.split())
                elif isinstance(val, list):
                    add_tags_to_groups(groups, group, val)

        if groups_to_tags(groups):
            # If a flat "tags" field also exists, add only missing tags as general.
            flat = self.gelbooru_tags_from_post(post)
            known = set(groups_to_tags(groups))
            add_tags_to_groups(groups, "general", [t for t in flat if normalize_tag(t) not in known])
            return groups

        # Older Gelbooru DAPI exposes only flat "tags". Categorize them using
        # page=dapi&s=tag&q=index&names=...
        flat = self.gelbooru_tags_from_post(post)
        if flat:
            return self._categorize_flat_tags("gelbooru.com", flat)
        return groups

    def gelbooru_tags_from_post(self, post):
        if not isinstance(post, dict):
            return []

        value = post.get("tags", "")
        if isinstance(value, str):
            return unique_keep_order(value.split())
        if isinstance(value, list):
            return unique_keep_order(value)
        return []

    def gelbooru_dapi_posts(self, params):
        api_params = {
            "page": "dapi",
            "s": "post",
            "q": "index",
            "json": "1",
        }
        api_params.update(params)
        api_params.update(self.auth_params(self.site_cfg("gelbooru.com")))

        r = self.session.get("https://gelbooru.com/index.php", params=api_params, timeout=self.timeout)

        try:
            data = r.json()
        except Exception:
            return []

        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]

        if isinstance(data, dict):
            post = data.get("post")
            if isinstance(post, list):
                return post
            if isinstance(post, dict):
                return [post]

        return []

    def gelbooru_tags(self, url):
        q = parse_qs(urlparse(url).query)

        if q.get("s", [""])[0] == "list" and q.get("md5"):
            return self.gelbooru_tags_by_md5(q["md5"][0])

        post_id = q.get("id", [None])[0]
        if not post_id:
            return []

        posts = self.gelbooru_dapi_posts({"id": post_id})
        if posts:
            tags = self.gelbooru_tags_from_post(posts[0])
            if tags:
                return tags

        # HTML fallback, если DAPI не отдал json
        html = self.session.get(
            f"https://gelbooru.com/index.php?page=post&s=view&id={post_id}",
            params=self.auth_params(self.site_cfg("gelbooru.com")),
            timeout=self.timeout
        ).text
        tags = self.gelbooru_tags_from_html(html)
        if tags:
            self._partial_match_found = True
            self._partial_match_reason = f"Gelbooru HTML recovered tags for deleted/hidden post id={post_id}"
            self.log(f"  PARTIAL MATCH: recovered {len(tags)} Gelbooru tags from HTML/deleted post")
        return tags

    def gelbooru_tags_by_md5(self, md5):
        # IQDB часто даёт ссылку вида:
        # https://gelbooru.com/index.php?page=post&s=list&md5=<hash>
        # Сначала пробуем DAPI, потом HTML. ВАЖНО: на list-page нельзя
        # сразу собирать "теги" из навигации, иначе получаются Posts/all.
        posts = self.gelbooru_dapi_posts({"tags": f"md5:{md5}", "limit": 1})
        if posts:
            tags = self._filter_recovered_gelbooru_tags(self.gelbooru_tags_from_post(posts[0]))
            if tags:
                return tags

            post_id = posts[0].get("id")
            if post_id:
                return self.gelbooru_tags(
                    f"https://gelbooru.com/index.php?page=post&s=view&id={post_id}"
                )

        # HTML fallback
        params = {"page": "post", "s": "list", "tags": f"md5:{md5}"}
        params.update(self.auth_params(self.site_cfg("gelbooru.com")))

        html = self.session.get("https://gelbooru.com/index.php", params=params, timeout=self.timeout).text
        soup = BeautifulSoup(html, "html.parser")

        # 1) Если list-page содержит ссылку на post view — идём туда.
        # Там чаще всего лежит настоящий sidebar с тегами.
        # Ищем настоящий post view id в HTML list/deleted страницы
        html_match = re.search(r'[?&]id=(\d+)', html)
        if html_match:
            post_id = html_match.group(1)
            self.log(f"  GELBOORU VIEW RECOVER: id={post_id}")
            tags = self._filter_recovered_gelbooru_tags(self.gelbooru_tags(
                f"https://gelbooru.com/index.php?page=post&s=view&id={post_id}"
            ))
            if tags:
                return tags

        # 2) Только если post view не помог, пробуем аварийно вытянуть теги
        # прямо из list/deleted HTML. Мусор типа Posts/all фильтруется.
        recovered = self._filter_recovered_gelbooru_tags(self.gelbooru_tags_from_html(html))
        if recovered:
            self._partial_match_found = True
            self._partial_match_reason = f"Gelbooru HTML recovered tags from md5 list {md5}"
            self.log(f"  PARTIAL MATCH: recovered {len(recovered)} Gelbooru tags from md5/list HTML")
            return recovered

        self.log("  PARTIAL MATCH SKIP: Gelbooru md5/list contained no real tags")
        return []


    def e621_tags(self, url):
        post_id = urlparse(url).path.strip("/").split("/")[-1]
        session = self.session_for_host("e621.net")
        data = safe_json_response(session.get(
            f"https://e621.net/posts/{post_id}.json",
            params=self.auth_params(self.site_cfg("e621.net")),
            timeout=self.timeout,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "User-Agent": "LocalBooru/3.0 (local archive manager; contact: local-user)",
            },
        ), "e621")
        tags = []
        post = data.get("post", {}) if isinstance(data, dict) else {}
        tag_map = post.get("tags", {}) if isinstance(post, dict) else {}
        if isinstance(tag_map, dict):
            for group in tag_map.values():
                if isinstance(group, list):
                    tags += group
        return tags

    def custom_tags_from_url(self, site, url):
        parts = urlparse(url).path.strip("/").split("/")
        post_id = parts[-1]
        base = site["base_url"].rstrip("/")
        if site.get("url_api", "posts_id_json") == "posts_id_json":
            api = f"{base}/posts/{post_id}.json"
        else:
            api = f"{base}/post/show/{post_id}.json"
        params = self.custom_auth_params(site)
        data = safe_json_response(self.session.get(api, params=params, timeout=self.timeout), site.get("domain", "custom"))
        return self.extract_tags_from_post(data)

    def auth_params(self, cfg):
        params = {}
        if not isinstance(cfg, dict):
            return params
        if cfg.get("login"):
            params["login"] = cfg["login"]
        if cfg.get("api_key"):
            params["api_key"] = cfg["api_key"]
        if cfg.get("user_id"):
            params["user_id"] = cfg["user_id"]
        return params

    def site_cfg(self, domain):
        sites = self.settings.get("sites", {})
        if not isinstance(sites, dict):
            return {}
        cfg = sites.get(domain, {})
        return cfg if isinstance(cfg, dict) else {}

    def custom_auth_params(self, site):
        return self.auth_params(site if isinstance(site, dict) else {})

    def extract_tags_from_post(self, data):
        if isinstance(data, list):
            if not data:
                return []
            data = data[0]
        if isinstance(data, dict) and "post" in data and isinstance(data["post"], dict):
            data = data["post"]
        if not isinstance(data, dict):
            return []
        tags = []
        for field in ["tags", "tag_string", "tag_string_general", "tag_string_character", "tag_string_copyright", "tag_string_artist", "tag_string_meta"]:
            value = data.get(field, "")
            if isinstance(value, str):
                tags += value.split()
            elif isinstance(value, list):
                tags += value
        return tags


    # ------------------------------------------------------------------
    # Engine-based booru lookup layer
    # ------------------------------------------------------------------
    def _normalize_engine_type(self, site):
        """Return one normalized engine family for any built-in/custom site.

        Sites are configuration. Engines are behavior.  Adding a new site should
        normally only require setting domain/base_url/type, not adding a new
        if-domain parser in the MD5 pipeline.
        """
        site = site if isinstance(site, dict) else {}
        raw = str(site.get("engine") or site.get("type") or site.get("kind") or "").strip().lower()
        domain = str(site.get("domain") or site.get("host") or site.get("base_url") or "").lower()

        if raw in ("danbooru", "danbooru2", "danbooru_html"):
            return "danbooru"
        if raw in ("gelbooru", "gelbooru_html", "rule34xxx", "rule34.xxx", "dapi"):
            return "gelbooru"
        if raw in ("moebooru", "rule34us", "rule34.us"):
            return "moebooru"
        if raw in ("e621", "e926"):
            return "e621"
        if raw in ("szurubooru", "philomena"):
            return raw

        # Domain fallback for older settings files.
        if "e621.net" in domain or "e926.net" in domain:
            return "e621"
        if "rule34.us" in domain or "konachan" in domain or "yande.re" in domain:
            return "moebooru"
        if "gelbooru" in domain or "rule34.xxx" in domain or "xbooru" in domain or "safebooru" in domain or "tbib" in domain or "realbooru" in domain:
            return "gelbooru"
        if "danbooru" in domain or "donmai" in domain or "allthefallen" in domain or "lolibooru" in domain or "hypnohub" in domain or "aibooru" in domain:
            return "danbooru"

        return "custom"

    def _site_root_from_cfg(self, site):
        site = site if isinstance(site, dict) else {}
        raw = str(site.get("base_url") or site.get("login_url") or site.get("url") or "").strip().rstrip("/")
        domain = str(site.get("domain") or site.get("host") or "").strip().strip("/")
        if not raw and domain:
            raw = "https://" + domain
        if raw and not raw.startswith(("http://", "https://")):
            raw = "https://" + raw
        try:
            u = urlparse(raw)
            scheme = u.scheme or "https"
            host = (u.netloc or domain or u.path.split("/", 1)[0]).lower().replace("www.", "")
            return f"{scheme}://{host}".rstrip("/")
        except Exception:
            return raw

    def _site_label(self, site):
        site = site if isinstance(site, dict) else {}
        return str(site.get("name") or site.get("domain") or site.get("base_url") or "site").strip() or "site"

    def _all_enabled_site_configs(self):
        """Return normalized enabled site configs.

        The UI can contain the same domain both as a built-in site and as a
        custom site.  Running both causes duplicate requests, duplicate ATF PoW,
        and confusing logs.  A site is identified by (domain, engine).  Later
        custom entries are allowed to fill/override empty built-in fields so
        user auth/API settings are preserved without scanning the same host twice.
        """
        by_key = {}

        def normalize_site(raw, *, is_custom=False):
            if not isinstance(raw, dict):
                return None
            if not raw.get("enabled", True if not is_custom else False):
                return None
            site = dict(raw)
            if not site.get("domain"):
                root = self._site_root_from_cfg(site)
                site["domain"] = urlparse(root).netloc.lower().replace("www.", "")
            site["domain"] = str(site.get("domain") or "").lower().replace("www.", "")
            if not site["domain"]:
                return None
            site.setdefault("login_url", "https://" + site["domain"])
            site["engine"] = self._normalize_engine_type(site)
            return site

        def merge_site(old, new):
            if old is None:
                return new
            merged = dict(old)
            # Prefer explicit values from the later/custom config, but do not
            # replace useful values with blanks.
            for k, v in (new or {}).items():
                if v not in (None, "", [], {}):
                    merged[k] = v
            # If either version is enabled, keep enabled true.
            merged["enabled"] = bool(old.get("enabled", True) or new.get("enabled", True))
            return merged

        sites = self.settings.get("sites", {}) if isinstance(self.settings, dict) else {}
        if isinstance(sites, dict):
            for domain, cfg in sites.items():
                if not isinstance(cfg, dict):
                    continue
                item = dict(cfg)
                item.setdefault("domain", str(domain).lower().replace("www.", ""))
                site = normalize_site(item, is_custom=False)
                if not site:
                    continue
                key = (site["domain"], site["engine"])
                by_key[key] = merge_site(by_key.get(key), site)

        custom_sites = self.settings.get("custom_sites", []) if isinstance(self.settings, dict) else []
        if isinstance(custom_sites, list):
            for item in custom_sites:
                site = normalize_site(item, is_custom=True)
                if not site:
                    continue
                key = (site["domain"], site["engine"])
                by_key[key] = merge_site(by_key.get(key), site)

        return list(by_key.values())

    def _auth_params_for_site(self, site):
        return self.auth_params(site if isinstance(site, dict) else {})

    def _engine_api_attempts(self, site, md5):
        """Build MD5 lookup attempts by engine family.

        Every booru-style site goes through this instead of one-off per-domain
        code. Each attempt is (url, params, expected_format_label).
        """
        site = site if isinstance(site, dict) else {}
        root = self._site_root_from_cfg(site).rstrip("/")
        engine = self._normalize_engine_type(site)
        auth = self._auth_params_for_site(site)
        attempts = []

        if engine == "danbooru":
            # Danbooru forks disagree: real Danbooru usually supports tags=md5:,
            # ATF historically answered search[md5]. Try both, strict MD5 guard
            # still decides whether tags are allowed.
            attempts += [
                (f"{root}/posts.json", {"tags": f"md5:{md5}", "limit": 1, **auth}, "json"),
                (f"{root}/posts.json", {"search[md5]": md5, "limit": 1, **auth}, "json"),
                (f"{root}/posts.json", {"md5": md5, "limit": 1, **auth}, "json"),
            ]
        elif engine == "gelbooru":
            attempts += [
                (f"{root}/index.php", {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": f"md5:{md5}", "limit": 1, **auth}, "json"),
                (f"{root}/index.php", {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": md5, "limit": 1, **auth}, "json"),
                (f"{root}/index.php", {"page": "dapi", "s": "post", "q": "index", "tags": f"md5:{md5}", "limit": 1, **auth}, "xml"),
                (f"{root}/index.php", {"page": "dapi", "s": "post", "q": "index", "tags": md5, "limit": 1, **auth}, "xml"),
            ]
        elif engine == "moebooru":
            attempts += [
                (f"{root}/post/index.json", {"tags": f"md5:{md5}", "limit": 1, **auth}, "json"),
                (f"{root}/post/index.json", {"tags": md5, "limit": 1, **auth}, "json"),
                (f"{root}/posts.json", {"tags": f"md5:{md5}", "limit": 1, **auth}, "json"),
                (f"{root}/posts.json", {"md5": md5, "limit": 1, **auth}, "json"),
            ]
        elif engine == "e621":
            attempts += [
                (f"{root}/posts.json", {"tags": f"md5:{md5}", "limit": 1, **auth}, "json"),
                (f"{root}/posts.json", {"tags": f"md5:{md5} status:any", "limit": 1, **auth}, "json"),
            ]
        elif engine == "szurubooru":
            attempts += [
                (f"{root}/api/posts", {"query": f"md5:{md5}", "limit": 1, **auth}, "json"),
                (f"{root}/api/posts", {"query": md5, "limit": 1, **auth}, "json"),
            ]
        else:
            # Unknown/custom: try all common engines. This is deliberately broad
            # but still safe because tags are only applied after exact MD5.
            attempts += [
                (f"{root}/posts.json", {"tags": f"md5:{md5}", "limit": 1, **auth}, "json"),
                (f"{root}/posts.json", {"search[md5]": md5, "limit": 1, **auth}, "json"),
                (f"{root}/posts.json", {"md5": md5, "limit": 1, **auth}, "json"),
                (f"{root}/post/index.json", {"tags": f"md5:{md5}", "limit": 1, **auth}, "json"),
                (f"{root}/index.php", {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": f"md5:{md5}", "limit": 1, **auth}, "json"),
                (f"{root}/index.php", {"page": "dapi", "s": "post", "q": "index", "tags": f"md5:{md5}", "limit": 1, **auth}, "xml"),
                (f"{root}/api/posts", {"query": f"md5:{md5}", "limit": 1, **auth}, "json"),
            ]

        return attempts

    def _post_url_for_engine(self, site, post):
        site = site if isinstance(site, dict) else {}
        if not isinstance(post, dict):
            return ""
        root = self._site_root_from_cfg(site).rstrip("/")
        engine = self._normalize_engine_type(site)
        post_id = post.get("id") or post.get("post_id") or post.get("pid")
        if not post_id:
            return ""
        if engine == "gelbooru":
            return f"{root}/index.php?page=post&s=view&id={post_id}"
        if engine == "moebooru":
            # rule34.us behaves more like a Gelbooru frontend despite being
            # listed in the Moebooru group by users.
            host = urlparse(root).netloc.lower()
            if "rule34.us" in host:
                return f"{root}/index.php?page=post&s=view&id={post_id}"
            return f"{root}/post/show/{post_id}"
        if engine == "e621":
            return f"{root}/posts/{post_id}"
        return f"{root}/posts/{post_id}"

    def _groups_from_engine_post(self, site, post, source_url=""):
        engine = self._normalize_engine_type(site)
        if engine == "gelbooru":
            groups = self.gelbooru_groups_from_post(post)
        else:
            groups = self._groups_from_post_dict_general(post)

        if source_url and not groups_to_tags(groups):
            try:
                html_groups = self.grouped_tags_from_url(source_url)
                if groups_to_tags(html_groups):
                    groups = html_groups
            except Exception:
                pass
        return groups

    def _html_search_params_for_engine(self, site, md5):
        root = self._site_root_from_cfg(site).rstrip("/")
        engine = self._normalize_engine_type(site)
        if engine == "gelbooru":
            base = f"{root}/index.php"
            searches = [
                {"page": "post", "s": "list", "tags": f"md5:{md5}"},
                {"page": "post", "s": "list", "tags": md5},
            ]
        elif engine == "moebooru":
            host = urlparse(root).netloc.lower()
            if "rule34.us" in host:
                base = f"{root}/index.php"
                searches = [
                    {"page": "post", "s": "list", "tags": f"md5:{md5}"},
                    {"page": "post", "s": "list", "tags": md5},
                ]
            else:
                base = f"{root}/post"
                searches = [{"tags": f"md5:{md5}"}, {"tags": md5}]
        else:
            base = f"{root}/posts"
            searches = [{"tags": f"md5:{md5}"}, {"tags": md5}]
        return base, searches

    def _engine_html_fallback_by_md5(self, site, md5):
        label = self._site_label(site)
        root = self._site_root_from_cfg(site)
        host = urlparse(root).netloc.lower().replace("www.", "") or label
        base, searches = self._html_search_params_for_engine(site, md5)
        session = self.session_for_host(host)
        for params in searches:
            try:
                r = self._atf_get_cached(
                    session,
                    base,
                    host,
                    params=params,
                    timeout=self.timeout,
                    headers={"Accept": "text/html,application/xhtml+xml,*/*"},
                )
                link = self._first_post_link_from_html(r.text or "", host)
                if not link:
                    continue
                tags, src, groups = self._html_tags_strict_by_md5(host, link, md5)
                if tags:
                    self.log(f"    {label} HTML STRICT MATCH: {redact_sensitive_url(src)}")
                    return tags, src, groups
            except Exception as e:
                self.log(f"    {label} HTML fallback error: {e}")
        return [], "", empty_tag_groups()

    def engine_by_md5(self, site, md5):
        """Single MD5 lookup path for every configured booru/custom site."""
        site = site if isinstance(site, dict) else {}
        label = self._site_label(site)
        root = self._site_root_from_cfg(site)
        host = urlparse(root).netloc.lower().replace("www.", "")
        session = self.session_for_host(host)
        engine = self._normalize_engine_type(site)
        headers = {
            "Accept": "application/json, application/xml, text/xml, */*",
            "User-Agent": "LocalBooru/3.0 (local archive manager)",
        }
        if engine == "e621":
            headers["User-Agent"] = "LocalBooru/3.0 (local archive manager; contact: local-user)"
            headers["Accept-Encoding"] = "gzip, deflate"

        for api, params, fmt in self._engine_api_attempts(site, md5):
            try:
                r = self._atf_get_cached(session, api, host, params=params, timeout=self.timeout, headers=headers)
                posts = self._posts_from_dapi_response(r, label)
                if not posts:
                    continue

                for post in posts:
                    if not isinstance(post, dict):
                        continue

                    # First try API-level explicit MD5.
                    if self._post_md5_value(post) != (md5 or "").lower():
                        # Some APIs omit md5 even in search result. Verify via
                        # post HTML before rejecting. This is generic for every
                        # engine, not ATF-only.
                        src_for_verify = self._post_url_for_engine(site, post)
                        if not src_for_verify:
                            self.log(f"    {label} MD5 REJECT: post has no post URL for HTML verification")
                            continue
                        try:
                            html = self._http_get_cached(session, src_for_verify, timeout=self.timeout, headers={"Accept": "text/html,application/xhtml+xml,*/*"}).text
                            if not self._verify_html_md5(label, html, md5):
                                got = self._post_md5_value(post)
                                if got:
                                    self.log(f"    {label} MD5 REJECT: local={md5} remote={got}")
                                else:
                                    self.log(f"    {label} MD5 REJECT: post={post.get('id')} has no verifiable md5")
                                continue
                        except Exception as e:
                            self.log(f"    {label} MD5 HTML VERIFY ERROR: {e}")
                            continue

                    source_url = self._post_url_for_engine(site, post)
                    groups = self._groups_from_engine_post(site, post, source_url)
                    tags = groups_to_tags(groups) or self._tags_from_post_dict(post)
                    if not tags and source_url:
                        try:
                            tags = self.tags_from_url(source_url)
                            groups = self._categorize_flat_tags(host or label, tags)
                        except Exception:
                            pass
                    if tags:
                        return tags, source_url or root, groups

            except Exception as e:
                self.log(f"    {label} {engine} API error: {e}")

        # Last chance: engine-generic strict HTML search, still automatic and
        # still exact-MD5 only.
        tags, src, groups = self._engine_html_fallback_by_md5(site, md5)
        if tags:
            return tags, src, groups
        return [], "", empty_tag_groups()

    def md5_lookup_all(self, md5):
        """Run exact MD5 lookup across every enabled configured site.

        Site configs are normalized into engine families:
            site config -> engine adapter -> normalize posts -> exact MD5 guard

        The per-file request cache prevents the same HTML/API page from being
        fetched repeatedly while API/XML/HTML fallbacks are tried.
        """
        all_tags = []
        all_groups = []
        sources = []

        old_cache_enabled = getattr(self, "_lookup_cache_enabled", False)
        old_request_cache = getattr(self, "_request_cache", {})
        self._lookup_cache_enabled = True
        self._request_cache = {}
        try:
            for site in self._all_enabled_site_configs():
                label = self._site_label(site)
                try:
                    self.log(f"  MD5 CHECK: {label}")
                    tags, source, groups = self.engine_by_md5(site, md5)
                    if tags:
                        self.log(f"  MD5 MATCH: {label} {redact_sensitive_url(source)}")
                        all_tags += tags
                        sources.append(f"md5 {label} {source}")
                        if groups:
                            all_groups.append(groups)
                except Exception as e:
                    self.log(f"  MD5 ERROR: {label}: {e}")
        finally:
            self._lookup_cache_enabled = old_cache_enabled
            self._request_cache = old_request_cache

        return unique_keep_order(all_tags), sources, all_groups

    def _custom_site_root(self, site):
        """Return clean site root for custom boorus.

        Users sometimes save base_url as /posts, /post, /index.php, or even a
        copied API URL.  Building /posts.json on top of that creates broken URLs
        such as /posts/posts.json and the server returns ordinary HTML.  Always
        normalize custom roots before MD5/API/HTML fallback.
        """
        site = site if isinstance(site, dict) else {}
        raw = (site.get("base_url") or "").strip().rstrip("/")
        if not raw:
            raw = "https://" + (site.get("domain") or "").strip().strip("/")
        if raw and not raw.startswith(("http://", "https://")):
            raw = "https://" + raw
        try:
            u = urlparse(raw)
            scheme = u.scheme or "https"
            host = (u.netloc or u.path.split("/", 1)[0]).lower().replace("www.", "")
            path = u.path or ""
            # Remove common copied paths.  Keep only the domain root.
            bad_parts = ("/posts", "/post", "/index.php", "/api", "/dapi")
            if any(path.lower().startswith(x) for x in bad_parts):
                path = ""
            root = f"{scheme}://{host}{path}".rstrip("/")
            return root or raw
        except Exception:
            return raw

    def _custom_response_posts(self, r, label):
        """Parse JSON/XML response into post dictionaries/lists safely."""
        posts = self._posts_from_dapi_response(r, label)
        if posts:
            return posts
        try:
            data = safe_json_response(r, label)
            return self._post_dicts_from_data(data)
        except Exception:
            return []

    def _try_custom_post_apis(self, site, md5):
        """Try several Danbooru/Gelbooru-compatible MD5 endpoints.

        This especially helps ATF/Danbooru forks where a user-selected md5_api
        value may be stale, or base_url was saved as a page URL.  Returns
        (posts, first_response_for_diagnostics).
        """
        base = self._custom_site_root(site)
        host = urlparse(base).netloc.lower().replace("www.", "")
        s = self.session_for_host(host)
        label = site.get("name") or host
        auth = self.custom_auth_params(site)
        # Auto-detect API style for smarter ordering
        style = self.detect_booru_api_style(base)
        if style == "danbooru":
            attempts = [
                (f"{base}/posts.json", {"tags": f"md5:{md5}", "limit": 1, **auth}),
                (f"{base}/posts.json", {"md5": md5, "limit": 1, **auth}),
            ]
        elif style == "moebooru":
            attempts = [
                (f"{base}/post/index.json", {"tags": f"md5:{md5}", "limit": 1, **auth}),
                (f"{base}/posts.json", {"tags": f"md5:{md5}", "limit": 1, **auth}),
            ]
        elif style == "gelbooru":
            attempts = [
                (f"{base}/index.php", {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": f"md5:{md5}", **auth}),
            ]
        elif style == "szurubooru":
            attempts = [
                (f"{base}/api/posts", {"query": f"md5:{md5}", "limit": 1, **auth}),
            ]
        else:
            # Unknown: try all common patterns
            attempts = [
                (f"{base}/posts.json", {"tags": f"md5:{md5}", "limit": 1, **auth}),
                (f"{base}/posts.json", {"search[md5]": md5, "limit": 1, **auth}),
                (f"{base}/posts.json", {"md5": md5, "limit": 1, **auth}),
                (f"{base}/post/index.json", {"tags": f"md5:{md5}", "limit": 1, **auth}),
                (f"{base}/index.php", {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": f"md5:{md5}", **auth}),
                (f"{base}/api/posts", {"query": f"md5:{md5}", "limit": 1, **auth}),
            ]
        first_response = None
        for api, params in attempts:
            try:
                r = self._atf_get(s, api, host, params=params, timeout=self.timeout, headers={"Accept": "application/json, application/xml, text/xml, */*"})
                if first_response is None:
                    first_response = r
                posts = self._custom_response_posts(r, label)
                if posts:
                    self.log(f"  CUSTOM API MATCH [{label}]: {redact_sensitive_url(r.url)}")
                    try:
                        debug_dir = Path.cwd() / "Local_Booru_Output" / "debug" if debug_enabled(self.settings) else None
                        debug_dir.mkdir(parents=True, exist_ok=True) if debug_dir else None
                        safe_md5 = ""
                        try:
                            safe_md5 = str(params.get("tags", "")).replace("md5:", "").replace("%3A", "_").replace(":", "_")
                        except Exception:
                            safe_md5 = "custom"
                        raw_file = (debug_dir / f"custom_api_match_{label}_{safe_md5}.json") if debug_dir else None
                        raw_file.write_text(r.text or "", encoding="utf-8", errors="ignore") if raw_file else None
                        self.log(f"  CUSTOM API RAW BACKUP: {raw_file}") if raw_file else None
                    except Exception as backup_e:
                        self.log(f"  CUSTOM API RAW BACKUP ERROR: {type(backup_e).__name__}: {backup_e}")
                    return posts, r
                ct = (r.headers.get("content-type") or "").lower()
                # If this endpoint clearly returned HTML, continue through all
                # attempts before falling back to HTML search.
                if "html" not in ct and (r.text or "").strip() not in ("", "[]", "{}"): 
                    body = (r.text or "")[:80].replace("\n", " ").replace("\r", " ")
                    self.log(f"  CUSTOM API EMPTY [{label}]: {r.status_code} {ct} {body!r}")
            except Exception as e:
                self.log(f"  CUSTOM API ERROR [{label}]: {e}")
        return [], first_response

    def detect_booru_api_style(self, base_url: str) -> str:
        """Probe a booru URL to detect its API style.

        Returns one of: "danbooru", "gelbooru", "moebooru", "szurubooru",
        "philomena", "unknown".

        Probes /posts.json, /post/index.json, /api/posts, /tags.json etc.
        Caches result in session so we don't probe on every image.
        """
        cache_key = f"__booru_style_{base_url}"
        cached = getattr(self, "_booru_style_cache", {}).get(base_url)
        if cached:
            return cached
        if not hasattr(self, "_booru_style_cache"):
            self._booru_style_cache = {}

        host = urlparse(base_url).netloc.lower().replace("www.", "")
        s = self.session_for_host(host)
        style = "unknown"

        probes = [
            # (url_suffix, param, style_if_ok)
            ("/posts.json",       {"limit": 1},              "danbooru"),
            ("/post/index.json",  {"limit": 1},              "moebooru"),
            ("/index.php",        {"page": "dapi", "s": "post", "q": "index", "json": "1", "limit": "1"}, "gelbooru"),
            ("/api/posts",        {"limit": 1},              "szurubooru"),
            ("/api.php",          {"request": "comments", "limit": 1}, "philomena"),
        ]

        for suffix, params, candidate in probes:
            try:
                url = base_url.rstrip("/") + suffix
                r = s.get(url, params=params, timeout=8, headers={"Accept": "application/json"})
                if r.status_code == 200:
                    ct = r.headers.get("content-type", "").lower()
                    if "json" in ct or r.text.strip().startswith(("[", "{")):
                        style = candidate
                        self.log(f"  BOORU DETECT: {host} → {style}")
                        break
            except Exception:
                pass

        self._booru_style_cache[base_url] = style
        return style

    def custom_html_fallback_by_md5(self, site, md5, first_response=None):
        """HTML fallback for Danbooru-like custom sites such as ATF.

        Some sites return ordinary HTML for their API endpoints even with a
        valid login cookie.  ATF is Danbooru-based, so a search page like
        /posts?tags=md5:<hash> can still expose a post link and/or grouped tag
        sidebar.  This fallback avoids treating that as a hard failure.
        """
        base = self._custom_site_root(site)
        if not base:
            return [], "", empty_tag_groups()

        parsed = urlparse(base)
        host = parsed.netloc.lower().replace("www.", "")
        s = self.session_for_host(host)

        if "allthefallen" in host and self.settings.get("strict_atf_md5", True):
            # ATF search HTML can expose unrelated/nearby posts. Do not use it as
            # a source of truth for MD5 lookup. Only real JSON/API post matches are accepted.
            self.log(f"  ATF STRICT: HTML fallback disabled for md5={md5}")
            return [], "", empty_tag_groups()

        urls = []
        # First, if the failed API response was actually a search/post HTML page,
        # try to recover tags from it before sending more requests.
        if first_response is not None:
            try:
                html0 = first_response.text or ""
                if html0.lstrip().lower().startswith("<!doctype") or "<html" in html0.lower():
                    groups0 = self.booru_groups_from_html(html0)
                    tags0 = groups_to_tags(groups0)
                    if tags0:
                        return tags0, str(first_response.url), groups0
            except Exception:
                pass

        # ATF/Danbooru direct MD5 endpoint from PostsController#index:
        # if params[:md5].present? it finds Post.find_by!(md5: params[:md5])
        # and redirects to the post page for HTML. This is more reliable than
        # tags=md5:HASH, which only returns a search page/sidebar.
        urls.append(f"{base}/posts?md5={md5}")
        urls.append(f"{base}/posts.json?md5={md5}")

        # Search fallbacks.
        urls.append(f"{base}/posts?q=md5%3A{md5}")
        urls.append(f"{base}/posts?tags=md5:{md5}")
        urls.append(f"{base}/posts?tags={md5}")
        urls.append(f"{base}/post?tags=md5:{md5}")
        urls.append(f"{base}/index.php?page=post&s=list&tags=md5:{md5}")
        urls.append(f"{base}/index.php?page=post&s=list&tags={md5}")

        seen = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            try:
                r = self._atf_get(s, url, host, timeout=self.timeout, headers={"Accept": "text/html,application/xhtml+xml,*/*"})
                html_text = r.text or ""
                self.log(f"  CUSTOM HTML TRY [{site.get('name') or host}]: {redact_sensitive_url(r.url)} status={r.status_code}")
                if r.status_code >= 400 or not html_text:
                    continue

                # Prefer real post page over search-page sidebar tags.
                # ATF search page has only incomplete flat tags; grouped tags are on /posts/ID.
                post_url = self._first_post_link_from_html(html_text, host)
                if not post_url:
                    # Danbooru-style post links: /posts/123
                    soup = BeautifulSoup(html_text, "html.parser")
                    a = soup.select_one('a[href^="/posts/"], a[href*="/posts/"]')
                    if a:
                        href = a.get("href", "")
                        if href.startswith("/"):
                            post_url = f"{parsed.scheme or 'https'}://{host}{href}"
                        elif href.startswith("http"):
                            post_url = href

                if post_url:
                    pr = self._atf_get(s, post_url, host, timeout=self.timeout)
                    phtml = pr.text or ""
                    pgroups = self.booru_groups_from_html(phtml)
                    ptags = groups_to_tags(pgroups)
                    if ptags:
                        self.log(f"  CUSTOM HTML MATCH [{site.get('name') or host}] {post_url}")
                        return ptags, post_url, pgroups

                    # Last chance: meta keywords on post pages.
                    soup = BeautifulSoup(phtml, "html.parser")
                    kw_tags = []
                    for meta in soup.select("meta[name='keywords']"):
                        for t in re.split(r"[,\s]+", meta.get("content", "")):
                            t = normalize_tag(t)
                            if t and t.lower() not in {"posts", "post", "all", "image", "images"}:
                                kw_tags.append(t)
                    if kw_tags:
                        groups = empty_tag_groups()
                        groups["general"] = unique_keep_order(kw_tags)
                        self.log(f"  CUSTOM HTML PARTIAL [{site.get('name') or host}] {post_url}")
                        return groups_to_tags(groups), post_url, groups

                # If no post link is visible, only then use page tags.
                groups = self.booru_groups_from_html(html_text)
                tags = groups_to_tags(groups)
                if tags and "verification" not in html_text[:1000].lower():
                    self.log(f"  CUSTOM HTML MATCH [{site.get('name') or host}] {url}")
                    return tags, url, groups
            except Exception as e:
                self.log(f"  CUSTOM HTML ERROR [{site.get('name') or host}]: {e}")

        return [], "", empty_tag_groups()

    def _is_atf_site(self, site, host=""):
        try:
            site = site if isinstance(site, dict) else {}
            text = " ".join([
                str(site.get("name") or ""),
                str(site.get("domain") or ""),
                str(site.get("base_url") or ""),
                str(site.get("url") or ""),
                str(host or ""),
            ]).lower()
            return "allthefallen" in text or " atf" in (" " + text) or text.strip() == "atf"
        except Exception:
            return False

    def _post_md5_value(self, post):
        """Extract a verifiable MD5 from any booru/custom post shape.

        This is intentionally site-agnostic. New/custom sites must not need a
        new one-off parser just to confirm exact file identity. We accept MD5
        only when it is attached to a trustworthy hash-ish key or a file/media
        URL field, not an arbitrary 32-hex number from unrelated text.
        """
        if not isinstance(post, dict):
            return ""

        md5_re = re.compile(r"^[0-9a-fA-F]{32}$")
        any_md5_re = re.compile(r"([0-9a-fA-F]{32})")

        def norm(v):
            if isinstance(v, str):
                v = v.strip().lower()
                if md5_re.fullmatch(v):
                    return v
            return ""

        # Direct/common fields.
        for key in (
            "md5", "file_md5", "image_md5", "hash", "file_hash",
            "media_md5", "content_md5", "original_md5", "checksum"
        ):
            got = norm(post.get(key))
            if got:
                return got

        # Nested common file/media objects.
        for obj_key in ("file", "media", "image", "sample", "preview", "asset", "original"):
            obj = post.get(obj_key)
            if isinstance(obj, dict):
                for key in ("md5", "hash", "file_md5", "image_md5", "checksum"):
                    got = norm(obj.get(key))
                    if got:
                        return got
                for key in ("url", "file_url", "download_url", "original_url", "sample_url", "preview_url"):
                    got = self._md5_from_urlish(obj.get(key))
                    if got:
                        return got

        # URL-ish fields at top level. Many booru APIs omit a separate md5 but
        # use md5 as the media filename.
        for key in (
            "file_url", "large_file_url", "source", "source_url", "url",
            "media_url", "image_url", "sample_url", "preview_url",
            "jpeg_url", "download_url", "original_url"
        ):
            got = self._md5_from_urlish(post.get(key))
            if got:
                return got

        # Some custom APIs store attributes under attrs/attributes.
        for obj_key in ("attrs", "attributes", "properties"):
            obj = post.get(obj_key)
            if isinstance(obj, dict):
                for key, val in obj.items():
                    lk = str(key).lower()
                    if "md5" in lk or "hash" in lk or "checksum" in lk:
                        got = norm(val)
                        if got:
                            return got
                    if "url" in lk or "file" in lk or "source" in lk:
                        got = self._md5_from_urlish(val)
                        if got:
                            return got

        return ""

    def _md5_from_urlish(self, value):
        """Return 32-hex md5 from a media URL/path-like value when present."""
        if not isinstance(value, str):
            return ""
        text = html.unescape(value.strip())
        if not text:
            return ""
        try:
            path = unquote(urlparse(text).path or text)
        except Exception:
            path = text
        base = path.rsplit("/", 1)[-1]
        # Accept sample_<md5>.jpg, <md5>_6.jpg, <md5>.png, etc.
        for m in re.finditer(r"([0-9a-fA-F]{32})", base):
            return m.group(1).lower()
        return ""

    def _verify_custom_post_md5(self, site, post, wanted_md5, base, host):
        """Return True only when the remote post explicitly confirms the same MD5.

        Custom boorus sometimes return a post for an MD5 query even when the
        returned post is not the exact local file. Tags are safe to apply only
        if the returned JSON, or the post's own JSON endpoint, contains the
        exact same md5 as the local file.
        """
        wanted = (wanted_md5 or "").strip().lower()
        got = self._post_md5_value(post)
        atf_site = self._is_atf_site(site, host)
        post_id = post.get("id") if isinstance(post, dict) else None

        # ATF is useful, but its search endpoint has already produced unsafe
        # matches in real use. For ATF, never trust the search response alone:
        # require the individual post JSON endpoint to confirm the exact MD5.
        if got and got != wanted:
            self.log(f"  CUSTOM MD5 REJECT: remote md5 differs local={wanted} remote={got}")
            return False
        if got and got == wanted and not atf_site:
            return True
        if atf_site and got == wanted:
            self.log("  ATF STRICT: search response md5 matches, verifying post JSON too")
        if not post_id:
            self.log("  CUSTOM MD5 REJECT: no post id and no remote md5 field")
            return False

        try:
            s = self.session_for_host(host)
            auth = self.custom_auth_params(site)
            urls = [
                (f"{base}/posts/{post_id}.json", auth),
                (f"{base}/posts.json", {"search[id]": post_id, "limit": 1, **auth}),
                (f"{base}/posts.json", {"tags": f"id:{post_id}", "limit": 1, **auth}),
            ]
            for url, params in urls:
                r = self._atf_get(
                    s,
                    url,
                    host,
                    params=params,
                    timeout=self.timeout,
                    headers={"Accept": "application/json, */*"},
                )
                posts = self._custom_response_posts(r, site.get("name") or host)
                for p in posts:
                    got2 = self._post_md5_value(p)
                    if got2:
                        if got2 == wanted:
                            self.log(f"  CUSTOM MD5 VERIFIED: post={post_id}")
                            return True
                        self.log(f"  CUSTOM MD5 REJECT: post={post_id} remote md5 differs local={wanted} remote={got2}")
                        return False
        except Exception as e:
            self.log(f"  CUSTOM MD5 VERIFY ERROR: {type(e).__name__}: {e}")

        self.log(f"  CUSTOM MD5 REJECT: post={post_id} has no verifiable md5")
        return False

    def custom_by_md5(self, site, md5):
        """
        Custom booru MD5 lookup.

        Older versions used r.json() directly here. That broke custom sites such
        as ATF when they returned Gelbooru/Danbooru XML, empty JSON, HTML login
        pages, or Cloudflare pages. Keep this function tolerant: non-JSON should
        not crash the whole lookup, and XML DAPI responses should still work.
        """
        base = self._custom_site_root(site)
        host = urlparse(base).netloc.lower().replace("www.", "")

        # Try multiple common API shapes instead of trusting the saved md5_api
        # blindly. This fixes custom sites after settings migrations and avoids
        # /posts/posts.json mistakes when base_url was copied from a page.
        posts, r = self._try_custom_post_apis(site, md5)

        if not posts:
            # HTML fallback for Danbooru-like custom boorus such as ATF.
            html_tags, html_source, html_groups = self.custom_html_fallback_by_md5(site, md5, first_response=r)
            if html_tags:
                return unique_keep_order(html_tags), html_source, html_groups

            # Avoid noisy "Expecting value" spam. Usually this is HTML login,
            # Cloudflare, empty response, or a disabled/broken API.
            ct = r.headers.get("content-type", "")
            body = (r.text or "")[:120].replace("\n", " ").replace("\r", " ")
            # ATF post-view fallback:
            # /posts?tags=md5:HASH shows incomplete flat tags, but /posts/ID has grouped tags.
            try:
                site_name = str(site.get("name") or host or "")
                base_url = str(site.get("base_url") or site.get("url") or "https://booru.allthefallen.moe")

                if "allthefallen" in base_url or "ATF" in site_name.upper():
                    self.log("  ATF STRICT: post-view recover disabled without verified JSON md5")
                    view_url = ""
                    html_text = ""

                    if view_url:
                        if self.log:
                            self.log(f"  ATF VIEW RECOVER: {redact_sensitive_url(view_url)}")

                        view_resp = self.session.get(
                            view_url,
                            timeout=self.timeout
                        )

                        if view_resp.status_code == 200:
                            atf_tags, atf_groups = atf_parse_post_view_html(
                                view_resp.text
                            )

                            if atf_tags:
                                if self.log:
                                    self.log(f"  ATF TAGS FROM VIEW: {len(atf_tags)}")
                                return unique_keep_order(atf_tags), view_url, atf_groups

            except Exception as atf_e:
                if self.log:
                    self.log(
                        f"  ATF VIEW RECOVER ERROR: "
                        f"{type(atf_e).__name__}: {atf_e}"
                    )

            if self.log and ("allthefallen" in str(site.get("base_url") or site.get("url") or "") or "ATF" in str(site.get("name") or "").upper()):
                self.log("  ATF VIEW NOT FOUND IN SEARCH HTML")
                try:
                    dump_dir = Path.cwd() / "Local_Booru_Output" / "debug"
                    dump_dir.mkdir(parents=True, exist_ok=True)
                    dump_file = (dump_dir / f"atf_search_dump_{md5}.html") if dump_dir else None
                    dump_file.write_text(r.text or "", encoding="utf-8", errors="ignore") if dump_file else None
                    self.log(f"  ATF HTML DUMP: {dump_file}") if dump_file else None
                except Exception as dump_e:
                    self.log(f"  ATF HTML DUMP ERROR: {type(dump_e).__name__}: {dump_e}")

            if self.log:
                self.log(
                    f"  CUSTOM NO JSON/POSTS [{site.get('name') or host}]: "
                    f"status={r.status_code} ct={ct} body={body!r}"
                )

            return [], "", empty_tag_groups()

        post = posts[0]

        if not self._verify_custom_post_md5(site, post, md5, base, host):
            self.log("  CUSTOM MATCH REJECTED: exact remote MD5 was not confirmed")
            return [], "", empty_tag_groups()

        tags = self.extract_tags_from_post(post) or self._tags_from_post_dict(post)

        if isinstance(post, dict):
            groups = {
                "artist": str(post.get("tag_string_artist", "") or "").split(),
                "character": str(post.get("tag_string_character", "") or "").split(),
                "copyright": str(post.get("tag_string_copyright", "") or "").split(),
                "general": str(post.get("tag_string_general", "") or post.get("tags", "") or "").split(),
                "meta": str(post.get("tag_string_meta", "") or "").split(),
            }
        else:
            groups = empty_tag_groups()

        # If custom API is Gelbooru-style and groups are missing, keep flat tags
        # in general instead of losing them.
        if not groups_to_tags(groups) and tags:
            groups = empty_tag_groups()
            groups["general"] = tags

        post_id = post.get("id") if isinstance(post, dict) else None
        source = str(getattr(r, "url", "") or base)

        if post_id:
            # Danbooru/ATF style.
            if "/posts" in source or "allthefallen" in base:
                source = f"{base}/posts/{post_id}"
            # Gelbooru-like old style.
            elif "index.php" in source:
                source = f"{base}/index.php?page=post&s=view&id={post_id}"
            # Very old custom fallback.
            else:
                source = f"{base}/posts/{post_id}"

        return unique_keep_order(tags), source, groups

    def _post_dicts_from_data(self, data):
        """Return only post dictionaries from common booru API shapes.

        This is the central normalizer for all MD5-capable sites.  Do not let
        callers iterate raw JSON directly: APIs may return dict, list, nested
        {"posts": [...]}, {"post": {...}}, {"data": [...]}, or malformed legacy
        strings.  Returning only dicts prevents the recurring
        "'str' object has no attribute 'get'" class of bugs across every site.
        """
        out = []

        def add(value):
            if isinstance(value, dict):
                out.append(value)

        def walk(value, depth=0):
            if depth > 4:
                return
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        out.append(item)
                return
            if not isinstance(value, dict):
                return

            # If the object itself looks like a post, keep it.
            if any(k in value for k in (
                "id", "md5", "file_md5", "image_md5", "hash",
                "tags", "tag_string", "tag_string_general", "file"
            )):
                add(value)
                return

            for key in ("posts", "post", "results", "items", "data", "response"):
                child = value.get(key)
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)

        walk(data)
        return [p for p in out if isinstance(p, dict)]

    def _posts_from_dapi_response(self, r, site_name="site"):
        """Parse Danbooru/Gelbooru/e621/DAPI JSON or XML into post dicts.

        All built-in and custom MD5 search paths should go through this method
        or _post_dicts_from_data(). It intentionally never returns strings or
        mixed values.
        """
        try:
            data = safe_json_response(r, site_name)
            posts = self._post_dicts_from_data(data)
            if posts:
                return posts
        except Exception as e:
            # HTML/login/Cloudflare/non-json is normal for some sources; do not
            # crash MD5 search, just let caller try the next endpoint.
            try:
                if self.log:
                    self.log(f"    {site_name}: JSON/DAPI parse skipped: {e}")
            except Exception:
                pass

        try:
            soup = BeautifulSoup(getattr(r, "text", "") or "", "xml")
            posts = []
            for p in soup.find_all("post"):
                attrs = dict(getattr(p, "attrs", {}) or {})
                if attrs:
                    posts.append(attrs)
            return [p for p in posts if isinstance(p, dict)]
        except Exception:
            return []

    def _tags_from_post_dict(self, post):
        """Extract a flat tag list from common booru/custom post dictionaries."""
        if not isinstance(post, dict):
            return []
        tags = []

        def add(value):
            if isinstance(value, str):
                tags.extend(value.replace(",", " ").split())
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        tags.append(item)
                    elif isinstance(item, dict):
                        # Some APIs return [{"name": "..."}]
                        for k in ("name", "tag", "value"):
                            v = item.get(k)
                            if isinstance(v, str):
                                tags.append(v)
                                break
            elif isinstance(value, dict):
                for group in value.values():
                    add(group)

        # Flat fields.
        for key in (
            "tags", "tag_string", "tag_string_general", "tag_string_character",
            "tag_string_copyright", "tag_string_artist", "tag_string_meta",
            "tag_string_species", "tag_string_invalid", "tag_string_lore"
        ):
            add(post.get(key))

        # e621/Danbooru-like nested tags.
        add(post.get("tags"))

        return unique_keep_order([normalize_tag(t) for t in tags if normalize_tag(t)])

    def _groups_from_post_dict_general(self, post):
        """Build artist/character/copyright/general/meta groups from any post dict."""
        groups = empty_tag_groups()
        if not isinstance(post, dict):
            return groups

        split_mapping = {
            "artist": ["tag_string_artist", "tags_artist", "artist_tags"],
            "character": ["tag_string_character", "tags_character", "character_tags"],
            "copyright": ["tag_string_copyright", "tags_copyright", "copyright_tags"],
            "general": ["tag_string_general", "tags_general", "general_tags"],
            "meta": ["tag_string_meta", "tags_meta", "tags_metadata", "metadata_tags"],
        }
        for group, keys in split_mapping.items():
            for key in keys:
                val = post.get(key)
                if isinstance(val, str):
                    add_tags_to_groups(groups, group, val.split())
                elif isinstance(val, list):
                    add_tags_to_groups(groups, group, val)

        tag_map = post.get("tags")
        if isinstance(tag_map, dict):
            category_map = {
                "artist": "artist",
                "artists": "artist",
                "character": "character",
                "characters": "character",
                "copyright": "copyright",
                "copyrights": "copyright",
                "general": "general",
                "tag": "general",
                "tags": "general",
                "meta": "meta",
                "metadata": "meta",
                "species": "meta",
                "invalid": "meta",
                "lore": "meta",
            }
            for key, val in tag_map.items():
                group = category_map.get(str(key).lower(), "general")
                if isinstance(val, str):
                    add_tags_to_groups(groups, group, val.split())
                elif isinstance(val, list):
                    add_tags_to_groups(groups, group, val)

        if not groups_to_tags(groups):
            groups["general"] = self._tags_from_post_dict(post)
        return groups

    def _absolute_site_url(self, host, href):
        href = str(href or "").strip()
        if not href:
            return ""
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("/"):
            return f"https://{host}" + href
        if href.startswith("index.php"):
            return f"https://{host}/" + href
        if href.startswith("http://") or href.startswith("https://"):
            return href
        return ""

    def _is_strict_post_url(self, url):
        """Return True only for concrete post pages, never search/random/list URLs."""
        try:
            u = urlparse(str(url or ""))
            path = (u.path or "").lower()
            query = (u.query or "").lower()
            full = (path + "?" + query).lower()
            # Search/list/random pages are never valid tag sources even if the
            # wanted MD5 appears in their query string. This bug previously
            # allowed /posts/random?tags=md5:<hash> to produce 3 garbage tags.
            if any(x in full for x in ("/posts/random", "/post/random", "s=list", "page=post&s=list", "tags=md5", "search[md5]")):
                return False
            if re.search(r"/posts?/\d+(?:$|[/?#])", path + "/"):
                return True
            if re.search(r"/post/show/\d+(?:$|[/?#])", path + "/"):
                return True
            if "page=post" in query and "s=view" in query and re.search(r"(?:^|&)id=\d+(?:&|$)", query):
                return True
        except Exception:
            return False
        return False

    def _first_post_link_from_html(self, html, host):
        soup = BeautifulSoup(html or "", "html.parser")
        selectors = [
            'a[href*="page=post"][href*="s=view"][href*="id="]',
            'a[href*="/index.php?page=post"][href*="id="]',
            'a[href^="/posts/"]',
            'a[href*="/posts/"]',
            'a[href*="/post/show/"]',
        ]

        for sel in selectors:
            for link in soup.select(sel):
                href = self._absolute_site_url(host, link.get("href", ""))
                if href and self._is_strict_post_url(href):
                    return href

        # ATF post cards render as <article id="post_123">...
        for article in soup.select('article[id^="post_"], div[id^="post_"]'):
            raw_id = article.get("id", "")
            m = re.search(r"post[_-](\d+)", raw_id)
            if m:
                return f"https://{host}/posts/{m.group(1)}"

        # data-post-id / data-id fallbacks
        for el in soup.find_all(True):
            attrs = getattr(el, "attrs", {}) or {}
            for key, val in attrs.items():
                k = str(key).lower()
                v = str(val)
                if "post" in k and "id" in k:
                    m = re.search(r"\d{2,}", v)
                    if m:
                        return f"https://{host}/posts/{m.group(0)}"

        # Raw HTML fallback.
        for pat in [
            r"/posts/(\d+)",
            r"id=[\"']post[_-](\d+)[\"']",
            r"data-post-id=[\"'](\d+)[\"']",
        ]:
            m = re.search(pat, html or "", flags=re.I)
            if m:
                return f"https://{host}/posts/{m.group(1)}"

        return ""



    def _html_explicit_md5_value(self, html, wanted_md5=""):
        """Extract a trustworthy md5 from a booru post/search HTML page.

        This is deliberately conservative. It only returns a hash when the page
        text contains an explicit md5/hash label or a media/file URL whose base
        filename contains a 32-hex hash. It is used only as a verification guard
        before applying tags from HTML-only fallbacks.
        """
        text = html or ""
        wanted = (wanted_md5 or "").strip().lower()
        patterns = [
            r"\bmd5\b\s*[:=]\s*[\"']?([0-9a-fA-F]{32})",
            r"\bhash\b\s*[:=]\s*[\"']?([0-9a-fA-F]{32})",
            r"data-md5\s*=\s*[\"']([0-9a-fA-F]{32})[\"']",
            r"data-hash\s*=\s*[\"']([0-9a-fA-F]{32})[\"']",
        ]
        for pat in patterns:
            for m in re.finditer(pat, text, flags=re.I):
                h = m.group(1).lower()
                if not wanted or h == wanted:
                    return h

        # Filename/url fallbacks. Many booru mirrors store media as
        # .../<md5>.jpg, sample_<md5>.jpg, or <md5>_6.jpg.
        url_pats = [
            r"https?://[^\"'<>\s]+",
            r"(?:file_url|source|src|href)\s*=\s*[\"']([^\"']+)[\"']",
        ]
        seen = []
        for pat in url_pats:
            for m in re.finditer(pat, text, flags=re.I):
                u = m.group(1) if m.lastindex else m.group(0)
                if not u:
                    continue
                low = u.lower()
                if not any(ext in low for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm")):
                    continue
                seen.append(u)
        for u in seen:
            base = unquote(urlparse(u).path.rsplit("/", 1)[-1])
            for m in re.finditer(r"([0-9a-fA-F]{32})", base):
                h = m.group(1).lower()
                if not wanted or h == wanted:
                    return h

        # Do NOT accept a bare occurrence of the wanted hash anywhere in HTML.
        # Search/random/list pages often contain the hash in the query URL, which
        # is not proof that the page is a concrete post or that its file matches.
        return ""

    def _verify_html_md5(self, site_name, html, wanted_md5):
        wanted = (wanted_md5 or "").strip().lower()
        got = self._html_explicit_md5_value(html, wanted)
        if got and got == wanted:
            return True
        if got and got != wanted:
            self.log(f"    {site_name} HTML MD5 REJECT: local={wanted} remote={got}")
        else:
            self.log(f"    {site_name} HTML MD5 REJECT: no explicit md5 in HTML")
        return False

    def _html_tags_strict_by_md5(self, site_name, url, wanted_md5):
        """Fetch a concrete post page and return tags only after HTML MD5 verification."""
        try:
            if not self._is_strict_post_url(url):
                self.log(f"    {site_name} HTML fallback rejected non-post URL: {redact_sensitive_url(url)}")
                return [], "", empty_tag_groups()
            host = urlparse(url).netloc.lower().replace("www.", "") or site_name
            session = self.session_for_host(host)
            html = self._http_get_cached(
                session,
                url,
                timeout=self.timeout,
                headers={"Accept": "text/html,application/xhtml+xml,*/*"},
            ).text
            if not self._verify_html_md5(site_name, html, wanted_md5):
                return [], "", empty_tag_groups()
            groups = self.booru_groups_from_html(html)
            tags = groups_to_tags(groups)
            if not tags:
                tags = self.tags_from_url(url)
                groups = self._categorize_flat_tags(site_name, tags)
            if tags:
                return tags, url, groups
        except Exception as e:
            self.log(f"    {site_name} HTML strict fallback error: {e}")
        return [], "", empty_tag_groups()

    def _html_search_strict_by_md5(self, site_name, base_url, wanted_md5):
        """Strict HTML fallback for boorus whose DAPI is blocked/broken.

        It tries md5:<hash> and raw <hash> searches, finds a post link, then
        verifies the post HTML contains the same md5 before taking any tags.
        """
        session = self.session_for_host(site_name)
        searches = [
            {"page": "post", "s": "list", "tags": f"md5:{wanted_md5}"},
            {"page": "post", "s": "list", "tags": wanted_md5},
        ]
        for params in searches:
            try:
                r = self._http_get_cached(
                    session,
                    base_url,
                    params=params,
                    timeout=self.timeout,
                    headers={"Accept": "text/html,application/xhtml+xml,*/*"},
                )
                html = r.text or ""
                link = self._first_post_link_from_html(html, site_name)
                if not link:
                    continue
                tags, src, groups = self._html_tags_strict_by_md5(site_name, link, wanted_md5)
                if tags:
                    self.log(f"    {site_name} HTML STRICT MATCH: {redact_sensitive_url(src)}")
                    return tags, src, groups
            except Exception as e:
                self.log(f"    {site_name} HTML search error: {e}")
        return [], "", empty_tag_groups()

    def _verify_builtin_post_md5(self, site_name, post, wanted_md5):
        """Strict guard for built-in MD5 searches.

        One root bug after the JSON/network refactor was that every site used
        its own idea of where the hash lives. Danbooru/Gelbooru usually expose
        ``md5`` at the top level, while e621 stores it as ``file.md5``. Use the
        same central extractor as custom boorus so exact-match validation works
        consistently and does not reject real e621 posts.
        """
        wanted = (wanted_md5 or "").strip().lower()
        got = self._post_md5_value(post)
        if not got:
            # Keep this as a safe reject: if the API did not explicitly confirm
            # the hash, do not apply tags from a potentially wrong post.
            self.log(f"    {site_name} MD5 REJECT: response has no explicit md5")
            return False
        if got != wanted:
            self.log(f"    {site_name} MD5 REJECT: local={wanted} remote={got}")
            return False
        return True

    def rule34xxx_by_md5(self, md5):
        cfg = self.site_cfg("rule34.xxx")
        session = self.session_for_host("rule34.xxx")

        attempts = [
            ("api.rule34.xxx json", "https://api.rule34.xxx/index.php", True),
            ("rule34.xxx json", "https://rule34.xxx/index.php", True),
            ("api.rule34.xxx xml", "https://api.rule34.xxx/index.php", False),
            ("rule34.xxx xml", "https://rule34.xxx/index.php", False),
        ]

        for label, api, use_json in attempts:
            params = {
                "page": "dapi",
                "s": "post",
                "q": "index",
                "tags": f"md5:{md5}",
                "limit": 1,
            }
            if use_json:
                params["json"] = "1"
            params.update(self.auth_params(cfg))

            try:
                r = session.get(api, params=params, timeout=self.timeout)
                if r.status_code != 200:
                    self.log(f"    rule34.xxx {label} status {r.status_code}")
                    continue

                posts = self._posts_from_dapi_response(r, "rule34.xxx")
                if posts:
                    p = posts[0]
                    if not self._verify_builtin_post_md5("rule34.xxx", p, md5):
                        continue
                    tags = self._tags_from_post_dict(p)
                    post_id = p.get("id")
                    if tags:
                        url = f"https://rule34.xxx/index.php?page=post&s=view&id={post_id}"
                        groups = self.grouped_tags_from_url(url)
                        return tags, url, groups
            except Exception as e:
                self.log(f"    rule34.xxx {label} error: {e}")

        tags, src, groups = self._html_search_strict_by_md5("rule34.xxx", "https://rule34.xxx/index.php", md5)
        if tags:
            return tags, src, groups
        self.log("    rule34.xxx HTML fallback skipped: no exact API MD5 confirmation")
        return [], ""

    def rule34us_by_md5(self, md5):
        cfg = self.site_cfg("rule34.us")
        session = self.session_for_host("rule34.us")

        attempts = [
            ("rule34.us json", "https://rule34.us/index.php", True),
            ("rule34.us xml", "https://rule34.us/index.php", False),
        ]

        for label, api, use_json in attempts:
            params = {
                "page": "dapi",
                "s": "post",
                "q": "index",
                "tags": f"md5:{md5}",
                "limit": 1,
            }
            if use_json:
                params["json"] = "1"
            params.update(self.auth_params(cfg))

            try:
                r = session.get(api, params=params, timeout=self.timeout)
                if r.status_code != 200:
                    self.log(f"    rule34.us {label} status {r.status_code}")
                    continue

                posts = self._posts_from_dapi_response(r, "rule34.us")
                if posts:
                    p = posts[0]
                    if not self._verify_builtin_post_md5("rule34.us", p, md5):
                        continue
                    tags = self._tags_from_post_dict(p)
                    post_id = p.get("id")
                    if tags:
                        url = f"https://rule34.us/index.php?page=post&s=view&id={post_id}"
                        groups = self.grouped_tags_from_url(url)
                        return tags, url, groups
            except Exception as e:
                self.log(f"    rule34.us {label} error: {e}")

        tags, src, groups = self._html_search_strict_by_md5("rule34.us", "https://rule34.us/index.php", md5)
        if tags:
            return tags, src, groups
        self.log("    rule34.us HTML fallback skipped: no exact API MD5 confirmation")
        return [], ""

    def danbooru_by_md5(self, md5):
        params = {"tags": f"md5:{md5}", "limit": 1}
        params.update(self.auth_params(self.site_cfg("danbooru.donmai.us")))
        s = self.session_for_host("danbooru.donmai.us")
        r = s.get("https://danbooru.donmai.us/posts.json", params=params, timeout=self.timeout)

        if r.status_code != 200:
            raise Exception(f"Danbooru status {r.status_code}: {r.text[:120]}")

        posts = self._posts_from_dapi_response(r, "danbooru")
        for p in posts:
            if not self._verify_builtin_post_md5("danbooru", p, md5):
                continue
            groups = self._groups_from_post_dict_general(p)
            tags = groups_to_tags(groups)
            if tags:
                return tags, f"https://danbooru.donmai.us/posts/{p.get('id')}", groups
        return [], ""

    def gelbooru_by_md5(self, md5):
        params = {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": f"md5:{md5}", "limit": 1}
        params.update(self.auth_params(self.site_cfg("gelbooru.com")))
        r = self.session_for_host("gelbooru.com").get("https://gelbooru.com/index.php", params=params, timeout=self.timeout)
        posts = self._posts_from_dapi_response(r, "gelbooru")
        if not posts:
            self.log("    gelbooru HTML fallback skipped: no exact API MD5 confirmation")
            return [], ""
        for p in posts:
            if not self._verify_builtin_post_md5("gelbooru", p, md5):
                continue
            post_id = p.get("id")
            groups = self.gelbooru_groups_from_post(p)
            if not groups_to_tags(groups):
                groups = self._groups_from_post_dict_general(p)
            tags = groups_to_tags(groups) or self._tags_from_post_dict(p)
            if tags:
                url = f"https://gelbooru.com/index.php?page=post&s=view&id={post_id}"
                try:
                    html_groups = self.grouped_tags_from_url(url)
                    if groups_to_tags(html_groups):
                        groups = html_groups
                        tags = groups_to_tags(groups)
                except Exception:
                    pass
                return tags, url, groups
        return [], ""

    def e621_by_md5(self, md5):
        """Exact e621 MD5 lookup through the same normalized post parser as all sites."""
        base_params = self.auth_params(self.site_cfg("e621.net"))
        session = self.session_for_host("e621.net")
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": "LocalBooru/3.0 (local archive manager; contact: local-user)",
        }

        attempts = [
            {"tags": f"md5:{md5}", "limit": 1, **base_params},
            {"tags": f"md5:{md5} status:any", "limit": 1, **base_params},
        ]

        for params in attempts:
            try:
                r = session.get(
                    "https://e621.net/posts.json",
                    params=params,
                    timeout=self.timeout,
                    headers=headers,
                )
                posts = self._posts_from_dapi_response(r, "e621")
                if not posts:
                    self.log("    e621 MD5: no posts in JSON response")
                    continue

                for p in posts:
                    if not self._verify_builtin_post_md5("e621", p, md5):
                        continue
                    groups = self._groups_from_post_dict_general(p)
                    tags = groups_to_tags(groups)
                    if tags:
                        return tags, f"https://e621.net/posts/{p.get('id')}", groups
            except Exception as e:
                self.log(f"    e621 lookup error: {e}")

        return [], ""

    def _load_saucenao_state(self):
        try:
            if self.saucenao_state_file.exists():
                data = json.loads(self.saucenao_state_file.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def _save_saucenao_state(self, data):
        try:
            self.saucenao_state_file.parent.mkdir(parents=True, exist_ok=True)
            self.saucenao_state_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _saucenao_cooldown_left(self):
        until = float(self._load_saucenao_state().get("cooldown_until", 0) or 0)
        return max(0, int(until - time.time()))

    def _set_saucenao_cooldown(self, reason="limit"):
        seconds = int(float(self.settings.get("saucenao_cooldown_seconds", 3600) or 3600))
        until = time.time() + max(60, seconds)
        self._save_saucenao_state({"cooldown_until": until, "reason": reason, "set_at": time.time()})
        self.log(f"  SAUCENAO COOLDOWN: {int(max(60, seconds)/60)} min ({reason})")

    def saucenao_search(self, img_path):
        if not self.settings.get("saucenao_api_key"):
            return []

        left = self._saucenao_cooldown_left()
        if left > 0:
            self.log(f"  SAUCENAO COOLDOWN ACTIVE: {left//60}m {left%60}s left")
            return []

        url = "https://saucenao.com/search.php"
        params = {
            "api_key": self.settings["saucenao_api_key"],
            "output_type": 2,
            "numres": 5,
            "db": 999,
        }

        r = _post_with_file(self.session, url, img_path, file_field="file",
                           extra_params=params, timeout=max(self.timeout, 60))

        if r.status_code == 429:
            self.log("  SAUCENAO 429: API limit reached")
            self._set_saucenao_cooldown("429")
            return []

        if r.status_code >= 500:
            self.log(f"  SAUCENAO SERVER ERROR {r.status_code}")
            return []

        r.raise_for_status()

        try:
            data = r.json()
        except Exception:
            self.log(f"  SAUCENAO NON-JSON: {r.text[:120]}")
            return []

        header = data.get("header", {})
        if header:
            short_rem = header.get('short_remaining')
            long_rem = header.get('long_remaining')
            self.log(
                "  SAUCENAO LIMITS: "
                f"short={short_rem} "
                f"long={long_rem}"
            )
            try:
                if int(short_rem) <= 0 or int(long_rem) <= 0:
                    self._set_saucenao_cooldown("api_limit")
            except Exception:
                pass

        return data.get("results", [])

    def saucenao_urls(self, img_path):
        urls = []
        domains = self.enabled_domains()
        for result in self.saucenao_search(img_path):
            sim = float(result.get("header", {}).get("similarity", 0))
            index_name = result.get("header", {}).get("index_name", "unknown")
            self.log(f"  SauceNAO {sim:.2f}% {index_name}")
            if sim < float(self.settings["min_similarity"]):
                continue
            for u in result.get("data", {}).get("ext_urls", []) or []:
                host = urlparse(u).netloc.lower().replace("www.", "")
                if host in domains:
                    urls.append((u, sim))
        return urls


    def iqdb_urls(self, img_path):
        """
        Fuzzy reverse-image fallback via IQDB.
        It does NOT use MD5. It uploads the image and parses result links.
        Only links whose host is enabled in settings are returned.
        """
        urls = []
        domains = self.enabled_domains()
        min_sim = float(self.settings.get("iqdb_min_similarity", 75.0))

        try:
            r = _post_with_file(self.session, "https://iqdb.org/", img_path,
                               file_field="file", extra_data={"forcegray": "on"},
                               timeout=max(self.timeout, 60))
            r.raise_for_status()
        except Exception as e:
            self.log(f"  IQDB SEARCH ERROR: {e}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.select("a[href]"):
            href = a.get("href", "").strip()
            if not href:
                continue

            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://iqdb.org" + href

            host = urlparse(href).netloc.lower().replace("www.", "")
            if href.rstrip("/") == "https://gelbooru.com":
                continue

            if "gelbooru.com" in href and "page=post" not in href:
                continue

            if href.rstrip("/") == "https://danbooru.donmai.us":
                continue

            if "danbooru.donmai.us" in href and "/posts/" not in href and "/post/show/" not in href:
                continue

            if host not in domains:
                continue

            row_text = ""
            parent = a
            for _ in range(5):
                parent = parent.parent
                if parent is None:
                    break
                row_text = parent.get_text(" ", strip=True)
                if "%" in row_text:
                    break

            sim = 100.0
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", row_text)
            if m:
                try:
                    sim = float(m.group(1))
                except Exception:
                    sim = 100.0

            if sim >= min_sim:
                urls.append((href, sim))

        seen = set()
        out = []
        for u, sim in urls:
            if u not in seen:
                seen.add(u)
                out.append((u, sim))

        for u, sim in out[:10]:
            self.log(f"  IQDB {sim:.2f}% {u}")

        return out

    def _ascii2d_parse_results(self, html: str, domains: set) -> list:
        """Parse ascii2d result page and return (url, similarity) pairs.

        ascii2d result structure:
          .item-box
            .detail-box
              h6  — artist/source info
                a  — link to post on source site
              .hash — perceptual hash

        We pick links from .detail-box h6 a that belong to known domains.
        Each result box is one match; first box is best match.
        """
        soup = BeautifulSoup(html, "html.parser")
        out = []
        seen = set()

        for i, box in enumerate(soup.select(".item-box")):
            detail = box.select_one(".detail-box")
            if not detail:
                continue
            # First h6 contains the primary source link
            for a in detail.select("h6 a[href]"):
                href = a.get("href", "").strip()
                if not href:
                    continue
                if href.startswith("//"):
                    href = "https:" + href
                if href.startswith("/"):
                    href = "https://ascii2d.net" + href
                host = urlparse(href).netloc.lower().replace("www.", "")
                if host in domains and href not in seen:
                    seen.add(href)
                    # Score: first result = best match, decrease by position
                    score = max(60.0, 100.0 - i * 5)
                    out.append((href, score))
                    self.log(f"  ASCII2D hit[{i}] {score:.0f}% {href}")
        return out

    def ascii2d_urls(self, img_path):
        domains = self.enabled_domains()
        api_key = (self.settings.get("ascii2d_api_key") or "").strip()
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Try file upload (hash search)
        hash_html = ""
        bovw_url = ""
        try:
            with img_path.open("rb") as f:
                r = _post_with_file(self.session, "https://ascii2d.net/search/file",
                                   img_path, file_field="file",
                                   timeout=max(self.timeout, 60))
            r.raise_for_status()
            hash_html = r.text
            # ascii2d returns hash search first; bovw link is in the page
            soup_tmp = BeautifulSoup(hash_html, "html.parser")
            bovw_link = soup_tmp.select_one("a[href*='/search/bovw/']")
            if bovw_link:
                bovw_path = bovw_link.get("href", "")
                if bovw_path.startswith("/"):
                    bovw_url = "https://ascii2d.net" + bovw_path
                elif bovw_path.startswith("http"):
                    bovw_url = bovw_path
            self.log(f"  ASCII2D hash search ok, bovw={bovw_url or 'none'}")
        except Exception as e:
            self.log(f"  ASCII2D SEARCH ERROR: {e}")
            return []

        out = self._ascii2d_parse_results(hash_html, domains)

        # If hash search gave no results, try bovw (color/feature search)
        if not out and bovw_url:
            try:
                r2 = self.session.get(
                    bovw_url,
                    headers=headers,
                    timeout=max(self.timeout, 45),
                    allow_redirects=True,
                )
                r2.raise_for_status()
                out = self._ascii2d_parse_results(r2.text, domains)
                if out:
                    self.log(f"  ASCII2D bovw fallback gave {len(out)} result(s)")
            except Exception as e:
                self.log(f"  ASCII2D BOVW ERROR: {e}")

        return out[:10]


    def cancelled(self):
        try:
            return bool(self.cancel_callback and self.cancel_callback())
        except Exception:
            return False

    def process_image(self, img):
        out_txt = out_sources = out_json = Path("__disabled_sidecar__")
        out_nomatch = img.with_suffix(".nomatch")

        if self.settings.get("skip_existing") and not self.settings.get("retry_nomatch"):
            already = output_processed_status(self.settings, img)
            if already:
                self.log(f"SKIP ARCHIVED ({already}): {img.name}")
                return "skip"
            if out_txt.exists() and out_txt.stat().st_size > 0:
                self.log(f"SKIP: {img.name}")
                return "skip"

        self.log(f"SEARCH: {img.name}")
        self._partial_match_found = False
        self._partial_match_reason = ""

        search_img = video_frame_image(img)
        if search_img != img:
            self.log(f"  VIDEO FRAME: {search_img.name}")

        img_phash = file_phash(search_img)
        if img_phash:
            self.log(f"  PHASH: {img_phash}")

        all_tags = []
        all_groups = []
        sources = []

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        if self.settings.get("enable_md5_lookup") and is_md5(img.stem):
            self.log(f"  TRY MD5 FROM FILENAME: {img.stem}")
            tags, srcs, groups = self.md5_lookup_all(img.stem)
            all_tags += tags
            sources += srcs
            all_groups += groups

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        if self.settings.get("enable_md5_lookup") and not all_tags:
            real = file_md5(search_img)
            self.log(f"  TRY REAL FILE MD5: {real}")
            tags, srcs, groups = self.md5_lookup_all(real)
            all_tags += tags
            sources += srcs
            all_groups += groups

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        if self.settings.get("enable_saucenao") and not all_tags:
            try:
                sauce_urls = self.saucenao_urls(search_img)
            except Exception as e:
                self.log(f"  SAUCENAO SEARCH ERROR: {e}")
                sauce_urls = []

            for url, sim in sauce_urls:
                try:
                    self.log(f"  SAUCE MATCH: {sim:.2f}% {url}")
                    tags = self.tags_from_url(url)
                    groups = self.grouped_tags_from_url(url)

                    if tags:
                        all_tags += tags
                        if groups and groups_to_tags(groups):
                            all_groups.append(groups)
                        sources.append(f"{sim:.2f}% {url}")
                        break

                except Exception as e:
                    self.log(f"  SAUCE URL ERROR: {url} {e}")

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        if self.settings.get("enable_iqdb") and not all_tags:
            self.log("  IQDB START")
            for url, sim in self.iqdb_urls(search_img):
                try:
                    self.log(f"  IQDB MATCH: {sim:.2f}% {url}")
                    tags = self.tags_from_url(url)
                    groups = self.grouped_tags_from_url(url)

                    if tags:
                        all_tags += tags
                        if groups and groups_to_tags(groups):
                            all_groups.append(groups)
                        else:
                            try:
                                host = urlparse(url).netloc.lower().replace("www.", "")
                                guessed = self._categorize_flat_tags(host, tags)
                                if guessed and groups_to_tags(guessed):
                                    all_groups.append(guessed)
                            except Exception:
                                pass
                        sources.append(f"IQDB {sim:.2f}% {url}")
                        break

                except Exception as e:
                    self.log(f"  IQDB URL ERROR: {url} {e}")

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        if self.settings.get("enable_ascii2d") and not all_tags:
            self.log("  ASCII2D START")

            for url, sim in self.ascii2d_urls(search_img):
                try:
                    self.log(f"  ASCII2D MATCH: {url}")

                    tags = self.tags_from_url(url)
                    groups = self.grouped_tags_from_url(url)

                    if tags:
                        all_tags += tags

                        if groups and groups_to_tags(groups):
                            all_groups.append(groups)

                        sources.append(f"ASCII2D {url}")

                        break

                except Exception as e:
                    self.log(f"  ASCII2D URL ERROR: {url} {e}")

        all_tags = unique_keep_order(filter_numeric_tags(all_tags, self.settings.get("ignore_numeric_tags")))

        if all_tags:
            tag_groups = merge_tag_groups(all_groups)
            if not groups_to_tags(tag_groups):
                tag_groups["general"] = all_tags

            result_status = "partial" if self._partial_match_found else "tagged"
            source_text = "\n".join(sources)
            if self._partial_match_found:
                source_text = source_text + ("\n" if source_text else "") + f"PARTIAL: {self._partial_match_reason}"

            # Write tags/source/json directly into Local_Booru_Output. Do not create
            # .tags.txt/.sources.txt beside originals anymore.
            write_sidecar_tags(self.settings, img, all_tags, source_text, tag_groups, status=result_status)

            if out_nomatch.exists():
                try:
                    out_nomatch.unlink()
                except Exception:
                    pass
            remove_nomatch(img)

            copy_result_files(self.settings, img, result_status)
            cleanup_archived_result(self.settings, img, ("nomatch",))
            if self._partial_match_found:
                self.log(f"  PARTIAL TAGS: {len(all_tags)}")
                return "partial"
            self.log(f"  TAGS: {len(all_tags)}")
            return "tagged"
        else:
            for p in [out_txt, out_sources, out_json]:
                if p.exists():
                    p.unlink()
            # NO_MATCH is now represented by output/no_match + nomatch_cache, not by
            # marker files next to the original source. Keep old .nomatch disabled.
            upsert_nomatch(img)
            cleanup_archived_result(self.settings, img, ("tagged", "partial"))
            copy_result_files(self.settings, img, "nomatch")
            self.log("  NO MATCH - no txt created")
            return "nomatch"


def extract_r34_urls_from_text(text):
    urls = []
    for m in re.finditer(r'https?://(?:www\.)?(rule34\.xxx|rule34\.us)[^\s<>"\']+', text, re.I):
        try:
            urls.append(m.group(0))
        except Exception:
            pass
    return list(dict.fromkeys(urls))



