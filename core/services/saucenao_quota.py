"""Restart-safe admission control for SauceNAO's long quota.

The API's last known ``long_remaining`` counter already lives in ``service_state``.
This module turns that observation into a conservative governor:

* admissions decrement the durable local estimate before the network request;
* every API response reconciles that estimate with the server counter;
* when the remaining budget is below the reserve, normal requests are deferred
  before network I/O and only one durable control probe is admitted per probe
  interval;
* while quota is known, requests are spread over a configurable horizon instead
  of consuming the complete daily allowance in one parser burst.

This does not replace the normal per-minute limiter.  Both gates must admit a
request.  No API key or request payload is stored here.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from urllib.parse import urlparse

from core.database.connection import db

SERVICE = "saucenao"
HOST = "saucenao.com"


@dataclass(frozen=True)
class QuotaAdmission:
    admitted: bool
    deferred: bool = False
    wait_seconds: float = 0.0
    retry_after: int = 0
    reason: str = ""
    remaining_before: int = -1
    remaining_after: int = -1
    reserve: int = 0
    target_interval: float = 0.0
    probe: bool = False
    admission_serial: int = 0


def is_saucenao_api_request(url_or_host: str) -> bool:
    raw = str(url_or_host or "").strip()
    try:
        parsed = urlparse(raw if "://" in raw else "//" + raw)
        host = (parsed.netloc or parsed.path or "").split("/", 1)[0].lower().replace("www.", "")
        path = parsed.path or ""
    except Exception:
        return False
    if host != HOST:
        return False
    # The limiter normally receives the complete upload URL.  A bare host is
    # accepted for compatibility with direct callers/tests, but unrelated
    # SauceNAO asset URLs are not charged against the API governor.
    return not path or path == "/" or path.rstrip("/").lower().endswith("/search.php")


def enabled(settings: dict | None) -> bool:
    settings = settings or {}
    return bool(settings.get("saucenao_quota_governor_enabled", True))


def _int_setting(settings: dict, key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(float(settings.get(key, default) or default))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def reserve_for(settings: dict | None) -> int:
    return _int_setting(settings or {}, "saucenao_quota_reserve", 10, 0, 10000)


def spread_seconds_for(settings: dict | None) -> int:
    return _int_setting(settings or {}, "saucenao_quota_spread_seconds", 3600, 60, 604800)


def probe_interval_for(settings: dict | None) -> int:
    return _int_setting(settings or {}, "saucenao_quota_probe_interval_seconds", 3600, 60, 86400)


def _ensure_row(con) -> None:
    con.execute(
        """INSERT INTO service_state(
               service,cooldown_until,reason,updated_at,short_remaining,long_remaining,
               quota_checked_at,quota_probe_after,quota_last_admitted_at,
               quota_admissions_since_snapshot,quota_admission_serial,
               quota_snapshot_serial
           ) VALUES(?,0,'',0,-1,-1,0,0,0,0,0,0)
           ON CONFLICT(service) DO NOTHING""",
        (SERVICE,),
    )


def admission(settings: dict, *, now: int | None = None) -> QuotaAdmission:
    """Atomically decide whether one SauceNAO API request may be sent now.

    A non-deferred result with ``wait_seconds`` asks the ordinary rate-limiter
    loop to wait and try again.  A deferred result must be routed to the durable
    reverse retry queue without touching the network.
    """
    settings = settings or {}
    stamp = int(time.time() if now is None else now)
    if not enabled(settings):
        return QuotaAdmission(admitted=True, reason="governor_disabled")
    reserve = reserve_for(settings)
    spread_seconds = spread_seconds_for(settings)
    probe_interval = probe_interval_for(settings)

    try:
        with db(settings, write=True) as con:
            _ensure_row(con)
            row = con.execute(
                """SELECT long_remaining,quota_checked_at,quota_probe_after,
                          quota_last_admitted_at,quota_admissions_since_snapshot,
                          quota_admission_serial,quota_snapshot_serial
                   FROM service_state WHERE service=?""",
                (SERVICE,),
            ).fetchone()
            remaining = int(row["long_remaining"] if row and row["long_remaining"] is not None else -1)
            checked_at = int(row["quota_checked_at"] or 0) if row else 0
            probe_after = int(row["quota_probe_after"] or 0) if row else 0
            last_admitted = int(row["quota_last_admitted_at"] or 0) if row else 0
            admissions = int(row["quota_admissions_since_snapshot"] or 0) if row else 0
            admission_serial = int(row["quota_admission_serial"] or 0) if row else 0

            # No server snapshot yet: admit one discovery request.  Persisting
            # the admission time prevents a restart from creating a burst before
            # the first response has reconciled the counter.
            if remaining < 0 or checked_at <= 0:
                if last_admitted > 0:
                    wait = max(0.0, 15.0 - float(stamp - last_admitted))
                    if wait > 0:
                        return QuotaAdmission(
                            admitted=False,
                            wait_seconds=wait,
                            retry_after=stamp + int(math.ceil(wait)),
                            reason="awaiting_first_snapshot",
                            remaining_before=remaining,
                            remaining_after=remaining,
                            reserve=reserve,
                            target_interval=15.0,
                        )
                con.execute(
                    """UPDATE service_state
                       SET quota_last_admitted_at=?,
                           quota_admissions_since_snapshot=?,
                           quota_admission_serial=?,updated_at=?
                       WHERE service=?""",
                    (stamp, admissions + 1, admission_serial + 1, stamp, SERVICE),
                )
                return QuotaAdmission(
                    admitted=True,
                    reason="initial_quota_probe",
                    remaining_before=remaining,
                    remaining_after=remaining,
                    reserve=reserve,
                    probe=True,
                    admission_serial=admission_serial + 1,
                )

            if remaining <= reserve:
                next_probe = max(probe_after, checked_at + probe_interval)
                if stamp < next_probe:
                    return QuotaAdmission(
                        admitted=False,
                        deferred=True,
                        retry_after=next_probe,
                        reason="reserve_protected",
                        remaining_before=remaining,
                        remaining_after=remaining,
                        reserve=reserve,
                    )
                # Exactly one worker/process admission advances the durable probe
                # deadline under BEGIN IMMEDIATE.  Other workers see the new
                # deadline and defer instead of stampeding the exhausted API.
                after = max(0, remaining - 1)
                con.execute(
                    """UPDATE service_state
                       SET long_remaining=?,quota_probe_after=?,
                           quota_last_admitted_at=?,quota_admissions_since_snapshot=?,
                           quota_admission_serial=?,updated_at=?,reason=? WHERE service=?""",
                    (
                        after,
                        stamp + probe_interval,
                        stamp,
                        admissions + 1,
                        admission_serial + 1,
                        stamp,
                        "quota_governor_probe",
                        SERVICE,
                    ),
                )
                return QuotaAdmission(
                    admitted=True,
                    reason="control_probe",
                    remaining_before=remaining,
                    remaining_after=after,
                    reserve=reserve,
                    probe=True,
                    admission_serial=admission_serial + 1,
                )

            spendable = max(1, remaining - reserve)
            target_interval = float(spread_seconds) / float(spendable)
            wait = max(0.0, target_interval - float(stamp - last_admitted)) if last_admitted > 0 else 0.0
            if wait > 0:
                return QuotaAdmission(
                    admitted=False,
                    wait_seconds=wait,
                    retry_after=stamp + int(math.ceil(wait)),
                    reason="adaptive_spacing",
                    remaining_before=remaining,
                    remaining_after=remaining,
                    reserve=reserve,
                    target_interval=target_interval,
                )

            after = max(0, remaining - 1)
            con.execute(
                """UPDATE service_state
                   SET long_remaining=?,quota_last_admitted_at=?,
                       quota_admissions_since_snapshot=?,quota_admission_serial=?,
                       updated_at=?,reason='' WHERE service=?""",
                (after, stamp, admissions + 1, admission_serial + 1, stamp, SERVICE),
            )
            return QuotaAdmission(
                admitted=True,
                reason="quota_admitted",
                remaining_before=remaining,
                remaining_after=after,
                reserve=reserve,
                target_interval=target_interval,
                admission_serial=admission_serial + 1,
            )
    except Exception as exc:
        # Quota state is safety-critical.  A transient DB failure must not turn
        # into an ungoverned API call; defer briefly and let the durable parser
        # retry path re-enter once SQLite is healthy.
        return QuotaAdmission(
            admitted=False,
            deferred=True,
            retry_after=stamp + 300,
            reason=f"governor_state_unavailable:{type(exc).__name__}",
            reserve=reserve,
        )


def public_state(settings: dict) -> dict:
    """Return governor diagnostics without exposing credentials."""
    reserve = reserve_for(settings)
    try:
        with db(settings, readonly=True) as con:
            row = con.execute(
                """SELECT short_remaining,long_remaining,quota_checked_at,
                          quota_probe_after,quota_last_admitted_at,
                          quota_admissions_since_snapshot,quota_admission_serial,
                          quota_snapshot_serial,reason
                   FROM service_state WHERE service=?""",
                (SERVICE,),
            ).fetchone()
        if not row:
            return {"enabled": enabled(settings), "reserve": reserve}
        return {
            "enabled": enabled(settings),
            "reserve": reserve,
            "short_remaining": int(row["short_remaining"] if row["short_remaining"] is not None else -1),
            "long_remaining": int(row["long_remaining"] if row["long_remaining"] is not None else -1),
            "quota_checked_at": int(row["quota_checked_at"] or 0),
            "quota_probe_after": int(row["quota_probe_after"] or 0),
            "quota_last_admitted_at": int(row["quota_last_admitted_at"] or 0),
            "quota_admissions_since_snapshot": int(row["quota_admissions_since_snapshot"] or 0),
            "quota_admission_serial": int(row["quota_admission_serial"] or 0),
            "quota_snapshot_serial": int(row["quota_snapshot_serial"] or 0),
            "reason": str(row["reason"] or ""),
        }
    except Exception as exc:
        return {"enabled": enabled(settings), "reserve": reserve, "error": f"{type(exc).__name__}: {exc}"}
