"""Shared download utilities used by both Grabber and Subscriptions."""
from __future__ import annotations

import time
from pathlib import Path
from core.file_safety import atomic_write_bytes
from urllib.parse import urlparse
from core.net import http_request

try:
    from curl_cffi import requests
    _CURL = True
except ImportError:
    import requests
    _CURL = False

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/136.0.0.0 Safari/537.36")

def _safe(s: str) -> str:
    """Make a string one safe folder name — never «.», «..» or empty: those name the folder itself or the one above
    (a subscription «..» downloaded beside the other sites' folders, and «Удалить скачанные» took them all)."""
    import re as _re
    name = _re.sub(r'[<>:"/\\|?*]', '_', str(s))[:60]
    return name if name.strip(" .") else "_"


def _safe_file_name(url_name: str, fallback: str) -> str:
    """A file name from the last part of a media URL: unsafe characters replaced, the name cut to
    60 characters **with its extension kept**.  yande.re and konachan name files
    «yande.re 12345 tag tag … .png»: cutting the whole name lost the extension, and the type fix
    after the download turned every file into «yande.png» — the second one was «already there»."""
    import re as _re
    from urllib.parse import unquote
    name = _re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", unquote(str(url_name or ""))).strip(" .")
    stem, dot, ext = name.rpartition(".")
    if dot and stem and 1 <= len(ext) <= 5 and ext.isalnum():
        stem = stem[:60].rstrip(" .")
        return f"{stem}.{ext.lower()}" if stem else fallback
    return name[:60].rstrip(" .") or fallback


_CF_HOSTS = {
    "danbooru.donmai.us", "booru.allthefallen.moe",
    "rule34.xxx", "rule34.us",
}

# ── Standalone ATF PoW helpers (mirrored from tagger/engine.py) ───────────────

def _atf_is_pow_page(text: str) -> bool:
    head = (text or "")[:20000].lower()
    return (
        "booru.allthefallen.moe | verification" in head
        or "x-verification-challenge" in head
        or "powseed" in head
        or "challenge-checkbox" in head
    )


def _atf_extract_js_const(text, name):
    import re as _re
    pattern = r"""const\s+""" + _re.escape(name) + r"""\s*=\s*["']([^"']*)["']"""
    m = _re.search(pattern, text or "")
    return m.group(1) if m else ""


def _atf_solve_pow(seed, prefix, max_nonce=20_000_000):
    import hashlib
    seed = seed or ""
    prefix = prefix or ""
    for nonce in range(max_nonce):
        h = hashlib.sha1(f"{seed}:{nonce}".encode()).hexdigest()
        if h.startswith(prefix):
            return str(nonce), h
    return "", ""


def _atf_solve_challenge(session, url, host, html_text, log):
    import re as _re, time as _time
    from urllib.parse import urljoin as _urljoin
    try:
        cid     = _atf_extract_js_const(html_text, "challenge_id")
        cgen    = _atf_extract_js_const(html_text, "challenge_generated")
        cexp    = _atf_extract_js_const(html_text, "challenge_cookie_expires")
        seed    = _atf_extract_js_const(html_text, "powSeed")
        post_to = _atf_extract_js_const(html_text, "post_to") or host

        m = _re.search(r"""powPrefix\s*=\s*["']0["']\.repeat\((\d+)\)""", html_text or "")
        prefix_len = int(m.group(1)) if m else 5
        dm = _re.search(r"const\s+delay\s*=\s*(\d+)", html_text or "")
        delay = int(dm.group(1)) if dm else 5

        if not (cid and cgen and cexp and seed):
            log("  ATF PoW: missing challenge fields")
            return False

        log(f"  ATF PoW: solving prefix={prefix_len}")
        nonce, h = _atf_solve_pow(seed, "0" * prefix_len)
        if not nonce:
            log("  ATF PoW: unsolvable")
            return False

        verify_url = (f"https://{host}/" if post_to.strip().lower().replace("www.", "") == host
                      else _urljoin(url, post_to))
        _time.sleep(min(delay, 10) + 0.25)
        resp = _rate_limited_post(
            session, verify_url, settings=None,
            json={"challenge_id": cid, "challenge_generated": cgen,
                  "challenge_cookie_expires": cexp, "pow_nonce": nonce, "pow_hash": h},
            timeout=30,
            headers={"Accept": "*/*", "Content-Type": "application/json",
                     "X-Requested-With": "XMLHttpRequest",
                     "X-Verification-Challenge": "1",
                     "Referer": url, "Origin": f"https://{host}"},
        )
        log(f"  ATF PoW: POST status={resp.status_code} cookies={len(session.cookies)}")
        return resp.status_code == 200
    except Exception as e:
        log(f"  ATF PoW ERROR: {e}")
        return False


# Magic bytes → correct extension mapping
_MAGIC = [
    (b"RIFF", b"WEBP", ".webp"),
    (bytes([0xff, 0xd8, 0xff]), None, ".jpg"),
    (b"\x89PNG", None, ".png"),
    (b"GIF8", None, ".gif"),
    (bytes([0x1a, 0x45, 0xdf, 0xa3]), None, ".webm"),
]

def _correct_extension(data: bytes) -> str | None:
    """Return correct extension if file magic differs from filename, else None."""
    if len(data) < 12:
        return None
    for magic, sub, ext in _MAGIC:
        if data[:len(magic)] == magic:
            if sub:  # needs sub-check (WebP)
                if data[8:8+len(sub)] != sub:
                    continue
            return ext
    # mp4 has ftyp at offset 4
    if data[4:8] == b"ftyp":
        return ".mp4"
    return None


# Session cache: host → session, reused within a subscription run
_session_cache: dict[str, object] = {}


def clear_session_cache():
    """Call at the start of each subscription run to get fresh sessions."""
    _session_cache.clear()


