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
        "quota_probe_after": 0,
        "quota_last_admitted_at": 0,
        "quota_admissions_since_snapshot": 0,
        "quota_admission_serial": 0,
        "quota_snapshot_serial": 0,
    }


def get_cooldown(settings: dict, service: str) -> dict:
    key = str(service or '').strip().lower()
    with db(settings, readonly=True) as con:
        row = con.execute(
            """SELECT cooldown_until, reason, updated_at,
                      short_remaining, long_remaining, quota_checked_at,
                      quota_probe_after, quota_last_admitted_at,
                      quota_admissions_since_snapshot, quota_admission_serial,
                      quota_snapshot_serial
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
        "quota_probe_after": int(row["quota_probe_after"] or 0),
        "quota_last_admitted_at": int(row["quota_last_admitted_at"] or 0),
        "quota_admissions_since_snapshot": int(row["quota_admissions_since_snapshot"] or 0),
        "quota_admission_serial": int(row["quota_admission_serial"] or 0),
        "quota_snapshot_serial": int(row["quota_snapshot_serial"] or 0),
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


def set_quota_snapshot(
    settings: dict,
    service: str,
    *,
    short_remaining=None,
    long_remaining=None,
    probe_interval_seconds=None,
    reserve=None,
    admission_serial=None,
) -> bool:
    """Reconcile a real API response with the durable local estimate.

    The request admission serial prevents an older, slower response from
    overwriting a newer snapshot. Any later local admissions are subtracted
    from the server counter before publication, so an in-flight overlap cannot
    accidentally give quota back. Returns ``False`` only for a stale response.
    """
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
    try:
        probe_interval = int(float(probe_interval_seconds if probe_interval_seconds is not None else settings.get("saucenao_quota_probe_interval_seconds", 3600)))
    except Exception:
        probe_interval = 3600
    probe_interval = max(60, min(86400, probe_interval))
    try:
        reserve_value = int(float(reserve if reserve is not None else settings.get("saucenao_quota_reserve", 10)))
    except Exception:
        reserve_value = 10
    reserve_value = max(0, reserve_value)

    with db(settings, write=True) as con:
        con.execute(
            """INSERT INTO service_state(
                   service,cooldown_until,reason,updated_at,short_remaining,long_remaining,
                   quota_checked_at,quota_probe_after,quota_last_admitted_at,
                   quota_admissions_since_snapshot,quota_admission_serial,quota_snapshot_serial
               ) VALUES(?,0,'',0,-1,-1,0,0,0,0,0,0)
               ON CONFLICT(service) DO NOTHING""",
            (key,),
        )
        row = con.execute(
            """SELECT long_remaining,quota_admission_serial,quota_snapshot_serial
               FROM service_state WHERE service=?""",
            (key,),
        ).fetchone()
        current_long = int(row["long_remaining"] if row and row["long_remaining"] is not None else -1)
        current_serial = int(row["quota_admission_serial"] or 0) if row else 0
        snapshot_serial = int(row["quota_snapshot_serial"] or 0) if row else 0
        try:
            token = int(admission_serial or 0)
        except Exception:
            token = 0
        if token <= 0:
            token = current_serial
        if token < snapshot_serial:
            return False

        later_admissions = max(0, current_serial - token)
        if long_value >= 0:
            effective_long = max(0, long_value - later_admissions)
        else:
            effective_long = current_long
        probe_after = now + probe_interval if effective_long >= 0 and effective_long <= reserve_value else 0
        reason = "quota_governor_probe" if probe_after else ""
        con.execute(
            """UPDATE service_state SET
                   short_remaining=?,long_remaining=?,quota_checked_at=?,
                   quota_probe_after=?,quota_admissions_since_snapshot=?,
                   quota_snapshot_serial=?,updated_at=?,reason=?
               WHERE service=?""",
            (
                short_value,
                effective_long,
                now,
                probe_after,
                later_admissions,
                token,
                now,
                reason,
                key,
            ),
        )
    return True


def clear_cooldown(settings: dict, service: str) -> None:
    set_cooldown(settings, service, 0, reason='')
