"""(v5.44.36) The frame of a video a booru makes its preview of — what its own search of similar pictures and the
services that took its previews (IQDB, SauceNAO) know the video by.

Measured on 2.11.2026 on 40 public video posts of general rating (20 of e621, 20 of Danbooru): e621 makes the
preview with ffmpeg's «thumbnail» filter over the first 100 frames, Danbooru over the first 300 — the frame whose
colours are closest to the average of those frames (the site's preview and that frame: pHash d=0–4 in 40 of 40).
The first frame, which the parser sent to reverse search since v5.23.2 («сайты делают превью из начала»), was the
preview in 8 of 40.  On the owner's Брак (72 videos) the first frame and the e621 one differ (d > 4) in 51, the
first and the Danbooru one in 53.  e621 IQDB found 1 of 7 public e621 videos by their first frame, 5 by the e621
frame (3 at 80 % and more); iqdb.org found 8 of the 72 on Danbooru by the Danbooru frame (80–99 %; by their first frame
5 of them not at all, one — AV1 — had no frame).

:func:`site_frames` returns both, chosen as the filter does (``libavfilter/vf_thumbnail.c``): a histogram of each
plane of the decoded picture (Y, U, V of a YUV video; the bytes of an RGB one), their average over the batch, the frame
with the smallest sum of squared differences from it (the first of equal ones); a video shorter than the batch — over
all of its frames.  The frames are read with PyAV (FFmpeg's own decoders, AV1 too: the OpenCV of the program cannot
decode AV1 without a video card that can), without it — with OpenCV (its BGR turned back into YUV: the choice may be
another frame of the same scene).  A video «lying on its side» (a phone's: a display matrix) is turned as FFmpeg turns
it (:func:`upright`; the histograms do not change) — PyAV gives it lying, and the sites' previews are upright.  The
chosen frame is read again from the key frame before it (:func:`_frame_at`).  Computed once a file (``_CACHE``: by
path, size and time of change)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

START = "start"             # a video's start (the first frame with a picture: core/tagger/hashing.video_frames)
E621 = "e621"
DANBOORU = "danbooru"
BATCH = {E621: 100, DANBOORU: 300}
#: how the journal names the frame that matched a site's preview («d=2, как у Danbooru»)
WHERE = {E621: "как у e621", DANBOORU: "как у Danbooru"}
WORDS = {START: "начало", **WHERE}
SIDE = 512                  # the pictures are kept at most this big (the parser's frames are, hashing._small_rgb)
READ_LIMIT = max(BATCH.values())

_LOG = logging.getLogger("local_booru.site_frames")
_LOCK = threading.Lock()
_CACHE: dict = {}           # (path, size, mtime_ns) -> {style: picture}; the last _KEEP files
_KEEP = 24
_PICKED: dict = {}          # (path, size, mtime_ns) -> (reader, {style: frame number}, pts) — small, kept longer
_PICKED_KEEP = 256

# FFmpeg's filter takes these as they are; any other picture FFmpeg turns into one of them first
_PLANAR = {"yuv410p", "yuv411p", "yuv420p", "yuv422p", "yuv440p", "yuv444p", "yuvj411p", "yuvj420p", "yuvj422p",
           "yuvj440p", "yuvj444p", "yuva420p", "yuva422p", "yuva444p", "gbrp", "gbrap"}
_PACKED = {"rgb24": (3, 0), "bgr24": (3, 0), "rgba": (4, 0), "bgra": (4, 0), "rgb0": (4, 0), "bgr0": (4, 0),
           "argb": (4, 1), "abgr": (4, 1), "0rgb": (4, 1), "0bgr": (4, 1)}


def _emit(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(message)
    else:
        _LOG.info(message)


def _key(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return str(path), int(st.st_size), int(st.st_mtime_ns)


def site_frames(path, settings=None, log=None) -> dict:
    """{style: RGB picture of at most :data:`SIDE` px} — the frame e621 (:data:`E621`) and Danbooru
    (:data:`DANBOORU`) make the video's preview of; {} — not readable."""
    path = Path(path)
    key = _key(path)
    if key is None:
        return {}
    with _LOCK:
        known = _CACHE.get(key)
    if known is not None:
        return known
    reader, chosen, stamps = _picked(path, key, log)
    found: dict = {}
    for index in sorted(set(chosen.values())):
        picture = _picture(reader, path, index, stamps, SIDE)
        if picture is not None:
            for style, number in chosen.items():
                if number == index:
                    found[style] = picture
    with _LOCK:
        _CACHE[key] = found
        while len(_CACHE) > _KEEP:
            _CACHE.pop(next(iter(_CACHE)))
    return found


