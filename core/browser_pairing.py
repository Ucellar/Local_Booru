"""Pairing and per-task credentials for the localhost browser companion.

The old static marker remains only as a protocol discriminator.  Authorization
requires a paired-extension token, while every issued browser task additionally
gets a one-time task token tied to the paired extension and lane.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any

from core.database.connection import db

PAIRING_HEADER = "X-Local-Booru-Pairing"
TASK_TOKEN_HEADER = "X-Local-Booru-Task-Token"


def _hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", "strict")).hexdigest()


def _clean_origin(origin: str) -> str:
    return str(origin or "").strip()[:500]


@dataclass(frozen=True)
class PairedClient:
    extension_id: str
    display_name: str
    origin: str


def create_pairing_code(settings: dict | None, *, display_name: str = "Browser Companion", ttl_seconds: int = 600) -> str:
    """Create a short, one-use code that is shown only inside Local Booru UI."""
    ttl = max(60, min(3600, int(ttl_seconds or 600)))
    # Eight digits: easy to type, still protected by expiry and bounded attempts.
    code = f"{secrets.randbelow(100_000_000):08d}"
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute("DELETE FROM browser_pairing_codes WHERE expires_at<? OR consumed_at>0", (now,))
        con.execute(
            """INSERT INTO browser_pairing_codes(code_hash,created_at,expires_at,consumed_at,attempts,max_attempts,display_name)
               VALUES(?,?,?,0,0,8,?)""",
            (_hash(code), now, now + ttl, str(display_name or "Browser Companion")[:160]),
        )
    return code


def exchange_pairing_code(settings: dict | None, *, code: str, origin: str, display_name: str = "") -> dict[str, Any]:
    raw = "".join(ch for ch in str(code or "") if ch.isdigit())
    now = int(time.time())
    if len(raw) != 8:
        return {"ok": False, "error": "invalid_pairing_code"}
    code_hash = _hash(raw)
    with db(settings, write=True) as con:
        row = con.execute("SELECT * FROM browser_pairing_codes WHERE code_hash=?", (code_hash,)).fetchone()
        if not row:
            return {"ok": False, "error": "invalid_pairing_code"}
        attempts = int(row["attempts"] or 0) + 1
        con.execute("UPDATE browser_pairing_codes SET attempts=? WHERE code_hash=?", (attempts, code_hash))
        if int(row["consumed_at"] or 0) > 0:
            return {"ok": False, "error": "pairing_code_consumed"}
        if int(row["expires_at"] or 0) < now:
            return {"ok": False, "error": "pairing_code_expired"}
        if attempts > int(row["max_attempts"] or 8):
            return {"ok": False, "error": "pairing_attempt_limit"}
        extension_id = "ext-" + secrets.token_hex(16)
        token = secrets.token_urlsafe(48)
        label = str(display_name or row["display_name"] or "Browser Companion")[:160]
        clean_origin = _clean_origin(origin)
        metadata = '{"origin":' + __import__("json").dumps(clean_origin, ensure_ascii=False) + '}'
        con.execute(
            """INSERT INTO paired_extensions(extension_id,display_name,token_hash,created_at,last_seen_at,revoked_at,metadata_json)
               VALUES(?,?,?,?,?,0,?)""",
            (extension_id, label, _hash(token), now, now, metadata),
        )
        con.execute("UPDATE browser_pairing_codes SET consumed_at=? WHERE code_hash=?", (now, code_hash))
    return {"ok": True, "extension_id": extension_id, "pairing_token": token, "display_name": label}


def authenticate(settings: dict | None, *, token: str, origin: str) -> PairedClient | None:
    token_hash = _hash(str(token or ""))
    if not token:
        return None
    with db(settings, write=True) as con:
        row = con.execute(
            "SELECT extension_id,display_name,metadata_json FROM paired_extensions WHERE token_hash=? AND revoked_at=0",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        try:
            metadata = __import__("json").loads(str(row["metadata_json"] or "{}"))
        except Exception:
            metadata = {}
        bound_origin = _clean_origin(metadata.get("origin") or "")
        current_origin = _clean_origin(origin)
        if bound_origin and current_origin and not secrets.compare_digest(bound_origin, current_origin):
            return None
        con.execute("UPDATE paired_extensions SET last_seen_at=? WHERE extension_id=?", (int(time.time()), str(row["extension_id"])))
        return PairedClient(str(row["extension_id"]), str(row["display_name"] or ""), bound_origin or current_origin)


def revoke_extension(settings: dict | None, extension_id: str) -> bool:
    with db(settings, write=True) as con:
        cur = con.execute("UPDATE paired_extensions SET revoked_at=? WHERE extension_id=? AND revoked_at=0", (int(time.time()), str(extension_id)))
        con.execute("DELETE FROM browser_task_tokens WHERE extension_id=?", (str(extension_id),))
        return int(cur.rowcount or 0) > 0


def revoke_all(settings: dict | None) -> int:
    with db(settings, write=True) as con:
        cur = con.execute("UPDATE paired_extensions SET revoked_at=? WHERE revoked_at=0", (int(time.time()),))
        con.execute("DELETE FROM browser_task_tokens")
        return int(cur.rowcount or 0)


def list_extensions(settings: dict | None) -> list[dict[str, Any]]:
    with db(settings, readonly=True) as con:
        rows = con.execute(
            "SELECT extension_id,display_name,created_at,last_seen_at,revoked_at,metadata_json FROM paired_extensions ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def issue_task_token(settings: dict | None, *, task_id: str, extension_id: str, lane: str, ttl_seconds: int = 300) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute("DELETE FROM browser_task_tokens WHERE expires_at<? OR consumed_at>0", (now - 60,))
        con.execute(
            """INSERT INTO browser_task_tokens(task_id,extension_id,lane,token_hash,issued_at,expires_at,consumed_at)
               VALUES(?,?,?,?,?,?,0)
               ON CONFLICT(task_id) DO UPDATE SET extension_id=excluded.extension_id,lane=excluded.lane,
                 token_hash=excluded.token_hash,issued_at=excluded.issued_at,expires_at=excluded.expires_at,consumed_at=0""",
            (str(task_id), str(extension_id), str(lane), _hash(token), now, now + max(30, min(3600, int(ttl_seconds or 300)))),
        )
    return token


def validate_task_token(settings: dict | None, *, task_id: str, extension_id: str, lane: str, token: str, consume: bool = False) -> bool:
    now = int(time.time())
    with db(settings, write=True) as con:
        row = con.execute(
            """SELECT token_hash,expires_at,consumed_at FROM browser_task_tokens
               WHERE task_id=? AND extension_id=? AND lane=?""",
            (str(task_id), str(extension_id), str(lane)),
        ).fetchone()
        if not row or int(row["expires_at"] or 0) < now or int(row["consumed_at"] or 0) > 0:
            return False
        if not secrets.compare_digest(str(row["token_hash"] or ""), _hash(str(token or ""))):
            return False
        if consume:
            con.execute("UPDATE browser_task_tokens SET consumed_at=? WHERE task_id=?", (now, str(task_id)))
        return True
