import os
import sys
from pathlib import Path

APP_NAME = "Local_Booru"
OUTPUT_FOLDER_NAME = "Local_Booru_Output"


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
    # Keep settings/cookies outside the release folder so reinstalling/updating the app
    # does not delete them. Prefer Documents, fallback to user home.
    try:
        docs = Path.home() / "Documents"
        docs.mkdir(parents=True, exist_ok=True)
        return docs
    except Exception:
        return Path.home()


def persistent_base_dir() -> Path:
    override = os.environ.get("LOCAL_BOORU_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return documents_dir() / APP_NAME


def ensure_output_base(selected: str | Path | None, root: str | Path | None = None) -> Path:
    """Return a safe media output folder that always ends with Local_Booru_Output.

    The user may select D:/ or F:/Media; we never create found/no_match directly in
    that folder. We create/use <selected>/Local_Booru_Output instead, unless the
    selected path already is named Local_Booru_Output.
    """
    selected_s = str(selected or "").strip()
    if selected_s:
        base = Path(selected_s).expanduser()
    else:
        root_s = str(root or "").strip()
        if root_s:
            rp = Path(root_s).expanduser()
            base = rp.parent if rp.name else rp
        else:
            base = documents_dir()
    try:
        base = base.resolve()
    except Exception:
        base = base.absolute()
    if base.name.lower() != OUTPUT_FOLDER_NAME.lower():
        base = base / OUTPUT_FOLDER_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


BASE_DIR = app_base_dir()
DATA_DIR = persistent_base_dir()
SETTINGS_DIR = DATA_DIR / "settings"
CACHE_DIR = DATA_DIR / "cache"
RUNTIME_DIR = DATA_DIR / "runtime"
LOGS_DIR = DATA_DIR / "logs"
DB_DIR = DATA_DIR / "db"
ASSETS_DIR = BASE_DIR / "assets"

# Only light persistent data is created here. Heavy media output is selected by user.
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


def result_output_base(settings: dict) -> "Path":
    """Return the user-selected output base folder (Local_Booru_Output).

    Kept here to break the circular library -> tagger_engine -> tagger dependency.
    """
    return ensure_output_base(
        (settings or {}).get("output_dir"),
        (settings or {}).get("root"),
    )