def _get_or_create_session(host: str, settings: dict, log=None):
    """Return cached session for host, creating and warming it if needed."""
    if host in _session_cache:
        return _session_cache[host]
    s = _session(host, settings=settings, log=log)
    clean_host = str(host or "").lower().replace("www.", "")
    if clean_host in {"e621.net", "e926.net"}:
        cfg = ((settings or {}).get("sites") or {}).get(clean_host, {})
        login = str((cfg or {}).get("login") or "").strip()
        identity = f"by {login} on e621" if login else "local archive manager"
        s.headers.update({
            "User-Agent": f"LocalBooru/3.2 ({identity})",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        })
    elif clean_host in {"danbooru.donmai.us", "donmai.us"}:
        cfg = ((settings or {}).get("sites") or {}).get("danbooru.donmai.us", {})
        login = str((cfg or {}).get("login") or "").strip()
        identity = f"by {login} on Danbooru" if login else "local archive manager"
        s.headers.update({
            "User-Agent": f"LocalBooru/3.6 ({identity})",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        })
    elif "allthefallen" in clean_host:
        cfg = ((settings or {}).get("sites") or {}).get("booru.allthefallen.moe", {})
        login = str((cfg or {}).get("login") or "").strip()
        identity = f"by {login} on ATF" if login else "local archive manager"
        s.headers.update({
            "User-Agent": f"LocalBooru/3.6 ({identity})",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        })
    # Explicit browser handoff is domain-scoped and DPAPI-protected.  It is
    # applied after the normal site defaults so the real browser User-Agent and
    # session cookies win only for the selected domain.
    try:
        from core.domain_credentials import load_domain_credentials
        creds = load_domain_credentials(settings, clean_host)
        if not creds and clean_host.startswith("www."):
            creds = load_domain_credentials(settings, clean_host[4:])
        if creds:
            if creds.get("user_agent"):
                s.headers["User-Agent"] = str(creds["user_agent"])
            for cookie in creds.get("cookies") or []:
                if not isinstance(cookie, dict) or not cookie.get("name"):
                    continue
                kwargs = {"path": str(cookie.get("path") or "/")}
                if cookie.get("domain"):
                    kwargs["domain"] = str(cookie.get("domain"))
                s.cookies.set(str(cookie.get("name")), str(cookie.get("value") or ""), **kwargs)
    except Exception as exc:
        if log:
            log(f"Browser credential handoff warning [{clean_host}]: {type(exc).__name__}")
    _session_cache[host] = s
    return s


def _handle_visual_duplicate(settings: dict, dest: Path, file_url: str, log):
    """A just-downloaded file that looks like a card already in the library (pHash distance <= 3).

    Returns None when it is not a duplicate or the new file is better (keep it), else
    ``(False, path of the kept card)``: the new file goes to Trash and the caller merges the post's
    metadata into the kept card.  (The old inline code read ``sqlite3.Row.get`` — no such method —
    after the file had already been moved to Trash, so the vanished file was registered as a
    «ghost» card.)
    """
    try:
        similar = _find_similar_in_db(settings, dest, threshold=3)
    except Exception as exc:  # the similarity index is optional: without it the download is kept
        log(f"  VISUAL DUPLICATE CHECK SKIPPED: {type(exc).__name__}: {exc}")
        return None
    if similar is None:
        return None
    similar = dict(similar)
    kept = Path(str(similar.get("path") or ""))
    if not str(kept) or not kept.is_file():
        return None
    new_size = dest.stat().st_size
    old_size = int(similar.get("size_bytes") or 0)
    old_px = int(similar.get("width") or 0) * int(similar.get("height") or 0)
    try:
        from PIL import Image as _Img
        with _Img.open(dest) as _im:
            new_w, new_h = _im.size
    except Exception:  # an unreadable image (video, odd format): compare by size
        new_w = new_h = 0
    new_px = new_w * new_h
    new_wins = (new_px > old_px) if (new_px and old_px) else (new_size > old_size)
    if new_wins:
        return None
    from core.library_lifecycle import trash_media_paths, update_url_history
    trash_media_paths(settings, [dest], reason="subscription_visual_duplicate", make_backup=False)
    try:
        update_url_history(settings, file_url, status="duplicate", error="similar existing file kept")
    except Exception as exc:
        log(f"  url history: {exc}")
    log(f"  SKIP DUPE → Удалено (similar exists): {dest.name}")
    return False, kept


def _find_similar_in_db(settings: dict, file_path, threshold: int = 3):
    """Return best similar DB row using VP-tree first, bounded SQL fallback second.

    Old code scanned every image phash. That is acceptable at 1k files and awful
    at 50k+.  The VP-tree table is now the fast path; if it is empty/stale we
    rebuild lazily and still keep a small fallback for safety.
    """
    con = None
    try:
        from core.tagger import file_phash, phash_distance
        from core.database.connection import get_connection
        from core.vptree import VPTree

        ph = file_phash(file_path)
        if not ph:
            return None

        con = get_connection(settings)
        from core.derived_cache_db import open_selected_cache
        with open_selected_cache(
            settings, "vp_tree", main_connection=con, require_fresh=False, allow_main_fallback=True
        ) as (tree_con, _cache_info):
            tree = VPTree(tree_con)
            tree.ensure_fresh_enough()
            matches = tree.search(ph, max_distance=threshold)
        if matches:
            ids = [int(mid) for mid, _ in matches[:25]]
            phs = ','.join(['?'] * len(ids))
            rows = con.execute(
                f"SELECT id, path, size_bytes, width, height, hash_phash FROM images WHERE id IN ({phs})",
                ids,
            ).fetchall()
            by_id = {int(r['id']): r for r in rows}
            for mid, _dist in matches:
                row = by_id.get(int(mid))
                if row is None:
                    continue
                # External generations may be safely searched while stale as a
                # subset index, but a cached hash is never enough for a positive
                # match. Revalidate against the current main.db pHash.
                try:
                    if phash_distance(ph, str(row['hash_phash'] or '')) <= threshold:
                        return row
                except Exception:
                    continue

        # Bounded fallback: compare same leading nybble bucket first. It is not
        # mathematically complete, but avoids full-table scans when the tree is
        # temporarily unavailable and still catches most near-duplicates.
        prefix = ph[:1]
        rows = con.execute(
            "SELECT path, size_bytes, width, height, hash_phash FROM images "
            "WHERE deleted=0 AND hash_phash IS NOT NULL AND hash_phash != '' AND substr(hash_phash,1,1)=? "
            "LIMIT 5000",
            (prefix,),
        ).fetchall()
        best = None
        best_dist = threshold + 1
        for row in rows:
            try:
                d = phash_distance(ph, row['hash_phash'])
                if d <= threshold and d < best_dist:
                    best_dist = d
                    best = row
            except Exception:
                continue
        return best
    except Exception:
        return None
    finally:
        # get_connection() returns a thread-local pooled connection by default.
        # Closing pooled handles here defeats the pool on every phash comparison.
        # When users explicitly disable the pool, this compatibility connection
        # still has to be closed normally.
        if con is not None and not bool((settings or {}).get("sqlite_connection_pool", True)):
            try:
                con.close()
            except Exception:
                pass


