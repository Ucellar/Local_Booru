"""Hard safety boundary between immutable source media and disposable Local Booru output.

The user's source archive is the rebuild seed. Local Booru may read or copy
bytes from it, but all media moves/deletes/replacements must be restricted to
the configured managed output tree, resolved without creating unavailable roots.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable

from core.paths import LOGS_DIR
from core.storage_roots import configured_output_root

EVENT_LOG = Path(LOGS_DIR) / "protected_source_actions.jsonl"


# The folders the program makes in its output folder for pictures (core/library_reset.FOLDERS + the downloads).
PROGRAM_FOLDERS = ("found", "partial_match", "no_match", "downloads", "trash", "subscriptions", "pixiv")
_ROOTS: dict = {}                       # a root folder as written -> (when, resolved): checks come in thousands
_ROOTS_KEEP = 5.0                       # seconds


def _resolved(path: str | Path) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except Exception:
        return Path(path).expanduser().absolute()


def _root(value: str) -> Path:
    """A root folder resolved, kept for a few seconds (a folder on the network answers slowly)."""
    now = time.monotonic()
    known = _ROOTS.get(value)
    if known is not None and now - known[0] < _ROOTS_KEEP:
        return known[1]
    resolved = _resolved(value)
    if len(_ROOTS) > 32:
        _ROOTS.clear()
    _ROOTS[value] = (now, resolved)
    return resolved


def _within(path: Path, root: Path) -> bool:
    """``path`` inside ``root`` (or the same), both resolved already."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def output_root(settings: dict) -> Path:
    return _root(str(configured_output_root(settings)))


def source_root(settings: dict) -> Path | None:
    value = str((settings or {}).get("root", "") or "").strip()
    return _root(value) if value else None


def is_inside(path: str | Path, root: str | Path) -> bool:
    try:
        _resolved(path).relative_to(_resolved(root))
        return True
    except Exception:
        return False


def is_managed_media_path(settings: dict, path: str | Path) -> bool:
    """Return True only for media living inside Local Booru generated output.

    When the user's source folder overlaps the output folder, its pictures stay the user's: in the source folder
    only the program's own folders (``PROGRAM_FOLDERS`` of the output) are the program's.  So the output kept
    inside the source folder is the program's; the source folder kept in the output folder, or the same folder
    as the output (an «output» folder of another program as the source with no output folder chosen), is the
    user's but for found/, trash/…; a source folder that is one of those (the parser re-reading the library)
    is the program's."""
    out = output_root(settings)
    target = _resolved(path)
    if not _within(target, out):
        return False
    source = source_root(settings)
    if source is None or not _within(target, source) or not _within(source, out):
        return True
    return any(_within(target, out / name) for name in PROGRAM_FOLDERS)


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
