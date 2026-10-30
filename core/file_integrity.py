"""Cooperative presence, byte-integrity, and verified-recovery maintenance."""
from __future__ import annotations

from collections import Counter
import hashlib
import logging
import os
from pathlib import Path
import re
import time
from typing import Callable, Any
from urllib.parse import parse_qs, urlparse
import uuid

from core.database.connection import connect, write_txn
from core.maintenance_coordinator import MaintenanceCancelled, get_maintenance_coordinator
from core.storage_roots import (
    configured_output_root,
    is_managed_output_path,
    managed_output_root_state,
    probe_media_path,
)

_log = logging.getLogger("local_booru.file_integrity")
PRESENCE_STATUSES = ("present", "missing", "offline_root", "path_error")
HASH_STATUSES = ("ok", "mismatch", "unreadable", "deferred")
RECOVERY_STATUSES = ("recovered", "failed", "blocked", "no_source", "cancelled")
_MD5_RE = re.compile(r"^[0-9a-f]{32}$")


def _emit(progress: Callable[[str], None] | None, text: str) -> None:
    if progress:
        progress(str(text))


def _start_run(settings: dict, total: int, *, kind: str = "presence") -> int:
    now = int(time.time())
    with write_txn(settings) as con:
        cur = con.execute(
            """
            INSERT INTO file_integrity_runs(kind,status,started_at,total)
            VALUES(?,?,?,?)
            """,
            (str(kind or "presence"), "running", now, int(total)),
        )
        return int(cur.lastrowid)


def _finish_run(settings: dict, run_id: int, result: dict, status: str, error: str = "") -> None:
    kind = str(result.get("kind") or "presence")
    with write_txn(settings) as con:
        con.execute(
            """
            UPDATE file_integrity_runs
            SET status=?,finished_at=?,total=?,checked=?,present=?,missing=?,offline_root=?,
                path_error=?,stale_skipped=?,last_image_id=?,last_error=?,hash_ok=?,
                hash_mismatch=?,hash_unreadable=?,hash_deferred=?,bytes_read=?,recovered=?,
                recovery_failed=?
            WHERE id=?
            """,
            (
                str(status), int(time.time()), int(result.get("total") or 0),
                int(result.get("checked") or 0), int(result.get("present") or 0),
                int(result.get("missing") or 0), int(result.get("offline_root") or 0),
                int(result.get("path_error") or 0), int(result.get("stale_skipped") or 0),
                int(result.get("last_image_id") or 0), str(error or "")[:4000],
                int(result.get("hash_ok") or 0), int(result.get("hash_mismatch") or 0),
                int(result.get("hash_unreadable") or 0), int(result.get("hash_deferred") or 0),
                int(result.get("bytes_read") or 0), int(result.get("recovered") or 0),
                int(result.get("recovery_failed") or 0), int(run_id),
            ),
        )
        con.execute(
            """
            DELETE FROM file_integrity_runs
            WHERE kind=? AND id NOT IN (
                SELECT id FROM file_integrity_runs WHERE kind=? ORDER BY id DESC LIMIT 200
            )
            """,
            (kind, kind),
        )


