from __future__ import annotations

import json
import hashlib
from copy import copy
from http.cookiejar import Cookie, MozillaCookieJar

from core.paths import BROWSER_COOKIES_DIR


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
    return _load_cookie_bundle_path(path)


def _load_cookie_bundle_path(path):
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return _normalize_cookie_records(data), data.get("user_agent")
        return _normalize_cookie_records(data), None
    except Exception:
        return [], None


def _cookie_scope(host: str, domain, host_only: bool, source_host: str) -> tuple[str, bool] | None:
    raw = str(domain or "").strip().lower()
    clean = raw.lstrip(".") or source_host
    if any(ch in clean for ch in "/\\:@ \t\r\n"):
        return None
    if host_only:
        # Companion used to store a www page's cookies under its root hostname.
        # Keep that explicit www cookie for a root -> www redirect, unchanged.
        allowed = clean == host or (host == source_host and clean == "www." + source_host)
        return (clean, False) if allowed else None
    # Only the selected host or a declared parent domain can supply its cookies.
    # Never attach a cookie exported for another host (including a child host).
    if clean != host and not ("." in clean and host.endswith("." + clean)) \
            and not (host == source_host and clean == "www." + source_host):
        return None
    return ("." + clean if raw.startswith(".") else clean, True)


def _cookie_pair(name, value) -> tuple[str, str] | None:
    if not name or value is None:
        return None
    name, value = str(name), str(value)
    try:
        name.encode("latin-1")
        value.encode("latin-1")
    except UnicodeEncodeError:
        return None
    if any(ch in name for ch in "=; \t\r\n") or any(ch in value for ch in "\r\n"):
        return None
    return name, value


def _record_cookie(host: str, record: dict, source_host: str) -> Cookie | None:
    pair = _cookie_pair(record.get("name"), record.get("value"))
    raw_domain = str(record.get("domain") or "")
    host_only = bool(record.get("hostOnly", not raw_domain.startswith(".")))
    scope = _cookie_scope(host, raw_domain, host_only or not raw_domain, source_host)
    if pair is None or scope is None:
        return None
    raw_expiry = record.get("expirationDate", record.get("expires"))
    try:
        expires = int(float(raw_expiry)) if raw_expiry not in (None, "") else None
    except (TypeError, ValueError, OverflowError):
        return None
    path = str(record.get("path") or "/")
    if not path.startswith("/") or any(ch in path for ch in "\r\n"):
        return None
    rest = {}
    if record.get("httpOnly"):
        rest["HttpOnly"] = None
    if record.get("sameSite"):
        rest["SameSite"] = str(record["sameSite"])
    domain, specified = scope
    return Cookie(0, pair[0], pair[1], None, False, domain, specified, domain.startswith("."),
                  path, bool(record.get("path")), bool(record.get("secure")), expires,
                  expires is None, None, None, rest)


def _scoped_cookie(host: str, cookie: Cookie, source_host: str) -> Cookie | None:
    scope = _cookie_scope(host, cookie.domain, not cookie.domain_specified or not cookie.domain, source_host)
    if scope is None or _cookie_pair(cookie.name, cookie.value) is None:
        return None
    out = copy(cookie)
    out.domain, out.domain_specified = scope
    out.domain_initial_dot = out.domain.startswith(".")
    if out.expires == 0:  # Netscape exports use zero for cookies that last for the browser session.
        out.expires, out.discard = None, True
    return out


def _bundle_cookies(host: str, records, user_agent, source_host: str) -> tuple[list[Cookie], str | None]:
    cookies = [cookie for record in _normalize_cookie_records(records)
               if (cookie := _record_cookie(host, record, source_host)) is not None]
    agent = str(user_agent or "").strip()
    return cookies, agent if agent and not any(ch in agent for ch in "\r\n") else None


def load_session_cookies(host: str, settings: dict | None = None) -> tuple[list[Cookie], str | None]:
    cookies, agent, _revision = load_session_cookie_snapshot(host, settings)
    return cookies, agent


def _snapshot_revision(source: str, payload) -> str:
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return source + ":" + hashlib.sha256(raw).hexdigest()


