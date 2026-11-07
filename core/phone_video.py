"""(v5.44.68) An episode for the phone's browser (``webui/pages/phone_videos.py``).

A phone's browser plays an MP4 or a WebM with one sound track; the episodes of the video folder are often MKV with
several voice-overs (``core/video_merge``), sometimes AC3, DTS or FLAC sound, an MPEG-TS.  Such an episode is made
into an MP4 for the phone once (:func:`remux`, PyAV — the program's own FFmpeg): the picture copied as it is (no
re-encoding: seconds, not minutes), the voice-over chosen — copied when the phone plays it (AAC, MP3, Opus), else
made AAC; the result kept in the program's cache (``cache/phone_video``, :data:`CACHE_LIMIT` at most, the oldest
leave first) and served with Range, so the phone's player seeks anywhere.  One episode is made at a time
(:class:`Jobs`); the next one of a series is asked for while the current one plays.

Subtitles inside the file (ASS, SRT) are given to the phone as WebVTT (:func:`subtitles_vtt`), kept in the same cache.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

_LOG = logging.getLogger("local_booru.phone_video")

PHONE_VIDEO = {"h264", "hevc", "av1", "vp9", "vp8"}       # pictures a phone's browser shows (copied as they are)
COPY_AUDIO = {"aac", "mp3", "opus"}                       # sound a phone's browser plays in an MP4 (copied)
DIRECT_EXTS = {".mp4", ".m4v", ".webm"}
DIRECT_AUDIO = COPY_AUDIO | {"vorbis"}
CACHE_LIMIT = 6 * 1024 ** 3                               # bytes of episodes made for the phone kept at most
AAC_RATE = 48000
AAC_BITS = 160_000
VERSION = "1"                                             # in the cache's names: a change of the making — made anew


class VideoError(Exception):
    """The episode could not be made for the phone (said on its page)."""


@dataclass
class Track:
    index: int                      # among the file's tracks of its kind
    codec: str
    title: str = ""
    language: str = ""

    def public(self) -> dict:
        return {"index": self.index, "codec": self.codec, "title": self.title, "language": self.language}


@dataclass
class Media:
    path: str
    format: str = ""                # FFmpeg's name of the container: "matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2" …
    video: str = ""                 # the picture's codec
    audio: list = field(default_factory=list)          # Track
    subtitles: list = field(default_factory=list)      # Track (text ones: ass, ssa, subrip, webvtt, mov_text)
    duration: float = 0.0

    @property
    def direct(self) -> bool:
        """The phone's browser plays the file as it is: an MP4 or a WebM, a picture it shows, at most one sound
        track and that one it plays."""
        return (Path(self.path).suffix.lower() in DIRECT_EXTS and self.video in PHONE_VIDEO and len(self.audio) <= 1
                and all(t.codec in DIRECT_AUDIO for t in self.audio))

    def track_titles(self) -> list:
        from core import video_tracks
        return [video_tracks.track_name(t.title, t.language, n + 1) for n, t in enumerate(self.audio)]


TEXT_SUBS = {"ass", "ssa", "subrip", "srt", "webvtt", "mov_text", "text"}
_INSPECTED: dict = {}
_INSPECTED_LOCK = threading.Lock()


def available() -> bool:
    try:
        import av  # noqa: F401
    except ImportError:
        return False
    return True


def _stamp(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return ""
    return f"{st.st_size}-{st.st_mtime_ns}"


def inspect(path) -> Media:
    """What is in the file (kept while the file is the same)."""
    path = Path(path)
    stamp = _stamp(path)
    if not stamp:
        raise VideoError("Файла серии нет на диске")
    key = str(path)
    with _INSPECTED_LOCK:
        known = _INSPECTED.get(key)
        if known and known[0] == stamp:
            return known[1]
    import av
    try:
        with av.open(str(path)) as box:
            media = Media(path=str(path), format=str(box.format.name or ""))
            if box.streams.video:
                media.video = str(box.streams.video[0].codec_context.name or "")
            for n, stream in enumerate(box.streams.audio):
                media.audio.append(Track(n, str(stream.codec_context.name or ""), _meta(stream, "title"),
                                         _meta(stream, "language")))
            for n, stream in enumerate(box.streams.subtitles):
                codec = str(stream.codec_context.name or "")
                if codec in TEXT_SUBS:
                    media.subtitles.append(Track(n, codec, _meta(stream, "title"), _meta(stream, "language")))
            if box.duration:
                media.duration = float(box.duration) / 1_000_000
            elif box.streams.video and box.streams.video[0].duration:
                video = box.streams.video[0]
                media.duration = float(video.duration * video.time_base)
    except (av.FFmpegError, OSError) as exc:
        raise VideoError(f"Файл серии не читается: {exc}") from exc
    if not media.video:
        raise VideoError("В файле нет картинки")
    with _INSPECTED_LOCK:
        if len(_INSPECTED) > 256:
            _INSPECTED.clear()
        _INSPECTED[key] = (stamp, media)
    return media


def _meta(stream, key: str) -> str:
    data = stream.metadata or {}
    return str(data.get(key) or data.get(key.upper()) or "").strip()[:120]


# ── the cache ────────────────────────────────────────────────────────────────────────────────────────────────────
def cache_dir() -> Path:
    from core.paths import CACHE_DIR
    folder = Path(CACHE_DIR) / "phone_video"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _key(path: Path, what: str) -> str:
    raw = f"{VERSION}|{path}|{_stamp(path)}|{what}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:24]


def made_path(path, audio: int) -> Path:
    """Where the phone's MP4 of this file with this voice-over is (or will be)."""
    return cache_dir() / f"{_key(Path(path), f'a{int(audio)}')}.mp4"


