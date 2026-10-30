"""Models that tell a photo from a drawing in Брак («фото или рисунок»): small classifiers made for exactly this
by deepghs (huggingface.co/deepghs, licence OpenRAIL):

- «аниме или фото» (``deepghs/anime_real_cls``): a picture is ``anime`` (drawn, rendered) or ``real`` (a photograph);
  98.8 % right on its test set (the fast one, 17 MB), 99.1 % (the precise one, 150 MB);
- «вид картинки» (``deepghs/anime_classification``): ``3d``, ``bangumi`` (an anime frame), ``comic``,
  ``illustration`` or ``not_painting`` (not a drawing: a photograph) — it knows 3D renders as a kind of its own;
- both together: the photo's chance is the mean of the two.

They run in onnxruntime on the processor (a package of ~15 MB — not the 2.5 GB of torch the CLIP check needs).
The weights are downloaded once into «models» of the archive's settings folder (or come with the program, in its
«models» folder): only the weights travel, the pictures never leave the computer.

A picture goes in as the model was taught (imgutils ``ClassifyModel``): transparency on white, RGB, resized to
the model's input (384×384) bilinearly, values 0…1 moved to −1…1, channels first.  The program's own loader
opens it (``visual_status._open_analysis_image``: the first frame of a GIF, a frame of a video, the turn written
in a photo's EXIF).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_LOG = logging.getLogger("local_booru.visual")

HF_ROOT = "https://huggingface.co"
PHOTO, DRAWING, UNSURE = "real", "booru", "unknown"
DEFAULT_SIZE = 384
DEFAULT_MIN_CONFIDENCE = 75          # %: below it the answer is «не уверен»


@dataclass(frozen=True)
class ModelSpec:
    key: str
    repo: str
    name: str
    title: str
    size_mb: int
    photo_labels: tuple            # the model's labels that mean «a photograph»
    labels: tuple                  # the model's labels in its output order (meta.json says it when present)


SPECS = {
    "anime_real_fast": ModelSpec("anime_real_fast", "deepghs/anime_real_cls", "mobilenetv3_v1.4_dist",
                                 "«аниме или фото», быстрая", 17, ("real",), ("anime", "real")),
    "anime_real_precise": ModelSpec("anime_real_precise", "deepghs/anime_real_cls", "caformer_s36_v1.4",
                                    "«аниме или фото», точная", 150, ("real",), ("anime", "real")),
    "anime_class_fast": ModelSpec("anime_class_fast", "deepghs/anime_classification", "mobilenetv3_v1.5_dist",
                                  "«вид картинки» (3D, аниме, комикс, рисунок или фото)", 17, ("not_painting",),
                                  ("3d", "bangumi", "comic", "illustration", "not_painting")),
}
ENSEMBLES = {"both_fast": ("anime_real_fast", "anime_class_fast")}
CHOICE_TITLES = {
    "auto": "выбирает программа (рекомендуется)",
    "anime_real_fast": SPECS["anime_real_fast"].title + " — 17 МБ",
    "anime_real_precise": SPECS["anime_real_precise"].title + " — 150 МБ, медленнее",
    "anime_class_fast": SPECS["anime_class_fast"].title + " — 17 МБ",
    "both_fast": "обе быстрые вместе — 34 МБ",
    "clip": "CLIP (нужны torch и transformers, 600 МБ)",
    "heuristic": "простая оценка по цветам (без модели, часто ошибается)",
}
MODEL_CHOICES = tuple(SPECS) + tuple(ENSEMBLES)
NAMES = {**{key: spec.title for key, spec in SPECS.items()}, "both_fast": "обе быстрые модели вместе",
         "clip": "CLIP", "heuristic": "простая оценка по цветам"}      # what answers, in the page's sentences

_RUNTIME: dict = {}             # onnxruntime: looked up once
_CLIP_DEPS: dict = {}           # torch and transformers: looked up once
_SESSIONS: dict = {}
_UNLOADABLE: dict = {}          # weights file -> why onnxruntime does not load it (not a broken file: another cause)
_LOCK = threading.Lock()


class Unreadable(Exception):
    """The file is not a picture the models can see (broken, too small, a video without a frame): the same model
    would fail on it every time, so its Брак entry is marked and not asked again (``visual_work.classify_pending``)."""


class BrokenModel(RuntimeError):
    """The weights file is cut off or damaged: it is put aside (``model.onnx.broken``), so it counts as not
    downloaded and is downloaded again."""


# ── what is chosen and what is there ───────────────────────────────────────
def specs_of(choice: str) -> tuple:
    """The models a choice runs (one, or the two of «both»)."""
    if choice in SPECS:
        return (SPECS[choice],)
    return tuple(SPECS[k] for k in ENSEMBLES.get(choice, ()))


def model_name(choice: str) -> str:
    """What the Брак entry records as its ``visual_model`` (a new name — the entry is checked again)."""
    return "deepghs:" + choice + ":" + "+".join(s.name for s in specs_of(choice))


def runtime(refresh: bool = False) -> dict:
    """{"ok", "version", "error"} of onnxruntime (looked up once; ``refresh`` after installing it)."""
    with _LOCK:
        if _RUNTIME and not refresh and (_RUNTIME.get("ok") or time.monotonic() - _RUNTIME.get("at", 0) < 60):
            return dict(_RUNTIME)
    try:
        import importlib
        importlib.invalidate_caches()
        import onnxruntime
        state = {"ok": True, "version": str(getattr(onnxruntime, "__version__", "?")), "error": ""}
    except Exception as exc:  # not installed, or a broken install: said on «Настройки»
        state = {"ok": False, "version": "", "error": f"{type(exc).__name__}: {exc}"}
    state["at"] = time.monotonic()
    with _LOCK:
        _RUNTIME.clear()
        _RUNTIME.update(state)
    return dict(state)


def models_root(settings: dict | None) -> Path:
    """Where downloaded weights go: «models/deepghs» in the archive's settings folder."""
    try:
        from core.paths import suggested_settings_storage_dir
        return suggested_settings_storage_dir(settings or {}) / "models" / "deepghs"
    except Exception:  # an odd setup: next to the program
        return Path("models") / "deepghs"