def _cookie_sources(host: str) -> list[str]:
    from core.parser.sites import HOST_ALIASES
    sources = [host]
    if host.startswith("www."):
        sources.append(host[4:])
    canonical = HOST_ALIASES.get(sources[-1])
    if canonical and canonical not in sources:
        sources.append(canonical)
    return sources


def cookie_dependency_hosts(host: str) -> list[str]:
    """Hosts whose saved state may affect this session, without reading the database.

    Include source candidates and the explicit www redirect authority. A renewal of
    either must invalidate cached cookies, even when the selected source stays the same.
    """
    host = str(host or "").strip().lower().lstrip(".")
    if not host or any(ch in host for ch in "/\\:@ \t\r\n"):
        return []
    return list(dict.fromkeys([*_cookie_sources(host), "www." + host]))


def _www_cookie_authority(host: str, source_host: str, cookies: list[Cookie], settings):
    """An exact www record supersedes embedded www cookies from an older root bundle."""
    if source_host != host:
        return cookies, None
    from core.domain_credentials import load_domain_cookie_record
    child = "www." + host
    authority = load_domain_cookie_record(settings, child)
    if authority is not None:
        cookies = [cookie for cookie in cookies if cookie.domain.lstrip(".").lower() != child]
    return cookies, [child, authority]


def load_session_cookie_snapshot(host: str, settings: dict | None = None) -> tuple[list[Cookie], str | None, str]:
    """Read one coherent cookie snapshot, with its original scope and browser identity.

    An exact-host Companion record is authoritative, including empty/revoked records.
    Known site aliases and www may use a canonical bundle only when there is no exact
    record; cookie domains are checked against the destination and never rewritten.
    Embedded www cookies support redirects only while www has no exact Companion record.
    An exact record, including empty/revoked, suppresses that older embedded authority.
    Without a handoff, the most recently written legacy JSON or Netscape TXT file wins;
    equal timestamps prefer JSON. Files are snapshots, never merged: merging revives
    deleted cookies. Only JSON supplies a User-Agent. Callers must preserve these
    Cookie objects and enforce host-only scope when preparing requests and redirects.
    The opaque revision changes when a saved snapshot changes; an empty revision
    means no saved source. Empty/revoked sources still have a revision so caches can
    clear server-acquired cookies when the person replaces or revokes their handoff.
    """
    from core.domain_credentials import load_domain_cookie_record
    host = str(host or "").strip().lower().lstrip(".")
    if not host or any(ch in host for ch in "/\\:@ \t\r\n"):
        return [], None, ""
    sources = _cookie_sources(host)
    for source_host in sources:
        handoff = load_domain_cookie_record(settings, source_host)
        if handoff is not None:
            cookies, agent = _bundle_cookies(host, handoff.get("cookies", []), handoff.get("user_agent"), source_host)
            cookies, authority = _www_cookie_authority(host, source_host, cookies, settings)
            return cookies, agent, _snapshot_revision("companion", [source_host, handoff, authority])
    existing = []
    for source_host in sources:
        candidates = [BROWSER_COOKIES_DIR / f"{source_host}.{suffix}" for suffix in ("txt", "json")]
        existing = [path for path in candidates if path.is_file()]
        if existing:
            break
    if not existing:
        return [], None, ""
    selected = max(existing, key=lambda path: (path.stat().st_mtime_ns, path.suffix == ".json"))
    stat = selected.stat()
    if selected.suffix == ".json":
        records, user_agent = _load_cookie_bundle_path(selected)
        cookies, agent = _bundle_cookies(host, records, user_agent, source_host)
    else:
        jar = load_netscape_cookie_file(selected)
        cookies = [scoped for cookie in jar or [] if (scoped := _scoped_cookie(host, cookie, source_host)) is not None]
        agent = None
    cookies, authority = _www_cookie_authority(host, source_host, cookies, settings)
    revision = _snapshot_revision(selected.suffix[1:], [source_host, stat.st_mtime_ns, stat.st_size, authority])
    return cookies, agent, revision