def _store_chunk(settings: dict, run_id: int, rows: list[tuple], result: dict) -> int:
    """Persist only rows whose live DB path still matches the checked path."""
    accepted = 0
    stale_statuses: Counter[str] = Counter()
    now = int(time.time())
    with write_txn(settings) as con:
        for image_id, checked_path, probe in rows:
            current = con.execute(
                "SELECT 1 FROM images WHERE id=? AND deleted=0 AND path=?",
                (int(image_id), str(checked_path)),
            ).fetchone()
            if not current:
                result["stale_skipped"] += 1
                stale_statuses[str(probe.get("status") or "path_error")] += 1
                continue
            con.execute(
                """
                INSERT INTO image_file_integrity(
                    image_id,checked_path,presence_status,root_kind,root_path,root_status,
                    last_presence_check,last_error,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(image_id) DO UPDATE SET
                    checked_path=excluded.checked_path,
                    presence_status=excluded.presence_status,
                    root_kind=excluded.root_kind,
                    root_path=excluded.root_path,
                    root_status=excluded.root_status,
                    last_presence_check=excluded.last_presence_check,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (
                    int(image_id), str(checked_path), str(probe.get("status") or "path_error"),
                    str(probe.get("root_kind") or ""), str(probe.get("root_path") or ""),
                    str(probe.get("root_status") or "unknown"), now,
                    str(probe.get("error") or "")[:2000], now,
                ),
            )
            accepted += 1
        for status, count in stale_statuses.items():
            if status in PRESENCE_STATUSES:
                result[status] = max(0, int(result.get(status) or 0) - int(count))
        con.execute(
            """
            UPDATE file_integrity_runs
            SET checked=?,present=?,missing=?,offline_root=?,path_error=?,stale_skipped=?,last_image_id=?
            WHERE id=?
            """,
            (
                int(result["checked"]), int(result["present"]), int(result["missing"]),
                int(result["offline_root"]), int(result["path_error"]),
                int(result["stale_skipped"]), int(result["last_image_id"]), int(run_id),
            ),
        )
    return accepted


def run_presence_check(
    settings: dict,
    *,
    mode: str = "forced",
    chunk_size: int = 250,
    max_files: int | None = None,
    progress: Callable[[str], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Check live image paths without deleting or deactivating any DB row."""
    settings = dict(settings or {})
    chunk_size = max(25, min(2000, int(chunk_size or 250)))
    coordinator = get_maintenance_coordinator()
    source_con = None
    run_id = 0
    started = time.monotonic()
    result: dict[str, Any] = {
        "kind": "presence",
        "ok": False,
        "cancelled": False,
        "total": 0,
        "checked": 0,
        "present": 0,
        "missing": 0,
        "offline_root": 0,
        "path_error": 0,
        "stale_skipped": 0,
        "last_image_id": 0,
        "root_states": {},
        "run_id": 0,
    }
    try:
        with coordinator.maintenance_job(
            "integrity_presence", stop_check=stop_check, progress=progress
        ):
            coordinator.wait_for_permission(
                settings, task_kind="integrity_presence", mode=mode,
                stop_check=stop_check, progress=progress,
                min_idle_seconds=float(settings.get("maintenance_idle_seconds", 5) or 5),
            )
            source_con = connect(settings, readonly=True)
            total_row = source_con.execute("SELECT COUNT(*) FROM images WHERE deleted=0").fetchone()
            total = int(total_row[0] or 0) if total_row else 0
            if max_files is not None:
                total = min(total, max(0, int(max_files)))
            result["total"] = total
            run_id = _start_run(settings, total)
            result["run_id"] = run_id
            _emit(progress, f"Presence: найдено живых записей {total}")

            last_id = 0
            remaining = total
            while remaining > 0:
                coordinator.wait_for_permission(
                    settings, task_kind="integrity_presence", mode=mode,
                    stop_check=stop_check, progress=progress,
                    min_idle_seconds=float(settings.get("maintenance_idle_seconds", 5) or 5),
                )
                limit = min(chunk_size, remaining)
                rows = source_con.execute(
                    "SELECT id,path FROM images WHERE deleted=0 AND id>? ORDER BY id LIMIT ?",
                    (int(last_id), int(limit)),
                ).fetchall()
                if not rows:
                    break

                root_cache = {}
                staged: list[tuple] = []
                chunk_counts: Counter[str] = Counter()
                for pos, row in enumerate(rows, start=1):
                    if stop_check and stop_check():
                        raise MaintenanceCancelled("integrity_presence: cancelled")
                    image_id = int(row["id"])
                    path = str(row["path"] or "")
                    probe = probe_media_path(settings, path, root_cache=root_cache)
                    status = str(probe.get("status") or "path_error")
                    if status not in PRESENCE_STATUSES:
                        status = "path_error"
                        probe["status"] = status
                        probe["error"] = str(probe.get("error") or "invalid_presence_status")
                    chunk_counts[status] += 1
                    staged.append((image_id, path, probe))
                    last_id = image_id
                    if pos % 25 == 0 and stop_check and stop_check():
                        raise MaintenanceCancelled("integrity_presence: cancelled")

                result["checked"] += len(staged)
                for key in PRESENCE_STATUSES:
                    result[key] += int(chunk_counts.get(key, 0))
                result["last_image_id"] = int(last_id)
                for state in root_cache.values():
                    result["root_states"][str(state.root)] = state.to_dict()
                _store_chunk(settings, run_id, staged, result)
                remaining = max(0, total - int(result["checked"]))
                _emit(
                    progress,
                    "Presence: "
                    f"{result['checked']}/{total}; present={result['present']}; "
                    f"missing={result['missing']}; offline_root={result['offline_root']}; "
                    f"path_error={result['path_error']}",
                )

            result["ok"] = True
            result["duration_seconds"] = round(max(0.0, time.monotonic() - started), 3)
            _finish_run(settings, run_id, result, "completed")
            _emit(progress, "Presence: готово; записи библиотеки и файлы не изменялись")
            return result
    except MaintenanceCancelled as exc:
        result["cancelled"] = True
        result["duration_seconds"] = round(max(0.0, time.monotonic() - started), 3)
        if run_id:
            _finish_run(settings, run_id, result, "cancelled", str(exc))
        return result
    except Exception as exc:
        result["duration_seconds"] = round(max(0.0, time.monotonic() - started), 3)
        if run_id:
            try:
                _finish_run(settings, run_id, result, "failed", f"{type(exc).__name__}: {exc}")
            except Exception:
                pass
        _log.exception("Presence integrity failed")
        raise
    finally:
        if source_con is not None:
            try:
                source_con.close()
            except Exception:
                pass


def get_presence_summary(settings: dict) -> dict[str, Any]:
    con = connect(settings, readonly=True)
    try:
        row = con.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN f.image_id IS NULL OR f.checked_path<>i.path THEN 1 ELSE 0 END) AS unknown,
                SUM(CASE WHEN f.checked_path=i.path AND f.presence_status='present' THEN 1 ELSE 0 END) AS present,
                SUM(CASE WHEN f.checked_path=i.path AND f.presence_status='missing' THEN 1 ELSE 0 END) AS missing,
                SUM(CASE WHEN f.checked_path=i.path AND f.presence_status='offline_root' THEN 1 ELSE 0 END) AS offline_root,
                SUM(CASE WHEN f.checked_path=i.path AND f.presence_status='path_error' THEN 1 ELSE 0 END) AS path_error,
                MAX(CASE WHEN f.checked_path=i.path THEN f.last_presence_check ELSE 0 END) AS last_checked_at,
                SUM(CASE WHEN f.image_id IS NULL OR f.checked_path<>i.path
                              OR lower(COALESCE(f.expected_md5,''))<>lower(COALESCE(i.hash_md5,''))
                              OR COALESCE(f.hash_status,'unknown') NOT IN ('ok','mismatch','unreadable','deferred')
                              OR COALESCE(f.last_hash_check,0)=0
                         THEN 1 ELSE 0 END) AS hash_unknown,
                SUM(CASE WHEN f.checked_path=i.path AND lower(f.expected_md5)=lower(i.hash_md5)
                              AND f.hash_status='ok' THEN 1 ELSE 0 END) AS hash_ok,
                SUM(CASE WHEN f.checked_path=i.path AND lower(f.expected_md5)=lower(i.hash_md5)
                              AND f.hash_status='mismatch' THEN 1 ELSE 0 END) AS hash_mismatch,
                SUM(CASE WHEN f.checked_path=i.path AND lower(f.expected_md5)=lower(i.hash_md5)
                              AND f.hash_status='unreadable' THEN 1 ELSE 0 END) AS hash_unreadable,
                SUM(CASE WHEN f.checked_path=i.path AND lower(f.expected_md5)=lower(i.hash_md5)
                              AND f.hash_status='deferred' THEN 1 ELSE 0 END) AS hash_deferred,
                MAX(CASE WHEN f.checked_path=i.path AND lower(f.expected_md5)=lower(i.hash_md5)
                         THEN f.last_hash_check ELSE 0 END) AS last_hash_check
            FROM images i
            LEFT JOIN image_file_integrity f ON f.image_id=i.id
            WHERE i.deleted=0
            """
        ).fetchone()
        summary = {k: int(row[k] or 0) for k in row.keys()} if row else {
            "total": 0, "unknown": 0, "present": 0, "missing": 0,
            "offline_root": 0, "path_error": 0, "last_checked_at": 0,
            "hash_unknown": 0, "hash_ok": 0, "hash_mismatch": 0,
            "hash_unreadable": 0, "hash_deferred": 0, "last_hash_check": 0,
        }
        run = con.execute(
            """
            SELECT id,status,started_at,finished_at,total,checked,present,missing,offline_root,
                   path_error,stale_skipped,last_image_id,last_error
            FROM file_integrity_runs WHERE kind='presence' ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        summary["last_run"] = dict(run) if run else {}
        hash_run = con.execute(
            """
            SELECT id,status,started_at,finished_at,total,checked,hash_ok,hash_mismatch,
                   hash_unreadable,hash_deferred,bytes_read,stale_skipped,last_image_id,last_error
            FROM file_integrity_runs WHERE kind='hash' ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        recovery_run = con.execute(
            """
            SELECT id,status,started_at,finished_at,total,checked,recovered,recovery_failed,
                   stale_skipped,last_image_id,last_error
            FROM file_integrity_runs WHERE kind='recovery' ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        summary["last_hash_run"] = dict(hash_run) if hash_run else {}
        summary["last_recovery_run"] = dict(recovery_run) if recovery_run else {}
        recovery_statuses = con.execute(
            """
            SELECT f.recovery_status,COUNT(*) AS files,MAX(f.last_recovery_attempt) AS last_attempt
            FROM image_file_integrity f
            JOIN images i ON i.id=f.image_id AND i.deleted=0 AND i.path=f.checked_path
            GROUP BY f.recovery_status ORDER BY f.recovery_status
            """
        ).fetchall()
        summary["recovery_statuses"] = [dict(r) for r in recovery_statuses]
        roots = con.execute(
            """
            SELECT f.root_kind,f.root_path,f.root_status,f.presence_status,COUNT(*) AS files,
                   MAX(f.last_presence_check) AS last_checked_at
            FROM image_file_integrity f
            JOIN images i ON i.id=f.image_id AND i.deleted=0 AND i.path=f.checked_path
            GROUP BY f.root_kind,f.root_path,f.root_status,f.presence_status
            ORDER BY f.root_kind,f.root_path,f.presence_status
            """
        ).fetchall()
        summary["roots"] = [dict(r) for r in roots]
        return summary
    finally:
        con.close()


def _valid_md5(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if _MD5_RE.fullmatch(text) else ""


def _signature(path: Path) -> tuple[int, int]:
    st = path.stat()
    return int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))


