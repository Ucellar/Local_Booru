"""Offline NO_MATCH visual classifier.

Purpose: only coarse local triage for the NO_MATCH page:

    real/PHOTO  - camera/photo-like file
    booru       - drawn/anime/cartoon/3D/game-render-like file

No network, no API, no cloud.  The preferred backend is a local CLIP model
stored on disk.  The old deterministic heuristic remains as an explicit
fallback only; it is not reliable enough to be the default classifier.
"""
from __future__ import annotations

import hashlib
import math
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

PHOTO = "real"
BOORU = "booru"
UNKNOWN = "unknown"
HEURISTIC_MODEL_NAME = "local_visual_real_booru_v1"
AI_MODEL_PREFIX = "local_clip_photo_illustration"
MODEL_NAME = HEURISTIC_MODEL_NAME  # legacy import name used by old tests/UI

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
_VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
_AI_CACHE: dict[str, Any] = {}

PHOTO_PROMPTS = [
    "a real photograph of a person",
    "a camera photo",
    "a real-life photo",
    "a cosplay photograph",
    "a realistic photograph",
    "a photo taken with a camera",
]
ILLUSTRATION_PROMPTS = [
    "an anime illustration",
    "a digital drawing",
    "a cartoon artwork",
    "a manga style artwork",
    "a 3d render or cg artwork",
    "game character art",
]

# Files needed for transformers.CLIPModel/CLIPProcessor.  This is a plain
# model download, not an image API: user media is never uploaded anywhere.
CLIP_DOWNLOAD_REPO = "openai/clip-vit-base-patch32"
CLIP_DOWNLOAD_BASE_URL = "https://huggingface.co/openai/clip-vit-base-patch32/resolve/main"
CLIP_REQUIRED_FILES = [
    "config.json",
    "preprocessor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "special_tokens_map.json",
    "pytorch_model.bin",
]
CLIP_LARGE_FILES = {"pytorch_model.bin": 100 * 1024 * 1024, "model.safetensors": 100 * 1024 * 1024}
# Used only for progress display. The exact HTTP size may differ a little, but
# this prevents the UI from looking frozen when Hugging Face does not expose a
# final Content-Length through redirects.
CLIP_EXPECTED_SIZES = {"pytorch_model.bin": 605 * 1024 * 1024}


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(float(v), hi))
    except Exception:
        return lo


def _open_analysis_image(path: Path, size: int = 224):
    from PIL import Image, ImageOps
    from core.image_safe import configure_pillow, safe_thumbnail_path

    configure_pillow()
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in _VIDEO_EXTS:
        thumb = safe_thumbnail_path(p, max(384, size), max(384, size))
        if thumb:
            p = Path(thumb)
    if suffix not in _IMAGE_EXTS and p.suffix.lower() not in _IMAGE_EXTS:
        raise ValueError("unsupported visual media type")
    im = Image.open(p)
    try:
        im.seek(0)
    except Exception:
        pass
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    else:
        im = im.convert("RGB")
    if im.width < 8 or im.height < 8:
        raise ValueError("image too small for visual classification")
    return im


def _analysis_thumb(path: Path):
    im = _open_analysis_image(path, size=128)
    from PIL import Image
    im.thumbnail((128, 128), Image.Resampling.BILINEAR)
    return im


def _rgb_to_hsv_components(r: int, g: int, b: int) -> tuple[float, float]:
    mx = max(r, g, b) / 255.0
    mn = min(r, g, b) / 255.0
    v = mx
    s = 0.0 if mx <= 0 else (mx - mn) / mx
    return s, v