def _rate_limited_get(session, url, settings=None, **kwargs):
    from core.http_rate_limiter import wait_for, apply_retry_after
    # Parser sessions come from core.tagger.get_session() (cookies, no global wrapper).
    # Plain fallback sessions still need rate limiting here.
    wrapped = bool(getattr(session, "_local_booru_global_limiter", False))
    if not wrapped:
        wait_for(url, settings or {})
    r = http_request(session, "GET", url, **kwargs)
    if not wrapped:
        apply_retry_after(r, settings or {})
    return r

def _rate_limited_post(session, url, settings=None, **kwargs):
    from core.http_rate_limiter import wait_for, apply_retry_after
    wrapped = bool(getattr(session, "_local_booru_global_limiter", False))
    if not wrapped:
        wait_for(url, settings or {})
    r = http_request(session, "POST", url, **kwargs)
    if not wrapped:
        apply_retry_after(r, settings or {})
    return r

def _smart_get(session, url, host, log, params=None, timeout=30, settings=None, headers=None, auth=None):
    """GET with ATF PoW handling. Returns response object."""
    r = _rate_limited_get(session, url, settings=settings, params=params, timeout=timeout, headers=headers, auth=auth)

    if "allthefallen" in host:
        # Empty body = likely PoW or auth wall
        body = getattr(r, "text", "") or ""
        if not body.strip() or _atf_is_pow_page(body):
            log("  ATF PoW page detected")
            if _atf_solve_challenge(session, url, host, body, log):
                r = _rate_limited_get(session, url, settings=settings, params=params, timeout=timeout, headers=headers, auth=auth)
            else:
                log("  ATF PoW: could not solve, proceeding anyway")

    return r



def _session(host: str, settings: dict | None = None, log=None):
    """Build a session with cookies from tagger — identical to parser session."""
    try:
        from core.tagger import get_session
        s = get_session(settings=settings or {}, log_func=log, target_host=host)
        return s
    except Exception as e:
        if log:
            log(f"  SESSION WARN: get_session failed ({e}), using plain session")

    # Hard fallback
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
    try:
        from core.network import install_safe_session
        install_safe_session(s, settings=settings or {}, log_func=log)
    except Exception:
        pass
    return s


def _auth_params(host: str, settings: dict | None = None) -> dict:
    """Return login/api_key/user_id params saved in Parser/Sites.

    Subscriptions must not keep separate credentials.  This resolver accepts the
    same settings structure used by the parser/site table, including aliases like
    api.rule34.xxx -> rule34.xxx and custom-site rows.
    """
    if not settings:
        return {}
    h = (host or "").lower().replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
    aliases = [h]
    if h == "api.rule34.xxx":
        aliases.append("rule34.xxx")
    if h == "rule34.xxx":
        aliases.append("api.rule34.xxx")
    if h == "danbooru.donmai.us":
        aliases.append("donmai.us")

    def _same_site(key: str, cfg: dict) -> bool:
        key = str(key or "").lower().replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
        if key in aliases:
            return True
        for field in ("domain", "host", "base_url", "url", "login_url"):
            val = str(cfg.get(field) or "").lower().replace("www.", "")
            if val and any(a in val for a in aliases):
                return True
        return False

    cfg = {}
    sites = settings.get("sites", {})
    if isinstance(sites, dict):
        # Normal flat form: {"rule34.xxx": {login/api_key/user_id...}}
        for a in aliases:
            v = sites.get(a)
            if isinstance(v, dict):
                cfg = v
                break
        # Defensive fallback for grouped forms copied from settings.py/UI.
        if not cfg:
            for key, v in sites.items():
                if isinstance(v, dict) and _same_site(key, v):
                    cfg = v
                    break
                if isinstance(v, dict):
                    for sub_key, sub_v in v.items():
                        if isinstance(sub_v, dict) and _same_site(sub_key, sub_v):
                            cfg = sub_v
                            break
                    if cfg:
                        break

    if not cfg:
        for item in settings.get("custom_sites", []) or []:
            if not isinstance(item, dict):
                continue
            if _same_site(item.get("domain") or item.get("base_url") or item.get("url") or "", item):
                cfg = item
                break

    params = {}
    for key in ("login", "api_key", "user_id"):
        val = str(cfg.get(key, "") or "").strip() if isinstance(cfg, dict) else ""
        if val:
            params[key] = val
    return params


def _auth_status(params: dict) -> str:
    """Safe auth summary for logs: never prints secrets."""
    return " ".join(f"{k}={'yes' if params.get(k) else 'no'}" for k in ("login", "api_key", "user_id"))


