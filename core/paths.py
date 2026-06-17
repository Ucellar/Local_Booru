"""Central filesystem layout for Local Booru.

Portable archive layout (v135):

    Local_Booru_Archive/
    ├── output/                 # managed media and archive-derived content
    └── settings/               # private/application state
        ├── config/
        ├── db/
        ├── cache/
        └── output/             # logs, runtime/browser data and backups

When portable storage is active the application does not persist a second
secret-bearing app_settings.json in Documents. Small locator files contain
only the selected workspace path: one travels beside the executable and one
is stored in the OS application-data locator directory so a newly unpacked
version can reconnect to the same archive before importing database modules.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "Local_Booru"
OUTPUT_FOLDER_NAME = "Local_Booru_Archive"
LEGACY_OUTPUT_FOLDER_NAME = "Local_Booru_Output"
WORKSPACE_POINTER_NAME = "local_booru_workspace.json"
GLOBAL_WORKSPACE_POINTER_NAME = "workspace_pointer.json"
WORKSPACE_POINTER_FORMAT = "local-booru-workspace-v1"


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "proj").exists():
            return exe_dir / "proj"
        if exe_dir.parent.name.lower() == "dist":
            return exe_dir.parent.parent
        if (exe_dir / "assets").exists():
            return exe_dir
        return exe_dir / "proj"
    return Path(__file__).resolve().parents[1]


def app_install_dir() -> Path:
    """Directory that travels with the executable/source checkout.

    The workspace pointer belongs here, not in the user's Documents folder.
    Tests may redirect it with LOCAL_BOORU_WORKSPACE_FILE.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def documents_dir() -> Path:
    """Legacy location only. Do not create it merely while resolving paths."""
    try:
        return Path.home() / "Documents"
    except Exception:
        return Path.home()


def legacy_data_dir() -> Path:
    return documents_dir() / APP_NAME


def _bootstrap_settings_file() -> Path:
    """Pre-v135 full configuration file; read only for migration/legacy mode."""
    return legacy_data_dir() / "settings" / "app_settings.json"


