"""Durable runtime state for quota-limited external services.

Queue rows, cooldown state and the last known quota snapshot are intentionally
stored in the same SQLite library as scan results.  This keeps restart-safe
behaviour and makes the current SauceNAO limit visible without exposing its key.
"""
from __future__ import annotations

import time
from core.database.connection import db


def _empty_state() -> dict:
    return {
        "cooldown_until": 0,
        "reason": "",
        "updated_at": 0,
        "short_remaining": -1,
        "long_remaining": -1,
        "quota_checked_at": 0,
    }


def get_cooldown(settings: dict, service: str) -> dict:
    key = str(service or '').strip().lower()
    with db(settings, readonly=True) as con:
        row = con.execute(
            """SELECT cooldown_until, reason, updated_at,
                      short_remaining, long_remaining, quota_checked_at
               FROM service_state WHERE service=?""",
            (key,),
        ).fetchone()
    if not row:
        return _empty_state()
    return {
        "cooldown_until": int(row["cooldown_until"] or 0),
        "reason": str(row["reason"] or ""),
        "updated_at": int(row["updated_at"] or 0),
        "short_remaining": int(row["short_remaining"] if row["short_remaining"] is not None else -1),
        "long_remaining": int(row["long_remaining"] if row["long_remaining"] is not None else -1),
        "quota_checked_at": int(row["quota_checked_at"] or 0),
    }


def set_cooldown(settings: dict, service: str, cooldown_until: int, *, reason: str = '') -> None:
    key = str(service or '').strip().lower()
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute(
            """INSERT INTO service_state(service,cooldown_until,reason,updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(service) DO UPDATE SET cooldown_until=excluded.cooldown_until,
               reason=excluded.reason, updated_at=excluded.updated_at""",
            (key, int(cooldown_until or 0), str(reason or ''), now),
        )


def set_quota_snapshot(settings: dict, service: str, *, short_remaining=None, long_remaining=None) -> None:
    """Store only quota counters returned by the API; credentials are never stored here."""
    key = str(service or '').strip().lower()
    now = int(time.time())
    try:
        short_value = int(short_remaining)
    except (TypeError, ValueError):
        short_value = -1
    try:
        long_value = int(long_remaining)
    except (TypeError, ValueError):
        long_value = -1
    with db(settings, write=True) as con:
        con.execute(
            """INSERT INTO service_state(service,cooldown_until,reason,updated_at,short_remaining,long_remaining,quota_checked_at)
               VALUES(?,0,'',?,?,?,?)
               ON CONFLICT(service) DO UPDATE SET short_remaining=excluded.short_remaining,
               long_remaining=excluded.long_remaining, quota_checked_at=excluded.quota_checked_at,
               updated_at=excluded.updated_at""",
            (key, now, short_value, long_value, now),
        )


def clear_cooldown(settings: dict, service: str) -> None:
    set_cooldown(settings, service, 0, reason='')