def _basic_auth_for_host(host: str, settings: dict | None = None):
    params = _auth_params(host, settings)
    login = str(params.get("login") or "").strip()
    api_key = str(params.get("api_key") or "").strip()
    if login and api_key and (_host(host) in {"e621.net", "e926.net", "danbooru.donmai.us", "donmai.us"} or "allthefallen" in _host(host)):
        return (login, api_key)
    return None


def _api_headers_for_host(host: str, settings: dict | None = None):
    clean = _host(host)
    params = _auth_params(clean, settings)
    login = str(params.get("login") or "").strip()
    if clean in {"e621.net", "e926.net"}:
        identity = f"by {login} on e621" if login else "local archive manager"
        return {"User-Agent": f"LocalBooru/3.6 ({identity})", "Accept": "application/json"}
    if clean in {"danbooru.donmai.us", "donmai.us"}:
        identity = f"by {login} on Danbooru" if login else "local archive manager"
        return {"User-Agent": f"LocalBooru/3.6 ({identity})", "Accept": "application/json"}
    if "allthefallen" in clean:
        identity = f"by {login} on ATF" if login else "local archive manager"
        return {"User-Agent": f"LocalBooru/3.6 ({identity})", "Accept": "application/json"}
    return None


def _query_auth_params_for_host(host: str, settings: dict | None = None):
    clean = _host(host)
    params = _auth_params(clean, settings)
    if _basic_auth_for_host(clean, settings) is not None:
        return {k: v for k, v in params.items() if k not in {"login", "api_key"}}
    return params


def _host(site: str) -> str:
    return (site or "").lower().replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]


def _posts_api_url(site: str, query: str, page: int, limit: int = 100) -> tuple[str, dict]:
    """Return (api_url, params) for tag search.

    page is zero-based inside subscriptions. Site-specific APIs are normalized here.
    """
    host = _host(site)

    # rule34.xxx currently behaves better through api.rule34.xxx for JSON DAPI.
    if host == "rule34.xxx" or host == "api.rule34.xxx":
        return "https://api.rule34.xxx/index.php", {
            "page": "dapi", "s": "post", "q": "index",
            "json": "1", "tags": query, "limit": limit, "pid": page,
        }

    # Gelbooru/DAPI style. pid is zero-based.
    if any(h in host for h in ["gelbooru", "xbooru", "hypnohub", "realbooru", "tbib", "safebooru"]):
        return f"https://{host}/index.php", {
            "page": "dapi", "s": "post", "q": "index",
            "json": "1", "tags": query, "limit": limit, "pid": page,
        }

    # rule34.us exposes website search, but no confirmed JSON/API endpoint.
    # Subscriptions require structured pagination, so fail closed instead of
    # sending invented DAPI calls and silently missing/mixing posts.
    if host == "rule34.us":
        return "", {}

    # Danbooru 2.x style. page is one-based.
    if any(h in host for h in ["danbooru", "donmai", "allthefallen", "lolibooru", "aibooru"]):
        return f"https://{host}/posts.json", {
            "tags": query, "limit": limit, "page": page + 1,
        }

    # Moebooru style.
    if any(h in host for h in ["konachan", "yande.re"]):
        return f"https://{host}/post/index.json", {
            "tags": query, "limit": limit, "page": page + 1,
        }

    # e621/e926 style.
    if "e621" in host or "e926" in host:
        return f"https://{host}/posts.json", {
            "tags": query, "limit": limit, "page": page + 1,
        }

    return f"https://{host}/posts.json", {"tags": query, "limit": limit, "page": page + 1}



def _query_with_id_bound(query: str, mode: str, cursor_id: int) -> str:
    """Return a site-neutral tag query with an id boundary when the API supports it.

    Booru page numbers shift when new posts arrive.  id:<N / id:>N is much
    more stable and is the closest lightweight version of Hydrus gallery
    continuation without importing its whole parser stack.
    """
    q = (query or "").strip()
    if cursor_id <= 0:
        return q
    if mode == "new":
        return (q + f" id:>{int(cursor_id)}").strip()
    # all/old/deep scans page from newest to oldest using id:<cursor after page 0
    return (q + f" id:<{int(cursor_id)}").strip()


def _posts_api_url_bound(site: str, query: str, page: int, limit: int = 100, cursor_id: int = 0, run_mode: str = "all") -> tuple[str, dict]:
    host = _host(site)

    # Gelbooru/Rule34 DAPI pagination is page-number based.  Do not mix
    # `id:<...` tag bounds with `pid`, because rule34.xxx can silently return
    # incomplete pages.  Cursor-style pages stay enabled for Danbooru/e621 only.
    if host in {"rule34.xxx", "api.rule34.xxx"} or any(h in host for h in ["gelbooru", "xbooru", "hypnohub", "realbooru", "tbib", "safebooru"]):
        return _posts_api_url(site, query, page, limit=limit)

    bounded_query = _query_with_id_bound(query, "new" if run_mode == "new" else "old", cursor_id)
    # Danbooru/e621 support stable cursor pages b<ID>/a<ID>.
    if cursor_id > 0 and any(h in host for h in ["danbooru", "donmai", "allthefallen", "lolibooru", "aibooru", "e621", "e926"]):
        api_url, params = _posts_api_url(site, query, 0, limit=limit)
        params["page"] = ("a" if run_mode == "new" else "b") + str(int(cursor_id))
        return api_url, params
    api_url, params = _posts_api_url(site, bounded_query, page, limit=limit)
    return api_url, params

def _extract_posts(data) -> list[dict]:
    """Normalize API response to a list of raw post dicts."""
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    if isinstance(data, dict):
        for key in ("post", "posts", "data"):
            v = data.get(key)
            if isinstance(v, list):
                return [p for p in v if isinstance(p, dict)]
            if isinstance(v, dict):
                # Gelbooru can return {'post': {'@attributes': ...}} for no posts;
                # treat dicts without id/file fields as empty.
                if any(k in v for k in ("id", "file_url", "md5", "file")):
                    return [v]
        if any(k in data for k in ("id", "file_url", "md5", "file")):
            return [data]
    return []