def workspace_pointer_file() -> Path:
    """Portable locator travelling beside one concrete copy of the program."""
    override = os.environ.get("LOCAL_BOORU_WORKSPACE_FILE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return app_install_dir() / WORKSPACE_POINTER_NAME


def stable_workspace_pointer_file() -> Path:
    """Stable locator shared by newly unpacked program versions.

    It stores only the path to Local_Booru_Archive/settings; settings, keys and
    cookies remain exclusively inside the selected archive.
    """
    override = os.environ.get("LOCAL_BOORU_GLOBAL_WORKSPACE_FILE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return _resolved(Path(local_app_data) / APP_NAME / GLOBAL_WORKSPACE_POINTER_NAME)
    return _resolved(Path.home() / ".local" / "share" / APP_NAME / GLOBAL_WORKSPACE_POINTER_NAME)


def _workspace_pointer_files() -> list[Path]:
    files: list[Path] = []
    for candidate in (workspace_pointer_file(), stable_workspace_pointer_file()):
        if candidate not in files:
            files.append(candidate)
    return files


def _resolved(value: str | Path) -> Path:
    try:
        return Path(value).expanduser().resolve()
    except Exception:
        return Path(value).expanduser().absolute()


def _settings_root_from_config(data: dict) -> Path | None:
    if not isinstance(data, dict) or not bool(data.get("separate_settings_storage", False)):
        return None
    explicit = str(data.get("settings_storage_dir", "") or "").strip()
    if explicit:
        p = _resolved(explicit)
        return p / "settings" if p.name.lower() == OUTPUT_FOLDER_NAME.lower() else p
    output = str(data.get("output_dir", "") or "").strip()
    if output:
        p = _resolved(output)
        if p.name.lower() == "output" and p.parent.name.lower() == OUTPUT_FOLDER_NAME.lower():
            return p.parent / "settings"
        if p.name.lower() == OUTPUT_FOLDER_NAME.lower():
            return p / "settings"
    return None


def _valid_portable_settings_root(target: str | Path | None) -> Path | None:
    if not target:
        return None
    root = _resolved(target)
    return root if (root / "config" / "app_settings.json").is_file() else None


def _read_pointer_settings_root(pointer: Path) -> Path | None:
    try:
        if not pointer.exists():
            return None
        data = json.loads(pointer.read_text(encoding="utf-8"))
        if data.get("format") not in (None, WORKSPACE_POINTER_FORMAT):
            return None
        explicit = str(data.get("settings_dir", "") or "").strip()
        if explicit:
            return _valid_portable_settings_root(explicit)
        archive = str(data.get("archive_root", "") or "").strip()
        return _valid_portable_settings_root(_resolved(archive) / "settings") if archive else None
    except Exception:
        return None


def _pointer_settings_root() -> Path | None:
    for pointer in _workspace_pointer_files():
        target = _read_pointer_settings_root(pointer)
        if target is not None:
            return target
    return None


def _near_program_archive_root() -> Path | None:
    """Open an archive placed beside the program without any external locator."""
    install = app_install_dir()
    for archive in (install / OUTPUT_FOLDER_NAME, install.parent / OUTPUT_FOLDER_NAME):
        target = _valid_portable_settings_root(archive / "settings")
        if target is not None:
            return target
    return None


def _legacy_configured_data_dir() -> Path | None:
    """Read the old full bootstrap settings only to migrate an existing install."""
    try:
        boot = _bootstrap_settings_file()
        if not boot.exists():
            return None
        return _valid_portable_settings_root(_settings_root_from_config(json.loads(boot.read_text(encoding="utf-8"))))
    except Exception:
        return None


def _configured_data_dir() -> Path | None:
    """Return configured private-state root before the rest of the app imports."""
    return _pointer_settings_root() or _near_program_archive_root() or _legacy_configured_data_dir()


def persistent_base_dir() -> Path:
    override = os.environ.get("LOCAL_BOORU_DATA_DIR", "").strip()
    if override:
        return _resolved(override)
    configured = _configured_data_dir()
    return configured if configured is not None else legacy_data_dir()


def ensure_output_base(selected: str | Path | None, root: str | Path | None = None) -> Path:
    """Return the managed archive-content root (``Local_Booru_Archive/output``).

    Old ``Local_Booru_Output`` selections remain readable so existing libraries
    are not unexpectedly moved or duplicated.  New archive selections always
    receive the two-branch Local_Booru_Archive layout.
    """
    selected_s = str(selected or "").strip()
    if selected_s:
        base = Path(selected_s).expanduser()
    else:
        root_s = str(root or "").strip()
        base = (Path(root_s).expanduser().parent if root_s else documents_dir())
    base = _resolved(base)
    if base.name.lower() == LEGACY_OUTPUT_FOLDER_NAME.lower():
        base.mkdir(parents=True, exist_ok=True)
        return base
    if base.name.lower() == "output" and base.parent.name.lower() == OUTPUT_FOLDER_NAME.lower():
        base.mkdir(parents=True, exist_ok=True)
        (base.parent / "settings").mkdir(parents=True, exist_ok=True)
        return base
    if base.name.lower() == OUTPUT_FOLDER_NAME.lower():
        archive_root = base
    else:
        archive_root = base / OUTPUT_FOLDER_NAME
    output = archive_root / "output"
    output.mkdir(parents=True, exist_ok=True)
    (archive_root / "settings").mkdir(parents=True, exist_ok=True)
    return output


def suggested_settings_storage_dir(settings: dict) -> Path:
    output = ensure_output_base((settings or {}).get("output_dir"), (settings or {}).get("root"))
    if output.name.lower() == "output" and output.parent.name.lower() == OUTPUT_FOLDER_NAME.lower():
        return output.parent / "settings"
    # A legacy output folder selected for an old library still receives a new
    # private branch beside it when the portable workspace option is enabled.
    return output.parent / OUTPUT_FOLDER_NAME / "settings"


def _copy_tree_without_deleting(source: Path, destination: Path) -> None:
    try:
        if source.exists() and source.resolve() != destination.resolve():
            shutil.copytree(source, destination, dirs_exist_ok=True)
    except Exception:
        pass


def _move_tree_contents(source: Path, destination: Path) -> None:
    """Move obsolete portable subtrees into settings/output without losing files."""
    try:
        if not source.exists() or source.resolve() == destination.resolve():
            return
        destination.mkdir(parents=True, exist_ok=True)
        for item in list(source.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(source)
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.move(str(item), str(target))
            else:
                # New-layout copy wins; do not retain a secret-bearing duplicate.
                item.unlink(missing_ok=True)
        for folder in sorted((p for p in source.rglob("*") if p.is_dir()), reverse=True):
            try:
                folder.rmdir()
            except OSError:
                pass
        try:
            source.rmdir()
        except OSError:
            pass
    except Exception:
        pass


def write_workspace_pointer(settings_root: str | Path) -> Path:
    """Persist only the workspace location locally and for future versions."""
    target = _resolved(settings_root)
    archive_root = target.parent if target.name.lower() == "settings" else target.parent
    payload = {
        "format": WORKSPACE_POINTER_FORMAT,
        "archive_root": str(archive_root),
        "settings_dir": str(target),
    }
    written: Path | None = None
    for pointer in _workspace_pointer_files():
        pointer.parent.mkdir(parents=True, exist_ok=True)
        tmp = pointer.with_suffix(pointer.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(pointer)
        if written is None:
            written = pointer
    return written or workspace_pointer_file()


def _remove_empty_legacy_dirs() -> None:
    """Remove empty folders created before a portable workspace was reconnected."""
    try:
        root = LEGACY_DATA_DIR
        if not root.exists():
            return
        for folder in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
            try:
                folder.rmdir()
            except OSError:
                pass
        try:
            root.rmdir()
        except OSError:
            pass
    except Exception:
        pass


def connect_existing_archive(selected: str | Path) -> Path | None:
    """Connect an existing Local_Booru_Archive without overwriting its config."""
    p = _resolved(selected)
    if p.name.lower() == "settings":
        target = p
    elif p.name.lower() == "output" and p.parent.name.lower() == OUTPUT_FOLDER_NAME.lower():
        target = p.parent / "settings"
    elif p.name.lower() == OUTPUT_FOLDER_NAME.lower():
        target = p / "settings"
    else:
        target = p / OUTPUT_FOLDER_NAME / "settings"
    target = _valid_portable_settings_root(target)
    if target is None:
        return None
    write_workspace_pointer(target)
    _remove_empty_legacy_dirs()
    return target


def remove_workspace_pointer() -> None:
    for pointer in _workspace_pointer_files():
        try:
            pointer.unlink(missing_ok=True)
        except Exception:
            pass


BASE_DIR = app_base_dir()
LEGACY_DATA_DIR = legacy_data_dir()
BOOTSTRAP_SETTINGS_FILE = _bootstrap_settings_file()
WORKSPACE_POINTER_FILE = workspace_pointer_file()
STABLE_WORKSPACE_POINTER_FILE = stable_workspace_pointer_file()
DATA_DIR = persistent_base_dir()
USING_SEPARATE_STORAGE = DATA_DIR.resolve() != LEGACY_DATA_DIR.resolve()

# v135 portable layout: the private branch contains only config/db/cache and
# one output subtree for logs/runtime/backups/reports. Legacy mode keeps old
# directories untouched until the user elects to use a portable archive.
SETTINGS_DIR = DATA_DIR / ("config" if USING_SEPARATE_STORAGE else "settings")
CACHE_DIR = DATA_DIR / "cache"
DB_DIR = DATA_DIR / "db"
SERVICE_OUTPUT_DIR = DATA_DIR / "output" if USING_SEPARATE_STORAGE else DATA_DIR
RUNTIME_DIR = SERVICE_OUTPUT_DIR / "runtime"
LOGS_DIR = SERVICE_OUTPUT_DIR / "logs"
BACKUPS_DIR = SERVICE_OUTPUT_DIR / "backups"
REPORTS_DIR = SERVICE_OUTPUT_DIR / "reports"
ASSETS_DIR = BASE_DIR / "assets"

for _p in (DATA_DIR, SETTINGS_DIR, CACHE_DIR, DB_DIR, SERVICE_OUTPUT_DIR, RUNTIME_DIR, LOGS_DIR, BACKUPS_DIR, REPORTS_DIR, ASSETS_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# Move the temporary v124-v134 service layout into the agreed settings/output
# branch as soon as that existing portable workspace is opened.
if USING_SEPARATE_STORAGE:
    _move_tree_contents(DATA_DIR / "logs", LOGS_DIR)
    _move_tree_contents(DATA_DIR / "runtime", RUNTIME_DIR)
    _move_tree_contents(DATA_DIR / "db_backups", BACKUPS_DIR / "db")

SETTINGS_FILE = SETTINGS_DIR / "app_settings.json"
FAVORITES_FILE = SETTINGS_DIR / "favorites.json"
CACHE_FILE = CACHE_DIR / "local_booru_cache.json"
NOMATCH_CACHE_FILE = CACHE_DIR / "nomatch_cache.json"
MD5_CACHE_FILE = CACHE_DIR / "md5_cache.json"
ERROR_LOG_FILE = LOGS_DIR / "errors.log"

BROWSER_PROFILE_DIR = RUNTIME_DIR / "browser_profile"
BROWSER_COOKIES_DIR = RUNTIME_DIR / "browser_cookies"
BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
BROWSER_COOKIES_DIR.mkdir(parents=True, exist_ok=True)
APP_ICON_FILE = ASSETS_DIR / "app_icon.ico"


def _retire_legacy_full_settings_if_portable() -> None:
    """Remove the old secret-bearing Documents copy after safe activation."""
    try:
        if not USING_SEPARATE_STORAGE or not SETTINGS_FILE.exists():
            return
        write_workspace_pointer(DATA_DIR)
        if BOOTSTRAP_SETTINGS_FILE.exists() and BOOTSTRAP_SETTINGS_FILE.resolve() != SETTINGS_FILE.resolve():
            BOOTSTRAP_SETTINGS_FILE.unlink(missing_ok=True)
    except Exception:
        # Never delete the old locator unless a pointer could be written.
        pass


def prepare_separate_storage(settings: dict) -> Path | None:
    """Populate ``Local_Booru_Archive/settings`` for activation on restart.

    Media stays in ``Local_Booru_Archive/output``.  Existing DB/cache/config are
    copied to avoid destructive migration; logs/runtime are copied into the new
    service-output branch.  The legacy Documents configuration is removed only
    after the canonical settings file and pointer are written by save_settings.
    """
    if not bool((settings or {}).get("separate_settings_storage", False)):
        return None
    explicit = str((settings or {}).get("settings_storage_dir", "") or "").strip()
    target = _resolved(explicit) if explicit else suggested_settings_storage_dir(settings)
    if target.name.lower() == OUTPUT_FOLDER_NAME.lower():
        target = target / "settings"
    target.mkdir(parents=True, exist_ok=True)
    config = target / "config"
    db = target / "db"
    cache = target / "cache"
    service_output = target / "output"
    for folder in (config, db, cache, service_output / "logs", service_output / "runtime", service_output / "backups", service_output / "reports"):
        folder.mkdir(parents=True, exist_ok=True)
    _copy_tree_without_deleting(DB_DIR, db)
    _copy_tree_without_deleting(CACHE_DIR, cache)
    _copy_tree_without_deleting(LOGS_DIR, service_output / "logs")
    _copy_tree_without_deleting(RUNTIME_DIR, service_output / "runtime")
    _copy_tree_without_deleting(BACKUPS_DIR, service_output / "backups")
    try:
        if SETTINGS_DIR.exists() and SETTINGS_DIR.resolve() != config.resolve():
            shutil.copytree(SETTINGS_DIR, config, dirs_exist_ok=True)
    except Exception:
        pass
    # Repair an early portable-layout workspace that already had logs/runtime
    # directly below settings/.
    _move_tree_contents(target / "logs", service_output / "logs")
    _move_tree_contents(target / "runtime", service_output / "runtime")
    _move_tree_contents(target / "db_backups", service_output / "backups" / "db")
    return target


def activate_portable_workspace() -> None:
    """Finish automatic migration from the v124-v134 Documents bootstrap copy."""
    _retire_legacy_full_settings_if_portable()


def result_output_base(settings: dict) -> Path:
    return ensure_output_base((settings or {}).get("output_dir"), (settings or {}).get("root"))
