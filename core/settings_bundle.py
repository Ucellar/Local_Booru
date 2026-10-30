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

# Credentials only.  Matching any key that merely *contains* "login", "cookie" or "auth"
# used to blank login_url of every site, switch off browser_companion_cookie_handoff_enabled
# and wipe tagger_fast_authoritative_hosts on import.
SENSITIVE_KEYS = {"api_key", "login", "password", "passwd", "user_id", "token", "secret",
                  "cookie", "cookies", "session", "session_id"}
SENSITIVE_SUFFIXES = ("_api_key", "_apikey", "_password", "_passwd", "_token", "_secret", "_cookies", "_session_id")
PROFILE_FORMAT = "local-booru-settings-profile-v1"


def _is_sensitive(key: str) -> bool:
    low = str(key or "").lower()
    return low in SENSITIVE_KEYS or low.endswith(SENSITIVE_SUFFIXES)


def _redact(value: Any, key: str = "") -> Any:
    if _is_sensitive(key):
        if value in (None, "", [], {}) or isinstance(value, bool):
            return value              # a switch is never a credential («light_backup_include_cookies»)
        return ""
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value]
    return value


def exported_settings(settings: dict, *, include_secrets: bool = False) -> dict:
    # «_…» keys are the running program's state (``_parser_running``…), not settings: never in a profile
    data = json.loads(json.dumps({k: v for k, v in dict(settings or {}).items() if not str(k).startswith("_")},
                                 ensure_ascii=False))
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


MAX_PROFILE_BYTES = 20 * 1024 * 1024        # a settings file is kilobytes; a «profile» of gigabytes is not one


class ProfileError(ValueError):
    """The file is not a readable settings profile (nothing was changed)."""


def _read_member(zf: zipfile.ZipFile, name: str) -> str:
    if zf.getinfo(name).file_size > MAX_PROFILE_BYTES:
        raise ProfileError(f"{name} в архиве слишком большой для профиля настроек")
    return zf.read(name).decode("utf-8-sig")


# No setting holds an endless number or one hundreds of digits long — at the top or deep inside a site's
# settings, where it would only fail later (a wait that never ends, a widget that cannot show it).
_BIG = 2 ** 63


def _no_constant(name: str):
    raise ProfileError(f"в профиле есть «{name}» вместо числа — файл повреждён или не из этой программы")


def _finite_float(text: str) -> float:
    import math
    value = float(text)
    if not math.isfinite(value) or abs(value) >= _BIG:
        raise ProfileError(f"в профиле число {text[:40]} — такого не бывает в настройках")
    return value


def _sane_int(text: str) -> int:
    if len(text) > 19 or abs(int(text)) >= _BIG:
        raise ProfileError(f"в профиле число {text[:40]}… — такого не бывает в настройках")
    return int(text)


def _json(text: str) -> Any:
    return json.loads(text, parse_constant=_no_constant, parse_float=_finite_float, parse_int=_sane_int)


