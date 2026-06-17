import hashlib
import json
import os
import time
import mimetypes
import shutil
import re
import html
import threading
import base64
import subprocess
import urllib.request
import urllib.error
import imagehash
from PIL import Image, ImageOps
try:
    from PIL import ImageCms
except Exception:
    ImageCms = None
from pathlib import Path
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs, urljoin, unquote, urlencode

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

try:
    from core.browser_companion_api import enqueue_e621_browser_fetch as _enqueue_e621_browser_fetch
except Exception:
    _enqueue_e621_browser_fetch = None

try:
    from core.site_driver import SiteDriver as _SiteDriver
except Exception:
    _SiteDriver = None

try:
    from core.cf_bypass import get_cf_clearance as _get_cf, make_cf_session as _make_cf_session
except Exception:
    _get_cf = None; _make_cf_session = None

try:
    from core.bandwidth import wait_for_domain as _bw_wait
except Exception:
    _bw_wait = lambda url: None

from core.paths import SETTINGS_FILE, BROWSER_PROFILE_DIR, BROWSER_COOKIES_DIR, CACHE_DIR, SERVICE_OUTPUT_DIR, ERROR_LOG_FILE, ensure_output_base
from core.nomatch_db import upsert_nomatch, remove_nomatch
from core.tag_utils import normalize_tag as _shared_normalize_tag, canonical_tag_key
from core.file_safety import atomic_copy2
from core.services.media_storage_service import copy_into_managed, unlink_managed, delete_bucket_artifacts
from core.source_protection import require_managed_media_mutation, is_source_archive_path
from core.redaction import sanitize_text
GALLERY_SETTINGS_FILE = SETTINGS_FILE
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS

COPY_SUFFIX_RE = re.compile(r"\s*\((\d+)\)$")
_RULE34_HOTLINK_PLAYWRIGHT_LOCK = threading.Lock()
_E621_BROWSER_API_LOCK = threading.Lock()


def has_copy_suffix(path):
    try:
        return bool(COPY_SUFFIX_RE.search(Path(path).stem))
    except Exception:
        return False


DEFAULT_SETTINGS = {
    "root": "C:/Local_Booru_Input",
    "saucenao_api_key": "",
    "min_similarity": 85.0,
    "delay_seconds": 8.0,
    "tagger_low_power_mode": False,
    "tagger_site_interval_seconds": 1.10,
    "tagger_conveyor_window": 32,
    "tagger_background_tag_groups": True,
    "request_timeout_seconds": 30,
    "request_connect_timeout_seconds": 10,
    "request_read_timeout_seconds": 30,
    "network_retry_attempts": 3,
    "network_retry_base_delay_seconds": 1.0,
    "network_retry_max_delay_seconds": 4.0,
    "network_retry_delay_seconds": 10,
    "sqlite_passive_checkpoint_every": 500,
    "saucenao_cooldown_seconds": 3600,
    "enable_md5_lookup": True,
    "parser_real_file_hash_cache_enabled": True,
    "rule34_sha1_async_locator_enabled": True,
    "rule34_sha1_async_locator_workers": 4,
    "rule34_variant_locator_side_queue_enabled": True,
    "rule34_image_key_locator_mode": "hotlink_only",
    "rule34_image_key_hotlink_redirect_enabled": True,
    "rule34_image_key_hotlink_extensions": "png",
    "rule34_image_key_hotlink_request_timeout": 4.0,
    "rule34_image_key_hotlink_playwright_fallback": True,
    "rule34_image_key_hotlink_playwright_headless": False,
    "rule34_image_key_hotlink_playwright_timeout": 25.0,
    "rule34_image_key_bucket_probe_enabled": False,
    "rule34_image_key_bucket_probe_sequence": "",
    "rule34_image_key_bucket_probe_max": 9999,
    "rule34_image_key_bucket_probe_step": 100,
    "rule34_image_key_bucket_request_timeout": 3.0,
    "rule34_image_key_bucket_total_timeout": 90.0,
    "parser_trust_filename_md5_only_if_matches_real": True,
    "enable_saucenao": True,
    "enable_iqdb": True,
    "enable_danbooru_iqdb": False,
    "enable_e621_iqdb": True,
    "e621_browser_api_fallback": True,
    "e621_browser_api_headless": False,
    "e621_browser_api_verify_timeout_seconds": 120,
    "e621_browser_api_backend": "companion_extension",
    "e621_browser_api_companion_timeout_seconds": 120,
    "e621_browser_api_allow_external_chrome_cdp": False,
    "e621_browser_api_cdp_port": 9222,
    "e621_browser_api_launch_external_chrome": False,
    "e621_iqdb_max_results": 5,
    "enable_tineye": False,
    "tineye_max_results": 10,
    "tineye_delay_min": 30,
    "tineye_delay_max": 90,
    "tineye_browser_fallback": True,
    "tineye_browser_headless": False,
    "tineye_browser_timeout_seconds": 60,
    # Parser-only TinEye cooldown after Cloudflare/Turnstile block pages.
    # This affects only Parser -> Reverse chain -> TinEye fallback, not the
    # grabber, companion API or manual browser usage.
    "tineye_parser_block_cooldown_seconds": 86400,
    "iqdb_min_similarity": 75.0,
    "r34_fuzzy_min_similarity": 60.0,
    "strict_atf_md5": True,
    "atf_pixel_hash_locator_enabled": True,
    "atf_pixel_hash_max_assets": 5,
    "enable_atf_auto_tags": False,
    "max_preview_cache_files": 1000,
    "preview_cache_max_age_days": 14,
    "skip_existing": True,
    "tag_only_untagged": True,
    "skip_copy_suffix_files": True,
    "retry_nomatch": False,
    "limit_files": 0,
    "enable_curl_cffi": False,
    "sites": {
        "rule34.xxx": {"enabled": True, "type": "rule34xxx", "login": "", "api_key": "", "user_id": "", "login_url": "https://rule34.xxx/index.php?page=account&s=options", "notes": "Официальный DAPI: api.rule34.xxx, user_id + api_key, json=1"},
        "rule34.us": {"enabled": True, "type": "rule34us", "login": "", "api_key": "", "user_id": "", "login_url": "https://rule34.us/index.php?page=account&s=login", "notes": "HTML-поиск с проверкой MD5; подтверждённого API нет"},
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
            retired = {"copy_results_enabled", "copy_mode", "tags_suffix", "sources_suffix", "output_suffix", "mark_no_match", "tagger_site_conveyor_enabled", "use_browser_auth", "use_system_browser_cookies", "browser_auth_url", "browser_auth_wait_seconds"}
            merged.update({k: v for k, v in data.items() if k not in retired})
            merged["sites"] = {**DEFAULT_SETTINGS["sites"], **data.get("sites", {})}
            merged["custom_sites"] = data.get("custom_sites", [])
            return merged
        except Exception:
            return DEFAULT_SETTINGS.copy()
    return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    # Use the central writer so portable workspaces never recreate a full
    # secret-bearing configuration file in Documents.
    from core.settings import save_settings as save_application_settings
    save_application_settings(settings)




def redact_sensitive_url(text):
    """Backward-compatible wrapper for central privacy redaction."""
    return sanitize_text(text)


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


def danbooru_pixel_hash(path):
    """Return Danbooru/ATF-style pixel_hash for static images.

    Danbooru media assets store a pixel_hash that is MD5(PAM header + raw
    RGBA pixel bytes) after orientation/color normalization.  The exact server
    implementation uses libvips; this implementation prefers Pillow so Local
    Booru does not gain a hard pyvips dependency.  For videos/animated images
    Danbooru falls back to the byte MD5, so do the same.
    """
    path = Path(path)
    try:
        ext = path.suffix.lower()
        if ext in VIDEO_EXTS:
            return file_md5(path).lower()
        with Image.open(path) as img:
            try:
                if bool(getattr(img, "is_animated", False)) and int(getattr(img, "n_frames", 1) or 1) > 1:
                    return file_md5(path).lower()
            except Exception:
                pass
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            try:
                icc = img.info.get("icc_profile") if hasattr(img, "info") else None
                if icc and ImageCms is not None:
                    src_profile = ImageCms.ImageCmsProfile(__import__('io').BytesIO(icc))
                    dst_profile = ImageCms.createProfile("sRGB")
                    img = ImageCms.profileToProfile(img, src_profile, dst_profile, outputMode="RGBA")
                else:
                    img = img.convert("RGBA")
            except Exception:
                img = img.convert("RGBA")
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            width, height = img.size
            header = (
                "P7\n"
                f"WIDTH {int(width)}\n"
                f"HEIGHT {int(height)}\n"
                "DEPTH 4\n"
                "MAXVAL 255\n"
                "TUPLTYPE RGB_ALPHA\n"
                "ENDHDR\n"
            )
            h = hashlib.md5()
            h.update(header.encode("ascii"))
            h.update(img.tobytes())
            return h.hexdigest().lower()
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
    """Parse an ATF post sidebar without ever trusting visible link text.

    ATF is Danbooru-based. Visible sidebar labels can contain counts/UI text;
    only the ``tags=`` query value in a tag-search href is accepted as a tag.
    Returns (tags, groups).
    """
    groups = {
        "artist": [],
        "character": [],
        "copyright": [],
        "species": [],
        "general": [],
        "meta": [],
    }

    def tag_from_href(anchor):
        try:
            href = html.unescape(str(anchor.get("href", "")))
            vals = parse_qs(urlparse(href).query).get("tags", [])
            if not vals:
                return ""
            tag = html.unescape(str(vals[0])).strip().replace(" ", "_")
            if not tag or tag.startswith(("rating:", "sort:", "md5:", "user:", "score:")):
                return ""
            if tag.lower() in {"?", "posts", "post", "all"}:
                return ""
            return tag
        except Exception:
            return ""

    try:
        soup = BeautifulSoup(html_text or "", "html.parser")
        category_map = {
            "0": "general",
            "1": "artist",
            "3": "copyright",
            "4": "character",
            "5": "meta",
        }

        # Numeric Danbooru/ATF categories.
        for cls_num, group_name in category_map.items():
            for el in soup.select(f".category-{cls_num} a.search-tag[href*='tags='], .category-{cls_num} a[href*='tags=']"):
                tag = tag_from_href(el)
                if tag and tag not in groups[group_name]:
                    groups[group_name].append(tag)

        # Named sidebar classes used by newer page variants.
        named_classes = {
            "artist": "artist", "character": "character", "copyright": "copyright",
            "general": "general", "metadata": "meta", "meta": "meta", "species": "species",
        }
        for cls_part, group_name in named_classes.items():
            for el in soup.select(f".tag-type-{cls_part} a.search-tag[href*='tags='], .tag-type-{cls_part} a[href*='tags=']"):
                tag = tag_from_href(el)
                if tag and tag not in groups[group_name]:
                    groups[group_name].append(tag)

        all_tags = []
        for group_name in ("artist", "copyright", "character", "species", "general", "meta"):
            for tag in groups[group_name]:
                if tag not in all_tags:
                    all_tags.append(tag)
        return all_tags, {k: v for k, v in groups.items() if v}
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
        tmp_dir = Path(CACHE_DIR) / "preview_cache" / "local_booru_frames"
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
        "contributor": [],
        "character": [],
        "copyright": [],
        "species": [],
        "general": [],
        "meta": [],
        "lore": [],
        "invalid": [],
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
    "species": "species", "specie": "species",
    "contributor": "contributor", "contributors": "contributor",
    "lore": "lore", "invalid": "invalid",
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

    for key in ["artist", "contributor", "character", "copyright", "species", "general", "meta", "lore", "invalid", "parody", "language", "category", "pages"]:
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
    "ascii2d.net",
}

# Import standard requests separately for file uploads
try:
    import requests as _std_requests
except ImportError:
    _std_requests = None

def _make_plain_session(target_host=None, settings=None, log_func=None):
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
    try:
        from core.network import install_safe_session
        install_safe_session(s, settings=settings or {}, log_func=log_func, cancel_callback=(settings or {}).get("_cancel_callback") if isinstance(settings, dict) else None)
    except Exception:
        pass
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
            s = _make_plain_session(target_host, settings=settings, log_func=log_func)
    else:
        s = _make_plain_session(target_host, settings=settings, log_func=log_func)
    
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

    if settings and target_host:
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

        # 2) Netscape cookies from Local_Booru_Archive/settings/output/runtime/browser_cookies/<host>.txt.
        # This is useful for cookies exported from real Chrome/Edge extensions.
        txt_jar, txt_info = load_txt_cookiejar_for_host(target_host)
        if txt_jar:
            added = _add_jar(txt_jar)
            total_added += added
            sources.append(f"{txt_info}:{added}")

        # 3) Optional fallback: real Chrome/Edge/Firefox cookies.
        if False:  # retired: never read system browser cookies automatically
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
                    log_func(f"  DANBOORU WARNING: cf_clearance missing; Cloudflare 403 is likely. Use external Chrome/Edge export to {BROWSER_COOKIES_DIR / 'danbooru.donmai.us.txt'}")
            else:
                log_func(f"COOKIES [{target_host}]: 0 ({'; '.join(sources) if sources else 'no sources'})")
                if target_host == "danbooru.donmai.us":
                    log_func("  DANBOORU WARNING: no cookies loaded; Cloudflare/login pages will probably fail.")

    # Wrap the real session once so parser, tagger and subscriptions all share
    # the same process-wide host throttle, including raw session.get() calls.
    try:
        if not bool(getattr(s, "_local_booru_global_limiter", False)):
            from core.http_rate_limiter import wait_for as _global_wait_for, apply_retry_after as _global_retry_after
            _orig_get = s.get
            _orig_post = s.post
            def _limited_get(url, *args, **kwargs):
                _global_wait_for(url, settings or {})
                response = _orig_get(url, *args, **kwargs)
                _global_retry_after(response, settings or {})
                return response
            def _limited_post(url, *args, **kwargs):
                _global_wait_for(url, settings or {})
                response = _orig_post(url, *args, **kwargs)
                _global_retry_after(response, settings or {})
                return response
            s.get = _limited_get
            s.post = _limited_post
            s._local_booru_global_limiter = True
    except Exception:
        pass
    return s


def _post_with_file(session, url, file_path, file_field="file", extra_data=None, extra_params=None, timeout=60, headers=None, auth=None):
    """POST a file upload using the safest available requests-compatible session.

    curl_cffi is not fully compatible with the file upload path, so it is
    converted to a plain requests.Session. cloudscraper, however, must keep
    its own session object; otherwise the Cloudflare bypass is lost and ASCII2D
    falls back to 403 even when cloudscraper is installed in the same Python.
    """
    import io, importlib
    std_req = importlib.import_module("requests")

    file_path_str = str(file_path) if not hasattr(file_path, 'read') else None
    filename = (
        getattr(file_path, 'name', '').split('/')[-1].split('\\')[-1]
        or (file_path_str or '').split('/')[-1].split('\\')[-1]
        or "image.jpg"
    )

    with (open(file_path_str, 'rb') if file_path_str else file_path) as f:
        file_bytes = f.read()

    target = session
    mod = type(session).__module__.lower()
    if "cloudscraper" not in mod and "curl_cffi" in mod:
        target = std_req.Session()
        try:
            target.cookies.update(session.cookies)
        except Exception:
            pass
        try:
            target.headers.update(dict(session.headers))
        except Exception:
            pass

    wrapped = bool(getattr(target, "_local_booru_global_limiter", False))
    if not wrapped:
        try:
            from core.http_rate_limiter import wait_for as _global_wait_for
            _global_wait_for(url, {})
        except InterruptedError:
            raise
        except Exception:
            pass
    response = target.post(
        url,
        files={file_field: (filename, io.BytesIO(file_bytes))},
        data=extra_data or {},
        params=extra_params or {},
        headers=headers or None,
        auth=auth,
        timeout=timeout,
    )
    if not wrapped:
        try:
            from core.http_rate_limiter import apply_retry_after as _global_retry_after
            _global_retry_after(response, {})
        except Exception:
            pass
    return response



class _SyntheticHTTPResponse:
    """Small response adapter for browser-backed JSON/API fetches.

    It intentionally mimics only the tiny subset used by safe_json_response()
    and post parsers: status_code, headers, text, json(), raise_for_status().
    """
    def __init__(self, status_code=200, text="", headers=None, url=""):
        self.status_code = int(status_code or 0)
        self.text = text or ""
        self.headers = dict(headers or {})
        self.url = url or ""

    def json(self):
        return json.loads(self.text or "null")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code} {self.url}".strip())


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


def _log_nomatch_promote_cleanup(log_func, import_result):
    try:
        cleanup = (import_result or {}).get("nomatch_cleanup") or {}
        rows = int(cleanup.get("rows_deactivated", 0) or 0)
        imgs = int(cleanup.get("image_rows_removed", 0) or 0)
        files = int(cleanup.get("files_removed", 0) or 0)
        errors = int(cleanup.get("errors", 0) or 0)
        if rows or imgs or files or errors:
            log_func(f"  NO_MATCH PROMOTE CLEANUP: rows={rows} image_rows={imgs} files_removed={files} errors={errors}")
    except Exception:
        pass

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
    # Use session_folder subfolder so each tagger run is isolated
    session_sub = settings.get("session_folder", "")
    media_dir = bucket_dir / "media" / session_sub if session_sub else bucket_dir / "media"
    return {
        "base": base_out,
        "bucket": bucket_dir,
        "media": media_dir,
        "tags": bucket_dir / "tags",
        "source": bucket_dir / "source",
        "searched": bucket_dir / "searched",
        "cache": bucket_dir / "cache",
        "media_file": media_dir / img.name,
        "searched_file": bucket_dir / "searched" / (img.stem + ".searched.json"),
    }


def output_processed_status(settings, img):
    """Return processed status using SQLite as the main storage.

    Old .searched.json files are ignored in this branch: the DB is the source of truth.
    """
    try:
        from core.database.storage import processed_status
        return processed_status(settings, img)
    except Exception:
        return None


def copy_result_files(settings, img, status):
    """Archive media while enforcing one live physical file per exact MD5.

    A second site or second input file may resolve to identical bytes.  For a
    FOUND result we reuse the already-live canonical media path instead of
    creating another gallery card/session copy. Metadata is merged later into
    that returned path.
    """
    img = Path(img)
    # Source media is immutable; every result is represented in managed output.
    paths = result_paths_for(settings, img, status)
    bucket = result_bucket_name(status)
    md5 = ""
    try:
        from core.file_hash_cache import get_or_compute_md5 as _cached_md5
        md5 = _cached_md5(settings, img)[0] if img.exists() else ""
    except Exception:
        try:
            md5 = file_md5(img).lower() if img.exists() else ""
        except Exception:
            md5 = ""

    # Exact tagged media already present in FOUND is the canonical physical copy.
    # Do not copy the same bytes into another session folder.
    if bucket == "found" and md5:
        try:
            from core.database.storage import found_media_path_by_md5
            existing = found_media_path_by_md5(settings, md5)
            if existing and Path(existing).exists():
                return Path(existing)
        except Exception:
            pass

    for d in (paths["media"], paths["cache"]):
        d.mkdir(parents=True, exist_ok=True)

    try:
        from core.preflight import ensure_space_for_write
        _incoming = img.stat().st_size if img.exists() else 0
        _ok_space, _space_msg = ensure_space_for_write(settings, paths["media_file"], _incoming)
        if not _ok_space:
            append_error_log("COPY STOP NO DISK SPACE: " + _space_msg)
            return None
        paths["media_file"] = copy_into_managed(settings, img, paths["media_file"], operation="tagger.copy_result", hash_md5=md5 or None)
    except Exception:
        return None

    # Found/partial metadata is inserted by save_found_metadata after this copy,
    # so its normal import lifecycle (Inbox/Archive) remains intact. NO_MATCH
    # has no metadata writer and must be registered here.
    if bucket == "no_match":
        try:
            from core.database.storage import ensure_image
            ensure_image(settings, paths["media_file"], status=bucket, original_path=str(img), hash_md5=md5 or None)
        except Exception:
            pass
    return paths["media_file"]


def _valid_archived_media_path(path) -> bool:
    """Return True only when a result copy/reuse actually exists on disk.

    Older code continued to write SQLite metadata even if copying a NO_MATCH
    retry into FOUND failed.  That produced gallery rows with tags but with a
    missing file path.
    """
    try:
        p = Path(path) if path else None
        if not p:
            return False
        if p.exists() and p.is_file():
            return True
        # Unit tests and a few legacy extension hooks may use a mocked relative
        # archive path without creating a real file.  Real Local Booru output
        # paths are absolute, so the missing-file guard still protects live use.
        if not p.is_absolute():
            return True
        return False
    except Exception:
        return False