def site_frame(path, style: str = DANBOORU, side: int | None = None, log=None):
    """(v5.44.37) One of the two frames as an RGB picture at most ``side`` px (None — the video's own size), upright;
    None — not readable.  The gallery's preview of a video is the Danbooru one (the owner: «превью видео … надо
    сделать таким … «средним»»: not a frame at a twentieth of the video)."""
    path = Path(path)
    key = _key(path)
    if key is None:
        return None
    reader, chosen, stamps = _picked(path, key, log)
    if style not in chosen:
        return None
    return _picture(reader, path, chosen[style], stamps, side)


def _picked(path: Path, key, log=None) -> tuple:
    """(reader, {style: frame number}, pts of the frames read) — chosen once a file (``_PICKED``): PyAV, without it
    or when it cannot read the file — OpenCV; (None, {}, None) — neither can."""
    with _LOCK:
        known = _PICKED.get(key)
    if known is not None:
        return known
    found = None
    try:
        found = _picks_pyav(path)
    except ImportError:
        found = None
    except Exception as exc:  # an odd file for PyAV: OpenCV may still read it
        _emit(log, f"SITE FRAMES: {path.name}: PyAV не прочитал ({type(exc).__name__}: {exc})")
        found = None
    if found is None:
        try:
            chosen = _picks_opencv(path)
            found = ("opencv", chosen, None) if chosen else (None, {}, None)
        except ImportError:
            found = (None, {}, None)
        except Exception as exc:  # unreadable for both: the parser's other frames still compare
            _emit(log, f"SITE FRAMES: {path.name}: OpenCV не прочитал ({type(exc).__name__}: {exc})")
            found = (None, {}, None)
    with _LOCK:
        _PICKED[key] = found
        while len(_PICKED) > _PICKED_KEEP:
            _PICKED.pop(next(iter(_PICKED)))
    return found


def _picture(reader, path: Path, index: int, stamps, side):
    try:
        if reader == "pyav":
            import av
            return _frame_at(av, path, index, stamps or [], side)
        if reader == "opencv":
            return _cv_frame_at(path, index, side)
    except Exception as exc:  # the frame is not read again after all: no picture (the caller has its own way)
        _LOG.info("SITE FRAMES: %s: кадр %d не прочитан (%s: %s)", path.name, index, type(exc).__name__, exc)
    return None


def forget() -> None:
    """Forget every file's frames (checks: the same file read another way)."""
    with _LOCK:
        _CACHE.clear()
        _PICKED.clear()


def picks(histograms) -> dict:
    """{style: index of the frame} the filter picks of these per-frame histograms (rows of 768 counts)."""
    import numpy as np
    rows = np.asarray(histograms, dtype=np.float64)
    out: dict = {}
    for style, batch in BATCH.items():
        part = rows[:batch]
        if not len(part):
            continue
        average = part.sum(axis=0) / len(part)
        errors = ((average - part) ** 2).sum(axis=1)
        out[style] = int(np.argmin(errors))          # the first of equal ones, as the filter
    return out


def _counts(np, data) -> "object":
    return np.bincount(data.ravel(), minlength=256)[:256]


def frame_histogram(frame):
    """The filter's histogram of a PyAV frame: 256 counts for each of the first three planes (a planar picture) or for
    each of the three colour bytes of a pixel (a packed one)."""
    import numpy as np
    name = frame.format.name
    if name not in _PLANAR and name not in _PACKED:
        frame = frame.reformat(format=_nearest(name))
        name = frame.format.name
    if name in _PACKED:
        size, skip = _PACKED[name]
        plane = frame.planes[0]
        rows = np.frombuffer(plane, dtype=np.uint8).reshape(plane.height, plane.line_size)
        pixels = rows[:, :frame.width * size].reshape(plane.height, frame.width, size)
        return np.concatenate([_counts(np, pixels[:, :, skip + c]) for c in range(3)])
    parts = []
    for plane in frame.planes[:3]:
        rows = np.frombuffer(plane, dtype=np.uint8).reshape(plane.height, plane.line_size)
        parts.append(_counts(np, rows[:, :plane.width]))
    return np.concatenate(parts)


def _nearest(name: str) -> str:
    """What FFmpeg turns a picture of another kind into for the filter: the same chroma at 8 bits (10-bit, NV12…),
    else RGB."""
    low = name.lower()
    if low.startswith(("yuv", "nv", "p0", "p2", "p4", "y2", "yuyv", "uyvy")):
        if "444" in low:
            return "yuv444p"
        if "422" in low or low.startswith(("nv16", "p2", "y2", "yuyv", "uyvy")):
            return "yuv422p"
        return "yuv420p"
    return "rgb24"


