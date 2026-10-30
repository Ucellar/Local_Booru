from __future__ import annotations

"""Shared filesystem path comparison helpers.

Local Booru stores absolute Windows paths in SQLite, but several subsystems also
receive equivalent paths with different slash/case spellings.  Keep canonical
comparison in one place instead of duplicating slightly different helpers in UI
and browser-import code.
"""

import os
from pathlib import Path
from typing import Any


def path_key(value: Any) -> str:
    """Return a stable, platform-aware comparison key for a path-like value."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(str(Path(raw)))))
    except Exception:
        try:
            return os.path.normcase(os.path.abspath(raw))
        except Exception:
            return os.path.normcase(raw)


def same_path(left: Any, right: Any) -> bool:
    left_key = path_key(left)
    right_key = path_key(right)
    return bool(left_key and right_key and left_key == right_key)