def _hash_path_cooperative(
    path: Path,
    *,
    expected_md5: str,
    settings: dict,
    coordinator,
    mode: str,
    stop_check=None,
    read_chunk_bytes: int = 4 * 1024 * 1024,
    permission_interval_bytes: int = 64 * 1024 * 1024,
) -> dict[str, Any]:
    """Hash one file without holding SQLite locks or monopolising foreground I/O."""
    expected = _valid_md5(expected_md5)
    try:
        before_size, before_mtime = _signature(path)
    except Exception as exc:
        return {
            "hash_status": "unreadable", "expected_md5": expected, "observed_md5": "",
            "bytes_read": 0, "size_bytes": 0, "mtime_ns": 0,
            "error": f"stat_before:{type(exc).__name__}: {exc}",
        }

    digest = hashlib.md5()
    total = 0
    next_permission = max(read_chunk_bytes, int(permission_interval_bytes or 0))
    try:
        with path.open("rb") as fh:
            while True:
                if stop_check and stop_check():
                    raise MaintenanceCancelled("integrity_hash: cancelled")
                chunk = fh.read(max(64 * 1024, int(read_chunk_bytes or 0)))
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
                if total >= next_permission:
                    decision = coordinator.can_run(
                        settings,
                        mode=mode,
                        min_idle_seconds=float(settings.get("maintenance_idle_seconds", 5) or 5),
                    )
                    if not decision.allowed:
                        return {
                            "hash_status": "deferred", "expected_md5": expected,
                            "observed_md5": "", "bytes_read": total,
                            "size_bytes": before_size, "mtime_ns": before_mtime,
                            "error": f"foreground_activity:{decision.reason}",
                        }
                    next_permission += max(read_chunk_bytes, int(permission_interval_bytes or 0))
        after_size, after_mtime = _signature(path)
        if (after_size, after_mtime) != (before_size, before_mtime):
            return {
                "hash_status": "deferred", "expected_md5": expected, "observed_md5": "",
                "bytes_read": total, "size_bytes": after_size, "mtime_ns": after_mtime,
                "error": "file_changed_during_hash",
            }
        observed = digest.hexdigest().lower()
        return {
            "hash_status": "ok" if expected and observed == expected else "mismatch",
            "expected_md5": expected, "observed_md5": observed, "bytes_read": total,
            "size_bytes": after_size, "mtime_ns": after_mtime, "error": "",
        }
    except MaintenanceCancelled:
        raise
    except Exception as exc:
        return {
            "hash_status": "unreadable", "expected_md5": expected, "observed_md5": "",
            "bytes_read": total, "size_bytes": before_size, "mtime_ns": before_mtime,
            "error": f"read:{type(exc).__name__}: {exc}",
        }


def _store_hash_chunk(settings: dict, run_id: int, rows: list[tuple], result: dict) -> int:
    """Persist hash results only while path, canonical MD5, and file signature remain current."""
    accepted = 0
    stale_counts: Counter[str] = Counter()
    now = int(time.time())
    with write_txn(settings) as con:
        for image_id, checked_path, expected_md5, probe, hashed in rows:
            current = con.execute(
                "SELECT hash_md5 FROM images WHERE id=? AND deleted=0 AND path=?",
                (int(image_id), str(checked_path)),
            ).fetchone()
            status = str(hashed.get("hash_status") or "unreadable")
            if not current or _valid_md5(current["hash_md5"]) != _valid_md5(expected_md5):
                result["stale_skipped"] += 1
                stale_counts[status] += 1
                continue
            if status in {"ok", "mismatch"}:
                try:
                    current_signature = _signature(Path(checked_path))
                except Exception:
                    current_signature = (-1, -1)
                expected_signature = (
                    int(hashed.get("size_bytes") or 0), int(hashed.get("mtime_ns") or 0)
                )
                if current_signature != expected_signature:
                    result["stale_skipped"] += 1
                    stale_counts[status] += 1
                    continue
            con.execute(
                """
                INSERT INTO image_file_integrity(
                    image_id,checked_path,presence_status,root_kind,root_path,root_status,
                    last_presence_check,last_error,updated_at,hash_status,expected_md5,
                    observed_md5,last_hash_check,hash_size_bytes,hash_mtime_ns,hash_error
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(image_id) DO UPDATE SET
                    checked_path=excluded.checked_path,
                    presence_status=excluded.presence_status,
                    root_kind=excluded.root_kind,
                    root_path=excluded.root_path,
                    root_status=excluded.root_status,
                    last_presence_check=excluded.last_presence_check,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at,
                    hash_status=excluded.hash_status,
                    expected_md5=excluded.expected_md5,
                    observed_md5=excluded.observed_md5,
                    last_hash_check=excluded.last_hash_check,
                    hash_size_bytes=excluded.hash_size_bytes,
                    hash_mtime_ns=excluded.hash_mtime_ns,
                    hash_error=excluded.hash_error
                """,
                (
                    int(image_id), str(checked_path), str(probe.get("status") or "path_error"),
                    str(probe.get("root_kind") or ""), str(probe.get("root_path") or ""),
                    str(probe.get("root_status") or "unknown"), now,
                    str(probe.get("error") or "")[:2000], now, status,
                    _valid_md5(expected_md5), str(hashed.get("observed_md5") or ""), now,
                    int(hashed.get("size_bytes") or 0), int(hashed.get("mtime_ns") or 0),
                    str(hashed.get("error") or "")[:2000],
                ),
            )
            accepted += 1
        for status, count in stale_counts.items():
            key = f"hash_{status}"
            if key in result:
                result[key] = max(0, int(result.get(key) or 0) - int(count))
        con.execute(
            """
            UPDATE file_integrity_runs
            SET checked=?,hash_ok=?,hash_mismatch=?,hash_unreadable=?,hash_deferred=?,
                bytes_read=?,stale_skipped=?,last_image_id=?
            WHERE id=?
            """,
            (
                int(result["checked"]), int(result["hash_ok"]), int(result["hash_mismatch"]),
                int(result["hash_unreadable"]), int(result["hash_deferred"]),
                int(result["bytes_read"]), int(result["stale_skipped"]),
                int(result["last_image_id"]), int(run_id),
            ),
        )
    return accepted


