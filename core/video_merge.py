"""Voice-overs of one episode in one file (v5.42): one picture, every voice-over a sound track of an MKV.

The owner asked for it (31.10.2026): «видеоряд один, а озвучка разная» — a library of many voice-overs would otherwise
keep the same picture once per voice-over (36 voice-overs of a «Фрирен» episode: ~10 GB in 720p, one file: ~1.2 GB).

Each voice-over's file has the same picture, so the sounds are put on one picture by the pictures, not by the sounds:

* **prints** (:func:`prints_of`, PyAV): every frame of a file as a tiny grey picture's horizontal gradient signs (16×9 →
  144 bits), with its time on the file's own clock;
* **anchors** (:func:`align`): a print of the voice-over every ``STEP`` seconds matched to the most alike print of the
  picture — kept only when it is clearly the one (close, and far better than any other place);
* **runs**: anchors of one shift (picture time − voice-over time) in a row; runs of the same shift across a dark or
  still stretch are one; a shift that changes is a cut: the voice-over has a piece the picture has not (a studio's
  intro, an advertisement) — its sound is left out —, or the picture has a piece the voice-over has not — silence there;
  where exactly it is cut is found frame by frame (``_cut``);
* each run's shift is made exact to a frame (``_exact``);
* too little matched (``MIN_COVER`` of the picture) — another version of the video (TV/BD, another cut): the voice-over
  is not put on it, it stays a file of its own, and it is said.

**The file** (:func:`merge`): the picture's packets as they are; each voice-over's AAC packets as they are, at their
new times (no sound is coded again: ADTS headers of MPEG-TS are taken off, the 2-byte AudioSpecificConfig made of
them); a gap is filled with silence coded in the track's own rate and channels; the tracks named («AniLibria»,
language «rus»).  Written beside as ``.part``, opened and checked (streams, length, a frame and sound at the end),
then put in place.

The ``.lb`` folder of a series (skipped by the library) keeps the prints of a picture between runs.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import struct
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

_LOG = logging.getLogger("local_booru.video")

STEP = 0.25                 # seconds between the prints of a voice-over that are matched to the picture
SIZE = (16, 9)              # the tiny picture a print is made of (gradient signs: 16 × 9 bits)
MAX_DIST = 24               # bits of 144 two prints of the same frame may differ by (another encoding, scaling)
MARGIN = 12                 # … and the next best place must be this much worse: else the frame is not telling
GUARD = 8                   # prints next to the best one (the same scene) are not «another place»
RUN_TOL = 0.35              # seconds: anchors of shifts this close are one run
MIN_RUN = 4                 # anchors a run needs to be believed (after the lone ones are left out — ``_steady``)
NEIGHBOURS = 3              # anchors on each side an anchor's shift is checked against
MIN_COVER = 0.6             # of the picture's length the voice-over must match, else it is another video
SILENCE_MIN = 0.03          # seconds of a gap filled with silence (smaller ones are the packets' own steps)
CHUNK = 1024                # voice-over prints matched at once (memory: CHUNK × picture prints)


class MergeError(Exception):
    """Said to the person as it is."""


def available() -> bool:
    try:
        import av  # noqa: F401
        import numpy  # noqa: F401 - the prints are numpy arrays
    except ImportError:
        return False
    return True


# ── prints ─────────────────────────────────────────────────────────────────────────
@dataclass
class Prints:
    times: object                   # numpy float64: each frame's time on the file's clock, seconds
    bits: object                    # numpy uint8 (frames × 18): its print
    frame: float = 1 / 24           # seconds of one frame
    width: int = 0
    height: int = 0

    @property
    def length(self) -> float:
        return float(self.times[-1] - self.times[0]) + self.frame if len(self.times) else 0.0


PRINTS_VERSION = 2          # how a print is made (2: the picture made small by hand first — ``thumb``); kept prints
                            # of another way are made anew
THUMB_FROM = 480            # a frame is cut down to about this width before it is made 17 × 9 (its every k-th pixel)
_EIGHT_BIT = {"yuv420p", "yuvj420p", "yuv422p", "yuvj422p", "yuv444p", "yuvj444p", "nv12", "nv21", "gray"}


def thumb(frame):
    """A frame's tiny grey picture (``SIZE[0] + 1`` × ``SIZE[1]``, int16).  Made small from a large frame directly, the
    scaler spends most of the time (4K: ~6 ms a frame — the owner's computer decoded 4K at ~1000 frames a second and
    made prints of ~150; a video card does not help: the frames are brought back to the processor slower), so its
    brightness plane is cut down first to ~``THUMB_FROM`` wide (every k-th pixel, no copy), then made 17 × 9.  Two
    encodes of a picture give closer prints so (3.6 bits of 144 differ on average against 4.3) — and in any size."""
    import av
    import numpy as np
    if frame.format.name in _EIGHT_BIT and frame.width and frame.height:
        plane = frame.planes[0]
        k = max(1, frame.width // THUMB_FROM)
        luma = np.frombuffer(plane, dtype=np.uint8).reshape(-1, plane.line_size)[:frame.height:k, :frame.width:k]
        small = av.VideoFrame.from_ndarray(np.ascontiguousarray(luma), format="gray")
        return small.reformat(width=SIZE[0] + 1, height=SIZE[1]).to_ndarray().astype(np.int16)
    return frame.to_ndarray(format="gray", width=SIZE[0] + 1, height=SIZE[1]).astype(np.int16)


def prints_of(path: Path, *, stop=None, cache: Path | None = None, keys: bool = False, start: float | None = None,
              end: float | None = None, threads: int = 0) -> Prints:
    """The prints of every frame of a file's picture (kept in ``cache`` by the file's size and time); ``keys`` — of
    its key frames only (8 times faster: where a voice-over is on the picture is found by them, the exact frame in
    windows — :class:`Detail`); ``start``/``end`` — of the frames of that stretch only (seconds, the file's clock);
    ``threads`` — the decoder's threads at most (work in the background leaves the computer to the person; 0 — all)."""
    import av
    import numpy as np
    path = Path(path)
    stat = path.stat()
    key = prints_key(stat, keys)
    if start is not None:
        cache = None
    if cache is not None and cache.is_file():
        try:
            with np.load(cache) as kept:
                if str(kept["key"]) == key:
                    return Prints(kept["times"], kept["bits"], float(kept["frame"]), int(kept["width"]),
                                  int(kept["height"]))
        except Exception as exc:   # noqa: BLE001 - a cut-short or foreign file (BadZipFile, EOFError …): made anew
            _LOG.info("prints of %s not read from %s: %s: %s", path.name, cache, type(exc).__name__, exc)
    times, bits = [], []
    try:
        with av.open(str(path)) as box:
            if not box.streams.video:
                raise MergeError(f"в файле {path.name} нет видео")
            stream = box.streams.video[0]
            stream.thread_type = "AUTO"
            if threads:
                stream.thread_count = int(threads)
            if keys:
                stream.codec_context.skip_frame = "NONKEY"
            rate = stream.average_rate or stream.guessed_rate or Fraction(24)
            width, height = stream.codec_context.width, stream.codec_context.height
            frames = box.decode(stream)
            if start is not None:
                # a seek lands on a key frame — in MPEG-TS sometimes after the place asked: from further back then
                margin = 1.0
                while True:
                    box.seek(int(max(0.0, start - margin) / stream.time_base), stream=stream)
                    frames = box.decode(stream)
                    first = next(frames, None)
                    if first is None or first.time is None or first.time <= start or start - margin <= 0:
                        break
                    margin *= 4
                frames = itertools.chain([first] if first is not None else [], frames)
            for n, frame in enumerate(frames):
                if stop is not None and n % 100 == 0 and stop():      # (v5.44.151; was 500 — seconds with one thread)
                    raise MergeError("остановлено")
                if frame.time is None or (start is not None and frame.time < start):
                    continue
                if end is not None and frame.time > end:
                    break
                grey = thumb(frame)
                bits.append(np.packbits((grey[:, 1:] > grey[:, :-1]).ravel()))
                times.append(frame.time)
    except av.FFmpegError as exc:
        raise MergeError(f"файл {path.name} не читается: {exc}") from exc
    if len(times) < (2 if start is not None else 3 if keys else 20):
        raise MergeError(f"в файле {path.name} почти нет кадров")
    order = np.argsort(np.array(times))
    found = Prints(np.array(times)[order], np.array(bits)[order], float(1 / rate), width, height)
    if cache is not None:
        keep(cache, path, found, keys=keys)
    return found


def prints_key(stat, keys: bool = False) -> str:
    """What kept prints of a file are kept under: its size and time, key frames or all, the way prints are made."""
    return f"{stat.st_size}-{int(stat.st_mtime)}-{'k' if keys else 'f'}{PRINTS_VERSION}"


def kept_prints(cache: Path, path: Path, *, keys: bool = False):
    """The prints of ``path`` kept in ``cache`` — None when there are none for the file as it is now (nothing is
    read from the file itself)."""
    import numpy as np
    try:
        key = prints_key(Path(path).stat(), keys)
        if not Path(cache).is_file():
            return None
        with np.load(cache) as kept:
            if str(kept["key"]) == key:
                return Prints(kept["times"], kept["bits"], float(kept["frame"]), int(kept["width"]), int(kept["height"]))
    except Exception as exc:   # noqa: BLE001 - a cut-short or foreign file: as if none
        _LOG.info("prints of %s not read from %s: %s: %s", Path(path).name, cache, type(exc).__name__, exc)
    return None


def restamp(cache: Path, old_key: str, new_key: str) -> bool:
    """Kept prints (or any kept ``.npz`` with a ``key``) of a file whose bytes changed but not what they were made of
    (an episode's MKV got another voice-over's track: its picture, its first track are the same) — kept under the
    file's new key."""
    import numpy as np
    try:
        if not Path(cache).is_file():
            return False
        with np.load(cache) as kept:
            if str(kept["key"]) != old_key:
                return False
            arrays = {name: kept[name] for name in kept.files if name != "key"}
    except Exception as exc:   # noqa: BLE001 - a cut-short or foreign file: left to be made anew
        _LOG.info("%s not restamped: %s: %s", cache, type(exc).__name__, exc)
        return False
    return save_kept(Path(cache), key=new_key, **arrays)


def keep(cache: Path, path: Path, prints: Prints, *, keys: bool = False) -> None:
    """``prints`` of ``path`` (as it is now) kept in ``cache``."""
    try:
        stat = path.stat()
    except OSError:
        return
    save_kept(cache, key=prints_key(stat, keys), times=prints.times,
              bits=prints.bits, frame=prints.frame, width=prints.width, height=prints.height)


def save_kept(cache: Path, **arrays) -> bool:
    """numpy arrays kept in ``cache`` (.npz) whole or not at all: written beside it, then put in its place — a full
    disk or a stop halfway leaves no cut-short file (it would be read as broken every time)."""
    import numpy as np
    part = cache.with_name(cache.name + ".part")
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with open(part, "wb") as out:
            np.savez(out, **arrays)
        os.replace(part, cache)
        return True
    except OSError as exc:
        _LOG.info("%s not kept: %s", cache.name, exc)
        try:
            part.unlink()
        except OSError:
            pass
        return False


# ── the voice-over put on the picture ──────────────────────────────────────────────────
@dataclass
class Piece:
    start: float                    # voice-over time (its file's clock) the piece begins at
    end: float                      # … and ends at
    shift: float                    # picture time = voice-over time + shift


@dataclass
class Fit:
    pieces: list = field(default_factory=list)
    cover: float = 0.0              # share of the picture's length matched
    dropped: float = 0.0            # seconds of the voice-over left out (its intro, inserts)
    silent: float = 0.0             # seconds of the picture without it (filled with silence)

    def words(self) -> str:
        bits = []
        shifts = sorted({round(p.shift, 2) for p in self.pieces})
        if shifts and any(abs(s) >= 0.05 for s in shifts):
            bits.append("сдвиг " + ", ".join(f"{s:+.2f} с".replace(".", ",") for s in shifts))
        if self.dropped >= 0.5:
            bits.append(f"вырезано своё {self.dropped:.0f} с".replace(".", ","))
        if self.silent >= 0.5:
            bits.append(f"тишина {self.silent:.0f} с")
        return "; ".join(bits) or "совпадает как есть"


class Detail:
    """Every frame of stretches of a voice-over's file, read when asked (its key frames were enough to find where it
    goes; the exact frame of a shift and of a cut needs them all)."""

    WINDOW = 4.0                     # seconds of one stretch read to make a shift exact
    WINDOWS = 5                      # stretches of a run
    MAX_GAP = 90.0                   # seconds of a gap between runs read to find the cut (more: its ends)

    def __init__(self, path: Path, stop=None):
        self.path, self.stop = Path(path), stop

    def frames(self, start: float, end: float) -> Prints | None:
        try:
            return prints_of(self.path, stop=self.stop, start=start, end=end)
        except MergeError as exc:
            if str(exc) == "остановлено":
                raise
            _LOG.info("frames %.1f–%.1f of %s not read: %s", start, end, self.path.name, exc)
            return None


def _joined(parts: list) -> Prints | None:
    import numpy as np
    parts = [p for p in parts if p is not None and len(p.times)]
    if not parts:
        return None
    times = np.concatenate([p.times for p in parts])
    bits = np.concatenate([p.bits for p in parts])
    order = np.argsort(times)
    keep = np.concatenate([[True], np.diff(times[order]) > 1e-6])
    return Prints(times[order][keep], bits[order][keep], parts[0].frame, parts[0].width, parts[0].height)


def _unpacked(prints: Prints):
    import numpy as np
    return np.unpackbits(prints.bits, axis=1).astype(np.float32)


def _anchors(picture: Prints, voice: Prints):
    """(voice time, picture time) of the voice-over's prints every ``STEP`` that are clearly one place of the
    picture."""
    import numpy as np
    pick = np.searchsorted(voice.times, np.arange(voice.times[0], voice.times[-1], STEP))
    pick = np.unique(np.clip(pick, 0, len(voice.times) - 1))
    vt, vb = voice.times[pick], _unpacked(Prints(voice.times[pick], voice.bits[pick]))
    pb = _unpacked(picture)
    ps = pb.sum(1)
    out = []
    for at in range(0, len(vt), CHUNK):
        chunk = vb[at:at + CHUNK]
        dist = chunk.sum(1)[:, None] + ps[None, :] - 2 * (chunk @ pb.T)
        best = dist.argmin(1)
        for k, i in enumerate(best):
            row = dist[k]
            d1 = row[i]
            if d1 > MAX_DIST:
                continue
            lo, hi = max(0, i - GUARD * 6), i + GUARD * 6 + 1       # frames: the same scene, ±2 s at 24 fps
            others = np.concatenate([row[:lo], row[hi:]])
            if others.size and others.min() - d1 < MARGIN:
                continue
            out.append((float(vt[at + k]), float(picture.times[i])))
    return out


def _steady(anchors: list) -> list:
    """The anchors whose shift is their neighbours' (a frame that was matched to a like one elsewhere — a repeated
    shot, a still — is left out)."""
    if len(anchors) < 3:
        return anchors
    shifts = [t - u for u, t in anchors]
    out = []
    for n, (u, t) in enumerate(anchors):
        near = sorted(shifts[max(0, n - NEIGHBOURS):n] + shifts[n + 1:n + 1 + NEIGHBOURS])
        if abs((t - u) - near[len(near) // 2]) <= RUN_TOL:
            out.append((u, t))
    return out


def _runs(anchors: list) -> list[dict]:
    anchors = _steady(anchors)
    runs, cur = [], None
    for u, t in anchors:
        shift = t - u
        if cur is not None and abs(shift - cur["shift"]) <= RUN_TOL:
            cur["end"] = u
            cur["shifts"].append(shift)
            cur["shift"] = sorted(cur["shifts"][-25:])[len(cur["shifts"][-25:]) // 2]
            continue
        if cur is not None:
            runs.append(cur)
        cur = {"start": u, "end": u, "shift": shift, "shifts": [shift]}
    if cur is not None:
        runs.append(cur)
    runs = [r for r in runs if len(r["shifts"]) >= MIN_RUN]
    joined = []                                  # one shift on both sides of an untelling stretch: one run
    for r in runs:
        if joined and abs(r["shift"] - joined[-1]["shift"]) <= RUN_TOL:
            joined[-1]["end"] = r["end"]
            joined[-1]["shifts"] += r["shifts"]
        else:
            joined.append(dict(r))
    for r in joined:
        values = sorted(r["shifts"])
        r["shift"] = values[len(values) // 2]
    return joined


def _distance_at(picture: Prints, pb, voice: Prints, vb, idx, shift: float):
    """Mean distance of the voice-over's frames ``idx`` to the picture's frames nearest to them at ``shift``."""
    import numpy as np
    at = voice.times[idx] + shift
    near = np.clip(np.searchsorted(picture.times, at), 0, len(picture.times) - 1)
    before = np.clip(near - 1, 0, len(picture.times) - 1)
    near = np.where(np.abs(picture.times[before] - at) < np.abs(picture.times[near] - at), before, near)
    return np.abs(vb[idx] - pb[near]).sum(1)


def _exact(picture: Prints, pb, voice: Prints, vb, run: dict) -> float:
    """The run's shift to a frame: the one of ±6 frames around it its frames are most alike at."""
    import numpy as np
    idx = np.nonzero((voice.times >= run["start"]) & (voice.times <= run["end"]))[0]
    if len(idx) > 2000:
        idx = idx[np.linspace(0, len(idx) - 1, 2000).astype(int)]
    best, value = run["shift"], None
    for k in range(-6, 7):
        shift = run["shift"] + k * picture.frame
        score = float(_distance_at(picture, pb, voice, vb, idx, shift).mean())
        if value is None or score < value:
            best, value = shift, score
    return best


def _cut(picture: Prints, pb, voice: Prints, vb, a: dict, b: dict) -> tuple[float, float]:
    """Where the voice-over goes from run ``a`` to run ``b``: (end of a, start of b) on its clock."""
    import numpy as np
    lo, hi = a["end"], b["start"]
    idx = np.nonzero((voice.times >= lo) & (voice.times <= hi))[0]
    gap = a["shift"] - b["shift"]                # > 0: the voice-over has a piece of its own here (left out)
    if len(idx) < 2:
        return (lo, lo + max(0.0, gap)) if gap > 0 else (lo, lo)
    da = _distance_at(picture, pb, voice, vb, idx, a["shift"]) <= MAX_DIST
    db = _distance_at(picture, pb, voice, vb, idx, b["shift"]) <= MAX_DIST
    times = voice.times[idx]
    best, score = lo, -1
    for k in range(len(idx) + 1):                # the first frame of b's side: k
        c = times[k] if k < len(idx) else hi
        if gap > 0:                              # a's side ends at c; b's begins at c + gap
            after = times >= c + gap
            value = int(da[:k].sum()) + int(db[after].sum())
        else:
            value = int(da[:k].sum()) + int(db[k:].sum())
        if value > score:
            best, score = c, value
    return (best, best + gap) if gap > 0 else (best, best)


def align(picture: Prints, voice: Prints, detail: Detail | None = None) -> Fit:
    """How the voice-over's file goes onto the picture; :class:`MergeError` when it is another video.  ``voice`` —
    the prints of all its frames, or of its key frames with ``detail`` to read every frame where it is needed."""
    pb = _unpacked(picture)
    runs = _runs(_anchors(picture, voice))
    if not runs:
        raise MergeError("картинка не совпадает ни в одном месте — это другое видео")
    for run in runs:
        near = voice
        if detail is not None:                    # windows across the run, every frame
            span = max(0.0, run["end"] - run["start"] - Detail.WINDOW)
            starts = [run["start"] + span * k / max(1, Detail.WINDOWS - 1) for k in range(Detail.WINDOWS)]
            near = _joined([detail.frames(a, a + Detail.WINDOW) for a in dict.fromkeys(round(x, 2) for x in starts)])
            near = near or voice
        run["shift"] = _exact(picture, pb, near, _unpacked(near), run)
    pieces = []
    start = float(voice.times[0])
    for a, b in zip(runs, runs[1:]):
        near = voice
        if detail is not None:                    # the gap between the runs, every frame (its ends when long)
            lo, hi = a["end"] - 1, b["start"] + 1
            if hi - lo <= Detail.MAX_GAP:
                near = detail.frames(lo, hi)
            else:
                near = _joined([detail.frames(lo, lo + Detail.MAX_GAP / 2), detail.frames(hi - Detail.MAX_GAP / 2, hi)])
            near = near or voice
        end_a, start_b = _cut(picture, pb, near, _unpacked(near), a, b)
        pieces.append(Piece(start, end_a, a["shift"]))
        start = start_b
    pieces.append(Piece(start, float(voice.times[-1]) + voice.frame, runs[-1]["shift"]))
    fit = Fit(pieces=pieces)
    first, last = float(picture.times[0]), float(picture.times[-1]) + picture.frame
    matched = 0.0
    for run in runs:
        lo, hi = max(first, run["start"] + run["shift"]), min(last, run["end"] + run["shift"])
        matched += max(0.0, hi - lo)
    fit.cover = matched / max(0.001, last - first)
    for p, q in zip(pieces, pieces[1:]):
        fit.dropped += max(0.0, q.start - p.end)
        fit.silent += max(0.0, (q.start + q.shift) - (p.end + p.shift))
    if fit.cover < MIN_COVER:
        raise MergeError(f"картинка совпала только на {fit.cover:.0%} — похоже, это другая версия видео "
                         "(ТВ и BD, другой монтаж)")
    return fit


# ── sound: packets of AAC ──────────────────────────────────────────────────────────────
_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


def adts_split(data: bytes) -> tuple[list[bytes], bytes]:
    """The raw AAC frames of ADTS data and their AudioSpecificConfig ([] , b"" — not ADTS)."""
    frames, config, at = [], b"", 0
    while at + 7 <= len(data):
        head = data[at:at + 9]
        if head[0] != 0xFF or (head[1] & 0xF0) != 0xF0:
            return [], b""
        absent = head[1] & 1
        profile = (head[2] >> 6) + 1
        rate = (head[2] >> 2) & 0x0F
        channels = ((head[2] & 1) << 2) | (head[3] >> 6)
        size = ((head[3] & 3) << 11) | (head[4] << 3) | (head[5] >> 5)
        blocks = (head[6] & 3) + 1
        header = 7 if absent else 9
        if size < header or at + size > len(data) or blocks != 1:
            return [], b""
        frames.append(bytes(data[at + header:at + size]))
        config = struct.pack(">H", (profile << 11) | (rate << 7) | (channels << 3))
        at += size
    return (frames, config) if at == len(data) else ([], b"")


@dataclass
class Track:
    path: Path                      # the file the sound is in
    title: str                      # «AniLibria»
    language: str = "rus"
    fit: Fit | None = None          # how it goes onto the picture (None — the picture's own file: as it is)
    index: int = 0                  # the sound stream of the file (its number among the file's sound streams)


def _sound(path: Path, index: int = 0):
    """(rate, layout name, AudioSpecificConfig, [(time on the file's clock, duration, raw AAC frame)])."""
    import av
    packets, config = [], b""
    with av.open(str(path)) as box:
        streams = [s for s in box.streams.audio]
        if len(streams) <= index:
            raise MergeError(f"в файле {path.name} нет звука")
        stream = streams[index]
        cc = stream.codec_context
        if cc.name != "aac":
            raise MergeError(f"звук в {path.name} — {cc.name}, программа собирает только AAC")
        config = bytes(cc.extradata or b"")
        rate, layout = _described(config) if config else (0, "")
        rate, layout = rate or cc.sample_rate, layout or (cc.layout.name if cc.layout else "")
        for packet in box.demux(stream):
            if packet.pts is None or packet.size == 0:
                continue
            data = bytes(packet)
            raw, made = adts_split(data) if data[:1] == b"\xff" else ([data], b"")
            if not raw:
                raise MergeError(f"звук в {path.name} не разобрать (ADTS)")
            if not config and made:              # MPEG-TS: the description is in every ADTS header
                config = made
                rate, layout = _described(made)
            step = 1024 / rate if rate else 0.0
            at = float(packet.pts * packet.time_base)
            for n, frame in enumerate(raw):
                packets.append((at + n * step, step, frame))
    if not config or not rate or not layout:
        raise MergeError(f"у звука в {path.name} нет описания (частота, каналы)")
    packets.sort(key=lambda p: p[0])
    return rate, layout, config, packets


_LAYOUTS = {1: "mono", 2: "stereo", 3: "3.0", 4: "4.0", 5: "5.0", 6: "5.1", 8: "7.1"}


def _described(config: bytes) -> tuple[int, str]:
    """(rate, layout) an AudioSpecificConfig names (0, "") when it names them otherwise (an explicit rate…)."""
    if len(config) < 2:
        return 0, ""
    value = int.from_bytes(config[:2], "big")
    index, channels = (value >> 7) & 0x0F, (value >> 3) & 0x0F
    channels = 8 if channels == 7 else channels
    return (_RATES[index] if index < len(_RATES) else 0), _LAYOUTS.get(channels, "")


_QUIET: dict = {}


def _silence(rate: int, layout: str) -> bytes:
    """One AAC frame of silence in the track's rate and channels (coded here once; silence frames are all alike)."""
    import av
    import numpy as np
    key = (rate, layout)
    if key not in _QUIET:
        coder = av.CodecContext.create("aac", "w")
        coder.sample_rate, coder.layout, coder.format = rate, layout, "fltp"
        coder.time_base = Fraction(1, rate)
        coder.open()
        channels = len(av.AudioLayout(layout).channels)
        made = []
        for n in range(12):                      # past the coder's first frames
            frame = av.AudioFrame.from_ndarray(np.zeros((channels, 1024), dtype=np.float32), format="fltp",
                                               layout=layout)
            frame.sample_rate, frame.pts = rate, n * 1024
            made += [bytes(p) for p in coder.encode(frame)]
        made += [bytes(p) for p in coder.encode(None)]
        if not made:
            raise MergeError("тишина для пропуска не кодируется")
        _QUIET[key] = made[min(len(made) - 1, 6)]
    return _QUIET[key]


def placed(track: Track, origin: float, length: float):
    """The track's sound on the picture's timeline (seconds from its first frame): (rate, layout, config,
    [(time, raw frame)]) — what the picture has not filled with silence."""
    rate, layout, config, packets = _sound(track.path, track.index)
    step = 1024 / rate
    out = []
    pieces = track.fit.pieces if track.fit is not None else None
    for at, _dur, frame in packets:
        if pieces is None:
            when = at - origin
        else:
            piece = next((p for p in pieces if p.start - 1e-6 <= at < p.end), None)
            if piece is None:
                continue
            when = at + piece.shift - origin
        if -step / 2 <= when < length and (not out or when >= out[-1][0] + step * 0.5):
            out.append((max(0.0, when), frame))
    filled, cursor = [], 0.0
    for when, frame in out + [(length, None)]:
        gap = when - cursor
        if gap >= max(SILENCE_MIN, step * 1.5):  # a gap: silence frames that fit before the next sound
            quiet = _silence(rate, layout)
            filled += [(cursor + n * step, quiet) for n in range(int(gap / step))]
        if frame is not None:
            filled.append((when, frame))
            cursor = when + step
    return rate, layout, config, filled


# ── the file ───────────────────────────────────────────────────────────────────────────
def merge(picture: Path, tracks: list[Track], target: Path, *, stop=None) -> float:
    """``target`` (.mkv): the picture of ``picture`` with ``tracks`` as its sound tracks, in this order.
    Written as ``<target>.part``, checked, then put in place (an old ``target`` replaced).  The picture's first
    frame's time in ``picture`` (the new file begins there)."""
    import av
    part = target.with_name(target.name + ".part")
    part.unlink(missing_ok=True)
    try:
        with av.open(str(picture)) as box:
            video = box.streams.video[0]
            origin = float(video.start_time * video.time_base) if video.start_time is not None else 0.0
            length = float(video.duration * video.time_base) if video.duration else \
                float(box.duration / av.time_base) if box.duration else 0.0
        sounds = []
        for track in tracks:
            if stop is not None and stop():
                raise MergeError("остановлено")
            sounds.append(placed(track, origin, length or 1e9))
        with av.open(str(picture)) as box, av.open(str(part), "w", format="matroska") as out:
            video = box.streams.video[0]
            vout = out.add_stream_from_template(video)
            aouts = []
            for track, (rate, layout, config, _packets) in zip(tracks, sounds):
                stream = out.add_stream("aac", rate=rate, layout=layout)
                stream.codec_context.extradata = config
                stream.time_base = Fraction(1, 1000)
                stream.metadata["title"] = track.title
                stream.metadata["language"] = track.language
                aouts.append(stream)
            queue = []                               # sound packets, each track in time order
            for n, (_rate, _layout, _config, packets) in enumerate(sounds):
                queue += [(when, n, frame) for when, frame in packets]
            queue.sort(key=lambda q: (q[0], q[1]))
            q = 0
            for packet in box.demux(video):
                if stop is not None and stop():
                    raise MergeError("остановлено")
                if packet.dts is None and packet.pts is None:
                    continue
                now = float(((packet.dts if packet.dts is not None else packet.pts) * packet.time_base)) - origin
                while q < len(queue) and queue[q][0] <= now:
                    _mux_sound(out, aouts[queue[q][1]], queue[q])
                    q += 1
                if packet.pts is not None:
                    packet.pts -= int(round(origin / packet.time_base))
                if packet.dts is not None:
                    packet.dts -= int(round(origin / packet.time_base))
                packet.stream = vout
                out.mux(packet)
            while q < len(queue):
                _mux_sound(out, aouts[queue[q][1]], queue[q])
                q += 1
        check(part, len(tracks), length)
        os.replace(part, target)
        return origin
    except av.FFmpegError as exc:
        part.unlink(missing_ok=True)
        raise MergeError(f"файл не собрался: {exc}") from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _mux_sound(out, stream, item) -> None:
    import av
    when, _n, frame = item
    packet = av.Packet(frame)
    packet.stream = stream
    packet.time_base = Fraction(1, 1000)
    packet.pts = packet.dts = int(round(when * 1000))
    packet.duration = int(round(1024 / stream.codec_context.sample_rate * 1000))
    out.mux(packet)


def check(path: Path, tracks: int, length: float) -> None:
    """The file made: one picture, ``tracks`` sound tracks, about the picture's length, sound decodable at its end."""
    import av
    try:
        with av.open(str(path)) as box:
            if len(box.streams.video) != 1 or len(box.streams.audio) != tracks:
                raise MergeError(f"в собранном файле не те дорожки: видео {len(box.streams.video)}, "
                                 f"звука {len(box.streams.audio)} из {tracks}")
            have = float(box.duration / av.time_base) if box.duration else 0.0
            if length and have < length - 2:
                raise MergeError(f"собранный файл короче видео: {have:.0f} с из {length:.0f}")
            heard = set()                            # every sound track decodes near the end
            box.seek(int(max(0.0, have - 30) * av.time_base))
            for packet in _first(box.demux(*box.streams.audio), 4000):
                if packet.stream.index not in heard and any(True for _f in packet.decode()):
                    heard.add(packet.stream.index)
                if len(heard) == tracks:
                    break
            missing = [s for s in box.streams.audio if s.index not in heard]
            if missing:
                raise MergeError(f"звук «{missing[0].metadata.get('title', '?')}» в собранном файле не читается")
    except av.FFmpegError as exc:
        raise MergeError(f"собранный файл не читается: {exc}") from exc


def _first(items, count: int):
    for n, item in enumerate(items):
        if n >= count:
            return
        yield item


def tracks_in(path: Path) -> list[str]:
    """The titles of the sound tracks of a file made here (or any file): [] when it is not read."""
    import av
    try:
        with av.open(str(path)) as box:
            return [str(s.metadata.get("title") or s.metadata.get("TITLE") or "") for s in box.streams.audio]
    except (av.FFmpegError, OSError):
        return []


def note_of(folder: Path) -> Path:
    return folder / ".lb" / "merge.json"


def read_note(folder: Path) -> dict:
    try:
        return json.loads(note_of(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ── (v5.44.150) an episode's picture lent by a release whose voice-over was not asked for ───────────
PICTURES = "picture.json"


def read_pictures(folder: Path) -> dict:
    """{an episode's stem: {"group": the release's sub-folder, "state"}} — ``.lb/picture.json`` of the series folder.
    "wanted": the release's file of the episode is downloaded for its picture alone (``video_dubs.Resolver.roles``:
    the original's cut in a better quality than the voice-overs asked for have) — its sound is not put into the
    episode's file; "refused": its picture did not fit the episode's file — not asked of it again."""
    from core.file_safety import read_json_kept
    place = Path(folder) / ".lb" / PICTURES
    try:
        data = read_json_kept(place, dict, what="pictures") if place.is_file() else None
    except (OSError, ValueError):
        data = None
    return {str(stem): item for stem, item in (data or {}).items()
            if isinstance(item, dict) and item.get("group") and item.get("state") in ("wanted", "refused")}


def put_picture(folder: Path, stem: str, group: str = "", state: str = "") -> None:
    """Notes (``state`` "wanted" / "refused") or forgets (no state) the release an episode's picture is lent by."""
    from core.file_safety import write_json_kept
    kept = read_pictures(folder)
    before = dict(kept)
    if state:
        kept[stem] = {"group": group, "state": state}
    else:
        kept.pop(stem, None)
    if kept == before:
        return
    place = Path(folder) / ".lb" / PICTURES
    try:
        place.parent.mkdir(parents=True, exist_ok=True)
        write_json_kept(place, json.dumps(kept, ensure_ascii=False, indent=1), dict)
    except OSError as exc:
        _LOG.info("the picture's note of %s not kept: %s", stem, exc)


# ── an episode of a series: its voice-overs' files → one file ────────────────────────────
def dub_key(name: str) -> str:
    """What makes two voice-overs' names one: the same words but for spaces, case and signs («Red Head Sound» and
    «RedHeadSound» — YummyAnime names a studio one way in one player and another way in the other), and «Озвучка X» is
    «X» (its track's name — ``title_of``).  A dubbing («Дубляж X»: the voices replaced) is not a voice-over («Озвучка
    X»: read over them), nor a subtitles' release — each keeps its word (the owner, 31.10.2026)."""
    import re
    text = str(name or "").casefold().strip()
    if text.startswith("озвучка"):
        text = text[len("озвучка"):]
    return re.sub(r"[\W_]+", "", text)


def title_of(group: str) -> str:
    """A voice-over's track name from its sub-folder: «Озвучка AniLibria» → «AniLibria»."""
    text = str(group or "").strip()
    for word in ("Озвучка ", "озвучка "):
        if text.startswith(word):
            return text[len(word):].strip() or text
    return text


def _height(path: Path) -> int:
    import av
    try:
        with av.open(str(path)) as box:
            return int(box.streams.video[0].codec_context.height or 0) if box.streams.video else 0
    except (av.FFmpegError, OSError):
        return 0


def picture_info(folder: Path, stem: str):
    """(height, seconds) of the picture an episode has in the series folder already — its MKV's, otherwise its largest
    voice-over file's; None when there is none."""
    if not available() or not folder.is_dir():
        return None
    import av
    mkv = folder / f"{stem}.mkv"
    found = [mkv] if mkv.is_file() else \
        [p for sub in folder.iterdir() if sub.is_dir() and not sub.name.startswith(".")
         and not sub.name.lower().startswith("субтитры") for p in (sub / f"{stem}.mp4", sub / f"{stem}.ts") if p.is_file()]
    best = None
    for path in found:
        try:
            with av.open(str(path)) as box:
                if not box.streams.video:
                    continue
                height = int(box.streams.video[0].codec_context.height or 0)
                seconds = float(box.duration / av.time_base) if box.duration else 0.0
        except (av.FFmpegError, OSError):
            continue
        if best is None or height > best[0]:
            best = (height, seconds)
    return best


def picture_height(folder: Path, stem: str) -> int:
    """The largest picture an episode has in the series folder already: its MKV, its voice-overs' files (0 — none)."""
    if not available():
        return 0
    found = [folder / f"{stem}.mkv"] + [p for sub in folder.iterdir() if sub.is_dir() and not sub.name.startswith(".")
                                        and not sub.name.lower().startswith("субтитры")
                                        for p in (sub / f"{stem}.mp4", sub / f"{stem}.ts")] if folder.is_dir() else []
    return max((_height(p) for p in found if p.is_file()), default=0)


def _prints_place(folder: Path, path: Path) -> Path:
    import hashlib
    name = hashlib.sha1(str(path.relative_to(folder) if path.is_relative_to(folder) else path).encode("utf-8"))
    return folder / ".lb" / "prints" / f"{name.hexdigest()[:16]}.npz"


def merge_episode(folder: Path, stem: str, files: list, *, say, stop, prefer: Path | str | None = None,
                  lent: bool = False) -> dict:
    """«<stem>.mkv» in the series folder: the voice-overs' files of one episode (``files``: [(track name, path)]) put
    into it — beside the ones it has.  The picture: ``prefer`` (one of ``files``: the original's cut chosen by
    ``video_dubs.Resolver.roles`` — it replaces the file's picture, its sound the track of the same name), otherwise
    the largest of them (the file's own one when equal).  A voice-over whose picture is another video stays a file of
    its own.  Its files put in are removed after the new file is checked.  (v5.44.150) ``lent`` — ``prefer`` gives its
    picture alone (a release whose voice-over was not asked for): its sound is no track; the episode's file is made
    anew on it even when no voice-over is added, and its file is removed after.  {"merged": [names], "kept": [(name,
    why)], "file": path or "", "picture": the file its picture came from, "repictured": the file got another
    picture, "refused": the lent picture was not taken (it fits nothing there)}."""
    target = folder / f"{stem}.mkv"
    had = tracks_in(target) if target.is_file() else []
    had_keys = {dub_key(name) for name in had}
    chosen = Path(prefer) if prefer and Path(prefer).is_file() and Path(prefer) != target else None
    lone = chosen if lent else None                      # gives the picture, no track
    fresh = [(name, Path(path)) for name, path in files if dub_key(name) not in had_keys and Path(path) != lone]
    chosen_name = next((name for name, path in files if chosen is not None and Path(path) == chosen), None)
    if lone is not None:
        chosen_name = None
    elif chosen_name is None:
        chosen = None
    result = {"merged": [], "kept": [], "file": "", "repictured": False, "refused": False}
    if not fresh and chosen is None:
        return result
    sources = ([(target, True)] if had else []) + [(path, False) for _name, path in fresh]
    picture = chosen if chosen is not None else max(sources, key=lambda s: (_height(s[0]), s[1]))[0]
    if stop():
        raise MergeError("остановлено")
    # one voice-over and no file yet: nothing to match to its picture — its prints (every frame read: a minute or two
    # an episode) are not made now (owner, 02.11.2026: a season of one voice-over «скачался», and the next one in
    # the queue waited for 25 such readings); a voice-over added later reads them then
    alone = not had and len(fresh) == 1 and fresh[0][1] == picture
    base = None if alone else prints_of(picture, stop=stop, cache=_prints_place(folder, picture))
    tracks: list[Track] = []
    if had:
        fit = None
        if picture != target:
            try:
                fit = align(base, prints_of(target, stop=stop, cache=_prints_place(folder, target), keys=True),
                            Detail(target, stop))
            except MergeError as exc:                    # the larger picture is another video: the file's own stays
                if str(exc) == "остановлено":
                    raise
                say(f"{stem}: картинка {picture.name} не совпала с общим файлом ({exc}) — оставляю картинку общего файла")
                picture = target
                base = prints_of(target, stop=stop, cache=_prints_place(folder, target))
        replaced = dub_key(chosen_name) if chosen_name is not None and picture == chosen \
            and dub_key(chosen_name) in had_keys else None
        tracks += [Track(chosen, name) if dub_key(name) == replaced  # its sound — from the picture's file now
                   else Track(target, name, fit=fit, index=n) for n, name in enumerate(had)]
    for name, path in fresh:
        if stop():
            raise MergeError("остановлено")
        if path == picture:
            tracks.append(Track(path, name))
            continue
        try:
            fit = align(base, prints_of(path, stop=stop, cache=_prints_place(folder, path), keys=True),
                        Detail(path, stop))
        except MergeError as exc:
            if str(exc) == "остановлено":
                raise
            result["kept"].append((name, str(exc)))
            say(f"{stem}: {name} — {exc}; остаётся отдельным файлом")
            continue
        say(f"{stem}: {name} — {fit.words()}")
        tracks.append(Track(path, name, fit=fit))
    added = [t for t in tracks if t.path != target]
    repictured = bool(had) and picture != target
    result["refused"] = lone is not None and (picture != lone or not tracks)
    if not tracks or (not added and not repictured):     # nothing to put in, and the picture there stays
        return result
    origin = merge(picture, tracks, target, stop=stop)
    result["merged"] = [t.title for t in added]
    result["file"] = str(target)
    result["picture"] = str(picture)
    result["repictured"] = repictured
    if lone is not None and picture == lone:             # the lent picture is in the file: its own file goes
        for gone in (lone, _prints_place(folder, lone)):
            try:
                gone.unlink(missing_ok=True)
            except OSError as exc:
                say(f"{stem}: {gone.name} не удалён — {exc}")
        try:
            if lone.parent != folder and not any(lone.parent.iterdir()):
                lone.parent.rmdir()
        except OSError as exc:
            _LOG.info("%s not removed: %s", lone.parent, exc)
    for track in added:                                  # the voice-overs' own files: in the new file now
        for gone in (track.path, _prints_place(folder, track.path)):
            try:
                gone.unlink(missing_ok=True)
            except OSError as exc:
                say(f"{stem}: {gone.name} не удалён — {exc}")
        try:
            if track.path.parent != folder and not any(track.path.parent.iterdir()):
                track.path.parent.rmdir()
        except OSError as exc:
            _LOG.info("%s not removed: %s", track.path.parent, exc)
    # the new file's picture is the picture's: its prints, on the new file's clock — the next voice-over is matched to
    # them without reading the file again
    if base is not None:
        keep(_prints_place(folder, target), target, Prints(base.times - origin, base.bits, base.frame, base.width,
                                                           base.height))
    return result


# ── the voice-overs of a series' episodes («Скачать с сайта», ``core/video_dubs``) ─────────────
class Merger:
    """``Found.had`` and ``Found.after``: a voice-over's episode in the episode's MKV is had; after «Скачать» (when
    asked) the voice-overs' files of each episode go into «Серия NN.mkv» in the series folder.  Subtitles' releases
    (their words are in the picture) stay files of their own."""

    def __init__(self, files: list, folder: Path):
        self.files = files
        self.folder = folder
        self._tracks: dict = {}                # an MKV's path -> ((size, time), its track names)

    @staticmethod
    def stem(remote) -> str:
        return remote.name.rsplit(".", 1)[0]

    @staticmethod
    def title(remote) -> str:
        from core.video_sources import SPECIALS
        return title_of(str(remote.group or "").removesuffix(SPECIALS))

    @staticmethod
    def takes(remote) -> bool:
        return not str(remote.group or "").lower().startswith("субтитры")

    def tracks(self, path: Path) -> list[str]:
        try:
            stat = path.stat()
        except OSError:
            return []
        key = (stat.st_size, stat.st_mtime_ns)
        kept = self._tracks.get(str(path))
        if kept is None or kept[0] != key:
            kept = (key, tracks_in(path) if available() else [])
            self._tracks[str(path)] = kept
        return kept[1]

    def had(self, remote) -> bool:
        if not self.takes(remote):
            return False
        key = dub_key(self.title(remote))
        return any(dub_key(name) == key for name in self.tracks(self.folder / f"{self.stem(remote)}.mkv"))

    def waiting(self) -> set:
        """The episodes whose voice-overs lie in their sub-folders, not in the episode's file: left by a download that
        did not end (the program closed, «Остановить», an error) — put together before the next one begins."""
        out = set()
        for remote in self.files:
            if self.takes(remote) and remote.group:
                place = self.folder / remote.group
                stem = self.stem(remote)
                if any((place / f"{stem}{ext}").is_file() for ext in (".mp4", ".ts", ".aac")):
                    out.add(stem)
        return out

    def _lent_over(self, stem: str, lender: dict, lent, result: dict, say) -> None:
        """After an episode was put together: its lent picture is in the file — the note goes; it did not fit — said,
        noted «refused» (not asked of that release again), and its file goes when the episode has a file of its own
        (with none it stays: a whole episode, a voice-over as any other); the release's file never came — the note
        and what was begun of it go."""
        place = self.folder / lender["group"]
        if lender["state"] != "wanted":
            return
        if lent is None:
            if not result["merged"] and not result.get("repictured"):
                return                           # nothing was put together: it may still come
            for left in list(place.iterdir()) if place.is_dir() else ():
                if left.name.startswith(stem + ".") and ".part" in left.name:      # «Серия 07.mp4.part», its notes
                    try:
                        left.unlink()
                    except OSError as exc:
                        _LOG.info("%s not removed: %s", left, exc)
            put_picture(self.folder, stem)
        elif result.get("refused"):
            say(f"{stem}: картинка «{title_of(lender['group'])}» не подошла к серии — остаётся прежняя")
            put_picture(self.folder, stem, lender["group"], "refused")
            if (self.folder / f"{stem}.mkv").is_file():
                for gone in (lent, _prints_place(self.folder, lent)):
                    try:
                        gone.unlink(missing_ok=True)
                    except OSError as exc:
                        say(f"{stem}: {gone.name} не удалён — {exc}")
        elif result["merged"] or result.get("repictured"):
            put_picture(self.folder, stem)
        try:
            if place.is_dir() and not any(place.iterdir()):
                place.rmdir()
        except OSError as exc:
            _LOG.info("%s not removed: %s", place, exc)

    def __call__(self, *, say, stop, stems=None) -> dict:
        """The voice-overs' files of the episodes into their «Серия NN.mkv» — every episode of the folder, or those of
        ``stems`` (one whose files have all come: put together at once, not at the end of the whole download — owner,
        05.11.2026: a season stopped half-way stayed half in «Озвучка JAM»)."""
        from core import video_library
        if not available():
            say("Собрать озвучки в один файл нельзя: в программе нет библиотеки PyAV — серии остались отдельными файлами")
            return {"episodes": 0}
        episodes: dict = {}
        for remote in self.files:                # every voice-over's episode in the folder, not only this download's
            if not self.takes(remote):
                continue
            stem = self.stem(remote)
            if stems is not None and stem not in stems:
                continue
            place = self.folder / remote.group if remote.group else self.folder
            path = next((place / f"{stem}{ext}" for ext in (".mp4", ".ts", ".aac") if (place / f"{stem}{ext}").is_file()),
                        None)                    # «.aac»: a voice-over's sound alone (``core/cvh_sound``)
            if path is not None:
                episodes.setdefault(stem, []).append((self.title(remote), path))
        done = 0
        lenders = read_pictures(self.folder)
        for n, (stem, files) in enumerate(sorted(episodes.items(), key=lambda e: video_library.natural_key(e[0])), 1):
            if stop():
                say("Сборка остановлена — остальные серии соберутся со следующего «Скачать»")
                break
            names = ", ".join(name for name, _path in files)
            say(f"Сборка {n}/{len(episodes)}: {stem} — {names}" if len(episodes) > 1 else f"Сборка: {stem} — {names}")
            from core.video_download import _set_meter
            _set_meter(merging=True, index=n, count=len(episodes), name=stem, file=-1, done=0, total=0, speed=0)
            prefer = None                        # the voice-over chosen for the episode's picture (its file)
            chosen = getattr(self, "pictures", {}).get(stem)
            if chosen is not None:
                place = self.folder / chosen.group if chosen.group else self.folder
                prefer = next((place / f"{stem}{ext}" for ext in (".mp4", ".ts") if (place / f"{stem}{ext}").is_file()), None)
            # (v5.44.150) a picture lent by a release whose voice-over was not asked for (``.lb/picture.json``: kept
            # between the runs — a download stopped half-way goes on the same): its file gives the picture alone
            lender = lenders.get(stem)
            lent = None
            if lender is not None and lender["state"] == "wanted":
                place = self.folder / lender["group"]
                lent = next((place / f"{stem}{ext}" for ext in (".mp4", ".ts") if (place / f"{stem}{ext}").is_file()), None)
                if lent is not None and any(dub_key(title_of(lender["group"])) == dub_key(name)
                                            for name in self.tracks(self.folder / f"{stem}.mkv")):
                    lent = None                  # its voice-over is in the file already: a voice-over as any other
                    prefer = prefer or next((path for _name, path in files if path.parent == place), None)
                elif lent is not None:
                    prefer = lent
            from core import video_skips
            before = video_skips.stamp_of(self.folder / f"{stem}.mkv")
            try:
                result = merge_episode(self.folder, stem, files, say=say, stop=stop, prefer=prefer,
                                       lent=lent is not None)
            except MergeError as exc:
                say(f"{stem}: не собрана — {exc}; файлы озвучек остались как были")
                continue
            except OSError as exc:               # Windows: the file is open in a player; the disk
                say(f"{stem}: не собрана — {exc} (если серия открыта в плеере, закройте её); файлы озвучек остались")
                continue
            if lender is not None:
                self._lent_over(stem, lender, lent, result, say)
            if result["merged"] or result.get("repictured"):
                done += 1
                count = len(self.tracks(Path(result["file"])))
                if result.get("repictured"):
                    whose = title_of(lender["group"]) if lent is not None and not result.get("refused") else \
                        self.title(chosen) if chosen is not None else ""
                    say(f"{stem}: картинка общего файла теперь — от «{whose}»" if whose
                        else f"{stem}: картинка общего файла заменена")
                elif lent is not None and not result.get("refused"):
                    say(f"{stem}: картинка — от «{title_of(lender['group'])}», её звук в файл не идёт")
                say(f"{stem}: в общем файле теперь озвучек: {count}")
                try:                             # YummyAnime's times of its picture's file — the new file's
                    video_skips.carry(self.folder, [result.get("picture") or ""], Path(result["file"]))
                    if not result.get("repictured") and before:      # the same picture: what was found of it stays
                        video_skips.kept_after_merge(self.folder, Path(result["file"]), before)
                except (OSError, ValueError) as exc:
                    _LOG.info("skips of %s not carried: %s", result["file"], exc)
        video_library.LIBRARY.invalidate()
        return {"episodes": done}
