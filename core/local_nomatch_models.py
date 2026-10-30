"""Optional compact local ONNX hooks for NO_MATCH triage.

No model weights are bundled.  The runtime is offline-only and fail-open: an
absent model or uncertain result never suppresses parser branches and never
causes destructive actions.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from core.database.connection import db

VALID_CLASSES = {"booru_illustration", "real_photo", "mixed_or_3d", "uncertain"}


def _session(model_path: str):
    import onnxruntime as ort
    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    available = set(ort.get_available_providers())
    return ort.InferenceSession(str(model_path), providers=[p for p in providers if p in available])


def classify_content(path: str | Path, settings: dict | None = None) -> dict[str, Any]:
    model = str((settings or {}).get("nomatch_compact_classifier_onnx", "") or "").strip()
    if not model or not Path(model).is_file():
        return {"class": "uncertain", "confidence": 0.0, "reason": "model_not_configured"}
    try:
        import numpy as np
        from PIL import Image
        sess = _session(model)
        inp = sess.get_inputs()[0]
        size = int((settings or {}).get("nomatch_compact_input_size", 224) or 224)
        with Image.open(path) as im:
            arr = np.asarray(im.convert("RGB").resize((size, size)), dtype=np.float32) / 255.0
        arr = np.transpose(arr, (2, 0, 1))[None, ...]
        out = sess.run(None, {inp.name: arr})[0][0]
        exp = np.exp(out - np.max(out)); probs = exp / max(1e-9, float(exp.sum()))
        labels = ["booru_illustration", "real_photo", "mixed_or_3d", "uncertain"]
        index = int(np.argmax(probs)); confidence = float(probs[index])
        cls = labels[index] if index < len(labels) else "uncertain"
        if confidence < float((settings or {}).get("nomatch_compact_min_confidence", 0.60) or 0.60):
            cls = "uncertain"
        return {"class": cls, "confidence": confidence, "model": Path(model).name}
    except Exception as exc:
        return {"class": "uncertain", "confidence": 0.0, "reason": f"{type(exc).__name__}: {exc}"}


def detect_text_regions(path: str | Path, settings: dict | None = None) -> dict[str, Any]:
    model = str((settings or {}).get("nomatch_text_detector_onnx", "") or "").strip()
    if not model or not Path(model).is_file():
        return {"text_present": False, "regions": [], "confidence": 0.0, "reason": "model_not_configured"}
    # Model-specific decoding differs; support a simple Nx5 normalized boxes contract.
    try:
        import numpy as np
        from PIL import Image
        sess = _session(model); inp = sess.get_inputs()[0]
        size = int((settings or {}).get("nomatch_text_input_size", 640) or 640)
        with Image.open(path) as im:
            arr = np.asarray(im.convert("RGB").resize((size, size)), dtype=np.float32) / 255.0
        arr = np.transpose(arr, (2, 0, 1))[None, ...]
        output = sess.run(None, {inp.name: arr})[0]
        threshold = float((settings or {}).get("nomatch_text_min_confidence", 0.55) or 0.55)
        regions = []
        for row in np.asarray(output).reshape(-1, np.asarray(output).shape[-1]):
            if len(row) < 5 or float(row[4]) < threshold:
                continue
            x1, y1, x2, y2 = [max(0.0, min(1.0, float(x))) for x in row[:4]]
            regions.append({"x": x1, "y": y1, "w": max(0.0, x2-x1), "h": max(0.0, y2-y1), "confidence": float(row[4])})
        return {"text_present": bool(regions), "regions": regions, "confidence": max([r["confidence"] for r in regions] or [0.0]), "model": Path(model).name}
    except Exception as exc:
        return {"text_present": False, "regions": [], "confidence": 0.0, "reason": f"{type(exc).__name__}: {exc}"}


def persist_results(settings: dict | None, *, image_id: int, content: dict[str, Any], text: dict[str, Any]) -> None:
    now = int(time.time())
    with db(settings, write=True) as con:
        con.execute("INSERT INTO local_model_results(image_id,model_kind,model_name,result_json,confidence,checked_at) VALUES(?,?,?,?,?,?) ON CONFLICT(image_id,model_kind) DO UPDATE SET model_name=excluded.model_name,result_json=excluded.result_json,confidence=excluded.confidence,checked_at=excluded.checked_at", (int(image_id), "content_class", str(content.get("model") or ""), json.dumps(content, ensure_ascii=False), float(content.get("confidence") or 0), now))
        con.execute("INSERT INTO local_model_results(image_id,model_kind,model_name,result_json,confidence,checked_at) VALUES(?,?,?,?,?,?) ON CONFLICT(image_id,model_kind) DO UPDATE SET model_name=excluded.model_name,result_json=excluded.result_json,confidence=excluded.confidence,checked_at=excluded.checked_at", (int(image_id), "text_regions", str(text.get("model") or ""), json.dumps(text, ensure_ascii=False), float(text.get("confidence") or 0), now))
        con.execute("DELETE FROM image_text_regions WHERE image_id=?", (int(image_id),))
        for region in text.get("regions") or []:
            con.execute("INSERT INTO image_text_regions(image_id,model_name,x_normalized,y_normalized,width_normalized,height_normalized,confidence,created_at) VALUES(?,?,?,?,?,?,?,?)", (int(image_id), str(text.get("model") or ""), float(region.get("x") or 0), float(region.get("y") or 0), float(region.get("w") or 0), float(region.get("h") or 0), float(region.get("confidence") or 0), now))


# ---------------------------------------------------------------------------
# Durable optional NO_MATCH analysis lane
# ---------------------------------------------------------------------------
import threading
from core.background_lane import BackgroundLane, EligibilityResult, LaneJob


def _configured(settings: dict | None) -> bool:
    if not bool((settings or {}).get("nomatch_local_models_enabled", False)):
        return False
    return any(
        str((settings or {}).get(key, "") or "").strip()
        for key in ("nomatch_compact_classifier_onnx", "nomatch_text_detector_onnx")
    )


class LocalNoMatchModelLane:
    """Run compact offline models without blocking the parser conveyor.

    Model absence is fail-open and does not create a durable job.  Runtime
    inference errors terminate the current job as an uncertain result rather
    than suppressing any reverse branch or causing destructive action.
    """
    def __init__(self, settings: dict, *, trace=None):
        self.settings = settings
        self.stop_event = threading.Event()
        self.lane = BackgroundLane(
            settings, "local-nomatch-models", classifier=self._classify,
            executor=self._execute, retry_policy=self._retry, trace=trace,
            page_size=int(settings.get("background_lane_page_size", 500) or 500),
        )
        self.thread = threading.Thread(target=self._run, name="local-nomatch-models", daemon=True)
        self.thread.start()

    def enqueue(self, path: str | Path, *, image_id: int = 0) -> int:
        if not _configured(self.settings):
            return 0
        key = str(Path(path))
        return self.lane.enqueue(
            key, {"path": key, "image_id": int(image_id or 0)},
            priority=5, dedupe_key=f"nomatch-model:{key}",
        )

    def _classify(self, job: LaneJob, _context: Any) -> EligibilityResult:
        if not _configured(self.settings):
            return EligibilityResult(False, "models_disabled")
        path = Path(str(job.payload.get("path") or ""))
        if not path.is_file():
            return EligibilityResult(False, "file_missing")
        return EligibilityResult(True)

    def _execute(self, job: LaneJob, _context: Any) -> None:
        path = Path(str(job.payload.get("path") or ""))
        content = classify_content(path, self.settings)
        text = detect_text_regions(path, self.settings)
        image_id = int(job.payload.get("image_id") or 0)
        if not image_id:
            try:
                with db(self.settings, readonly=True) as con:
                    row = con.execute(
                        "SELECT id FROM images WHERE deleted=0 AND path=? ORDER BY id DESC LIMIT 1",
                        (str(path),),
                    ).fetchone()
                    image_id = int(row[0]) if row else 0
            except Exception:
                image_id = 0
        if image_id:
            persist_results(self.settings, image_id=image_id, content=content, text=text)
        cls = str(content.get("class") or "uncertain")
        visual = "booru" if cls == "booru_illustration" else ("real" if cls == "real_photo" else "unknown")
        try:
            from core.nomatch_db import set_visual_status
            set_visual_status(
                path, visual, float(content.get("confidence") or 0.0),
                str(content.get("model") or ""), settings=self.settings,
            )
        except Exception:
            pass

    @staticmethod
    def _retry(job: LaneJob, exc: BaseException) -> tuple[bool, int, str]:
        # Bad/unsupported input is not a reason to hammer a local model forever.
        return False, 0, f"{type(exc).__name__}: {exc}"

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.lane.drain_once(target=2, should_stop=self.stop_event.is_set)

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)


_LANES: dict[str, LocalNoMatchModelLane] = {}
_LANES_LOCK = threading.Lock()
_SHUTDOWN_REGISTERED: set[str] = set()


def _lane_key(settings: dict) -> str:
    try:
        from core.paths import DATA_DIR
        return str(DATA_DIR)
    except Exception:
        return str(id(settings))


def get_local_nomatch_lane(settings: dict) -> LocalNoMatchModelLane | None:
    if not _configured(settings):
        return None
    key = _lane_key(settings)
    with _LANES_LOCK:
        lane = _LANES.get(key)
        if lane is None or not lane.thread.is_alive():
            lane = LocalNoMatchModelLane(settings)
            _LANES[key] = lane
            if key not in _SHUTDOWN_REGISTERED:
                try:
                    from core.shutdown import register
                    register(f"local NO_MATCH models [{key}]", lane.stop)
                    _SHUTDOWN_REGISTERED.add(key)
                except Exception:
                    pass
        return lane


def enqueue_local_nomatch_analysis(settings: dict | None, path: str | Path, *, image_id: int = 0) -> int:
    if settings is None or not _configured(settings):
        return 0
    try:
        lane = get_local_nomatch_lane(settings)
        return lane.enqueue(path, image_id=image_id) if lane else 0
    except Exception:
        return 0