def _post_tags(post: dict) -> set[str]:
    """Return set of lowercase tag strings from a post dict (any booru format)."""
    tags: set[str] = set()
    for key in (
        "tag_string", "tags", "tag_string_general", "tag_string_artist",
        "tag_string_character", "tag_string_copyright", "tag_string_meta",
    ):
        raw = post.get(key, "")
        if isinstance(raw, str):
            tags.update(t.strip().lower() for t in raw.split() if t.strip())
        elif isinstance(raw, list):
            tags.update(str(t).strip().lower() for t in raw if t)
        elif isinstance(raw, dict):
            for v in raw.values():
                if isinstance(v, list):
                    tags.update(str(t).strip().lower() for t in v if t)
    return tags


def _post_md5(post: dict) -> str:
    """Extract MD5 hash from a post dict (any booru format)."""
    for key in ("md5", "hash", "file_md5"):
        v = post.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    f = post.get("file", {})
    if isinstance(f, dict):
        v = f.get("md5", "")
        if v:
            return str(v).strip().lower()
    return ""


def _post_id(post: dict) -> int:
    try:
        return int(post.get("id") or post.get("post_id") or 0)
    except Exception:
        return 0


def _absolute_url(url: str, site: str) -> str:
    if not url:
        return ""
    url = str(url).strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return f"https://{_host(site)}" + url
    return url



def _post_referrer(site: str, post: dict) -> str:
    """Return a realistic post-page referer for file downloads.

    Some booru/CDN setups are picky about hotlink protection.  A root-domain
    Referer usually works, but using the actual post page is closer to browsers
    and imgbrd-grabber behavior.
    """
    host = _host(site)
    pid = _post_id(post)
    if not host:
        return ""
    if not pid:
        return f"https://{host}/"
    if host in {"rule34.xxx", "rule34.us", "xbooru.com", "hypnohub.net"} or any(h in host for h in ["gelbooru", "realbooru", "tbib", "safebooru"]):
        return f"https://{host}/index.php?page=post&s=view&id={pid}"
    return f"https://{host}/posts/{pid}"


def _media_url_groups(post: dict, site: str = "") -> tuple[list[str], list[str], list[str]]:
    """(originals, reduced copies, page candidates) of a post's media URLs.

    Originals — the file the site stores (``file_url``, e621 ``file.url``, Danbooru's ``original``
    media asset).  Reduced — ``large_file_url``/sample/jpeg/preview: other bytes, a smaller copy.
    Page candidates — URLs the browser saw on the post page (the extension).  Never the ``source``
    field: that often points to Pixiv/Twitter/etc. instead of a direct file.
    """
    seen: set[str] = set()
    groups: tuple[list[str], list[str], list[str]] = ([], [], [])

    def add(group: int, v):
        if not (isinstance(v, str) and v.strip()):
            return
        if v.strip().startswith("/") and not v.strip().startswith("//") and not _host(site):
            return                      # a site-relative path without the site: no URL
        u = _absolute_url(v, site)
        if not u or u in seen:
            return
        try:
            from core.network import is_permanently_disabled_url
            if is_permanently_disabled_url(u):
                return
        except Exception:
            pass
        seen.add(u)
        groups[group].append(u)

    for key in ("file_url", "source_file_url", "original_url"):
        add(0, post.get(key))
    f = post.get("file", {})
    if isinstance(f, dict):
        add(0, f.get("url"))
        add(0, f.get("ext_url"))
    media = post.get("media_asset")
    variants = media.get("variants") if isinstance(media, dict) else None
    for v in variants if isinstance(variants, list) else []:
        if isinstance(v, dict) and v.get("type") == "original":
            add(0, v.get("url"))
    add(1, post.get("large_file_url"))
    sample = post.get("sample", {})
    if isinstance(sample, dict):
        add(1, sample.get("url"))
    for key in ("sample_url", "sample_file_url", "jpeg_url"):
        add(1, post.get(key))
    preview = post.get("preview", {})
    if isinstance(preview, dict):
        add(1, preview.get("url"))
    for key in ("preview_file_url", "preview_url"):
        add(1, post.get(key))
    # Browser candidates are fallback-only.  An official post API original
    # (including e621's nested file.url) must always stay ahead of page links.
    for key in ("browser_media_urls", "media_urls", "original_urls"):
        values = post.get(key) or []
        if isinstance(values, str):
            values = [values]
        for value in values:
            add(2, value)
    return groups


def original_media_urls(post: dict, site: str = "") -> list[str]:
    """URLs of the file the site stores for the post — never a sample, jpeg or preview copy."""
    return _media_url_groups(post if isinstance(post, dict) else {}, site)[0]


def _file_url_variants(post: dict, site: str = "") -> list[str]:
    """Return media URL fallbacks in quality order: originals, then sample/jpeg/preview, then
    the URLs the browser saw on the page."""
    originals, reduced, page = _media_url_groups(post, site)
    return originals + reduced + page


def _file_url(post: dict, site: str = "") -> str:
    variants = _file_url_variants(post, site)
    return variants[0] if variants else ""


def _valid_media_bytes(data: bytes, content_type: str = "") -> tuple[bool, str | None]:
    if not data or len(data) < 64:
        return False, None
    head = data[:256].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html") or b"<html" in head[:80]:
        return False, None
    ct = (content_type or "").lower()
    if "text/html" in ct or (ct.startswith("text/") and "svg" not in ct):
        return False, None
    ext = _correct_extension(data)
    if ext:
        return True, ext
    # Some servers send octet-stream with unusual images; allow only if CT is media-like.
    if ct.startswith("image/") or ct.startswith("video/"):
        return True, None
    return False, None