def _folder(root: Path, spec: ModelSpec) -> Path:
    return root / spec.repo.split("/")[-1] / spec.name


def _candidates(settings: dict | None, spec: ModelSpec) -> list:
    out = []
    try:
        import sys
        from core.paths import app_base_dir, app_install_dir
        roots = [app_install_dir() / "models" / "deepghs", app_base_dir() / "models" / "deepghs"]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(str(meipass)) / "models" / "deepghs")
    except Exception:
        roots = []
    for root in [*roots, models_root(settings)]:
        folder = _folder(root, spec)
        if folder not in out:
            out.append(folder)
    return out


def model_file(settings: dict | None, spec: ModelSpec) -> Path | None:
    """The weights of ``spec`` on this computer (None when they are not downloaded)."""
    for folder in _candidates(settings, spec):
        path = folder / "model.onnx"
        try:
            if path.is_file() and path.stat().st_size > 64 * 1024:
                return path
        except OSError:
            continue
    return None


def chosen(settings: dict | None) -> str:
    """The model that answers now: the one chosen on «Настройки», or for «сама» the fast «аниме или фото» when
    onnxruntime is installed, else CLIP when it can run, else the colour estimate if it was chosen in the old
    window; "" — nothing can answer."""
    s = settings or {}
    choice = str(s.get("visual_nomatch_model") or "auto").strip().lower()
    if choice in MODEL_CHOICES or choice in ("clip", "heuristic"):
        return choice
    if runtime()["ok"]:
        return "anime_real_fast"
    old = str(s.get("visual_nomatch_backend") or "clip_local").strip().lower()
    if old in ("heuristic", "local_heuristic", "v1"):
        return "heuristic"
    if _clip_ready(s):
        return "clip"
    return ""


def _clip_ready(settings: dict) -> bool:
    """CLIP can run: its files and torch with transformers (the import is looked up once — torch loads for seconds)."""
    with _LOCK:
        known = _CLIP_DEPS.get("ok")
    if known is None:
        import importlib.util
        known = all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers"))
        with _LOCK:
            _CLIP_DEPS["ok"] = known
    if not known:
        return False
    from core.visual_status import _clip_available_files, _settings_model_dir
    return _clip_available_files(_settings_model_dir(settings))


def missing(settings: dict | None, choice: str) -> list:
    """The models of ``choice`` not downloaded yet."""
    return [spec for spec in specs_of(choice) if model_file(settings, spec) is None]