def ready(path, audio: int) -> Path | None:
    target = made_path(path, audio)
    if target.is_file():
        try:
            os.utime(target)                         # looked at: the last to leave the cache
        except OSError:
            pass
        return target
    return None


def tidy(limit: int = CACHE_LIMIT, keep: Path | None = None) -> None:
    """The cache under ``limit``: the episodes looked at longest ago leave (one being sent just now stays — the
    system does not let it go — and is tried again next time)."""
    folder = cache_dir()
    files = []
    for item in folder.iterdir():
        if item.is_file() and item.suffix in (".mp4", ".vtt"):
            try:
                st = item.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, item))
        elif item.is_file() and item.name.endswith(".part"):
            try:
                if time.time() - item.stat().st_mtime > 6 * 3600:      # left by a run that ended in the middle
                    item.unlink()
            except OSError:
                pass
    total = sum(size for _m, size, _i in files)
    for _mtime, size, item in sorted(files):
        if total <= limit:
            break
        if keep is not None and item == keep:
            continue
        try:
            item.unlink()
            total -= size
        except OSError:
            continue


# ── making the phone's MP4 ───────────────────────────────────────────────────────────────────────────────────────
def remux(path, audio: int, target: Path, *, progress=None, stop=None) -> Path:
    """``target`` (.mp4, moov first): the picture of ``path`` copied, its ``audio``-th sound track copied or made AAC.
    ``progress(part 0…1)``; ``stop()`` true — :class:`VideoError` («остановлено»)."""
    import av
    media = inspect(path)
    if media.video not in PHONE_VIDEO:
        raise VideoError(f"Картинку этого файла ({media.video or 'неизвестно'}) браузер телефона не показывает")
    part = target.with_name(target.name + ".part")
    part.unlink(missing_ok=True)
    length = media.duration or 0.0
    try:
        with av.open(str(path)) as box, av.open(str(part), "w", format="mp4",
                                                options={"movflags": "+faststart"}) as out:
            vin = box.streams.video[0]
            vout = out.add_stream_from_template(vin)
            if media.video == "hevc":
                try:
                    vout.codec_context.codec_tag = "hvc1"     # how Apple's and some Android players want HEVC in MP4
                except (AttributeError, ValueError, TypeError):
                    pass
            ain = box.streams.audio[audio] if 0 <= audio < len(box.streams.audio) else None
            copy = ain is not None and str(ain.codec_context.name) in COPY_AUDIO
            aout = resampler = None
            if ain is not None:
                if copy:
                    aout = out.add_stream_from_template(ain)
                else:
                    aout = out.add_stream("aac", rate=AAC_RATE, layout="stereo")
                    aout.codec_context.bit_rate = AAC_BITS
                    aout.time_base = Fraction(1, AAC_RATE)
                    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=AAC_RATE)
                title = _meta(ain, "title")
                if title:
                    aout.metadata["title"] = title
            origin = float(vin.start_time * vin.time_base) if vin.start_time is not None else 0.0
            streams = [vin] + ([ain] if ain is not None else [])
            shown = -1.0
            for packet in box.demux(*streams):
                if stop is not None and stop():
                    raise VideoError("остановлено")
                if packet.stream is vin:
                    if packet.dts is None and packet.pts is None:
                        continue
                    packet.stream = vout
                    out.mux(packet)
                    if progress is not None and length > 0 and packet.pts is not None:
                        now = min(1.0, max(0.0, (float(packet.pts * packet.time_base) - origin) / length))
                        if now - shown >= 0.01:
                            shown = now
                            progress(now)
                elif copy:
                    if packet.dts is None and packet.pts is None:
                        continue
                    packet.stream = aout
                    out.mux(packet)
                else:
                    for frame in packet.decode():
                        _encode(out, aout, resampler, frame)
            if resampler is not None:
                _encode(out, aout, resampler, None)
                for made in aout.encode(None):
                    out.mux(made)
    except av.FFmpegError as exc:
        part.unlink(missing_ok=True)
        raise VideoError(f"Серия не собралась для телефона: {exc}") from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    os.replace(part, target)
    if progress is not None:
        progress(1.0)
    return target


