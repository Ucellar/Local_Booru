"""Hard safety boundary between immutable source media and disposable Local Booru output.

The user's source archive is the rebuild seed. Local Booru may read or copy
bytes from it, but all media moves/deletes/replacements must be restricted to
the managed output tree returned by :func:`core.paths.result_output_base`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable

from core.paths import LOGS_DIR, result_output_base

EVENT_LOG = Path(LOGS_DIR) / "protected_source_actions.jsonl"


def _resolved(path: str | Path) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except Exception:
        return Path(path).expanduser().absolute()


def output_root(settings: dict) -> Path:
    return _resolved(result_output_base(settings))


def source_root(settings: dict) -> Path | None:
    value = str((settings or {}).get("root", "") or "").strip()
    return _resolved(value) if value else None


def is_inside(path: str | Path, root: str | Path) -> bool:
    try:
        _resolved(path).relative_to(_resolved(root))
        return True
    except Exception:
        return False


def is_managed_media_path(settings: dict, path: str | Path) -> bool:
    """Return True only for media living inside Local Booru generated output."""
    return is_inside(path, output_root(settings))


def is_source_archive_path(settings: dict, path: str | Path) -> bool:
    root = source_root(settings)
    if root is None:
        return False
    # If the user placed output inside the source folder, generated output is
    # still mutable; everything else under source remains immutable.
    return is_inside(path, root) and not is_managed_media_path(settings, path)


def log_blocked_mutation(settings: dict, path: str | Path, operation: str, *, detail: str = "") -> None:
    try:
        EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "at": int(time.time()),
            "operation": str(operation or "mutation"),
            "path": str(_resolved(path)),
            "source_root": str(source_root(settings) or ""),
            "output_root": str(output_root(settings)),
            "detail": str(detail or ""),
        }
        with EVENT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def require_managed_media_mutation(settings: dict, path: str | Path, operation: str) -> bool:
    """Allow destructive media action only within generated output.

    The database may be rebuilt freely; original source bytes are never moved,
    overwritten or removed. False is deliberately non-throwing so old UI paths
    can fail safely and report a skipped protected file.
    """
    if is_managed_media_path(settings, path):
        return True
    log_blocked_mutation(settings, path, operation, detail="blocked: outside managed output")
    return False


def filter_managed_mutations(settings: dict, paths: Iterable[str | Path], operation: str) -> tuple[list[Path], list[Path]]:
    allowed: list[Path] = []
    blocked: list[Path] = []
    for raw in paths or []:
        p = Path(raw)
        if require_managed_media_mutation(settings, p, operation):
            allowed.append(p)
        else:
            blocked.append(p)
    return allowed, blocked


def recent_blocked_events(limit: int = 100) -> list[dict]:
    try:
        if not EVENT_LOG.exists():
            return []
        lines = EVENT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, int(limit)):]
        result = []
        for line in lines:
            try:
                result.append(dict(json.loads(line)))
            except Exception:
                pass
        return result
    except Exception:
        return []
