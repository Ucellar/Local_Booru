"""Explicit, domain-scoped browser cookie/User-Agent handoff.

On Windows the cookie JSON is encrypted with DPAPI.  Non-Windows runs use a
clearly marked base64 fallback only for tests/development; production target is
Windows and no cookie values are ever written to normal logs or diagnostics.
"""
from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

from core.database.connection import db


def _protect_windows(raw: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
    buf = ctypes.create_string_buffer(raw)
    in_blob = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(in_blob), "Local Booru", None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _unprotect_windows(raw: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
    buf = ctypes.create_string_buffer(raw)
    in_blob = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def protect_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_protect_windows(raw)).decode("ascii")
    return "test-b64:" + base64.b64encode(raw).decode("ascii")


def unprotect_payload(blob: str) -> dict[str, Any]:
    prefix, _, value = str(blob or "").partition(":")
    raw = base64.b64decode(value.encode("ascii"))
    if prefix == "dpapi":
        raw = _unprotect_windows(raw)
    elif prefix != "test-b64":
        raise ValueError("unknown credential protection")
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def store_domain_credentials(settings: dict | None, *, domain: str, cookies: list[dict[str, Any]], user_agent: str) -> dict[str, Any]:
    domain = str(domain or "").strip().lower().lstrip(".")[:255]
    if not domain or any(ch in domain for ch in "/\\:@"):
        raise ValueError("invalid domain")
    clean = []
    for cookie in cookies or []:
        if not isinstance(cookie, dict):
            continue
        cdom = str(cookie.get("domain") or domain).lower().lstrip(".")
        if not (cdom == domain or domain.endswith("." + cdom) or cdom.endswith("." + domain)):
            continue
        clean.append({k: cookie.get(k) for k in ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite", "expirationDate") if k in cookie})
    blob = protect_payload({"domain": domain, "cookies": clean})
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute(
            """INSERT INTO domain_session_credentials(domain,protected_blob,user_agent,cookie_count,received_at,last_verified_at,last_error,revoked_at)
               VALUES(?,?,?,?,?,0,'',0)
               ON CONFLICT(domain) DO UPDATE SET protected_blob=excluded.protected_blob,user_agent=excluded.user_agent,
                 cookie_count=excluded.cookie_count,received_at=excluded.received_at,last_error='',revoked_at=0""",
            (domain, blob, str(user_agent or "")[:1000], len(clean), now),
        )
    return {"ok": True, "domain": domain, "cookie_count": len(clean), "received_at": now}


def load_domain_credentials(settings: dict | None, domain: str) -> dict[str, Any]:
    domain = str(domain or "").strip().lower().lstrip(".")
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT * FROM domain_session_credentials WHERE domain=? AND revoked_at=0", (domain,)).fetchone()
    if not row:
        return {}
    payload = unprotect_payload(str(row["protected_blob"] or ""))
    payload.update({"user_agent": str(row["user_agent"] or ""), "received_at": int(row["received_at"] or 0)})
    return payload


def list_domain_credentials(settings: dict | None) -> list[dict[str, Any]]:
    with db(settings, readonly=True) as con:
        rows = con.execute("SELECT domain,cookie_count,received_at,last_verified_at,last_error,revoked_at FROM domain_session_credentials ORDER BY domain").fetchall()
    return [dict(row) for row in rows]


def revoke_domain(settings: dict | None, domain: str) -> bool:
    with db(settings, write=True) as con:
        cur = con.execute("UPDATE domain_session_credentials SET protected_blob='',revoked_at=? WHERE domain=?", (int(time.time()), str(domain or "").lower().lstrip(".")))
        return int(cur.rowcount or 0) > 0