# ── download ───────────────────────────────────────────────────────────────
def download(settings: dict | None, choice: str, *, progress: Callable[[str], None] | None = None,
             stop_check: Callable[[], bool] | None = None) -> list:
    """Download the weights of ``choice`` that are not here yet (resumes a broken download); returns their paths."""
    from core.visual_status import _download_one_file
    say = progress or (lambda _m: None)
    done = []
    for spec in specs_of(choice):
        if model_file(settings, spec) is not None:
            continue
        folder = _folder(models_root(settings), spec)
        folder.mkdir(parents=True, exist_ok=True)
        base = f"{HF_ROOT}/{spec.repo}/resolve/main/{spec.name}"
        say(f"Скачиваю {spec.title} ({spec.size_mb} МБ) в {folder}")
        _download_one_file(f"{base}/meta.json", folder / "meta.json", stop_check=stop_check, label="meta.json")
        _download_one_file(f"{base}/model.onnx", folder / "model.onnx", progress=_every_5_percent(say),
                           stop_check=stop_check, label="model.onnx")
        labels = _labels(spec, folder / "model.onnx")
        if not set(spec.photo_labels) <= set(labels):
            _drop(folder)                                       # not kept: it would pass for the model next time
            raise RuntimeError(f"у скачанной модели другие метки: {labels}")
        forget_sessions()
        try:
            _session(settings, folder / "model.onnx")           # a file cut off on the way does not load
        except BrokenModel as exc:
            _drop(folder)
            raise RuntimeError(f"файл модели пришёл повреждённым ({exc}) — скачайте ещё раз") from exc
        _drop(folder, ("model.onnx.broken",))                 # a damaged one put aside before: replaced now
        done.append(folder / "model.onnx")
        say(f"Готово: {spec.title}")
    return done


def _drop(folder: Path, names: tuple = ("model.onnx", "meta.json", "model.onnx.broken")) -> None:
    for name in names:
        try:
            (folder / name).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:                                  # held by something: said, the next try overwrites
            _LOG.warning("visual model file %s not removed: %s", folder / name, exc)


def _every_5_percent(say):
    """The downloader reports every megabyte: the journal gets a line per 5 %."""
    last = {"p": -5}

    def progress(text: str) -> None:
        import re
        m = re.search(r"\((\d+(?:\.\d+)?)%\)", str(text))
        if m is None:
            return
        value = float(m.group(1))
        if value - last["p"] >= 5 or value >= 100:
            last["p"] = value
            say(str(text))
    return progress


# ── running a model ────────────────────────────────────────────────────────
def _labels(spec: ModelSpec, path: Path) -> tuple:
    try:
        meta = json.loads((path.parent / "meta.json").read_text(encoding="utf-8"))
        labels = tuple(str(x) for x in meta.get("labels") or ())
        if labels:
            return labels
    except (OSError, ValueError, AttributeError):
        pass
    return spec.labels


def _session(settings: dict | None, path: Path):
    """The model's onnxruntime session, kept for the program run (one per file)."""
    key = str(path)
    with _LOCK:
        known = _SESSIONS.get(key)
    if known is not None:
        return known
    import onnxruntime as ort
    options = ort.SessionOptions()
    try:
        threads = int((settings or {}).get("visual_nomatch_threads", 2) or 2)
    except (TypeError, ValueError):
        threads = 2
    options.intra_op_num_threads = max(1, min(8, threads))
    options.inter_op_num_threads = 1
    options.log_severity_level = 3            # its warnings about a model's insides are not the person's business
    try:
        session = ort.InferenceSession(key, sess_options=options, providers=["CPUExecutionProvider"])
    except Exception as exc:  # onnxruntime raises its own types: a cut-off file, or a model this version cannot run
        text = f"{type(exc).__name__}: {exc}"
        if "INVALID_PROTOBUF" in text or "Protobuf parsing failed" in text:
            aside = path.with_name(path.name + ".broken")
            try:
                path.replace(aside)
            except OSError as move_exc:
                _LOG.warning("broken visual model %s not put aside: %s", path, move_exc)
            _LOG.warning("visual model %s is damaged (%s): put aside, it will be downloaded again", path, text)
            raise BrokenModel(text) from exc
        with _LOCK:
            _UNLOADABLE[key] = text
        raise
    shape = session.get_inputs()[0].shape
    height, width = (shape[2], shape[3]) if len(shape) == 4 else (None, None)
    size = (width if isinstance(width, int) and width > 0 else DEFAULT_SIZE,
            height if isinstance(height, int) and height > 0 else DEFAULT_SIZE)
    entry = (session, size, session.get_inputs()[0].name)
    with _LOCK:
        _SESSIONS.setdefault(key, entry)
        return _SESSIONS[key]


