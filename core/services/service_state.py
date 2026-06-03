"""Durable runtime state for quota-limited external services.

Queue rows and cooldown state are intentionally stored in the same SQLite
library as scan results so restart cannot split them across JSON files.
"""
from __future__ import annotations

import time
from core.database.connection import db


def get_cooldown(settings: dict, service: str) -> dict:
    key = str(service or '').strip().lower()
    with db(settings, readonly=True) as con:
        row = con.execute(
            "SELECT cooldown_until, reason, updated_at FROM service_state WHERE service=?",
            (key,),
        ).fetchone()
    if not row:
        return {"cooldown_until": 0, "reason": "", "updated_at": 0}
    return {"cooldown_until": int(row["cooldown_until"] or 0), "reason": str(row["reason"] or ""), "updated_at": int(row["updated_at"] or 0)}


def set_cooldown(settings: dict, service: str, cooldown_until: int, *, reason: str = '') -> None:
    key = str(service or '').strip().lower()
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute(
            """INSERT INTO service_state(service,cooldown_until,reason,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(service) DO UPDATE SET cooldown_until=excluded.cooldown_until,
               reason=excluded.reason, updated_at=excluded.updated_at""",
            (key, int(cooldown_until or 0), str(reason or ''), now),
        )


def clear_cooldown(settings: dict, service: str) -> None:
    set_cooldown(settings, service, 0, reason='')