def _fit(picture, side=SIDE):
    """RGB, at most ``side`` px (None — as it is)."""
    from PIL import Image
    picture = picture.convert("RGB")
    if side and max(picture.size) > side:
        picture.thumbnail((side, side), Image.Resampling.BILINEAR)
    return picture


def turn_of(frame) -> int:
    """How far a PyAV frame is to be turned clockwise, as FFmpeg turns it (its display matrix: a phone's video
    «lying on its side»), 0, 90, 180 or 270 — FFmpeg (the sites' previews) and OpenCV turn it, PyAV does not."""
    try:
        rotation = float(getattr(frame, "rotation", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return int(round(-rotation / 90.0)) % 4 * 90


def upright(frame):
    """A PyAV frame as an RGB picture the way FFmpeg shows it (turned by its display matrix)."""
    from PIL import Image
    picture = frame.to_image()
    turn = turn_of(frame)
    if turn == 90:
        picture = picture.transpose(Image.Transpose.ROTATE_270)
    elif turn == 180:
        picture = picture.transpose(Image.Transpose.ROTATE_180)
    elif turn == 270:
        picture = picture.transpose(Image.Transpose.ROTATE_90)
    return picture


def _picks_pyav(path: Path):
    """("pyav", the two frames' numbers, the pts of the frames read) by PyAV; None — PyAV has no video in it to read."""
    import av
    histograms, stamps = [], []
    with av.open(str(path)) as container:
        if not container.streams.video:
            return None
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        for frame in container.decode(stream):
            histograms.append(frame_histogram(frame))
            stamps.append(frame.pts)
            if len(histograms) >= READ_LIMIT:
                break
    if not histograms:
        return None
    return "pyav", picks(histograms), stamps


def _frame_at(av, path: Path, index: int, stamps: list, side=SIDE):
    """The ``index``-th frame of the first reading again, at most ``side`` px: from the key frame before its time
    (``stamps`` — the frames' pts), not the whole start once more; that did not give the very frame (a file without
    pts, a seek the file does not allow) — read on from its start."""
    stamp = stamps[index] if index < len(stamps) else None
    if stamp is not None and index:
        try:
            with av.open(str(path)) as container:
                stream = container.streams.video[0]
                stream.thread_type = "AUTO"
                container.seek(stamp, stream=stream, backward=True, any_frame=False)
                for frame in container.decode(stream):
                    if frame.pts is not None and frame.pts >= stamp:
                        if frame.pts == stamp:
                            return _fit(upright(frame), side)
                        break
        except Exception as exc:  # the file does not allow it: read on from its start below
            _LOG.info("SITE FRAMES: %s: кадр %d не найден переходом (%s: %s)", path.name, index, type(exc).__name__,
                      exc)
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        for number, frame in enumerate(container.decode(stream)):
            if number == index:
                return _fit(upright(frame), side)
    return None


#: BGR (as OpenCV gives a frame) -> Y, Cb, Cr of BT.601 at the video range (16–235): what the decoder had before
#: OpenCV turned it into BGR, nearly (on 40 videos the frame chosen from these and FFmpeg's: pHash d ≤ 4 in 62 of 72
#: choices, d > 10 in 2; from OpenCV's own BGR→YUV — in 59 and 6)
_BT601 = ((24.966, 128.553, 65.481), (112.0, -74.203, -37.797), (-18.214, -93.786, 112.0))


def _bgr_histogram(np, frame):
    weights = np.asarray(_BT601, dtype=np.float32) / 255.0
    luma = frame.astype(np.float32) @ weights[0] + 16.0
    small = frame[::2, ::2].astype(np.float32)
    parts = [luma, small @ weights[1] + 128.0, small @ weights[2] + 128.0]
    return np.concatenate([_counts(np, np.clip(np.rint(p), 0, 255).astype(np.uint8)) for p in parts])


def _picks_opencv(path: Path) -> dict:
    """The two frames' numbers by OpenCV: its BGR turned back into the video's YUV 4:2:0 for the histograms (FFmpeg's
    choice from the decoder's own planes may be another frame of the same scene); {} — nothing read."""
    import cv2
    import numpy as np
    histograms = []
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return {}
        while len(histograms) < READ_LIMIT:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            histograms.append(_bgr_histogram(np, frame))
    finally:
        cap.release()
    return picks(histograms) if histograms else {}


def _cv_frame_at(path: Path, index: int, side=SIDE):
    """The ``index``-th frame read by OpenCV from the start (as its numbers were counted), at most ``side`` px."""
    import cv2
    from PIL import Image
    cap = cv2.VideoCapture(str(path))
    try:
        for number in range(index + 1):
            ok, frame = cap.read()
            if not ok or frame is None:
                return None
            if number == index:
                return _fit(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), side)
    finally:
        cap.release()
    return None
