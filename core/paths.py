import json
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "Local_Booru"
OUTPUT_FOLDER_NAME = "Local_Booru_Archive"
LEGACY_OUTPUT_FOLDER_NAME = "Local_Booru_Output"


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


def documents_dir() -> Path:
    try:
        docs = Path.home() / "Documents"
        docs.mkdir(parents=True, exist_ok=True)
        return docs
    except Exception:
        return Path.home()


def legacy_data_dir() -> Path:
    return documents_dir() / APP_NAME


def _bootstrap_settings_file() -> Path:
    return legacy_data_dir() / "settings" / "app_settings.json"


def _configured_data_dir() -> Path | None:
    """Read the light bootstrap file before the rest of the app is imported.

    A new storage location can only become active after restart: constants such
    as DB_DIR and CACHE_DIR are intentionally stable for the lifetime of a run.
    """
    try:
        boot = _bootstrap_settings_file()
        if not boot.exists():
            return None
        data = json.loads(boot.read_text(encoding="utf-8"))
        if not bool(data.get("separate_settings_storage", False)):
            return None
        explicit = str(data.get("settings_storage_dir", "") or "").strip()
        if explicit:
            return Path(explicit).expanduser().resolve()
        output = str(data.get("output_dir", "") or "").strip()
        if output:
            p = Path(output).expanduser().resolve()
            if p.name.lower() == "output" and p.parent.name.lower() == OUTPUT_FOLDER_NAME.lower():
                return p.parent / "settings"
    except Exception:
        return None
    return None


def persistent_base_dir() -> Path:
    override = os.environ.get("LOCAL_BOORU_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    configured = _configured_data_dir()
    return configured if configured is not None else legacy_data_dir()


def ensure_output_base(selected: str | Path | None, root: str | Path | None = None) -> Path:
    """Return media output folder, retaining old saved output paths safely."""
    selected_s = str(selected or "").strip()
    if selected_s:
        base = Path(selected_s).expanduser()
    else:
        root_s = str(root or "").strip()
        base = (Path(root_s).expanduser().parent if root_s else documents_dir())
    try:
        base = base.resolve()
    except Exception:
        base = base.absolute()
    if base.name.lower() == LEGACY_OUTPUT_FOLDER_NAME.lower():
        base.mkdir(parents=True, exist_ok=True)
        return base
    if base.name.lower() == "output" and base.parent.name.lower() == OUTPUT_FOLDER_NAME.lower():
        base.mkdir(parents=True, exist_ok=True)
        return base
    if base.name.lower() == OUTPUT_FOLDER_NAME.lower():
        base = base / "output"
    else:
        base = base / OUTPUT_FOLDER_NAME / "output"
    base.mkdir(parents=True, exist_ok=True)
    return base


def suggested_settings_storage_dir(settings: dict) -> Path:
    output = ensure_output_base((settings or {}).get("output_dir"), (settings or {}).get("root"))
    if output.name.lower() == "output" and output.parent.name.lower() == OUTPUT_FOLDER_NAME.lower():
        return output.parent / "settings"
    return output.parent / "Local_Booru_Archive" / "settings"


BASE_DIR = app_base_dir()
LEGACY_DATA_DIR = legacy_data_dir()
BOOTSTRAP_SETTINGS_FILE = _bootstrap_settings_file()
DATA_DIR = persistent_base_dir()
USING_SEPARATE_STORAGE = DATA_DIR.resolve() != LEGACY_DATA_DIR.resolve()
SETTINGS_DIR = DATA_DIR / ("config" if USING_SEPARATE_STORAGE else "settings")
CACHE_DIR = DATA_DIR / "cache"
RUNTIME_DIR = DATA_DIR / "runtime"
LOGS_DIR = DATA_DIR / "logs"
DB_DIR = DATA_DIR / "db"
ASSETS_DIR = BASE_DIR / "assets"

for _p in (DATA_DIR, SETTINGS_DIR, CACHE_DIR, RUNTIME_DIR, LOGS_DIR, DB_DIR, ASSETS_DIR):
    _p.mkdir(parents=True, exist_ok=True)

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


def prepare_separate_storage(settings: dict) -> Path | None:
    """Copy current persistent data to the selected settings root for next run.

    Existing data is copied, never removed. The legacy settings file remains a
    bootstrap pointer so the next application launch can select the new root
    before importing database/cache modules.
    """
    if not bool((settings or {}).get("separate_settings_storage", False)):
        return None
    explicit = str((settings or {}).get("settings_storage_dir", "") or "").strip()
    target = Path(explicit).expanduser().resolve() if explicit else suggested_settings_storage_dir(settings)
    target.mkdir(parents=True, exist_ok=True)
    config = target / "config"
    config.mkdir(parents=True, exist_ok=True)
    for source, dest_name in ((DB_DIR, "db"), (CACHE_DIR, "cache"), (LOGS_DIR, "logs"), (RUNTIME_DIR, "runtime")):
        try:
            if source.exists() and source.resolve() != (target / dest_name).resolve():
                shutil.copytree(source, target / dest_name, dirs_exist_ok=True)
        except Exception:
            pass
    try:
        if SETTINGS_DIR.exists() and SETTINGS_DIR.resolve() != config.resolve():
            shutil.copytree(SETTINGS_DIR, config, dirs_exist_ok=True)
    except Exception:
        pass
    return target


def result_output_base(settings: dict) -> Path:
    return ensure_output_base((settings or {}).get("output_dir"), (settings or {}).get("root"))