def read_profile(source: str | Path) -> tuple[dict, dict]:
    """(manifest, settings) of a profile file; ProfileError when it is not one (a damaged or foreign
    archive, a JSON that is not settings)."""
    source = Path(source)
    try:
        if source.suffix.lower() == ".json":
            if source.stat().st_size > MAX_PROFILE_BYTES:
                raise ProfileError("файл слишком большой для профиля настроек")
            raw = _json(source.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                raise ProfileError("JSON профиля не является объектом настроек")
            return {"format": "legacy-json", "includes_secrets": None}, raw
        with zipfile.ZipFile(source, "r") as zf:
            names = set(zf.namelist())
            if "app_settings.json" not in names:
                raise ProfileError("В архиве отсутствует app_settings.json")
            manifest = _json(_read_member(zf, "manifest.json")) if "manifest.json" in names else {}
            if not isinstance(manifest, dict):
                raise ProfileError("manifest.json профиля — не описание профиля")
            if manifest.get("format") not in (None, PROFILE_FORMAT):
                raise ProfileError("Неизвестный формат профиля настроек")
            settings = _json(_read_member(zf, "app_settings.json"))
    except ProfileError:
        raise
    except Exception as exc:     # damaged, encrypted, odd compression (zlib / lzma / bz2), nested too deep…:
        # whatever the file does to the readers, it is not a profile, and nothing has been changed
        raise ProfileError(f"файл не читается как профиль: {type(exc).__name__}: {exc}") from exc
    if not isinstance(settings, dict):
        raise ProfileError("Профиль не содержит объект настроек")
    return manifest, settings


def _take(key: str, value: Any) -> bool:
    """A profile value replaces the current one, except an empty (redacted) credential."""
    return not (_is_sensitive(key) and value in (None, "", [], {}))


def _merge_site(current: dict, imported: dict) -> dict:
    out = dict(current or {})
    for key, value in (imported or {}).items():
        if _take(key, value):
            out[key] = value
    return out


def merge_profile(current: dict, imported: dict) -> dict:
    """Profile settings over the current ones; current credentials survive a redacted profile.

    Sites are merged per site (by domain / site_id), so a site that is only in the current
    settings, or only its API key, is not lost.
    """
    out = dict(current or {})
    for key, value in (imported or {}).items():
        if key == "sites" and isinstance(value, dict):
            sites = dict(out.get("sites") or {})
            for domain, cfg in value.items():
                sites[domain] = _merge_site(sites.get(domain) or {}, cfg if isinstance(cfg, dict) else {})
            out["sites"] = sites
        elif key == "custom_sites" and isinstance(value, list):
            def ident(cfg):
                return str(cfg.get("site_id") or cfg.get("domain") or "") if isinstance(cfg, dict) else ""
            by_id = {ident(c): c for c in (out.get("custom_sites") or []) if ident(c)}
            merged = []
            for cfg in value:
                if isinstance(cfg, dict):
                    merged.append(_merge_site(by_id.pop(ident(cfg), {}), cfg))
            merged += list(by_id.values())
            out["custom_sites"] = merged
        elif _take(key, value):
            out[key] = value
    return out


# Places and state of this computer: a profile made on another one (or before the archive moved) must not
# point the program at its folders, its database or its state — they keep their current values.
LOCAL_KEYS = frozenset({
    "root", "output_dir", "manga_root", "settings_storage_dir", "separate_settings_storage", "light_backup_dir",
    "visual_nomatch_clip_model_dir", "nomatch_compact_classifier_onnx", "nomatch_text_detector_onnx",
    "external_browser_executable", "logo_path", "light_backup_last_at",
    "sqlite_db_folder", "sqlite_allow_recreate_missing_db", "db_initialized_once",
    "booru_corpus_path", "gallery_data_source", "exiftool_path", "metadata_exiftool_path", "session_folder",
})
# …and any other key named as a place (an older or newer version's folder or program)
_PLACE_SUFFIXES = ("_dir", "_folder", "_path", "_root", "_executable", "_exe", "_onnx")
# A number the program shows in a window field (a 32-bit spin box) — a bigger one stops the old settings
# page from opening, and with it the program window
_FIELD_LIMIT = 2 ** 31


def is_local(key: str) -> bool:
    """A setting about this computer: its folders, programs, database, state."""
    return key in LOCAL_KEYS or key.lower().endswith(_PLACE_SUFFIXES)


def _fits(value: Any, reference: Any, *, known: bool) -> bool:
    """The same JSON kind as ``reference`` (the current or default value) when the setting is ``known``
    (a number for a number: the window's fields take 5.0 or 5.5 where 5 was), and a top-level number
    that a window field can hold."""
    if isinstance(value, bool) or (known and isinstance(reference, bool)):
        return isinstance(value, bool) and (not known or isinstance(reference, bool))
    if isinstance(value, (int, float)):
        big_already = known and isinstance(reference, (int, float)) and abs(reference) >= _FIELD_LIMIT
        if abs(value) >= (_BIG if big_already else _FIELD_LIMIT):
            return False
        return not known or isinstance(reference, (int, float))
    return not known or isinstance(value, type(reference))


def clean_profile(imported: dict, current: dict) -> tuple[dict, list]:
    """The settings of a profile that may replace the current ones.

    Left as they are: this computer's places, database and state (``is_local``), the running program's
    «_…» state and credentials the profile was made without (empty).  A setting this computer has (in its
    settings or the defaults) takes only a value of the same kind; a setting it has never had (the
    program writes some only when they change) is taken as it is.  Numbers are ones a window field can
    show.  Returns (kept, names of the keys left out)."""
    from core.settings import DEFAULT_SETTINGS
    kept, skipped = {}, []
    for key, value in dict(imported or {}).items():
        key = str(key)
        if key.startswith("_") or is_local(key) or not _take(key, value):
            continue
        known = key in current or key in DEFAULT_SETTINGS
        reference = current[key] if key in current else DEFAULT_SETTINGS.get(key)
        if not _fits(value, reference, known=known):
            skipped.append(key)
            continue
        if key == "sites" and not (isinstance(value, dict) and all(isinstance(v, dict) for v in value.values())):
            skipped.append(key)
            continue
        if key == "custom_sites" and not (isinstance(value, list) and all(isinstance(v, dict) for v in value)):
            skipped.append(key)
            continue
        kept[key] = value
    return kept, sorted(skipped)


def import_profile(source: str | Path, current_settings: dict, *, apply: bool = True) -> dict:
    """Merge a portable profile into defaults/current settings with a backup.

    Returns the merged settings and backup path. It never imports SQLite/media; the folders, files and
    database of this computer (``is_local``) stay as they are, and a value of another kind than the setting
    has (a text for a number, a list for the sites, a number no window field can show) is left out
    (``skipped``).  Raises ProfileError when the file is not a profile.
    """
    manifest, imported = read_profile(source)
    imported, skipped = clean_profile(imported, dict(current_settings or {}))
    from core.settings import deep_merge, DEFAULT_SETTINGS, save_settings
    from core.paths import SETTINGS_FILE, BACKUPS_DIR
    merged = deep_merge(DEFAULT_SETTINGS, merge_profile(dict(current_settings or {}), imported))
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
    return {"settings": merged, "backup": backup, "manifest": manifest, "source": str(source), "skipped": skipped}