def prepare(image, size: tuple) -> Any:
    """A PIL picture → the model's input (1×3×H×W, float32, −1…1)."""
    import numpy as np
    from PIL import Image
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        white.alpha_composite(rgba)
        image = white
    if image.mode != "RGB":
        image = image.convert("RGB")
    resized = image.resize((int(size[0]), int(size[1])), Image.BILINEAR)
    data = np.asarray(resized, dtype=np.float32).transpose(2, 0, 1) / 255.0
    return ((data - 0.5) / 0.5).astype(np.float32)[None, ...]


def _scores(session, input_name: str, data) -> Any:
    import numpy as np
    names = [o.name for o in session.get_outputs()]
    wanted = "output" if "output" in names else names[0]
    out = np.asarray(session.run([wanted], {input_name: data})[0], dtype=np.float64)[0]
    total = float(out.sum())
    if out.min() < 0 or abs(total - 1.0) > 1e-3:          # logits: made into chances
        exp = np.exp(out - out.max())
        out = exp / exp.sum()
    return out


def photo_chance(settings: dict | None, spec: ModelSpec, image) -> tuple[float, dict]:
    """(the chance that the picture is a photograph, the model's chances by label) for an open PIL picture."""
    path = model_file(settings, spec)
    if path is None:
        raise RuntimeError(f"модель {spec.title} не скачана")
    session, size, input_name = _session(settings, path)
    scores = _scores(session, input_name, prepare(image, size))
    labels = _labels(spec, path)
    by_label = {str(labels[i]) if i < len(labels) else str(i): float(scores[i]) for i in range(len(scores))}
    return sum(by_label.get(label, 0.0) for label in spec.photo_labels), by_label


def min_confidence(settings: dict | None) -> float:
    try:
        value = float((settings or {}).get("visual_nomatch_min_confidence", DEFAULT_MIN_CONFIDENCE))
    except (TypeError, ValueError):
        value = DEFAULT_MIN_CONFIDENCE
    return max(50.0, min(99.0, value)) / 100.0


def decide(photo: float, settings: dict | None) -> tuple[str, float]:
    """(status, confidence) from the photo's chance: an answer below the confidence asked for is «не уверен»."""
    confidence = max(photo, 1.0 - photo)
    if confidence < min_confidence(settings):
        return UNSURE, confidence
    return (PHOTO if photo >= 0.5 else DRAWING), confidence


def classify_image(settings: dict | None, choice: str, image) -> dict:
    """The answer of ``choice`` for an open picture (the contract of ``visual_status.classify_visual_status``)."""
    started = time.time()
    chances, details = [], {}
    for spec in specs_of(choice):
        photo, by_label = photo_chance(settings, spec, image)
        chances.append(photo)
        details[spec.key] = {k: round(v, 4) for k, v in by_label.items()}
    if not chances:
        raise ValueError(f"нет такой модели: {choice}")
    photo = sum(chances) / len(chances)
    status, confidence = decide(photo, settings)
    return {"visual_status": status, "visual_confidence": round(confidence, 4), "visual_model": model_name(choice),
            "visual_checked_at": int(time.time()), "photo_score": round(photo, 4), "booru_score": round(1 - photo, 4),
            "details": details, "runtime_ms": int((time.time() - started) * 1000)}


def classify(settings: dict | None, choice: str, path) -> dict:
    """The answer of ``choice`` for a file of the library."""
    from core.visual_status import _open_analysis_image
    try:
        image = _open_analysis_image(Path(path), size=DEFAULT_SIZE)
    except (FileNotFoundError, PermissionError):
        raise                           # gone or held for a moment: asked again later
    except Exception as exc:  # Pillow's many errors: broken, truncated, too small, a decompression bomb, no frame
        raise Unreadable(f"{type(exc).__name__}: {exc}") from exc
    try:
        return classify_image(settings, choice, image)
    finally:
        try:
            image.close()
        except Exception:
            pass


def load_problem(settings: dict | None, choice: str) -> str:
    """Why the weights of ``choice`` are here but onnxruntime does not run them ("" — nothing known)."""
    for spec in specs_of(choice):
        path = model_file(settings, spec)
        with _LOCK:
            why = _UNLOADABLE.get(str(path)) if path is not None else None
        if why:
            return f"модель {spec.title} не загружается: {why}"
    return ""


def forget_sessions() -> None:
    """Drop the loaded models and what was known about them (after a download replaced a file, and in checks)."""
    with _LOCK:
        _SESSIONS.clear()
        _UNLOADABLE.clear()
