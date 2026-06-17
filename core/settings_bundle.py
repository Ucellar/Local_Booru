"""Portable settings profile export/import for Local Booru.

The bundle intentionally never contains the media library or SQLite database.
Sensitive credentials are excluded by default and can only be included by an
explicit user action in the UI.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import json
import shutil
import zipfile

SENSITIVE_FRAGMENTS = ("api_key", "password", "passwd", "cookie", "token", "secret", "auth", "login")
PROFILE_FORMAT = "local-booru-settings-profile-v1"


def _is_sensitive(key: str) -> bool:
    low = str(key or "").lower()
    return any(fragment in low for fragment in SENSITIVE_FRAGMENTS)


def _redact(value: Any, key: str = "") -> Any:
    if _is_sensitive(key):
        if value in (None, "", [], {}):
            return value
        return ""
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value]
    return value


def exported_settings(settings: dict, *, include_secrets: bool = False) -> dict:
    data = json.loads(json.dumps(dict(settings or {}), ensure_ascii=False))
    return data if include_secrets else _redact(data)


def export_profile(settings: dict, destination: str | Path, *, include_secrets: bool = False) -> str:
    """Create a portable zip containing settings only, never media/database."""
    destination = Path(destination)
    if destination.suffix.lower() != ".zip":
        destination = destination.with_suffix(".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format": PROFILE_FORMAT,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "includes_secrets": bool(include_secrets),
        "includes_database": False,
        "includes_media": False,
        "notes": "Профиль интерфейса/парсера. Медиа и SQLite не входят в архив.",
    }
    profile = exported_settings(settings, include_secrets=include_secrets)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("app_settings.json", json.dumps(profile, ensure_ascii=False, indent=2))
    return str(destination)


def read_profile(source: str | Path) -> tuple[dict, dict]:
    source = Path(source)
    if source.suffix.lower() == ".json":
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("JSON профиля не является объектом настроек")
        return {"format": "legacy-json", "includes_secrets": None}, raw
    with zipfile.ZipFile(source, "r") as zf:
        names = set(zf.namelist())
        if "app_settings.json" not in names:
            raise ValueError("В архиве отсутствует app_settings.json")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8")) if "manifest.json" in names else {}
        if manifest.get("format") not in (None, PROFILE_FORMAT):
            raise ValueError("Неизвестный формат профиля настроек")
        settings = json.loads(zf.read("app_settings.json").decode("utf-8"))
    if not isinstance(settings, dict):
        raise ValueError("Профиль не содержит объект настроек")
    return manifest, settings


def import_profile(source: str | Path, current_settings: dict, *, apply: bool = True) -> dict:
    """Merge a portable profile into defaults/current settings with a backup.

    Returns the merged settings and backup path. It never imports SQLite/media.
    """
    manifest, imported = read_profile(source)
    from core.settings import deep_merge, DEFAULT_SETTINGS, save_settings
    from core.paths import SETTINGS_FILE, BACKUPS_DIR
    merged = deep_merge(DEFAULT_SETTINGS, {**dict(current_settings or {}), **imported})
    backup = ""
    if apply:
        backups = Path(BACKUPS_DIR) / "config"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backups / f"app_settings_before_import_{stamp}.json"
        if Path(SETTINGS_FILE).exists():
            shutil.copy2(SETTINGS_FILE, backup_path)
        else:
            backup_path.write_text(json.dumps(dict(current_settings or {}), ensure_ascii=False, indent=2), encoding="utf-8")
        backup = str(backup_path)
        save_settings(merged)
    return {"settings": merged, "backup": backup, "manifest": manifest, "source": str(source)}