def _encode(out, aout, resampler, frame) -> None:
    """A decoded sound frame (None — the end) through the resampler into AAC, its time kept (an offset of the sound
    against the picture stays as it was)."""
    when = None
    if frame is not None and frame.pts is not None and frame.time_base:
        when = float(frame.pts * frame.time_base)
    for made in resampler.resample(frame):
        if when is not None:
            made.pts = int(round(when * AAC_RATE))
            made.time_base = Fraction(1, AAC_RATE)
            when += made.samples / AAC_RATE
        for packet in aout.encode(made):
            out.mux(packet)


# ── one at a time ────────────────────────────────────────────────────────────────────────────────────────────────
class Jobs:
    """The episodes being made for the phone: one at a time, in the order asked; the state of each by its target."""

    def __init__(self):
        self._lock = threading.Lock()
        self._states: dict = {}                 # target name -> {"state", "part", "error", "at"}
        self._queue: list = []                  # (target, path, audio)
        self._worker: threading.Thread | None = None

    def state(self, path, audio: int) -> dict:
        target = made_path(path, audio)
        if target.is_file():
            return {"state": "ready", "part": 1.0, "error": ""}
        with self._lock:
            known = self._states.get(target.name)
            return dict(known) if known else {"state": "none", "part": 0.0, "error": ""}

    def ask(self, path, audio: int) -> dict:
        """Make it unless it is made or being made (a failed one is tried again)."""
        target = made_path(path, audio)
        if ready(path, audio) is not None:
            return {"state": "ready", "part": 1.0, "error": ""}
        with self._lock:
            known = self._states.get(target.name)
            if known and known["state"] in ("waiting", "going"):
                return dict(known)
            self._states[target.name] = {"state": "waiting", "part": 0.0, "error": "", "at": time.time()}
            self._queue.append((target, str(path), int(audio)))
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="phone-video", daemon=True)
                self._worker.start()
            return dict(self._states[target.name])

    def _set(self, name: str, **values) -> None:
        with self._lock:
            if name in self._states:
                self._states[name].update(values)

    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._worker = None
                    return
                target, path, audio = self._queue.pop(0)
            if target.is_file():
                self._set(target.name, state="ready", part=1.0)
                continue
            self._set(target.name, state="going")
            started = time.time()
            try:
                tidy(keep=target)
                remux(path, audio, target, progress=lambda part, n=target.name: self._set(n, part=round(part, 3)))
                self._set(target.name, state="ready", part=1.0)
                _LOG.info("phone: %s made for the phone in %.1f s", Path(path).name, time.time() - started)
            except VideoError as exc:
                self._set(target.name, state="failed", error=str(exc))
                _LOG.warning("phone: %s not made for the phone: %s", Path(path).name, exc)
            except Exception as exc:  # noqa: BLE001 - one episode must not stop the others
                self._set(target.name, state="failed", error=f"Серия не собралась для телефона: {exc}")
                _LOG.exception("phone: %s failed", Path(path).name)


JOBS = Jobs()


# ── subtitles inside the file ────────────────────────────────────────────────────────────────────────────────────
_ASS_TAGS = re.compile(r"\{[^}]*\}")


def _cue_text(codec: str, data: bytes) -> str:
    text = data.decode("utf-8", "replace")
    if codec in ("ass", "ssa"):
        # Matroska keeps a line as «ReadOrder,Layer,Style,Name,MarginL,MarginR,MarginV,Effect,Text»; a «Dialogue:» line
        # of an older muxer has one field more in front — the text is after the eighth comma either way
        if text.startswith("Dialogue:"):
            text = text.split(":", 1)[1]
            fields = text.split(",", 9)
            text = fields[9] if len(fields) > 9 else fields[-1]
        else:
            fields = text.split(",", 8)
            text = fields[8] if len(fields) > 8 else fields[-1]
        text = _ASS_TAGS.sub("", text).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    return "\n".join(line.strip() for line in text.replace("\r", "").split("\n") if line.strip())


def subtitles_vtt(path, index: int) -> str:
    """The ``index``-th text subtitle track of the file as WebVTT (kept in the cache)."""
    from core import video_tracks
    path = Path(path)
    media = inspect(path)
    track = next((t for t in media.subtitles if t.index == index), None)
    if track is None:
        raise VideoError("Таких субтитров в файле нет")
    kept = cache_dir() / f"{_key(path, f's{index}')}.vtt"
    if kept.is_file():
        return kept.read_text(encoding="utf-8")
    import av
    cues = []
    try:
        with av.open(str(path)) as box:
            stream = box.streams.subtitles[index]
            for packet in box.demux(stream):
                if packet.pts is None or not packet.size:
                    continue
                start = float(packet.pts * packet.time_base)
                length = float(packet.duration * packet.time_base) if packet.duration else 3.0
                words = _cue_text(track.codec, bytes(packet))
                if words:
                    cues.append((max(0.0, start), max(0.0, start) + max(0.2, length), words))
                if len(cues) >= video_tracks.MAX_CUES:
                    break
    except (av.FFmpegError, OSError) as exc:
        raise VideoError(f"Субтитры файла не читаются: {exc}") from exc
    cues.sort(key=lambda c: c[0])
    text = video_tracks.to_vtt(cues)
    try:
        kept.write_text(text, encoding="utf-8")
    except OSError:
        pass
    return text