def _features(path: Path) -> dict[str, float]:
    im = _analysis_thumb(path)
    w, h = im.size
    get_pixels = getattr(im, "get_flattened_data", im.getdata)
    px = list(get_pixels())
    n = max(1, len(px))

    bins = Counter(((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4) for r, g, b in px)
    entropy = 0.0
    for c in bins.values():
        p = c / n
        entropy -= p * math.log2(p)
    entropy_norm = _clamp(entropy / 11.0)
    unique_norm = _clamp(len(bins) / min(4096, n))
    top_ratio = _clamp((bins.most_common(1)[0][1] / n) if bins else 1.0)

    grays: list[int] = []
    sats: list[float] = []
    vals: list[float] = []
    for r, g, b in px:
        grays.append((299 * r + 587 * g + 114 * b) // 1000)
        s, v = _rgb_to_hsv_components(r, g, b)
        sats.append(s)
        vals.append(v)

    diffs: list[int] = []
    for y in range(h):
        row = y * w
        for x in range(w - 1):
            diffs.append(abs(grays[row + x] - grays[row + x + 1]))
    for y in range(h - 1):
        row = y * w
        nxt = (y + 1) * w
        for x in range(w):
            diffs.append(abs(grays[row + x] - grays[nxt + x]))
    if not diffs:
        diffs = [0]
    mean_diff = sum(diffs) / len(diffs)
    texture = _clamp(mean_diff / 42.0)
    flat_ratio = _clamp(sum(1 for d in diffs if d <= 3) / len(diffs))
    hard_edge_ratio = _clamp(sum(1 for d in diffs if d >= 44) / len(diffs))

    sat_mean = sum(sats) / n
    val_mean = sum(vals) / n
    sat_std = math.sqrt(sum((x - sat_mean) ** 2 for x in sats) / n)
    val_std = math.sqrt(sum((x - val_mean) ** 2 for x in vals) / n)

    return {
        "entropy": entropy_norm,
        "unique": unique_norm,
        "top_color": top_ratio,
        "texture": texture,
        "flat": flat_ratio,
        "hard_edge": hard_edge_ratio,
        "sat_mean": _clamp(sat_mean),
        "sat_std": _clamp(sat_std * 2.0),
        "val_std": _clamp(val_std * 2.0),
    }


def _heuristic_classify(path: str | Path, settings: dict | None = None) -> dict[str, Any]:
    settings = settings or {}
    p = Path(path)
    try:
        f = _features(p)
        photo_score = (
            0.38 * f["entropy"]
            + 0.24 * f["texture"]
            + 0.16 * f["unique"]
            + 0.14 * f["val_std"]
            + 0.08 * f["sat_std"]
            - 0.18 * f["flat"]
            - 0.13 * f["hard_edge"]
            - 0.07 * f["sat_mean"]
            - 0.05 * f["top_color"]
        )
        threshold = float(settings.get("visual_nomatch_real_threshold", 0.34) or 0.34)
        status = PHOTO if photo_score >= threshold else BOORU
        margin = abs(photo_score - threshold)
        confidence = _clamp(0.50 + margin * 1.55)
        return {
            "visual_status": status,
            "visual_confidence": round(confidence, 4),
            "visual_model": HEURISTIC_MODEL_NAME,
            "visual_checked_at": int(time.time()),
            "score": round(photo_score, 4),
            "features": f,
        }
    except Exception as exc:
        return {
            "visual_status": UNKNOWN,
            "visual_confidence": 0.0,
            "visual_model": HEURISTIC_MODEL_NAME,
            "visual_checked_at": int(time.time()),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _bundled_clip_model_candidates(settings: dict | None = None) -> list[Path]:
    """Return model locations that travel with the program.

    The normal user build must be self-contained: the user should not have to
    find or download a CLIP model manually.  The optional setting below is only
    a developer/debug override.
    """
    candidates: list[Path] = []
    try:
        from core.paths import app_install_dir, app_base_dir
        install = app_install_dir()
        app_base = app_base_dir()
        candidates.extend([
            install / "models" / "clip",
            install / "assets" / "models" / "clip",
            app_base / "models" / "clip",
            app_base / "assets" / "models" / "clip",
        ])
    except Exception:
        pass
    try:
        import sys
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            root = Path(str(meipass))
            candidates.extend([root / "models" / "clip", root / "assets" / "models" / "clip"])
    except Exception:
        pass
    # Keep the portable archive path as a last-resort compatibility location,
    # but it is not the primary UX anymore.
    try:
        from core.paths import suggested_settings_storage_dir
        candidates.append(suggested_settings_storage_dir(settings or {}) / "models" / "clip")
    except Exception:
        pass
    # Stable fallback for tests/source checkouts.
    candidates.append(Path("models") / "clip")
    out: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        try:
            key = str(p.expanduser().resolve())
        except Exception:
            key = str(p)
        if key not in seen:
            out.append(p.expanduser())
            seen.add(key)
    return out


def auto_clip_model_dir(settings: dict | None = None) -> Path:
    """Writable location for first-run model download.

    Bundled builds may ship models/clip next to the program, but a small exe/zip
    can instead download the model once into the archive settings branch.  This
    keeps the user's media offline: only model weights are downloaded.
    """
    settings = settings or {}
    explicit = str(settings.get("visual_nomatch_clip_model_dir", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    try:
        from core.paths import suggested_settings_storage_dir
        return suggested_settings_storage_dir(settings) / "models" / "clip"
    except Exception:
        return Path("models") / "clip"


def _settings_model_dir(settings: dict | None) -> Path:
    settings = settings or {}
    explicit = str(settings.get("visual_nomatch_clip_model_dir", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    for candidate in _bundled_clip_model_candidates(settings):
        if _clip_available_files(candidate):
            return candidate
    dl = auto_clip_model_dir(settings)
    if _clip_available_files(dl):
        return dl
    # Return the writable auto-download location for clear error messages.
    return dl


def _path_fingerprint(p: Path) -> str:
    try:
        text = str(p.resolve())
    except Exception:
        text = str(p)
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:10]


def current_visual_model_name(settings: dict | None = None) -> str:
    settings = settings or {}
    backend = str(settings.get("visual_nomatch_backend", "clip_local") or "clip_local").strip().lower()
    if backend in ("heuristic", "local_heuristic", "v1"):
        return HEURISTIC_MODEL_NAME
    model_dir = _settings_model_dir(settings)
    return f"{AI_MODEL_PREFIX}:{_path_fingerprint(model_dir)}"


def _clip_available_files(model_dir: Path) -> bool:
    if not model_dir.exists() or not model_dir.is_dir():
        return False
    names = {p.name for p in model_dir.iterdir() if p.is_file()}
    required_small = {
        "config.json",
        "preprocessor_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.json",
        "merges.txt",
        "special_tokens_map.json",
    }
    if not required_small.issubset(names):
        return False
    for weight_name in ("pytorch_model.bin", "model.safetensors"):
        weight = model_dir / weight_name
        try:
            if weight.is_file() and weight.stat().st_size >= CLIP_LARGE_FILES.get(weight_name, 100 * 1024 * 1024):
                return True
        except Exception:
            pass
    return False


def missing_clip_model_files(model_dir: Path | None = None, settings: dict | None = None) -> list[str]:
    model_dir = Path(model_dir) if model_dir is not None else _settings_model_dir(settings)
    missing: list[str] = []
    for name in CLIP_REQUIRED_FILES:
        p = model_dir / name
        min_size = CLIP_LARGE_FILES.get(name, 1)
        try:
            if not p.is_file() or p.stat().st_size < min_size:
                missing.append(name)
        except Exception:
            missing.append(name)
    return missing


def _format_bytes(n: int | float | None) -> str:
    try:
        v = float(n or 0)
    except Exception:
        v = 0.0
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.1f} {unit}" if unit != "B" else f"{int(v)} B"
        v /= 1024.0
    return f"{v:.1f} GB"


def _download_one_file(url: str, dest: Path, *, progress=None, stop_check=None, label: str = "") -> None:
    """Download one model file with a visible temporary .download file.

    The v293 downloader started from zero after any interruption and did not
    expose enough state to the UI.  This version resumes an existing partial
    file when the server supports HTTP Range and keeps progress readable.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".download")
    expected = int(CLIP_EXPECTED_SIZES.get(dest.name, 0) or 0)
    existing = 0
    try:
        if tmp.is_file():
            existing = max(0, int(tmp.stat().st_size))
    except Exception:
        existing = 0

    headers = {"User-Agent": "LocalBooru/1.0 model-downloader"}
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"
    req = urllib.request.Request(url, headers=headers)
    downloaded = existing
    mode = "ab" if existing > 0 else "wb"
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            status = getattr(response, "status", None) or response.getcode()
            # If the server ignored Range and returned 200, restart cleanly.
            if existing > 0 and int(status or 0) == 200:
                existing = 0
                downloaded = 0
                mode = "wb"
            remaining = int(response.headers.get("Content-Length") or 0)
            total = existing + remaining if existing and remaining else remaining
            if expected and (not total or total < expected // 2):
                total = expected
            with tmp.open(mode) as fh:
                if progress and existing > 0:
                    progress(_download_progress_text(label or dest.name, downloaded, total, tmp))
                while True:
                    if stop_check and stop_check():
                        raise RuntimeError("download cancelled")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(_download_progress_text(label or dest.name, downloaded, total, tmp))
        if downloaded <= 0:
            raise RuntimeError(f"empty download: {url}")
        min_size = CLIP_LARGE_FILES.get(dest.name, 1)
        if downloaded < min_size:
            raise RuntimeError(f"download too small: {dest.name} {_format_bytes(downloaded)}")
        tmp.replace(dest)
    except Exception:
        # Keep partial large files for resume, but remove tiny/corrupt fragments.
        try:
            if tmp.is_file() and tmp.stat().st_size < 1024 * 1024:
                tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _download_progress_text(label: str, downloaded: int, total: int = 0, tmp: Path | None = None) -> str:
    if total and total > 0:
        pct = max(0.0, min(100.0, downloaded * 100.0 / total))
        return f"AI-модель: {label} {_format_bytes(downloaded)} / {_format_bytes(total)} ({pct:.1f}%)"
    return f"AI-модель: {label} {_format_bytes(downloaded)}"


def _clip_partial_download_state(model_dir: Path) -> dict[str, Any]:
    partials: list[dict[str, Any]] = []
    total_bytes = 0
    try:
        if model_dir.exists():
            for p in model_dir.glob("*.download"):
                try:
                    size = int(p.stat().st_size)
                except Exception:
                    size = 0
                name = p.name[:-9] if p.name.endswith(".download") else p.name
                expected = int(CLIP_EXPECTED_SIZES.get(name, 0) or 0)
                total_bytes += size
                partials.append({
                    "name": name,
                    "path": str(p),
                    "bytes": size,
                    "expected": expected,
                    "text": _download_progress_text(name, size, expected, p),
                })
    except Exception:
        pass
    return {
        "active": bool(partials),
        "files": partials,
        "bytes": total_bytes,
        "text": "; ".join(x.get("text", "") for x in partials if x.get("text")),
    }


def download_clip_model(settings: dict | None = None, *, progress=None, stop_check=None) -> dict[str, Any]:
    """Download CLIP files once for local NO_MATCH classification.

    This only downloads public model weights/configs from Hugging Face.  It does
    not upload user files and the classifier still runs with local_files_only.
    """
    settings = settings or {}
    target = auto_clip_model_dir(settings)
    target.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(f"AI-модель: папка {target}")
    downloaded: list[str] = []
    skipped: list[str] = []
    for name in CLIP_REQUIRED_FILES:
        if stop_check and stop_check():
            raise RuntimeError("download cancelled")
        dest = target / name
        min_size = CLIP_LARGE_FILES.get(name, 1)
        try:
            if dest.is_file() and dest.stat().st_size >= min_size:
                skipped.append(name)
                if progress:
                    progress(f"AI-модель: уже есть {name}")
                continue
        except Exception:
            pass
        url = f"{CLIP_DOWNLOAD_BASE_URL}/{name}"
        if progress:
            progress(f"AI-модель: скачиваю {name}")
        _download_one_file(url, dest, progress=progress, stop_check=stop_check, label=name)
        downloaded.append(name)
    missing = missing_clip_model_files(target, settings)
    if missing:
        raise RuntimeError("CLIP model download incomplete: missing " + ", ".join(missing))
    try:
        marker = target / "LOCAL_BOORU_DOWNLOADED_MODEL.txt"
        marker.write_text(
            "Downloaded by Local Booru for local NO_MATCH photo/illustration classification.\n"
            "User media is not uploaded; the model is used offline after this download.\n"
            f"Repository: {CLIP_DOWNLOAD_REPO}\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    _AI_CACHE.clear()
    if progress:
        progress("AI-модель: готово")
    return {"model_dir": str(target), "downloaded": downloaded, "skipped": skipped, "missing": []}


def _load_clip_backend(settings: dict | None = None) -> dict[str, Any]:
    settings = settings or {}
    model_dir = _settings_model_dir(settings)
    key = str(model_dir.resolve() if model_dir.exists() else model_dir)
    if key in _AI_CACHE:
        return _AI_CACHE[key]
    if not _clip_available_files(model_dir):
        locations = "; ".join(str(p) for p in _bundled_clip_model_candidates(settings)[:4])
        raise RuntimeError(
            "bundled local CLIP model is missing from this build. "
            "A full AI build must ship the model files inside the program, usually in models/clip. "
            f"Checked: {locations}"
        )

    # Hard offline guard: transformers must not try to fetch anything.
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor
    except Exception as exc:
        raise RuntimeError(
            "local CLIP backend needs installed packages: torch, transformers"
        ) from exc

    device_setting = str(settings.get("visual_nomatch_device", "auto") or "auto").strip().lower()
    if device_setting == "cuda":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    elif device_setting == "cpu":
        device = "cpu"
    else:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    model = CLIPModel.from_pretrained(str(model_dir), local_files_only=True)
    processor = CLIPProcessor.from_pretrained(str(model_dir), local_files_only=True)
    model.eval().to(device)
    obj = {
        "model": model,
        "processor": processor,
        "torch": torch,
        "device": device,
        "model_dir": str(model_dir),
        "model_name": current_visual_model_name(settings),
    }
    _AI_CACHE[key] = obj
    return obj


def _softmax(xs: list[float]) -> list[float]:
    if not xs:
        return []
    m = max(xs)
    exps = [math.exp(float(x) - m) for x in xs]
    s = sum(exps) or 1.0
    return [x / s for x in exps]


def _topk_mean(xs: list[float], k: int = 2) -> float:
    vals = sorted([float(x) for x in xs], reverse=True)
    if not vals:
        return 0.0
    vals = vals[:max(1, min(k, len(vals)))]
    return float(sum(vals) / len(vals))


def _clip_zero_shot_classify(path: str | Path, settings: dict | None = None) -> dict[str, Any]:
    settings = settings or {}
    p = Path(path)
    started = time.time()
    be = _load_clip_backend(settings)
    prompts = list(PHOTO_PROMPTS) + list(ILLUSTRATION_PROMPTS)
    image = _open_analysis_image(p, size=224)
    processor = be["processor"]
    model = be["model"]
    torch = be["torch"]
    device = be["device"]

    inputs = processor(text=prompts, images=image, return_tensors="pt", padding=True)
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        out = model(**inputs)
        logits = out.logits_per_image[0].detach().float().cpu().tolist()
    # Do not sum prompt probabilities: the class with more prompts gets an
    # artificial advantage and many obvious files become unknown.  Collapse each
    # class to a representative logit first, then softmax the two classes.
    photo_logits = [float(x) for x in logits[:len(PHOTO_PROMPTS)]]
    art_logits = [float(x) for x in logits[len(PHOTO_PROMPTS):]]
    photo_logit = _topk_mean(photo_logits, 2)
    art_logit = _topk_mean(art_logits, 2)
    photo_prob, art_prob = _softmax([photo_logit, art_logit])
    photo_prob = float(photo_prob)
    art_prob = float(art_prob)
    best_prob = max(photo_prob, art_prob)
    margin = abs(photo_prob - art_prob)

    min_conf = float(settings.get("visual_nomatch_ai_min_confidence", 0.56) or 0.56)
    min_margin = float(settings.get("visual_nomatch_ai_min_margin", 0.08) or 0.08)
    # v293/v294 wrote too-strict defaults to settings.json. Treat those exact
    # factory values as stale so old installs get the improved classifier.
    if abs(min_conf - 0.62) < 1e-6:
        min_conf = 0.56
    if abs(min_margin - 0.12) < 1e-6:
        min_margin = 0.08
    if best_prob >= min_conf and margin >= min_margin:
        status = PHOTO if photo_prob > art_prob else BOORU
    else:
        status = UNKNOWN
    return {
        "visual_status": status,
        "visual_confidence": round(best_prob, 4),
        "visual_model": be["model_name"],
        "visual_checked_at": int(time.time()),
        "photo_score": round(photo_prob, 4),
        "booru_score": round(art_prob, 4),
        "photo_logit": round(photo_logit, 4),
        "booru_logit": round(art_logit, 4),
        "margin": round(margin, 4),
        "runtime_ms": int((time.time() - started) * 1000),
        "device": device,
    }




def local_clip_model_state(settings: dict | None = None) -> dict[str, Any]:
    """Human-readable state for the local NO_MATCH AI model.

    Used by UI self-checks.  This does not access the network.
    """
    settings = settings or {}
    candidates = _bundled_clip_model_candidates(settings)
    model_dir = _settings_model_dir(settings)
    available = _clip_available_files(model_dir)
    partial = _clip_partial_download_state(auto_clip_model_dir(settings))
    deps_ok = True
    deps_error = ""
    missing_deps: list[str] = []
    try:
        import torch  # noqa: F401
    except Exception as exc:
        deps_ok = False
        missing_deps.append("torch")
        deps_error = f"torch: {type(exc).__name__}: {exc}"
    try:
        import transformers  # noqa: F401
    except Exception as exc:
        deps_ok = False
        missing_deps.append("transformers")
        deps_error = (deps_error + "; " if deps_error else "") + f"transformers: {type(exc).__name__}: {exc}"
    try:
        import sys
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        python_executable = sys.executable
    except Exception:
        python_version = "?"
        python_executable = ""
    return {
        "backend": str(settings.get("visual_nomatch_backend", "clip_local") or "clip_local"),
        "available": bool(available),
        "deps_ok": bool(deps_ok),
        "deps_error": deps_error,
        "missing_deps": missing_deps,
        "model_dir": str(model_dir),
        "download_dir": str(auto_clip_model_dir(settings)),
        "download_active": bool(partial.get("active")),
        "download_bytes": int(partial.get("bytes", 0) or 0),
        "download_text": str(partial.get("text", "") or ""),
        "download_files": partial.get("files", []),
        "missing_files": missing_clip_model_files(model_dir, settings) if not available else [],
        "checked": [str(x) for x in candidates],
        "model_name": current_visual_model_name(settings),
        "python_version": python_version,
        "python_executable": python_executable,
    }

def classify_visual_status(path: str | Path, settings: dict | None = None) -> dict[str, Any]:
    """Return durable NO_MATCH sorting status.

    Backends:
    - clip_local: local CLIP model from disk, no network; uncertain => unknown.
    - heuristic: legacy deterministic v1; explicit fallback only.
    """
    settings = settings or {}
    backend = str(settings.get("visual_nomatch_backend", "clip_local") or "clip_local").strip().lower()
    if backend in ("heuristic", "local_heuristic", "v1"):
        return _heuristic_classify(path, settings)
    try:
        return _clip_zero_shot_classify(path, settings)
    except Exception as exc:
        if bool(settings.get("visual_nomatch_ai_fallback_heuristic", False)):
            info = _heuristic_classify(path, settings)
            info["visual_model"] = info.get("visual_model", HEURISTIC_MODEL_NAME) + ":fallback_after_ai_error"
            info["ai_error"] = f"{type(exc).__name__}: {exc}"
            return info
        return {
            "visual_status": UNKNOWN,
            "visual_confidence": 0.0,
            # Error results must not look like a valid cache entry.  Otherwise
            # a temporary missing torch/model state permanently sticks as
            # "[вид ?] 0%" even after the user fixes the environment.
            "visual_model": current_visual_model_name(settings) + ":error",
            "visual_checked_at": int(time.time()),
            "error": f"{type(exc).__name__}: {exc}",
        }


def classify_nomatch_if_enabled(path: str | Path, settings: dict | None = None) -> dict[str, Any] | None:
    settings = settings or {}
    if not bool(settings.get("visual_nomatch_classify_enabled", True)):
        return None
    return classify_visual_status(path, settings)
