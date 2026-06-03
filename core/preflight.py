"""Free-space and mass-download preflight helpers."""
from __future__ import annotations

import shutil
from pathlib import Path

from core.paths import result_output_base


def format_bytes(value: int | float) -> str:
    n = float(value or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024 or unit == "ТБ":
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} ТБ"


def extract_known_size(post: dict) -> int:
    if not isinstance(post, dict):
        return 0
    values = [post.get("file_size"), post.get("filesize"), post.get("size")]
    f = post.get("file")
    if isinstance(f, dict):
        values += [f.get("size"), f.get("file_size")]
    for value in values:
        try:
            v = int(value or 0)
            if v > 0:
                return v
        except Exception:
            pass
    return 0


def output_disk_info(settings: dict) -> dict:
    folder = result_output_base(settings)
    folder.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(folder)
    reserve = int(float(settings.get("disk_free_reserve_gb", 2.0) or 2.0) * 1024 ** 3)
    return {"folder": str(folder), "total": int(usage.total), "used": int(usage.used), "free": int(usage.free), "reserve": reserve}


def ensure_space_for_write(settings: dict, destination: str | Path, incoming_bytes: int = 0) -> tuple[bool, str]:
    p = Path(destination)
    p.parent.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(p.parent)
    reserve = int(float(settings.get("disk_free_reserve_gb", 2.0) or 2.0) * 1024 ** 3)
    need = max(0, int(incoming_bytes or 0)) + reserve
    if int(usage.free) < need:
        return False, f"Недостаточно места: свободно {format_bytes(usage.free)}, требуется минимум {format_bytes(need)} с резервом."
    return True, ""


def build_large_download_plan(settings: dict, groups: list[list[dict]]) -> dict:
    total = len(groups)
    known_files = 0
    known_bytes = 0
    for group in groups:
        # Same MD5 group downloads only one file. Use first candidate with a known size.
        for seed in group:
            size = extract_known_size(seed.get("post") or {})
            if size > 0:
                known_files += 1
                known_bytes += size
                break
    disk = output_disk_info(settings)
    threshold = max(1, int(settings.get("large_download_warning_count", 1000) or 1000))
    warn = total >= threshold or known_bytes > max(0, disk["free"] - disk["reserve"])
    return {
        "groups": total,
        "known_files": known_files,
        "known_bytes": known_bytes,
        "disk": disk,
        "warn": warn,
        "not_enough_space": known_bytes > max(0, disk["free"] - disk["reserve"]),
    }