def download_media_to_staging(
    site: str,
    urls: list[str] | tuple[str, ...],
    settings: dict,
    destination,
    *,
    referrer: str = "",
    expected_md5: str = "",
    log=None,
    stop_check=None,
) -> dict:
    """Download one recorded media candidate into a disposable staging path.

    This is the low-level recovery/download primitive.  It deliberately skips
    library dedupe and parser persistence, streams bytes instead of buffering a
    whole video in RAM, validates media magic, respects the shared HTTP/rate
    limit stack, and only reports success when ``expected_md5`` matches.
    """
    import hashlib
    from itertools import chain
    from pathlib import Path

    from core.file_safety import atomic_write_chunks
    from core.network import is_permanently_disabled_url
    from core.preflight import ensure_space_for_write

    log = log or (lambda _m: None)
    settings = dict(settings or {})
    dest = Path(destination)
    expected = str(expected_md5 or "").strip().lower()
    max_bytes = max(1, int(float(settings.get("recovery_max_download_mb", 4096) or 4096))) * 1024 * 1024
    candidates: list[str] = []
    for value in urls or []:
        url = str(value or "").strip()
        if not url or url in candidates or is_permanently_disabled_url(url):
            continue
        candidates.append(url)
    if not candidates:
        return {"ok": False, "status": "no_source", "error": "no_allowed_media_url"}
    try:
        from core.download_policy import require_allowed
        require_allowed(
            settings, operation="staged_media_download", source_site=str(site or ""),
            media_url=str(candidates[0]), post_url=str(referrer or ""),
            payload={"destination": str(dest), "expected_md5": expected},
            create_intent=bool(settings.get("download_policy_create_intents", True)),
        )
    except Exception as exc:
        try:
            from core.download_policy import DownloadBlockedByPolicy
            if isinstance(exc, DownloadBlockedByPolicy):
                return {"ok": False, "status": "blocked_by_policy", "error": str(exc), "intent_id": int(exc.decision.intent_id or 0)}
        except Exception:
            pass
        raise

    last_error = ""
    for url in candidates:
        if stop_check and stop_check():
            raise InterruptedError("Recovery download cancelled")
        host = _host(site or url)
        session = _get_or_create_session(host, settings, log=log)
        headers = {
            "Referer": str(referrer or f"https://{host}/"),
            "Accept": "image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8",
        }
        response = None
        try:
            response = _rate_limited_get(
                session,
                url,
                settings=settings,
                timeout=90,
                headers=headers,
                allow_redirects=True,
                stream=True,
            )
            if response.status_code in (401, 403):
                last_error = f"HTTP {response.status_code}: auth/cookie wall"
                continue
            if response.status_code in (404, 410):
                last_error = f"HTTP {response.status_code}: media missing"
                continue
            response.raise_for_status()
            content_type = str(response.headers.get("Content-Type") or "")
            try:
                content_length = int(response.headers.get("Content-Length") or 0)
            except Exception:
                content_length = 0
            if content_length > max_bytes:
                last_error = f"download too large: {content_length} > {max_bytes}"
                continue
            space_ok, space_error = ensure_space_for_write(settings, dest, content_length)
            if not space_ok:
                return {"ok": False, "status": "failed", "error": space_error, "source_url": url}

            iterator = response.iter_content(chunk_size=1024 * 1024)
            prefix_parts: list[bytes] = []
            prefix_size = 0
            while prefix_size < 4096:
                try:
                    part = next(iterator)
                except StopIteration:
                    break
                if part:
                    prefix_parts.append(bytes(part))
                    prefix_size += len(part)
            prefix = b"".join(prefix_parts)
            media_ok, _extension = _valid_media_bytes(prefix, content_type)
            if not media_ok:
                last_error = f"non-media response ({len(prefix)} bytes, {content_type or '?'})"
                continue

            digest = hashlib.md5()
            total = 0

            def chunks():
                nonlocal total
                for chunk in chain(prefix_parts, iterator):
                    if stop_check and stop_check():
                        raise InterruptedError("Recovery download cancelled")
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"download exceeded limit {max_bytes}")
                    digest.update(chunk)
                    yield chunk

            atomic_write_chunks(dest, chunks(), should_stop=stop_check)
            observed = digest.hexdigest().lower()
            if expected and observed != expected:
                try:
                    dest.unlink(missing_ok=True)
                except Exception:
                    pass
                last_error = f"downloaded MD5 mismatch: expected={expected} observed={observed}"
                continue
            return {
                "ok": True,
                "status": "downloaded",
                "path": str(dest),
                "source_url": url,
                "bytes": int(total),
                "observed_md5": observed,
                "content_type": content_type,
            }
        except InterruptedError:
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
    return {"ok": False, "status": "failed", "error": last_error or "all_media_candidates_failed"}

