"""Export-copy helpers for drag-and-drop out of the gallery.

The archive original is never moved or modified.  A temporary copy is created
and, when ExifTool is available, Local Booru source/tag metadata is embedded in
standard XMP/IPTC fields understood by viewers such as XnView.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
import os
import shutil
import subprocess
import tempfile
import time
import sys
import urllib.request
import zipfile
import threading
from typing import Iterable


def _safe_filename(name: str) -> str:
    name = str(name or "export")
    for ch in '<>:"/\\|?*\x00':
        name = name.replace(ch, "_")
    return name[:180] or "export"


def drag_export_root(settings: dict | None = None) -> Path:
    try:
        from core.paths import CACHE_DIR
        base = Path(CACHE_DIR) / "drag_export"
    except Exception:
        base = Path(tempfile.gettempdir()) / "Local_Booru_drag_export"
    base.mkdir(parents=True, exist_ok=True)
    return base


def cleanup_old_drag_exports(settings: dict | None = None, *, max_age_hours: int = 24) -> None:
    root = drag_export_root(settings)
    cutoff = time.time() - max(1, int(max_age_hours or 24)) * 3600
    try:
        for child in root.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                elif child.is_file() and child.stat().st_mtime < cutoff:
                    child.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


_EXIFTOOL_LOCK = threading.Lock()
_EXIFTOOL_AUTODOWNLOAD_ATTEMPTED = False


def _app_install_dir() -> Path:
    try:
        from core.paths import app_install_dir
        return Path(app_install_dir())
    except Exception:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parents[1]


def _embedded_exiftool_dirs(settings: dict | None = None) -> list[Path]:
    """Locations that may travel with the program or the selected workspace."""
    out: list[Path] = []
    try:
        from core.paths import DATA_DIR, app_install_dir
        out.append(Path(DATA_DIR) / "tools" / "exiftool")
        out.append(Path(DATA_DIR) / "cache" / "tools" / "exiftool")
        install = Path(app_install_dir())
        out.append(install / "tools" / "exiftool")
        out.append(install / "tools")
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        try:
            out.append(Path(sys.executable).resolve().parent / "tools" / "exiftool")
            out.append(Path(sys.executable).resolve().parent / "tools")
        except Exception:
            pass
        try:
            out.append(Path(getattr(sys, "_MEIPASS")) / "tools" / "exiftool")
            out.append(Path(getattr(sys, "_MEIPASS")) / "tools")
        except Exception:
            pass
    seen: set[str] = set(); deduped: list[Path] = []
    for d in out:
        try:
            key = str(d.resolve()).lower()
        except Exception:
            key = str(d).lower()
        if key in seen:
            continue
        seen.add(key); deduped.append(d)
    return deduped


def _exiftool_in_dir(d: Path) -> str:
    for name in ("exiftool.exe", "exiftool(-k).exe", "exiftool"):
        p = d / name
        if p.exists() and p.is_file():
            return str(p)
    return ""


def _find_exiftool_no_download(settings: dict | None = None) -> str:
    settings = settings or {}
    candidates = [settings.get("exiftool_path"), settings.get("metadata_exiftool_path")]
    for c in candidates:
        if not c:
            continue
        c = str(c)
        if os.path.isabs(c) and Path(c).exists():
            return c
        found = shutil.which(c)
        if found:
            return found
    for d in _embedded_exiftool_dirs(settings):
        found = _exiftool_in_dir(d)
        if found:
            return found
    for c in ("exiftool.exe", "exiftool"):
        found = shutil.which(c)
        if found:
            return found
    return ""


def _install_dir_for_exiftool(settings: dict | None = None) -> Path:
    try:
        from core.paths import DATA_DIR
        d = Path(DATA_DIR) / "tools" / "exiftool"
    except Exception:
        d = _app_install_dir() / "tools" / "exiftool"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_embedded_exiftool(settings: dict | None = None) -> str:
    """Find bundled ExifTool, or auto-install it into the workspace tools folder.

    In release builds the preferred layout is:
        <app>/tools/exiftool/exiftool.exe
        <app>/tools/exiftool/exiftool_files/

    In source/dev mode, if ExifTool is missing and internet is available, we
    download the official Windows 64-bit zip into Local_Booru_Archive/settings/tools.
    Metadata export still succeeds without this by creating the copy, but then it
    cannot embed XMP/IPTC tags.
    """
    global _EXIFTOOL_AUTODOWNLOAD_ATTEMPTED
    existing = _find_exiftool_no_download(settings)
    if existing:
        return existing
    if os.environ.get("LOCAL_BOORU_DISABLE_EXIFTOOL_AUTODOWNLOAD", "").strip():
        return ""
    if os.name != "nt":
        return ""
    with _EXIFTOOL_LOCK:
        existing = _find_exiftool_no_download(settings)
        if existing:
            return existing
        if _EXIFTOOL_AUTODOWNLOAD_ATTEMPTED:
            return ""
        _EXIFTOOL_AUTODOWNLOAD_ATTEMPTED = True
        dest = _install_dir_for_exiftool(settings)
        tmp_zip = dest / "exiftool_download.zip"
        urls = [
            "https://downloads.sourceforge.net/project/exiftool/exiftool-13.59_64.zip",
            "https://sourceforge.net/projects/exiftool/files/exiftool-13.59_64.zip/download",
        ]
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "LocalBooru/1.0"})
                with urllib.request.urlopen(req, timeout=60) as r, open(tmp_zip, "wb") as f:
                    ctype = str(r.headers.get("Content-Type", "")).lower()
                    data0 = r.read(4096)
                    if b"<html" in data0.lower() or "text/html" in ctype:
                        raise RuntimeError("download returned html")
                    f.write(data0)
                    shutil.copyfileobj(r, f)
                with zipfile.ZipFile(tmp_zip, "r") as zf:
                    members = zf.namelist()
                    root_prefix = ""
                    for m in members:
                        low = m.replace("\\", "/").lower()
                        if low.endswith("exiftool(-k).exe") or low.endswith("exiftool.exe"):
                            root_prefix = m.rsplit("/", 1)[0] if "/" in m else ""
                            break
                    for m in members:
                        low = m.replace("\\", "/").lower()
                        if not (low.endswith("exiftool(-k).exe") or low.endswith("exiftool.exe") or "/exiftool_files/" in ("/" + low)):
                            continue
                        rel = m
                        if root_prefix and rel.startswith(root_prefix + "/"):
                            rel = rel[len(root_prefix) + 1:]
                        target = dest / rel
                        if m.endswith("/"):
                            target.mkdir(parents=True, exist_ok=True); continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(m) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)
                        if target.name.lower() == "exiftool(-k).exe":
                            renamed = target.with_name("exiftool.exe")
                            try:
                                if renamed.exists():
                                    renamed.unlink()
                                target.rename(renamed)
                            except Exception:
                                shutil.copy2(target, renamed)
                try:
                    tmp_zip.unlink(missing_ok=True)
                except Exception:
                    pass
                found = _find_exiftool_no_download(settings)
                if found:
                    return found
            except Exception:
                try:
                    tmp_zip.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
    return ""


def _find_exiftool(settings: dict | None = None) -> str:
    return _ensure_embedded_exiftool(settings)


def _iter_groups(item: dict) -> list[tuple[str, str]]:
    groups = item.get("tag_groups") or {}
    out: list[tuple[str, str]] = []
    if isinstance(groups, dict):
        for group, values in groups.items():
            group = str(group or "general")
            for tag in values or []:
                tag = str(tag or "").strip()
                if tag:
                    out.append((group, tag))
    if not out:
        for tag in item.get("tags") or []:
            tag = str(tag or "").strip()
            if tag:
                out.append(("general", tag))
    seen = set(); deduped = []
    for group, tag in out:
        key = (group.lower(), tag.lower())
        if key in seen:
            continue
        seen.add(key); deduped.append((group, tag))
    return deduped


def _source_urls(item: dict) -> list[str]:
    urls = []
    for src in item.get("sources") or []:
        if isinstance(src, dict):
            u = str(src.get("url") or "").strip()
        else:
            u = str(src or "").strip()
        if u and u not in urls:
            urls.append(u)
    return urls


def _write_metadata_with_exiftool(settings: dict, path: Path, item: dict) -> bool:
    exiftool = _find_exiftool(settings)
    if not exiftool:
        return False
    tags = _iter_groups(item)
    urls = _source_urls(item)
    args = [exiftool, "-overwrite_original", "-charset", "filename=utf8"]
    # Clear then append so dragged copies reflect current Local Booru metadata.
    args += ["-XMP-dc:Subject=", "-IPTC:Keywords=", "-XMP-lr:HierarchicalSubject="]
    for group, tag in tags:
        args.append(f"-XMP-dc:Subject+={tag}")
        args.append(f"-IPTC:Keywords+={tag}")
        args.append(f"-XMP-lr:HierarchicalSubject+=Local Booru|{group}|{tag}")
    if urls:
        desc = "Local Booru sources:\n" + "\n".join(urls)
        args.append(f"-XMP-dc:Description={desc}")
        args.append("-XMP-dc:Source=")
        for url in urls[:20]:
            args.append(f"-XMP-dc:Source+={url}")
    args.append(str(path))
    try:
        cp = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        return cp.returncode == 0
    except Exception:
        return False


def export_gallery_item_copy(settings: dict, item: dict, export_dir: Path | None = None) -> Path:
    src = Path(str((item or {}).get("path") or ""))
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(str(src))
    if export_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        export_dir = drag_export_root(settings) / stamp
    export_dir.mkdir(parents=True, exist_ok=True)
    out = export_dir / _safe_filename(src.name)
    # Avoid collision when dragging several files with same name.
    if out.exists():
        stem, suffix = out.stem, out.suffix
        i = 2
        while True:
            candidate = export_dir / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                out = candidate; break
            i += 1
    shutil.copy2(src, out)
    _write_metadata_with_exiftool(settings or {}, out, item or {})
    return out
