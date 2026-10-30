"""Safe Pixiv ugoira ZIP acquisition and local conversion.

The browser supplies the authenticated ZIP URL and official frame delays.
Local Booru downloads it through the central session policy, validates and
extracts it with bounded resources, then converts it to WebM (ffmpeg) or a
bounded animated WebP fallback.  A static preview is never silently accepted
as the canonical ugoira.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from core.database.connection import db
from core.download_policy import require_allowed
from core.paths import result_output_base


class UgoiraError(RuntimeError):
    pass


def _safe_id(value: object) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not raw:
        raise UgoiraError("missing Pixiv illust id")
    return raw[:32]


def _download_zip(settings: dict, *, illust_id: str, url: str, post_url: str, destination: Path, log, stop_check) -> None:
    from core.downloader_utils import _get_or_create_session, _rate_limited_get
    require_allowed(
        settings, operation="pixiv_ugoira", source_site="pixiv.net", media_url=url,
        post_url=post_url, payload={"illust_id": illust_id},
        create_intent=bool(settings.get("download_policy_create_intents", True)),
    )
    host = (urlparse(url).netloc or "i.pximg.net").lower().replace("www.", "")
    session = _get_or_create_session(host, settings, log=log)
    maximum = max(16, int(settings.get("pixiv_ugoira_max_zip_mb", 2048) or 2048)) * 1024 * 1024
    response = _rate_limited_get(
        session, url, settings=settings, timeout=120, stream=True, allow_redirects=True,
        headers={"Referer": post_url or f"https://www.pixiv.net/artworks/{illust_id}", "Accept": "application/zip,*/*;q=0.8"},
    )
    response.raise_for_status()
    try:
        length = int(response.headers.get("Content-Length") or 0)
    except Exception:
        length = 0
    if length > maximum:
        raise UgoiraError(f"ugoira ZIP too large: {length} > {maximum}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + f".part-{os.getpid()}-{time.time_ns()}")
    total = 0
    try:
        with open(tmp, "wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if stop_check and stop_check():
                    raise InterruptedError("ugoira download cancelled")
                if not chunk:
                    continue
                total += len(chunk)
                if total > maximum:
                    raise UgoiraError(f"ugoira ZIP exceeded limit: {total} > {maximum}")
                fh.write(chunk)
            fh.flush(); os.fsync(fh.fileno())
        with open(tmp, "rb") as fh:
            if fh.read(4) != b"PK\x03\x04":
                raise UgoiraError("Pixiv returned a non-ZIP response")
        os.replace(tmp, destination)
    finally:
        try:
            if tmp.exists(): tmp.unlink()
        except Exception:
            pass


def _extract(zip_path: Path, frames: list[dict], work: Path, *, settings: dict, stop_check) -> list[tuple[Path, int]]:
    max_members = max(1, int(settings.get("pixiv_ugoira_max_frames", 5000) or 5000))
    max_unpacked = max(64, int(settings.get("pixiv_ugoira_max_unpacked_mb", 4096) or 4096)) * 1024 * 1024
    requested = []
    for entry in frames or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("file") or entry.get("name") or "")
        delay = max(1, min(60000, int(entry.get("delay") or 100)))
        if name:
            requested.append((name, delay))
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if not infos or len(infos) > max_members:
            raise UgoiraError(f"invalid ugoira frame count: {len(infos)}")
        by_name = {info.filename: info for info in infos}
        if not requested:
            requested = [(info.filename, 100) for info in infos if not info.is_dir()]
        if len(requested) > max_members:
            raise UgoiraError("ugoira frame metadata exceeds configured limit")
        total = 0
        out = []
        for index, (name, delay) in enumerate(requested):
            if stop_check and stop_check():
                raise InterruptedError("ugoira conversion cancelled")
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or len(pure.parts) != 1:
                raise UgoiraError(f"unsafe ugoira member: {name}")
            info = by_name.get(name)
            if info is None or info.is_dir():
                raise UgoiraError(f"missing ugoira frame: {name}")
            total += int(info.file_size or 0)
            if total > max_unpacked:
                raise UgoiraError("ugoira unpacked size exceeds configured limit")
            suffix = Path(name).suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                raise UgoiraError(f"unsupported ugoira frame type: {suffix}")
            target = work / f"{index:06d}{suffix}"
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            out.append((target, delay))
        return out


def _ffmpeg_convert(frames: list[tuple[Path, int]], output: Path, *, stop_check) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    concat = output.with_suffix(".concat.txt")
    lines = []
    for path, delay in frames:
        escaped = path.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
        lines.append(f"duration {max(0.001, delay / 1000.0):.6f}")
    if frames:
        escaped = frames[-1][0].resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-lossless", "1", str(output)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        while proc.poll() is None:
            if stop_check and stop_check():
                proc.terminate()
                try: proc.wait(timeout=3)
                except Exception: proc.kill()
                raise InterruptedError("ugoira conversion cancelled")
            time.sleep(0.1)
        if proc.returncode != 0:
            err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace")[-1000:]
            raise UgoiraError(f"ffmpeg ugoira conversion failed: {err}")
        return output.is_file() and output.stat().st_size > 0
    finally:
        try: concat.unlink(missing_ok=True)
        except Exception: pass


def _pillow_convert(frames: list[tuple[Path, int]], output: Path, *, settings: dict, stop_check) -> bool:
    from PIL import Image
    if not frames:
        raise UgoiraError("ugoira has no frames")
    with Image.open(frames[0][0]) as first:
        width, height = first.size
    estimate = width * height * 4 * len(frames)
    cap = max(64, int(settings.get("pixiv_ugoira_pillow_max_mb", 512) or 512)) * 1024 * 1024
    if estimate > cap:
        raise UgoiraError(f"ffmpeg unavailable and Pillow RAM estimate exceeds limit: {estimate} > {cap}")
    images = []
    try:
        for path, _delay in frames:
            if stop_check and stop_check():
                raise InterruptedError("ugoira conversion cancelled")
            with Image.open(path) as im:
                images.append(im.convert("RGBA").copy())
        durations = [delay for _path, delay in frames]
        images[0].save(output, format="WEBP", save_all=True, append_images=images[1:], duration=durations, loop=0, lossless=True, method=4)
        return output.is_file() and output.stat().st_size > 0
    finally:
        for image in images:
            try: image.close()
            except Exception: pass


def process_ugoira(settings: dict, *, item: dict, log=None, stop_check=None) -> dict:
    log = log or (lambda _m: None)
    ugoira = item.get("ugoira") if isinstance(item.get("ugoira"), dict) else {}
    zip_url = str(ugoira.get("zip_url") or item.get("file_url") or "").strip()
    illust_id = _safe_id(item.get("post_id"))
    if not zip_url.lower().startswith(("http://", "https://")):
        raise UgoiraError("missing ugoira ZIP URL")
    base = result_output_base(settings) / "pixiv" / "ugoira" / illust_id
    base.mkdir(parents=True, exist_ok=True)
    zip_path = base / f"{illust_id}.zip"
    _download_zip(settings, illust_id=illust_id, url=zip_url, post_url=str(item.get("post_url") or ""), destination=zip_path, log=log, stop_check=stop_check)
    frames_meta = list(ugoira.get("frames") or [])
    with tempfile.TemporaryDirectory(prefix=f"lb-ugoira-{illust_id}-", dir=str(base)) as temp:
        frames = _extract(zip_path, frames_meta, Path(temp), settings=settings, stop_check=stop_check)
        webm = base / f"{illust_id}.webm"
        converted = webm if _ffmpeg_convert(frames, webm, stop_check=stop_check) else base / f"{illust_id}.webp"
        if converted.suffix == ".webp" and not _pillow_convert(frames, converted, settings=settings, stop_check=stop_check):
            raise UgoiraError("ugoira WebP conversion produced no output")
    result = {"zip_path": str(zip_path), "converted_path": str(converted), "format": converted.suffix.lstrip("."), "frames": frames_meta, "original_url": zip_url, "illust_id": illust_id}
    item["_ugoira_asset"] = result
    return result


def persist_ugoira_asset(settings: dict, *, image_id: int, asset: dict) -> None:
    if not image_id or not asset:
        return
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute(
            """INSERT INTO pixiv_ugoira_assets(image_id,illust_id,zip_path,converted_path,original_url,frames_json,conversion_format,conversion_status,last_error,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,'converted','',?,?)
               ON CONFLICT(illust_id) DO UPDATE SET image_id=excluded.image_id,zip_path=excluded.zip_path,converted_path=excluded.converted_path,
                 original_url=excluded.original_url,frames_json=excluded.frames_json,conversion_format=excluded.conversion_format,
                 conversion_status='converted',last_error='',updated_at=excluded.updated_at""",
            (int(image_id), str(asset.get("illust_id") or ""), str(asset.get("zip_path") or ""), str(asset.get("converted_path") or ""),
             str(asset.get("original_url") or ""), json.dumps(asset.get("frames") or [], ensure_ascii=False), str(asset.get("format") or ""), now, now),
        )
