"""Central secret redaction for logs, diagnostics and crash reports.

No diagnostic or error path should ever persist API keys, login values,
tokens, passwords or HTTP cookie/authorization payloads. Media/source URLs
remain readable; only credentials are removed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SECRET_KEYS = (
    "api_key", "apikey", "api-key", "access_token", "refresh_token", "token",
    "authorization", "auth", "password", "passwd", "pass", "login", "user_id",
    "username", "cookie", "secret", "session_key",
)
# Cookie and form names of sites that are secrets too, but only as whole names in text
# (as dict keys, ``is_secret_key`` already catches «pass_hash» by «pass»).
_TEXT_ONLY_KEYS = ("pass_hash", "cf_clearance", "csrf_token", "csrftoken")
_KEY_GROUP = "|".join(re.escape(k) for k in _SECRET_KEYS + _TEXT_ONLY_KEYS)

# Query strings, form payloads and plain key=value fragments.
_QUERY_RE = re.compile(
    rf"(?i)(?P<prefix>(?<![A-Za-z0-9_])(?:{_KEY_GROUP})\s*(?:=|%3[dD])\s*)"
    r"(?!(?:yes|no)(?:[&\s,;]|$))(?P<value>[^&\s,;]+)"     # «api_key=no» (is there a key) stays readable
)
# Session cookies of sites: _danbooru2_session=…, PHPSESSID=…, sessionid=….
_SESSION_RE = re.compile(r"(?i)(?P<prefix>(?<![A-Za-z0-9])[A-Za-z0-9_]*sess(?:ion)?(?:_?id)?\s*=\s*)(?P<value>[^&\s,;]+)")
# JSON / repr-like output: "api_key": "secret" or 'token': 'secret'.
_QUOTED_RE = re.compile(
    rf"(?i)(?P<prefix>[\"'](?:{_KEY_GROUP})[\"']\s*:\s*[\"'])(?P<value>.*?)(?P<suffix>[\"'])"
)
# A (login, key) pair as Python prints it: auth=('bob', 'key').
_AUTH_PAIR_RE = re.compile(r"(?i)(?P<prefix>(?<![A-Za-z0-9_])auth\s*=\s*)\([^)]*\)")
# A login and a password inside an address: https://bob:key@host/…
_URL_USERINFO_RE = re.compile(r"(?i)(?P<prefix>\b[a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]*@")
# Headers are safer removed wholesale — also when a log line has text before them.
_HEADER_RE = re.compile(r"(?im)(?P<prefix>(?<![A-Za-z0-9_-])(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*).*$")
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+\-/=]+")


def is_secret_key(key: object) -> bool:
    name = str(key or "").lower().replace("-", "_")
    return any(token.replace("-", "_") in name for token in _SECRET_KEYS)


def sanitize_text(value: object) -> str:
    """Return human-readable text with credential values removed."""
    text = str(value if value is not None else "")
    text = _HEADER_RE.sub(lambda m: m.group("prefix") + "<removed>", text)
    text = _BEARER_RE.sub(r"\1<removed>", text)
    text = _URL_USERINFO_RE.sub(lambda m: m.group("prefix") + "<removed>@", text)
    text = _AUTH_PAIR_RE.sub(lambda m: m.group("prefix") + "<removed>", text)
    text = _QUOTED_RE.sub(lambda m: m.group("prefix") + "<removed>" + m.group("suffix"), text)
    text = _QUERY_RE.sub(lambda m: m.group("prefix") + "<removed>", text)
    text = _SESSION_RE.sub(lambda m: m.group("prefix") + "<removed>", text)
    return text


def sanitize_object(value: Any, key: str = "") -> Any:
    """Redact secrets recursively before serialising reports/settings."""
    if is_secret_key(key):
        return "<removed>" if value not in (None, "", [], {}) else value
    if isinstance(value, dict):
        return {k: sanitize_object(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_object(item, key) for item in value]
    if isinstance(value, tuple):
        return [sanitize_object(item, key) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def sanitize_file_in_place(path: str | Path) -> bool:
    """Rewrite a text log/report only when redaction changed its contents."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return False
    try:
        original = p.read_text(encoding="utf-8", errors="replace")
        cleaned = sanitize_text(original)
        if cleaned == original:
            return False
        p.write_text(cleaned, encoding="utf-8")
        return True
    except Exception:
        return False


def sanitize_log_directory(log_dir: str | Path) -> dict[str, int]:
    """Remove secrets from old persisted logs/crash reports in place.

    Raw copies are intentionally not preserved: backing up exposed credentials
    would defeat the privacy repair. This touches logs only, never the DB/media.
    """
    root = Path(log_dir)
    checked = changed = 0
    if not root.exists():
        return {"checked": 0, "changed": 0}
    for pattern in ("*.log", "*.log.*", "*.json", "*.jsonl", "*.jsonl.*"):
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            checked += 1
            changed += int(sanitize_file_in_place(path))
    return {"checked": checked, "changed": changed}


class RedactingFormatter:
    """Formatter wrapper kept simple so it can wrap any stdlib formatter."""
    def __init__(self, formatter):
        self.formatter = formatter

    def format(self, record):
        return sanitize_text(self.formatter.format(record))
