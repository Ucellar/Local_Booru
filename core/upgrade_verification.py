"""Bounded, local quality and identity verification for media upgrades.

The module deliberately avoids whole-video/all-frame comparison. Static media
is decoded once; animations and videos use container metadata plus a bounded set
of sampled frame fingerprints under the existing RAM guard.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


LOSSLESS_FORMATS = {"png", "webp_lossless", "tiff", "bmp"}
ANIMATED_FORMATS = {"gif", "apng", "webp_animation"}
VIDEO_FORMATS = {"mp4", "webm", "mkv", "mov", "avi", "m4v"}


@dataclass(frozen=True)
class MediaDescriptor:
    path: str = ""
    md5: str = ""
    width: int = 0
    height: int = 0
    file_size: int = 0
    format: str = ""
    frame_count: int = 1
    duration: float = 0.0
    has_alpha: bool = False
    is_video: bool = False
    is_animation: bool = False
    source_identity: str = ""
    fps: float = 0.0
    codec: str = ""
    has_audio: bool = False


@dataclass(frozen=True)
class VerificationPlan:
    media_kind: str
    sample_positions: tuple[float, ...]
    estimated_peak_bytes: int
    full_decode: bool = False
    reason: str = ""


@dataclass(frozen=True)
class QualityDecision:
    decision: str  # better / worse / same / variant / uncertain
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)


def estimate_decode_peak_bytes(width: int, height: int, *, frames_in_memory: int = 2, channels: int = 4) -> int:
    pixels = max(1, int(width or 0)) * max(1, int(height or 0))
    return pixels * max(1, int(channels)) * max(1, int(frames_in_memory))


def sampled_positions(duration: float = 0.0, frame_count: int = 0, *, max_samples: int = 7) -> tuple[float, ...]:
    count = max(2, min(15, int(max_samples or 7)))
    return tuple(round(i / (count - 1), 6) for i in range(count))


def build_verification_plan(desc: MediaDescriptor, settings: dict | None = None) -> VerificationPlan:
    settings = settings or {}
    fmt = str(desc.format or "").strip().lower()
    max_samples = int(settings.get("upgrade_verification_max_samples", 7) or 7)
    if desc.is_video or fmt in VIDEO_FORMATS:
        positions = sampled_positions(desc.duration, desc.frame_count, max_samples=max_samples)
        return VerificationPlan(
            "video", positions,
            estimate_decode_peak_bytes(desc.width, desc.height, frames_in_memory=2),
            full_decode=False,
            reason="container metadata + bounded sampled-frame fingerprints",
        )
    if desc.is_animation or fmt in ANIMATED_FORMATS or int(desc.frame_count or 1) > 1:
        positions = sampled_positions(desc.duration, desc.frame_count, max_samples=max_samples)
        return VerificationPlan(
            "animation", positions,
            estimate_decode_peak_bytes(desc.width, desc.height, frames_in_memory=2),
            full_decode=False,
            reason="frame count/duration + bounded sampled-frame fingerprints",
        )
    return VerificationPlan(
        "static", (0.0,), estimate_decode_peak_bytes(desc.width, desc.height, frames_in_memory=2),
        full_decode=True,
        reason="single-image bounded decode",
    )


def ram_budget_allows(plan: VerificationPlan, settings: dict | None = None) -> tuple[bool, str]:
    settings = settings or {}
    try:
        from core.memory_guard import process_memory_snapshot
        snap = process_memory_snapshot()
        available = int(snap.get("available_bytes") or 0)
    except Exception:
        available = 0
    reserve_mb = int(settings.get("upgrade_verification_ram_reserve_mb", 2048) or 2048)
    hard_job_mb = int(settings.get("upgrade_verification_job_cap_mb", 1024) or 1024)
    required = int(plan.estimated_peak_bytes or 0)
    if required > hard_job_mb * 1024 * 1024:
        return False, f"estimated decode {required} exceeds per-job cap {hard_job_mb} MiB"
    if available and available - required < reserve_mb * 1024 * 1024:
        return False, f"insufficient free RAM after reserve {reserve_mb} MiB"
    return True, "ok"


def _parse_ratio(value: str) -> float:
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return 0.0
    try:
        if "/" in text:
            a, b = text.split("/", 1)
            return float(a) / max(1e-9, float(b))
        return float(text)
    except Exception:
        return 0.0


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb", buffering=0) as fh:
        buf = bytearray(256 * 1024)
        view = memoryview(buf)
        while True:
            n = fh.readinto(buf)
            if not n:
                break
            h.update(view[:n])
    return h.hexdigest()


def _ffprobe_descriptor(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    if proc.returncode != 0:
        raise ValueError((proc.stderr or "ffprobe failed")[:300])
    payload = json.loads(proc.stdout or "{}")
    streams = list(payload.get("streams") or [])
    video = next((s for s in streams if str(s.get("codec_type")) == "video"), {})
    audio = any(str(s.get("codec_type")) == "audio" for s in streams)
    fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    duration = float(video.get("duration") or fmt.get("duration") or 0.0)
    frames = int(video.get("nb_frames") or 0)
    fps = _parse_ratio(str(video.get("avg_frame_rate") or video.get("r_frame_rate") or ""))
    if not frames and duration and fps:
        frames = int(round(duration * fps))
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "duration": duration,
        "frame_count": max(1, frames),
        "fps": fps,
        "codec": str(video.get("codec_name") or ""),
        "has_audio": audio,
        "format": str((fmt.get("format_name") or path.suffix.lstrip("."))).split(",", 1)[0].lower(),
    }


def describe_media(path: str | Path, *, compute_md5: bool = True, source_identity: str = "") -> MediaDescriptor:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(str(p))
    suffix = p.suffix.lower().lstrip(".")
    file_size = int(p.stat().st_size)
    if suffix in VIDEO_FORMATS:
        data = _ffprobe_descriptor(p)
        return MediaDescriptor(
            path=str(p), md5=_md5(p) if compute_md5 else "", file_size=file_size,
            width=int(data["width"]), height=int(data["height"]), format=str(data["format"]),
            frame_count=int(data["frame_count"]), duration=float(data["duration"]),
            is_video=True, is_animation=False, source_identity=str(source_identity or ""),
            fps=float(data["fps"]), codec=str(data["codec"]), has_audio=bool(data["has_audio"]),
        )
    from PIL import Image
    with Image.open(p) as im:
        width, height = map(int, im.size)
        fmt = str(im.format or suffix or "").lower()
        n_frames = max(1, int(getattr(im, "n_frames", 1) or 1))
        has_alpha = "A" in str(im.mode or "") or "transparency" in im.info
        duration_ms = 0
        if n_frames > 1:
            # Reading frame metadata is bounded and does not keep frames in RAM.
            for idx in range(min(n_frames, 10000)):
                try:
                    im.seek(idx)
                    duration_ms += int(im.info.get("duration") or 0)
                except EOFError:
                    break
        if fmt == "webp" and bool(im.info.get("lossless")):
            fmt = "webp_lossless"
        elif fmt == "png" and n_frames > 1:
            fmt = "apng"
        elif fmt == "webp" and n_frames > 1:
            fmt = "webp_animation"
        return MediaDescriptor(
            path=str(p), md5=_md5(p) if compute_md5 else "", width=width, height=height,
            file_size=file_size, format=fmt, frame_count=n_frames,
            duration=max(0.0, duration_ms / 1000.0), has_alpha=bool(has_alpha),
            is_video=False, is_animation=n_frames > 1, source_identity=str(source_identity or ""),
        )


def compare_quality(old: MediaDescriptor, new: MediaDescriptor) -> QualityDecision:
    reasons: list[str] = []
    old_kind = "video" if old.is_video else "animation" if old.is_animation or old.frame_count > 1 else "static"
    new_kind = "video" if new.is_video else "animation" if new.is_animation or new.frame_count > 1 else "static"
    if old_kind != new_kind:
        return QualityDecision("variant", 1.0, (f"media kind changed: {old_kind}->{new_kind}",))
    if old_kind in {"video", "animation"}:
        if old.duration and new.duration and abs(float(old.duration) - float(new.duration)) > max(0.5, 0.02 * max(old.duration, new.duration)):
            return QualityDecision("variant", 0.99, ("duration mismatch",))
        if old.frame_count > 1 and new.frame_count > 1 and old.frame_count != new.frame_count:
            relative = abs(old.frame_count - new.frame_count) / max(old.frame_count, new.frame_count)
            if relative > 0.03:
                return QualityDecision("uncertain", 0.7, ("frame-count mismatch",))

    old_pixels = max(1, int(old.width or 0) * int(old.height or 0))
    new_pixels = max(1, int(new.width or 0) * int(new.height or 0))
    pixel_ratio = new_pixels / old_pixels
    if pixel_ratio >= 1.5 and new.width >= old.width and new.height >= old.height:
        reasons.append(f"pixel area x{pixel_ratio:.2f}")
    elif pixel_ratio < 0.95:
        return QualityDecision("worse", 0.95, (f"lower pixel area x{pixel_ratio:.2f}",))

    old_fmt = str(old.format or "").lower(); new_fmt = str(new.format or "").lower()
    if old_fmt in LOSSLESS_FORMATS and new_fmt in {"jpg", "jpeg"}:
        return QualityDecision("uncertain", 0.8, ("lossless-to-lossy transition", *reasons))
    if new_fmt in LOSSLESS_FORMATS and old_fmt in {"jpg", "jpeg"}:
        reasons.append("lossy-to-lossless transition")
    if new.has_alpha and not old.has_alpha:
        reasons.append("new alpha channel")
    if old_kind == "video":
        if old.has_audio and not new.has_audio:
            return QualityDecision("worse", 0.95, ("audio track lost",))
        if new.codec and old.codec and new.codec != old.codec:
            reasons.append(f"codec {old.codec}->{new.codec}")
    if old.file_size and new.file_size:
        size_ratio = new.file_size / max(1, old.file_size)
        if size_ratio >= 1.25:
            reasons.append(f"file size x{size_ratio:.2f}")
        elif pixel_ratio > 1.2 and size_ratio < 0.5 and new_fmt in {"jpg", "jpeg"}:
            return QualityDecision("uncertain", 0.75, ("larger dimensions but much smaller lossy file",))

    if reasons and pixel_ratio >= 1.2:
        return QualityDecision("better", 0.9, tuple(reasons))
    if old.md5 and new.md5 and old.md5.lower() == new.md5.lower():
        return QualityDecision("same", 1.0, ("exact MD5",))
    return QualityDecision("uncertain", 0.5, tuple(reasons or ["insufficient quality evidence"]))