def fetch_posts_for_query(
    site: str,
    query: str,
    settings: dict,
    since_post_id: int = 0,
    max_pages: int = 3,
    blacklist_tags: list | None = None,
    run_mode: str = "all",
    log=None,
    status: dict | None = None,
    max_posts: int = 0,
) -> tuple[list[dict], int]:
    """Fetch post metadata without downloading. Returns (posts, highest_id_seen).

    ``status`` (optional) receives ``pages_ok`` — pages the site answered — and ``error`` — why it
    stopped answering — so «the site is down» is not mistaken for «no new posts».
    ``max_posts`` (> 0): stop after that many posts (the newest; «Скачать тег» with a limit).

    run_mode:
      all — candidates from scanned pages, dedup is handled later.
      new — only posts newer than since_post_id. If since_post_id is 0, this is
            baseline mode: return no posts and only save newest id.
      old — posts older than since_post_id; if since_post_id is 0, behaves like all.
    """
    log = log or (lambda m: None)
    host = _host(site)
    s = _get_or_create_session(host, settings, log=log)
    auth = _query_auth_params_for_host(host, settings)
    api_host_for_auth = "api.rule34.xxx" if host == "rule34.xxx" else host
    if api_host_for_auth not in {host}:
        auth.update(_query_auth_params_for_host(api_host_for_auth, settings))
    request_auth = _basic_auth_for_host(host, settings)
    request_headers = _api_headers_for_host(host, settings)
    if host in {"rule34.xxx", "api.rule34.xxx", "gelbooru.com", "e621.net", "e926.net", "danbooru.donmai.us", "booru.allthefallen.moe"}:
        log(f"  AUTH [{site}]: {_auth_status(auth)}")
    if host in {"rule34.xxx", "api.rule34.xxx"} and not (auth.get("api_key") and auth.get("user_id")):
        log("  RULE34 WARNING: api_key/user_id не найдены в таблице сайтов; выдача API может быть урезана")

    # Blacklist is applied after MD5 grouping in subscriptions so a file cannot
    # slip through from another site whose tag list is incomplete. Keep this
    # argument only for backwards compatibility with older callers.
    bl_set: set[str] = set()
    posts: list[dict] = []
    highest_id = since_post_id
    stop = False
    e621_null_file_urls = 0

    cursor_id = int(since_post_id or 0)
    seen_ids: set[int] = set()
    # rule34.xxx officially allows up to 1000 posts per API page.  Using that
    # limit is both faster for large subscriptions and kinder to its 60 RPM API.
    page_limit = 1000 if host in {"rule34.xxx", "api.rule34.xxx"} else 100
    max_posts = max(0, int(max_posts or 0))
    if max_posts:
        page_limit = min(page_limit, max_posts)
    status = status if status is not None else {}
    status.setdefault("pages_ok", 0)
    for page in range(max_pages):
        if stop:
            break
        # Prefer id-bound pagination after the first page/checkpoint; page numbers
        # shift and often stop around shallow limits on DAPI sites.
        api_url, base_params = _posts_api_url_bound(site, query, page, limit=page_limit, cursor_id=cursor_id if (page > 0 or run_mode in {"new", "old"}) else 0, run_mode=run_mode)
        if not api_url:
            log(f"  FETCH [{site}]: no verified API endpoint; automatic subscription/tag scan disabled")
            status["error"] = "no verified API endpoint"
            break
        params = {**base_params, **auth}
        try:
            r = _smart_get(s, api_url, host, log, params=params, timeout=45, settings=settings, headers=request_headers, auth=request_auth)
            if r.status_code in (401, 403):
                log(f"  FETCH [{site}]: {r.status_code} — нужны cookies/API из парсера")
                status["error"] = f"HTTP {r.status_code}"
                break
            r.raise_for_status()
            body = getattr(r, "text", "") or ""
            if not body.strip():
                log(f"  FETCH [{site}]: пустой ответ")
                status["error"] = "empty answer"
                break
            try:
                data = r.json()
            except Exception:
                log(f"  FETCH [{site}]: HTML/не JSON вместо API-ответа")
                status["error"] = "not JSON"
                break
            raw_posts = _extract_posts(data)
        except Exception as e:
            log(f"  FETCH PAGE ERROR [{site}]: {e}")
            status["error"] = f"{type(e).__name__}: {e}"
            break
        status["pages_ok"] += 1

        if not raw_posts:
            log(f"  FETCH [{site}] page={page}: 0 постов (status={r.status_code}, size={len(body)})")
            break

        log(f"  FETCH [{site}] page={page}: {len(raw_posts)} постов (status={r.status_code})")
        # APIs normally return newest→oldest. Keep that stable here.
        raw_posts.sort(key=_post_id, reverse=True)
        page_ids = [_post_id(p) for p in raw_posts if _post_id(p) > 0]
        if page_ids:
            # next page continues before the smallest id we just saw
            cursor_id = min(page_ids)

        for post in raw_posts:
            pid = _post_id(post)
            if pid and pid in seen_ids:
                continue
            if pid:
                seen_ids.add(pid)
            if pid > highest_id:
                highest_id = pid

            # First run of new-only: just set baseline, don't download history.
            if run_mode == "new" and since_post_id <= 0:
                continue

            if run_mode == "new" and since_post_id > 0 and pid <= since_post_id:
                stop = True
                break
            if run_mode == "old" and since_post_id > 0 and pid >= since_post_id:
                continue

            # Do not filter by blacklist or by existing MD5 during scanning.
            # The import planner must first combine all same-file candidates so
            # merged tags/sources are preserved and blacklist sees every site.
            # Posts without API file_url are also kept for HTML fallback in the runner.
            if "e621" in host or "e926" in host:
                file_block = post.get("file", {})
                if isinstance(file_block, dict) and file_block.get("url") is None and not _file_url(post, site):
                    e621_null_file_urls += 1
            posts.append(post)
            if max_posts and len(posts) >= max_posts:
                stop = True
                break

        time.sleep(0.25)

    if e621_null_file_urls:
        log(
            f"  NOTICE [{site}]: {e621_null_file_urls} post(s) have file.url=null "
            "(OACBLOCK or auth required); media download is unavailable for them"
        )
    return posts, highest_id