def run_hash_verification(
    settings: dict,
    *,
    mode: str = "idle",
    chunk_size: int = 25,
    max_files: int | None = None,
    progress: Callable[[str], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Read live media bytes and compare them with canonical ``images.hash_md5``.

    The operation is read-only for media.  Mismatches are recorded for review;
    canonical hashes, paths, and ``images.deleted`` are never modified.
    """
    settings = dict(settings or {})
    chunk_size = max(1, min(250, int(chunk_size or 25)))
    coordinator = get_maintenance_coordinator()
    source_con = None
    run_id = 0
    started = time.monotonic()
    result: dict[str, Any] = {
        "kind": "hash", "ok": False, "cancelled": False, "total": 0, "checked": 0,
        "hash_ok": 0, "hash_mismatch": 0, "hash_unreadable": 0, "hash_deferred": 0,
        "bytes_read": 0, "stale_skipped": 0, "last_image_id": 0, "run_id": 0,
    }
    try:
        with coordinator.maintenance_job("integrity_hash", stop_check=stop_check, progress=progress):
            coordinator.wait_for_permission(
                settings, task_kind="integrity_hash", mode=mode, stop_check=stop_check,
                progress=progress,
                min_idle_seconds=float(settings.get("maintenance_idle_seconds", 5) or 5),
            )
            source_con = connect(settings, readonly=True)
            total_row = source_con.execute(
                "SELECT COUNT(*) FROM images WHERE deleted=0 AND length(lower(trim(hash_md5)))=32 AND hash_md5 NOT GLOB '*[^0-9A-Fa-f]*'"
            ).fetchone()
            total = int(total_row[0] or 0) if total_row else 0
            if max_files is not None:
                total = min(total, max(0, int(max_files)))
            result["total"] = total
            run_id = _start_run(settings, total, kind="hash")
            result["run_id"] = run_id
            _emit(progress, f"Hash integrity: живых записей с MD5 {total}; чтение только в idle-чанках")

            last_id = 0
            remaining = total
            while remaining > 0:
                coordinator.wait_for_permission(
                    settings, task_kind="integrity_hash", mode=mode, stop_check=stop_check,
                    progress=progress,
                    min_idle_seconds=float(settings.get("maintenance_idle_seconds", 5) or 5),
                )
                rows = source_con.execute(
                    """
                    SELECT id,path,lower(trim(hash_md5)) AS hash_md5
                    FROM images
                    WHERE deleted=0 AND length(lower(trim(hash_md5)))=32 AND hash_md5 NOT GLOB '*[^0-9A-Fa-f]*' AND id>?
                    ORDER BY id LIMIT ?
                    """,
                    (int(last_id), min(chunk_size, remaining)),
                ).fetchall()
                if not rows:
                    break
                root_cache = {}
                staged: list[tuple] = []
                for row in rows:
                    if stop_check and stop_check():
                        raise MaintenanceCancelled("integrity_hash: cancelled")
                    image_id = int(row["id"])
                    path = str(row["path"] or "")
                    expected = _valid_md5(row["hash_md5"])
                    probe = probe_media_path(settings, path, root_cache=root_cache)
                    presence = str(probe.get("status") or "path_error")
                    if presence == "present":
                        hashed = _hash_path_cooperative(
                            Path(path), expected_md5=expected, settings=settings,
                            coordinator=coordinator, mode=mode, stop_check=stop_check,
                        )
                    elif presence == "path_error":
                        hashed = {
                            "hash_status": "unreadable", "expected_md5": expected,
                            "observed_md5": "", "bytes_read": 0, "size_bytes": 0,
                            "mtime_ns": 0, "error": str(probe.get("error") or "path_error"),
                        }
                    else:
                        hashed = {
                            "hash_status": "deferred", "expected_md5": expected,
                            "observed_md5": "", "bytes_read": 0, "size_bytes": 0,
                            "mtime_ns": 0, "error": f"presence:{presence}",
                        }
                    status = str(hashed["hash_status"])
                    result[f"hash_{status}"] += 1
                    result["bytes_read"] += int(hashed.get("bytes_read") or 0)
                    result["checked"] += 1
                    result["last_image_id"] = image_id
                    last_id = image_id
                    staged.append((image_id, path, expected, probe, hashed))
                _store_hash_chunk(settings, run_id, staged, result)
                remaining = max(0, total - int(result["checked"]))
                _emit(
                    progress,
                    f"Hash integrity: {result['checked']}/{total}; ok={result['hash_ok']}; "
                    f"mismatch={result['hash_mismatch']}; unreadable={result['hash_unreadable']}; "
                    f"deferred={result['hash_deferred']}; bytes={result['bytes_read']}",
                )

            result["ok"] = True
            result["duration_seconds"] = round(max(0.0, time.monotonic() - started), 3)
            _finish_run(settings, run_id, result, "completed")
            _emit(progress, "Hash integrity: готово; canonical MD5 и медиа не изменялись")
            return result
    except MaintenanceCancelled as exc:
        result["cancelled"] = True
        result["duration_seconds"] = round(max(0.0, time.monotonic() - started), 3)
        if run_id:
            _finish_run(settings, run_id, result, "cancelled", str(exc))
        return result
    except Exception as exc:
        result["duration_seconds"] = round(max(0.0, time.monotonic() - started), 3)
        if run_id:
            try:
                _finish_run(settings, run_id, result, "failed", f"{type(exc).__name__}: {exc}")
            except Exception:
                pass
        _log.exception("Hash integrity failed")
        raise
    finally:
        if source_con is not None:
            try:
                source_con.close()
            except Exception:
                pass


def _looks_like_direct_media_url(value: str) -> bool:
    try:
        path = urlparse(str(value or "")).path.lower()
    except Exception:
        return False
    return Path(path).suffix in {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".webm", ".mp4", ".mkv", ".avif"
    }


def _post_id_from_url(value: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
        query = parse_qs(parsed.query)
        for key in ("id", "post_id"):
            candidate = str((query.get(key) or [""])[0]).strip()
            if candidate.isdigit():
                return candidate
        match = re.search(r"/(?:posts?|post/show)/(\d+)(?:/|$)", parsed.path)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


def _recovery_sources(settings: dict, image_id: int) -> list[dict[str, Any]]:
    con = connect(settings, readonly=True)
    try:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(url, host="", kind="post", rank=50):
            text = str(url or "").strip()
            if not text or text in seen:
                return
            try:
                from core.network import is_permanently_disabled_url
                if is_permanently_disabled_url(text):
                    return
            except Exception:
                pass
            seen.add(text)
            out.append({"url": text, "host": str(host or ""), "kind": str(kind), "rank": int(rank)})

        raw = con.execute(
            "SELECT site,post_url,file_url FROM raw_metadata WHERE image_id=?", (int(image_id),)
        ).fetchone()
        if raw:
            add(raw["file_url"], raw["site"], "direct", 0)
            add(raw["post_url"], raw["site"], "post", 10)
        aliases = con.execute(
            "SELECT site,source_url FROM image_site_md5_aliases WHERE image_id=? ORDER BY observed_at DESC",
            (int(image_id),),
        ).fetchall()
        for row in aliases:
            add(row["source_url"], row["site"], "post", 20)
        sources = con.execute(
            """
            SELECT s.host,s.url FROM image_sources x JOIN sources s ON s.id=x.source_id
            WHERE x.image_id=? ORDER BY s.id
            """,
            (int(image_id),),
        ).fetchall()
        for row in sources:
            add(row["url"], row["host"], "direct" if _looks_like_direct_media_url(row["url"]) else "post", 30)
        return sorted(out, key=lambda item: (item["rank"], item["url"]))
    finally:
        con.close()


def _resolve_recovery_urls(settings: dict, source: dict[str, Any], *, progress=None) -> list[str]:
    """Resolve a recorded direct URL or post URL to original-media candidates."""
    url = str(source.get("url") or "").strip()
    host = str(source.get("host") or "").strip().lower().replace("www.", "")
    if not host:
        try:
            host = (urlparse(url).hostname or "").lower().replace("www.", "")
        except Exception:
            host = ""
    if str(source.get("kind") or "") == "direct" or _looks_like_direct_media_url(url):
        return [url]

    resolved: list[str] = []
    post_id = _post_id_from_url(url)
    if host and post_id:
        try:
            from core.downloader_utils import fetch_posts_for_query, _file_url_variants, _post_id
            posts, _highest = fetch_posts_for_query(
                host, f"id:{post_id}", settings, max_pages=1, run_mode="all",
                log=lambda msg: _emit(progress, f"Recovery metadata: {msg}"),
            )
            post = next((item for item in posts if str(_post_id(item)) == post_id), posts[0] if posts else {})
            for media_url in _file_url_variants(post or {}, host):
                if media_url and media_url not in resolved:
                    resolved.append(media_url)
        except Exception as exc:
            _emit(progress, f"Recovery metadata warning [{host}]: {type(exc).__name__}: {exc}")
    if resolved:
        return resolved
    try:
        from core.net import http_request
        from core.tagger import Tagger
        tagger = Tagger(dict(settings or {}), lambda msg: _emit(progress, f"Recovery page: {msg}"))
        session = tagger.session_for_host(host or url)
        response = http_request(
            session, "GET", url, timeout=tagger.timeout,
            headers={"Accept": "text/html,application/xhtml+xml,*/*"},
        )
        original = str(tagger._html_original_media_url(response.text or "", url) or "").strip()
        if original and original not in resolved:
            from core.network import is_permanently_disabled_url
            if not is_permanently_disabled_url(original):
                resolved.append(original)
    except Exception as exc:
        _emit(progress, f"Recovery page warning [{host or 'unknown'}]: {type(exc).__name__}: {exc}")
    return resolved


def _start_recovery_attempt(settings: dict, image_id: int, expected: str, target: str) -> int:
    """Claim a still-current recovery candidate under the DB write lock.

    A long-running presence/hash pass may have become stale before recovery
    starts.  Do not overwrite a newer integrity row with ``running`` and do not
    create an attempt unless path, canonical MD5, and candidate status still
    match the snapshot selected by the caller.
    """
    now = int(time.time())
    expected = _valid_md5(expected)
    with write_txn(settings) as con:
        current = con.execute(
            """
            SELECT 1
            FROM images i
            JOIN image_file_integrity f ON f.image_id=i.id AND f.checked_path=i.path
            WHERE i.id=? AND i.deleted=0 AND i.path=?
              AND lower(trim(i.hash_md5))=?
              AND (f.presence_status='missing' OR f.hash_status='mismatch')
            """,
            (int(image_id), str(target), expected),
        ).fetchone()
        if not current:
            return 0
        cur = con.execute(
            """
            INSERT INTO file_recovery_attempts(
                image_id,expected_md5,status,target_path,started_at
            ) VALUES(?,?, 'running', ?, ?)
            """,
            (int(image_id), expected, str(target), now),
        )
        con.execute(
            """
            UPDATE image_file_integrity
            SET recovery_status='running',last_recovery_attempt=?,recovery_error='',updated_at=?
            WHERE image_id=? AND checked_path=?
            """,
            (now, now, int(image_id), str(target)),
        )
        return int(cur.lastrowid)

def _finish_recovery_attempt(
    settings: dict,
    attempt_id: int,
    image_id: int,
    *,
    status: str,
    source_url: str = "",
    staging_path: str = "",
    quarantine_path: str = "",
    bytes_downloaded: int = 0,
    observed_md5: str = "",
    error: str = "",
) -> None:
    now = int(time.time())
    recovery_status = status if status in RECOVERY_STATUSES else "failed"
    with write_txn(settings) as con:
        attempt = con.execute(
            "SELECT target_path FROM file_recovery_attempts WHERE id=? AND image_id=?",
            (int(attempt_id), int(image_id)),
        ).fetchone()
        target_path = str(attempt["target_path"] or "") if attempt else ""
        con.execute(
            """
            UPDATE file_recovery_attempts
            SET source_url=?,status=?,staging_path=?,quarantine_path=?,bytes_downloaded=?,
                observed_md5=?,finished_at=?,last_error=?
            WHERE id=? AND image_id=?
            """,
            (
                str(source_url), recovery_status, str(staging_path), str(quarantine_path),
                int(bytes_downloaded), str(observed_md5), now, str(error)[:4000],
                int(attempt_id), int(image_id),
            ),
        )
        # A path change during network work makes this attempt historical.  Its
        # audit row is still closed, but the current integrity state is left
        # untouched instead of attaching a stale failure to a new path.
        if target_path:
            con.execute(
                """
                UPDATE image_file_integrity
                SET recovery_status=?,recovery_source_url=?,last_recovery_attempt=?,
                    recovery_error=?,updated_at=?
                WHERE image_id=? AND checked_path=?
                """,
                (
                    recovery_status, str(source_url), now, str(error)[:2000], now,
                    int(image_id), target_path,
                ),
            )


def _update_recovery_run_progress(settings: dict, run_id: int, result: dict) -> None:
    with write_txn(settings) as con:
        con.execute(
            """
            UPDATE file_integrity_runs SET checked=?,recovered=?,recovery_failed=?,
                stale_skipped=?,last_image_id=? WHERE id=?
            """,
            (
                int(result.get("checked") or 0), int(result.get("recovered") or 0),
                int(result.get("recovery_failed") or 0),
                int(result.get("stale_skipped") or 0),
                int(result.get("last_image_id") or 0), int(run_id),
            ),
        )

def _install_staged_recovery(
    settings: dict,
    *,
    image_id: int,
    target_path: str,
    expected_md5: str,
    expected_observed_md5: str = "",
    staged_path: str,
    source_url: str,
    attempt_id: int,
    bytes_downloaded: int = 0,
) -> dict[str, Any]:
    """Install verified bytes without overwriting a file in place.

    The output root is classified without creating it.  Existing mismatched
    bytes are moved to quarantine first, and both filesystem actions are rolled
    back if the SQLite transaction cannot commit.
    """
    from core.media_utils import file_md5

    target = Path(target_path)
    staged = Path(staged_path)
    expected = _valid_md5(expected_md5)
    expected_observed = _valid_md5(expected_observed_md5)
    output_root = Path(configured_output_root(settings))
    root_state = managed_output_root_state(settings)
    if not root_state.available:
        return {
            "ok": False, "status": "blocked",
            "error": f"managed_output_unavailable:{root_state.status}:{root_state.detail}",
        }
    if not expected or not staged.is_file() or file_md5(staged).lower() != expected:
        return {"ok": False, "status": "failed", "error": "staging_verification_failed"}
    if not is_managed_output_path(settings, target) or not is_managed_output_path(settings, staged):
        return {"ok": False, "status": "blocked", "error": "target_or_staging_outside_managed_output"}

    existed_before = target.is_file()
    before_signature = None
    before_md5 = ""
    if existed_before:
        try:
            before_signature = _signature(target)
            before_md5 = file_md5(target).lower()
        except Exception as exc:
            return {"ok": False, "status": "failed", "error": f"target_precheck:{exc}"}
        if before_md5 != expected:
            if not expected_observed:
                return {
                    "ok": False, "status": "failed",
                    "error": "target_appeared_after_missing_check",
                    "observed_md5": before_md5,
                }
            if before_md5 != expected_observed:
                return {
                    "ok": False, "status": "failed",
                    "error": "target_bytes_changed_since_hash_check",
                    "observed_md5": before_md5,
                }

    quarantine = None
    installed = False
    moved_old = False
    now = int(time.time())
    try:
        with write_txn(settings) as con:
            row = con.execute(
                "SELECT path,hash_md5 FROM images WHERE id=? AND deleted=0",
                (int(image_id),),
            ).fetchone()
            if not row or str(row["path"] or "") != str(target) or _valid_md5(row["hash_md5"]) != expected:
                raise RuntimeError("library_row_changed_before_recovery")
            fresh_root = managed_output_root_state(settings)
            if not fresh_root.available:
                raise RuntimeError(
                    f"managed_output_unavailable:{fresh_root.status}:{fresh_root.detail}"
                )

            if existed_before:
                if not target.is_file() or _signature(target) != before_signature:
                    raise RuntimeError("target_changed_before_recovery")
                if before_md5 == expected:
                    staged.unlink(missing_ok=True)
                else:
                    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
                    quarantine = output_root / "recovery_quarantine" / stamp / (
                        target.name + f".corrupt_{before_md5[:12] or 'unknown'}"
                    )
                    if not is_managed_output_path(settings, quarantine):
                        raise PermissionError("quarantine_outside_managed_output")
                    quarantine.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(str(target), str(quarantine))
                    moved_old = True
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(str(staged), str(target))
                    installed = True
            else:
                if target.exists():
                    raise RuntimeError("target_appeared_before_recovery")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(staged), str(target))
                installed = True

            size, mtime = _signature(target)
            con.execute(
                """
                INSERT INTO image_file_integrity(
                    image_id,checked_path,presence_status,root_status,last_presence_check,
                    hash_status,expected_md5,observed_md5,last_hash_check,hash_size_bytes,
                    hash_mtime_ns,hash_error,recovery_status,recovery_source_url,
                    last_recovery_attempt,recovery_error,updated_at
                ) VALUES(?,?,'present','available',?,'ok',?,?,?, ?,?,'','recovered',?,?, '',?)
                ON CONFLICT(image_id) DO UPDATE SET
                    checked_path=excluded.checked_path,presence_status='present',root_status='available',
                    last_presence_check=excluded.last_presence_check,hash_status='ok',
                    expected_md5=excluded.expected_md5,observed_md5=excluded.observed_md5,
                    last_hash_check=excluded.last_hash_check,hash_size_bytes=excluded.hash_size_bytes,
                    hash_mtime_ns=excluded.hash_mtime_ns,hash_error='',recovery_status='recovered',
                    recovery_source_url=excluded.recovery_source_url,
                    last_recovery_attempt=excluded.last_recovery_attempt,recovery_error='',
                    updated_at=excluded.updated_at
                """,
                (
                    int(image_id), str(target), now, expected, expected, now, int(size), int(mtime),
                    str(source_url), now, now,
                ),
            )
            con.execute(
                """
                UPDATE file_recovery_attempts
                SET source_url=?,status='recovered',staging_path=?,target_path=?,quarantine_path=?,
                    bytes_downloaded=?,observed_md5=?,finished_at=?,last_error=''
                WHERE id=? AND image_id=?
                """,
                (
                    str(source_url), str(staged), str(target), str(quarantine or ""),
                    int(bytes_downloaded), expected, now, int(attempt_id), int(image_id),
                ),
            )
        return {"ok": True, "status": "recovered", "quarantine_path": str(quarantine or "")}
    except Exception as exc:
        # Restore the exact pre-operation filesystem state whenever installation
        # or the SQLite commit fails.  cache/status can be regenerated, bytes cannot.
        try:
            if installed and target.exists():
                os.replace(str(target), str(staged))
            if moved_old and quarantine is not None and quarantine.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(quarantine), str(target))
        except Exception as rollback_exc:
            return {
                "ok": False, "status": "failed",
                "error": f"{type(exc).__name__}: {exc}; rollback_failed:{rollback_exc}",
                "quarantine_path": str(quarantine or ""),
            }
        return {
            "ok": False, "status": "failed", "error": f"{type(exc).__name__}: {exc}",
            "quarantine_path": str(quarantine or ""),
        }

def run_verified_recovery(
    settings: dict,
    *,
    image_ids: list[int] | tuple[int, ...] | None = None,
    max_files: int | None = None,
    mode: str = "idle",
    progress: Callable[[str], None] | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Restore confirmed missing/mismatched managed media through verified staging."""
    settings = dict(settings or {})
    coordinator = get_maintenance_coordinator()
    started = time.monotonic()
    run_id = 0
    active_attempt_id = 0
    active_image_id = 0
    active_stage = ""
    active_source = ""
    active_quarantine = ""
    active_bytes = 0
    active_observed = ""
    result: dict[str, Any] = {
        "kind": "recovery", "ok": False, "cancelled": False, "total": 0, "checked": 0,
        "recovered": 0, "recovery_failed": 0, "blocked": 0, "no_source": 0,
        "stale_skipped": 0, "last_image_id": 0, "run_id": 0,
    }
    selected = sorted({int(value) for value in (image_ids or []) if int(value) > 0})

    def update_progress() -> None:
        if run_id:
            _update_recovery_run_progress(settings, run_id, result)
        _emit(
            progress,
            f"Recovery: {result['checked']}/{result['total']}; recovered={result['recovered']}; "
            f"failed={result['recovery_failed']}; blocked={result['blocked']}; "
            f"no_source={result['no_source']}; stale={result['stale_skipped']}",
        )

    try:
        with coordinator.maintenance_job("integrity_recovery", stop_check=stop_check, progress=progress):
            coordinator.wait_for_permission(
                settings, task_kind="integrity_recovery", mode=mode, stop_check=stop_check,
                progress=progress,
                min_idle_seconds=float(settings.get("maintenance_idle_seconds", 5) or 5),
            )
            con = connect(settings, readonly=True)
            try:
                where_ids = ""
                args: list[Any] = []
                if selected:
                    where_ids = f" AND i.id IN ({','.join('?' for _ in selected)})"
                    args.extend(selected)
                rows = con.execute(
                    f"""
                    SELECT i.id,i.path,lower(trim(i.hash_md5)) AS hash_md5,
                           f.presence_status,f.hash_status,f.observed_md5,
                           f.hash_size_bytes,f.hash_mtime_ns
                    FROM images i
                    JOIN image_file_integrity f ON f.image_id=i.id AND f.checked_path=i.path
                    WHERE i.deleted=0 AND length(lower(trim(i.hash_md5)))=32
                      AND i.hash_md5 NOT GLOB '*[^0-9A-Fa-f]*'
                      AND (f.presence_status='missing' OR f.hash_status='mismatch')
                      {where_ids}
                    ORDER BY i.id
                    """,
                    tuple(args),
                ).fetchall()
            finally:
                con.close()
            if max_files is not None:
                rows = rows[:max(0, int(max_files))]
            result["total"] = len(rows)
            run_id = _start_run(settings, len(rows), kind="recovery")
            result["run_id"] = run_id
            _emit(progress, f"Recovery: подтверждённых missing/mismatch кандидатов {len(rows)}")

            from core.downloader_utils import download_media_to_staging

            output_root = Path(configured_output_root(settings))
            for row in rows:
                coordinator.wait_for_permission(
                    settings, task_kind="integrity_recovery", mode=mode, stop_check=stop_check,
                    progress=progress,
                    min_idle_seconds=float(settings.get("maintenance_idle_seconds", 5) or 5),
                )
                if stop_check and stop_check():
                    raise MaintenanceCancelled("integrity_recovery: cancelled")

                image_id = int(row["id"])
                target = str(row["path"] or "")
                expected = _valid_md5(row["hash_md5"])
                mismatch_candidate = str(row["hash_status"] or "") == "mismatch"
                expected_observed = _valid_md5(row["observed_md5"]) if mismatch_candidate else ""
                result["checked"] += 1
                result["last_image_id"] = image_id
                active_attempt_id = 0
                active_image_id = 0
                active_stage = active_source = active_quarantine = ""
                active_bytes = 0
                active_observed = ""

                # Preflight has no filesystem side effects.  It rejects source
                # archive paths and unavailable managed roots before mkdir or HTTP.
                if not is_managed_output_path(settings, target):
                    attempt_id = _start_recovery_attempt(settings, image_id, expected, target)
                    if not attempt_id:
                        result["stale_skipped"] += 1
                    else:
                        result["blocked"] += 1
                        result["recovery_failed"] += 1
                        _finish_recovery_attempt(
                            settings, attempt_id, image_id, status="blocked",
                            error="automatic recovery is allowed only inside managed output",
                        )
                    update_progress()
                    continue

                root_state = managed_output_root_state(settings)
                if not root_state.available:
                    attempt_id = _start_recovery_attempt(settings, image_id, expected, target)
                    if not attempt_id:
                        result["stale_skipped"] += 1
                    else:
                        result["blocked"] += 1
                        result["recovery_failed"] += 1
                        _finish_recovery_attempt(
                            settings, attempt_id, image_id, status="blocked",
                            error=f"managed_output_unavailable:{root_state.status}:{root_state.detail}",
                        )
                    update_progress()
                    continue

                current_probe = probe_media_path(settings, target, root_cache={})
                current_presence = str(current_probe.get("status") or "path_error")
                if current_presence in {"offline_root", "path_error"}:
                    attempt_id = _start_recovery_attempt(settings, image_id, expected, target)
                    if not attempt_id:
                        result["stale_skipped"] += 1
                    else:
                        result["blocked"] += 1
                        result["recovery_failed"] += 1
                        _finish_recovery_attempt(
                            settings, attempt_id, image_id, status="blocked",
                            error=f"target_unavailable:{current_presence}:{current_probe.get('error') or ''}",
                        )
                    update_progress()
                    continue

                if current_presence == "present":
                    if mismatch_candidate:
                        try:
                            current_signature = _signature(Path(target))
                        except Exception:
                            current_signature = (-1, -1)
                        recorded_signature = (
                            int(row["hash_size_bytes"] or 0), int(row["hash_mtime_ns"] or 0)
                        )
                        # Fresh v437 mismatch rows have a signature.  Legacy or
                        # manually repaired rows may not; the install phase still
                        # verifies the current full MD5 against observed_md5 before
                        # any quarantine move, so omitting this cheap prefilter is
                        # safe for those rows.
                        if recorded_signature[1] > 0 and current_signature != recorded_signature:
                            result["stale_skipped"] += 1
                            update_progress()
                            continue
                    else:
                        # A file recorded as missing appeared before recovery.
                        # Never quarantine or replace bytes that were not the
                        # subject of a verified mismatch result.
                        result["stale_skipped"] += 1
                        update_progress()
                        continue

                attempt_id = _start_recovery_attempt(settings, image_id, expected, target)
                if not attempt_id:
                    result["stale_skipped"] += 1
                    update_progress()
                    continue
                active_attempt_id = attempt_id
                active_image_id = image_id

                sources = _recovery_sources(settings, image_id)
                if not sources:
                    result["no_source"] += 1
                    result["recovery_failed"] += 1
                    _finish_recovery_attempt(
                        settings, attempt_id, image_id, status="no_source",
                        error="no recorded source URL",
                    )
                    active_attempt_id = active_image_id = 0
                    update_progress()
                    continue

                recovered = False
                errors: list[str] = []
                for source in sources:
                    coordinator.wait_for_permission(
                        settings, task_kind="integrity_recovery", mode=mode,
                        stop_check=stop_check, progress=progress,
                        min_idle_seconds=float(settings.get("maintenance_idle_seconds", 5) or 5),
                    )
                    if stop_check and stop_check():
                        raise MaintenanceCancelled("integrity_recovery: cancelled")
                    urls = _resolve_recovery_urls(settings, source, progress=progress)
                    if not urls:
                        errors.append(f"no media URL from {source['url']}")
                        continue

                    # Recheck the output root immediately before creating staging.
                    root_state = managed_output_root_state(settings)
                    if not root_state.available:
                        errors.append(
                            f"managed_output_unavailable:{root_state.status}:{root_state.detail}"
                        )
                        break
                    suffix = Path(target).suffix or ".bin"
                    stage = (
                        output_root / "recovery_staging" / str(run_id)
                        / f"{image_id}_{uuid.uuid4().hex}{suffix}"
                    )
                    if not is_managed_output_path(settings, stage):
                        errors.append("staging_outside_managed_output")
                        break
                    stage.parent.mkdir(parents=True, exist_ok=True)
                    active_stage = str(stage)
                    active_source = str(source.get("url") or "")
                    downloaded = download_media_to_staging(
                        str(source.get("host") or source.get("url") or ""), urls, settings, stage,
                        referrer=str(source.get("url") or ""), expected_md5=expected,
                        log=lambda msg: _emit(progress, f"Recovery download: {msg}"),
                        stop_check=stop_check,
                    )
                    active_bytes = int(downloaded.get("bytes") or 0)
                    active_observed = str(downloaded.get("observed_md5") or "")
                    if not downloaded.get("ok"):
                        errors.append(str(downloaded.get("error") or "download failed"))
                        try:
                            stage.unlink(missing_ok=True)
                        except Exception:
                            pass
                        continue

                    installed = _install_staged_recovery(
                        settings, image_id=image_id, target_path=target, expected_md5=expected,
                        expected_observed_md5=expected_observed, staged_path=str(stage),
                        source_url=str(downloaded.get("source_url") or source["url"]),
                        attempt_id=attempt_id, bytes_downloaded=active_bytes,
                    )
                    active_quarantine = str(installed.get("quarantine_path") or "")
                    if installed.get("ok"):
                        result["recovered"] += 1
                        recovered = True
                        active_attempt_id = active_image_id = 0
                        active_stage = active_source = active_quarantine = ""
                        active_bytes = 0
                        active_observed = ""
                        break
                    errors.append(str(installed.get("error") or "install failed"))
                    try:
                        stage.unlink(missing_ok=True)
                    except Exception:
                        pass

                if not recovered:
                    result["recovery_failed"] += 1
                    _finish_recovery_attempt(
                        settings, attempt_id, image_id, status="failed",
                        source_url=active_source, staging_path=active_stage,
                        quarantine_path=active_quarantine, bytes_downloaded=active_bytes,
                        observed_md5=active_observed,
                        error="; ".join(errors)[-4000:] or "all recovery sources failed",
                    )
                    active_attempt_id = active_image_id = 0
                    active_stage = active_source = active_quarantine = ""
                    active_bytes = 0
                    active_observed = ""
                update_progress()

            result["ok"] = True
            result["duration_seconds"] = round(max(0.0, time.monotonic() - started), 3)
            _finish_run(settings, run_id, result, "completed")
            _emit(progress, "Recovery: готово; приняты только байты с точным canonical MD5")
            return result
    except (MaintenanceCancelled, InterruptedError) as exc:
        result["cancelled"] = True
        result["duration_seconds"] = round(max(0.0, time.monotonic() - started), 3)
        if active_stage:
            try:
                Path(active_stage).unlink(missing_ok=True)
            except Exception:
                pass
        if active_attempt_id and active_image_id:
            try:
                _finish_recovery_attempt(
                    settings, active_attempt_id, active_image_id, status="cancelled",
                    source_url=active_source, staging_path=active_stage,
                    quarantine_path=active_quarantine, bytes_downloaded=active_bytes,
                    observed_md5=active_observed, error=str(exc),
                )
            except Exception:
                pass
        if run_id:
            _finish_run(settings, run_id, result, "cancelled", str(exc))
        return result
    except Exception as exc:
        result["duration_seconds"] = round(max(0.0, time.monotonic() - started), 3)
        if active_stage:
            try:
                Path(active_stage).unlink(missing_ok=True)
            except Exception:
                pass
        if active_attempt_id and active_image_id:
            try:
                _finish_recovery_attempt(
                    settings, active_attempt_id, active_image_id, status="failed",
                    source_url=active_source, staging_path=active_stage,
                    quarantine_path=active_quarantine, bytes_downloaded=active_bytes,
                    observed_md5=active_observed,
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
        if run_id:
            try:
                _finish_run(settings, run_id, result, "failed", f"{type(exc).__name__}: {exc}")
            except Exception:
                pass
        _log.exception("Verified recovery failed")
        raise

