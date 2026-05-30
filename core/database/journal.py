"""Small operation journal for Local Booru.

This is not a huge Hydrus-style transaction engine.  It is a durable trace of
multi-step operations so crashes can be diagnosed and later reconciled.
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .connection import db


def _now() -> int:
    return int(time.time())


def start(settings: dict, op_type: str, *, target_type: str = "", target_id: str = "", payload: dict | None = None) -> str:
    op_id = f"op_{_now()}_{uuid.uuid4().hex[:10]}"
    now = _now()
    with db(settings, write=True) as con:
        con.execute(
            """
            INSERT INTO operation_journal
            (op_id, op_type, status, target_type, target_id, payload_json, created_at, updated_at)
            VALUES (?, ?, 'running', ?, ?, ?, ?, ?)
            """,
            (op_id, op_type, target_type, str(target_id or ""), json.dumps(payload or {}, ensure_ascii=False), now, now),
        )
    return op_id


def finish(settings: dict, op_id: str, *, status: str = "done", error: str = "", payload_update: dict | None = None) -> None:
    now = _now()
    payload_json = None
    if payload_update is not None:
        try:
            with db(settings, readonly=True) as con:
                row = con.execute("SELECT payload_json FROM operation_journal WHERE op_id=?", (op_id,)).fetchone()
            old = json.loads(row["payload_json"] or "{}") if row else {}
            old.update(payload_update)
            payload_json = json.dumps(old, ensure_ascii=False)
        except Exception:
            payload_json = json.dumps(payload_update, ensure_ascii=False)
    with db(settings, write=True) as con:
        if payload_json is None:
            con.execute(
                "UPDATE operation_journal SET status=?, error=?, updated_at=?, finished_at=? WHERE op_id=?",
                (status, str(error)[:2000], now, now, op_id),
            )
        else:
            con.execute(
                "UPDATE operation_journal SET status=?, error=?, payload_json=?, updated_at=?, finished_at=? WHERE op_id=?",
                (status, str(error)[:2000], payload_json, now, now, op_id),
            )


def fail(settings: dict, op_id: str, error: BaseException | str) -> None:
    finish(settings, op_id, status="failed", error=str(error))


@contextmanager
def operation(settings: dict, op_type: str, *, target_type: str = "", target_id: str = "", payload: dict | None = None) -> Iterator[str]:
    op_id = start(settings, op_type, target_type=target_type, target_id=target_id, payload=payload)
    try:
        yield op_id
    except Exception as e:
        fail(settings, op_id, e)
        raise
    else:
        finish(settings, op_id)


def unfinished(settings: dict, limit: int = 100) -> list[dict[str, Any]]:
    with db(settings, readonly=True) as con:
        rows = con.execute(
            """
            SELECT * FROM operation_journal
            WHERE status='running'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]