def download_post_file(
    site: str,
    post: dict,
    settings: dict,
    query: str = "",
    session_folder: str = "",
    log=None,
    operation: str = "post_download",
    allow_reduced: bool = False,
) -> tuple[bool, object]:
    """Download one post file safely. Returns (success, dest_path|None).

    Only the original is taken unless ``allow_reduced``: a sample or preview saved under the
    post's MD5 would stand in for the original for good («уже есть», never downloaded again).
    The browser import allows them (the user pointed at that picture; a better copy found later
    replaces it through the quality upgrade)."""
    log = log or (lambda m: None)
    host = _host(site)
    s = _get_or_create_session(host, settings, log=log)

    from pathlib import Path
    originals, reduced, page_urls = _media_url_groups(post, site)
    file_urls = originals + (reduced if allow_reduced else []) + page_urls
    if not file_urls:
        if reduced:
            log(f"  DL SKIP [{site}]: post {_post_id(post)} has no original file URL (only sample/preview); not saved")
        return False, None
    # Authoritative process-wide gate. All current Browser Import and
    # Subscription paths pass through this function; future feature lanes must
    # do the same or use download_media_to_staging(), which enforces the same policy.
    from core.download_policy import require_allowed
    require_allowed(
        settings,
        operation=str(operation or query or "post_download"),
        source_site=str(site or host),
        media_url=str(file_urls[0]),
        post_url=_post_referrer(site, post),
        payload={"post_id": _post_id(post), "query": str(query or "")},
        create_intent=bool((settings or {}).get("download_policy_create_intents", True)),
    )
    def _history(url: str, status: str, error: str = ""):
        try:
            from core.library_lifecycle import update_url_history
            update_url_history(settings, url, status=status, error=error)
        except Exception:
            pass

    from datetime import datetime as _dt
    _ts = session_folder or _dt.now().strftime("%Y-%m-%d_%H-%M")
    from core.paths import result_output_base
    out_dir = (
        result_output_base(settings)
        / "subscriptions"
        / _safe(site)
        / _safe(query or "misc")
        / _ts
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    pid = _post_id(post)

    post_hash = _post_md5(post)
    if post_hash:
        try:
            from core.deleted_registry import has_deleted_md5
            if has_deleted_md5(post_hash, settings=settings):
                policy = str((settings or {}).get("deleted_reimport_policy", "skip") or "skip").lower()
                if policy != "return_inbox":
                    log(f"  SKIP DELETED [{site}]: exact MD5 was permanently removed earlier")
                    for _u in file_urls: _history(_u, "skipped_deleted", "exact MD5 previously deleted")
                    return False, None
        except Exception:
            pass
        try:
            from core.database.storage import md5_exists as _md5_exists
            if _md5_exists(settings, post_hash):
                for _u in file_urls: _history(_u, "duplicate", "exact MD5 already in library")
                return False, None
        except Exception:
            pass

    last_error = ""
    try:
        headers = {"Referer": _post_referrer(site, post) or f"https://{host}/", "Accept": "image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8"}
        dest = None
        fname = ""
        raw = b""
        correct_ext = None
        for idx, file_url in enumerate(file_urls):
            url_name = Path(urlparse(file_url).path).name
            fname = _safe_file_name(url_name, f"{post_hash or pid}.bin")
            dest = out_dir / fname

            if dest.exists():
                _history(file_url, "duplicate", "already on disk this session")
                return False, dest  # already on disk this session
            parent = dest.parent.parent
            if parent.exists():
                for ts_dir in parent.iterdir():
                    if ts_dir.is_dir():
                        existing = ts_dir / fname
                        if existing.exists():
                            _history(file_url, "duplicate", "already on disk in another session")
                            return False, existing  # exists in another session

            fr = _rate_limited_get(s, file_url, settings=settings, timeout=90, headers=headers, allow_redirects=True)
            final_host = _host(getattr(fr, "url", file_url))
            if final_host and final_host != _host(file_url) and final_host == host:
                # suspicious but not fatal; magic-bytes decides below
                pass
            if "allthefallen" in host:
                body = getattr(fr, "text", "") if not getattr(fr, "content", b"")[:16] else ""
                if _atf_is_pow_page(body):
                    _atf_solve_challenge(s, f"https://{host}/", host, body, log)
                    fr = _rate_limited_get(s, file_url, settings=settings, timeout=90, headers=headers, allow_redirects=True)
            if fr.status_code in (401, 403):
                last_error = f"{fr.status_code} auth/cookie wall"
                _history(file_url, "auth_required", last_error)
                log(f"  DL FALLBACK [{site}]: {last_error} {fname}")
                continue
            if fr.status_code in (404, 410):
                last_error = f"{fr.status_code} missing file"
                _history(file_url, "missing", last_error)
                log(f"  DL FALLBACK [{site}]: {last_error} {fname}")
                continue
            fr.raise_for_status()
            raw = fr.content or b""
            ok, correct_ext = _valid_media_bytes(raw, fr.headers.get("Content-Type") or "")
            if not ok:
                last_error = f"non-media response ({len(raw)} bytes, {fr.headers.get('Content-Type','?')})"
                _history(file_url, "invalid_media", last_error)
                log(f"  DL FALLBACK [{site}]: {last_error} {fname}")
                continue
            break
        else:
            log(f"  DL SKIP [{site}]: all media fallbacks failed: {last_error}")
            return False, None

        assert dest is not None
        if correct_ext and dest.suffix.lower() != correct_ext:
            dest = dest.with_suffix(correct_ext)
            fname = dest.name
            if dest.exists():
                return False, None

        from core.preflight import ensure_space_for_write
        _space_ok, _space_msg = ensure_space_for_write(settings, dest, len(raw))
        if not _space_ok:
            log(f"  DL STOP [{site}]: {_space_msg}")
            try:
                from core.library_lifecycle import update_url_history
                update_url_history(settings, file_url, status="failed_disk_space", error=_space_msg)
            except Exception:
                pass
            return False, None
        atomic_write_bytes(dest, raw)
        log(f"  DL [{site}]: {fname}")

        duplicate = _handle_visual_duplicate(settings, dest, file_url, log)
        if duplicate is not None:
            return duplicate

        try:
            from core.library_lifecycle import update_url_history
            update_url_history(settings, file_url, status="downloaded")
        except Exception:
            pass
        time.sleep(0.2)
        return True, dest
    except Exception as e:
        log(f"  DL ERROR [{site}] {fname}: {e}")
        try:
            from core.library_lifecycle import update_url_history
            if 'file_url' in locals() and file_url:
                update_url_history(settings, file_url, status="failed_temp", error=str(e))
        except Exception:
            pass
        return False, None
