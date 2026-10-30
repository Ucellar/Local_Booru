"""Shared storage-root availability checks.

A missing media path is not automatically a missing file.  The whole volume or
configured archive root may be unavailable.  Database-missing protection and
file-presence maintenance use this same classifier so they cannot disagree
about an offline drive.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import os
from pathlib import Path, PureWindowsPath
import re
import stat
from typing import Any


ROOT_AVAILABLE = "available"
ROOT_OFFLINE = "offline_root"
ROOT_MISSING = "missing_root"
ROOT_ERROR = "path_error"


@dataclass(frozen=True)
class StorageRootState:
    root: str
    status: str
    volume_root: str = ""
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.status == ROOT_AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["available"] = self.available
        return data


def _raw(value: str | Path | None) -> str:
    return str(value or "").strip().strip('"')


def volume_root(value: str | Path | None) -> str:
    """Return a drive/UNC/POSIX anchor without requiring it to exist."""
    raw = _raw(value)
    if not raw:
        return ""
    if re.match(r"^[A-Za-z]:[\\/]", raw):
        return raw[:2] + "\\"
    if raw.startswith("\\\\") or raw.startswith("//"):
        try:
            win = PureWindowsPath(raw)
            anchor = str(win.anchor or "")
            return anchor if anchor else raw
        except Exception:
            parts = [p for p in re.split(r"[\\/]+", raw) if p]
            return ("\\\\" + "\\".join(parts[:2]) + "\\") if len(parts) >= 2 else raw
    try:
        return str(Path(raw).anchor or "")
    except Exception:
        return ""


def _same_location(left: str | Path | None, right: str | Path | None) -> bool:
    a = _raw(left)
    b = _raw(right)
    if not a or not b:
        return False
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(a))) == os.path.normcase(
            os.path.abspath(os.path.normpath(b))
        )
    except Exception:
        return os.path.normcase(a.rstrip("\\/")) == os.path.normcase(b.rstrip("\\/"))


def check_storage_root(value: str | Path | None) -> StorageRootState:
    """Classify a configured root without creating or mutating it."""
    raw = _raw(value)
    if not raw:
        return StorageRootState("", ROOT_ERROR, "", "empty_root")
    anchor = volume_root(raw)
    try:
        if os.path.isdir(raw):
            return StorageRootState(raw, ROOT_AVAILABLE, anchor)
        if os.path.exists(raw):
            return StorageRootState(raw, ROOT_ERROR, anchor, "root_is_not_directory")
    except OSError as exc:
        return StorageRootState(raw, ROOT_ERROR, anchor, f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        return StorageRootState(raw, ROOT_ERROR, anchor, f"{type(exc).__name__}: {exc}")

    # A missing drive/UNC share means the storage itself is offline.  If the
    # volume exists but a configured subfolder does not, keep that distinction
    # in the root state.  Media rows under either state are reported as
    # offline_root, never as thousands of individually missing files.
    if anchor:
        try:
            anchor_exists = os.path.isdir(anchor)
        except OSError as exc:
            return StorageRootState(raw, ROOT_ERROR, anchor, f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            return StorageRootState(raw, ROOT_ERROR, anchor, f"{type(exc).__name__}: {exc}")
        if not anchor_exists:
            return StorageRootState(raw, ROOT_OFFLINE, anchor, "volume_or_share_unavailable")
        if not _same_location(raw, anchor):
            return StorageRootState(raw, ROOT_MISSING, anchor, "configured_root_missing")

    # Relative/malformed paths have no useful volume anchor.  A POSIX absolute
    # root normally has '/', so reaching this branch is a path configuration
    # problem rather than proof that one media file disappeared.
    return StorageRootState(raw, ROOT_MISSING, anchor, "configured_root_missing")


def _contains(path: str, root: str) -> bool:
    if not path or not root:
        return False
    try:
        p = os.path.normcase(os.path.abspath(os.path.normpath(path)))
        r = os.path.normcase(os.path.abspath(os.path.normpath(root)))
        return os.path.commonpath([p, r]) == r
    except Exception:
        p = os.path.normcase(path.replace("/", "\\").rstrip("\\"))
        r = os.path.normcase(root.replace("/", "\\").rstrip("\\"))
        return p == r or p.startswith(r + "\\")


def configured_output_root(settings: dict | None) -> str:
    """Resolve the managed output root without creating directories."""
    settings = settings or {}
    from core.paths import LEGACY_OUTPUT_FOLDER_NAME, OUTPUT_FOLDER_NAME, documents_dir

    selected = _raw(settings.get("output_dir"))
    if selected:
        base = Path(selected).expanduser()
    else:
        source = _raw(settings.get("root"))
        base = Path(source).expanduser().parent if source else documents_dir()
    try:
        base = base.resolve()
    except Exception:
        base = base.absolute()
    if base.name.lower() == LEGACY_OUTPUT_FOLDER_NAME.lower():
        return str(base)
    if base.name.lower() == "output":
        return str(base)
    if (base / "output").exists() or (base / "settings").exists() or base.name.lower() == OUTPUT_FOLDER_NAME.lower():
        return str(base / "output")
    return str(base / OUTPUT_FOLDER_NAME / "output")


def path_is_within_root(path: str | Path | None, root: str | Path | None) -> bool:
    """Return containment using the same Windows/POSIX normalization as probes."""
    return _contains(_raw(path), _raw(root))


def managed_output_root_state(settings: dict | None) -> StorageRootState:
    """Classify the configured output root without creating it."""
    return check_storage_root(configured_output_root(settings or {}))


def is_managed_output_path(settings: dict | None, path: str | Path | None) -> bool:
    """Check output containment without calling directory-creating path helpers."""
    return path_is_within_root(path, configured_output_root(settings or {}))


def configured_storage_roots(settings: dict | None) -> list[tuple[str, str]]:
    """Return configured media roots in precedence order, without probing.

    This function must not call result_output_base()/ensure_output_base(); a
    presence check is specifically expected to notice an unavailable root and
    must never recreate it as a side effect.
    """
    settings = settings or {}
    roots: list[tuple[str, str]] = []
    out = _raw(configured_output_root(settings))
    src = _raw(settings.get("root"))
    for kind, root in (("output", out), ("source", src)):
        if root and not any(_same_location(root, existing) for _k, existing in roots):
            roots.append((kind, root))
    return roots


def storage_root_for_path(settings: dict | None, path: str | Path | None) -> tuple[str, str]:
    raw = _raw(path)
    for kind, root in configured_storage_roots(settings):
        if _contains(raw, root):
            return kind, root
    anchor = volume_root(raw)
    if anchor:
        return "external", anchor
    try:
        parent = str(Path(raw).expanduser().parent) if raw else ""
    except Exception:
        parent = ""
    return "external", parent


def probe_media_path(
    settings: dict | None,
    path: str | Path | None,
    *,
    root_cache: dict[str, StorageRootState] | None = None,
) -> dict[str, Any]:
    """Return present/missing/offline_root/path_error for one media path."""
    raw = _raw(path)
    kind, root = storage_root_for_path(settings, raw)
    cache = root_cache if root_cache is not None else {}
    cache_key = os.path.normcase(str(root or ""))
    root_state = cache.get(cache_key)
    if root_state is None:
        root_state = check_storage_root(root)
        cache[cache_key] = root_state

    base = {
        "path": raw,
        "root_kind": kind,
        "root_path": str(root_state.root or root),
        "root_status": root_state.status,
        "volume_root": root_state.volume_root,
        "error": root_state.detail,
    }
    if root_state.status in {ROOT_OFFLINE, ROOT_MISSING}:
        return {**base, "status": "offline_root"}
    if root_state.status != ROOT_AVAILABLE:
        return {**base, "status": "path_error"}
    if not raw:
        return {**base, "status": "path_error", "error": "empty_media_path"}
    try:
        file_stat = os.stat(raw)
        if stat.S_ISREG(file_stat.st_mode):
            return {**base, "status": "present", "error": ""}
        return {**base, "status": "path_error", "error": "path_is_not_file"}
    except FileNotFoundError:
        # The root may have disappeared after the per-chunk root probe.  Before
        # confirming an individual missing file, bypass the cache and classify
        # the root once more.  This bounds a hot-unplug race to zero false
        # missing statuses instead of up to one full chunk.
        fresh_root = check_storage_root(root)
        refreshed = {
            **base,
            "root_path": str(fresh_root.root or root),
            "root_status": fresh_root.status,
            "volume_root": fresh_root.volume_root,
            "error": fresh_root.detail,
        }
        cache[cache_key] = fresh_root
        if fresh_root.status in {ROOT_OFFLINE, ROOT_MISSING}:
            return {**refreshed, "status": "offline_root"}
        if fresh_root.status != ROOT_AVAILABLE:
            return {**refreshed, "status": "path_error"}
        return {**refreshed, "status": "missing", "error": ""}
    except OSError as exc:
        return {**base, "status": "path_error", "error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:
        return {**base, "status": "path_error", "error": f"{type(exc).__name__}: {exc}"}
