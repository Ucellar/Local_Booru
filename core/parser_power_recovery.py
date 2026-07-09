"""Crash/power-loss recovery helpers for long parser runs.

The parser already journals per-site results in SQLite, but a hard power cut can
happen between copying/merging a result and committing every related site-status
row.  Keep a tiny rolling list of the most recently *completed* files.  If the
previous parser session did not shut down cleanly, the next run forces those
files through the normal found-check/merge path again.

Important: earlier builds tracked files as soon as they were submitted to the
parser window.  With large conveyor windows that meant the recovery pass could
recheck the first/oldest files in the queue, not the files that actually finished
last.  Completed-file tracking is the authoritative list now; started-file
tracking is kept only as a compatibility fallback for old state files.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import time
from typing import Iterable

from core.paths import RUNTIME_DIR
from core.database.connection import db

STATE_FILE = RUNTIME_DIR / "parser_power_recovery.json"
MAX_RECENT = 10


def _read_state() -> dict:
    try:
        if not STATE_FILE.exists():
            return {}
        data = json.loads(STATE_FILE.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(data: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, STATE_FILE)
    except Exception:
        # Recovery is best-effort.  It must never break parsing.
        pass


def begin_parser_session(settings: dict, root: str | Path) -> list[Path]:
    """Start a new parser session and return last-run recovery candidates.

    Candidates are returned only when the previous session was still marked
    active.  A normal DONE/STOP clears the marker.
    """
    old = _read_state()
    recent: list[Path] = []
    if bool(old.get("active")):
        # Prefer the files that actually completed last.  Fall back to the old
        # started/submitted list only for state written by older builds.
        source = (
            list(old.get("completed_recent") or [])
            or list(old.get("recent_completed") or [])
            or list(old.get("recent") or [])
            or list(old.get("recent_started") or [])
        )
        for raw in list(source)[-MAX_RECENT:]:
            try:
                p = Path(str(raw))
                if p.exists():
                    recent.append(p)
            except Exception:
                continue
    _write_state({
        "active": True,
        "root": str(root or settings.get("root", "")),
        "started_at": int(time.time()),
        "updated_at": int(time.time()),
        "recent_started": [],
        "completed_recent": [],
    })
    # Preserve order and uniqueness.
    out: list[Path] = []
    seen = set()
    for p in recent:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out[-MAX_RECENT:]


def record_parser_file(settings: dict, path: str | Path) -> None:
    """Remember that a file has entered parser processing.

    This list is not used for normal recovery selection anymore.  It is only a
    fallback when no completed-file list exists, because submit order is not the
    same as completion order in the per-site conveyor.
    """
    try:
        p = str(Path(path))
        data = _read_state()
        recent = [str(x) for x in (data.get("recent_started") or data.get("recent") or []) if str(x) != p]
        recent.append(p)
        data.update({
            "active": True,
            "updated_at": int(time.time()),
            "recent_started": recent[-MAX_RECENT:],
        })
        _write_state(data)
    except Exception:
        pass


def record_parser_file_completed(settings: dict, path: str | Path) -> None:
    """Remember that a file has finished parser processing.

    Power-loss recovery must replay the latest *completed* files, not the first
    files submitted to a large conveyor window.
    """
    try:
        p = str(Path(path))
        data = _read_state()
        recent = [str(x) for x in (data.get("completed_recent") or data.get("recent_completed") or []) if str(x) != p]
        recent.append(p)
        data.update({
            "active": True,
            "updated_at": int(time.time()),
            "completed_recent": recent[-MAX_RECENT:],
            # Compatibility/debug alias: old code/readers used the word recent.
            "recent_completed": recent[-MAX_RECENT:],
        })
        _write_state(data)
    except Exception:
        pass


def mark_parser_session_clean(settings: dict) -> None:
    """Mark parser shutdown as clean so next run does not force recovery."""
    try:
        data = _read_state()
        data.update({"active": False, "clean_at": int(time.time())})
        _write_state(data)
    except Exception:
        pass


def force_recheck_sites(settings: dict, paths: Iterable[str | Path], site_keys: Iterable[str], *, scan_revision: int = 1) -> int:
    """Clear per-site completion journal for paths that may have been mid-write.

    This does not delete found media, tags, sources or processed_files.  If the
    file was already in found, the normal exact-MD5 save path will merge into the
    existing found row and avoid creating another physical copy.
    """
    path_values = [str(Path(p)) for p in (paths or []) if str(p or "").strip()]
    key_values = [str(k or "").strip().lower() for k in (site_keys or []) if str(k or "").strip()]
    if not path_values:
        return 0
    removed = 0
    with db(settings, write=True) as con:
        for p in path_values:
            if key_values:
                placeholders = ",".join(["?"] * len(key_values))
                cur = con.execute(
                    f"DELETE FROM site_scan_status WHERE original_path=? AND scan_revision=? AND site_key IN ({placeholders})",
                    [p, int(scan_revision or 1), *key_values],
                )
            else:
                cur = con.execute(
                    "DELETE FROM site_scan_status WHERE original_path=? AND scan_revision=?",
                    (p, int(scan_revision or 1)),
                )
            try:
                removed += int(cur.rowcount or 0)
            except Exception:
                pass
    return removed