def cleanup_archived_result(settings, img, statuses=("nomatch",)):
    """Remove a promoted file and inherited generated artifacts from an output bucket.

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
                    unlink_managed(settings, f, operation="tagger.cleanup_archived_result")
            except Exception:
                pass
        # Live mode no longer writes tag/source sidecars. Generated cache/search
        # artifacts, if inherited from an old build, are cleaned only inside the
        # disposable output tree through the storage gateway.
        try:
            delete_bucket_artifacts(settings, paths["media"] / img.name, operation="tagger.cleanup_legacy_output_artifacts")
        except Exception:
            pass


def save_found_metadata(settings, img, tags, source_url="", groups=None, status="tagged", *, archived_media_path=None, hash_md5=None, source_tag_groups=None):
    """Store tags/source in SQLite only for the canonical archived media path.

    ``archived_media_path`` may already point at an existing exact-MD5 canonical
    file, so additional sites enrich one row instead of producing duplicates.
    """
    img = Path(img)
    paths = result_paths_for(settings, img, status)
    paths["media"].mkdir(parents=True, exist_ok=True)
    paths["cache"].mkdir(parents=True, exist_ok=True)
    media_path = Path(archived_media_path) if archived_media_path else paths["media_file"]
    if not groups or not groups_to_tags(groups):
        groups = {"artist": [], "character": [], "copyright": [], "general": list(tags or []), "meta": []}
    try:
        from core.import_pipeline import register_media_import
        return register_media_import(
            settings,
            media_path,
            tags=tags or [],
            groups=groups,
            sources=[source_url] if source_url else [],
            status=result_bucket_name(status),
            original_path=str(img),
            hash_md5=hash_md5,
            origin="tagger",
            source_tag_groups=source_tag_groups,
            merge_existing=True,
        )
    except Exception:
        return None

# Temporary public compatibility alias for old extensions/imports; internal code uses SQLite terminology.
write_sidecar_tags = save_found_metadata

def promote_manual_match(settings, img, tags, source_url="", groups=None):
    """Promote a NO_MATCH file to found after manual URL tag extraction."""
    img = Path(img)
    tags = unique_keep_order(tags)
    # Archive/reuse the one canonical exact-MD5 media copy first; metadata then
    # attaches to that physical row rather than creating another live image.
    archived_media = copy_result_files(settings, img, "tagged")
    if not _valid_archived_media_path(archived_media):
        append_error_log(f"PROMOTE MANUAL MATCH FAILED: managed copy missing for {img}")
        return False
    source_groups = [{"url": source_url, "groups": groups or {"general": list(tags or [])}, "method": "manual_url"}] if source_url else []
    save_found_metadata(settings, img, tags, source_url, groups, status="tagged", archived_media_path=archived_media, source_tag_groups=source_groups)
    remove_nomatch(img, settings=settings)
    # Archive as found first, then remove old no_match/partial copies.
    cleanup_archived_result(settings, img, ("nomatch", "partial"))
    return True

def append_error_log(msg):
    try:
        ERROR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        safe = sanitize_text(msg)
        max_bytes = 10 * 1024 * 1024
        if ERROR_LOG_FILE.exists() and ERROR_LOG_FILE.stat().st_size >= max_bytes:
            for idx in range(4, 0, -1):
                src = ERROR_LOG_FILE.with_name(ERROR_LOG_FILE.name + f".{idx}")
                dst = ERROR_LOG_FILE.with_name(ERROR_LOG_FILE.name + f".{idx + 1}")
                try:
                    if dst.exists():
                        dst.unlink()
                    if src.exists():
                        src.replace(dst)
                except Exception:
                    pass
            try:
                ERROR_LOG_FILE.replace(ERROR_LOG_FILE.with_name(ERROR_LOG_FILE.name + ".1"))
            except Exception:
                pass
        with ERROR_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {safe}\n")
    except Exception:
        pass


def do_browser_login(auth_url, wait_seconds=60):
    raise RuntimeError("Browser login is not available in desktop v3 yet")



def cleanup_preview_cache(settings=None):
    """Keep generated preview/frame files bounded by age and count."""
    settings = settings or {}
    max_files = int(settings.get("max_preview_cache_files", settings.get("max_thumb_cache_files", 20000)) or 20000)
    max_age_days = int(settings.get("preview_cache_max_age_days", settings.get("thumb_cache_max_age_days", 90)) or 90)
    roots = [
        CACHE_DIR / "preview_cache",
        CACHE_DIR / "thumbs",
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


_TRANSIENT_NETWORK_MARKERS = (
    "read timed out", "connect timed out", "connection timed out", "timeout error",
    "timeout('", "timeouterror", "connection aborted", "connection reset",
    "failed to resolve", "getaddrinfo failed", "nameresolutionerror",
    "temporary failure in name resolution", "network is unreachable",
    "connection refused", "max retries exceeded", "ssleoferror",
    "unexpected_eof_while_reading", "remote end closed connection",
    "network temporary failure", "http 408", "http 425", "http 429", "http 500", "http 502", "http 503", "http 504",
)

def _is_transient_network_error_text(message):
    text = str(message or "").lower()
    return any(marker in text for marker in _TRANSIENT_NETWORK_MARKERS)

def _network_error_host(message):
    text = str(message or "")
    m = re.search(r'host=["\']([^"\']+)', text, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r"\[([^\]]+)\]", text)
    return m.group(1).lower() if m else "network"

class Tagger:
    def __init__(self, settings, log_func):
        self.settings = settings
        # v204: Fluffle/FuzzySearch are removed from the parser chain after
        # low-score false positives polluted tags. Keep any legacy config keys
        # inert so old settings cannot re-enable them.
        for _removed_reverse_key in (
            "enable_fuzzysearch", "fuzzysearch_api_key", "fuzzysearch_endpoint",
            "fuzzysearch_api_docs_url", "fuzzysearch_max_results",
            "enable_fluffle", "fluffle_api_key", "fluffle_endpoint",
            "fluffle_api_docs_url", "fluffle_max_results",
        ):
            self.settings.pop(_removed_reverse_key, None)
        self._external_log = log_func
        self._transient_network_events = []
        self._transient_network_hosts = set()
        self._background_group_urls = []
        self.log = self._log_and_track
        self.session = get_session(settings, self.log)
        self.timeout = max(5, int(float(settings.get("request_timeout_seconds", 20))))
        self.saucenao_state_file = CACHE_DIR / "saucenao_state.json"
        self._saucenao_deferred = False
        self._saucenao_defer_reason = ""
        self._saucenao_retry_after = 0
        self._last_saucenao_source_only = []
        self._last_reverse_source_only = []
        self._tineye_tagged_total = 0
        self._tineye_source_only_total = 0
        # Parser-only cooldown for the TinEye reverse fallback.  Do not use this
        # as a global browser/grabber switch: it only protects the parser mass
        # queue from repeatedly opening Cloudflare-blocked TinEye pages.
        self._tineye_parser_disabled_until = float(self.settings.get("_tineye_parser_disabled_until", 0) or 0)
        self._tineye_parser_block_reason = str(self.settings.get("_tineye_parser_block_reason", "") or "")
        self._e621_iqdb_cooldown_until = 0
        self._e621_iqdb_missing_auth_logged = False
        self._background_group_urls = []
        self._partial_match_found = False
        self._partial_match_reason = ""
        self.cancel_callback = None
        # Optional UI callback used by the per-site conveyor. Signature:
        # callback(site_label, media_path, state). It is deliberately passive:
        # the engine remains usable without any Qt/UI dependency.
        self.activity_callback = None
        # Per-Tagger session/request caches.  Network code must not reload
        # cookies or re-run anti-bot verification for every fallback branch.
        self._session_cache = {}
        self._request_cache = {}
        self._lookup_cache_enabled = False
        self._last_variant_site_md5s = []
        self._last_atf_pixel_hash_site_md5s = []
        self._atf_pixel_hash_guard = False
        # One-shot diagnostics for sites that are correctly configured but may
        # simply have no overlap with the local archive. This separates an
        # empty MD5 result from a dead/malformed public DAPI endpoint.
        self._dapi_health_reported = set()
        self._last_lookup_status = ""
        self._rule34_auth_missing_warned = set()
        self._e621_browser_pw = None
        self._e621_browser_browser = None
        self._e621_browser_context = None
        self._e621_browser_page = None
        self._e621_browser_process = None
        self._e621_browser_host = ""
        self._e621_browser_verified_hosts = set()
        try:
            cleanup_preview_cache(self.settings)
        except Exception:
            pass

    def _log_and_track(self, message):
        text = sanitize_text(message)
        if _is_transient_network_error_text(text):
            self._transient_network_events.append(text[:400])
            self._transient_network_hosts.add(_network_error_host(text))
        self._external_log(text)

    def _reset_network_state(self):
        self._transient_network_events = []
        self._transient_network_hosts = set()

    def transient_network_failed(self):
        return bool(self._transient_network_events)

    def network_failure_summary(self):
        hosts = sorted(h for h in self._transient_network_hosts if h)
        return ", ".join(hosts) if hosts else "network"

    def report_activity(self, site_label, media_path, state="Ищет"):
        callback = getattr(self, "activity_callback", None)
        if callable(callback):
            try:
                callback(str(site_label), str(media_path), str(state))
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

    def _http_get_cached(self, session, url, *, params=None, timeout=None, headers=None, allow_redirects=None):
        """GET with per-file cache.

        APT/booru lookup commonly tries JSON, XML and HTML fallbacks for the
        same page.  Without this cache one file can hit the same domain 5-10
        times, reloading cookies and re-running Cloudflare/PoW checks.  The
        cache is active only during one MD5 lookup, so it cannot return stale
        data for later files.
        """
        key_params = dict(params or {})
        if allow_redirects is not None:
            key_params["__allow_redirects"] = int(bool(allow_redirects))
        key = self._request_cache_key("GET", url, key_params)
        if self._lookup_cache_enabled and key in self._request_cache:
            return self._request_cache[key]
        kwargs = {"params": params, "timeout": timeout, "headers": headers}
        if allow_redirects is not None:
            kwargs["allow_redirects"] = bool(allow_redirects)
        r = session.get(url, **kwargs)
        if self._lookup_cache_enabled:
            self._request_cache[key] = r
        return r

    def _atf_get_cached(self, session, url, host, **kwargs):
        key = self._request_cache_key("ATFGET", url, kwargs.get("params"))
        if self._lookup_cache_enabled and key in self._request_cache:
            return self._request_cache[key]
        r = self._atf_get(session, url, host, **kwargs)
        h = str(host or "").lower().replace("www.", "")
        if h in ("e621.net", "e926.net") and self._e621_is_cloudflare_html_response(r):
            br = self._e621_browser_get_json_response(
                url,
                params=kwargs.get("params"),
                auth=kwargs.get("auth"),
                host=h,
                context="exact-md5",
            )
            if br is not None:
                r = br
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

    def _needs_background_tag_groups(self, url):
        """Whether category recovery for a matched URL must be deferred.

        These hosts expose usable clean post tags quickly, but category recovery
        may add HTML/tag-index requests. In conveyor/fallback operation that
        extra work belongs to the durable enrichment queue.
        """
        if not self.settings.get("tagger_background_tag_groups", self.settings.get("tagger_background_rule34_categories", True)):
            return False
        host = urlparse(str(url or "")).netloc.lower().replace("www.", "")
        return host in {"gelbooru.com", "rule34.xxx", "api.rule34.xxx", "xbooru.com", "hypnohub.net"}

    def groups_or_defer_background(self, url, tags):
        if self._needs_background_tag_groups(url):
            if url and url not in self._background_group_urls:
                self._background_group_urls.append(str(url))
            groups = empty_tag_groups()
            groups["general"] = unique_keep_order([normalize_tag(t) for t in (tags or []) if normalize_tag(t)])
            return groups
        return self.grouped_tags_from_url(url)

    def take_background_group_urls(self):
        urls = unique_keep_order(list(self._background_group_urls or []))
        self._background_group_urls = []
        return urls

    def reverse_url_tags_and_groups(self, url, method="reverse"):
        """Return tags and source-scoped groups for a reverse-search URL.

        Normal flat sources are saved fast and then refined by the durable
        background category queue.  TinEye, however, often returns a concrete
        rule34.xxx HTML post URL as the only usable source.  Saving that as a
        plain flat list leaves the source view stuck under ``general`` until a
        later queue pass.  For TinEye rule34 hits, resolve the concrete post
        immediately through the normal guarded rule34 path: DAPI decides tag
        membership, HTML only classifies those confirmed tags.
        """
        host = urlparse(str(url or "")).netloc.lower().replace("www.", "")
        method = str(method or "").lower()
        if method == "tineye" and host in ("rule34.xxx", "api.rule34.xxx"):
            groups = self.grouped_tags_from_url(url)
            tags = groups_to_tags(groups)
            if tags:
                self.log(f"  TINEYE RULE34 CATEGORY GUARD: grouped={len(tags)} url={url}")
                return tags, groups
        tags = self.tags_from_url(url)
        groups = self.groups_or_defer_background(url, tags)
        return tags, groups

    def _post_id_from_url_for_md5_relay(self, url: str, host: str = "") -> str:
        """Extract a concrete post id from common booru post URLs."""
        try:
            parsed = urlparse(str(url or ""))
            host = (host or parsed.netloc or "").lower().replace("www.", "")
            q = parse_qs(parsed.query or "")
            for key in ("id", "post_id", "pid"):
                got = (q.get(key) or [""])[0]
                if str(got).strip().isdigit():
                    return str(got).strip()
            parts = [x for x in (parsed.path or "").strip("/").split("/") if x]
            # Danbooru/e621 modern: /posts/<id>
            if len(parts) >= 2 and parts[0] in ("posts", "post") and parts[1].isdigit():
                # /post/show/<id> is handled below; /post/<id> also occurs on some forks.
                if parts[0] == "posts":
                    return parts[1]
            # Legacy boorus: /post/show/<id>
            if len(parts) >= 3 and parts[0] == "post" and parts[1] == "show" and parts[2].isdigit():
                return parts[2]
            # Some result URLs end with the numeric id.
            if parts and parts[-1].isdigit():
                return parts[-1]
        except Exception:
            pass
        return ""

    def _md5_near_keywords_from_html(self, html_text: str) -> str:
        """Conservative HTML fallback: only accept 32-hex near md5/hash words."""
        text = html.unescape(str(html_text or ""))
        if not text:
            return ""
        patterns = [
            r"(?:md5|hash|checksum)[^0-9a-fA-F]{0,120}([0-9a-fA-F]{32})",
            r"([0-9a-fA-F]{32})[^0-9a-fA-F]{0,120}(?:md5|hash|checksum)",
        ]
        for pat in patterns:
            for m in re.finditer(pat, text, flags=re.I):
                val = (m.group(1) or "").lower()
                if is_md5(val):
                    return val
        return ""


    def _normalize_pixiv_relay_queries(self, url: str):
        """Build safe booru source-search queries for one Pixiv/Pximg URL.

        Never use a bare ``source:*<id>*`` query: Danbooru can match the same
        digit run inside unrelated Twitter/Fanbox/etc URLs.  All wildcard
        queries are scoped to Pixiv/Pximg or the Pixiv page filename form.
        """
        url = str(url or "").strip()
        out = []
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or "").lower().replace("www.", "")
            path = unquote(parsed.path or "")
            query = parsed.query or ""
        except Exception:
            host, path, query = "", "", ""

        def add(q):
            q = str(q or "").strip()
            if q and q not in out:
                out.append(q)

        pid = ""
        patterns = [
            r"/artworks/(\d{5,})",
            r"[?&]illust_id=(\d{5,})",
            r"/(\d{5,})_p\d+",
            r"/(\d{5,})(?:_ugoira)?(?:\.|$)",
        ]
        haystacks = [path, query, url]
        for hay in haystacks:
            for pat in patterns:
                m = re.search(pat, hay)
                if m:
                    pid = m.group(1)
                    break
            if pid:
                break

        # Exact current URL is safe if it already contains a Pixiv/Pximg host.
        if ("pixiv.net" in host or "pximg.net" in host) and url:
            add(f"source:{url}")

        if pid:
            add(f"pixiv_id:{pid}")
            add(f"source:*i.pximg.net*{pid}*")
            # Danbooru often stores the direct original URL only, e.g.
            # .../40884608_p0.jpg, not pixiv.net/artworks/40884608.
            for page in range(0, 6):
                add(f"source:*{pid}_p{page}*")
            add(f"source:*pixiv.net/artworks/{pid}*")
            add(f"source:*pixiv.net/en/artworks/{pid}*")
            add(f"source:*member_illust.php*illust_id={pid}*")
        return out

    def _source_relay_query_strings_for_url(self, url: str):
        """Return safe source-search queries for unsupported reverse URLs."""
        url = str(url or "").strip()
        if not url:
            return []
        try:
            host = (urlparse(url).netloc or "").lower().replace("www.", "")
        except Exception:
            host = ""
        if "pixiv.net" in host or "pximg.net" in host:
            return self._normalize_pixiv_relay_queries(url)
        # For non-Pixiv unsupported URLs use only full normalized URL, not a
        # bare numeric/id substring.  This keeps FA/Fanbox/Kemono/E-Hentai relay
        # safe as a locator while avoiding broad false positives.
        return [f"source:{url}", f"source:*{url}*"]

    def _first_md5_from_posts(self, posts):
        for post in posts or []:
            got = self._post_md5_value(post)
            if got and is_md5(got):
                return got.lower()
        return ""

    def _source_relay_posts_danbooru(self, query, limit=5):
        session = self.session_for_host("danbooru.donmai.us")
        params = {"tags": query, "limit": int(limit)}
        params.update(self._danbooru_api_params("danbooru.donmai.us"))
        r = session.get(
            "https://danbooru.donmai.us/posts.json",
            params=params,
            timeout=self.timeout,
            headers=self._danbooru_api_headers("danbooru.donmai.us"),
            auth=self._danbooru_auth_tuple("danbooru.donmai.us"),
        )
        self._danbooru_log_response_problem(r, "source-relay")
        data = safe_json_response(r, "danbooru.donmai.us")
        return self._post_dicts_from_data(data)

    def _source_relay_posts_gelbooru_like(self, host, query, limit=5):
        session = self.session_for_host(host)
        params = {"page": "dapi", "s": "post", "q": "index", "json": "1", "limit": int(limit), "tags": query}
        params.update(self.auth_params(self.site_cfg(host)))
        r = session.get(f"https://{host}/index.php", params=params, timeout=self.timeout)
        return self._posts_from_dapi_response(r, host)

    def _source_relay_posts_rule34xxx(self, query, limit=5):
        session = self.session_for_host("rule34.xxx")
        params = self._rule34xxx_api_params(self.site_cfg("rule34.xxx"), limit=int(limit), tags=query)
        r = session.get(
            "https://api.rule34.xxx/index.php",
            params=params,
            timeout=self.timeout,
            headers=self._rule34xxx_api_headers(self.site_cfg("rule34.xxx")),
        )
        self._rule34xxx_log_response_problem(r, "source-relay")
        return self._posts_from_dapi_response(r, "rule34.xxx")

    def _source_relay_posts_atf(self, query, limit=5):
        host = "booru.allthefallen.moe"
        session = self.session_for_host(host)
        params = {"tags": query, "limit": int(limit)}
        params.update(self._danbooru_api_params(host))
        r = self._atf_get(
            session,
            "https://booru.allthefallen.moe/posts.json",
            host,
            params=params,
            timeout=self.timeout,
            headers=self._danbooru_api_headers(host),
            auth=self._danbooru_auth_tuple(host),
        )
        self._danbooru_log_response_problem(r, "source-relay", host)
        data = safe_json_response(r, host)
        return self._post_dicts_from_data(data)

    def _atf_media_assets_by_pixel_hash(self, pixel_hash, limit=5):
        """Return ATF media_assets for a Danbooru-style pixel_hash.

        This is a locator, not a tag source.  The media_asset response gives an
        authoritative original file md5; tags still come from /posts.json by
        that md5 through the normal ATF JSON path.
        """
        pixel_hash = str(pixel_hash or "").strip().lower()
        if not is_md5(pixel_hash):
            return []
        host = "booru.allthefallen.moe"
        session = self.session_for_host(host)
        params = {"search[pixel_hash]": pixel_hash, "limit": int(limit)}
        params.update(self._danbooru_api_params(host))
        r = self._atf_get_cached(
            session,
            "https://booru.allthefallen.moe/media_assets.json",
            host,
            params=params,
            timeout=self.timeout,
            headers=self._danbooru_api_headers(host),
            auth=self._danbooru_auth_tuple(host),
        )
        self._danbooru_log_response_problem(r, "media-asset-pixel-hash", host)
        data = safe_json_response(r, host)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ("media_assets", "assets", "results"):
                if isinstance(data.get(key), list):
                    return [x for x in data.get(key) if isinstance(x, dict)]
            if data.get("id") is not None:
                return [data]
        return []

    def _atf_pixel_hash_locator_lookup(self, site, local_md5):
        """ATF fallback: local file -> pixel_hash -> media_asset.md5 -> post tags.

        Useful for resized/recompressed derivatives where byte MD5 differs but
        ATF's Danbooru-style pixel_hash still points to the original asset.
        """
        if bool(getattr(self, "_atf_pixel_hash_guard", False)):
            return [], "", empty_tag_groups()
        if not bool(self.settings.get("atf_pixel_hash_locator_enabled", True)):
            return [], "", empty_tag_groups()
        path = str(getattr(self, "_current_md5_lookup_path", "") or "").strip()
        if not path:
            return [], "", empty_tag_groups()
        try:
            p = Path(path)
            if not p.exists() or p.suffix.lower() not in MEDIA_EXTS:
                return [], "", empty_tag_groups()
        except Exception:
            return [], "", empty_tag_groups()

        pixel_hash = danbooru_pixel_hash(path)
        if not pixel_hash:
            self.log("    ATF PIXEL HASH: local pixel_hash unavailable")
            return [], "", empty_tag_groups()

        self.log(f"    ATF PIXEL HASH: search media_assets pixel_hash={pixel_hash}")
        try:
            assets = self._atf_media_assets_by_pixel_hash(pixel_hash, limit=int(self.settings.get("atf_pixel_hash_max_assets", 5) or 5))
        except Exception as e:
            self.log(f"    ATF PIXEL HASH ERROR: {type(e).__name__}: {e}")
            return [], "", empty_tag_groups()
        if not assets:
            self.log("    ATF PIXEL HASH MISS: no media_asset")
            return [], "", empty_tag_groups()

        local_md5 = str(local_md5 or "").strip().lower()
        seen = set()
        for asset in assets:
            asset_md5 = str(asset.get("md5") or "").strip().lower()
            if not is_md5(asset_md5) or asset_md5 in seen:
                continue
            seen.add(asset_md5)
            if asset_md5 == local_md5:
                self.log(f"    ATF PIXEL HASH SKIP: asset md5 equals local md5 {asset_md5}")
                continue
            self.log(
                f"    ATF PIXEL HASH ASSET: asset={asset.get('id','?')} "
                f"md5={asset_md5} size={asset.get('image_width','?')}x{asset.get('image_height','?')}"
            )
            try:
                old_guard = getattr(self, "_atf_pixel_hash_guard", False)
                self._atf_pixel_hash_guard = True
                tags, source, groups = self.engine_by_md5(site, asset_md5)
            finally:
                self._atf_pixel_hash_guard = old_guard
            if tags:
                try:
                    self._last_atf_pixel_hash_site_md5s = unique_keep_order(list(getattr(self, "_last_atf_pixel_hash_site_md5s", []) or []) + [asset_md5])
                    self._last_variant_site_md5s = unique_keep_order(list(getattr(self, "_last_variant_site_md5s", []) or []) + [asset_md5])
                except Exception:
                    pass
                self._last_lookup_match_method = "atf_pixel_hash"
                self.log(f"    ATF PIXEL HASH TAGS: md5={asset_md5} tags={len(tags)} source={redact_sensitive_url(source)}")
                return tags, source or f"https://booru.allthefallen.moe/media_assets/{asset.get('id','')}", groups
            self.log(f"    ATF PIXEL HASH POST MISS: md5={asset_md5}")
        return [], "", empty_tag_groups()

    def _source_relay_posts_e621(self, query, limit=5):
        host = "e621.net"
        session = self.session_for_host(host)
        params = {"tags": query, "limit": int(limit)}
        params.update(self._e621_api_params(host, include_v2=True))
        api_url = "https://e621.net/posts.json"
        auth = self._e621_auth_tuple(host)
        r = session.get(
            api_url,
            params=params,
            timeout=self.timeout,
            headers=self._e621_api_headers(host),
            auth=auth,
        )
        self._e621_log_response_problem(r, "source-relay")
        if self._e621_is_cloudflare_html_response(r):
            br = self._e621_browser_get_json_response(api_url, params=params, auth=auth, host=host, context="source-relay")
            if br is not None:
                r = br
        data = safe_json_response(r, host)
        posts = []
        if isinstance(data, dict) and isinstance(data.get("posts"), list):
            posts = data.get("posts")
        else:
            posts = self._post_dicts_from_data(data)
        return [p for p in posts if isinstance(p, dict)]

    def _source_relay_probe_sites(self, query):
        """Try one safe source query across trusted booru APIs and return md5/site."""
        attempts = [
            ("danbooru.donmai.us", self._source_relay_posts_danbooru),
            ("gelbooru.com", lambda q: self._source_relay_posts_gelbooru_like("gelbooru.com", q)),
            ("rule34.xxx", self._source_relay_posts_rule34xxx),
            ("booru.allthefallen.moe", self._source_relay_posts_atf),
            ("e621.net", self._source_relay_posts_e621),
        ]
        for site, func in attempts:
            try:
                posts = func(query)
                got = self._first_md5_from_posts(posts)
                if got:
                    return got, site, len(posts or [])
            except Exception as e:
                # Auth-required/no-match on one relay site must not cancel the
                # whole relay; the next trusted booru may still have it.
                try:
                    self.log(f"  SOURCE MD5 RELAY PROBE SKIP [{site}]: {type(e).__name__}: {e}")
                except Exception:
                    pass
        return "", "", 0

    def extract_md5_from_post_url(self, url: str) -> str:
        """Try to recover a verified post MD5 from a reverse-search result URL.

        This is a source locator, not a tag source.  Known booru hosts are read
        through their JSON/DAPI post endpoint first; arbitrary HTML is used only
        as a last-resort regex and only when a 32-hex value is close to md5/hash
        wording.
        """
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            return ""
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or "").lower().replace("www.", "")
        except Exception:
            return ""
        if not host:
            return ""

        post_id = self._post_id_from_url_for_md5_relay(url, host)

        def _md5_from_posts(posts):
            for post in posts or []:
                got = self._post_md5_value(post)
                if got and is_md5(got):
                    return got.lower()
            return ""

        try:
            if host in ("rule34.xxx", "api.rule34.xxx") and post_id:
                posts = self._rule34xxx_dapi_posts_by_id(self.site_cfg("rule34.xxx"), post_id)
                got = _md5_from_posts(posts)
                if got:
                    return got

            if host in ("gelbooru.com", "xbooru.com", "hypnohub.net"):
                q = parse_qs(parsed.query or "")
                lookup = {}
                if post_id:
                    lookup = {"id": post_id}
                elif (q.get("md5") or [""])[0]:
                    md5q = str((q.get("md5") or [""])[0]).strip().lower()
                    if is_md5(md5q):
                        return md5q
                elif str((q.get("tags") or [""])[0]).lower().startswith("md5:"):
                    md5q = str((q.get("tags") or [""])[0]).split(":", 1)[1].strip().lower()
                    if is_md5(md5q):
                        return md5q
                if lookup:
                    params = {"page": "dapi", "s": "post", "q": "index", "json": "1", **lookup}
                    params.update(self.auth_params(self.site_cfg(host)))
                    r = self.session_for_host(host).get(f"https://{host}/index.php", params=params, timeout=self.timeout)
                    got = _md5_from_posts(self._posts_from_dapi_response(r, host))
                    if got:
                        return got

            if host in ("danbooru.donmai.us", "donmai.us") and post_id:
                r = self.session_for_host("danbooru.donmai.us").get(
                    f"https://danbooru.donmai.us/posts/{post_id}.json",
                    params=self._danbooru_api_params("danbooru.donmai.us"),
                    timeout=self.timeout,
                    headers=self._danbooru_api_headers("danbooru.donmai.us"),
                    auth=self._danbooru_auth_tuple("danbooru.donmai.us"),
                )
                self._danbooru_log_response_problem(r, "post-md5-extract", "danbooru.donmai.us")
                data = safe_json_response(r, "danbooru.donmai.us")
                post = data if isinstance(data, dict) else {}
                got = self._post_md5_value(post)
                if got and is_md5(got):
                    return got.lower()

            if host in ("booru.allthefallen.moe", "allthefallen.moe") and post_id:
                atf_host = "booru.allthefallen.moe"
                r = self._atf_get(
                    self.session_for_host(atf_host),
                    f"https://booru.allthefallen.moe/posts/{post_id}.json",
                    atf_host,
                    params=self._danbooru_api_params(atf_host),
                    timeout=self.timeout,
                    headers=self._danbooru_api_headers(atf_host),
                    auth=self._danbooru_auth_tuple(atf_host),
                )
                self._danbooru_log_response_problem(r, "post-md5-extract", atf_host)
                data = safe_json_response(r, atf_host)
                post = data if isinstance(data, dict) else {}
                got = self._post_md5_value(post)
                if got and is_md5(got):
                    return got.lower()

            if host in ("e621.net", "e926.net") and post_id:
                params = self._e621_api_params(host, include_v2=True)
                api_url = f"https://{host}/posts/{post_id}.json"
                auth = self._e621_auth_tuple(host)
                r = self.session_for_host(host).get(
                    api_url,
                    params=params,
                    timeout=self.timeout,
                    headers=self._e621_api_headers(host),
                    auth=auth,
                )
                self._e621_log_response_problem(r, "md5-relay-post-json")
                if self._e621_is_cloudflare_html_response(r):
                    br = self._e621_browser_get_json_response(api_url, params=params, auth=auth, host=host, context="md5-relay-post-json")
                    if br is not None:
                        r = br
                data = safe_json_response(r, host)
                post = data.get("post", data) if isinstance(data, dict) else {}
                got = self._post_md5_value(post)
                if got and is_md5(got):
                    return got.lower()

            if host in ("yande.re", "konachan.com", "konachan.net") and post_id:
                session = self.session_for_host(host)
                attempts = [
                    (f"https://{host}/post/show/{post_id}.json", None),
                    (f"https://{host}/post.json", {"tags": f"id:{post_id}", "limit": 1}),
                ]
                for api_url, params in attempts:
                    try:
                        r = session.get(api_url, params=params, timeout=self.timeout, headers={"Accept": "application/json, */*"})
                        data = safe_json_response(r, host)
                        got = _md5_from_posts(self._post_dicts_from_data(data))
                        if got:
                            return got
                    except Exception:
                        continue

            # Direct static/data URLs often contain the MD5 as media filename.
            media_md5 = self._md5_from_urlish(url)
            if media_md5:
                return media_md5

            # Last resort: HTML page regex near md5/hash/checksum.  Do not scan
            # arbitrary 32-hex strings far away from hash wording.
            r = self.session_for_host(host).get(url, timeout=self.timeout, headers={"Accept": "text/html,application/xhtml+xml,*/*"})
            got = self._md5_near_keywords_from_html(getattr(r, "text", "") or "")
            if got:
                return got
        except Exception as e:
            try:
                self.log(f"  MD5 RELAY EXTRACT ERROR [{host}]: {type(e).__name__}: {e}")
            except Exception:
                pass
        return ""

    def extract_md5_from_source_url_relay(self, url: str) -> str:
        """Source URL -> trusted booru/API -> MD5 relay.

        Unsupported reverse-search hits (Pixiv/Fanbox/FA/Kemono/etc.) are not
        tag sources.  They are only locators.  We search the URL/source ID on
        trusted booru APIs, take an authoritative MD5 from the found post, then
        pass that MD5 through the ordinary MD5 pipeline.
        """
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            return ""
        queries = self._source_relay_query_strings_for_url(url)
        if not queries:
            return ""
        seen = set()
        for query in queries:
            query = str(query or "").strip()
            if not query or query in seen:
                continue
            seen.add(query)
            # Absolute safety: never run bare source:*12345678* style probes.
            if re.fullmatch(r"source:\*?\d{5,}\*?", query):
                self.log(f"  SOURCE MD5 RELAY UNSAFE QUERY SKIPPED: {query}")
                continue
            try:
                md5v, site, count = self._source_relay_probe_sites(query)
                if md5v:
                    self.log(f"  SOURCE MD5 RELAY FOUND: md5={md5v} via={site} query={query} posts={count}")
                    return md5v.lower()
            except Exception as e:
                try:
                    self.log(f"  SOURCE MD5 RELAY ERROR: {type(e).__name__}: {e}")
                except Exception:
                    pass
        self.log(f"  SOURCE MD5 RELAY MISS: {url}")
        return ""

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
            if host in ("booru.allthefallen.moe", "allthefallen.moe"):
                return self.atf_tags(url)
            if host == "gelbooru.com":
                return self.gelbooru_tags(url)
            if host in ("xbooru.com", "hypnohub.net"):
                return groups_to_tags(self.documented_dapi_groups_from_url(url, host))
            if host in ("e621.net", "e926.net"):
                return self.e621_tags(url, host=host)
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

    def _guarded_category_projection(self, authoritative_tags, candidate_groups, baseline_groups=None):
        """Return categories only for tags confirmed by a structured post response.

        Gelbooru/Rule34 HTML exposes useful colour/category information, but the
        rendered page may also contain sidebar, recommendation or navigation
        tags.  ``authoritative_tags`` comes from the exact DAPI/API post and is
        the only tag set permitted to enter source-scoped metadata.
        """
        ordered = unique_keep_order([normalize_tag(tag) for tag in (authoritative_tags or []) if normalize_tag(tag)])
        allowed = set(ordered)
        out = empty_tag_groups()
        assigned = set()

        def merge(groups):
            for group, values in (groups or {}).items():
                for raw in values or []:
                    norm = normalize_tag(raw)
                    if not norm or norm not in allowed or norm in assigned:
                        continue
                    safe_group = "meta" if norm == "artist_request" else str(group or "general")
                    add_tags_to_groups(out, safe_group, [norm])
                    assigned.add(norm)

        # HTML classification takes precedence when available; an already
        # structured API response fills any missing classifications afterwards.
        merge(candidate_groups)
        merge(baseline_groups)
        for norm in ordered:
            if norm not in assigned:
                add_tags_to_groups(out, "meta" if norm == "artist_request" else "general", [norm])
        return out

    def _flat_general_groups(self, tags):
        groups = empty_tag_groups()
        groups["general"] = unique_keep_order([normalize_tag(t) for t in (tags or []) if normalize_tag(t)])
        return groups

    def _groups_have_classified_tags(self, groups):
        return any((groups or {}).get(key) for key in ("artist", "character", "copyright", "species", "meta", "contributor", "lore", "invalid"))

    def _flat_source_html_category_overlay(self, host, url, api_tags, api_groups=None):
        """Classify an already-confirmed flat API tag set using page HTML only.

        The API/post response remains authoritative for membership.  HTML is
        allowed to move those same tags between categories, but it is never
        allowed to add sidebar/recommendation/search tags to the source record.
        When the durable background queue is enabled, failed HTML classification
        deliberately falls back to the original flat general set instead of
        running extra tag-catalogue requests in the same job.
        """
        host = (host or "").lower().replace("www.", "")
        api_tags = unique_keep_order([normalize_tag(t) for t in (api_tags or []) if normalize_tag(t)])
        if not api_tags:
            return empty_tag_groups()
        baseline = api_groups if groups_to_tags(api_groups or {}) else self._flat_general_groups(api_tags)
        try:
            session_host = "rule34.xxx" if host == "api.rule34.xxx" else host
            html = self.session_for_host(session_host).get(
                url, timeout=self.timeout,
                headers={"Accept": "text/html,application/xhtml+xml,*/*"},
            ).text
            if host in ("rule34.xxx", "api.rule34.xxx"):
                html_groups = self.booru_groups_from_html(html)
                shown = "rule34.xxx"
            else:
                html_groups = self.gelbooru_groups_from_html(html)
                shown = host
            safe_groups = self._guarded_category_projection(api_tags, html_groups, baseline)
            classified = sum(len(safe_groups.get(key, []) or []) for key in ("artist", "character", "copyright", "species", "meta"))
            if groups_to_tags(safe_groups):
                self.log(f"    {shown} TAG CATEGORY SOURCE: guarded_html_overlay classified={classified} general={len(safe_groups.get('general', []) or [])}")
                return safe_groups
        except Exception as e:
            self.log(f"    {host} HTML CATEGORY OVERLAY ERROR: {type(e).__name__}: {e}")

        # In the background category pipeline HTML is the sorter.  The tag-index
        # API remains a non-background fallback only, so foreground/queue work
        # never mutates the old flat-list contract.
        if self._needs_background_tag_groups(url):
            return self._guarded_category_projection(api_tags, {}, baseline)
        return self._categorize_flat_tags("rule34.xxx" if host == "api.rule34.xxx" else host, api_tags)

    def grouped_tags_from_url(self, url):
        host = urlparse(url).netloc.lower().replace("www.", "")

        try:
            if host in ("rule34.xxx", "api.rule34.xxx"):
                # Rule34 API decides membership; HTML only sorts those exact tags.
                api_tags = unique_keep_order(self.rule34xxx_tags(url))
                if not api_tags:
                    return empty_tag_groups()
                return self._flat_source_html_category_overlay(host, url, api_tags, self._flat_general_groups(api_tags))

            if host == "rule34.us":
                # No verified JSON API exists here. On a concrete post page,
                # read tag values from href only; never use rendered link text.
                if not self._is_strict_post_url(url):
                    return empty_tag_groups()
                r = self.session_for_host("rule34.us").get(
                    url, timeout=self.timeout,
                    headers={"Accept": "text/html,application/xhtml+xml,*/*"},
                )
                return self.gelbooru_groups_from_html(r.text or "")

            if host in ("danbooru.donmai.us", "donmai.us"):
                # Official Danbooru exposes post tags in JSON. Never parse the
                # visible HTML page as a metadata fallback: on a Cloudflare or
                # login page it is not authoritative data.
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
                    params=self._danbooru_api_params("danbooru.donmai.us"),
                    timeout=self.timeout,
                    headers=self._danbooru_api_headers("danbooru.donmai.us"),
                    auth=self._danbooru_auth_tuple("danbooru.donmai.us"),
                )
                self._danbooru_log_response_problem(r, "post-md5-extract")
                data = safe_json_response(r, "danbooru.donmai.us")
                return self._groups_from_post_dict_general(data if isinstance(data, dict) else {})

            if host in ("booru.allthefallen.moe", "allthefallen.moe"):
                return self.atf_groups_from_url(url)

            if host in ("gelbooru.com", "xbooru.com", "hypnohub.net"):
                # Gelbooru-family DAPI decides membership.  If the post API is
                # flat, HTML is used only as a guarded category overlay; it never
                # contributes new tags.  If the API itself already contains
                # split categories, preserve them directly.
                api_groups = self.documented_dapi_groups_from_url(url, host, categorize_flat=False)
                api_tags = unique_keep_order(groups_to_tags(api_groups))
                if not api_tags:
                    return empty_tag_groups()
                if self._groups_have_classified_tags(api_groups):
                    return self._guarded_category_projection(api_tags, {}, api_groups)
                return self._flat_source_html_category_overlay(host, url, api_tags, api_groups)

            if host in ("e621.net", "e926.net"):
                # e621 post JSON already exposes clean grouped tags. Never parse the
                # visible HTML sidebar: it contains UI labels/counts such as
                # ``Uploaded by the artist`` and ``4.2m`` that polluted SQLite.
                post_id = urlparse(url).path.strip("/").split("/")[-1]
                session = self.session_for_host(host)
                api_url = f"https://{host}/posts/{post_id}.json"
                params = self._e621_api_params(host, include_v2=True)
                auth = self._e621_auth_tuple(host)
                r = session.get(
                    api_url,
                    params=params,
                    timeout=self.timeout,
                    headers=self._e621_api_headers(host),
                    auth=auth,
                )
                self._e621_log_response_problem(r, "post-groups")
                if self._e621_is_cloudflare_html_response(r):
                    br = self._e621_browser_get_json_response(api_url, params=params, auth=auth, host=host, context="post-groups")
                    if br is not None:
                        r = br
                data = safe_json_response(r, host)
                post = data.get("post", data) if isinstance(data, dict) else {}
                return self._groups_from_post_dict_general(post)

        except Exception:
            pass

        return empty_tag_groups()

    def rule34xxx_tags(self, url):
        post_id = parse_qs(urlparse(url).query).get("id", [None])[0]
        if not post_id:
            return []
        api = "https://api.rule34.xxx/index.php"
        site = self.site_cfg("rule34.xxx")
        params = self._rule34xxx_api_params(site, id=post_id, limit=1)
        session = self.session_for_host("rule34.xxx")
        response = session.get(api, params=params, timeout=self.timeout, headers=self._rule34xxx_api_headers(site))
        self._rule34xxx_log_response_problem(response, "post-tags")
        data = safe_json_response(response, "rule34.xxx")
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
        """Read rule34.us post tags from a concrete page using href values only.

        rule34.us has Gelbooru-like browser search syntax but no confirmed JSON
        API. Visible tag link text is not metadata because it may include post
        counters; only ``tags=`` query values are accepted here.
        """
        try:
            if not self._is_strict_post_url(url):
                return []
            session = self.session_for_host("rule34.us")
            r = session.get(url, timeout=self.timeout, headers={"Accept": "text/html,application/xhtml+xml,*/*"})
            if int(getattr(r, "status_code", 0) or 0) >= 400:
                return []
            return self.gelbooru_tags_from_html(r.text or "")
        except Exception:
            return []

    def _post_id_from_danbooru_like_url(self, url):
        parts = urlparse(url).path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "post" and parts[1] == "show":
            return parts[2]
        if len(parts) >= 2 and parts[0] == "posts":
            return parts[1]
        return parts[-1] if parts else ""

    def atf_groups_from_url(self, url):
        """Read ATF/AllTheFallen post tags only from its Danbooru-compatible JSON API."""
        post_id = self._post_id_from_danbooru_like_url(url)
        if not post_id:
            return empty_tag_groups()
        host = "booru.allthefallen.moe"
        session = self.session_for_host(host)
        r = self._atf_get(
            session,
            f"https://booru.allthefallen.moe/posts/{post_id}.json",
            host,
            params=self._danbooru_api_params(host),
            timeout=self.timeout,
            headers=self._danbooru_api_headers(host),
            auth=self._danbooru_auth_tuple(host),
        )
        self._danbooru_log_response_problem(r, "post-json", host)
        data = safe_json_response(r, host)
        post = data.get("post", data) if isinstance(data, dict) else {}
        return self._groups_from_post_dict_general(post)

    def atf_tags(self, url):
        return groups_to_tags(self.atf_groups_from_url(url))

    def danbooru_tags(self, url):
        """Read official Danbooru post tags only from its JSON API."""
        parts = urlparse(url).path.strip("/").split("/")

        if len(parts) >= 3 and parts[0] == "post" and parts[1] == "show":
            post_id = parts[2]
        elif len(parts) >= 2 and parts[0] == "posts":
            post_id = parts[1]
        else:
            post_id = parts[-1]

        session = self.session_for_host("danbooru.donmai.us")
        r = session.get(
            f"https://danbooru.donmai.us/posts/{post_id}.json",
            params=self._danbooru_api_params("danbooru.donmai.us"),
            timeout=self.timeout,
            headers=self._danbooru_api_headers("danbooru.donmai.us"),
            auth=self._danbooru_auth_tuple("danbooru.donmai.us"),
        )
        self._danbooru_log_response_problem(r, "post-json")
        data = safe_json_response(r, "danbooru.donmai.us")
        return groups_to_tags(self._groups_from_post_dict_general(data if isinstance(data, dict) else {}))

    

    def _danbooru_tag_from_href(self, href):
        """Return one real Danbooru tag encoded in a tag-search link.

        Restricted post pages may expose visible sidebar tags when the JSON
        object omits them.  Never read ``a.text`` here: it can include counts,
        action labels or other UI text.  Only a single ``tags=...`` value from
        a sidebar link is accepted.
        """
        try:
            query = parse_qs(urlparse(html.unescape(str(href or ""))).query)
            values = query.get("tags") or []
        except Exception:
            return ""
        if len(values) != 1:
            return ""
        raw = html.unescape(str(values[0] or "")).strip()
        # A tag link names exactly one tag.  Search/filter links such as
        # ``tags=foo bar`` or ``tags=rating:s`` are not metadata tags.
        if not raw or len(raw.split()) != 1:
            return ""
        tag = normalize_tag(raw)
        low = tag.lower()
        if not tag or low.startswith((
            "rating:", "order:", "sort:", "md5:", "id:", "user:",
            "status:", "filetype:", "parent:", "source:",
        )):
            return ""
        if any(ch in tag for ch in ("/", "?", "=", "&", "#")):
            return ""
        return tag

    def _danbooru_groups_from_confirmed_html(self, html_text):
        """Extract restricted Danbooru sidebar tags from href values only.

        The caller must already have a concrete post returned by Danbooru's
        exact MD5 JSON query.  This routine is deliberately unusable as a
        general HTML-search fallback and therefore cannot apply Cloudflare or
        login-page text as metadata.
        """
        text = str(html_text or "")
        head = text[:30000].lower()
        if any(marker in head for marker in (
            "just a moment", "cf-chl-", "cloudflare",
            "attention required", "captcha",
        )):
            return empty_tag_groups()

        soup = BeautifulSoup(text, "html.parser")
        groups = empty_tag_groups()
        group_classes = {
            "artist": ("tag-type-artist", "category-1", "tag-type-1"),
            "copyright": ("tag-type-copyright", "category-3", "tag-type-3"),
            "character": ("tag-type-character", "category-4", "tag-type-4"),
            "general": ("tag-type-general", "category-0", "tag-type-0"),
            "meta": ("tag-type-meta", "tag-type-metadata", "category-5", "tag-type-5"),
        }
        selectors = [
            "#tag-list li", "ul#tag-list li", ".tag-list li",
            "li[class*='tag-type-']", "li[class*='category-']",
        ]
        nodes = []
        seen_nodes = set()
        for selector in selectors:
            for node in soup.select(selector):
                marker = id(node)
                if marker not in seen_nodes:
                    seen_nodes.add(marker)
                    nodes.append(node)

        for node in nodes:
            cls = " ".join(node.get("class", [])).lower()
            group = "general"
            for candidate, needles in group_classes.items():
                if any(needle in cls for needle in needles):
                    group = candidate
                    break
            # Current Danbooru renders the real tag as ``a.search-tag`` and
            # the visible post count as a separate sibling.  Prefer that exact
            # contract; retain href-only compatibility for older page variants.
            links = node.select("a.search-tag[href*='tags=']")
            if not links:
                links = node.select("a[href*='tags=']")
            for link in links:
                tag = self._danbooru_tag_from_href(link.get("href", ""))
                if tag:
                    groups[group].append(tag)

        for group in groups:
            groups[group] = unique_keep_order(groups[group])
        return groups

    def _danbooru_confirmed_html_fallback(self, session, post_id):
        """Fetch HTML only for an already confirmed restricted Danbooru post."""
        post_id = str(post_id or "").strip()
        if not post_id or not post_id.isdigit():
            return empty_tag_groups()
        post_url = f"https://danbooru.donmai.us/posts/{post_id}"
        try:
            response = self._http_get_cached(
                session, post_url, timeout=self.timeout,
                headers={"Accept": "text/html,application/xhtml+xml,*/*"},
            )
            if int(getattr(response, "status_code", 0) or 0) >= 400:
                self.log(f"    danbooru.donmai.us RESTRICTED HTML SKIP: post={post_id} status={response.status_code}")
                return empty_tag_groups()
            groups = self._danbooru_groups_from_confirmed_html(response.text or "")
            tags = groups_to_tags(groups)
            if tags:
                self.log(f"    danbooru.donmai.us TAG SOURCE: html_sidebar_href post={post_id} tags={len(tags)}")
            else:
                self.log(f"    danbooru.donmai.us RESTRICTED HTML SKIP: post={post_id} no href tags")
            return groups
        except Exception as e:
            self.log(f"    danbooru.donmai.us RESTRICTED HTML ERROR: post={post_id} {type(e).__name__}: {e}")
            return empty_tag_groups()


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
            # Sidebar links may include a visible global count: "horse 231k" / "meesh 2.0k".
            # Strip it before spaces become underscores; the API tag itself is never touched here.
            t = re.sub(r"(?:\s+|_)\d+(?:[.,]\d+)?[kmb]?\s*$", "", t, flags=re.IGNORECASE).strip()
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
            "species": "species", "вид": "species", "виды": "species",
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
                for key in ("artist", "character", "copyright", "species", "general", "meta"):
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
            "species": ["li.tag-type-species a", ".tag-type-species a", "li[class*='species'] a", "a.tag-type-species"],
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
        """Dormant compatibility parser: recover only tag values encoded in href.

        Automatic Gelbooru scanning is JSON-only; rule34.us uses this parser
        only after exact MD5 verification of a concrete HTML post page. It deliberately ignores rendered link text,
        counters and meta-keywords to avoid interface-text tags in SQLite.
        """
        soup = BeautifulSoup(html or "", "html.parser")
        tags = []
        selectors = [
            "li.tag-type-general a[href*='tags=']",
            "li.tag-type-character a[href*='tags=']",
            "li.tag-type-copyright a[href*='tags=']",
            "li.tag-type-artist a[href*='tags=']",
            "li.tag-type-metadata a[href*='tags=']",
            "li.tag-type-meta a[href*='tags=']",
            "ul#tag-list a[href*='tags=']",
            "#tag-list a[href*='tags=']",
            "aside a[href*='tags=']",
            "a[href*='page=post'][href*='tags=']",
        ]
        for sel in selectors:
            for a in soup.select(sel):
                href = a.get("href", "") or ""
                try:
                    for raw in parse_qs(urlparse(href).query).get("tags", []):
                        for tag in str(raw).replace("+", " ").split():
                            tag = normalize_tag(tag)
                            if tag and not tag.startswith(("rating:", "sort:", "md5:")):
                                tags.append(tag)
                except Exception:
                    pass
        return self._filter_recovered_gelbooru_tags(unique_keep_order(tags))

    def gelbooru_groups_from_html(self, html):
        """Extract Gelbooru sidebar categories from href tag values only.

        Gelbooru has used several sidebar layouts over time: named classes such
        as ``tag-type-artist``, numeric classes such as ``tag-type-1``, and
        heading-based sections (``Artist`` / ``Character`` / ``Metadata``).
        The caller still guards membership against the exact DAPI post tags, so
        this method is permitted to classify tags but never to introduce a new
        source tag into SQLite.
        """
        soup = BeautifulSoup(html or "", "html.parser")
        groups = empty_tag_groups()
        class_selectors = {
            # Gelbooru numeric classes follow the common booru category IDs:
            # 0 general, 1 artist, 3 copyright, 4 character, 5 metadata.
            "artist": ["li.tag-type-artist", ".tag-type-artist", "li.tag-type-1", ".tag-type-1", "li.category-1", ".category-1"],
            "character": ["li.tag-type-character", ".tag-type-character", "li.tag-type-4", ".tag-type-4", "li.category-4", ".category-4"],
            "copyright": ["li.tag-type-copyright", "li.tag-type-copyrights", ".tag-type-copyright", ".tag-type-copyrights", "li.tag-type-3", ".tag-type-3", "li.category-3", ".category-3"],
            "general": ["li.tag-type-general", ".tag-type-general", "li.tag-type-0", ".tag-type-0", "li.category-0", ".category-0"],
            "meta": ["li.tag-type-metadata", "li.tag-type-meta", ".tag-type-metadata", ".tag-type-meta", "li.tag-type-5", ".tag-type-5", "li.category-5", ".category-5"],
        }

        def append_links(group, node):
            for a in node.select("a[href*='tags=']"):
                href = a.get("href", "") or ""
                for tag in self._clean_booru_tag_candidate("", href):
                    tag = normalize_tag(tag)
                    if tag and tag not in groups[group]:
                        groups[group].append(tag)

        for group, selectors in class_selectors.items():
            seen_nodes = set()
            for selector in selectors:
                for node in soup.select(selector):
                    marker = id(node)
                    if marker in seen_nodes:
                        continue
                    seen_nodes.add(marker)
                    append_links(group, node)

        # Current/alternate Gelbooru themes can render only textual section
        # headings while the tag links themselves have no category class.
        section_groups = self._groups_from_sidebar_sections(soup)
        for group, values in (section_groups or {}).items():
            for tag in values or []:
                tag = normalize_tag(tag)
                if tag and tag not in groups[group]:
                    groups[group].append(tag)

        # Preserve compatibility with uncategorised pages; the DAPI guard in the
        # caller will still discard all non-post/sidebar pollution.
        if not groups_to_tags(groups):
            for a in soup.select("a[href*='tags=']"):
                for tag in self._clean_booru_tag_candidate("", a.get("href", "") or ""):
                    tag = normalize_tag(tag)
                    if tag and tag not in groups["general"]:
                        groups["general"].append(tag)

        for key in groups:
            groups[key] = self._filter_recovered_gelbooru_tags(unique_keep_order(groups[key]))
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
        elif host in ("xbooru.com", "hypnohub.net"):
            base = f"https://{host}/index.php"
            session_host = host
        else:
            groups["general"] = clean
            return groups

        remaining = set(clean)
        cache_updates = {}
        try:
            from core.database.storage import cached_tag_categories
            cached = cached_tag_categories(self.settings, host, clean)
        except Exception:
            cached = {}
        for _name, _cat in dict(cached or {}).items():
            if _name in remaining:
                add_tags_to_groups(groups, _cat or "general", [_name])
                remaining.discard(_name)
        if not remaining:
            return groups
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
            if host == "rule34.xxx":
                params.update(self._rule34xxx_api_auth_params(self.site_cfg("rule34.xxx")))
                req_headers = self._rule34xxx_api_headers(self.site_cfg("rule34.xxx"))
            else:
                params.update(self.auth_params(self.site_cfg(session_host)))
                req_headers = None
            try:
                r = s.get(base, params=params, timeout=self.timeout, headers=req_headers)
                if host == "rule34.xxx":
                    self._rule34xxx_log_response_problem(r, "tag-category")
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
                    cache_updates[name] = group
                    remaining.discard(name)
            except Exception as e:
                self.log(f"    TAG CATEGORY ERROR [{host}]: {e}")

        # Gelbooru's current documentation explicitly supports names=<many tags>.
        # Do not turn one matched post into tens of API requests in its live
        # lane; a failed/unclassified batch will be retried by the guarded
        # background overlay.  Keep the expensive compatibility fallback only
        # for other Gelbooru-family engines whose API behaviour is uncertain.
        single_tag_fallback = host != "gelbooru.com"
        for tag in list(remaining) if single_tag_fallback else []:
            for key in ("name", "name_pattern"):
                try:
                    params = {"page": "dapi", "s": "tag", "q": "index", "json": "1", key: tag}
                    if host == "rule34.xxx":
                        params.update(self._rule34xxx_api_auth_params(self.site_cfg("rule34.xxx")))
                        req_headers = self._rule34xxx_api_headers(self.site_cfg("rule34.xxx"))
                    else:
                        params.update(self.auth_params(self.site_cfg(session_host)))
                        req_headers = None
                    r = s.get(base, params=params, timeout=self.timeout, headers=req_headers)
                    if host == "rule34.xxx":
                        self._rule34xxx_log_response_problem(r, "tag-category-single")
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
                        group = group_from_tag_type(typ)
                        add_tags_to_groups(groups, group, [tag])
                        cache_updates[tag] = group
                        remaining.discard(tag)
                        found = True
                        break
                    if found:
                        break
                except Exception:
                    pass

        try:
            if cache_updates:
                from core.database.storage import upsert_tag_category_cache
                upsert_tag_category_cache(self.settings, host, cache_updates, method="dapi_tag_api")
        except Exception:
            pass

        # Do not lose unknown tags. They still belong to the exact post, even
        # when the tag catalogue does not return a category for them.
        add_tags_to_groups(groups, "general", [tag for tag in clean if tag in remaining])
        if host in ("gelbooru.com", "rule34.xxx"):
            classified = sum(len(groups.get(key, []) or []) for key in ("artist", "character", "copyright", "meta"))
            self.log(f"    {host} TAG CATEGORY SOURCE: dapi_tag_api classified={classified} general={len(groups.get('general', []) or [])}")
        return groups

    def _categorize_flat_tags(self, source_host, tags):
        source_host = (source_host or "").lower().replace("www.", "")
        if source_host in ("gelbooru.com", "rule34.xxx", "xbooru.com", "hypnohub.net"):
            groups = self._categorize_tags_via_dapi(source_host, tags)
            if groups_to_tags(groups):
                return groups
        groups = empty_tag_groups()
        groups["general"] = unique_keep_order([normalize_tag(t) for t in tags or [] if normalize_tag(t)])
        return groups

    def gelbooru_groups_from_post(self, post, source_host="gelbooru.com", *, categorize_flat=True):
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

        # Older Gelbooru-family DAPI exposes only a flat "tags" field.  In the
        # live conveyor/background path this must stay flat and be sorted later
        # by guarded HTML overlay; the tag-list API is only a fallback when
        # explicit immediate categorisation is requested.
        flat = self.gelbooru_tags_from_post(post)
        if flat:
            if not categorize_flat:
                return self._flat_general_groups(flat)
            return self._categorize_flat_tags(source_host, flat)
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

    def documented_dapi_groups_from_url(self, url, host, *, categorize_flat=True):
        """Read Gelbooru-family post metadata only from exact JSON DAPI calls."""
        host = str(host or "").lower().replace("www.", "")
        if host not in {"gelbooru.com", "xbooru.com", "hypnohub.net"}:
            return empty_tag_groups()
        q = parse_qs(urlparse(url).query)
        post_id = q.get("id", [None])[0]
        md5_value = q.get("md5", [None])[0]
        tag_query = q.get("tags", [None])[0]
        if post_id:
            lookup = {"id": post_id}
        elif md5_value:
            lookup = {"tags": f"md5:{md5_value}", "limit": 1}
        elif isinstance(tag_query, str) and tag_query.lower().startswith("md5:"):
            lookup = {"tags": tag_query, "limit": 1}
        else:
            return empty_tag_groups()
        params = {"page": "dapi", "s": "post", "q": "index", "json": "1", **lookup}
        params.update(self.auth_params(self.site_cfg(host)))
        r = self.session_for_host(host).get(f"https://{host}/index.php", params=params, timeout=self.timeout)
        posts = self._posts_from_dapi_response(r, host)
        if posts:
            return self.gelbooru_groups_from_post(posts[0], host, categorize_flat=categorize_flat)
        return empty_tag_groups()

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
        """Read Gelbooru tags from DAPI only; never scrape visible page text."""
        q = parse_qs(urlparse(url).query)
        post_id = q.get("id", [None])[0]
        if q.get("s", [""])[0] == "list" and q.get("md5"):
            return self.gelbooru_tags_by_md5(q["md5"][0])
        if not post_id:
            return []
        posts = self.gelbooru_dapi_posts({"id": post_id})
        if posts:
            return self._filter_recovered_gelbooru_tags(self.gelbooru_tags_from_post(posts[0]))
        self.log(f"    gelbooru.com DAPI JSON only: no post id={post_id}; HTML fallback disabled")
        return []

    def gelbooru_tags_by_md5(self, md5):
        """Read Gelbooru tags only after the documented exact MD5 DAPI lookup."""
        posts = self.gelbooru_dapi_posts({"tags": f"md5:{md5}", "limit": 1})
        for post in posts:
            if not self._verify_builtin_post_md5("gelbooru.com", post, md5):
                continue
            tags = self._filter_recovered_gelbooru_tags(self.gelbooru_tags_from_post(post))
            if tags:
                return tags
        self.log("    gelbooru.com DAPI JSON only: no exact API MD5 match; HTML fallback disabled")
        return []


    def _danbooru_site_cfg(self, host="danbooru.donmai.us"):
        """Return saved settings for official Danbooru.

        Danbooru accepts API credentials either as query params or as HTTP
        Basic auth.  Prefer the canonical saved host so transient ``donmai.us``
        URLs and normalized site rows do not lose the user's configured key.
        """
        host = str(host or "danbooru.donmai.us").lower().replace("www.", "")
        candidates = []
        if host in ("danbooru.donmai.us", "donmai.us"):
            candidates.extend(["danbooru.donmai.us", host])
        elif "allthefallen" in host:
            candidates.extend(["booru.allthefallen.moe", host])
        else:
            candidates.append(host)
        for key in candidates:
            cfg = self.site_cfg(key)
            if isinstance(cfg, dict) and cfg:
                return cfg
        return {}

    def _danbooru_api_headers(self, host="danbooru.donmai.us"):
        """Headers for the official Danbooru JSON API.

        Danbooru's API documentation asks clients to identify themselves with a
        unique application User-Agent and not to impersonate browsers or the
        default library header.  Browser cookies may still be loaded by the
        session layer for Cloudflare/login pages, but API requests must override
        that browser UA with a truthful Local Booru API UA.
        """
        cfg = self._danbooru_site_cfg(host)
        login = str(cfg.get("login") or "").strip() if isinstance(cfg, dict) else ""
        configured = str((cfg or {}).get("user_agent") or (cfg or {}).get("api_user_agent") or "").strip() if isinstance(cfg, dict) else ""
        if configured:
            ua = configured
        else:
            if "allthefallen" in host:
                product = "ATF"
            elif host in ("danbooru.donmai.us", "donmai.us"):
                product = "Danbooru"
            else:
                product = host or "Danbooru-compatible API"
            identity = f"by {login} on {product}" if login else "local archive manager"
            ua = f"LocalBooru/3.6 ({identity})"
        return {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": ua,
        }

    def _danbooru_auth_tuple(self, host="danbooru.donmai.us"):
        """Return HTTP Basic auth tuple for official Danbooru, if configured."""
        cfg = self._danbooru_site_cfg(host)
        if not isinstance(cfg, dict):
            return None
        login = str(cfg.get("login") or "").strip()
        api_key = str(cfg.get("api_key") or "").strip()
        if not login or not api_key:
            return None
        return (login, api_key)

    def _danbooru_api_params(self, host="danbooru.donmai.us"):
        """Official Danbooru query params, without credentials when Basic auth exists."""
        if self._danbooru_auth_tuple(host) is not None:
            return {}
        return self.auth_params(self._danbooru_site_cfg(host))

    def _danbooru_is_cloudflare_html_response(self, response):
        try:
            status = int(getattr(response, "status_code", 0) or 0)
            ct = str(getattr(response, "headers", {}).get("content-type", "") or "").lower()
            text = (getattr(response, "text", "") or "")[:4000].lower()
        except Exception:
            return False
        return (
            status in (403, 503, 520, 522, 524)
            and "html" in ct
            and ("cloudflare" in text or "just a moment" in text or "verify you are human" in text or "security verification" in text)
        )

    def _danbooru_log_response_problem(self, response, context="API", host="danbooru.donmai.us"):
        """Log actionable Danbooru-compatible API diagnostics before JSON parsing."""
        try:
            shown = str(host or "danbooru.donmai.us").lower().replace("www.", "")
            if shown == "donmai.us":
                shown = "danbooru.donmai.us"
            status = int(getattr(response, "status_code", 0) or 0)
            ct = str(getattr(response, "headers", {}).get("content-type", "") or "")
            text = (getattr(response, "text", "") or "")[:220].replace("\n", " ").replace("\r", " ")
            if self._danbooru_is_cloudflare_html_response(response):
                self.log(
                    f"    {shown} {context}: CLOUDFLARE/403 HTML instead of JSON; "
                    f"pass browser verification, save cookies, and check official User-Agent + Basic Auth"
                )
                return True
            if status == 401:
                self.log(f"    {shown} {context}: 401 Unauthorized; login/api_key invalid")
                return True
            if status == 403:
                self.log(f"    {shown} {context}: 403 Forbidden; check permissions, official User-Agent and API auth (ct={ct!r})")
                return True
            if status in (421, 429):
                self.log(f"    {shown} {context}: {status} throttled/rate limited; response={text!r}")
                return True
            if status in (502, 503):
                self.log(f"    {shown} {context}: server/load status={status}; response={text!r}")
                return True
        except Exception:
            pass
        return False

    def _e621_client_param(self, host="e621.net"):
        """Value for e621's browser-extension/userscript _client parameter.

        e621 asks browser-bound clients that cannot set a custom User-Agent to
        identify themselves with _client. The normal requests path still uses
        the official User-Agent header; the browser fallback uses _client
        because fetch() runs inside a real browser context.
        """
        try:
            cfg = self.site_cfg(host)
            login = str((cfg or {}).get("login") or "").strip() if isinstance(cfg, dict) else ""
        except Exception:
            login = ""
        identity = f"by {login} on e621" if login else "local archive manager"
        return f"LocalBooru/3.7 ({identity})"

    def _e621_url_with_client_param(self, url, params=None, host="e621.net"):
        params2 = dict(params or {})
        params2.setdefault("_client", self._e621_client_param(host))
        qs = urlencode(params2, doseq=True)
        return str(url) + (("&" if "?" in str(url) else "?") + qs if qs else "")

    def _e621_auth_header_value(self, auth):
        try:
            if not auth:
                return ""
            login, api_key = auth[0], auth[1]
            token = base64.b64encode(f"{login}:{api_key}".encode("utf-8")).decode("ascii")
            return f"Basic {token}"
        except Exception:
            return ""

    def _e621_browser_api_enabled(self):
        return bool(self.settings.get("e621_browser_api_fallback", True))

    def _e621_browser_backend(self):
        backend = str(self.settings.get("e621_browser_api_backend") or "companion_extension").strip().lower()
        if backend not in ("companion_extension", "external_chrome_cdp", "playwright_chromium"):
            backend = "companion_extension"
        # v314's default external CDP profile is unreliable with e621 Cloudflare and
        # can hang forever on the managed challenge.  Keep it available only as an
        # explicit advanced option; the normal fallback is the user-installed
        # browser companion extension running in the user's real browser session.
        if backend == "external_chrome_cdp" and not bool(self.settings.get("e621_browser_api_allow_external_chrome_cdp", False)):
            backend = "companion_extension"
        return backend

    def _e621_cdp_port(self):
        try:
            return int(self.settings.get("e621_browser_api_cdp_port", 9222) or 9222)
        except Exception:
            return 9222

    def _e621_cdp_version_url(self):
        return f"http://127.0.0.1:{self._e621_cdp_port()}/json/version"

    def _e621_cdp_is_ready(self):
        try:
            with urllib.request.urlopen(self._e621_cdp_version_url(), timeout=1.5) as r:
                return 200 <= int(getattr(r, "status", 200) or 200) < 300
        except Exception:
            return False

    def _find_external_chrome_exe(self):
        candidates = []
        for env_name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env_name)
            if not base:
                continue
            candidates.extend([
                Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ])
        candidates.extend([
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        ])
        for c in candidates:
            try:
                if c.exists():
                    return str(c)
            except Exception:
                pass
        for name in ("chrome.exe", "msedge.exe", "chrome", "chromium", "google-chrome", "microsoft-edge"):
            try:
                found = shutil.which(name)
                if found:
                    return found
            except Exception:
                pass
        return ""

    def _launch_external_chrome_for_e621(self, host="e621.net"):
        if not bool(self.settings.get("e621_browser_api_launch_external_chrome", True)):
            return False
        if self._e621_cdp_is_ready():
            return True
        exe = self._find_external_chrome_exe()
        if not exe:
            self.log("    e621.net BROWSER API: external Chrome/Edge not found; fallback to Playwright Chromium")
            return False
        profile_dir = Path(BROWSER_PROFILE_DIR) / "e621_external_chrome_cdp"
        profile_dir.mkdir(parents=True, exist_ok=True)
        port = self._e621_cdp_port()
        args = [
            exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
            f"https://{host}/",
        ]
        try:
            self.log(f"    e621.net BROWSER API: launching external Chrome/Edge via CDP port={port} profile={profile_dir}")
            self._e621_browser_process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            self.log(f"    e621.net BROWSER API: external Chrome launch failed: {type(e).__name__}: {e}")
            return False
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            if self._e621_cdp_is_ready():
                return True
            time.sleep(0.35)
        self.log("    e621.net BROWSER API: external Chrome CDP did not become ready; fallback to Playwright Chromium")
        return False

    def _ensure_e621_browser_page(self, host="e621.net"):
        if sync_playwright is None:
            self.log("    e621.net BROWSER API SKIP: Playwright not installed; install: python -m pip install playwright")
            return None
        host = str(host or "e621.net").lower().replace("www.", "")
        if host not in ("e621.net", "e926.net"):
            host = "e621.net"
        backend = self._e621_browser_backend()
        try:
            if (self._e621_browser_context is not None and self._e621_browser_page is not None
                    and self._e621_browser_host == host and getattr(self, "_e621_browser_backend_active", "") == backend):
                return self._e621_browser_page
        except Exception:
            pass
        try:
            self._e621_browser_pw = sync_playwright().start()
            if backend == "external_chrome_cdp":
                if self._launch_external_chrome_for_e621(host):
                    self.log(f"    e621.net BROWSER API: connecting to external Chrome/Edge CDP port={self._e621_cdp_port()}")
                    self._e621_browser_browser = self._e621_browser_pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self._e621_cdp_port()}")
                    self._e621_browser_context = self._e621_browser_browser.contexts[0] if self._e621_browser_browser.contexts else self._e621_browser_browser.new_context()
                    pages = list(self._e621_browser_context.pages or [])
                    self._e621_browser_page = pages[0] if pages else self._e621_browser_context.new_page()
                    try:
                        self._e621_browser_page.goto(f"https://{host}/", wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                    self._e621_browser_host = host
                    self._e621_browser_backend_active = backend
                    return self._e621_browser_page
                # If external browser cannot be launched/attached, continue with old Playwright backend.

            profile_dir = Path(BROWSER_PROFILE_DIR) / "e621_browser_api"
            profile_dir.mkdir(parents=True, exist_ok=True)
            headless = bool(self.settings.get("e621_browser_api_headless", False))
            self.log(f"    e621.net BROWSER API: opening Playwright Chromium profile={profile_dir} headless={headless}")
            self._e621_browser_context = self._e621_browser_pw.chromium.launch_persistent_context(
                str(profile_dir),
                headless=headless,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            self._e621_browser_page = self._e621_browser_context.pages[0] if self._e621_browser_context.pages else self._e621_browser_context.new_page()
            self._e621_browser_host = host
            self._e621_browser_backend_active = "playwright_chromium"
            return self._e621_browser_page
        except Exception as e:
            self.log(f"    e621.net BROWSER API ERROR: {type(e).__name__}: {e}")
            try:
                if self._e621_browser_context:
                    self._e621_browser_context.close()
            except Exception:
                pass
            try:
                if getattr(self, "_e621_browser_browser", None):
                    self._e621_browser_browser.close()
            except Exception:
                pass
            try:
                if self._e621_browser_pw:
                    self._e621_browser_pw.stop()
            except Exception:
                pass
            self._e621_browser_context = None
            self._e621_browser_page = None
            self._e621_browser_browser = None
            self._e621_browser_pw = None
            return None

    def _e621_page_has_cloudflare(self, page):
        try:
            text = (page.content() or "")[:5000].lower()
            title = ""
            try:
                title = (page.title() or "").lower()
            except Exception:
                pass
            return any(x in (text + " " + title) for x in ("cloudflare", "just a moment", "verify you are human", "security verification"))
        except Exception:
            return False

    def _e621_wait_browser_verification(self, page, host="e621.net"):
        host = str(host or "e621.net").lower().replace("www.", "")
        if host in self._e621_browser_verified_hosts:
            return True
        try:
            page.goto(f"https://{host}/", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        if not self._e621_page_has_cloudflare(page):
            self._e621_browser_verified_hosts.add(host)
            return True
        timeout_s = max(5, int(float(self.settings.get("e621_browser_api_verify_timeout_seconds", 120) or 120)))
        self.log(f"    e621.net BROWSER API: Cloudflare page is open; pass verification in the browser window ({timeout_s}s)")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.cancelled():
                return False
            try:
                page.wait_for_timeout(1500)
            except Exception:
                time.sleep(1.5)
            if not self._e621_page_has_cloudflare(page):
                self.log("    e621.net BROWSER API: browser verification passed")
                self._e621_browser_verified_hosts.add(host)
                return True
        self.log("    e621.net BROWSER API: verification timeout; request remains blocked")
        return False

    def _e621_companion_get_json_response(self, url, *, params=None, auth=None, host="e621.net", context="api"):
        if _enqueue_e621_browser_fetch is None:
            self.log("    e621.net COMPANION API SKIP: browser companion bridge is unavailable")
            return None
        host = str(host or "e621.net").lower().replace("www.", "")
        if host not in ("e621.net", "e926.net"):
            host = "e621.net"
        full_url = self._e621_url_with_client_param(url, params=params, host=host)
        auth_header = self._e621_auth_header_value(auth)
        try:
            timeout_s = float(self.settings.get("e621_browser_api_companion_timeout_seconds", 120) or 120)
        except Exception:
            timeout_s = 120
        self.log("    e621.net COMPANION API: waiting for installed Chrome extension/e621 tab to fetch JSON")
        result = _enqueue_e621_browser_fetch(full_url, auth_header=auth_header, timeout_s=timeout_s)
        if not result:
            self.log("    e621.net COMPANION API: no response; install/update companion extension and keep e621.net open in normal Chrome")
            return None
        status = int((result or {}).get("status") or 0)
        text = str((result or {}).get("text") or "")
        headers = dict((result or {}).get("headers") or {})
        if "content-type" not in {str(k).lower(): v for k, v in headers.items()}:
            headers["content-type"] = "application/json" if text.strip().startswith(("{", "[")) else "text/html"
        if (result or {}).get("error"):
            self.log(f"    e621.net COMPANION API {context} ERROR: {(result or {}).get('error')}")
        bridge_mode = str((result or {}).get("bridge_mode") or "")
        page_title = str((result or {}).get("page_title") or "")
        page_fetch_error = str((result or {}).get("page_fetch_error") or "")
        mode_note = f" mode={bridge_mode}" if bridge_mode else ""
        self.log(f"    e621.net COMPANION API {context}: status={status} ct={headers.get('content-type','')}{mode_note}")
        if page_fetch_error:
            self.log(f"    e621.net COMPANION API {context}: page-context fetch failed; fallback used: {page_fetch_error}")
        if status in (403, 503) and page_title:
            self.log(f"    e621.net COMPANION API {context}: e621 tab title={page_title!r}")
        return _SyntheticHTTPResponse(status, text, headers, (result or {}).get("url") or full_url)

    def _e621_browser_get_json_response(self, url, *, params=None, auth=None, host="e621.net", context="api"):
        """Fetch e621 JSON from a real browser context after Cloudflare verification.

        This is not an HTML tag parser and not a proxy/antidetect bypass. It is
        the same official JSON endpoint, but executed inside the user-verified
        browser session. The request includes e621's documented _client param so
        Local Booru is still identified even though fetch() uses the browser UA.
        """
        if not self._e621_browser_api_enabled():
            return None
        host = str(host or "e621.net").lower().replace("www.", "")
        if host not in ("e621.net", "e926.net"):
            host = "e621.net"
        with _E621_BROWSER_API_LOCK:
            backend = self._e621_browser_backend()
            if backend == "companion_extension":
                return self._e621_companion_get_json_response(url, params=params, auth=auth, host=host, context=context)
            page = self._ensure_e621_browser_page(host)
            if page is None:
                return None
            if not self._e621_wait_browser_verification(page, host):
                return None
            full_url = self._e621_url_with_client_param(url, params=params, host=host)
            auth_header = self._e621_auth_header_value(auth)
            try:
                result = page.evaluate(
                    """async ({url, authHeader}) => {
                        const headers = {"Accept": "application/json"};
                        if (authHeader) headers["Authorization"] = authHeader;
                        const resp = await fetch(url, {
                            method: "GET",
                            credentials: "include",
                            headers
                        });
                        const text = await resp.text();
                        const outHeaders = {};
                        resp.headers.forEach((v, k) => { outHeaders[k] = v; });
                        return {status: resp.status, url: resp.url, headers: outHeaders, text};
                    }""",
                    {"url": full_url, "authHeader": auth_header},
                )
                status = int((result or {}).get("status") or 0)
                text = str((result or {}).get("text") or "")
                headers = dict((result or {}).get("headers") or {})
                if "content-type" not in {str(k).lower(): v for k, v in headers.items()}:
                    headers["content-type"] = "application/json" if text.strip().startswith(("{", "[")) else "text/html"
                self.log(f"    e621.net BROWSER API {context}: status={status} ct={headers.get('content-type','')}")
                return _SyntheticHTTPResponse(status, text, headers, (result or {}).get("url") or full_url)
            except Exception as e:
                self.log(f"    e621.net BROWSER API {context} ERROR: {type(e).__name__}: {e}")
                return None

    def _e621_api_headers(self, host="e621.net"):
        """Headers required by the official e621/e926 JSON API.

        e621 requires a descriptive non-empty User-Agent and explicitly asks
        clients not to impersonate a browser.  Do not reuse the Cloudflare/browser
        User-Agent saved with cookies here; API requests must identify Local
        Booru as an API client.
        """
        host = str(host or "e621.net").lower().replace("www.", "")
        cfg = self.site_cfg(host)
        login = str(cfg.get("login") or "").strip() if isinstance(cfg, dict) else ""
        configured = str((cfg or {}).get("user_agent") or (cfg or {}).get("api_user_agent") or "").strip() if isinstance(cfg, dict) else ""
        if configured:
            ua = configured
        else:
            identity = f"by {login} on e621" if login else "local archive manager"
            ua = f"LocalBooru/3.3 ({identity})"
        return {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": ua,
        }

    def _e621_auth_tuple(self, host="e621.net"):
        """Return Basic-auth tuple for e621/e926 or None for public calls.

        The API docs prefer HTTP Basic auth.  Keeping login/api_key out of query
        parameters also prevents secrets from leaking into request URLs/logs.
        """
        host = str(host or "e621.net").lower().replace("www.", "")
        cfg = self.site_cfg(host)
        if not isinstance(cfg, dict):
            return None
        login = str(cfg.get("login") or "").strip()
        api_key = str(cfg.get("api_key") or "").strip()
        if not login or not api_key:
            return None
        return (login, api_key)

    def _e621_api_params(self, host="e621.net", *, include_v2=True):
        """Common e621 query params, excluding credentials when Basic auth exists."""
        params = {}
        if include_v2:
            params.update({"v2": "true", "mode": "extended"})
        if self._e621_auth_tuple(host) is None:
            # Legacy fallback only: if the user configured login/api_key but the
            # auth tuple cannot be built, auth_params keeps older setups working.
            params.update(self.auth_params(self.site_cfg(host)))
        return params

    def _e621_is_cloudflare_html_response(self, response):
        try:
            status = int(getattr(response, "status_code", 0) or 0)
            ct = str(getattr(response, "headers", {}).get("content-type", "") or "").lower()
            text = (getattr(response, "text", "") or "")[:4000].lower()
        except Exception:
            return False
        return (
            status in (403, 503, 520, 522, 524)
            and "html" in ct
            and ("cloudflare" in text or "just a moment" in text or "verify you are human" in text or "security verification" in text)
        )

    def _e621_log_response_problem(self, response, context="API"):
        """Return True if a clearer e621 diagnostic was logged."""
        try:
            status = int(getattr(response, "status_code", 0) or 0)
            ct = str(getattr(response, "headers", {}).get("content-type", "") or "")
            text = (getattr(response, "text", "") or "")[:220].replace("\n", " ").replace("\r", " ")
            if self._e621_is_cloudflare_html_response(response):
                self.log(
                    f"    e621.net {context}: CLOUDFLARE/403 HTML instead of JSON; "
                    f"pass browser verification, save cookies, and check official User-Agent + Basic Auth"
                )
                return True
            if status == 401:
                self.log(f"    e621.net {context}: 401 Unauthorized; login/api_key invalid or API access disabled")
                return True
            if status == 403:
                self.log(f"    e621.net {context}: 403 Forbidden; check official User-Agent and API auth (ct={ct!r})")
                return True
            if status in (429, 503):
                self.log(f"    e621.net {context}: rate/server limit status={status}; response={text!r}")
                return True
        except Exception:
            pass
        return False

    def e621_tags(self, url, host="e621.net"):
        post_id = urlparse(url).path.strip("/").split("/")[-1]
        host = str(host or "e621.net").lower().replace("www.", "")
        if host not in ("e621.net", "e926.net"):
            host = "e621.net"
        session = self.session_for_host(host)
        params = self._e621_api_params(host, include_v2=True)
        # e621 is rolling out v2 post responses.  Request extended tags so the
        # old grouped-tag path and the new v2 grouped response both preserve
        # artist/character/copyright/species/meta categories.  If the server is
        # still on legacy defaults, these parameters are harmless.
        api_url = f"https://{host}/posts/{post_id}.json"
        auth = self._e621_auth_tuple(host)
        response = session.get(
            api_url,
            params=params,
            timeout=self.timeout,
            headers=self._e621_api_headers(host),
            auth=auth,
        )
        self._e621_log_response_problem(response, "post-json")
        if self._e621_is_cloudflare_html_response(response):
            br = self._e621_browser_get_json_response(api_url, params=params, auth=auth, host=host, context="post-json")
            if br is not None:
                response = br
        data = safe_json_response(response, host)
        if isinstance(data, list):
            post = data[0] if data and isinstance(data[0], dict) else {}
        else:
            post = data.get("post", data) if isinstance(data, dict) else {}
        return groups_to_tags(self._groups_from_post_dict_general(post))

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
        if raw in ("hypnohub",):
            # HypnoHub exposes Gelbooru-compatible DAPI, not /posts.json.
            return "gelbooru"
        if raw in ("szurubooru", "philomena"):
            return raw

        # Domain fallback for older settings files.
        if "e621.net" in domain or "e926.net" in domain:
            return "e621"
        if "rule34.us" in domain or "konachan" in domain or "yande.re" in domain:
            return "moebooru"
        if "gelbooru" in domain or "rule34.xxx" in domain or "xbooru" in domain or "hypnohub" in domain or "safebooru" in domain or "tbib" in domain or "realbooru" in domain:
            return "gelbooru"
        if "danbooru" in domain or "donmai" in domain or "allthefallen" in domain or "lolibooru" in domain or "aibooru" in domain:
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

        result = list(by_key.values())
        try:
            order = [str(x).lower().replace("www.", "") for x in (self.settings.get("_parser_blueprint_site_order") or []) if str(x).strip()]
            if order:
                by_domain = {}
                for site in result:
                    by_domain.setdefault(str(site.get("domain", "")).lower().replace("www.", ""), site)
                # v321: blueprint site blocks stay visible even when the site is
                # disabled, but runtime respects the normal site table by default.
                # Disabled blocks are visual skip/pass-through; they must not force
                # network lanes unless the user explicitly re-enables the site.
                if bool(self.settings.get("_parser_blueprint_sites_only", False)) and not bool(self.settings.get("parser_blueprint_respect_site_enabled", True)):
                    raw_candidates = []
                    if isinstance(sites, dict):
                        for domain, cfg in sites.items():
                            if isinstance(cfg, dict):
                                item = dict(cfg); item.setdefault("domain", str(domain).lower().replace("www.", "")); raw_candidates.append(item)
                    if isinstance(custom_sites, list):
                        raw_candidates.extend([dict(x) for x in custom_sites if isinstance(x, dict)])
                    for raw in raw_candidates:
                        raw["enabled"] = True
                        site = normalize_site(raw, is_custom=False)
                        if site:
                            by_domain.setdefault(str(site.get("domain", "")).lower().replace("www.", ""), site)
                ordered = [by_domain[d] for d in order if d in by_domain]
                if bool(self.settings.get("_parser_blueprint_sites_only", False)):
                    result = ordered
                else:
                    used = {str(s.get("domain", "")).lower().replace("www.", "") for s in ordered}
                    result = ordered + [s for s in result if str(s.get("domain", "")).lower().replace("www.", "") not in used]
        except Exception:
            pass
        return result

    def _auth_params_for_site(self, site):
        return self.auth_params(site if isinstance(site, dict) else {})

    def _site_driver_for(self, site):
        site = site if isinstance(site, dict) else {}
        if not _SiteDriver:
            return None
        root = self._site_root_from_cfg(site).rstrip("/")
        host = urlparse(root).netloc.lower().replace("www.", "")
        engine = self._normalize_engine_type(site)
        try:
            return _SiteDriver.for_site(site, engine_name=engine, host=host)
        except AttributeError:
            # Backward compatibility with older driver modules if a stale import
            # is still present in an interactive process.
            return _SiteDriver.for_host(host) or _SiteDriver.for_engine(engine)

    def _engine_api_attempts(self, site, md5):
        """Build MD5 lookup attempts only from JSON site configs.

        Site-specific endpoint paths/params live in settings/sites/*.json.
        This executor only resolves root/auth and delegates request construction
        to SiteDriver; adding/fixing a site must not require editing this method.
        """
        site = site if isinstance(site, dict) else {}
        driver = self._site_driver_for(site)
        if not driver:
            return []
        root = self._site_root_from_cfg(site).rstrip("/")
        host = urlparse(root).netloc.lower().replace("www.", "")
        engine = self._normalize_engine_type(site)
        # v250: rule34.xxx often reaches this method through normalized site rows
        # created by the parser/site conveyor.  Those transient rows may omit
        # api_key/user_id even though the saved APT site settings contain them.
        # The preflight auth guard already uses _rule34xxx_api_auth_params() with
        # the saved-rule34 fallback, but the actual request previously used only
        # _auth_params_for_site(site), so api.rule34.xxx received no credentials
        # and returned HTTP 200 + "Missing authentication".
        if host in {"rule34.xxx", "api.rule34.xxx"} or engine == "rule34xxx":
            auth = self._rule34xxx_api_auth_params(site)
        elif engine == "e621":
            # e621 credentials are sent by HTTP Basic auth in engine_by_md5(),
            # not as login/api_key URL parameters.  The public endpoint still
            # works without credentials.
            auth = self._e621_api_params(host, include_v2=False)
        elif engine == "danbooru" and (host in {"danbooru.donmai.us", "donmai.us"} or "allthefallen" in host):
            # Danbooru-compatible official APIs (Danbooru and ATF) support
            # Basic auth. Keep login/api_key out of URLs/logs when configured.
            auth = self._danbooru_api_params(host)
        else:
            auth = self._auth_params_for_site(site)
        return driver.md5_attempts(root, md5, auth or None, site=site)

    def _configured_response_posts(self, r, site, driver=None, label="site"):
        """Parse API response using JSON post_list_path before generic fallback."""
        # v249: rule34.xxx may return HTTP 200 + application/json while the
        # body is a plain "Missing authentication" string.  Detect this before
        # the generic parser logs it as a harmless non-json response and before
        # the HTML locator wastes time verifying unrelated recent posts.
        try:
            host = urlparse(self._site_root_from_cfg(site)).netloc.lower().replace("www.", "")
        except Exception:
            host = ""
        if host in {"rule34.xxx", "api.rule34.xxx"} or "rule34.xxx" in str(label).lower():
            if self._rule34xxx_auth_missing_response(r):
                self._rule34xxx_mark_auth_required(label or "rule34.xxx")
                return []
        driver = driver or self._site_driver_for(site)
        if driver:
            try:
                data = safe_json_response(r, label)
                posts = driver.extract_post_list(data)
                if posts:
                    return posts
            except Exception:
                pass
        return self._posts_from_dapi_response(r, label)

    def _configured_post_id(self, site, post):
        driver = self._site_driver_for(site)
        if driver and isinstance(post, dict):
            got = driver.extract_field(post, "id")
            if got not in (None, "", [], {}):
                return got
        return post.get("id") or post.get("post_id") or post.get("pid") if isinstance(post, dict) else None

    def _configured_post_md5(self, site, post):
        driver = self._site_driver_for(site)
        if driver and isinstance(post, dict):
            got = driver.extract_field(post, "md5")
            if isinstance(got, str) and is_md5(got.strip().lower()):
                return got.strip().lower()
        return self._post_md5_value(post)

    def _configured_tags_from_post(self, site, post):
        driver = self._site_driver_for(site)
        tags = []
        if driver and isinstance(post, dict):
            try:
                tags = driver.extract_tags(post)
            except Exception:
                tags = []
        if not tags:
            tags = self._tags_from_post_dict(post)
        return unique_keep_order([normalize_tag(t) for t in tags if normalize_tag(t)])

    def _post_url_for_engine(self, site, post):
        site = site if isinstance(site, dict) else {}
        if not isinstance(post, dict):
            return ""
        root = self._site_root_from_cfg(site).rstrip("/")
        post_id = self._configured_post_id(site, post)
        if not post_id:
            return ""
        driver = self._site_driver_for(site)
        if driver:
            try:
                url = driver.post_url(root, post_id)
                if url:
                    return url
            except Exception:
                pass
        engine = self._normalize_engine_type(site)
        if engine == "gelbooru":
            return f"{root}/index.php?page=post&s=view&id={post_id}"
        if engine == "moebooru":
            return f"{root}/post/show/{post_id}"
        return f"{root}/posts/{post_id}"

    def _rule34xxx_groups_from_tag_info(self, post, flat_tags=None):
        """Build groups from rule34.xxx DAPI fields=tag_info when present.

        rule34.xxx documents ``fields=tag_info`` on the post endpoint.  The
        exact shape is not guaranteed across XML/JSON variants, so this parser
        accepts the common forms: a list of row dicts, a dict of group names to
        tag lists, and row dicts containing name/tag plus type/category.
        """
        groups = empty_tag_groups()
        if not isinstance(post, dict):
            return groups
        allowed = set(unique_keep_order([normalize_tag(t) for t in (flat_tags or []) if normalize_tag(t)]))
        if not allowed:
            allowed = set(self._configured_tags_from_post({"domain": "rule34.xxx", "type": "rule34xxx"}, post))
        if not allowed:
            return groups

        candidates = []
        for key in ("tag_info", "tagInfo", "tags_info", "taginfo", "tag_types", "tag_categories"):
            if key in post and post.get(key) not in (None, "", [], {}):
                candidates.append(post.get(key))
        if not candidates:
            return groups

        group_aliases = {
            "artist": "artist", "artists": "artist", "creator": "artist", "creators": "artist",
            "character": "character", "characters": "character",
            "copyright": "copyright", "copyrights": "copyright", "series": "copyright",
            "meta": "meta", "metadata": "meta", "metas": "meta",
            "general": "general", "tags": "general",
        }

        def add_named(name, typ=None, fallback_group="general"):
            tag = normalize_tag(name)
            if not tag or tag not in allowed:
                return
            group = group_from_tag_type(typ) if typ not in (None, "", [], {}) else fallback_group
            add_tags_to_groups(groups, group, [tag])

        def parse_value(value, fallback_group="general"):
            if value in (None, "", [], {}):
                return
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return
                if text[:1] in "[{":
                    try:
                        parse_value(json.loads(text), fallback_group=fallback_group)
                        return
                    except Exception:
                        pass
                # Last resort: only accept plain tag strings when the caller
                # already told us the group name, never arbitrary text blobs.
                if fallback_group != "general":
                    for part in text.replace(",", " ").split():
                        add_named(part, None, fallback_group)
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    parse_value(item, fallback_group=fallback_group)
                return
            if isinstance(value, dict):
                # Row form: {name/tag: "foo", type/category: 1}
                name = value.get("name") or value.get("tag") or value.get("label") or value.get("tag_name")
                typ = value.get("type", value.get("category", value.get("tag_type", value.get("tagType"))))
                if name not in (None, "", [], {}):
                    add_named(name, typ, fallback_group)
                # Grouped form: {artist: [...], character: [...], ...}
                for key, sub in value.items():
                    key_s = str(key or "").strip().lower()
                    if key_s in {"name", "tag", "label", "tag_name", "type", "category", "tag_type", "tagtype"}:
                        continue
                    parse_value(sub, fallback_group=group_aliases.get(key_s, fallback_group))

        for candidate in candidates:
            parse_value(candidate)

        # Do not add missing general tags here.  Caller will fall back to the
        # tag catalogue/general preservation path.  This function's job is only
        # to use authoritative tag_info categories when they exist.
        return groups

    def _groups_from_engine_post(self, site, post, source_url=""):
        driver = self._site_driver_for(site)
        if driver and isinstance(post, dict):
            try:
                raw_groups = driver.extract_tag_groups(post)
                groups = empty_tag_groups()
                for group, values in (raw_groups or {}).items():
                    add_tags_to_groups(groups, group, values)
                if groups_to_tags(groups):
                    return groups
            except Exception:
                pass

        engine = self._normalize_engine_type(site)
        root = self._site_root_from_cfg(site).rstrip("/")
        host = urlparse(root).netloc.lower().replace("www.", "")
        driver_cfg = getattr(driver, "cfg", {}) if driver else {}

        if bool(driver_cfg.get("fast_flat_tags", False)):
            flat_tags = self._configured_tags_from_post(site, post)
            # Keep exact-MD5 source lanes fast and deterministic: they collect
            # the authoritative flat tag set from the post API and persist it
            # immediately.  Category recovery for flat-tag sites belongs to the
            # durable background enrichment queue, where HTML is used only as a
            # guarded category overlay over these already-confirmed API tags.
            # This restores the original pipeline:
            #   rule34 API -> flat tags -> SQL -> background HTML classification.
            if host in ("rule34.xxx", "api.rule34.xxx"):
                # rule34.xxx foreground MD5 lanes are intentionally flat-only.
                # Categories are recovered later by the background HTML overlay
                # over the already-confirmed API tags.  Do not request/use
                # post-level tag_info here: it previously made the exact-MD5
                # endpoint noisy and moved classification back into the hot path.
                groups = empty_tag_groups()
                groups["general"] = flat_tags
                return groups
            groups = empty_tag_groups()
            groups["general"] = flat_tags
            return groups

        if str(driver_cfg.get("tag_category_mode") or "") == "gelbooru_tag_list_api":
            if source_url and self._needs_background_tag_groups(source_url):
                return self.gelbooru_groups_from_post(post, host, categorize_flat=False)
            return self.gelbooru_groups_from_post(post, host)

        if engine == "e621" or bool(driver_cfg.get("strict_json_only", False)):
            groups = self._groups_from_post_dict_general(post)
            if not groups_to_tags(groups):
                groups["general"] = self._configured_tags_from_post(site, post)
            return groups

        if engine == "gelbooru":
            groups = self.gelbooru_groups_from_post(
                post, host, categorize_flat=not (source_url and self._needs_background_tag_groups(source_url))
            )
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
                # rule34.us documents Gelbooru-like browser search but no
                # structured API. Try the metatag form first and raw hash form
                # second; both are safe because a concrete result is accepted
                # only when its remote media bytes hash to the requested MD5.
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

    def _report_dapi_health_once(self, site, host, session, headers=None):
        """Probe a documented DAPI host once after its first local MD5 miss.

        Xbooru/HypnoHub may genuinely contain none of the current files. The
        parser previously printed identical text for that case and for a dead
        or HTML-only endpoint. A one-request probe makes the distinction visible
        without accepting any unverified metadata.
        """
        if host not in {"xbooru.com", "hypnohub.net"}:
            return
        done = getattr(self, "_dapi_health_reported", set())
        if host in done:
            return
        done.add(host)
        self._dapi_health_reported = done
        try:
            params = {"page": "dapi", "s": "post", "q": "index", "json": "1", "limit": 1}
            params.update(self._auth_params_for_site(site))
            r = self._atf_get_cached(
                session, f"https://{host}/index.php", host, params=params,
                timeout=self.timeout, headers=headers or {}
            )
            posts = self._posts_from_dapi_response(r, host)
            if posts and isinstance(posts[0], dict):
                pid = posts[0].get("id", "?")
                self.log(f"    {host} DAPI ENDPOINT ACTIVE: probe post={pid}; local MD5 absent so far")
            else:
                status = int(getattr(r, "status_code", 0) or 0)
                ctype = str(getattr(r, "headers", {}).get("content-type", "") or "").split(";", 1)[0]
                self.log(f"    {host} DAPI ENDPOINT WARNING: no parseable probe post status={status} content_type={ctype or 'unknown'}")
        except Exception as e:
            self.log(f"    {host} DAPI ENDPOINT ERROR: {type(e).__name__}: {e}")

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

    def _ensure_cf_clearance(self, host: str, root: str) -> None:
        """Auto-obtain cf_clearance for CF-protected sites if missing.
        
        Checks existing cookies - if no cf_clearance found, tries to get one
        via DrissionPage/patchright/playwright and saves to cookie file.
        """
        if not _get_cf:
            return
        cf_hosts = ["donmai.us", "allthefallen.moe"]
        if not any(cf in (host or "") for cf in cf_hosts):
            return
        
        # Check if we already have cf_clearance
        try:
            existing, _ = load_cookie_bundle_for_host(host)
            has_cf = any(
                (c.name if hasattr(c, "name") else c.get("name", "")) == "cf_clearance"
                for c in (existing or [])
            )
            if has_cf:
                return  # Already have it
        except Exception:
            pass
        
        self.log(f"  CF AUTO: no cf_clearance for {host}, attempting auto-solve...")
        result = _get_cf(root or f"https://{host}", log_fn=self.log)
        if result:
            cookies, ua = result
            # Save to cookie file so it persists
            from core.cf_bypass import save_cookies_to_file
            saved = save_cookies_to_file(cookies, host)
            if saved:
                self.log(f"  CF AUTO: saved cookies to {saved.name}")
            # Also update session cache
            try:
                s = _make_cf_session(cookies, root or f"https://{host}", ua)
                self._session_cache[host] = s
                self.log(f"  CF AUTO: session updated with new cf_clearance ✓")
            except Exception as e:
                self.log(f"  CF AUTO: session update error: {e}")
        else:
            self.log(f"  CF AUTO: failed to get cf_clearance for {host}")


    def _rule34xxx_site_cfg(self, site=None):
        """Return the effective saved rule34.xxx site settings.

        rule34.xxx official DAPI authentication is not Basic Auth: the docs use
        ``user_id`` + ``api_key`` URL parameters.  The transient site rows built
        for conveyor lanes can miss those fields, so always merge them with the
        saved ``rule34.xxx`` row before building API requests.
        """
        merged = {}
        saved = self.site_cfg("rule34.xxx")
        if isinstance(saved, dict):
            merged.update(saved)
        if isinstance(site, dict):
            merged.update({k: v for k, v in site.items() if v not in (None, "")})
        return merged

    def _rule34xxx_api_headers(self, site=None):
        """Headers for the official rule34.xxx DAPI.

        The official endpoint is https://api.rule34.xxx/index.php.  It does not
        document browser impersonation as an auth method; use a truthful Local
        Booru User-Agent and pass user_id/api_key in query params.
        """
        cfg = self._rule34xxx_site_cfg(site)
        login = str(cfg.get("login") or "").strip()
        user_id = str(cfg.get("user_id") or "").strip()
        configured = str(cfg.get("user_agent") or cfg.get("api_user_agent") or "").strip()
        if configured:
            ua = configured
        else:
            identity = f"by {login} on rule34.xxx" if login else (f"user_id {user_id} on rule34.xxx" if user_id else "local archive manager")
            ua = f"LocalBooru/3.5 ({identity})"
        return {
            "Accept": "application/json, application/xml, text/xml, */*",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": ua,
        }

    def _rule34xxx_api_auth_params(self, site=None):
        """Return official rule34.xxx DAPI credentials: user_id + api_key only."""
        cfg = self._rule34xxx_site_cfg(site)
        out = {}
        api_key = str(cfg.get("api_key") or "").strip()
        user_id = str(cfg.get("user_id") or "").strip()
        if api_key:
            out["api_key"] = api_key
        if user_id:
            out["user_id"] = user_id
        return out

    def _rule34xxx_api_params(self, site=None, **extra):
        """Build official DAPI query params for rule34.xxx.

        Unlike Danbooru/e621, rule34.xxx does not use Basic Auth for this API;
        it documents ``user_id`` and ``api_key`` as request parameters.  Keep
        login out of DAPI URLs because it is not part of the official contract.
        """
        params = {"page": "dapi", "s": "post", "q": "index", "json": "1"}
        params.update({k: v for k, v in (extra or {}).items() if v not in (None, "")})
        params.update(self._rule34xxx_api_auth_params(site))
        return params

    def _rule34xxx_has_required_api_auth(self, site=None):
        auth = self._rule34xxx_api_auth_params(site)
        return bool(auth.get("api_key") and auth.get("user_id"))

    def _rule34xxx_response_text_for_auth_check(self, response):
        """Best-effort response body read used only for rule34.xxx diagnostics."""
        chunks = []
        try:
            text = getattr(response, "text", "") or ""
            if text:
                chunks.append(str(text))
        except Exception:
            pass
        try:
            content = getattr(response, "content", b"") or b""
            if isinstance(content, bytes) and content:
                chunks.append(content[:4096].decode("utf-8", "ignore"))
            elif content:
                chunks.append(str(content)[:4096])
        except Exception:
            pass
        try:
            data = response.json()
            if isinstance(data, str):
                chunks.append(data)
            elif isinstance(data, dict):
                chunks.append(" ".join(str(v) for v in data.values()))
            elif isinstance(data, list):
                chunks.append(" ".join(str(v) for v in data[:5]))
        except Exception:
            pass
        return " ".join(chunks)

    def _rule34xxx_auth_missing_response(self, response):
        text = self._rule34xxx_response_text_for_auth_check(response).lower()
        if not text:
            return False
        return (
            "missing authentication" in text
            or "go to api.rule34.xxx" in text
            or ("authentication" in text and "api.rule34.xxx" in text)
            or "api key" in text and "user" in text and "required" in text
        )

    def _rule34xxx_is_cloudflare_html_response(self, response):
        try:
            status = int(getattr(response, "status_code", 0) or 0)
            ct = str(getattr(response, "headers", {}).get("content-type", "") or "").lower()
            text = (getattr(response, "text", "") or "")[:4000].lower()
        except Exception:
            return False
        return (
            status in (403, 503, 520, 522, 524)
            and "html" in ct
            and ("cloudflare" in text or "just a moment" in text or "verify you are human" in text or "security verification" in text)
        )

    def _rule34xxx_log_response_problem(self, response, context="DAPI"):
        """Return True if a clearer rule34.xxx API diagnostic was logged."""
        try:
            status = int(getattr(response, "status_code", 0) or 0)
            ct = str(getattr(response, "headers", {}).get("content-type", "") or "")
            text = (self._rule34xxx_response_text_for_auth_check(response) or "")[:220].replace("\n", " ").replace("\r", " ")
            if self._rule34xxx_auth_missing_response(response):
                self._rule34xxx_mark_auth_required("rule34.xxx")
                return True
            if self._rule34xxx_is_cloudflare_html_response(response):
                self.log(
                    f"    rule34.xxx {context}: CLOUDFLARE/HTML instead of DAPI data; "
                    f"official DAPI is api.rule34.xxx with user_id+api_key"
                )
                return True
            if status == 403:
                self.log(f"    rule34.xxx {context}: 403 Forbidden; check user_id/api_key and DAPI access (ct={ct!r})")
                return True
            if status in (429, 503):
                self.log(f"    rule34.xxx {context}: rate/server limit status={status}; response={text!r}")
                return True
            if status >= 500:
                self.log(f"    rule34.xxx {context}: server status={status}; response={text!r}")
                return True
        except Exception:
            pass
        return False

    def _rule34xxx_mark_auth_required(self, label="rule34.xxx"):
        self._last_lookup_status = "auth_required"
        key = str(label or "rule34.xxx")
        warned = getattr(self, "_rule34_auth_missing_warned", set())
        if key not in warned:
            warned.add(key)
            self._rule34_auth_missing_warned = warned
            self.log(f"    {key} AUTH REQUIRED: нужен официальный rule34.xxx API key + User ID; cookies/cf_clearance не заменяют DAPI-доступ")



    def _rule34xxx_current_image_key_candidates(self):
        """Return rule34.xxx 40-hex image keys found in the current media filename.

        This is intentionally filename/path based, not a cryptographic claim:
        rule34.xxx may store images under a 40-hex ``image`` key that is not the
        local file MD5/SHA1.  The key is only used as a post locator after the
        real local MD5 has already missed.
        """
        texts = []
        for attr in ("_current_md5_lookup_path", "_current_scan_media_path", "_current_source_path"):
            try:
                value = getattr(self, attr, "")
            except Exception:
                value = ""
            if value:
                texts.append(str(value))
                try:
                    pp = Path(str(value))
                    texts.append(pp.name)
                    texts.append(pp.stem)
                except Exception:
                    pass
        keys = []
        seen = set()
        rx = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{40})(?![0-9a-fA-F])")
        for text in texts:
            for m in rx.findall(text or ""):
                key = m.lower()
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
        return keys

    def _rule34xxx_post_contains_image_key(self, post, image_key):
        """Verify that a DAPI post really belongs to a rule34.xxx image key."""
        if not isinstance(post, dict):
            return False
        wanted = (image_key or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", wanted):
            return False
        fields = (
            "image", "file_url", "sample_url", "preview_url", "large_file_url",
            "jpeg_url", "source_url", "url", "download_url", "original_url",
        )
        for key in fields:
            try:
                value = post.get(key)
            except Exception:
                value = None
            if value is None:
                continue
            if wanted in str(value).lower():
                return True
        for obj_key in ("file", "media", "sample", "preview", "original", "asset", "files"):
            obj = post.get(obj_key) if isinstance(post, dict) else None
            if isinstance(obj, dict):
                for value in obj.values():
                    if value is not None and wanted in str(value).lower():
                        return True
        return False

    def _rule34xxx_dapi_posts_by_id(self, site, post_id, headers=None):
        """Fetch a concrete rule34.xxx post id through the official DAPI JSON endpoint."""
        pid = str(post_id or "").strip()
        if not pid.isdigit():
            return []
        site = site if isinstance(site, dict) else {}
        label = self._site_label(site)
        session = self.session_for_host("rule34.xxx")
        dapi_headers = self._rule34xxx_api_headers(site)
        params = self._rule34xxx_api_params(site, id=pid, limit=1)
        try:
            r = self._http_get_cached(
                session,
                "https://api.rule34.xxx/index.php",
                params=params,
                timeout=self.timeout,
                headers=dapi_headers,
            )
            self._rule34xxx_log_response_problem(r, "post-by-id")
            if self._rule34xxx_auth_missing_response(r):
                self._rule34xxx_mark_auth_required(label)
                self.log(f"    {label} IMAGE KEY LOCATOR DAPI SKIP: API authentication required")
                return []
            return self._configured_response_posts(r, site, None, label) or []
        except Exception as e:
            self.log(f"    {label} IMAGE KEY LOCATOR DAPI ERROR: api.rule34.xxx json post={pid} {e}")
        return []

    def _rule34xxx_image_key_locator_lookup(self, site, md5, headers=None):
        """Locate a rule34.xxx post by the 40-hex image key after local MD5 miss.

        The preferred locator is the rule34 hotlink endpoint:
        ``https://hl.rule34.xxx/public/hotlink.php?img=<40hex>.png``.
        Browser testing showed this endpoint can redirect directly to the post
        page while sample-bucket URLs are slow and unreliable.  The redirect is
        used only to obtain a post id; tags are fetched only through DAPI by id.
        Sample-bucket probing is now opt-in legacy fallback only.
        """
        site = site if isinstance(site, dict) else {}
        label = self._site_label(site)
        local_md5 = (md5 or "").strip().lower()
        image_keys = self._rule34xxx_current_image_key_candidates()
        if not image_keys:
            return [], "", empty_tag_groups()
        if getattr(self, "_last_lookup_status", "") == "auth_required":
            return [], "", empty_tag_groups()

        session = self.session_for_host("rule34.xxx")
        request_headers = dict(headers or {})
        request_headers.setdefault("Accept", "text/html,application/xhtml+xml,*/*")
        request_headers.setdefault("User-Agent", "LocalBooru/3.2 (local archive manager)")
        request_headers.setdefault("Referer", "https://rule34.xxx/")

        trusted_hotlink_candidates = set()

        def _add_candidate(candidates, pid, trusted_hotlink=False):
            pid = str(pid or "").strip()
            if pid.isdigit() and pid not in candidates:
                candidates.append(pid)
            if pid.isdigit() and trusted_hotlink:
                trusted_hotlink_candidates.add(pid)

        def _extract_post_ids_from_text_blob(candidates, text):
            text = str(text or "")
            if not text:
                return
            # Normal post URLs: index.php?page=post&s=view&id=123
            for pid in re.findall(r"[?&]id=(\d+)", text):
                _add_candidate(candidates, pid)
            # HTML-escaped post URLs: page=post&amp;s=view&amp;id=123
            for pid in re.findall(r"(?:[?&]|&amp;)id=(\d+)", text):
                _add_candidate(candidates, pid)
            # Sample-image quirk: sample_<key>.jpg?12345678
            for pid in re.findall(r"sample_[0-9a-fA-F]{40}\.(?:jpg|jpeg|png|webp)\?(\d{4,})", text):
                _add_candidate(candidates, pid)
            for pid in re.findall(r'page=post[^\s"\'<>]*?s=view[^\s"\'<>]*?(?:[?&]|&amp;)id=(\d+)', text):
                _add_candidate(candidates, pid)
            for pid in re.findall(r"post[^\d]{0,32}(\d{4,})", text, flags=re.I):
                # Weak fallback: keep only after DAPI image-key verification.
                _add_candidate(candidates, pid)

        def _extract_post_ids_from_locator_response(response):
            candidates = []
            blobs = []
            final_url = str(getattr(response, "url", "") or "")
            blobs.append(final_url)
            # Redirect chain can contain the only Location with id=... before requests
            # follows it or normalizes the final URL back to the sample path.
            try:
                for h in getattr(response, "history", []) or []:
                    blobs.append(str(getattr(h, "url", "") or ""))
                    try:
                        blobs.append(str((getattr(h, "headers", {}) or {}).get("Location", "") or ""))
                        blobs.append(str((getattr(h, "headers", {}) or {}).get("Refresh", "") or ""))
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                hdrs = getattr(response, "headers", {}) or {}
                blobs.append(str(hdrs.get("Location", "") or ""))
                blobs.append(str(hdrs.get("Refresh", "") or ""))
            except Exception:
                pass
            body = ""
            try:
                body = getattr(response, "text", "") or ""
            except Exception:
                body = ""
            blobs.append(body)
            for text in blobs:
                _extract_post_ids_from_text_blob(candidates, text)
                parsed = urlparse(str(text or ""))
                if parsed.query and re.fullmatch(r"\d+", parsed.query.strip()):
                    _add_candidate(candidates, parsed.query.strip())
            try:
                soup = BeautifulSoup(body, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a.get("href") or ""
                    _extract_post_ids_from_text_blob(candidates, href)
                    if "page=post" not in href or "s=view" not in href:
                        continue
                    query = parse_qs(urlparse(urljoin("https://rule34.xxx/", href.replace("&amp;", "&"))).query)
                    _add_candidate(candidates, (query.get("id") or [""])[0])
                for tag in soup.find_all(["link", "meta", "script"]):
                    for attr in ("href", "content", "src"):
                        value = tag.get(attr) or ""
                        _extract_post_ids_from_text_blob(candidates, value)
                        if "page=post" in value and "id=" in value:
                            query = parse_qs(urlparse(urljoin("https://rule34.xxx/", value.replace("&amp;", "&"))).query)
                            _add_candidate(candidates, (query.get("id") or [""])[0])
            except Exception:
                pass
            return candidates

        def _hotlink_ext_from_url(url, image_key):
            try:
                parsed = urlparse(str(url or ""))
                query = parse_qs(parsed.query or "")
                img_values = query.get("img") or []
                for value in img_values:
                    m = re.search(rf"{re.escape(image_key)}\.([a-zA-Z0-9]+)", str(value or ""), flags=re.I)
                    if m:
                        ext = m.group(1).lower()
                        if ext in ("jpg", "jpeg", "png", "webp", "gif"):
                            return ext
                m = re.search(rf"sample_{re.escape(image_key)}\.([a-zA-Z0-9]+)", str(url or ""), flags=re.I)
                if m:
                    ext = m.group(1).lower()
                    if ext in ("jpg", "jpeg", "png", "webp", "gif"):
                        return ext
            except Exception:
                pass
            return ""

        def _hotlink_url_for_key(image_key, ext):
            ext = (ext or "").strip().lower().lstrip(".")
            if ext == "jpeg":
                ext = "jpg"
            if ext not in ("jpg", "png", "webp", "gif"):
                return ""
            return f"https://hl.rule34.xxx/public/hotlink.php?img={image_key}.{ext}"

        def _looks_like_hotlink_url(url):
            try:
                parsed = urlparse(str(url or ""))
                host = (parsed.netloc or "").lower().replace("www.", "")
                return host == "hl.rule34.xxx" and "/public/hotlink.php" in (parsed.path or "")
            except Exception:
                return False

        def _hotlink_urls_from_response(response, fallback_url=""):
            urls = []
            def add(value):
                value = str(value or "")
                if value and value not in urls and _looks_like_hotlink_url(value):
                    urls.append(value)
            try:
                add(getattr(response, "url", ""))
            except Exception:
                pass
            try:
                for h in getattr(response, "history", []) or []:
                    add(getattr(h, "url", ""))
                    hdrs = getattr(h, "headers", {}) or {}
                    add(hdrs.get("Location", ""))
            except Exception:
                pass
            try:
                hdrs = getattr(response, "headers", {}) or {}
                add(hdrs.get("Location", ""))
            except Exception:
                pass
            add(fallback_url)
            return urls

        def _probe_hotlink_redirect(image_key, ext, referer_url="", source_url=""):
            """Probe rule34 hotlink.php as an exact 40hex locator.

            The hotlink endpoint can redirect to /index.php?page=post&s=view&id=...
            even when sample bucket probing returns 403/404.  A post id from this
            redirect is trusted as an exact locator for the requested img=<key>.<ext>;
            tags are still fetched only through DAPI by id.
            """
            if not bool(self.settings.get("rule34_image_key_hotlink_redirect_enabled", True)):
                return []
            hotlink_url = source_url or _hotlink_url_for_key(image_key, ext)
            if not hotlink_url or not _looks_like_hotlink_url(hotlink_url):
                return []
            try:
                hotlink_timeout = float(self.settings.get("rule34_image_key_hotlink_request_timeout", 4.0) or 4.0)
            except Exception:
                hotlink_timeout = 4.0
            hotlink_timeout = max(1.5, min(float(self.timeout or 10.0), hotlink_timeout))
            base_headers = dict(request_headers or {})
            # Direct browser navigation to hotlink.php normally has no Referer.
            # A Referer can trigger hotlink protection and return 403 instead of
            # the post redirect, so try a no-referer browser-like request first.
            browser_ua = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
            common_headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "User-Agent": browser_ua,
                "Upgrade-Insecure-Requests": "1",
            }
            hheaders_variants = []
            h0 = dict(base_headers)
            h0.update(common_headers)
            h0.pop("Referer", None)
            hheaders_variants.append(("noreferrer", h0))
            if referer_url:
                h1 = dict(base_headers)
                h1.update(common_headers)
                h1["Referer"] = referer_url
                hheaders_variants.append(("referer", h1))
            candidates = []

            def _cookie_names(jar):
                names = []
                try:
                    for c in jar:
                        name = getattr(c, "name", None)
                        if name:
                            names.append(str(name))
                except Exception:
                    pass
                return sorted(set(names))

            def _copy_rule34_cookies_to_hotlink_session(src, dst):
                """Bridge rule34.xxx browser cookies to hl.rule34.xxx.

                The embedded browser can open hl.rule34.xxx, but parser requests
                previously loaded zero cookies for hl.rule34.xxx.  Exported
                Cloudflare cookies are often stored as host-only rule34.xxx
                cookies, so requests will not send them to the hotlink subdomain
                unless we explicitly mirror them.
                """
                copied = 0
                try:
                    for c in getattr(src, "cookies", []) or []:
                        name = getattr(c, "name", None)
                        value = getattr(c, "value", None)
                        path = getattr(c, "path", "/") or "/"
                        if not name or value is None:
                            continue
                        name = str(name)
                        value = str(value)
                        path = str(path or "/")
                        for kwargs in (
                            {"domain": "hl.rule34.xxx", "path": path},
                            {"domain": ".rule34.xxx", "path": path},
                            {"path": path},
                        ):
                            try:
                                dst.cookies.set(name, value, **kwargs)
                                copied += 1
                            except Exception:
                                pass
                except Exception:
                    pass
                return copied

            sessions = []
            try:
                hs = self.session_for_host("hl.rule34.xxx")
                copied = _copy_rule34_cookies_to_hotlink_session(session, hs)
                sessions.append((hs, "hl"))
                try:
                    names = _cookie_names(getattr(hs, "cookies", []))
                    preview = ", ".join(names[:12])
                    more = "..." if len(names) > 12 else ""
                    self.log(
                        f"    {label} IMAGE KEY HOTLINK COOKIE BRIDGE: copied={copied} "
                        f"cookies={len(names)} [{preview}{more}]"
                    )
                except Exception:
                    pass
            except Exception:
                sessions.append((session, "rule34"))

            def _playwright_cookie_records():
                records = []
                seen = set()

                def add_record(name, value, domain, path="/", expires=None, secure=True, http_only=False):
                    try:
                        if not name or value is None:
                            return
                        name = str(name)
                        value = str(value)
                        domain = str(domain or "").strip()
                        path = str(path or "/")
                        key = (name, value, domain, path)
                        if key in seen:
                            return
                        seen.add(key)
                        rec = {"name": name, "value": value, "domain": domain, "path": path}
                        try:
                            exp = int(float(expires)) if expires not in (None, "", 0, -1) else None
                            if exp and exp > int(time.time()) - 60:
                                rec["expires"] = exp
                        except Exception:
                            pass
                        rec["secure"] = bool(secure)
                        rec["httpOnly"] = bool(http_only)
                        records.append(rec)
                    except Exception:
                        pass

                # Playwright must get the same site state as the app browser: both
                # rule34.xxx and the hotlink subdomain need the rule34 cookies.
                for src_sess, _src_name in sessions + [(session, "rule34")]:
                    try:
                        for c in getattr(src_sess, "cookies", []) or []:
                            name = getattr(c, "name", None)
                            value = getattr(c, "value", None)
                            path = getattr(c, "path", "/") or "/"
                            expires = getattr(c, "expires", None)
                            secure = getattr(c, "secure", True)
                            http_only = getattr(c, "rest", {}).get("HttpOnly", False) if hasattr(c, "rest") else False
                            for dom in ("rule34.xxx", "hl.rule34.xxx", ".rule34.xxx"):
                                add_record(name, value, dom, path, expires, secure, http_only)
                    except Exception:
                        pass
                return records

            def _probe_hotlink_with_playwright():
                if not bool(self.settings.get("rule34_image_key_hotlink_playwright_fallback", True)):
                    return []
                try:
                    from playwright.sync_api import sync_playwright as _sync_playwright
                except Exception as e:
                    self.log(f"    {label} IMAGE KEY HOTLINK PLAYWRIGHT SKIP: not installed ({type(e).__name__}); install: python -m pip install playwright && python -m playwright install chromium")
                    return []
                try:
                    timeout_sec = float(self.settings.get("rule34_image_key_hotlink_playwright_timeout", 25.0) or 25.0)
                except Exception:
                    timeout_sec = 25.0
                timeout_ms = int(max(8000, min(90000, timeout_sec * 1000)))
                headless = bool(self.settings.get("rule34_image_key_hotlink_playwright_headless", False))
                # Keep one persistent profile and one launch at a time.  Chromium
                # profile locking makes concurrent launch_persistent_context calls fail.
                profile_dir = Path(BROWSER_PROFILE_DIR) / "rule34_hotlink_playwright"
                profile_dir.mkdir(parents=True, exist_ok=True)
                cookie_records = _playwright_cookie_records()
                self.log(
                    f"    {label} IMAGE KEY HOTLINK PLAYWRIGHT START: url={hotlink_url} "
                    f"mode={'headless' if headless else 'visible'} cookies={len(cookie_records)}"
                )
                out = []
                with _RULE34_HOTLINK_PLAYWRIGHT_LOCK:
                    try:
                        with _sync_playwright() as pw:
                            context = None
                            try:
                                context = pw.chromium.launch_persistent_context(
                                    user_data_dir=str(profile_dir),
                                    headless=headless,
                                    viewport={"width": 1280, "height": 900},
                                    user_agent=browser_ua,
                                    accept_downloads=False,
                                    args=["--disable-blink-features=AutomationControlled"],
                                )
                                try:
                                    if cookie_records:
                                        context.add_cookies(cookie_records)
                                except Exception as ce:
                                    self.log(f"    {label} IMAGE KEY HOTLINK PLAYWRIGHT COOKIES WARN: {type(ce).__name__}: {str(ce)[:120]}")
                                page = context.pages[0] if context.pages else context.new_page()
                                try:
                                    page.goto(hotlink_url, wait_until="domcontentloaded", timeout=timeout_ms)
                                except Exception as ge:
                                    # Cloudflare/browser redirects can finish after Playwright
                                    # reports a navigation timeout.  Continue and inspect URL/DOM.
                                    self.log(f"    {label} IMAGE KEY HOTLINK PLAYWRIGHT GOTO WARN: {type(ge).__name__}: {str(ge)[:120]}")
                                deadline = time.time() + min(12.0, timeout_ms / 1000.0)
                                while time.time() < deadline:
                                    try:
                                        cur = str(page.url or "")
                                        _extract_post_ids_from_text_blob(out, cur)
                                        if out:
                                            break
                                    except Exception:
                                        pass
                                    try:
                                        page.wait_for_timeout(250)
                                    except Exception:
                                        time.sleep(0.25)
                                try:
                                    cur = str(page.url or "")
                                    _extract_post_ids_from_text_blob(out, cur)
                                    if not out:
                                        html_text = page.content() or ""
                                        _extract_post_ids_from_text_blob(out, html_text)
                                except Exception:
                                    pass
                                final_url = ""
                                try:
                                    final_url = str(page.url or "")
                                except Exception:
                                    pass
                                if out:
                                    self.log(
                                        f"    {label} IMAGE KEY HOTLINK PLAYWRIGHT CANDIDATES: key={image_key} "
                                        f"ids={','.join(out[:5])} url={final_url}"
                                    )
                                else:
                                    self.log(f"    {label} IMAGE KEY HOTLINK PLAYWRIGHT MISS: url={final_url or hotlink_url}")
                            finally:
                                try:
                                    if context is not None:
                                        context.close()
                                except Exception:
                                    pass
                    except Exception as e:
                        self.log(f"    {label} IMAGE KEY HOTLINK PLAYWRIGHT ERROR: {type(e).__name__}: {str(e)[:180]}")
                return out

            # Keep the rule34 session as a fallback only after the bridged hl
            # session.  Do not let a naked rule34-session 403 become the only
            # observed result.
            if not any(obj is session for obj, _name in sessions):
                sessions.append((session, "rule34"))
            seen_req = set()
            last_status = ""
            last_ctype = ""
            last_final = hotlink_url
            for sess_index, (hs, session_label) in enumerate(sessions):
                for header_label, hheaders in hheaders_variants:
                    for follow in (False, True):
                        cache_key = (id(hs), header_label, follow, hotlink_url)
                        if cache_key in seen_req:
                            continue
                        seen_req.add(cache_key)
                        try:
                            # Hotlink probing intentionally bypasses the per-file
                            # GET cache: the same URL is tried with different cookie
                            # jars and browser-like headers.  The generic cache key
                            # does not include cookies/headers, so caching the first
                            # 403 would hide the successful browser-equivalent probe.
                            if "_http_get_cached" in getattr(self, "__dict__", {}):
                                # Unit tests monkeypatch this instance method with
                                # fake responses; keep that path testable without
                                # restoring the production cache bug.
                                r = self._http_get_cached(hs, hotlink_url, timeout=hotlink_timeout, headers=hheaders, allow_redirects=follow)
                            else:
                                r = hs.get(hotlink_url, timeout=hotlink_timeout, headers=hheaders, allow_redirects=follow)
                            try:
                                last_status = str(getattr(r, "status_code", "") or "")
                                last_ctype = str((getattr(r, "headers", {}) or {}).get("content-type", "") or "")
                                last_final = str(getattr(r, "url", hotlink_url) or hotlink_url)
                            except Exception:
                                pass
                        except Exception as e:
                            last_status = type(e).__name__
                            last_ctype = ""
                            last_final = hotlink_url
                            continue
                        for pid in _extract_post_ids_from_locator_response(r):
                            _add_candidate(candidates, pid, trusted_hotlink=True)
                        if candidates:
                            try:
                                final = getattr(r, "url", hotlink_url)
                                self.log(
                                    f"    {label} IMAGE KEY HOTLINK REDIRECT CANDIDATES: key={image_key} ext={ext} "
                                    f"ids={','.join(candidates[:5])} url={final} follow={int(follow)} "
                                    f"session={session_label}/{sess_index+1} headers={header_label}"
                                )
                            except Exception:
                                pass
                            return candidates
                        # If the first response contains another hotlink URL in Location,
                        # probe that exact URL too.  This covers sample -> hl redirect chains
                        # where requests stops on hl with HTTP 403.
                        for next_hotlink in _hotlink_urls_from_response(r):
                            if next_hotlink == hotlink_url:
                                continue
                            next_ext = _hotlink_ext_from_url(next_hotlink, image_key) or ext
                            nested = _probe_hotlink_redirect(image_key, next_ext, referer_url or hotlink_url, next_hotlink)
                            for pid in nested:
                                _add_candidate(candidates, pid, trusted_hotlink=True)
                            if candidates:
                                return candidates
            if not candidates and str(last_status) == "403":
                try:
                    self.log(f"    {label} IMAGE KEY HOTLINK REQUESTS 403: trying automatic Playwright browser resolver")
                except Exception:
                    pass
                for pid in _probe_hotlink_with_playwright():
                    _add_candidate(candidates, pid, trusted_hotlink=True)
                if candidates:
                    return candidates
            if not candidates:
                try:
                    self.log(
                        f"    {label} IMAGE KEY HOTLINK MISS DETAIL: key={image_key} ext={ext} "
                        f"status={last_status or 'missing'} type={last_ctype} url={last_final}"
                    )
                except Exception:
                    pass
            return candidates

        def _image_key_hotlink_extensions():
            raw = str(self.settings.get("rule34_image_key_hotlink_extensions", "png") or "png")
            exts = []
            for part in re.split(r"[,;\s]+", raw):
                ext = part.strip().lower().lstrip(".")
                if ext == "jpeg":
                    ext = "jpg"
                if ext in ("png", "jpg", "webp", "gif") and ext not in exts:
                    exts.append(ext)
            return exts or ["png"]

        def _image_key_bucket_sequence():
            """Return sample directory buckets for rule34 image-key probing.

            User-observed rule34 behavior: the same 40hex key can resolve with
            /samples/1/sample_<key>.jpg, while PNG may require another bucket
            such as /samples/100/.  Probe 1, 100, 200, ... up to 9999 by
            default, with settings overrides for future tuning.
            """
            raw = str(self.settings.get("rule34_image_key_bucket_probe_sequence", "") or "")
            buckets = []
            for part in re.split(r"[,;\s]+", raw):
                part = part.strip()
                if not part:
                    continue
                try:
                    value = int(part)
                except Exception:
                    continue
                if 0 <= value <= 9999 and value not in buckets:
                    buckets.append(value)
            if buckets:
                return buckets

            try:
                max_bucket = int(self.settings.get("rule34_image_key_bucket_probe_max", 9999) or 9999)
            except Exception:
                max_bucket = 9999
            try:
                step = int(self.settings.get("rule34_image_key_bucket_probe_step", 100) or 100)
            except Exception:
                step = 100
            max_bucket = max(1, min(9999, max_bucket))
            step = max(1, min(1000, step))

            buckets = [1]
            for value in range(100, max_bucket + 1, step):
                if value not in buckets:
                    buckets.append(value)
            if max_bucket not in buckets:
                buckets.append(max_bucket)
            return buckets

        def _image_key_sample_url_plan(image_key):
            """Build optional legacy sample URLs for rule34 image-key probing.

            v279 changes the default locator to direct hotlink only:
            https://hl.rule34.xxx/public/hotlink.php?img=<key>.png

            The old 1/100/200/... sample bucket sweep is kept only as an
            explicit legacy mode because it is slow and can starve the worker
            before the useful hotlink/tag-query locators run.
            """
            mode = str(self.settings.get("rule34_image_key_locator_mode", "hotlink_only") or "hotlink_only").strip().lower()
            if mode not in ("bucket_sweep", "sample_bucket_sweep", "legacy_bucket_sweep"):
                return [], []
            if not bool(self.settings.get("rule34_image_key_bucket_probe_enabled", False)):
                return [], []

            urls = []
            seen = set()

            def add(url):
                if url not in seen:
                    seen.add(url)
                    urls.append(url)

            buckets = _image_key_bucket_sequence()
            for bucket in buckets:
                add(f"https://rule34.xxx//samples/{bucket}/sample_{image_key}.jpg")
                add(f"https://rule34.xxx//samples/{bucket}/sample_{image_key}.png")
            add(f"https://rule34.xxx/samples/1/sample_{image_key}.jpg")
            add(f"https://rule34.xxx/samples/1/sample_{image_key}.png")
            return urls, buckets

        try:
            image_key_request_timeout = float(self.settings.get("rule34_image_key_bucket_request_timeout", 3.0) or 3.0)
        except Exception:
            image_key_request_timeout = 3.0
        try:
            image_key_total_timeout = float(self.settings.get("rule34_image_key_bucket_total_timeout", 90.0) or 90.0)
        except Exception:
            image_key_total_timeout = 90.0
        image_key_request_timeout = max(1.5, min(float(self.timeout or 10.0), image_key_request_timeout))
        image_key_total_timeout = max(10.0, image_key_total_timeout)

        for image_key in image_keys[:3]:
            sample_urls, buckets = _image_key_sample_url_plan(image_key)
            hotlink_exts = _image_key_hotlink_extensions()
            self.log(
                f"    {label} IMAGE KEY LOCATOR START: key={image_key} local_md5={local_md5 or 'missing'} "
                f"mode={str(self.settings.get('rule34_image_key_locator_mode', 'hotlink_only') or 'hotlink_only')} "
                f"hotlink_exts={','.join(hotlink_exts)} sample_urls={len(sample_urls)} "
                f"timeout={image_key_request_timeout:.1f}s total_timeout={image_key_total_timeout:.1f}s"
            )
            candidates = []
            # Probe direct hotlink first without counting it as a sample bucket.
            # Browser tests showed that hl.rule34.xxx/public/hotlink.php?img=<key>.<ext>
            # can redirect straight to the post page even when sample bucket URLs
            # return 403/404 or stop on the hotlink host.
            for ext in hotlink_exts:
                for pid in _probe_hotlink_redirect(image_key, ext, ""):
                    _add_candidate(candidates, pid, trusted_hotlink=True)
                if candidates:
                    break
            miss_logged = 0
            started_at = time.monotonic()
            if not candidates:
                for idx, sample_url in enumerate(sample_urls, start=1):
                    if time.monotonic() - started_at >= image_key_total_timeout:
                        self.log(f"    {label} IMAGE KEY LOCATOR TIME BUDGET EXHAUSTED: key={image_key} tried={idx-1}/{len(sample_urls)}")
                        break
                    responses = []
                    # First request keeps redirects visible.  If the server returns
                    # 404, do not waste a second followed-redirect request.  If it
                    # returns 2xx/3xx without an id in headers/location, follow once
                    # to parse the final HTML/URL.
                    try:
                        r0 = self._http_get_cached(session, sample_url, timeout=image_key_request_timeout, headers=request_headers, allow_redirects=False)
                        responses.append(r0)
                    except Exception as e:
                        if miss_logged < 10:
                            self.log(f"    {label} IMAGE KEY LOCATOR ERROR: key={image_key} url={sample_url} redirects=0 {e}")
                            miss_logged += 1
                        continue

                    direct_hotlink_url = _looks_like_hotlink_url(sample_url)
                    for pid in _extract_post_ids_from_locator_response(r0):
                        _add_candidate(candidates, pid, trusted_hotlink=direct_hotlink_url)
                    if candidates and direct_hotlink_url:
                        try:
                            final = getattr(r0, "url", sample_url)
                            self.log(f"    {label} IMAGE KEY HOTLINK REDIRECT CANDIDATES: key={image_key} ext={_hotlink_ext_from_url(sample_url, image_key) or 'unknown'} ids={','.join(candidates[:5])} url={final} follow=0 session=sample")
                        except Exception:
                            pass

                    # Direct hotlink URL or sample->hotlink redirect: the important
                    # post id may be in the next Location/final URL from hotlink.php,
                    # not in the sample URL itself.
                    if not candidates:
                        hotlink_urls = _hotlink_urls_from_response(r0, sample_url)
                        for hotlink_url in hotlink_urls:
                            ext = _hotlink_ext_from_url(hotlink_url, image_key) or _hotlink_ext_from_url(sample_url, image_key)
                            for pid in _probe_hotlink_redirect(image_key, ext, sample_url, hotlink_url):
                                _add_candidate(candidates, pid, trusted_hotlink=True)
                            if candidates:
                                break

                    if not candidates:
                        status0 = int(getattr(r0, 'status_code', 0) or 0)
                        should_follow = status0 and status0 != 404
                        if should_follow:
                            try:
                                r1 = self._http_get_cached(session, sample_url, timeout=image_key_request_timeout, headers=request_headers, allow_redirects=True)
                                responses.append(r1)
                                r1_direct_hotlink = _looks_like_hotlink_url(sample_url)
                                for pid in _extract_post_ids_from_locator_response(r1):
                                    _add_candidate(candidates, pid, trusted_hotlink=r1_direct_hotlink)
                                if candidates and r1_direct_hotlink:
                                    try:
                                        final = getattr(r1, "url", sample_url)
                                        self.log(f"    {label} IMAGE KEY HOTLINK REDIRECT CANDIDATES: key={image_key} ext={_hotlink_ext_from_url(sample_url, image_key) or 'unknown'} ids={','.join(candidates[:5])} url={final} follow=1 session=sample")
                                    except Exception:
                                        pass
                                if not candidates:
                                    hotlink_urls = _hotlink_urls_from_response(r1, sample_url)
                                    for hotlink_url in hotlink_urls:
                                        ext = _hotlink_ext_from_url(hotlink_url, image_key) or _hotlink_ext_from_url(sample_url, image_key)
                                        for pid in _probe_hotlink_redirect(image_key, ext, sample_url, hotlink_url):
                                            _add_candidate(candidates, pid, trusted_hotlink=True)
                                        if candidates:
                                            break
                            except Exception as e:
                                if miss_logged < 10:
                                    self.log(f"    {label} IMAGE KEY LOCATOR ERROR: key={image_key} url={sample_url} redirects=1 {e}")
                                    miss_logged += 1

                    if candidates:
                        try:
                            final = getattr(responses[-1], 'url', sample_url) if responses else sample_url
                            self.log(f"    {label} IMAGE KEY LOCATOR POST CANDIDATES: key={image_key} ids={','.join(candidates[:5])} url={final} tried={idx}/{len(sample_urls)}")
                        except Exception:
                            pass
                        break

                    try:
                        if responses and miss_logged < 10:
                            r0 = responses[-1]
                            ctype = (getattr(r0, 'headers', {}) or {}).get('content-type', '')
                            status = getattr(r0, 'status_code', '')
                            final = getattr(r0, 'url', sample_url)
                            self.log(f"    {label} IMAGE KEY LOCATOR MISS DETAIL: key={image_key} status={status} type={ctype} url={final}")
                            miss_logged += 1
                    except Exception:
                        pass

            if not candidates:
                tried_count = 0
                try:
                    tried_count = min(len(sample_urls), idx if 'idx' in locals() else 0)
                except Exception:
                    tried_count = 0
                self.log(f"    {label} IMAGE KEY LOCATOR: no post id for key={image_key} sample_urls_tried={tried_count}/{len(sample_urls)}")
                continue

            for pid in candidates[:5]:
                posts = self._rule34xxx_dapi_posts_by_id(site, pid, headers=headers)
                if getattr(self, "_last_lookup_status", "") == "auth_required":
                    return [], "", empty_tag_groups()
                for post in posts or []:
                    if not isinstance(post, dict):
                        continue
                    trusted_hotlink = str(pid) in trusted_hotlink_candidates
                    if not trusted_hotlink and not self._rule34xxx_post_contains_image_key(post, image_key):
                        self.log(f"    {label} IMAGE KEY LOCATOR REJECT: post={pid} key not present in DAPI media fields")
                        continue
                    source_url = self._post_url_for_engine(site, post) or f"https://rule34.xxx/index.php?page=post&s=view&id={pid}"
                    groups = self._groups_from_engine_post(site, post, source_url)
                    tags = groups_to_tags(groups) or self._configured_tags_from_post(site, post)
                    site_md5 = self._configured_post_md5(site, post)
                    if not tags:
                        self.log(f"    {label} IMAGE KEY LOCATOR REJECT: post={pid} verified key but no tags")
                        continue
                    classified = sum(len(groups.get(k, []) or []) for k in ("artist", "character", "copyright", "meta")) if isinstance(groups, dict) else 0
                    match_method = "image_key_hotlink_redirect" if trusted_hotlink else "image_key_locator_variant"
                    self.log(
                        f"    {label} TAG SOURCE: {match_method} post={pid} "
                        f"key={image_key} trusted_hotlink={1 if trusted_hotlink else 0} "
                        f"site_md5={site_md5 or 'missing'} local_md5={local_md5 or 'missing'} "
                        f"tags={len(tags)} classified={classified}"
                    )
                    site_md5s = list(getattr(self, "_last_rule34_image_key_site_md5s", []) or [])
                    if site_md5 and site_md5 not in site_md5s:
                        site_md5s.append(site_md5)
                    self._last_rule34_image_key_site_md5s = site_md5s
                    self._last_lookup_match_method = "rule34_image_key_hotlink_redirect" if trusted_hotlink else "rule34_image_key_variant"
                    return tags, source_url, groups
        return [], "", empty_tag_groups()

    def _rule34xxx_sha1_async_search_queries(self, image_key):
        """Return conservative rule34.xxx tag-search operators for a 40-hex key.

        These are only one part of the SHA1/40hex locator.  v274 also probes
        structured DAPI/list parameters such as ``sha1=<key>`` and
        ``image=<key>.png`` because rule34.xxx does not consistently expose
        filename/SHA1 fields through the normal tag search syntax.
        """
        key = str(image_key or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", key):
            return []
        return unique_keep_order([
            f"sha1:{key}",
            f"hash:{key}",
            f"image:{key}",
            f"image:*{key}*",
            f"filename:{key}",
            f"file:{key}",
            f"source:{key}",
            f"source:*{key}*",
            f"source:*{key}",
            f"source:{key}*",
            key,
        ])

    def _rule34xxx_sha1_async_param_probes(self, image_key):
        """Return structured rule34.xxx locator probes for SHA1/40hex values.

        These probes are intentionally accepted only as locators.  If a post
        returned by a probe does not expose the 40hex key in media fields, tags
        are accepted only for a single-result exact structured probe (for
        example ``sha1=<key>``), because an unknown parameter may otherwise be
        ignored by the site and return unrelated recent posts.
        """
        key = str(image_key or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", key):
            return []
        exts = ["png", "jpg", "jpeg", "webp", "gif"]
        probes = []

        def add(name, params, exact_single=False):
            clean = {str(k): str(v) for k, v in (params or {}).items() if v is not None and str(v) != ""}
            probes.append({"name": name, "params": clean, "exact_single": bool(exact_single)})

        # The likely path when the user says “found by SHA1”.
        add("sha1-param", {"sha1": key}, True)
        add("sha1-param-dash", {"sha1": key.upper()}, True)

        # Rule34/booru forks have used inconsistent internal names for media fields.
        add("hash-param", {"hash": key}, True)
        add("image-param", {"image": key}, True)
        add("file-param", {"file": key}, True)
        add("filename-param", {"filename": key}, True)
        add("file-url-param", {"file_url": key}, True)
        add("source-param", {"source": key}, True)

        for ext in exts:
            add(f"image-param-{ext}", {"image": f"{key}.{ext}"}, True)
            add(f"file-param-{ext}", {"file": f"{key}.{ext}"}, True)
            add(f"filename-param-{ext}", {"filename": f"{key}.{ext}"}, True)

        # Some front-end list pages are not DAPI-compatible but still produce
        # post links.  These must later be verified by DAPI-by-id.
        add("list-sha1-param", {"sha1": key}, False)
        add("list-hash-param", {"hash": key}, False)
        add("list-image-param", {"image": key}, False)
        add("list-file-param", {"file": key}, False)
        return probes

    def _rule34xxx_response_post_ids(self, response):
        """Extract post IDs from a rule34.xxx HTML/list/redirect response."""
        ids = []
        seen = set()

        def add(pid):
            pid = str(pid or "").strip()
            if pid.isdigit() and pid not in seen:
                seen.add(pid)
                ids.append(pid)

        blobs = []
        try:
            blobs.append(str(getattr(response, "url", "") or ""))
        except Exception:
            pass
        try:
            for h in getattr(response, "history", []) or []:
                blobs.append(str(getattr(h, "url", "") or ""))
                hdrs = getattr(h, "headers", {}) or {}
                blobs.append(str(hdrs.get("Location", "") or ""))
                blobs.append(str(hdrs.get("Refresh", "") or ""))
        except Exception:
            pass
        try:
            hdrs = getattr(response, "headers", {}) or {}
            blobs.append(str(hdrs.get("Location", "") or ""))
            blobs.append(str(hdrs.get("Refresh", "") or ""))
        except Exception:
            pass
        try:
            body = getattr(response, "text", "") or ""
        except Exception:
            body = ""
        blobs.append(body)
        for blob in blobs:
            text = str(blob or "")
            if not text:
                continue
            for pid in re.findall(r"(?:[?&]|&amp;)id=(\d+)", text):
                add(pid)
            for pid in re.findall(r"page=post[^\s\"'<>]*?s=view[^\s\"'<>]*?(?:[?&]|&amp;)id=(\d+)", text):
                add(pid)
            for pid in re.findall(r"sample_[0-9a-fA-F]{40}\.(?:jpg|jpeg|png|webp)\?(\d{4,})", text):
                add(pid)
            try:
                parsed = urlparse(text)
                if parsed.query and re.fullmatch(r"\d+", parsed.query.strip()):
                    add(parsed.query.strip())
            except Exception:
                pass
        try:
            soup = BeautifulSoup(body, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a.get("href") or ""
                if "page=post" not in href or "s=view" not in href or "id=" not in href:
                    continue
                query = parse_qs(urlparse(urljoin("https://rule34.xxx/", href.replace("&amp;", "&"))).query)
                add((query.get("id") or [""])[0])
            for tag in soup.find_all(["link", "meta", "script"]):
                for attr in ("href", "content", "src"):
                    value = tag.get(attr) or ""
                    if "page=post" not in value or "id=" not in value:
                        continue
                    query = parse_qs(urlparse(urljoin("https://rule34.xxx/", value.replace("&amp;", "&"))).query)
                    add((query.get("id") or [""])[0])
        except Exception:
            pass
        return ids

    def _rule34xxx_verified_variant_from_post(self, site, post, image_key, local_md5, method, headers=None, trusted_exact_param=False):
        """Build accepted rule34.xxx variant metadata from a DAPI-verified post."""
        site = site if isinstance(site, dict) else {}
        label = self._site_label(site)
        key = str(image_key or "").strip().lower()
        if not isinstance(post, dict):
            return [], "", empty_tag_groups()
        if not trusted_exact_param and not self._rule34xxx_post_contains_image_key(post, key):
            return [], "", empty_tag_groups()
        pid = str(post.get("id") or post.get("post_id") or "").strip()
        source_url = self._post_url_for_engine(site, post) or (f"https://rule34.xxx/index.php?page=post&s=view&id={pid}" if pid else "https://rule34.xxx/")
        groups = self._groups_from_engine_post(site, post, source_url)
        tags = groups_to_tags(groups) or self._configured_tags_from_post(site, post)
        site_md5 = self._configured_post_md5(site, post)
        if not tags:
            self.log(f"    {label} SHA1 ASYNC LOCATOR REJECT: post={pid or '?'} verified key but no tags")
            return [], "", empty_tag_groups()
        classified = sum(len(groups.get(k, []) or []) for k in ("artist", "character", "copyright", "meta")) if isinstance(groups, dict) else 0
        self.log(
            f"    {label} TAG SOURCE: {method} post={pid or '?'} "
            f"key={key} site_md5={site_md5 or 'missing'} local_md5={local_md5 or 'missing'} "
            f"trusted_exact_param={int(bool(trusted_exact_param))} tags={len(tags)} classified={classified}"
        )
        site_md5s = list(getattr(self, "_last_rule34_image_key_site_md5s", []) or [])
        if site_md5 and site_md5 not in site_md5s:
            site_md5s.append(site_md5)
        self._last_rule34_image_key_site_md5s = site_md5s
        self._last_lookup_match_method = method.replace("_locator", "_variant") if method.endswith("_locator") else method
        return tags, source_url, groups

    def _rule34xxx_sha1_async_locator_lookup(self, site, md5, headers=None):
        """Asynchronously locate rule34.xxx posts by 40-hex/SHA1-like keys.

        This is deliberately a separate locator path from exact MD5.  It is used
        after the real local MD5 misses, for files named like
        ``<40hex>__<local-md5-prefix>.png`` where the 40-hex value is remembered
        from rule34.xxx/SHA1/image-key search.  The async workers only produce
        candidate post IDs or DAPI posts; tags are accepted only after DAPI media
        fields contain the same 40-hex key.
        """
        site = site if isinstance(site, dict) else {}
        label = self._site_label(site)
        local_md5 = (md5 or "").strip().lower()
        image_keys = self._rule34xxx_current_image_key_candidates()
        if not image_keys:
            return [], "", empty_tag_groups()
        if getattr(self, "_last_lookup_status", "") == "auth_required":
            return [], "", empty_tag_groups()
        if not bool(self.settings.get("rule34_sha1_async_locator_enabled", True)):
            return [], "", empty_tag_groups()

        request_headers = dict(headers or {})
        request_headers.setdefault("Accept", "text/html,application/xhtml+xml,application/json,application/xml,text/xml,*/*")
        request_headers.setdefault("User-Agent", self._rule34xxx_api_headers(site).get("User-Agent", "LocalBooru/3.5 (local archive manager)"))
        request_headers.setdefault("Referer", "https://rule34.xxx/")
        workers = max(1, min(8, int(self.settings.get("rule34_sha1_async_locator_workers", 4) or 4)))
        request_timeout = max(3.0, min(float(self.timeout or 10.0), float(self.settings.get("rule34_sha1_async_locator_request_timeout", 6.0) or 6.0)))
        total_timeout = max(12.0, float(self.settings.get("rule34_sha1_async_locator_total_timeout", 55.0) or 55.0))
        deadline = time.monotonic() + total_timeout

        def _time_left():
            return max(0.0, deadline - time.monotonic())

        def _is_trusted_exact_tag_query(query, image_key):
            q = str(query or "").strip().lower()
            key = str(image_key or "").strip().lower()
            return bool(key) and any(q == f"{prefix}:{key}" for prefix in ("sha1", "hash", "image", "filename", "file"))

        def _task_dapi_search(image_key, query, api_url, use_json):
            params = self._rule34xxx_api_params(site, tags=query, limit=20)
            session = self.session_for_host("rule34.xxx")
            r = self._http_get_cached(session, api_url, params=params, timeout=request_timeout, headers=request_headers)
            return {"kind": "dapi", "key": image_key, "query": query, "probe": "tags", "exact_single": False, "trusted_exact_query": _is_trusted_exact_tag_query(query, image_key), "url": api_url, "response": r}

        def _task_dapi_param_search(image_key, probe, api_url, use_json):
            probe = dict(probe or {})
            params = self._rule34xxx_api_params(site, limit=20)
            params.update(probe.get("params") or {})
            session = self.session_for_host("rule34.xxx")
            r = self._http_get_cached(session, api_url, params=params, timeout=request_timeout, headers=request_headers)
            return {
                "kind": "dapi", "key": image_key, "query": probe.get("name") or "param",
                "probe": probe.get("name") or "param", "exact_single": bool(probe.get("exact_single")),
                "url": api_url, "response": r,
            }

        def _task_html_search(image_key, query):
            params = {"page": "post", "s": "list", "tags": query}
            session = self.session_for_host("rule34.xxx")
            r = self._http_get_cached(session, "https://rule34.xxx/index.php", params=params, timeout=request_timeout, headers=request_headers)
            return {"kind": "ids", "key": image_key, "query": query, "source": "html-tags", "trusted_exact_query": _is_trusted_exact_tag_query(query, image_key), "ids": self._rule34xxx_response_post_ids(r)}

        def _task_html_param_search(image_key, probe):
            probe = dict(probe or {})
            params = {"page": "post", "s": "list"}
            params.update(probe.get("params") or {})
            session = self.session_for_host("rule34.xxx")
            r = self._http_get_cached(session, "https://rule34.xxx/index.php", params=params, timeout=request_timeout, headers=request_headers)
            return {"kind": "ids", "key": image_key, "query": probe.get("name") or "html-param", "source": "html-param", "ids": self._rule34xxx_response_post_ids(r)}

        self.log(f"    {label} SHA1 ASYNC LOCATOR START: keys={','.join(image_keys[:3])} workers={workers} request_timeout={request_timeout:.1f}s total_timeout={total_timeout:.1f}s")
        candidate_ids = []
        candidate_posts = []
        errors = []

        # Run the locator in small async stages: one 40hex key + one query
        # family at a time.  This avoids blasting rule34.xxx with every possible
        # permutation while still letting JSON/XML/API/main/HTML probes overlap.
        for image_key in image_keys[:3]:
            if _time_left() <= 0:
                self.log(f"    {label} SHA1 ASYNC LOCATOR TIME BUDGET EXHAUSTED before key={image_key}")
                break
            if self.cancelled() or candidate_posts or candidate_ids:
                break
            # First try structured parameter probes.  These are the only probes
            # that can represent a real SHA1 lookup if rule34 supports one.
            # Keep structured param probing short.  Real logs showed that a long
            # sequence of unsupported param variants can keep the rule34 worker
            # busy for >120s before the useful tag-query path is even reached.
            param_probe_names = {"sha1-param", "hash-param", "image-param", "file-param", "filename-param"}
            param_probes = [p for p in self._rule34xxx_sha1_async_param_probes(image_key) if p.get("name") in param_probe_names]
            for probe in param_probes:
                if _time_left() <= 0:
                    self.log(f"    {label} SHA1 ASYNC LOCATOR TIME BUDGET EXHAUSTED before structured probe={probe.get('name')}")
                    break
                if self.cancelled() or candidate_posts or candidate_ids:
                    break
                # Structured SHA1/40hex params must be tested through DAPI only.
                # The browser list page ignores unknown params like sha1/hash/image
                # and returns unrelated recent posts; treating those ids as
                # candidates can stop the real tag-query probes too early.
                stage_jobs = [
                    ("dapi-param", image_key, probe, "https://api.rule34.xxx/index.php", True),
                ]
                executor = ThreadPoolExecutor(max_workers=workers)
                futures = []
                try:
                    for kind, k, probe_obj, api_url, use_json in stage_jobs:
                        if kind == "dapi-param":
                            futures.append(executor.submit(_task_dapi_param_search, k, probe_obj, api_url, use_json))
                        else:
                            futures.append(executor.submit(_task_html_param_search, k, probe_obj))
                    for fut in as_completed(futures):
                        if self.cancelled():
                            break
                        try:
                            item = fut.result()
                        except Exception as e:
                            errors.append(f"{type(e).__name__}: {str(e)[:120]}")
                            continue
                        key = item.get("key") or ""
                        query_used = item.get("query") or ""
                        if item.get("kind") == "dapi":
                            r = item.get("response")
                            self._rule34xxx_log_response_problem(r, "sha1-async-locator")
                            if self._rule34xxx_auth_missing_response(r):
                                self._rule34xxx_mark_auth_required(label)
                                self.log(f"    {label} SHA1 ASYNC LOCATOR DAPI SKIP: API authentication required")
                                return [], "", empty_tag_groups()
                            posts = self._configured_response_posts(r, site, None, label) or []
                            verified = [(p, False) for p in posts if self._rule34xxx_post_contains_image_key(p, key)]
                            if not verified and item.get("exact_single") and len(posts) == 1:
                                verified = [(posts[0], True)]
                            if verified:
                                trusted_n = sum(1 for _, trusted in verified if trusted)
                                self.log(
                                    f"    {label} SHA1 ASYNC DAPI CANDIDATES: key={key} "
                                    f"query={query_used} posts={len(verified)} trusted_exact={trusted_n}"
                                )
                                candidate_posts.extend((key, p, trusted) for p, trusted in verified)
                                break
                        else:
                            ids = [str(x) for x in (item.get("ids") or []) if str(x).isdigit()]
                            if ids:
                                trusted_html = bool(item.get("trusted_exact_query") and len(ids) == 1)
                                self.log(f"    {label} SHA1 ASYNC HTML CANDIDATES: key={key} query={query_used} ids={','.join(ids[:5])} trusted_exact={int(trusted_html)}")
                                for pid in ids:
                                    pair = (key, pid, trusted_html)
                                    if pair not in candidate_ids:
                                        candidate_ids.append(pair)
                                break
                finally:
                    for fut in futures:
                        if not fut.done():
                            fut.cancel()
                    try:
                        executor.shutdown(wait=False, cancel_futures=True)
                    except TypeError:
                        executor.shutdown(wait=False)

            # Then fall back to tag-query probes.
            for query in self._rule34xxx_sha1_async_search_queries(image_key):
                if _time_left() <= 0:
                    self.log(f"    {label} SHA1 ASYNC LOCATOR TIME BUDGET EXHAUSTED before tag query={query}")
                    break
                if self.cancelled() or candidate_posts or candidate_ids:
                    break
                stage_jobs = [
                    ("dapi", image_key, query, "https://api.rule34.xxx/index.php", True),
                    ("html", image_key, query, "", False),
                ]
                executor = ThreadPoolExecutor(max_workers=workers)
                futures = []
                try:
                    for kind, k, q, api_url, use_json in stage_jobs:
                        if kind == "dapi":
                            futures.append(executor.submit(_task_dapi_search, k, q, api_url, use_json))
                        else:
                            futures.append(executor.submit(_task_html_search, k, q))
                    for fut in as_completed(futures):
                        if self.cancelled():
                            break
                        try:
                            item = fut.result()
                        except Exception as e:
                            errors.append(f"{type(e).__name__}: {str(e)[:120]}")
                            continue
                        key = item.get("key") or ""
                        query_used = item.get("query") or ""
                        if item.get("kind") == "dapi":
                            r = item.get("response")
                            self._rule34xxx_log_response_problem(r, "sha1-async-locator")
                            if self._rule34xxx_auth_missing_response(r):
                                self._rule34xxx_mark_auth_required(label)
                                self.log(f"    {label} SHA1 ASYNC LOCATOR DAPI SKIP: API authentication required")
                                return [], "", empty_tag_groups()
                            posts = self._configured_response_posts(r, site, None, label) or []
                            verified = [(p, False) for p in posts if self._rule34xxx_post_contains_image_key(p, key)]
                            if verified:
                                trusted_n = sum(1 for _, trusted in verified if trusted)
                                self.log(f"    {label} SHA1 ASYNC DAPI CANDIDATES: key={key} query={query_used} posts={len(verified)} trusted_exact={trusted_n}")
                                candidate_posts.extend((key, p, trusted) for p, trusted in verified)
                                break
                        else:
                            ids = [str(x) for x in (item.get("ids") or []) if str(x).isdigit()]
                            if ids:
                                trusted_html = bool(item.get("trusted_exact_query") and len(ids) == 1)
                                self.log(f"    {label} SHA1 ASYNC HTML CANDIDATES: key={key} query={query_used} ids={','.join(ids[:5])} trusted_exact={int(trusted_html)}")
                                for pid in ids:
                                    pair = (key, pid, trusted_html)
                                    if pair not in candidate_ids:
                                        candidate_ids.append(pair)
                                break
                finally:
                    # If we already found a candidate, cancel queued probes from
                    # this stage. Running requests are allowed to finish quickly;
                    # no SQLite/output writes happen in these workers.
                    for fut in futures:
                        if not fut.done():
                            fut.cancel()
                    try:
                        executor.shutdown(wait=False, cancel_futures=True)
                    except TypeError:
                        executor.shutdown(wait=False)

        for item in candidate_posts[:10]:
            if len(item) == 3:
                key, post, trusted_exact = item
            else:
                key, post = item
                trusted_exact = False
            method = "rule34_sha1_exact_param_locator" if trusted_exact else "rule34_sha1_async_locator"
            tags, source_url, groups = self._rule34xxx_verified_variant_from_post(
                site, post, key, local_md5, method, headers=headers, trusted_exact_param=trusted_exact
            )
            if tags:
                return tags, source_url, groups

        for item in candidate_ids[:10]:
            if len(item) == 3:
                key, pid, trusted_exact = item
            else:
                key, pid = item
                trusted_exact = False
            posts = self._rule34xxx_dapi_posts_by_id(site, pid, headers=headers)
            if getattr(self, "_last_lookup_status", "") == "auth_required":
                return [], "", empty_tag_groups()
            for post in posts or []:
                if not trusted_exact and not self._rule34xxx_post_contains_image_key(post, key):
                    self.log(f"    {label} SHA1 ASYNC LOCATOR REJECT: post={pid} key not present in DAPI media fields")
                    continue
                method = "rule34_sha1_exact_query_locator" if trusted_exact else "rule34_sha1_async_locator"
                tags, source_url, groups = self._rule34xxx_verified_variant_from_post(
                    site, post, key, local_md5, method, headers=headers, trusted_exact_param=trusted_exact
                )
                if tags:
                    return tags, source_url, groups

        if errors:
            self.log(f"    {label} SHA1 ASYNC LOCATOR ERRORS: {len(errors)} first={errors[0]}")
        self.log(f"    {label} SHA1 ASYNC LOCATOR: no DAPI-verified post for keys={','.join(image_keys[:3])}")
        return [], "", empty_tag_groups()

    def _rule34xxx_html_md5_locator_lookup(self, site, md5, headers=None):
        """Find a rule34.xxx post through its browser md5 page, then verify via DAPI.

        The browser ``page=post&s=list&md5=<hash>`` endpoint is used only as a
        locator for post IDs.  It is never trusted for tags or MD5.  Every
        located id is refetched from the DAPI JSON endpoint and accepted only if
        JSON exposes the exact requested MD5.
        """
        wanted_md5 = (md5 or "").strip().lower()
        if not is_md5(wanted_md5):
            return [], "", empty_tag_groups()
        site = site if isinstance(site, dict) else {}
        label = self._site_label(site)
        if not self._rule34xxx_has_required_api_auth(site):
            self._rule34xxx_mark_auth_required(label)
            self.log(f"    {label} HTML MD5 LOCATOR SKIP: DAPI verification requires API key + User ID")
            return [], "", empty_tag_groups()
        html_host = "rule34.xxx"
        session = self.session_for_host(html_host)
        request_headers = dict(headers or {})
        request_headers.setdefault("Accept", "text/html,application/xhtml+xml,*/*")
        request_headers.setdefault("User-Agent", "LocalBooru/3.2 (local archive manager)")

        list_url = "https://rule34.xxx/index.php"
        # Do not use page=post&s=list&md5=<hash>. Real rule34.xxx logs show
        # that endpoint may ignore the parameter and return recent unrelated
        # posts, causing a burst of DAPI reject checks for every file.  The HTML
        # locator is only allowed to use a tag search form; DAPI still verifies
        # the concrete post md5 before accepting anything.
        list_attempts = [
            {"page": "post", "s": "list", "tags": f"md5:{wanted_md5}"},
            {"page": "post", "s": "list", "tags": wanted_md5},
        ]
        post_ids = []
        seen = set()
        for params in list_attempts:
            try:
                r = self._http_get_cached(session, list_url, params=params, timeout=self.timeout, headers=request_headers)
                text = getattr(r, "text", "") or ""
            except Exception as e:
                self.log(f"    {label} HTML MD5 LOCATOR ERROR: {e}")
                continue
            candidates = []
            try:
                soup = BeautifulSoup(text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a.get("href") or ""
                    if "page=post" not in href or "s=view" not in href or "id=" not in href:
                        continue
                    query = parse_qs(urlparse(urljoin("https://rule34.xxx/", href)).query)
                    pid = (query.get("id") or [""])[0]
                    if str(pid).isdigit():
                        candidates.append(str(pid))
            except Exception:
                pass
            for pid in re.findall(r"[?&]id=(\d+)", text):
                candidates.append(str(pid))
            for pid in candidates:
                if pid not in seen:
                    seen.add(pid)
                    post_ids.append(pid)
            if post_ids:
                break

        if not post_ids:
            self.log(f"    {label} HTML MD5 LOCATOR: no post link for md5={wanted_md5}")
            return [], "", empty_tag_groups()

        dapi_headers = self._rule34xxx_api_headers(site)
        api_attempts = []
        for pid in post_ids[:5]:
            p1 = self._rule34xxx_api_params(site, id=pid, limit=1)
            p2 = self._rule34xxx_api_params(site, tags=f"id:{pid}", limit=1)
            api_attempts.append((pid, "https://api.rule34.xxx/index.php", p1))
            api_attempts.append((pid, "https://api.rule34.xxx/index.php", p2))
        for pid, api_url, params in api_attempts:
            try:
                r = self._http_get_cached(session, api_url, params=params, timeout=self.timeout, headers=dapi_headers)
                self._rule34xxx_log_response_problem(r, "html-md5-locator")
                if self._rule34xxx_auth_missing_response(r):
                    self._rule34xxx_mark_auth_required(label)
                    self.log(f"    {label} HTML MD5 LOCATOR DAPI SKIP: API authentication required")
                    return [], "", empty_tag_groups()
                posts = self._configured_response_posts(r, site, None, label)
            except Exception as e:
                self.log(f"    {label} HTML MD5 LOCATOR DAPI ERROR: post={pid} {e}")
                continue
            for post in posts or []:
                if not isinstance(post, dict):
                    continue
                post_md5 = self._configured_post_md5(site, post)
                if post_md5 != wanted_md5:
                    self.log(f"    {label} HTML MD5 LOCATOR REJECT: post={pid} remote={post_md5 or 'missing'}")
                    continue
                source_url = self._post_url_for_engine(site, post) or f"https://rule34.xxx/index.php?page=post&s=view&id={pid}"
                groups = self._groups_from_engine_post(site, post, source_url)
                tags = groups_to_tags(groups) or self._configured_tags_from_post(site, post)
                if tags:
                    classified = sum(len(groups.get(k, []) or []) for k in ("artist", "character", "copyright", "meta")) if isinstance(groups, dict) else 0
                    self.log(f"    {label} TAG SOURCE: html_md5_locator_dapi_verified post={pid} tags={len(tags)} classified={classified}")
                    return tags, source_url, groups
        self.log(f"    {label} HTML MD5 LOCATOR: ids={','.join(post_ids[:5])} but no DAPI-verified exact MD5")
        return [], "", empty_tag_groups()

    def engine_by_md5(self, site, md5):
        _html_rejected = False  # track if HTML MD5 already rejected for this site

        # BLOCK: danbooru/ATF - only proceed if HTML md5 verification passes
        # These sites return false positives without proper CF session
        _root = self._site_root_from_cfg(site) if isinstance(site, dict) else ""
        _ehost = urlparse(_root).netloc.lower().replace("www.", "")
        _is_cf_strict = any(cf in _ehost for cf in ["donmai.us", "allthefallen.moe"])
        """Single MD5 lookup path for every configured booru/custom site."""
        site = site if isinstance(site, dict) else {}
        label = self._site_label(site)
        root = self._site_root_from_cfg(site)
        host = urlparse(root).netloc.lower().replace("www.", "")
        session = self.session_for_host(host)
        engine = self._normalize_engine_type(site)
        driver = self._site_driver_for(site)
        driver_cfg = getattr(driver, "cfg", {}) if driver else {}
        self._last_lookup_status = ""
        official_danbooru = bool(driver_cfg.get("accept_missing_md5_on_exact_query", False))
        is_atf = self._is_atf_site(site, host)
        is_documented_dapi_exact = bool(driver_cfg.get("strict_json_only", False)) and not bool(driver_cfg.get("fast_flat_tags", False)) and engine not in {"e621"}
        is_rule34xxx_dapi_exact = bool(driver_cfg.get("fast_flat_tags", False))
        is_rule34us_html_only = str(driver_cfg.get("html_fallback") or "") == "rule34us_strict"
        if is_rule34xxx_dapi_exact and not self._rule34xxx_has_required_api_auth(site):
            self._rule34xxx_mark_auth_required(label)
            return [], "", empty_tag_groups()
        headers = {
            "Accept": "application/json, application/xml, text/xml, */*",
            "User-Agent": "LocalBooru/3.2 (local archive manager)",
        }
        if official_danbooru or is_atf:
            headers.update(self._danbooru_api_headers(host))
        if engine == "e621":
            headers.update(self._e621_api_headers(host))
        if is_rule34xxx_dapi_exact:
            headers.update(self._rule34xxx_api_headers(site))

        _rejected_post_ids_this_md5 = set()  # skip post IDs already rejected in this engine_by_md5 call

        for api, params, fmt in self._engine_api_attempts(site, md5):
            try:
                request_kwargs = {"params": params, "timeout": self.timeout, "headers": headers}
                if official_danbooru or is_atf:
                    request_kwargs["auth"] = self._danbooru_auth_tuple(host)
                if engine == "e621":
                    request_kwargs["auth"] = self._e621_auth_tuple(host)
                r = self._atf_get_cached(session, api, host, **request_kwargs)
                if official_danbooru or is_atf:
                    self._danbooru_log_response_problem(r, "exact-md5", host)
                if engine == "e621":
                    self._e621_log_response_problem(r, "exact-md5")
                if is_rule34xxx_dapi_exact:
                    self._rule34xxx_log_response_problem(r, "exact-md5")
                if is_rule34xxx_dapi_exact and self._rule34xxx_auth_missing_response(r):
                    self._rule34xxx_mark_auth_required(label)
                    return [], "", empty_tag_groups()
                posts = self._configured_response_posts(r, site, driver, label)
                if is_rule34xxx_dapi_exact and getattr(self, "_last_lookup_status", "") == "auth_required":
                    return [], "", empty_tag_groups()
                if not posts:
                    continue

                # rule34.xxx DAPI is treated as an exact-MD5 authority only when
                # a returned post explicitly exposes the requested md5. Real
                # rule34.xxx responses can sometimes ignore tags=md5:<hash>
                # (especially around deleted=show/tag_info) and return a large
                # unrelated page of posts. Do not spam one reject per unrelated
                # post and do not let HTML rescue it; keep only exposed exact
                # matches, otherwise skip this endpoint as unsafe.
                if is_rule34xxx_dapi_exact:
                    wanted_for_filter = (md5 or "").strip().lower()
                    exact_rule34_posts = []
                    first_remote_md5 = ""
                    first_post_id = ""
                    for _candidate in posts:
                        if not isinstance(_candidate, dict):
                            continue
                        _candidate_md5 = self._configured_post_md5(site, _candidate)
                        if not first_remote_md5:
                            first_remote_md5 = _candidate_md5 or "missing"
                            first_post_id = str(_candidate.get("id", "") or "?")
                        if _candidate_md5 == wanted_for_filter:
                            exact_rule34_posts.append(_candidate)
                    if exact_rule34_posts:
                        posts = exact_rule34_posts[:1]
                    else:
                        returned_count = len(posts) if isinstance(posts, list) else 0
                        self.log(
                            f"    {label} DAPI exact lookup ignored MD5: "
                            f"local={md5} first_post={first_post_id or '?'} "
                            f"first_remote={first_remote_md5 or 'missing'} returned={returned_count}; endpoint skipped"
                        )
                        continue

                for post in posts:
                    if not isinstance(post, dict):
                        continue

                    # First try API-level explicit MD5.
                    post_md5 = self._configured_post_md5(site, post)
                    wanted_md5 = (md5 or "").lower()

                    # Skip post IDs already rejected by a previous attempt in this call.
                    _pid_str = str(post.get("id", ""))
                    if _pid_str and _pid_str in _rejected_post_ids_this_md5:
                        continue

                    # --- MD5 verification ---
                    # Ordinary API posts must expose the requested MD5.  Official
                    # Danbooru restricted/Gold posts are a special case: when the
                    # exact ``tags=md5:<hash>`` JSON request returned a concrete
                    # post_id but hid md5/file_url, the query itself identifies the
                    # candidate.  Do not reject it as remote=missing.
                    restricted_danbooru_candidate = bool(
                        official_danbooru and _pid_str and not post_md5
                    )
                    _md5_ok = (post_md5 == wanted_md5) or restricted_danbooru_candidate
                    if restricted_danbooru_candidate:
                        self.log(f"    {label} RESTRICTED CANDIDATE: post={_pid_str} remote=hidden exact_md5_query=1")

                    if not _md5_ok:
                        if official_danbooru:
                            # A different exposed hash is a real mismatch.  HTML
                            # is not permitted to rescue it.
                            self.log(f"    {label} JSON MD5 REJECT: post={post.get('id', '?')} remote={post_md5 or 'missing'}")
                            if _pid_str:
                                _rejected_post_ids_this_md5.add(_pid_str)
                            continue

                        if is_documented_dapi_exact or is_rule34xxx_dapi_exact:
                            # Their exact JSON DAPI lookup is authoritative for automatic
                            # matching. A result without the requested explicit MD5 is not
                            # trusted and must never be rescued from visible HTML.
                            self.log(f"    {label} JSON MD5 REJECT: local={md5} remote={post_md5 or 'missing'}")
                            if _pid_str:
                                _rejected_post_ids_this_md5.add(_pid_str)
                            continue

                        # ATF with a wrong OR missing md5 in JSON is rejected here.
                        # Do not "rescue" ATF by fetching/parsing HTML: ATF is Danbooru-like
                        # and automatic metadata must be API-first/API-only for exact MD5.
                        if is_atf and self.settings.get("strict_atf_md5", True):
                            self.log(f"    {label} JSON MD5 REJECT: local={md5} remote={post_md5 or 'missing'}; HTML rescue disabled")
                            if not hasattr(self, "_atf_rejected_posts"):
                                self._atf_rejected_posts = set()
                            if _pid_str:
                                self._atf_rejected_posts.add(_pid_str)
                                _rejected_post_ids_this_md5.add(_pid_str)
                            continue

                        # For non-ATF compatible sites when md5 is absent or different:
                        # fetch the concrete post page HTML and verify the md5 there.
                        # Deleted posts have no file_url in HTML → no md5 → safe reject.
                        _src_for_verify = self._post_url_for_engine(site, post)
                        if not _src_for_verify:
                            self.log(f"    {label} MD5 REJECT: post has no post URL for HTML verification")
                            continue
                        try:
                            if is_atf:
                                _html_v = self._atf_get_cached(
                                    session, _src_for_verify, host,
                                    timeout=self.timeout,
                                    headers={"Accept": "text/html,application/xhtml+xml,*/*"}).text
                            else:
                                _html_v = self._http_get_cached(
                                    session, _src_for_verify, timeout=self.timeout,
                                    headers={"Accept": "text/html,application/xhtml+xml,*/*"}).text
                            if self._verify_html_md5(label, _html_v, md5):
                                _md5_ok = True
                            else:
                                if post_md5:
                                    self.log(f"    {label} MD5 REJECT: local={md5} remote={post_md5}")
                                else:
                                    self.log("    " + label + " MD5 REJECT: post=" + str(post.get("id","?")) + " no verifiable md5")
                                if _pid_str:
                                    _rejected_post_ids_this_md5.add(_pid_str)
                                if _is_cf_strict:
                                    break
                                continue
                        except Exception as e:
                            self.log(f"    {label} MD5 HTML VERIFY ERROR: {e}")
                            continue

                    if not _md5_ok:
                        continue

                    source_url = self._post_url_for_engine(site, post)

                    # ATF blacklist: skip if this post was already wrong for another file
                    if "allthefallen" in label:
                        _atf_bl = getattr(self, "_atf_rejected_posts", set())
                        _atf_pid = str(post.get("id", ""))
                        if _atf_pid in _atf_bl:
                            self.log("    " + label + " BLACKLIST: post=" + _atf_pid + " was wrong for another file, skipping")
                            continue

                    groups = self._groups_from_engine_post(site, post, source_url)
                    tags = groups_to_tags(groups) or self._configured_tags_from_post(site, post)
                    if official_danbooru and tags:
                        self.log(f"    {label} TAG SOURCE: json_api post={_pid_str or '?'} tags={len(tags)}")
                    elif is_atf and tags:
                        self.log(f"    {label} TAG SOURCE: json_api_exact_md5 post={_pid_str or '?'} tags={len(tags)}")
                    elif is_documented_dapi_exact and tags:
                        self.log(f"    {label} TAG SOURCE: dapi_json_exact_md5 post={_pid_str or '?'} tags={len(tags)}")
                    elif is_rule34xxx_dapi_exact and tags:
                        _classified = sum(len(groups.get(k, []) or []) for k in ("artist", "character", "copyright", "meta")) if isinstance(groups, dict) else 0
                        if _classified:
                            self.log(f"    {label} TAG SOURCE: dapi_json_exact_md5_categorized post={_pid_str or '?'} tags={len(tags)} classified={_classified}")
                        else:
                            self.log(f"    {label} TAG SOURCE: dapi_json_exact_md5_flat_fast post={_pid_str or '?'} tags={len(tags)}")

                    # Restricted official Danbooru fallback: only after a concrete
                    # post came from the exact MD5 JSON result and JSON contained no
                    # tags.  The parser below reads tag names from href query values
                    # only; visible sidebar text is intentionally ignored.
                    if official_danbooru and not tags and restricted_danbooru_candidate:
                        groups = self._danbooru_confirmed_html_fallback(session, _pid_str)
                        tags = groups_to_tags(groups)

                    if is_rule34xxx_dapi_exact and not tags:
                        # rule34.xxx deleted/show XML may expose only md5/deleted
                        # stubs.  They prove that a hash once existed on the
                        # site, but they do not contain tags/source metadata.
                        # Do not mark the file as found and do not scrape HTML
                        # for new tags; keep waiting for another exact source or
                        # reverse-search fallback.
                        self.log(f"    {label} DAPI exact MD5 tagless/deleted stub skipped: post={_pid_str or '?'} md5={post_md5 or 'missing'}")
                        continue

                    if not tags and source_url and not official_danbooru and not is_atf and not is_documented_dapi_exact and not is_rule34xxx_dapi_exact and engine != "e621":
                        try:
                            tags = self.tags_from_url(source_url)
                            groups = self._categorize_flat_tags(host or label, tags)
                        except Exception:
                            pass
                    if tags:
                        return tags, source_url or root, groups

            except Exception as e:
                self.log(f"    {label} {engine} API error: {e}")

        # Official e621/e926 never use HTML metadata.  Official Danbooru may
        # parse HTML only inside the concrete restricted-candidate branch above;
        # no blind HTML search is allowed after API errors or empty JSON results.
        if engine == "e621":
            self.log(f"    {label} JSON only: no exact API match; HTML tag fallback disabled")
            return [], "", empty_tag_groups()
        if official_danbooru:
            self.log(f"    {label} no exact JSON candidate; restricted HTML fallback not allowed")
            return [], "", empty_tag_groups()
        if is_atf:
            tags, src, groups = self._atf_pixel_hash_locator_lookup(site, md5)
            if tags:
                return tags, src, groups
            self.log(f"    {label} JSON only: no exact API MD5 match; HTML fallback disabled")
            return [], "", empty_tag_groups()
        if is_documented_dapi_exact:
            if bool(driver_cfg.get("health_probe_on_miss", False)):
                self._report_dapi_health_once(site, host, session, headers)
            self.log(f"    {label} DAPI JSON only: no exact API MD5 match; HTML fallback disabled")
            return [], "", empty_tag_groups()
        if is_rule34xxx_dapi_exact:
            if getattr(self, "_last_lookup_status", "") == "auth_required":
                return [], "", empty_tag_groups()
            side_queue_variant_locators = bool(self.settings.get("_rule34_variant_locators_run_in_side_queue", False))
            if side_queue_variant_locators:
                # v283: in the site conveyor, expensive rule34 image-key/SHA1
                # locator branches are run by a separate opportunistic queue.
                # The exact-MD5 rule34 lane must not be held for 30-90 seconds
                # by a locator miss; otherwise all later rule34 MD5 checks are
                # serialized behind it.  This flag is internal to conveyor
                # lane workers; the ordinary one-file path can still run the
                # locators inline.
                self.log(f"    {label} VARIANT LOCATORS SIDE-QUEUED: foreground MD5 lane skips image-key/SHA1")
            else:
                if bool(driver_cfg.get("image_key_locator", True)):
                    tags, src, groups = self._rule34xxx_image_key_locator_lookup(site, md5, headers=headers)
                    if tags:
                        return tags, src, groups
                    if getattr(self, "_last_lookup_status", "") == "auth_required":
                        return [], "", empty_tag_groups()
                if bool(self.settings.get("rule34_sha1_async_locator_enabled", True)):
                    tags, src, groups = self._rule34xxx_sha1_async_locator_lookup(site, md5, headers=headers)
                    if tags:
                        return tags, src, groups
                    if getattr(self, "_last_lookup_status", "") == "auth_required":
                        return [], "", empty_tag_groups()
            if bool(driver_cfg.get("html_md5_locator", True)):
                tags, src, groups = self._rule34xxx_html_md5_locator_lookup(site, md5, headers=headers)
                if tags:
                    return tags, src, groups
                if getattr(self, "_last_lookup_status", "") == "auth_required":
                    return [], "", empty_tag_groups()
            suffix = "html locator found no DAPI-verified post; image-key/SHA1 are side-queued" if side_queue_variant_locators else "image-key/sha1/html locators found no DAPI-verified post"
            self.log(f"    {label} DAPI exact lookup: no exact API MD5 match; {suffix}")
            return [], "", empty_tag_groups()
        if is_rule34us_html_only:
            tags, src, groups = self._engine_html_fallback_by_md5(site, md5)
            if tags:
                self.log(f"    {label} TAG SOURCE: html_href_exact_md5 tags={len(tags)}")
                return tags, src, groups
            self.log(f"    {label} HTML only: no exact MD5-verified post match")
            return [], "", empty_tag_groups()

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
        source_tag_groups = []

        old_cache_enabled = getattr(self, "_lookup_cache_enabled", False)
        old_request_cache = getattr(self, "_request_cache", {})
        self._lookup_cache_enabled = True
        self._request_cache = {}
        self._last_rule34_image_key_site_md5s = []
        self._last_atf_pixel_hash_site_md5s = []
        self._last_variant_site_md5s = []
        try:
            sites = self._all_enabled_site_configs()

            # ATF / allthefallen is useful as a last-resort source, but in real
            # use it sometimes returns very noisy site-specific tags.  Do not let
            # it pollute results that were already verified by cleaner sources
            # such as Danbooru/Gelbooru/e621/rule34.xxx.  It remains available
            # only as fallback when nothing else found tags.
            def _is_atf_site(_site):
                try:
                    text = " ".join([
                        str(_site.get("domain") or ""),
                        str(_site.get("name") or ""),
                        str(_site.get("base_url") or ""),
                        str(_site.get("login_url") or ""),
                        str(_site.get("url") or ""),
                    ]).lower()
                    return "allthefallen" in text or text.strip() == "atf" or " atf" in (" " + text)
                except Exception:
                    return False

            sites = sorted(sites, key=lambda _site: 1 if _is_atf_site(_site) else 0)

            for site in sites:
                label = self._site_label(site)
                try:
                    self.log(f"  MD5 CHECK: {label}")

                    self._last_lookup_match_method = "md5"
                    tags, source, groups = self.engine_by_md5(site, md5)
                    method = str(getattr(self, "_last_lookup_match_method", "md5") or "md5")
                    if tags:
                        self.log(f"  MD5 MATCH: {label} {redact_sensitive_url(source)}" if method == "md5" else f"  VARIANT MATCH: {label} {method} {redact_sensitive_url(source)}")
                        all_tags += tags
                        sources.append(f"{method} {label} {source}")
                        if groups:
                            all_groups.append(groups)
                        if source:
                            source_tag_groups.append({"url": source, "groups": groups or {"general": list(tags)}, "method": method})
                except Exception as e:
                    self.log(f"  MD5 ERROR: {label}: {e}")
        finally:
            self._lookup_cache_enabled = old_cache_enabled
            self._request_cache = old_request_cache

        self._last_md5_source_tag_groups = source_tag_groups
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
                        debug_dir = Path(SERVICE_OUTPUT_DIR) / "debug" if debug_enabled(self.settings) else None
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

        # e621 v2 groups related fields under files.meta/original/sample/preview.
        files_obj = post.get("files")
        if isinstance(files_obj, dict):
            meta_obj = files_obj.get("meta")
            if isinstance(meta_obj, dict):
                for key in ("md5", "hash", "file_md5", "image_md5", "checksum"):
                    got = norm(meta_obj.get(key))
                    if got:
                        return got
            for obj_key in ("original", "sample", "preview"):
                obj = files_obj.get(obj_key)
                if isinstance(obj, dict):
                    for key in ("md5", "hash", "file_md5", "image_md5", "checksum"):
                        got = norm(obj.get(key))
                        if got:
                            return got
                    for key in ("url", "file_url", "download_url", "original_url", "sample_url", "preview_url", "jpg", "webp"):
                        got = self._md5_from_urlish(obj.get(key))
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
            if atf_site:
                auth = self._danbooru_api_params(host)
                request_headers = self._danbooru_api_headers(host)
                request_auth = self._danbooru_auth_tuple(host)
            else:
                auth = self.custom_auth_params(site)
                request_headers = {"Accept": "application/json, */*"}
                request_auth = None
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
                    headers=request_headers,
                    auth=request_auth,
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
                    dump_dir = Path(SERVICE_OUTPUT_DIR) / "debug"
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

    def _posts_from_xml_text(self, text):
        """Return DAPI <post .../> dictionaries from XML-like response text.

        Some Gelbooru-compatible endpoints, especially rule34.xxx deleted/show
        lookups, ignore ``json=1`` and answer with ``text/xml``.  That is still a
        valid DAPI response and should be filtered by the same exact-MD5 guard as
        JSON.  This helper is intentionally conservative: it only returns
        attribute dictionaries from real ``<post>`` elements and never scrapes
        visible HTML tags.
        """
        raw = (text or "").strip()
        if not raw:
            return []
        # Avoid parsing arbitrary HTML pages as XML just because they contain a
        # word named post somewhere.  DAPI XML normally starts with an XML header
        # or a <posts> root.
        low = raw[:256].lower()
        if not (low.startswith("<?xml") or low.startswith("<posts") or "<posts" in low):
            return []
        try:
            soup = BeautifulSoup(raw, "xml")
            posts = []
            for node in soup.find_all("post"):
                attrs = dict(getattr(node, "attrs", {}) or {})
                if attrs:
                    posts.append(attrs)
            return [p for p in posts if isinstance(p, dict)]
        except Exception:
            return []

    def _posts_from_dapi_response(self, r, site_name="site"):
        """Parse Danbooru/Gelbooru/e621/DAPI JSON or XML into post dicts.

        All built-in and custom MD5 search paths should go through this method
        or _post_dicts_from_data(). It intentionally never returns strings or
        mixed values.
        """
        r_text = getattr(r, "text", "") or ""
        r_status = getattr(r, "status_code", 0)
        try:
            r_ct = str(getattr(r, "headers", {}).get("content-type", "")).lower()
        except Exception:
            r_ct = ""

        # DAPI XML is a first-class response, not a JSON parse error.  Parse it
        # before safe_json_response() so logs do not get spammed with
        # "non-json" for legitimate XML bodies.
        xml_first = "xml" in r_ct or (r_text.lstrip().lower().startswith(("<?xml", "<posts")))
        if xml_first:
            posts = self._posts_from_xml_text(r_text)
            if posts:
                return posts
            if 'count="0"' in r_text[:300] or "count='0'" in r_text[:300]:
                return []

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
                    # If the body was XML but had no usable <post>, keep it
                    # silent: exact-MD5 filtering will not be able to use it,
                    # and logging it as "JSON skipped" is misleading.
                    is_xml_body = "xml" in r_ct or r_text.lstrip().lower().startswith(("<?xml", "<posts"))
                    # Silent: these are all "not found" or known broken APIs
                    is_empty_json = "json" in r_ct and not r_text.strip()
                    is_zero_xml = is_xml_body and ('count="0"' in r_text[:300] or "count='0'" in r_text[:300])
                    is_404 = r_status == 404
                    is_html_not_found = "html" in r_ct and r_status == 200
                    if is_empty_json or is_zero_xml or is_404 or is_xml_body:
                        pass  # silent: no usable DAPI posts from this endpoint
                    elif is_html_not_found and site_name in ("hypnohub.net", "rule34.us"):
                        pass  # silent: these sites return HTML index when no results
                    else:
                        self.log(f"    {site_name}: JSON/DAPI parse skipped: {e}")
            except Exception:
                pass

        # Last chance: some servers send XML with a misleading content type.
        return self._posts_from_xml_text(r_text)

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
            "species": ["tag_string_species", "tags_species", "species_tags"],
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
                "species": "species",
                "contributor": "contributor",
                "contributors": "contributor",
                "lore": "lore",
                "invalid": "invalid",
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

    def _html_original_media_url(self, html_text, post_url):
        """Return a likely original media URL from a concrete HTML post page."""
        soup = BeautifulSoup(html_text or "", "html.parser")
        selectors = [
            "a#image-download-link",
            "a[href*='/data/original/']",
            "a[href*='/images/']",
            "a[href*='/img/']",
            "source[src]",
            "video source[src]",
            "img#image",
            "img#post-image",
            "img.image",
            "meta[property='og:image']",
            "meta[name='twitter:image']",
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if not el:
                continue
            value = el.get("href") or el.get("src") or el.get("content")
            if value:
                return urljoin(post_url, html.unescape(str(value)))
        match = re.search(r'''https?://[^"'<>\s]+\.(?:jpg|jpeg|png|gif|webp|mp4|webm)(?:\?[^"'<>\s]*)?''', html_text or "", re.I)
        return match.group(0) if match else ""

    def _remote_media_md5_matches(self, site_name, media_url, wanted_md5):
        """Hash a candidate's media bytes when its HTML hides the MD5."""
        wanted = (wanted_md5 or "").strip().lower()
        if not media_url or not re.fullmatch(r"[0-9a-f]{32}", wanted):
            return False
        try:
            host = urlparse(media_url).netloc.lower().replace("www.", "") or site_name
            session = self.session_for_host(host)
            r = session.get(media_url, timeout=self.timeout, stream=True,
                            headers={"Accept": "image/*,video/*,application/octet-stream,*/*"})
            status = int(getattr(r, "status_code", 0) or 0)
            ctype = str(getattr(r, "headers", {}).get("content-type", "") or "").lower()
            if status >= 400 or "text/html" in ctype:
                self.log(f"    {site_name} REMOTE FILE MD5 REJECT: media response status={status} content_type={ctype or 'unknown'}")
                return False
            digest = hashlib.md5()
            if hasattr(r, "iter_content"):
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        digest.update(chunk)
            else:
                digest.update(getattr(r, "content", b"") or b"")
            got = digest.hexdigest().lower()
            if got == wanted:
                self.log(f"    {site_name} REMOTE FILE MD5 VERIFIED: media bytes match local hash")
                return True
            self.log(f"    {site_name} REMOTE FILE MD5 REJECT: local={wanted} remote={got}")
        except Exception as e:
            self.log(f"    {site_name} REMOTE FILE MD5 ERROR: {type(e).__name__}: {e}")
        return False

    def _verify_html_or_remote_media_md5(self, site_name, html_text, post_url, wanted_md5):
        """Verify a concrete HTML candidate without trusting search text."""
        wanted = (wanted_md5 or "").strip().lower()
        got = self._html_explicit_md5_value(html_text, wanted)
        if got and got == wanted:
            return True
        if site_name == "rule34.us":
            media_url = self._html_original_media_url(html_text, post_url)
            if media_url and self._remote_media_md5_matches(site_name, media_url, wanted):
                return True
        if got and got != wanted:
            self.log(f"    {site_name} HTML MD5 REJECT: local={wanted} remote={got}")
        else:
            self.log(f"    {site_name} HTML MD5 REJECT: no explicit md5 or verified original media in HTML")
        return False

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
            if not self._verify_html_or_remote_media_md5(site_name, html, url, wanted_md5):
                return [], "", empty_tag_groups()
            if site_name == "rule34.us":
                groups = self.gelbooru_groups_from_html(html)
                tags = groups_to_tags(groups)
                if not tags:
                    tags = self.gelbooru_tags_from_html(html)
                    groups = empty_tag_groups()
                    groups["general"] = tags
            else:
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
        if not self._rule34xxx_has_required_api_auth(cfg):
            self._rule34xxx_mark_auth_required("rule34.xxx")
            return [], "", empty_tag_groups()
        session = self.session_for_host("rule34.xxx")
        params = self._rule34xxx_api_params(cfg, tags=f"md5:{md5}", limit=1)
        try:
            r = session.get(
                "https://api.rule34.xxx/index.php",
                params=params,
                timeout=self.timeout,
                headers=self._rule34xxx_api_headers(cfg),
            )
            self._rule34xxx_log_response_problem(r, "compat-md5")
            posts = self._posts_from_dapi_response(r, "rule34.xxx")
            for p in posts or []:
                if not isinstance(p, dict):
                    continue
                if not self._verify_builtin_post_md5("rule34.xxx", p, md5):
                    continue
                tags = self._tags_from_post_dict(p)
                post_id = p.get("id")
                if tags:
                    url = f"https://rule34.xxx/index.php?page=post&s=view&id={post_id}"
                    groups = self.grouped_tags_from_url(url)
                    return tags, url, groups
        except Exception as e:
            self.log(f"    rule34.xxx official DAPI error: {e}")
        self.log("    rule34.xxx official DAPI skipped: no exact API MD5 confirmation")
        return [], "", empty_tag_groups()

    def rule34us_by_md5(self, md5):
        """Compatibility entry point routed through strict verified HTML search."""
        site = dict(self.site_cfg("rule34.us") or {})
        site.setdefault("domain", "rule34.us")
        site.setdefault("type", "rule34us")
        site.setdefault("login_url", "https://rule34.us")
        site["enabled"] = True
        return self.engine_by_md5(site, md5)

    def danbooru_by_md5(self, md5):
        """Compatibility entry point routed through the restricted-safe pipeline."""
        site = dict(self.site_cfg("danbooru.donmai.us") or {})
        site.setdefault("domain", "danbooru.donmai.us")
        site.setdefault("type", "danbooru")
        site.setdefault("login_url", "https://danbooru.donmai.us/session/new")
        site["enabled"] = True
        return self.engine_by_md5(site, md5)

    def gelbooru_by_md5(self, md5):
        """Compatibility entry point routed through the DAPI JSON-only pipeline."""
        site = dict(self.site_cfg("gelbooru.com") or {})
        site.setdefault("domain", "gelbooru.com")
        site.setdefault("type", "gelbooru_html")
        site.setdefault("login_url", "https://gelbooru.com")
        site["enabled"] = True
        return self.engine_by_md5(site, md5)

    def e621_by_md5(self, md5):
        """Exact e621 MD5 lookup through the same normalized post parser as all sites."""
        base_params = self._e621_api_params("e621.net", include_v2=True)
        session = self.session_for_host("e621.net")
        headers = self._e621_api_headers("e621.net")
        auth = self._e621_auth_tuple("e621.net")

        attempts = [
            {"tags": f"md5:{md5}", "limit": 1, **base_params},
            {"tags": f"md5:{md5} status:any", "limit": 1, **base_params},
        ]

        for params in attempts:
            try:
                api_url = "https://e621.net/posts.json"
                r = session.get(
                    api_url,
                    params=params,
                    timeout=self.timeout,
                    headers=headers,
                    auth=auth,
                )
                self._e621_log_response_problem(r, "compat-md5")
                if self._e621_is_cloudflare_html_response(r):
                    br = self._e621_browser_get_json_response(api_url, params=params, auth=auth, host="e621.net", context="compat-md5")
                    if br is not None:
                        r = br
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
        """Load quota cooldown from SQLite; import the pre-v128 JSON state once."""
        try:
            from core.services.service_state import get_cooldown, set_cooldown
            state = get_cooldown(self.settings, "saucenao")
            if int(state.get("cooldown_until", 0) or 0) > 0:
                return state
            # Compatibility migration only: never write live cooldowns to JSON again.
            if self.saucenao_state_file.exists():
                data = json.loads(self.saucenao_state_file.read_text(encoding="utf-8"))
                until = int(float(data.get("cooldown_until", 0) or 0)) if isinstance(data, dict) else 0
                if until > 0:
                    set_cooldown(self.settings, "saucenao", until, reason=str(data.get("reason") or "legacy_json_import"))
                    try:
                        self.saucenao_state_file.rename(self.saucenao_state_file.with_suffix(".json.migrated.bak"))
                    except Exception:
                        pass
                    return get_cooldown(self.settings, "saucenao")
            return state
        except Exception:
            return {"cooldown_until": 0, "reason": "", "updated_at": 0}

    def _save_saucenao_state(self, data):
        try:
            from core.services.service_state import set_cooldown
            set_cooldown(self.settings, "saucenao", int(float(data.get("cooldown_until", 0) or 0)), reason=str(data.get("reason") or ""))
        except Exception:
            pass

    def _saucenao_cooldown_left(self):
        until = float(self._load_saucenao_state().get("cooldown_until", 0) or 0)
        return max(0, int(until - time.time()))

    def _saucenao_cooldown_until(self):
        return int(float(self._load_saucenao_state().get("cooldown_until", 0) or 0))

    def _defer_saucenao_retry(self, reason="limit"):
        self._saucenao_deferred = True
        self._saucenao_defer_reason = str(reason or "limit")
        self._saucenao_retry_after = max(int(time.time()) + 1, self._saucenao_cooldown_until())

    def saucenao_retry_after_epoch(self):
        return int(self._saucenao_retry_after or self._saucenao_cooldown_until() or time.time() + 60)

    def _set_saucenao_cooldown(self, reason="limit"):
        seconds = int(float(self.settings.get("saucenao_cooldown_seconds", 3600) or 3600))
        until = time.time() + max(60, seconds)
        self._save_saucenao_state({"cooldown_until": until, "reason": reason, "set_at": time.time()})
        self.log(f"  SAUCENAO COOLDOWN: {int(max(60, seconds)/60)} min ({reason})")

    def _reverse_candidates_log_path(self):
        try:
            base = ensure_output_base(self.settings)
            logs_dir = Path(base) / "logs"
        except Exception:
            try:
                logs_dir = Path(SERVICE_OUTPUT_DIR) / "logs"
            except Exception:
                logs_dir = Path(".")
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return logs_dir / "reverse_candidates.log"

    def _write_reverse_candidate_log(self, service, *, img_path=None, similarity=0.0, index_name="", title="", urls=None, supported=False, decision="", extra=None):
        """Durable reverse candidate diagnostics for unsupported/source-only relay."""
        try:
            rec = {
                "ts": int(time.time()),
                "service": str(service or ""),
                "file": str(Path(img_path).name) if img_path else "",
                "path": str(img_path or ""),
                "similarity": float(similarity or 0),
                "index_name": str(index_name or ""),
                "title": sanitize_text(str(title or "")),
                "urls": [sanitize_text(str(u)) for u in (urls or []) if str(u or "").strip()],
                "supported_parser": bool(supported),
                "decision": str(decision or ""),
            }
            if isinstance(extra, dict):
                rec.update({str(k): sanitize_text(str(v)) for k, v in extra.items()})
            path = self._reverse_candidates_log_path()
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            pass

    def saucenao_search(self, img_path):
        if not self.settings.get("saucenao_api_key"):
            return []

        left = self._saucenao_cooldown_left()
        if left > 0:
            self.log(f"  SAUCENAO COOLDOWN ACTIVE: {left//60}m {left%60}s left")
            self._defer_saucenao_retry("cooldown_active")
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
            self._defer_saucenao_retry("429")
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
            try:
                from core.services.service_state import set_quota_snapshot
                set_quota_snapshot(self.settings, "saucenao", short_remaining=short_rem, long_remaining=long_rem)
            except Exception as e:
                self.log(f"  SAUCENAO QUOTA STATE WARNING: {e}")
            self.log(
                "  SAUCENAO LIMITS: "
                f"short={short_rem} "
                f"long={long_rem}"
            )
            try:
                if int(short_rem) <= 0 or int(long_rem) <= 0:
                    self._set_saucenao_cooldown("api_limit")
                    # Results from this final allowed request are still usable.
                    # If none produce tags, process_image defers this file instead of writing NO_MATCH.
                    self._defer_saucenao_retry("api_limit")
                else:
                    from core.services.service_state import clear_cooldown
                    clear_cooldown(self.settings, "saucenao")
            except Exception:
                pass

        return data.get("results", [])

    def _e621_md5_candidate_from_saucenao_result(self, result):
        """Extract e621 MD5 candidate from SauceNAO's e621 filename text.

        SauceNAO often returns labels like ``e621.net - <md5>_6.jpg`` even when
        e621 API itself is blocked by Cloudflare.  Treat this only as a candidate
        and still verify it through the normal MD5 pipeline before accepting tags.
        """
        try:
            header = result.get("header", {}) if isinstance(result, dict) else {}
            data = result.get("data", {}) if isinstance(result, dict) else {}
            hay = " ".join([
                str(header.get("index_name") or ""),
                str(data.get("source") or ""),
                str(data.get("title") or ""),
                str(data.get("material") or ""),
                " ".join(str(u or "") for u in (data.get("ext_urls") or [])),
            ])
        except Exception:
            hay = str(result or "")
        if "e621" not in hay.lower() and "e926" not in hay.lower():
            return ""
        # Prefer the characteristic e621 filename form: <md5>_6.jpg / _0.png.
        for m in re.finditer(r"(?i)\b([0-9a-f]{32})_[0-9]+\.(?:jpe?g|png|gif|webm|mp4)\b", hay):
            got = (m.group(1) or "").lower()
            if is_md5(got):
                return got
        # Fallback only if the same text clearly names e621/e926.
        for m in re.finditer(r"(?i)\b([0-9a-f]{32})\b", hay):
            got = (m.group(1) or "").lower()
            if is_md5(got):
                return got
        return ""

    def saucenao_urls(self, img_path):
        urls = []
        self._last_saucenao_source_only = []
        self._last_saucenao_md5_candidates = {}
        domains = self.enabled_domains()
        min_similarity = float(self.settings.get("min_similarity", 85.0) or 85.0)
        relay_min = float(self.settings.get("unsupported_relay_min_similarity", min_similarity) or min_similarity)
        for result in self.saucenao_search(img_path):
            sim = float(result.get("header", {}).get("similarity", 0))
            index_name = result.get("header", {}).get("index_name", "unknown")
            data = result.get("data", {}) or {}
            source_title = str(data.get("source") or data.get("title") or data.get("material") or "").strip()
            label = f"{index_name}" + (f" - {source_title}" if source_title else "")
            self.log(f"  SauceNAO {sim:.2f}% {label}")

            result_urls = list(data.get("ext_urls", []) or [])
            e621_md5_candidate = self._e621_md5_candidate_from_saucenao_result(result)
            supported_urls = []
            unsupported_urls = []
            for u in result_urls:
                try:
                    host = urlparse(u).netloc.lower().replace("www.", "")
                except Exception:
                    host = ""
                if host in domains:
                    supported_urls.append(u)
                else:
                    unsupported_urls.append(u)

            decision = "below_threshold"
            if sim >= min_similarity and supported_urls:
                decision = "supported_url"
            elif sim >= relay_min and unsupported_urls:
                decision = "unsupported_relay_probe"
            elif sim >= min_similarity:
                decision = "source_only_no_url"
            self._write_reverse_candidate_log(
                "SauceNAO",
                img_path=img_path,
                similarity=sim,
                index_name=index_name,
                title=source_title,
                urls=result_urls,
                supported=bool(supported_urls),
                decision=decision,
            )

            if sim < min_similarity:
                continue

            for u in supported_urls:
                if e621_md5_candidate:
                    self._last_saucenao_md5_candidates[str(u)] = e621_md5_candidate
                urls.append((u, sim))

            # Unsupported 85%+ candidates are not tag sources, but they are now
            # passed back to process_image so MD5/source relay can try to find a
            # trusted booru post before falling back to SOURCE-ONLY.
            for u in unsupported_urls:
                if sim >= relay_min:
                    if e621_md5_candidate:
                        self._last_saucenao_md5_candidates[str(u)] = e621_md5_candidate
                    urls.append((u, sim))

            # Preserve unsupported pages as source-only hints when relay does not
            # yield a trusted MD5/tag match.
            if unsupported_urls or (not supported_urls and not result_urls):
                best_url = unsupported_urls[0] if unsupported_urls else ""
                best_host = urlparse(best_url).netloc.lower().replace("www.", "") if best_url else ""
                self._last_saucenao_source_only.append({
                    "url": best_url,
                    "host": best_host,
                    "label": label,
                    "similarity": sim,
                    "index_name": str(index_name or ""),
                })
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

            # IQDB may include Gelbooru's general/list page next to the real
            # candidate (for example ``page=post&s=list&tags=all``).  A list
            # page is not evidence for one image and must never be shown as a
            # saved source link.
            if host == "gelbooru.com":
                gel_q = parse_qs(urlparse(href).query)
                if gel_q.get("page", [""])[0].lower() == "post" and gel_q.get("s", [""])[0].lower() == "list":
                    self.log(f"  IQDB REJECT NON-POST SOURCE [gelbooru.com]: {href}")
                    continue
                if "page=post" not in href:
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


    def danbooru_iqdb_urls(self, img_path):
        """Optional Danbooru-specific IQDB fallback.

        This is kept separate from the main IQDB pass because the user may want
        it enabled explicitly.  It uploads the image to danbooru.iqdb.org and
        only returns URLs that can be handled by the existing site parsers, so it
        cannot create tags by itself.
        """
        urls = []
        domains = self.enabled_domains()
        min_sim = float(self.settings.get("iqdb_min_similarity", 75.0))

        try:
            r = _post_with_file(self.session, "https://danbooru.iqdb.org/", img_path,
                               file_field="file", extra_data={"forcegray": "on"},
                               timeout=max(self.timeout, 60))
            r.raise_for_status()
        except Exception as e:
            self.log(f"  DANBOORU IQDB SEARCH ERROR: {e}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.select("a[href]"):
            href = a.get("href", "").strip()
            if not href:
                continue
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://danbooru.iqdb.org" + href

            parsed = urlparse(href)
            host = parsed.netloc.lower().replace("www.", "")

            if host in {"danbooru.iqdb.org", "iqdb.org"}:
                continue
            if href.rstrip("/") == "https://danbooru.donmai.us":
                continue
            if "danbooru.donmai.us" in host and "/posts/" not in parsed.path and "/post/show/" not in parsed.path:
                continue
            if host == "gelbooru.com":
                gel_q = parse_qs(parsed.query)
                if gel_q.get("page", [""])[0].lower() == "post" and gel_q.get("s", [""])[0].lower() == "list":
                    self.log(f"  DANBOORU IQDB REJECT NON-POST SOURCE [gelbooru.com]: {href}")
                    continue
                if "page=post" not in href:
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
            self.log(f"  DANBOORU IQDB {sim:.2f}% {u}")

        return out

    def e621_iqdb_urls(self, img_path):
        """Reverse-image fallback through e621's IQDB endpoint.

        This runs after public IQDB and before Ascii2D/SauceNAO.  It uploads the
        local image to ``/iqdb_queries.json`` and converts returned post ids into
        ordinary e621 post URLs, so the normal JSON tag parser remains the only
        metadata writer.  The endpoint needs an e621 login + API key; without
        credentials it is skipped, not treated as a failed match.
        """
        host = "e621.net"
        cfg = self.site_cfg(host)
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            return []
        login = str(cfg.get("login") or "").strip()
        api_key = str(cfg.get("api_key") or "").strip()
        if not login or not api_key:
            if not self._e621_iqdb_missing_auth_logged:
                self.log("  E621 IQDB SKIP: нужен login + api_key в настройках сайта e621.net")
                self._e621_iqdb_missing_auth_logged = True
            return []

        now = time.time()
        if self._e621_iqdb_cooldown_until and now < self._e621_iqdb_cooldown_until:
            left = int(self._e621_iqdb_cooldown_until - now)
            self.log(f"  E621 IQDB COOLDOWN: {left//60}m {left%60}s left")
            return []

        try:
            import importlib
            std_req = importlib.import_module("requests")
            auth = std_req.auth.HTTPBasicAuth(login, api_key)
        except Exception:
            auth = (login, api_key)

        driver = self._site_driver_for({"domain": host, "engine": "e621"})
        reverse_cfg = {}
        try:
            reverse_cfg = dict((driver.cfg or {}).get("reverse_image_search") or {}) if driver else {}
        except Exception:
            reverse_cfg = {}

        root = "https://e621.net"
        raw_url = str(reverse_cfg.get("url") or "").strip()
        path = str(reverse_cfg.get("path") or "/iqdb_queries.json").strip()
        if raw_url:
            url = raw_url.replace("{root}", root)
        else:
            if path and not path.startswith("/"):
                path = "/" + path
            url = root + path
        file_field = str(reverse_cfg.get("file_field") or "file")
        headers = self._e621_api_headers(host)
        headers["Accept"] = "application/json"
        default_max = int(reverse_cfg.get("max_results", 5) or 5)
        max_results = max(1, min(20, int(self.settings.get("e621_iqdb_max_results", default_max) or default_max)))
        extra_params = reverse_cfg.get("params") or {}
        if not isinstance(extra_params, dict):
            extra_params = {}
        try:
            r = _post_with_file(
                self.session,
                url,
                img_path,
                file_field=file_field,
                extra_params=extra_params,
                timeout=max(self.timeout, 60),
                headers=headers,
                auth=auth,
            )
        except Exception as e:
            self.log(f"  E621 IQDB SEARCH ERROR: {e}")
            return []

        ct = ""
        try:
            ct = r.headers.get("content-type", "")
        except Exception:
            pass
        self.log(f"  E621 IQDB STATUS: {getattr(r, 'status_code', '?')} {ct}")

        if getattr(r, "status_code", 0) in (401, 403):
            if self._e621_is_cloudflare_html_response(r):
                self.log("  E621 IQDB CLOUDFLARE/403: HTML security verification instead of JSON; pass browser check/save cookies and verify User-Agent + Basic Auth")
            elif getattr(r, "status_code", 0) == 401:
                self.log("  E621 IQDB AUTH ERROR: login/api_key invalid or API access disabled")
            else:
                self.log("  E621 IQDB 403: forbidden; check official User-Agent, login/api_key, and API access")
            return []
        if getattr(r, "status_code", 0) == 429:
            self._e621_iqdb_cooldown_until = time.time() + 90
            self.log("  E621 IQDB 429: временная пауза 90с")
            return []
        if getattr(r, "status_code", 0) >= 500:
            self.log(f"  E621 IQDB SERVER ERROR {getattr(r, 'status_code', '?')}")
            return []
        try:
            r.raise_for_status()
        except Exception as e:
            self.log(f"  E621 IQDB HTTP ERROR: {e}")
            return []

        try:
            data = r.json()
        except Exception:
            snippet = getattr(r, "text", "")[:160].replace("\n", " ").replace("\r", " ")
            self.log(f"  E621 IQDB NON-JSON: {snippet}")
            return []

        def _resolve(value, field_path):
            if field_path is None:
                return value
            cur = value
            for part in str(field_path).split("."):
                if part == "":
                    continue
                if isinstance(cur, dict):
                    cur = cur.get(part)
                elif isinstance(cur, list):
                    try:
                        cur = cur[int(part)]
                    except Exception:
                        return None
                else:
                    return None
            return cur

        result_paths = reverse_cfg.get("result_list_path") or [None, "results", "posts", "iqdb_queries", "matches"]
        if isinstance(result_paths, (str, type(None))):
            result_paths = [result_paths]
        raw_results = []
        for path_candidate in result_paths:
            candidate = _resolve(data, path_candidate)
            if isinstance(candidate, list):
                raw_results = candidate
                break
            if isinstance(candidate, dict):
                raw_results = [candidate]
                break
        post_id_fields = reverse_cfg.get("post_id_fields") or ["post_id", "post.id", "id"]
        score_fields = reverse_cfg.get("score_fields") or ["similarity", "score", "distance", "rank"]

        out = []
        seen = set()
        for idx, item in enumerate(raw_results or []):
            post_id = None
            score = None
            if isinstance(item, dict):
                for field in post_id_fields:
                    post_id = _resolve(item, field)
                    if post_id not in (None, "", [], {}):
                        break
                for field in score_fields:
                    score = _resolve(item, field)
                    if score not in (None, "", [], {}):
                        break
            elif isinstance(item, (int, str)):
                post_id = item
            try:
                post_id = int(post_id)
            except Exception:
                continue
            if post_id <= 0 or post_id in seen:
                continue
            seen.add(post_id)
            # e621 does not document this as a normal percent in the old public
            # examples. Keep the score only for logs/order; metadata validation
            # still happens by fetching the post JSON via tags_from_url().
            sim = 100.0
            if score is not None:
                try:
                    sim = float(score)
                except Exception:
                    sim = 100.0
            template = str(reverse_cfg.get("post_url") or "{root}/posts/{post_id}")
            hit_url = template.replace("{root}", root).replace("{post_id}", str(post_id)).replace("{id}", str(post_id))
            out.append((hit_url, sim))
            self.log(f"  E621 IQDB HIT[{idx}]: {hit_url}")
            if len(out) >= max_results:
                break

        if not out:
            self.log("  E621 IQDB: no results")
        return out

    def _extract_urls_from_json(self, value, *, max_results=10):
        """Conservative URL extractor for optional reverse-search JSON APIs.

        Fuzzy/Fluffle style APIs change response field names over time.  This
        routine does not trust a specific schema; it walks JSON and keeps HTTP
        links.  Metadata verification still happens later via tags_from_url(),
        so a loose extractor cannot write tags by itself.
        """
        found = []
        seen = set()

        def score_from_context(obj):
            if not isinstance(obj, dict):
                return 100.0
            for key in ("similarity", "score", "match", "distance", "certainty", "confidence"):
                if key in obj:
                    try:
                        raw = float(obj.get(key) or 0)
                        if key == "distance":
                            return max(0.0, 100.0 - raw)
                        if raw <= 1.0:
                            return raw * 100.0
                        return raw
                    except Exception:
                        pass
            return 100.0

        def add_url(url, ctx=None):
            url = str(url or "").strip()
            if not url.startswith(("http://", "https://")):
                return
            if url in seen:
                return
            seen.add(url)
            found.append((url, score_from_context(ctx)))

        def walk(node, ctx=None):
            if len(found) >= max_results:
                return
            if isinstance(node, dict):
                local_ctx = node
                for key in ("url", "source", "source_url", "sourceUrl", "post_url", "postUrl", "location", "href"):
                    v = node.get(key)
                    if isinstance(v, str):
                        add_url(v, local_ctx)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, str):
                                add_url(item, local_ctx)
                # Common nested shapes: {post:{url}}, {sources:[{url}]}, etc.
                for v in node.values():
                    walk(v, local_ctx)
            elif isinstance(node, list):
                for item in node:
                    walk(item, ctx)
            elif isinstance(node, str):
                add_url(node, ctx)

        walk(value)
        return found[:max_results]

    def _record_reverse_source_only(self, label, url, similarity=0.0):
        """Store a reverse-search URL as a source-only candidate for No Match."""
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            return False
        normalized_label = str(label or "Reverse").strip() or "Reverse"
        upper_label = normalized_label.upper()
        try:
            host = urlparse(url).netloc.lower().replace("www.", "")
        except Exception:
            host = ""
        # Internal helper/thumbnail/proxy URLs are not user-facing sources.
        if host in {"content.fluffle.xyz", "fluffle.xyz", "api.fluffle.xyz", "api-next.fuzzysearch.net", "fuzzysearch.net"} or host.endswith(".fluffle.xyz"):
            self.log(f"  {upper_label} INTERNAL URL IGNORED AS SOURCE: {url}")
            return False
        try:
            sim = float(similarity or 0)
        except Exception:
            sim = 0.0
        if not hasattr(self, "_last_reverse_source_only") or self._last_reverse_source_only is None:
            self._last_reverse_source_only = []
        for item in list(self._last_reverse_source_only or []):
            if str(item.get("url") or "") == url:
                return True
        self._last_reverse_source_only.append({"label": normalized_label, "url": url, "similarity": sim})
        self.log(f"  {upper_label} SOURCE-ONLY CANDIDATE: {sim:.2f}% {url}")
        return True


    def _tineye_block_text_info(self, text: str):
        """Return (blocked, code, reason) for TinEye/Cloudflare block pages."""
        raw = str(text or "")
        low = raw.lower()
        code = ""
        m = re.search(r"error\s*code\s*[:#]?\s*(\d{4,})", raw, re.I)
        if m:
            code = m.group(1)
        blocked_markers = (
            "performance and security by cloudflare",
            "sorry, you have been blocked",
            "this verification can fail",
            "cloudflare's troubleshooting documentation",
            "cf-error-details",
            "ray id:",
            "turnstile",
            "verify you are human",
            "checking your browser",
            "are you human",
            "captcha",
        )
        if code == "600010" or any(x in low for x in blocked_markers):
            reason = f"cloudflare_{code}" if code else "cloudflare_or_challenge"
            return True, code, reason
        return False, code, ""

    def _tineye_parser_cooldown_seconds(self):
        try:
            value = float(self.settings.get("tineye_parser_block_cooldown_seconds", 86400) or 86400)
        except Exception:
            value = 86400.0
        return max(300.0, min(7 * 24 * 3600.0, value))

    def _tineye_set_parser_cooldown(self, reason="cloudflare_or_challenge", code=""):
        """Disable only parser TinEye fallback for a cooldown window."""
        until = time.time() + self._tineye_parser_cooldown_seconds()
        self._tineye_parser_disabled_until = until
        label = str(reason or "cloudflare_or_challenge")
        if code:
            label = f"{label}:{code}"
        self._tineye_parser_block_reason = label
        try:
            self.settings["_tineye_parser_disabled_until"] = until
            self.settings["_tineye_parser_block_reason"] = label
        except Exception:
            pass
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(until))
        except Exception:
            stamp = str(int(until))
        self.log(f"  TINEYE PARSER FALLBACK BLOCKED: reason={label}; disabled_until={stamp}")

    def _tineye_parser_cooldown_active(self):
        try:
            until = float(getattr(self, "_tineye_parser_disabled_until", 0) or 0)
        except Exception:
            until = 0.0
        now = time.time()
        if until <= now:
            return False
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(until))
        except Exception:
            stamp = str(int(until))
        reason = getattr(self, "_tineye_parser_block_reason", "") or self.settings.get("_tineye_parser_block_reason", "") or "blocked"
        self.log(f"  TINEYE SKIP: parser fallback cooldown until {stamp} ({reason})")
        return True

    def _tineye_normalize_source_url(self, value):
        """Normalize one TinEye URL candidate and drop non-source/tracker URLs.

        TinEye result pages contain many ordinary page assets/analytics links in
        the DOM.  Treat TinEye as source-only: keep user-facing source pages,
        convert known image-CDN URLs to canonical post pages when possible, and
        drop trackers, script endpoints, tag-list pages and duplicates.
        """
        u = str(value or "").strip().strip('"\'')
        if not u:
            return ""
        try:
            u = html.unescape(u).replace("\\/", "/")
        except Exception:
            u = u.replace("\\/", "/")
        if u.startswith("//"):
            u = "https:" + u
        if not u.startswith(("http://", "https://")):
            return ""
        try:
            parsed = urlparse(u)
            host = (parsed.netloc or "").lower().replace("www.", "")
            path = parsed.path or "/"
        except Exception:
            return ""
        if not host:
            return ""

        noise_hosts = {
            "tineye.com", "www.tineye.com",
            "googletagmanager.com", "google-analytics.com", "analytics.google.com",
            "doubleclick.net", "googlesyndication.com", "googleadservices.com",
            "cloudflareinsights.com", "sentry.io", "newrelic.com", "hotjar.com",
            "w3.org", "schema.org",
        }
        if host in noise_hosts or host.endswith(".tineye.com") or host.endswith(".googletagmanager.com"):
            return ""
        if host.endswith(".google-analytics.com") or host.endswith(".doubleclick.net"):
            return ""
        if host.endswith(".w3.org"):
            return ""

        # TinEye often returns Paheal CDN image URLs.  These are better saved as
        # stable post pages because the image host itself contains no metadata UI.
        try:
            if host.endswith("paheal.net") and "/_images/" in path:
                decoded_path = unquote(path)
                m = re.search(r"/_images/[^/]+/(\d{3,})(?:\s|%20|-|_)", decoded_path, re.I)
                if m:
                    return f"https://rule34.paheal.net/post/view/{m.group(1)}"
        except Exception:
            pass

        # rule34.paheal list pages are tag/search pages, not a specific source.
        if host == "rule34.paheal.net" and path.startswith("/post/list/"):
            return ""

        # Remove fragments and common tracking params to make duplicate matching
        # deterministic, but keep real query strings such as ?id=.
        query = parsed.query or ""
        if query:
            kept = []
            for part in query.split("&"):
                k = part.split("=", 1)[0].lower()
                if k.startswith("utm_") or k in {"fbclid", "gclid", "yclid", "mc_cid", "mc_eid"}:
                    continue
                kept.append(part)
            query = "&".join(kept)
        scheme = parsed.scheme or "https"
        normalized = f"{scheme}://{host}{path}"
        if query:
            normalized += "?" + query
        return normalized

    def _tineye_filter_source_urls(self, raw_urls, max_results):
        """Filter, normalize and rank TinEye source-only candidates."""
        out = []
        seen = set()
        for item in raw_urls or []:
            value = item[0] if isinstance(item, (tuple, list)) and item else item
            u = self._tineye_normalize_source_url(value)
            if not u:
                continue
            try:
                p = urlparse(u)
                key = (p.netloc.lower().replace("www.", ""), unquote(p.path).rstrip("/").lower(), p.query)
            except Exception:
                key = (u.lower(), "", "")
            if key in seen:
                continue
            seen.add(key)
            out.append(u)

        # If TinEye gave a proper page on thatpervert, drop duplicated CDN/image
        # URLs from the same site; the page is the useful source-only record.
        has_thatpervert_post = False
        for u in out:
            try:
                p = urlparse(u)
                host = p.netloc.lower().replace("www.", "")
                if host == "thatpervert.com" and p.path.startswith("/post/"):
                    has_thatpervert_post = True
                    break
            except Exception:
                pass
        if has_thatpervert_post:
            filtered = []
            for u in out:
                try:
                    p = urlparse(u)
                    host = p.netloc.lower().replace("www.", "")
                    if host.endswith("thatpervert.com") and host != "thatpervert.com":
                        continue
                except Exception:
                    pass
                filtered.append(u)
            out = filtered

        return out[:max(1, int(max_results or 1))]


    def _tineye_try_playwright(self, img_path_obj, max_results):
        """Upload to TinEye through a normal visible Playwright browser profile.

        This is deliberately a plain browser fallback, not an anti-bot bypass:
        no stealth flags, no webdriver masking and no captcha solving.  It stays
        in the normal mass reverse queue as the last automatic fallback after
        MD5/IQDB/SauceNAO, but it uses a persistent visible browser profile so
        saved cookies/session state can be reused.
        """
        import re as _re
        import time as _time
        from html import unescape as _html_unescape

        try:
            from playwright.sync_api import sync_playwright as _sync_playwright
        except Exception as _e:
            self.log(f"  TINEYE BROWSER: Playwright не установлен: {_e}")
            self.log("  TINEYE BROWSER: установка: pip install playwright && playwright install chromium")
            return None

        def _collect_playwright_cookies(host: str):
            cookies_out = []
            sources = []

            def _add_cookie_record(name, value, domain=None, path="/", expires=None, secure=None, http_only=None, same_site=None):
                try:
                    if not name or value is None:
                        return False
                    name = str(name)
                    value = str(value)
                    # Playwright/Chromium cookie names and values must be plain strings.
                    name.encode("utf-8")
                    value.encode("utf-8")
                    c = {"name": name, "value": value, "path": path or "/"}
                    dom = str(domain or "").strip()
                    if dom:
                        c["domain"] = dom
                    else:
                        c["url"] = f"https://{host}/"
                    try:
                        if expires not in (None, "", 0, -1):
                            exp = int(float(expires))
                            # Skip obviously expired cookies.
                            if exp > int(_time.time()) - 60:
                                c["expires"] = exp
                    except Exception:
                        pass
                    if secure is not None:
                        c["secure"] = bool(secure)
                    if http_only is not None:
                        c["httpOnly"] = bool(http_only)
                    if same_site:
                        ss = str(same_site).strip().lower()
                        if ss in ("strict", "lax", "none"):
                            c["sameSite"] = {"strict": "Strict", "lax": "Lax", "none": "None"}[ss]
                    cookies_out.append(c)
                    return True
                except Exception:
                    return False

            try:
                app_cookies, _ua = load_cookie_bundle_for_host(host)
                added = 0
                for c in _normalize_cookie_records(app_cookies):
                    if _add_cookie_record(
                        c.get("name"),
                        c.get("value"),
                        c.get("domain") or f".{host}",
                        c.get("path") or "/",
                        c.get("expires") or c.get("expirationDate"),
                        c.get("secure"),
                        c.get("httpOnly"),
                        c.get("sameSite"),
                    ):
                        added += 1
                if added:
                    sources.append(f"app-json:{added}")
            except Exception:
                pass

            try:
                txt_jar, txt_info = load_txt_cookiejar_for_host(host)
                added = 0
                if txt_jar:
                    for c in txt_jar:
                        if _add_cookie_record(
                            getattr(c, "name", None),
                            getattr(c, "value", None),
                            getattr(c, "domain", None),
                            getattr(c, "path", "/"),
                            getattr(c, "expires", None),
                            getattr(c, "secure", None),
                            False,
                            None,
                        ):
                            added += 1
                if added:
                    sources.append(f"{txt_info}:{added}")
            except Exception:
                pass

            return cookies_out, sources

        try:
            img_path_obj = Path(img_path_obj)
            if not img_path_obj.exists():
                self.log("  TINEYE BROWSER SKIP: файл не найден")
                return None

            profile_dir = Path(BROWSER_PROFILE_DIR) / "tineye"
            profile_dir.mkdir(parents=True, exist_ok=True)

            # TinEye browser fallback is deliberately visible/headful.  Older
            # configs from v210/v211 may still contain tineye_browser_headless=true;
            # ignore that stale value because it makes the user unable to pass
            # normal TinEye prompts and caused headless timeouts in real logs.
            stale_headless = bool(self.settings.get("tineye_browser_headless", False))
            headless = False
            timeout_ms = int(float(self.settings.get("tineye_browser_timeout_seconds", 60) or 60) * 1000)
            timeout_ms = max(15000, min(180000, timeout_ms))
            manual_wait_ms = int(float(self.settings.get("tineye_browser_manual_wait_seconds", 120) or 120) * 1000)
            manual_wait_ms = max(0, min(600000, manual_wait_ms))

            self.log(
                "  TINEYE BROWSER: Playwright persistent profile "
                f"({'headless' if headless else 'visible'})"
            )
            if stale_headless:
                self.log("  TINEYE BROWSER: старый tineye_browser_headless=true проигнорирован; нужен видимый браузер")

            with _sync_playwright() as _pw:
                context = None
                try:
                    context = _pw.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        headless=headless,
                        viewport={"width": 1365, "height": 900},
                        accept_downloads=False,
                    )

                    try:
                        profile_cookie_count = len(context.cookies(["https://tineye.com/"]))
                        if profile_cookie_count:
                            self.log(f"  TINEYE BROWSER PROFILE COOKIES: {profile_cookie_count} из persistent profile")
                    except Exception:
                        pass

                    saved_cookies, cookie_sources = _collect_playwright_cookies("tineye.com")
                    if saved_cookies:
                        try:
                            context.add_cookies(saved_cookies)
                            self.log(f"  TINEYE BROWSER COOKIES: loaded: {len(saved_cookies)} ({'; '.join(cookie_sources)})")
                        except Exception as _ce:
                            self.log(f"  TINEYE BROWSER COOKIES: не удалось добавить cookies: {type(_ce).__name__}: {str(_ce)[:120]}")
                    else:
                        self.log("  TINEYE BROWSER COOKIES: 0 (no sources)")

                    page = context.pages[0] if context.pages else context.new_page()
                    try:
                        page.goto("https://tineye.com/", wait_until="domcontentloaded", timeout=timeout_ms)
                    except Exception as _ge:
                        # A visible browser can still finish loading shortly after
                        # Playwright's navigation timeout.  Continue and inspect DOM.
                        self.log(f"  TINEYE BROWSER: стартовая страница не дождалась DOM полностью: {type(_ge).__name__}; продолжаю")
                    try:
                        page.wait_for_load_state("load", timeout=min(timeout_ms, 30000))
                    except Exception:
                        self._external_log("  TINEYE BROWSER: load/networkidle не дождался; продолжаю по DOM")
                    try:
                        page.wait_for_timeout(1500)
                    except Exception:
                        pass

                    def _challenge_status():
                        try:
                            txt = (page.inner_text("body", timeout=3000) or "")
                        except Exception:
                            txt = ""
                        blocked, code, reason = self._tineye_block_text_info(txt)
                        if blocked:
                            return "blocked", code, reason
                        return "ok", "", ""

                    status, code, reason = _challenge_status()
                    # v212 kept a manual path where the "видимый браузер ждёт ручное прохождение";
                    # v272 treats Cloudflare/Troubleshoot block pages as parser-only cooldown instead.
                    if status == "blocked":
                        self.log(f"  TINEYE BROWSER: Cloudflare/verification block detected{f' code={code}' if code else ''}")
                        self._tineye_set_parser_cooldown(reason or "cloudflare_or_challenge", code)
                        return None

                    selector = "input[type='file']"
                    file_input = None
                    try:
                        page.wait_for_selector(selector, state="attached", timeout=min(timeout_ms, 45000))
                        handles = page.query_selector_all(selector)
                        if handles:
                            file_input = handles[0]
                    except Exception:
                        file_input = None
                    if file_input is None:
                        self.log("  TINEYE BROWSER: input[type=file] не найден")
                        return None

                    self.log(f"  TINEYE BROWSER: загружаю {img_path_obj.name} через страницу TinEye...")
                    file_input.set_input_files(str(img_path_obj))

                    try:
                        page.click("button[type='submit'], input[type='submit'], button:has-text('Search'), button:has-text('Upload')", timeout=3000)
                    except Exception:
                        pass

                    html_text = ""
                    final_url = ""
                    external_hint = _re.compile(r"https?://(?![^/]*tineye\.com)[^\"'<>\\]{8,}", _re.I)
                    wait_until = _time.monotonic() + timeout_ms / 1000.0
                    while _time.monotonic() < wait_until:
                        try:
                            page.wait_for_timeout(3000)
                        except Exception:
                            pass
                        try:
                            html_text = page.content()
                            final_url = page.url
                        except Exception:
                            html_text = ""
                            final_url = ""
                        blocked, code, reason = self._tineye_block_text_info(html_text)
                        if blocked:
                            self.log(f"  TINEYE BROWSER: Cloudflare/verification block detected during upload{f' code={code}' if code else ''}")
                            self._tineye_set_parser_cooldown(reason or "cloudflare_or_challenge", code)
                            return None
                        if external_hint.search(html_text):
                            break
                        # If TinEye moved to a results/search page, give it a bit
                        # more time but do not require networkidle, which is flaky.
                        if "/search" in (final_url or ""):
                            try:
                                page.wait_for_timeout(4000)
                                html_text = page.content()
                            except Exception:
                                pass
                            break
                    if not html_text:
                        try:
                            html_text = page.content()
                            final_url = page.url
                        except Exception:
                            html_text = ""
                            final_url = ""
                    blocked, code, reason = self._tineye_block_text_info(html_text)
                    if blocked:
                        self.log(f"  TINEYE BROWSER: Cloudflare/verification block detected in final page{f' code={code}' if code else ''}")
                        self._tineye_set_parser_cooldown(reason or "cloudflare_or_challenge", code)
                        return None
                finally:
                    if context is not None:
                        try:
                            context.close()
                        except Exception:
                            pass

            urls = []

            def _add_url(value):
                u = self._tineye_normalize_source_url(value)
                if not u:
                    return
                if u not in urls:
                    urls.append(u)

            for pattern in [
                r'"(?:backlink|backlink_url|page_url|source_url|url)"\s*:\s*"(https?://[^"\\]{10,}(?:\\.[^"\\]*)*)"',
                r"(?:href|data-url|data-backlink)=[\"'](https?://[^\"']{10,})[\"']",
            ]:
                for m in _re.finditer(pattern, html_text):
                    _add_url(m.group(1))
                if urls:
                    break

            before_filter_count = len(urls)
            urls = self._tineye_filter_source_urls(urls, max_results)
            dropped = max(0, before_filter_count - len(urls))
            if dropped:
                self.log(f"  TINEYE BROWSER FILTER: отброшено мусорных/дублирующих URL: {dropped}")
            self.log(f"  TINEYE BROWSER: {len(urls)} результатов")
            for u in urls[:3]:
                self.log(f"    -> {u[:80]}")
            return [(u, 0.0) for u in urls]
        except Exception as _e:
            # TinEye is optional and source-only.  Its browser fallback must not
            # poison the whole file as a transient network failure, otherwise a
            # good SauceNAO source-only result gets deferred instead of saved.
            msg = str(_e).splitlines()[0][:180]
            self._external_log(f"  TINEYE BROWSER ERROR: {type(_e).__name__}: {msg}")
            return None


    def tineye_urls(self, img_path):
        """TinEye fallback for the final broken/no-tag tail.

        Fast path tries the public HTTP upload once.  If TinEye returns 405/403,
        the session remembers that the HTTP endpoint is blocked and falls back to
        a normal Playwright browser profile for later TinEye attempts.  Captcha
        or challenge pages are not bypassed; TinEye is disabled for the current
        run in that case.
        """
        if not self.settings.get("enable_tineye"):
            return []
        if self._tineye_parser_cooldown_active():
            return []
        if self.settings.get("_tineye_disabled_this_session"):
            self.log("  TINEYE SKIP: отключён до конца текущего запуска")
            return []
        import time
        import random
        import re as _re
        from html import unescape as _html_unescape

        try:
            delay_min = float(self.settings.get("tineye_delay_min", 30) or 0)
            delay_max = float(self.settings.get("tineye_delay_max", 90) or 0)
        except Exception:
            delay_min, delay_max = 30.0, 90.0
        delay_min = max(0.0, min(3600.0, delay_min))
        delay_max = max(0.0, min(3600.0, delay_max))
        if delay_max < delay_min:
            delay_min, delay_max = delay_max, delay_min
        delay = random.uniform(delay_min, delay_max) if delay_max > 0 else 0.0
        if delay > 0:
            self.log(f"  TINEYE: ожидание {delay:.0f}с перед отправкой...")
            # Parser-only TinEye wait: keep it cancellable and re-check cooldown
            # so a Cloudflare block from another file stops the queue quickly.
            end_wait = time.monotonic() + delay
            while time.monotonic() < end_wait:
                if self.cancelled() or self._tineye_parser_cooldown_active():
                    return []
                time.sleep(min(5.0, max(0.0, end_wait - time.monotonic())))
        else:
            self.log("  TINEYE: задержка отключена")

        max_results = max(1, min(100, int(self.settings.get("tineye_max_results", 10) or 10)))
        try:
            img_path_obj = Path(img_path)
            if not img_path_obj.exists():
                self.log("  TINEYE SKIP: файл не найден")
                return []

            if self.settings.get("_tineye_http_upload_blocked"):
                if self.settings.get("tineye_browser_fallback", True):
                    self.log("  TINEYE: HTTP upload ранее вернул 405/403; сразу использую браузер")
                    _pw_result = self._tineye_try_playwright(img_path_obj, max_results)
                    if _pw_result is not None:
                        return _pw_result
                self.log("  TINEYE: браузерный режим недоступен/не помог; отключаю TinEye на сессию")
                try:
                    self.settings["_tineye_disabled_this_session"] = True
                except Exception:
                    pass
                return []

            # Use curl_cffi when available; its requests-like API does not
            # support files=, so the upload branch below uses CurlMime/multipart.
            using_cffi = False
            try:
                import curl_cffi as _curl_cffi
                from curl_cffi import requests as _cffi_requests
                s = _cffi_requests.Session(impersonate="chrome136")
                using_cffi = True
                self.log("  TINEYE: curl_cffi Chrome136 multipart")
            except ImportError:
                _curl_cffi = None
                s = self.session
                self.log("  TINEYE: curl_cffi недоступен, используется обычный requests")

            # Full browser headers including sec-ch-ua and sec-fetch-*
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/136.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": random.choice([
                    "en-US,en;q=0.9",
                    "en-GB,en;q=0.9",
                    "en-US,en;q=0.8,de;q=0.6",
                ]),
                "Accept-Encoding": "gzip, deflate, br",
                "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "none",
                "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1",
                "Cache-Control": "max-age=0",
            }

            # Step 1: load homepage for cookies + CSRF
            csrf = ""
            try:
                home_r = s.get(
                    "https://tineye.com/",
                    headers=headers,
                    timeout=20,
                )
                blocked, code, reason = self._tineye_block_text_info(getattr(home_r, "text", ""))
                if blocked:
                    self.log(f"  TINEYE: Cloudflare/verification block on homepage{f' code={code}' if code else ''}")
                    self._tineye_set_parser_cooldown(reason or "cloudflare_or_challenge", code)
                    return []
                for pattern in [
                    r"csrfmiddlewaretoken[^>]+value=[\"']([^\"']+)",
                    r'"csrfToken"\s*:\s*"([^"]{8,})"',
                    r"csrf-token[^>]+content=[\"']([^\"']+)",
                ]:
                    m = _re.search(pattern, home_r.text)
                    if m:
                        csrf = m.group(1)
                        break
                # Small jitter after homepage load
                time.sleep(random.uniform(1.5, 4.0))
            except Exception as exc:
                self.log(f"  TINEYE: главная не загрузилась: {exc}")

            # Step 2: upload image
            self.log(f"  TINEYE: загружаю {img_path_obj.name} через /search/upload...")
            suf = img_path_obj.suffix.lower().lstrip(".")
            mime_map = {"png": "image/png", "gif": "image/gif",
                        "webp": "image/webp", "jpeg": "image/jpeg"}
            mime = mime_map.get(suf, "image/jpeg")

            up_headers = dict(headers)
            up_headers["Accept"] = "application/json, */*; q=0.01"
            up_headers["Referer"] = "https://tineye.com/"
            up_headers["Origin"] = "https://tineye.com"
            up_headers["sec-fetch-site"] = "same-origin"
            up_headers["sec-fetch-mode"] = "cors"
            up_headers["sec-fetch-dest"] = "empty"
            up_headers["X-Requested-With"] = "XMLHttpRequest"

            form_data = {}
            if csrf:
                form_data["csrfmiddlewaretoken"] = csrf

            if using_cffi:
                multipart = _curl_cffi.CurlMime()
                try:
                    if csrf:
                        multipart.addpart(name="csrfmiddlewaretoken", data=str(csrf).encode("utf-8"))
                    multipart.addpart(
                        name="image",
                        content_type=mime,
                        filename=img_path_obj.name,
                        local_path=str(img_path_obj),
                    )
                    resp = s.post(
                        "https://tineye.com/search/upload",
                        headers=up_headers,
                        multipart=multipart,
                        timeout=60,
                        allow_redirects=True,
                    )
                finally:
                    close_multipart = getattr(multipart, "close", None)
                    if callable(close_multipart):
                        try:
                            close_multipart()
                        except Exception:
                            pass
            else:
                with open(img_path_obj, "rb") as fh:
                    resp = s.post(
                        "https://tineye.com/search/upload",
                        headers=up_headers,
                        files={"image": (img_path_obj.name, fh, mime)},
                        data=form_data,
                        timeout=60,
                        allow_redirects=True,
                    )

            blocked, code, reason = self._tineye_block_text_info(getattr(resp, "text", ""))
            if blocked:
                self.log(f"  TINEYE: Cloudflare/verification block on upload response{f' code={code}' if code else ''}")
                self._tineye_set_parser_cooldown(reason or "cloudflare_or_challenge", code)
                return []

            if resp.status_code not in (200, 301, 302):
                self.log(f"  TINEYE: HTTP {resp.status_code}")
                if resp.status_code in (405, 403):
                    self.log("  TINEYE: HTTP endpoint не принимает автоматическую загрузку; запоминаю на сессию")
                    try:
                        self.settings["_tineye_http_upload_blocked"] = True
                    except Exception:
                        pass
                    if self.settings.get("tineye_browser_fallback", True):
                        self.log("  TINEYE: пробую через браузерный профиль Playwright...")
                        _pw_result = self._tineye_try_playwright(img_path_obj, max_results)
                        if _pw_result is not None:
                            return _pw_result
                    self.log("  TINEYE: браузерный режим не дал результата; TinEye отключён на сессию")
                    try:
                        self.settings["_tineye_disabled_this_session"] = True
                    except Exception:
                        pass
                return []

            body = resp.text
            self.log(f"  TINEYE: ответ {len(body)} байт")

            urls: list = []

            def _add_tineye_result_url(value):
                u = self._tineye_normalize_source_url(value)
                if not u:
                    return
                if u not in urls:
                    urls.append(u)

            # Try JSON response
            try:
                import json as _json
                j = _json.loads(body)
                matches = j.get("matches") or (j.get("results") or {}).get("matches") or []
                for match in (matches or [])[:max_results * 5]:
                    if not isinstance(match, dict):
                        continue
                    for bl in match.get("backlinks") or []:
                        if isinstance(bl, dict):
                            _add_tineye_result_url(bl.get("backlink") or bl.get("url") or bl.get("source_url"))
                        else:
                            _add_tineye_result_url(bl)
                    # Some response variants place the best page/image URL at the match level.
                    _add_tineye_result_url(match.get("backlink") or match.get("url") or match.get("page_url"))
            except Exception:
                pass

            # HTML / embedded-JSON fallback
            if not urls:
                for pattern in [
                    r'"(?:backlink|backlink_url|page_url|url)"\s*:\s*"(https?://[^"\\]{10,}(?:\\.[^"\\]*)*)"',
                    r"(?:href|data-url|data-backlink)=[\"'](https?://[^\"']{10,})[\"']",
                ]:
                    for m in _re.finditer(pattern, body):
                        _add_tineye_result_url(m.group(1))
                    if urls:
                        break

            before_filter_count = len(urls)
            urls = self._tineye_filter_source_urls(urls, max_results)
            dropped = max(0, before_filter_count - len(urls))
            if dropped:
                self.log(f"  TINEYE FILTER: отброшено мусорных/дублирующих URL: {dropped}")
            self.log(f"  TINEYE: {len(urls)} результатов")
            for u in urls[:3]:
                self.log(f"    -> {u[:80]}")
            return [(u, 0.0) for u in urls]

        except Exception as exc:
            self.log(f"  TINEYE ERROR: {type(exc).__name__}: {exc}")
            return []


    def _iqdb_url_md5_tokens(self, url):
        """Extract explicit MD5 tokens from IQDB result URLs.

        IQDB often returns Gelbooru list URLs like ?md5=<hash>.  The selector
        uses these tokens only for ranking inside the same site; it does not
        turn list pages into accepted post URLs by itself.
        """
        text = unquote(str(url or "")).lower()
        return set(re.findall(r"(?<![a-f0-9])([a-f0-9]{32})(?![a-f0-9])", text))


    def select_iqdb_best_per_site(self, candidates, expected_md5s=None):
        """Choose one IQDB post per host, then metadata may be merged across hosts.

        IQDB may return several visually similar posts from one booru.  Merging
        those posts contaminates tags.  Different booru sites are useful
        complementary metadata sources, so one selected post from each enabled
        host is retained.  An explicit URL MD5 match outranks similarity inside
        the same host; otherwise the highest similarity wins.
        """
        expected = {str(md5).strip().lower() for md5 in (expected_md5s or []) if is_md5(str(md5).strip().lower())}
        selected = {}
        host_order = []
        counts = {}

        for index, pair in enumerate(candidates or []):
            try:
                url, sim = pair
                similarity = float(sim)
            except Exception:
                continue
            host = urlparse(str(url)).netloc.lower().replace("www.", "") or "unknown"
            counts[host] = counts.get(host, 0) + 1
            if host not in host_order:
                host_order.append(host)
            url_md5s = self._iqdb_url_md5_tokens(url)
            exact_md5 = bool(expected.intersection(url_md5s))
            item = {
                "url": str(url),
                "similarity": similarity,
                "host": host,
                "exact_md5": exact_md5,
                "order": index,
            }
            current = selected.get(host)
            if current is None or (int(exact_md5), similarity, -index) > (int(current["exact_md5"]), current["similarity"], -current["order"]):
                selected[host] = item

        if counts:
            summary = ", ".join(f"{host}={counts[host]}" for host in host_order)
            self.log(f"  IQDB RESULTS BY SITE: {summary}")
        return [selected[host] for host in host_order if host in selected]

    def _ascii2d_parse_results(self, html: str, domains: set) -> list:
        """Parse ascii2d result page and return (url, similarity) pairs.

        ascii2d.net HTML structure (handles both old and new layouts):
          .item-box
            .item-content  — thumbnail
            .detail-box
              small         — source site name + link  ← PRIMARY
              h6 > a        — artist link (pixiv/twitter)
              .hash         — perceptual hash value
        
        Strategy:
        1. Check all <a href> in .detail-box for known booru domains
        2. Also try external links (pixiv, twitter) as source hints
        3. Skip internal ascii2d links
        """
        soup = BeautifulSoup(html, "html.parser")
        out = []
        seen = set()

        for i, box in enumerate(soup.select(".item-box")):
            detail = box.select_one(".detail-box")
            if not detail:
                continue

            # Collect all candidate links from this result box
            candidates = []
            for a in detail.select("a[href]"):
                href = a.get("href", "").strip()
                if not href:
                    continue
                if href.startswith("//"):
                    href = "https:" + href
                elif href.startswith("/"):
                    href = "https://ascii2d.net" + href

                host = urlparse(href).netloc.lower().replace("www.", "")

                # Skip ascii2d internal links
                if "ascii2d.net" in host:
                    continue

                candidates.append((href, host))

            # Score: first box = best, each subsequent = -5
            score = max(60.0, 100.0 - i * 5)

            # Priority 1: exact domain match (gelbooru, rule34, etc.)
            for href, host in candidates:
                if host in domains and href not in seen:
                    seen.add(href)
                    out.append((href, score))
                    self.log(f"  ASCII2D hit[{i}] {score:.0f}% {href}")
                    break  # one per box

            # Priority 2: if no exact match, try partial domain match
            if not any(href for href, _ in candidates if href in seen):
                for href, host in candidates:
                    if href not in seen:
                        # Check if any enabled domain is a substring
                        for dom in domains:
                            if dom in host or host in dom:
                                seen.add(href)
                                out.append((href, score * 0.8))
                                self.log(f"  ASCII2D partial[{i}] {score*0.8:.0f}% {href}")
                                break

        return out

    def _get_ascii2d_session(self):
        """Build a session that can bypass ascii2d Cloudflare protection.

        cloudscraper is tried before curl_cffi because ASCII2D needs file upload.
        curl_cffi can warm the homepage, but the upload path is not reliable
        when converted to plain requests.
        """
        # Priority 0: FlareSolverr — real Chrome, 100% CF bypass
        fs_url = ""
        try:
            fs_url = (self.settings or {}).get("flaresolverr_url", "").strip() if self.settings else ""
        except Exception:
            pass
        if fs_url:
            try:
                from core.flaresolverr import FlareSolverrClient
                import requests as _std_req, time as _time
                # Cache session for 25 minutes (cf_clearance expires after 30min)
                _cache_key = f"ascii2d_session_{fs_url}"
                _cached = getattr(self, "_ascii2d_session_cache", {})
                _cached_entry = _cached.get(_cache_key)
                if _cached_entry and _time.time() - _cached_entry[1] < 1500:
                    self.log("  ASCII2D: reusing cached FlareSolverr session")
                    return _cached_entry[0]

                client = FlareSolverrClient(fs_url)
                # Solve homepage to get cf_clearance
                self.log("  ASCII2D: FlareSolverr solving homepage for cf_clearance...")
                cookies, ua = client.get_cookies_for("https://ascii2d.net/")
                cf = next((c for c in cookies if c.get("name") == "cf_clearance"), None)
                if cf:
                    self.log(f"  ASCII2D: FlareSolverr got cf_clearance ✓")
                else:
                    self.log("  ASCII2D: FlareSolverr no cf_clearance yet, trying upload page...")
                    cookies2, ua2 = client.get_cookies_for("https://ascii2d.net/search/file")
                    cookies = cookies + cookies2
                    ua = ua2 or ua
                    cf = next((c for c in cookies if c.get("name") == "cf_clearance"), None)

                # Build plain requests session with CF cookies + matching UA
                s = _std_req.Session()
                s.headers.update({
                    "User-Agent": ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                    "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
                    "Referer": "https://ascii2d.net/",
                })
                from urllib.parse import urlparse
                for c in cookies:
                    try:
                        s.cookies.set(
                            c.get("name",""), c.get("value",""),
                            domain=c.get("domain","ascii2d.net").lstrip(".")
                        )
                    except Exception:
                        pass

                status = "✓ (cf_clearance)" if cf else "(no cf_clearance)"
                self.log(f"  ASCII2D: FlareSolverr session ready {status}")
                # Cache session
                if not hasattr(self, "_ascii2d_session_cache"):
                    self._ascii2d_session_cache = {}
                self._ascii2d_session_cache[_cache_key] = (s, _time.time())
                return s
            except Exception as fe:
                self.log(f"  ASCII2D: FlareSolverr error: {fe}")
        else:
            self.log("  ASCII2D: FlareSolverr not set (Settings → FlareSolverr URL)")

        # Priority 1: cloudscraper for Cloudflare + file upload
        try:
            import cloudscraper
            cs = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            cs.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/136.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
                "Referer": "https://ascii2d.net/",
            })
            try:
                r_warm = cs.get("https://ascii2d.net/", timeout=20, allow_redirects=True)
                self.log(f"  ASCII2D: cloudscraper warm-up status={getattr(r_warm, 'status_code', '?')}")
            except Exception as we:
                self.log(f"  ASCII2D: cloudscraper warm-up failed: {we}")
            self.log("  ASCII2D: using cloudscraper")
            return cs
        except ImportError:
            self.log("  ASCII2D: cloudscraper not installed in this Python. Try: python -m pip install cloudscraper")
        except Exception as e:
            self.log(f"  ASCII2D: cloudscraper error: {e}")

        # Priority 2: curl_cffi with Chrome impersonation
        if _CURL_CFFI:
            try:
                s = requests.Session(impersonate="chrome120")
                s.headers.update({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/136.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                    "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Referer": "https://ascii2d.net/",
                })
                try:
                    r_warm = s.get("https://ascii2d.net/", timeout=15, allow_redirects=True)
                    self.log(f"  ASCII2D: curl_cffi warm-up status={getattr(r_warm, 'status_code', '?')}")
                except Exception as we:
                    self.log(f"  ASCII2D: curl_cffi warm-up failed: {we}")
                return s
            except Exception as e:
                self.log(f"  ASCII2D: curl_cffi session error: {e}")

        # Priority 3: requests
        import importlib as _imp
        std = _imp.import_module("requests").Session()
        std.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/136.0.0.0 Safari/537.36"
        )
        self.log("  ASCII2D: falling back to standard requests (may get 403)")
        return std

    def _ascii2d_try_playwright(self, img_path, domains):
        """Try ascii2d via browser automation. Returns list or None on failure."""

        def _launch_and_search(get_browser_fn):
            """Inner: try to get browser from factory fn, upload, return html."""
            try:
                _browser, _ctx = get_browser_fn()
                _page = _ctx.new_page()
                _page.goto("https://ascii2d.net/", timeout=60000, wait_until="load")
                _page.wait_for_timeout(4000)
                _page.goto("https://ascii2d.net/search/file",
                           timeout=60000, wait_until="load")
                _page.wait_for_timeout(3000)
                # Wait for CF to finish (either file input appears or we timeout)
                _deadline = 35  # seconds — CF challenge timeout (patchright needs more time)
                _found = False
                import time as _t
                _t0 = _t.time()
                while _t.time() - _t0 < _deadline:
                    try:
                        _page.wait_for_selector("input[type='file']", timeout=2000)
                        _found = True
                        break
                    except Exception:
                        _page.wait_for_timeout(1500)
                if not _found:
                    _browser.close()
                    return None  # CF still blocking
                _page.set_input_files("input[type='file']", str(img_path))
                _page.wait_for_timeout(800)
                _page.click("input[type='submit'], button[type='submit']")
                _page.wait_for_load_state("networkidle", timeout=30000)
                _html = _page.content()
                _browser.close()
                return _html
            except Exception as _e:
                try: _browser.close()
                except Exception: pass
                raise _e

        # Method A: patchright (undetected chromium, best CF bypass)
        try:
            from patchright.sync_api import sync_playwright as _patchright
            self.log("  ASCII2D: patchright (undetected Chrome)...")
            with _patchright() as _pw:
                def _get():
                    _b = _pw.chromium.launch(
                        headless=True,
                        args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
                    _c = _b.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/142.0.0.0 Safari/537.36",
                        viewport={"width":1280,"height":800})
                    return _b, _c
                _html = _launch_and_search(_get)
            if _html and "item-box" in _html:
                _out = self._ascii2d_parse_results(_html, domains)
                self.log(f"  ASCII2D: patchright ✓ {len(_out)} result(s)")
                return _out or []
            elif _html:
                self.log("  ASCII2D: patchright - page loaded but CF still blocking")
            else:
                self.log("  ASCII2D: patchright - CF challenge not solved in time")
        except ImportError:
            self.log("  ASCII2D: patchright not installed → pip install patchright && python -m patchright install chromium")
        except Exception as _pe:
            self.log(f"  ASCII2D: patchright error: {type(_pe).__name__}: {str(_pe)[:160]}")

        # Method B: regular Playwright
        try:
            from playwright.sync_api import sync_playwright as _pwright
            self.log("  ASCII2D: Playwright headless...")
            with _pwright() as _pw:
                def _get():
                    _b = _pw.chromium.launch(
                        headless=True,
                        args=["--no-sandbox","--disable-blink-features=AutomationControlled"],
                    )
                    _c = _b.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 Chrome/142.0.0.0 Safari/537.36",
                        viewport={"width":1280,"height":800},
                    )
                    _c.add_init_script(
                        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
                    )
                    return _b, _c
                _html = _launch_and_search(_get)
            if _html and "item-box" in _html:
                _out = self._ascii2d_parse_results(_html, domains)
                self.log(f"  ASCII2D: Playwright ✓ {len(_out)} result(s)")
                return _out or []
            elif _html:
                self.log("  ASCII2D: Playwright CF still blocking (install patchright for better bypass)")
            else:
                self.log("  ASCII2D: Playwright timed out (CF challenge not solved)")
        except ImportError:
            self.log("  ASCII2D: Playwright not installed → pip install playwright && playwright install chromium")
        except Exception as _pe:
            self.log(f"  ASCII2D: Playwright error: {type(_pe).__name__}: {str(_pe)[:80]}")

        return None  # All browser methods failed

    def ascii2d_by_url(self, image_url: str, session=None) -> list:
        """Search ascii2d by image URL instead of file upload.
        Works via FlareSolverr GET request (no POST = no CF block).
        """
        if not image_url or not image_url.startswith("http"):
            return []
        domains = self.enabled_domains()
        try:
            import urllib.parse
            enc = urllib.parse.quote(image_url, safe="")
            search_url = f"https://ascii2d.net/search/url/{enc}"
            if session is None:
                session = self._get_ascii2d_session()
            self.log(f"  ASCII2D URL search: {image_url[:60]}...")
            r = session.get(search_url, timeout=30, allow_redirects=True)
            if r.status_code == 200:
                results = self._ascii2d_parse_results(r.text, domains)
                if results:
                    self.log(f"  ASCII2D URL search: {len(results)} result(s)")
                return results
        except Exception as e:
            self.log(f"  ASCII2D URL search error: {e}")
        return []

    def ascii2d_urls(self, img_path):
        domains = self.enabled_domains()

        # Priority 1: Playwright — real Chromium, 100% CF bypass for file upload
        # Try patchright first (undetected fork of Playwright)
        # then regular Playwright, then FlareSolverr CSRF trick
        _pw_result = self._ascii2d_try_playwright(img_path, domains)
        if _pw_result is not None:
            return _pw_result

        # Priority 2: FlareSolverr + cloudscraper + curl_cffi fallbacks
        s = self._get_ascii2d_session()

        # Try file upload (hash search)
        hash_html = ""
        bovw_url = ""
        try:
            # Get CSRF token from homepage before uploading
            extra_data = {}
            try:
                warm = s.get("https://ascii2d.net/search/file", timeout=15)
                if warm.status_code == 200:
                    from bs4 import BeautifulSoup as _BS
                    _soup = _BS(warm.text, "html.parser")
                    csrf = _soup.select_one("input[name='authenticity_token']")
                    if csrf:
                        extra_data["authenticity_token"] = csrf.get("value","")
                    utf8 = _soup.select_one("input[name='utf8']")
                    if utf8:
                        extra_data["utf8"] = utf8.get("value", "✓")
                    if "cf_clearance" in str(warm.cookies):
                        self.log("  ASCII2D: got cf_clearance from upload page")
            except Exception as _we:
                self.log(f"  ASCII2D: pre-upload fetch: {_we}")

            r = _post_with_file(s, "https://ascii2d.net/search/file",
                               img_path, file_field="file",
                               extra_data=extra_data,
                               timeout=max(self.timeout, 60))
            if r.status_code == 403:
                self.log("  ASCII2D: 403 upload blocked by Cloudflare Bot Fight Mode")
                # Try URL-based search as fallback
                # Look for source URLs in known locations
                source_urls = []
                try:
                    # Check if img_path has an associated source URL in DB
                    from core.database.connection import db
                    with db(self.settings, readonly=True) as _conn:
                        _row = _conn.execute(
                            "SELECT rm.file_url FROM raw_metadata rm "
                            "JOIN images i ON i.id=rm.image_id WHERE i.path=?",
                            (str(img_path),)
                        ).fetchone()
                        if _row and _row[0]:
                            source_urls.append(_row[0])
                except Exception:
                    pass
                if source_urls:
                    self.log(f"  ASCII2D: trying URL search for {source_urls[0][:50]}...")
                    url_results = self.ascii2d_by_url(source_urls[0], s)
                    if url_results:
                        return url_results
                self.log("  ASCII2D: no source URL for URL search. Use FlareSolverr + Playwright for full bypass.")
                return []
            r.raise_for_status()
            hash_html = r.text
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

        # If hash gave no results, try bovw (color/feature search)
        if not out and bovw_url:
            try:
                r2 = s.get(bovw_url, timeout=max(self.timeout, 45), allow_redirects=True)
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

    def merge_conveyor_match_into_existing(self, archived_media_path, original_img, all_tags, sources, all_groups, source_tag_groups=None):
        """Merge newly found source metadata into an existing FOUND archive row.

        A newly enabled site must enrich an already archived file without copying
        the same media into a new session folder on every incremental rescan.
        """
        archived_media_path = Path(archived_media_path)
        original_img = Path(original_img)
        all_tags = unique_keep_order(filter_numeric_tags(list(all_tags or []), self.settings.get("ignore_numeric_tags")))
        if not all_tags or not archived_media_path.exists():
            return "nomatch"
        tag_groups = merge_tag_groups(list(all_groups or []))
        if not groups_to_tags(tag_groups):
            tag_groups["general"] = all_tags
        source_list = [str(x) for x in (sources or []) if str(x).strip()]
        try:
            from core.import_pipeline import register_media_import
            _merge_result = register_media_import(
                self.settings, archived_media_path, tags=all_tags, groups=tag_groups,
                sources=source_list, status="found", original_path=str(original_img),
                source_tag_groups=source_tag_groups,
                origin="tagger", merge_existing=True, generate_thumbnail=False,
            )
            _added = int((_merge_result or {}).get("source_added", 0) or 0)
            if _added:
                self.log(f"  EXACT MD5 MERGE: existing_media={archived_media_path} source_added={_added} no_physical_copy_created=1")
            _log_nomatch_promote_cleanup(self.log, _merge_result)
        except Exception as e:
            self.log(f"  EXISTING METADATA MERGE ERROR: {e}")
            return "error"
        self.log(f"  TAGS MERGED INTO EXISTING FOUND [{Path(original_img).name}]: {len(all_tags)}")
        return "tagged"

    def save_conveyor_match(self, img, all_tags, sources, all_groups, source_tag_groups=None):
        """Commit an aggregate exact-MD5 match collected by site workers.

        The asynchronous site conveyor keeps network calls parallel per host,
        but persistence remains serialized through this method.  This prevents
        concurrent SQLite writes and preserves the same result format used by
        the ordinary single-file path.
        """
        img = Path(img)
        all_tags = unique_keep_order(filter_numeric_tags(list(all_tags or []), self.settings.get("ignore_numeric_tags")))
        if not all_tags:
            return "nomatch"
        tag_groups = merge_tag_groups(list(all_groups or []))
        if not groups_to_tags(tag_groups):
            tag_groups["general"] = all_tags
        source_text = "\n".join(str(x) for x in (sources or []) if str(x).strip())
        archived_media = copy_result_files(self.settings, img, "tagged")
        if not _valid_archived_media_path(archived_media):
            self.log(f"  FOUND SAVE ERROR: managed copy missing for {img.name}; metadata not written")
            return "retry_network"
        _import_result = save_found_metadata(self.settings, img, all_tags, source_text, tag_groups, status="tagged", archived_media_path=archived_media, source_tag_groups=source_tag_groups)
        if isinstance(_import_result, dict) and int(_import_result.get("source_added", 0) or 0):
            self.log(f"  EXACT MD5 MERGE: existing_media={_import_result.get('canonical_path','')} source_added={int(_import_result.get('source_added',0))} no_physical_copy_created=1")
        _log_nomatch_promote_cleanup(self.log, _import_result)
        remove_nomatch(img, settings=self.settings)
        cleanup_archived_result(self.settings, img, ("nomatch", "partial"))
        self.log(f"  TAGS [{img.name}]: {len(all_tags)}")
        return "tagged"

    def process_image(self, img, persist_lock=None):
        """Search one file and serialize only final persistence when a lock is supplied.

        Reverse-search network operations can take minutes. In conveyor mode they
        must not hold the SQLite/file-output lock or exact-MD5 lanes will appear
        frozen while SauceNAO/IQDB/Ascii2D is waiting.
        """
        def _persist_guard():
            return persist_lock if persist_lock is not None else nullcontext()
        self._reset_network_state()
        self._saucenao_deferred = False
        self._saucenao_defer_reason = ""
        self._saucenao_retry_after = 0
        self._last_saucenao_source_only = []
        self._last_reverse_source_only = []

        if self.settings.get("skip_existing") and not self.settings.get("retry_nomatch"):
            already = output_processed_status(self.settings, img)
            if already:
                self.log(f"SKIP ARCHIVED ({already}): {img.name}")
                return "skip"

        self.log(f"SEARCH: {img.name}")
        self._partial_match_found = False
        self._partial_match_reason = ""

        search_img = video_frame_image(img)
        if search_img != img:
            self.log(f"  VIDEO FRAME: {search_img.name}")

        try:
            from core.file_hash_cache import get_or_compute_phash as _cached_phash
            img_phash, _phash_hit = _cached_phash(self.settings, search_img, file_phash)
        except Exception:
            img_phash = file_phash(search_img)
            _phash_hit = False
        if img_phash:
            self.log(f"  PHASH: {img_phash}" + (" (cache)" if _phash_hit else ""))

        all_tags = []
        all_groups = []
        sources = []
        source_tag_groups = []
        known_md5s = set()

        def _reverse_md5_relay(label, url, similarity=0.0):
            """Try URL -> verified MD5 -> normal MD5 pipeline before URL tags.

            Reverse search often returns a post page on one site.  If that page
            exposes an authoritative MD5, checking that MD5 across every enabled
            site is safer and richer than using only tags from the original
            reverse result URL.
            """
            url = str(url or "").strip()
            if not url:
                return {"kind": "none"}
            md5v = ""
            relay_origin = ""
            try:
                if str(label).lower().startswith("saucenao"):
                    md5v = str((getattr(self, "_last_saucenao_md5_candidates", {}) or {}).get(url) or "").strip().lower()
                    if md5v and is_md5(md5v):
                        relay_origin = "embedded-md5"
                        self.log(f"  {label} E621 TITLE MD5 CANDIDATE: {md5v}")
                if not md5v:
                    md5v = self.extract_md5_from_post_url(url)
                    if md5v:
                        relay_origin = "post-url"
                if not md5v:
                    md5v = self.extract_md5_from_source_url_relay(url)
                    if md5v:
                        relay_origin = "source-search"
            except Exception as e:
                self.log(f"  {label} MD5 RELAY EXTRACT ERROR: {type(e).__name__}: {e}")
                md5v = ""
                relay_origin = ""
            md5v = str(md5v or "").strip().lower()
            if md5v and str(label).lower().startswith("tineye"):
                self.log(f"  TINEYE SOURCE URL RELAY: origin={relay_origin or 'unknown'} md5={md5v} url={url}")
            if not is_md5(md5v):
                return {"kind": "none"}
            if md5v in known_md5s:
                self.log(f"  {label} MD5 RELAY SKIP: already checked md5={md5v}")
                return {"kind": "seen", "md5": md5v}
            known_md5s.add(md5v)
            relay_lookup_allowed = bool(
                self.settings.get("enable_md5_lookup")
                or self.settings.get("_allow_reverse_md5_relay_lookup")
            )
            if not relay_lookup_allowed:
                self.log(f"  {label} MD5 RELAY FOUND BUT MD5 LOOKUP DISABLED: md5={md5v} url={url}")
                self._record_reverse_source_only(f"{label} MD5 relay", url, similarity)
                return {"kind": "source_only", "md5": md5v}
            self.log(f"  {label} MD5 RELAY: url={url} md5={md5v}")
            old_lookup_path = getattr(self, "_current_md5_lookup_path", "")
            self._current_md5_lookup_path = str(img)
            try:
                tags_r, srcs_r, groups_r = self.md5_lookup_all(md5v)
                stg_r = list(getattr(self, "_last_md5_source_tag_groups", []) or [])
            finally:
                self._current_md5_lookup_path = old_lookup_path
            tags_r = unique_keep_order(filter_numeric_tags(list(tags_r or []), self.settings.get("ignore_numeric_tags")))
            if tags_r:
                self.log(f"  {label} MD5 RELAY TAGS: md5={md5v} tags={len(tags_r)} sources={len(srcs_r or [])}")
                return {"kind": "tags", "md5": md5v, "tags": tags_r, "sources": list(srcs_r or []), "groups": list(groups_r or []), "source_tag_groups": stg_r}
            self.log(f"  {label} MD5 RELAY SOURCE-ONLY: md5={md5v} no tags from enabled MD5 sites; url={url}")
            self._record_reverse_source_only(f"{label} MD5 relay", url, similarity)
            return {"kind": "source_only", "md5": md5v}

        def _accept_reverse_md5_relay(label, relay):
            before_count = len(set(all_tags))
            all_tags.extend(list(relay.get("tags") or []))
            for src in list(relay.get("sources") or []):
                sources.append(f"{label} relay-md5 {relay.get('md5','')} {src}")
            for grp in list(relay.get("groups") or []):
                if grp and groups_to_tags(grp):
                    all_groups.append(grp)
            source_tag_groups.extend(list(relay.get("source_tag_groups") or []))
            merged_added = len(set(all_tags)) - before_count
            self.log(f"  {label} MD5 RELAY ACCEPTED: md5={relay.get('md5','')} added_unique={max(merged_added, 0)}")

        # v238: exact MD5 lookup must use the real byte hash of the input file,
        # not a 32-hex filename.  Users can rename Telegram/booru files, or a
        # stale MD5 can be copied from another image.  The real hash is cached
        # by path+size+mtime so repeated parser passes do not reread the file.
        filename_md5 = img.stem.lower() if is_md5(img.stem) else ""
        real_md5 = ""
        real_md5_cache_hit = False
        try:
            from core.file_hash_cache import get_or_compute_md5 as _cached_md5
            # Exact booru MD5 for videos belongs to the media file, not to the
            # extracted video frame used for pHash/reverse image search.
            real_md5, real_md5_cache_hit = _cached_md5(self.settings, img)
        except Exception:
            try:
                real_md5 = file_md5(img).lower()
            except Exception:
                real_md5 = ""
        if real_md5:
            known_md5s.add(real_md5.lower())
            self.log(f"  REAL FILE MD5: {real_md5}" + (" (cache)" if real_md5_cache_hit else ""))
        if filename_md5 and real_md5 and filename_md5 != real_md5:
            self.log(f"  FILENAME MD5 MISMATCH: name={filename_md5} real={real_md5}; filename ignored")
        elif filename_md5 and not real_md5:
            known_md5s.add(filename_md5)

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        if self.settings.get("enable_md5_lookup") and real_md5:
            self.log(f"  TRY REAL FILE MD5: {real_md5}")
            old_lookup_path = getattr(self, "_current_md5_lookup_path", "")
            self._current_md5_lookup_path = str(img)
            try:
                tags, srcs, groups = self.md5_lookup_all(real_md5)
            finally:
                self._current_md5_lookup_path = old_lookup_path
            all_tags += tags
            sources += srcs
            all_groups += groups
            source_tag_groups += list(getattr(self, "_last_md5_source_tag_groups", []) or [])

            # Variant locators can expose an authoritative original/site MD5
            # after the local byte-MD5 missed: rule34 image-key/hotlink and ATF
            # pixel_hash -> media_asset are the current producers.  Run every new
            # variant MD5 through all enabled exact-MD5 sites immediately to merge
            # e621/Gelbooru/Danbooru/etc. before expensive reverse searches.
            variant_site_md5s = []
            for _attr in ("_last_variant_site_md5s", "_last_rule34_image_key_site_md5s", "_last_atf_pixel_hash_site_md5s"):
                for m in list(getattr(self, _attr, []) or []):
                    m = str(m or "").strip().lower()
                    if is_md5(m) and m not in variant_site_md5s:
                        variant_site_md5s.append(m)
            for site_md5 in unique_keep_order(variant_site_md5s):
                if site_md5 in known_md5s:
                    continue
                known_md5s.add(site_md5)
                self.log(f"  TRY VARIANT SITE MD5 RELAY: {site_md5}")
                old_lookup_path = getattr(self, "_current_md5_lookup_path", "")
                self._current_md5_lookup_path = str(img)
                try:
                    tags2, srcs2, groups2 = self.md5_lookup_all(site_md5)
                finally:
                    self._current_md5_lookup_path = old_lookup_path
                all_tags += tags2
                sources += srcs2
                all_groups += groups2
                source_tag_groups += list(getattr(self, "_last_md5_source_tag_groups", []) or [])

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        # Safe legacy fallback: only use a 32-hex filename when it is the same as
        # the real byte hash, or when the byte hash could not be computed at all.
        # A mismatched filename is never trusted for tags/sources.
        if self.settings.get("enable_md5_lookup") and not all_tags and filename_md5 and (not real_md5 or filename_md5 == real_md5):
            self.log(f"  TRY MD5 FROM FILENAME: {filename_md5}")
            tags, srcs, groups = self.md5_lookup_all(filename_md5)
            all_tags += tags
            sources += srcs
            all_groups += groups
            source_tag_groups += list(getattr(self, "_last_md5_source_tag_groups", []) or [])

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        try:
            from core.grabber_md5_cache import enabled as _grabber_md5_enabled
            _grabber_disk_cache_enabled = _grabber_md5_enabled(self.settings)
        except Exception:
            _grabber_disk_cache_enabled = bool(self.settings.get("grabber_disk_metadata_cache_enabled", self.settings.get("developer_grabber_md5_cache_enabled", True)))

        if not all_tags and _grabber_disk_cache_enabled:
            try:
                from core.grabber_md5_cache import lookup as _grabber_md5_lookup
                for _md5 in list(known_md5s):
                    cached = _grabber_md5_lookup(self.settings, _md5)
                    if not cached:
                        continue
                    cached_tags = list(cached.get("tags") or [])
                    cached_groups = cached.get("groups") or {}
                    if not cached_tags:
                        continue
                    cached_urls = list(cached.get("post_urls") or []) or list(cached.get("file_urls") or [])
                    self.log(f"  GRABBER MD5 CACHE HIT: md5={_md5} tags={len(cached_tags)} sources={len(cached_urls)}")
                    all_tags += cached_tags
                    if cached_groups and groups_to_tags(cached_groups):
                        all_groups.append(cached_groups)
                    else:
                        all_groups.append({"general": cached_tags})
                    for url in cached_urls:
                        sources.append(f"Grabber cache exact MD5 {url}")
                    stg_list = list(cached.get("source_tag_groups") or [])
                    if stg_list:
                        source_tag_groups += stg_list
                    elif cached_urls:
                        source_tag_groups += [{"url": u, "groups": cached_groups or {"general": cached_tags}, "method": "grabber_md5_cache"} for u in cached_urls]
                    break
            except Exception as e:
                self.log(f"  GRABBER MD5 CACHE ERROR: {type(e).__name__}: {e}")

        # Preserve paid SauceNAO quota: normal files try free reverse sources first.
        # A durable SauceNAO retry sets _saucenao_retry_only and skips this stage.
        if self.settings.get("enable_iqdb") and not all_tags and not self.settings.get("_saucenao_retry_only", False):
            _fallback_started = time.monotonic()
            self.report_activity("IQDB", img, "Обратный поиск")
            self.log("  IQDB START")

            # If MD5-site lookup was disabled, still allow an exact hash exposed
            # by IQDB result URLs to win inside its own site.
            if not known_md5s and real_md5:
                known_md5s.add(real_md5.lower())

            candidates = self.iqdb_urls(search_img)
            selected_candidates = self.select_iqdb_best_per_site(candidates, known_md5s)
            selected_with_tags = 0
            for selected in selected_candidates:
                url = selected["url"]
                sim = selected["similarity"]
                host = selected["host"]
                exact_note = " exact_md5=1" if selected["exact_md5"] else ""
                # A similarity candidate is not yet a confirmed metadata source.
                # Some Gelbooru result URLs look like posts but redirect to a
                # gallery/deleted page and return no API metadata.  Save the
                # source only after this exact URL yields tags for the file.
                try:
                    self.log(f"  IQDB SELECTED [{host}]: {sim:.2f}% {url}{exact_note}")
                    relay = _reverse_md5_relay("IQDB", url, sim)
                    if relay.get("kind") == "tags":
                        _accept_reverse_md5_relay("IQDB", relay)
                        sources.append(f"IQDB {sim:.2f}% relay-source {url}")
                        selected_with_tags += 1
                        break
                    if relay.get("kind") == "source_only":
                        continue
                    tags = self.tags_from_url(url)
                    groups = self.groups_or_defer_background(url, tags)

                    if tags:
                        sources.append(f"IQDB {sim:.2f}% {url}")
                        selected_with_tags += 1
                        before_count = len(set(all_tags))
                        all_tags += tags
                        if groups and groups_to_tags(groups):
                            all_groups.append(groups)
                        else:
                            try:
                                guessed = self._categorize_flat_tags(host, tags)
                                if guessed and groups_to_tags(guessed):
                                    all_groups.append(guessed)
                            except Exception:
                                pass
                        source_tag_groups.append({"url": url, "groups": groups or {"general": list(tags)}, "method": "iqdb"})
                        merged_added = len(set(all_tags)) - before_count
                        self.log(f"  IQDB TAGS [{host}]: received={len(tags)} added_unique={max(merged_added, 0)}")
                    else:
                        self.log(f"  IQDB DISCARD UNVERIFIED SOURCE [{host}]: no metadata returned; {url}")

                except Exception as e:
                    self.log(f"  IQDB DISCARD UNVERIFIED SOURCE [{host}]: metadata error; {url} {e}")
            if all_tags and selected_candidates:
                self.log(
                    f"  IQDB MERGED SITES: selected_sources={len(selected_candidates)} "
                    f"tag_sources={selected_with_tags} unique_tags={len(set(all_tags))}"
                )
            _fallback_elapsed = time.monotonic() - _fallback_started
            if _fallback_elapsed >= 5.0:
                self.log(f"  SLOW FALLBACK: IQDB path took {_fallback_elapsed:.1f}s for {img.name}")

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        if self.settings.get("enable_danbooru_iqdb") and not all_tags and not self.settings.get("_saucenao_retry_only", False):
            _fallback_started = time.monotonic()
            self.report_activity("Danbooru IQDB", img, "Обратный поиск")
            self.log("  DANBOORU IQDB START")

            if not known_md5s and real_md5:
                known_md5s.add(real_md5.lower())

            candidates = self.danbooru_iqdb_urls(search_img)
            selected_candidates = self.select_iqdb_best_per_site(candidates, known_md5s)
            selected_with_tags = 0
            for selected in selected_candidates:
                url = selected["url"]
                sim = selected["similarity"]
                host = selected["host"]
                exact_note = " exact_md5=1" if selected["exact_md5"] else ""
                try:
                    self.log(f"  DANBOORU IQDB SELECTED [{host}]: {sim:.2f}% {url}{exact_note}")
                    relay = _reverse_md5_relay("Danbooru IQDB", url, sim)
                    if relay.get("kind") == "tags":
                        _accept_reverse_md5_relay("Danbooru IQDB", relay)
                        sources.append(f"Danbooru IQDB {sim:.2f}% relay-source {url}")
                        selected_with_tags += 1
                        break
                    if relay.get("kind") == "source_only":
                        continue
                    tags = self.tags_from_url(url)
                    groups = self.groups_or_defer_background(url, tags)

                    if tags:
                        sources.append(f"Danbooru IQDB {sim:.2f}% {url}")
                        selected_with_tags += 1
                        before_count = len(set(all_tags))
                        all_tags += tags
                        if groups and groups_to_tags(groups):
                            all_groups.append(groups)
                        else:
                            try:
                                guessed = self._categorize_flat_tags(host, tags)
                                if guessed and groups_to_tags(guessed):
                                    all_groups.append(guessed)
                            except Exception:
                                pass
                        source_tag_groups.append({"url": url, "groups": groups or {"general": list(tags)}, "method": "danbooru_iqdb"})
                        merged_added = len(set(all_tags)) - before_count
                        self.log(f"  DANBOORU IQDB TAGS [{host}]: received={len(tags)} added_unique={max(merged_added, 0)}")
                    else:
                        self.log(f"  DANBOORU IQDB DISCARD UNVERIFIED SOURCE [{host}]: no metadata returned; {url}")
                except Exception as e:
                    self.log(f"  DANBOORU IQDB DISCARD UNVERIFIED SOURCE [{host}]: metadata error; {url} {e}")
            if all_tags and selected_candidates:
                self.log(
                    f"  DANBOORU IQDB MERGED SITES: selected_sources={len(selected_candidates)} "
                    f"tag_sources={selected_with_tags} unique_tags={len(set(all_tags))}"
                )
            _fallback_elapsed = time.monotonic() - _fallback_started
            if _fallback_elapsed >= 5.0:
                self.log(f"  SLOW FALLBACK: Danbooru IQDB path took {_fallback_elapsed:.1f}s for {img.name}")

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        if self.settings.get("enable_e621_iqdb") and not all_tags and not self.settings.get("_saucenao_retry_only", False):
            _fallback_started = time.monotonic()
            self.report_activity("e621 IQDB", img, "Обратный поиск")
            self.log("  E621 IQDB START")

            e621_with_tags = 0
            for url, sim in self.e621_iqdb_urls(search_img):
                try:
                    self.log(f"  E621 IQDB MATCH: {url}")
                    relay = _reverse_md5_relay("E621 IQDB", url, sim)
                    if relay.get("kind") == "tags":
                        _accept_reverse_md5_relay("E621 IQDB", relay)
                        sources.append(f"E621 IQDB relay-source {url}")
                        e621_with_tags += 1
                        break
                    if relay.get("kind") == "source_only":
                        continue
                    tags = self.tags_from_url(url)
                    groups = self.groups_or_defer_background(url, tags)
                    if tags:
                        before_count = len(set(all_tags))
                        all_tags += tags
                        if groups and groups_to_tags(groups):
                            all_groups.append(groups)
                        else:
                            try:
                                guessed = self._categorize_flat_tags("e621.net", tags)
                                if guessed and groups_to_tags(guessed):
                                    all_groups.append(guessed)
                            except Exception:
                                pass
                        sources.append(f"E621 IQDB {url}")
                        source_tag_groups.append({"url": url, "groups": groups or {"general": list(tags)}, "method": "e621_iqdb"})
                        e621_with_tags += 1
                        merged_added = len(set(all_tags)) - before_count
                        self.log(f"  E621 IQDB TAGS: received={len(tags)} added_unique={max(merged_added, 0)}")
                        break
                    else:
                        self.log(f"  E621 IQDB DISCARD: no metadata returned; {url}")
                except Exception as e:
                    self.log(f"  E621 IQDB URL ERROR: {url} {e}")
            if all_tags and e621_with_tags:
                self.log(f"  E621 IQDB ACCEPTED: tag_sources={e621_with_tags} unique_tags={len(set(all_tags))}")
            _fallback_elapsed = time.monotonic() - _fallback_started
            if _fallback_elapsed >= 5.0:
                self.log(f"  SLOW FALLBACK: e621 IQDB path took {_fallback_elapsed:.1f}s for {img.name}")

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        # v204: FuzzySearch and Fluffle were removed from the active reverse
        # chain. They caused low-score false positives and tag pollution.

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        if self.settings.get("enable_ascii2d") and not all_tags and not self.settings.get("_saucenao_retry_only", False):
            _fallback_started = time.monotonic()
            self.report_activity("Ascii2D", img, "Обратный поиск")
            self.log("  ASCII2D START")

            for url, sim in self.ascii2d_urls(search_img):
                try:
                    self.log(f"  ASCII2D MATCH: {url}")
                    relay = _reverse_md5_relay("ASCII2D", url, sim)
                    if relay.get("kind") == "tags":
                        _accept_reverse_md5_relay("ASCII2D", relay)
                        sources.append(f"ASCII2D relay-source {url}")
                        break
                    if relay.get("kind") == "source_only":
                        continue

                    tags = self.tags_from_url(url)
                    groups = self.groups_or_defer_background(url, tags)

                    if tags:
                        all_tags += tags

                        if groups and groups_to_tags(groups):
                            all_groups.append(groups)

                        sources.append(f"ASCII2D {url}")
                        source_tag_groups.append({"url": url, "groups": groups or {"general": list(tags)}, "method": "ascii2d"})

                        break

                except Exception as e:
                    self.log(f"  ASCII2D URL ERROR: {url} {e}")
            _fallback_elapsed = time.monotonic() - _fallback_started
            if _fallback_elapsed >= 5.0:
                self.log(f"  SLOW FALLBACK: Ascii2D path took {_fallback_elapsed:.1f}s for {img.name}")

        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        if self.settings.get("enable_saucenao") and not all_tags:
            _fallback_started = time.monotonic()
            if self.settings.get("_saucenao_retry_only", False):
                self.log(f"  SAUCENAO RETRY ONLY START: {img.name}")
            else:
                self.log(f"  SAUCENAO START AFTER IQDB/ASCII2D MISS: {img.name}")
            self.report_activity("SauceNAO", img, "Обратный поиск")
            try:
                sauce_urls = self.saucenao_urls(search_img)
            except Exception as e:
                self.log(f"  SAUCENAO SEARCH ERROR: {e}")
                sauce_urls = []

            for url, sim in sauce_urls:
                try:
                    self.log(f"  SAUCE MATCH: {sim:.2f}% {url}")
                    relay = _reverse_md5_relay("SauceNAO", url, sim)
                    if relay.get("kind") == "tags":
                        _accept_reverse_md5_relay("SauceNAO", relay)
                        sources.append(f"SauceNAO {sim:.2f}% relay-source {url}")
                        break
                    if relay.get("kind") == "source_only":
                        continue
                    tags = self.tags_from_url(url)
                    groups = self.groups_or_defer_background(url, tags)

                    if tags:
                        all_tags += tags
                        if groups and groups_to_tags(groups):
                            all_groups.append(groups)
                        sources.append(f"{sim:.2f}% {url}")
                        source_tag_groups.append({"url": url, "groups": groups or {"general": list(tags)}, "method": "saucenao"})
                        break

                except Exception as e:
                    self.log(f"  SAUCE URL ERROR: {url} {e}")
            _fallback_elapsed = time.monotonic() - _fallback_started
            if _fallback_elapsed >= 5.0:
                self.log(f"  SLOW FALLBACK: SauceNAO path took {_fallback_elapsed:.1f}s for {img.name}")


        all_tags = unique_keep_order(filter_numeric_tags(all_tags, self.settings.get("ignore_numeric_tags")))

        # STOP is a hard boundary for persistence: an in-flight HTTP response may
        # return after cancellation, but it must not write TAGGED/NO_MATCH state.
        if self.cancelled():
            self.log("  CANCELLED")
            return "skip"

        if self.settings.get("enable_tineye") and not all_tags\
                and not self.settings.get("_saucenao_retry_only", False)\
                and not self.settings.get("_skip_tineye_this_pass", False):
            _fallback_started = time.monotonic()
            # TinEye is a weak optional source-only fallback for the broken tail.
            # Its HTTP/browser failures must not turn the whole file into
            # retry_network, especially when SauceNAO has already provided a
            # usable source-only candidate.
            _network_events_before_tineye = list(getattr(self, "_transient_network_events", []) or [])
            _network_hosts_before_tineye = set(getattr(self, "_transient_network_hosts", set()) or set())
            self.report_activity("TinEye", img, "Обратный поиск")
            self.log("  TINEYE START")
            accepted = 0
            source_only_saved = 0
            try:
                for url, sim in self.tineye_urls(search_img):
                    try:
                        self.log(f"  TINEYE MATCH: {sim:.2f}% {url}")
                        relay = _reverse_md5_relay("TinEye", url, sim)
                        if relay.get("kind") == "tags":
                            _accept_reverse_md5_relay("TinEye", relay)
                            sources.append(f"TinEye {sim:.2f}% relay-source {url}")
                            accepted += 1
                            break
                        if relay.get("kind") == "source_only":
                            source_only_saved += 1
                            continue
                        tags, groups = self.reverse_url_tags_and_groups(url, method="tineye")
                        if tags:
                            before_count = len(set(all_tags))
                            all_tags += tags
                            if groups and groups_to_tags(groups):
                                all_groups.append(groups)
                            sources.append(f"TinEye {sim:.2f}% {url}")
                            source_tag_groups.append({"url": url, "groups": groups or {"general": list(tags)}, "method": "tineye"})
                            accepted += 1
                            merged_added = len(set(all_tags)) - before_count
                            self.log(f"  TINEYE TAGS: received={len(tags)} added_unique={max(merged_added, 0)}")
                            break
                        else:
                            if self._record_reverse_source_only("TinEye", url, sim):
                                source_only_saved += 1
                            else:
                                self.log(f"  TINEYE NO USABLE SOURCE: no metadata returned; {url}")
                    except Exception as e:
                        self.log(f"  TINEYE URL ERROR: {url} {e}")
                if all_tags and accepted:
                    self._tineye_tagged_total += 1
                    self.log(f"  TINEYE ACCEPTED: tag_sources={accepted} unique_tags={len(set(all_tags))}")
                if source_only_saved and not accepted:
                    self._tineye_source_only_total += 1
                    self.log(f"  TINEYE SOURCE-ONLY: candidates={source_only_saved}")
            finally:
                # Do not let TinEye timeout/HTTP/browser errors block saving the
                # existing SauceNAO source-only/no-match result.
                self._transient_network_events = _network_events_before_tineye
                self._transient_network_hosts = _network_hosts_before_tineye
            _fallback_elapsed = time.monotonic() - _fallback_started
            if _fallback_elapsed >= 5.0:
                self.log(f"  SLOW FALLBACK: TinEye path took {_fallback_elapsed:.1f}s for {img.name}")


        if self.cancelled():
            self.log("  CANCELLED BEFORE SAVE")
            return "skip"

        if all_tags:
            tag_groups = merge_tag_groups(all_groups)
            if not groups_to_tags(tag_groups):
                tag_groups["general"] = all_tags

            result_status = "partial" if self._partial_match_found else "tagged"
            source_text = "\n".join(sources)
            if self._partial_match_found:
                source_text = source_text + ("\n" if source_text else "") + f"PARTIAL: {self._partial_match_reason}"

            # Network lookups above intentionally run without the conveyor persist
            # lock.  Only the SQLite/output mutation below is serialized.
            with _persist_guard():
                # Write metadata directly into the managed archive output. Do not create
                # .tags.txt/.sources.txt beside originals anymore.
                archived_media = copy_result_files(self.settings, img, result_status)
                if not _valid_archived_media_path(archived_media):
                    self.log(f"  FOUND SAVE ERROR: managed copy missing for {img.name}; metadata not written")
                    return "retry_network"
                _import_result = save_found_metadata(self.settings, img, all_tags, source_text, tag_groups, status=result_status, archived_media_path=archived_media, hash_md5=real_md5 or None, source_tag_groups=source_tag_groups)
                if isinstance(_import_result, dict) and int(_import_result.get("source_added", 0) or 0):
                    self.log(f"  EXACT MD5 MERGE: existing_media={_import_result.get('canonical_path','')} source_added={int(_import_result.get('source_added',0))} no_physical_copy_created=1")
                _log_nomatch_promote_cleanup(self.log, _import_result)

                remove_nomatch(img, settings=self.settings)

                cleanup_archived_result(self.settings, img, ("nomatch",))
            if self._partial_match_found:
                self.log(f"  PARTIAL TAGS: {len(all_tags)}")
                return "partial"
            self.log(f"  TAGS: {len(all_tags)}")
            return "tagged"
        else:
            if self._saucenao_deferred and self.settings.get("enable_saucenao") and self.settings.get("saucenao_api_key"):
                retry_at = self.saucenao_retry_after_epoch()
                wait_for = max(0, retry_at - int(time.time()))
                self.log(
                    "  SAUCENAO DEFERRED: API cooldown active; file NOT sent to NO_MATCH; "
                    f"retry in {wait_for//60}m {wait_for%60}s"
                )
                return "retry_saucenao"
            if self.transient_network_failed():
                self.log(
                    "  NETWORK TEMPORARY FAILURE: lookup was incomplete "
                    f"({self.network_failure_summary()}); file deferred, NOT sent to NO_MATCH"
                )
                return "retry_network"
            source_only = None
            try:
                candidates = []
                candidates.extend(list(getattr(self, "_last_reverse_source_only", []) or []))
                candidates.extend(list(getattr(self, "_last_saucenao_source_only", []) or []))
                # Prefer the best real source-only hint across TinEye/SauceNAO.
                # Internal helper/proxy URLs are filtered before they enter this list.
                candidates.sort(key=lambda x: float(x.get("similarity", 0) or 0), reverse=True)
                source_only = candidates[0] if candidates else None
            except Exception:
                source_only = None
            visual_info = None
            with _persist_guard():
                # A side queue (rule34 image-key/Playwright, TinEye relay, etc.) can
                # promote the same original file while this slower reverse fallback is
                # still running.  Never let a late NO_MATCH write overwrite a FOUND row.
                try:
                    current_status = str(output_processed_status(self.settings, img) or "").lower()
                except Exception:
                    current_status = ""
                if current_status in ("found", "tagged", "partial"):
                    self.log(f"  NO_MATCH SKIP: already promoted to {current_status} by another queue")
                    return "skip"

                # NO_MATCH is durable SQLite state plus a disposable managed output copy.
                # Never delete FOUND/PARTIAL media here. A duplicate/replayed
                # no-match pass for the same basename used to remove a file that
                # had just been promoted from NO_MATCH to the gallery, leaving a
                # tagged DB row pointing at a missing file.
                archived_nomatch = copy_result_files(self.settings, img, "nomatch")
                try:
                    from core.visual_status import classify_nomatch_if_enabled
                    visual_info = classify_nomatch_if_enabled(archived_nomatch or img, self.settings)
                except Exception as _visual_exc:
                    visual_info = None
                    try:
                        self.log(f"  VISUAL STATUS ERROR: {type(_visual_exc).__name__}: {_visual_exc}")
                    except Exception:
                        pass
                visual_kwargs = {}
                if visual_info and not visual_info.get("error"):
                    visual_kwargs = {
                        "visual_status": str(visual_info.get("visual_status") or ""),
                        "visual_confidence": float(visual_info.get("visual_confidence") or 0.0),
                        "visual_model": str(visual_info.get("visual_model") or ""),
                        "visual_checked_at": int(visual_info.get("visual_checked_at") or 0),
                    }
                if source_only:
                    upsert_nomatch(
                        img,
                        reason="source_only",
                        settings=self.settings,
                        media_path=archived_nomatch,
                        source_url=str(source_only.get("url") or ""),
                        source_label=str(source_only.get("label") or "unsupported source"),
                        source_similarity=float(source_only.get("similarity", 0) or 0),
                        **visual_kwargs,
                    )
                else:
                    upsert_nomatch(img, settings=self.settings, media_path=archived_nomatch, **visual_kwargs)
            if visual_info:
                try:
                    if visual_info.get("error"):
                        self.log(f"  VISUAL STATUS ERROR: {visual_info.get('error')}")
                    else:
                        self.log(
                            "  VISUAL STATUS: "
                            f"{visual_info.get('visual_status','')} "
                            f"{float(visual_info.get('visual_confidence', 0) or 0) * 100:.0f}%"
                        )
                except Exception:
                    pass
            if source_only:
                self.log(
                    "  SOURCE-ONLY SAVED TO SQLITE: "
                    f"{float(source_only.get('similarity', 0) or 0):.2f}% "
                    f"{source_only.get('label','')} {source_only.get('url','')}"
                )
            else:
                self.log("  NO MATCH SAVED TO SQLITE")
            return "nomatch"


def extract_r34_urls_from_text(text):
    urls = []
    for m in re.finditer(r'https?://(?:www\.)?(rule34\.xxx|rule34\.us)[^\s<>"\']+', text, re.I):
        try:
            urls.append(m.group(0))
        except Exception:
            pass
    return list(dict.fromkeys(urls))



