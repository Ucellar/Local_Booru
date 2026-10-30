"""How long a video is and its picture size, read from the file's own headers (no decoding, no other program).

The gallery leaves videos longer than «Настройки → Вкладка «Видео»» out and shows them on the tab «Видео»: the
length of every video card is kept in ``images.duration`` (seconds; 0 — not looked at yet, -1 — could not tell).
It is read at import (``core/database/storage._ensure_image``) and, for cards from before, by a background duty
(``backfill`` from ``core/background_duties``).  The picture size of a video card (``images.width/height``, 0 before)
comes from the same reading (``core.media_utils.image_size``).

Containers read here: MP4 / MOV / M4V / 3GP (``mvhd``, fragmented files — their fragments), Matroska / WebM
(``Info → Duration``; a file without it, as a browser's recording, — the time of its last cluster), AVI (``strh`` of
the video stream, ``avih``), FLV (``onMetaData``), ASF / WMV (File Properties).  Anything else, or a file these
readers cannot tell — OpenCV, when installed (frames / fps).  Reading takes a few small pieces of the file: the
beginning, the ``moov`` box, the end of a Matroska file.
"""
from __future__ import annotations

import logging
import os
import struct
import threading
from dataclasses import dataclass
from pathlib import Path

_LOG = logging.getLogger("local_booru.video")

VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".3gp", ".3g2", ".mkv", ".webm", ".avi", ".flv", ".wmv", ".asf",
              ".ts", ".m2ts", ".mts", ".mpg", ".mpeg", ".ogv"}
MAX_MOOV = 96 * 1024 * 1024          # a moov box bigger than this is not read into memory
MKV_TAIL = 4 * 1024 * 1024           # the end of a Matroska file looked at for its last cluster
UNKNOWN = -1.0                        # images.duration of a video whose length could not be told


@dataclass(frozen=True)
class VideoInfo:
    duration: float = 0.0             # seconds, 0 — not told by the file
    width: int = 0
    height: int = 0


class _Bad(Exception):
    """The file is not what its header says (cut short, another format)."""


# ── public ─────────────────────────────────────────────────────────────────────
_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 512


def probe(path) -> VideoInfo | None:
    """The video's length and size, or None when the file cannot be read at all.  A result is kept for the same
    file (path, size, time) — the import asks for the size and the length one after the other."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (os.path.normcase(str(p)), st.st_size, st.st_mtime_ns)
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]
    info = _probe(p)
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = info
    return info


def duration_for_db(path) -> float:
    """``images.duration`` of a video file: seconds, or -1 when its length could not be told."""
    info = probe(path)
    return round(info.duration, 3) if info is not None and info.duration > 0 else UNKNOWN


def is_video_name(name) -> bool:
    return Path(str(name)).suffix.lower() in VIDEO_EXTS


def human_duration(seconds) -> str:
    """12:34 or 1:02:03."""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


# ── which reader ────────────────────────────────────────────────────────────────
def _probe(p: Path) -> VideoInfo | None:
    try:
        with open(p, "rb") as fh:
            head = fh.read(64)
            size = os.fstat(fh.fileno()).st_size
            reader = _reader_for(head, p.suffix.lower())
            info = None
            if reader is not None:
                try:
                    info = reader(fh, size)
                except (_Bad, struct.error, ValueError, OverflowError) as exc:
                    _LOG.debug("video %s: header not read (%s)", p.name, exc)
                    info = None
    except OSError as exc:
        _LOG.debug("video %s not readable: %s", p, exc)
        return None
    if info is None or info.duration <= 0 or info.width <= 0:
        more = _opencv(p)
        if more is not None:
            info = VideoInfo(duration=info.duration if info and info.duration > 0 else more.duration,
                             width=info.width if info and info.width > 0 else more.width,
                             height=info.height if info and info.height > 0 else more.height)
    return info if info is not None else VideoInfo()


def _reader_for(head: bytes, ext: str):
    if len(head) >= 8 and head[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pnot", b"uuid"):
        return _mp4
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return _matroska
    if head[:4] == b"RIFF" and head[8:12] in (b"AVI ", b"AVIX"):
        return _avi
    if head[:3] == b"FLV":
        return _flv
    if head[:16] == _ASF_HEADER:
        return _asf
    if ext in {".mp4", ".m4v", ".mov", ".3gp", ".3g2"}:
        return _mp4
    return None


def _opencv(p: Path) -> VideoInfo | None:
    """OpenCV's count of frames and their rate (the program has it for video thumbnails)."""
    try:
        import cv2
    except ImportError:
        return None
    cap = None
    try:
        cap = cv2.VideoCapture(str(p))
        if not cap.isOpened():
            return None
        frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = frames / fps if frames > 0 and 0 < fps < 1000 else 0.0
        return VideoInfo(duration=duration if duration < 10 ** 6 else 0.0, width=max(0, width), height=max(0, height))
    except Exception as exc:  # OpenCV raises its own error types for files it cannot open
        _LOG.debug("video %s: OpenCV could not tell its length (%s)", p.name, exc)
        return None
    finally:
        if cap is not None:
            cap.release()


# ── MP4 / MOV ────────────────────────────────────────────────────────────────────
def _boxes(data: bytes, start: int = 0, end: int | None = None):
    """(type, payload start, payload end) of the boxes in ``data[start:end]``."""
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def _top_boxes(fh, file_size: int):
    """(type, payload start, payload end) of the boxes at the top of the file, read header by header."""
    pos = 0
    while pos + 8 <= file_size:
        fh.seek(pos)
        head = fh.read(16)
        if len(head) < 8:
            return
        size, kind = struct.unpack_from(">I4s", head, 0)
        header = 8
        if size == 1:
            if len(head) < 16:
                return
            size = struct.unpack_from(">Q", head, 8)[0]
            header = 16
        elif size == 0:
            size = file_size - pos
        if size < header:
            return
        yield kind, pos + header, min(file_size, pos + size)
        pos += size


def _find(data: bytes, start: int, end: int, kind: bytes):
    for k, s, e in _boxes(data, start, end):
        if k == kind:
            return s, e
    return None


def _mp4(fh, file_size: int) -> VideoInfo:
    moov = None
    moofs = []
    for kind, start, end in _top_boxes(fh, file_size):
        if kind == b"moov" and moov is None:
            if end - start > MAX_MOOV:
                raise _Bad("moov too large")
            fh.seek(start)
            moov = fh.read(end - start)
        elif kind == b"moof":
            moofs.append((start, end))
    if moov is None:
        raise _Bad("no moov")
    timescale, duration = 0, 0
    width = height = 0
    tracks = {}                    # track id -> (timescale, is video)
    fragment_duration = 0
    for kind, start, end in _boxes(moov):
        if kind == b"mvhd":
            version = moov[start]
            if version == 1:
                timescale, duration = struct.unpack_from(">IQ", moov, start + 20)
            else:
                timescale, duration = struct.unpack_from(">II", moov, start + 12)
                if duration == 0xFFFFFFFF:
                    duration = 0
        elif kind == b"trak":
            track_id, track_scale, is_video, w, h = _mp4_track(moov, start, end)
            tracks[track_id] = (track_scale, is_video)
            if is_video and not width and w > 0 and h > 0:
                width, height = w, h
        elif kind == b"mvex":
            found = _find(moov, start, end, b"mehd")
            if found:
                s, _e = found
                fragment_duration = (struct.unpack_from(">Q", moov, s + 4)[0] if moov[s] == 1
                                     else struct.unpack_from(">I", moov, s + 4)[0])
    seconds = duration / timescale if timescale and duration else 0.0
    if seconds <= 0 and fragment_duration and timescale:
        seconds = fragment_duration / timescale
    if seconds <= 0 and moofs:
        seconds = _fragments_end(fh, moofs, tracks)
    return VideoInfo(duration=max(0.0, seconds), width=width, height=height)


def _mp4_track(moov: bytes, start: int, end: int) -> tuple[int, int, bool, int, int]:
    """(track id, its timescale, whether it is the picture, width, height) of a ``trak``."""
    track_id, scale, is_video, width, height = 0, 0, False, 0, 0
    tkhd = _find(moov, start, end, b"tkhd")
    if tkhd:
        s, _e = tkhd
        version = moov[s]
        track_id = struct.unpack_from(">I", moov, s + (20 if version == 1 else 12))[0]
        tail = s + (88 if version == 1 else 76)            # after the matrix: width and height (16.16)
        if tail + 8 <= len(moov):
            a, b, _u, c, d = struct.unpack_from(">iiiii", moov, tail - 36)
            width = struct.unpack_from(">I", moov, tail)[0] >> 16
            height = struct.unpack_from(">I", moov, tail + 4)[0] >> 16
            if a == 0 and d == 0 and abs(b) == 0x10000 and abs(c) == 0x10000:
                width, height = height, width                # turned a quarter: shown the other way round
    mdia = _find(moov, start, end, b"mdia")
    if mdia:
        ms, me = mdia
        hdlr = _find(moov, ms, me, b"hdlr")
        if hdlr:
            is_video = moov[hdlr[0] + 8:hdlr[0] + 12] == b"vide"
        mdhd = _find(moov, ms, me, b"mdhd")
        if mdhd:
            s, _e = mdhd
            scale = struct.unpack_from(">I", moov, s + (20 if moov[s] == 1 else 12))[0]
        if is_video and (not width or not height):
            width, height = _stsd_size(moov, ms, me) or (width, height)
    return track_id, scale, is_video, width, height


def _stsd_size(moov: bytes, start: int, end: int):
    minf = _find(moov, start, end, b"minf")
    stbl = _find(moov, *minf, b"stbl") if minf else None
    stsd = _find(moov, *stbl, b"stsd") if stbl else None
    if not stsd:
        return None
    entry = stsd[0] + 8                       # version, flags, entry count; then the first sample entry
    if entry + 36 > stsd[1]:
        return None
    width, height = struct.unpack_from(">HH", moov, entry + 8 + 24)
    return (width, height) if width and height else None


def _fragments_end(fh, moofs: list, tracks: dict) -> float:
    """A fragmented MP4 whose moov does not say its length: where its last fragment of the picture ends."""
    best = 0.0
    for start, end in moofs:
        if end - start > 16 * 1024 * 1024:
            continue
        fh.seek(start)
        data = fh.read(end - start)
        for kind, s, e in _boxes(data):
            if kind != b"traf":
                continue
            track_id, default_duration, base, total = 0, 0, 0, 0
            for k, bs, be in _boxes(data, s, e):
                if k == b"tfhd":
                    flags = int.from_bytes(data[bs + 1:bs + 4], "big")
                    track_id = struct.unpack_from(">I", data, bs + 4)[0]
                    pos = bs + 8 + (8 if flags & 0x01 else 0) + (4 if flags & 0x02 else 0)
                    if flags & 0x08:
                        default_duration = struct.unpack_from(">I", data, pos)[0]
                elif k == b"tfdt":
                    base = (struct.unpack_from(">Q", data, bs + 4)[0] if data[bs] == 1
                            else struct.unpack_from(">I", data, bs + 4)[0])
                elif k == b"trun":
                    total += _trun_duration(data, bs, be, default_duration)
            scale, is_video = tracks.get(track_id, (0, False))
            if scale and (is_video or not any(v for _s, v in tracks.values())):
                best = max(best, (base + total) / scale)
    return best


def _trun_duration(data: bytes, start: int, end: int, default_duration: int) -> int:
    flags = int.from_bytes(data[start + 1:start + 4], "big")
    count = struct.unpack_from(">I", data, start + 4)[0]
    pos = start + 8 + (4 if flags & 0x01 else 0) + (4 if flags & 0x04 else 0)
    if not flags & 0x100:
        return count * default_duration
    step = 4 * sum(1 for bit in (0x100, 0x200, 0x400, 0x800) if flags & bit)
    total = 0
    for _ in range(count):
        if pos + 4 > end:
            break
        total += struct.unpack_from(">I", data, pos)[0]
        pos += step
    return total


# ── Matroska / WebM ─────────────────────────────────────────────────────────────
_SEGMENT, _CLUSTER, _INFO, _TRACKS, _SEEKHEAD = 0x18538067, 0x1F43B675, 0x1549A966, 0x1654AE6B, 0x114D9B74


def _vint(data: bytes, pos: int, keep_marker: bool):
    """(value, length) of an EBML number at ``pos``; the value is None for an «unknown» size."""
    if pos >= len(data):
        raise _Bad("cut short")
    first = data[pos]
    length = 1
    mask = 0x80
    while length <= 8 and not first & mask:
        mask >>= 1
        length += 1
    if length > 8 or pos + length > len(data):
        raise _Bad("bad number")
    value = first if keep_marker else first & (mask - 1)
    for b in data[pos + 1:pos + length]:
        value = (value << 8) | b
    if not keep_marker and value == (1 << (7 * length)) - 1:
        return None, length
    return value, length


def _elements(data: bytes, start: int, end: int):
    """(id, payload start, payload end) of the EBML elements in ``data[start:end]``; an «unknown» size runs to
    ``end``."""
    pos = start
    while pos < end:
        try:
            ident, n = _vint(data, pos, True)
            size, m = _vint(data, pos + n, False)
        except _Bad:
            return
        body = pos + n + m
        stop = end if size is None else min(end, body + size)
        yield ident, body, stop
        if size is None:
            return
        pos = body + size


def _uint(data: bytes, start: int, end: int) -> int:
    return int.from_bytes(data[start:end], "big") if end > start else 0


def _float(data: bytes, start: int, end: int) -> float:
    if end - start == 4:
        return struct.unpack_from(">f", data, start)[0]
    if end - start == 8:
        return struct.unpack_from(">d", data, start)[0]
    return 0.0


def _matroska(fh, file_size: int) -> VideoInfo:
    fh.seek(0)
    data = fh.read(min(file_size, 2 * 1024 * 1024))
    segment = None
    for ident, start, end in _elements(data, 0, len(data)):
        if ident == _SEGMENT:
            segment = start
            break
    if segment is None:
        raise _Bad("no segment")
    info_at, tracks_at = None, None
    found_info = found_tracks = None
    for ident, start, end in _elements(data, segment, len(data)):
        if ident == _INFO:
            found_info = (start, end)
        elif ident == _TRACKS:
            found_tracks = (start, end)
        elif ident == _SEEKHEAD:
            info_at, tracks_at = _seek_positions(data, start, end, segment)
        elif ident == _CLUSTER:
            break
    scale, duration = 1_000_000, 0.0
    width = height = 0
    info = _element_data(fh, data, found_info, info_at, _INFO)
    if info is not None:
        block, start, end = info
        for ident, s, e in _elements(block, start, end):
            if ident == 0x2AD7B1:
                scale = _uint(block, s, e) or 1_000_000
            elif ident == 0x4489:
                duration = _float(block, s, e)
    tracks = _element_data(fh, data, found_tracks, tracks_at, _TRACKS)
    if tracks is not None:
        width, height = _matroska_size(*tracks)
    seconds = duration * scale / 1e9 if duration > 0 else 0.0
    if seconds <= 0:
        seconds = _matroska_tail(fh, file_size, scale)
    return VideoInfo(duration=max(0.0, seconds), width=width, height=height)


def _seek_positions(data: bytes, start: int, end: int, segment: int):
    info_at = tracks_at = None
    for ident, s, e in _elements(data, start, end):
        if ident != 0x4DBB:
            continue
        target, position = 0, None
        for k, ks, ke in _elements(data, s, e):
            if k == 0x53AB:
                target = _uint(data, ks, ke)
            elif k == 0x53AC:
                position = _uint(data, ks, ke)
        if position is None:
            continue
        if target == _INFO:
            info_at = segment + position
        elif target == _TRACKS:
            tracks_at = segment + position
    return info_at, tracks_at


def _element_data(fh, data: bytes, found, at, ident: int):
    """(bytes, payload start, payload end) of an element found in the first piece, or read where the seek head
    points."""
    if found is not None:
        return data, found[0], found[1]
    if at is None:
        return None
    fh.seek(at)
    head = fh.read(16)
    try:
        got, n = _vint(head, 0, True)
        size, m = _vint(head, n, False)
    except _Bad:
        return None
    if got != ident or size is None or size > 16 * 1024 * 1024:
        return None
    fh.seek(at + n + m)
    block = fh.read(size)
    return block, 0, len(block)


def _matroska_size(data: bytes, start: int, end: int) -> tuple[int, int]:
    for ident, s, e in _elements(data, start, end):
        if ident != 0xAE:                            # TrackEntry
            continue
        kind, width, height, shown_w, shown_h, unit = 0, 0, 0, 0, 0, 0
        for k, ks, ke in _elements(data, s, e):
            if k == 0x83:
                kind = _uint(data, ks, ke)
            elif k == 0xE0:
                for v, vs, ve in _elements(data, ks, ke):
                    if v == 0xB0:
                        width = _uint(data, vs, ve)
                    elif v == 0xBA:
                        height = _uint(data, vs, ve)
                    elif v == 0x54B0:
                        shown_w = _uint(data, vs, ve)
                    elif v == 0x54BA:
                        shown_h = _uint(data, vs, ve)
                    elif v == 0x54B2:
                        unit = _uint(data, vs, ve)
        if kind == 1 and width and height:
            if shown_w and shown_h and unit == 0:
                return shown_w, shown_h
            if shown_w and shown_h and unit == 3:            # an aspect ratio, not pixels
                return int(round(height * shown_w / shown_h)), height
            return width, height
    return 0, 0


def _matroska_tail(fh, file_size: int, scale: int) -> float:
    """A file without Duration (a recording a browser made, a file still being written): the time of its last
    cluster and the last block in it."""
    start = max(0, file_size - MKV_TAIL)
    fh.seek(start)
    tail = fh.read(file_size - start)
    pos = tail.rfind(b"\x1f\x43\xb6\x75")
    tries = 0
    while pos >= 0 and tries < 64:
        tries += 1
        found = _cluster_end(tail, pos)
        if found is not None:
            seconds = found * scale / 1e9
            if 0 < seconds < 7 * 24 * 3600:
                return seconds
        pos = tail.rfind(b"\x1f\x43\xb6\x75", 0, pos)
    return 0.0


def _cluster_end(tail: bytes, pos: int):
    """The time of the last block of the cluster at ``pos`` (in TimecodeScale units), or None when these bytes are
    not a cluster (the same four bytes can be inside a frame): a cluster starts with its Timecode."""
    try:
        size, m = _vint(tail, pos + 4, False)
        body = pos + 4 + m
        first, n = _vint(tail, body, True)
        length, k = _vint(tail, body + n, False)
    except _Bad:
        return None
    if first != 0xE7 or length is None or not 1 <= length <= 8:
        return None
    stop = len(tail) if size is None else min(len(tail), body + size)
    timecode = _uint(tail, body + n + k, body + n + k + length)
    last_block = 0
    for ident, s, e in _elements(tail, body + n + k + length, stop):
        if ident in (0xA3, 0xA1):                              # SimpleBlock / Block: track, then int16 time
            last_block = max(last_block, _block_time(tail, s, e))
        elif ident == 0xA0:                                    # BlockGroup
            for b, bs, be in _elements(tail, s, e):
                if b == 0xA1:
                    last_block = max(last_block, _block_time(tail, bs, be))
    return timecode + last_block


def _block_time(data: bytes, start: int, end: int) -> int:
    try:
        _track, n = _vint(data, start, False)
    except _Bad:
        return 0
    if start + n + 2 > end:
        return 0
    return max(0, struct.unpack_from(">h", data, start + n)[0])


# ── AVI ──────────────────────────────────────────────────────────────────────
def _avi(fh, file_size: int) -> VideoInfo:
    fh.seek(12)
    data = fh.read(min(file_size - 12, 1024 * 1024))
    avih = None
    stream = None                       # (scale, rate, length) of the first video stream
    total_frames = 0
    width = height = 0

    def walk(start: int, end: int, in_strl: bool = False) -> None:
        nonlocal avih, stream, total_frames
        pos = start
        while pos + 8 <= end:
            chunk, size = struct.unpack_from("<4sI", data, pos)
            body = pos + 8
            if chunk == b"LIST":
                list_type = data[body:body + 4]
                if list_type in (b"hdrl", b"strl", b"odml"):
                    walk(body + 4, min(end, body + size), list_type == b"strl")
                elif list_type == b"movi":
                    return
            elif chunk == b"avih" and body + 40 <= len(data):
                avih = struct.unpack_from("<10I", data, body)
            elif chunk == b"strh" and in_strl and body + 36 <= len(data):
                scale, rate = struct.unpack_from("<II", data, body + 20)
                length = struct.unpack_from("<I", data, body + 32)[0]
                if data[body:body + 4] == b"vids" and stream is None:
                    stream = (scale, rate, length)
            elif chunk == b"dmlh" and body + 4 <= len(data):
                total_frames = struct.unpack_from("<I", data, body)[0]
            pos = body + size + (size & 1)
    walk(0, len(data))
    seconds = 0.0
    if avih is not None:
        micro, frames = avih[0], avih[4]
        width, height = avih[8], avih[9]
        frames = max(frames, total_frames)
        seconds = frames * micro / 1e6 if micro and frames else 0.0
    if stream is not None:
        scale, rate, length = stream
        if rate and scale and length:
            from_stream = length * scale / rate
            if total_frames and avih is not None and avih[0]:
                from_stream = max(from_stream, total_frames * avih[0] / 1e6)
            seconds = from_stream
    if avih is None and stream is None:
        raise _Bad("no avih")
    return VideoInfo(duration=max(0.0, seconds), width=width, height=height)


# ── FLV ──────────────────────────────────────────────────────────────────────
def _flv(fh, file_size: int) -> VideoInfo:
    fh.seek(0)
    data = fh.read(min(file_size, 256 * 1024))
    offset = struct.unpack_from(">I", data, 5)[0]
    pos = offset + 4
    while pos + 11 <= len(data):
        tag_type = data[pos] & 0x1F
        size = int.from_bytes(data[pos + 1:pos + 4], "big")
        body = pos + 11
        if tag_type == 18:
            values = _amf_meta(data, body, min(len(data), body + size))
            return VideoInfo(duration=float(values.get("duration") or 0.0), width=int(values.get("width") or 0),
                             height=int(values.get("height") or 0))
        pos = body + size + 4
    raise _Bad("no onMetaData")


def _amf_meta(data: bytes, pos: int, end: int) -> dict:
    """The numbers of an AMF0 «onMetaData» (the rest is skipped)."""
    if data[pos] != 0x02:
        return {}
    n = struct.unpack_from(">H", data, pos + 1)[0]
    pos += 3 + n
    if pos >= end or data[pos] not in (0x03, 0x08):
        return {}
    pos += 5 if data[pos] == 0x08 else 1
    out = {}
    while pos + 3 <= end:
        n = struct.unpack_from(">H", data, pos)[0]
        key = data[pos + 2:pos + 2 + n].decode("utf-8", "replace")
        pos += 2 + n
        if pos >= end or (n == 0 and data[pos] == 0x09):
            break
        kind = data[pos]
        if kind == 0x00:
            out[key] = struct.unpack_from(">d", data, pos + 1)[0]
        pos = _amf_skip(data, pos, end)
        if pos < 0:
            break
    return out


def _amf_skip(data: bytes, pos: int, end: int) -> int:
    kind = data[pos]
    pos += 1
    if kind == 0x00:
        return pos + 8
    if kind == 0x01:
        return pos + 1
    if kind == 0x02:
        return pos + 2 + struct.unpack_from(">H", data, pos)[0]
    if kind == 0x0C:
        return pos + 4 + struct.unpack_from(">I", data, pos)[0]
    if kind == 0x0B:
        return pos + 10
    if kind in (0x05, 0x06):
        return pos
    if kind in (0x03, 0x08):
        if kind == 0x08:
            pos += 4
        while pos + 3 <= end:
            n = struct.unpack_from(">H", data, pos)[0]
            pos += 2 + n
            if n == 0 and pos < end and data[pos] == 0x09:
                return pos + 1
            pos = _amf_skip(data, pos, end)
            if pos < 0:
                return -1
        return -1
    if kind == 0x0A:
        count = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        for _ in range(min(count, 100000)):
            pos = _amf_skip(data, pos, end)
            if pos < 0 or pos > end:
                return -1
        return pos
    return -1


# ── ASF / WMV ──────────────────────────────────────────────────────────────────
_ASF_HEADER = bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")
_ASF_FILE_PROPERTIES = bytes.fromhex("a1dcab8c47a9cf118ee400c00c205365")
_ASF_STREAM_PROPERTIES = bytes.fromhex("9107dcb7b7a9cf118ee600c00c205365")
_ASF_VIDEO = bytes.fromhex("c0ef19bc4d5bcf11a8fd00805f5c442b")


def _asf(fh, file_size: int) -> VideoInfo:
    fh.seek(0)
    head = fh.read(30)
    header_size = struct.unpack_from("<Q", head, 16)[0]
    if header_size > 32 * 1024 * 1024:
        raise _Bad("asf header too large")
    fh.seek(0)
    data = fh.read(header_size)
    pos = 30
    seconds, width, height = 0.0, 0, 0
    while pos + 24 <= len(data):
        guid = data[pos:pos + 16]
        size = struct.unpack_from("<Q", data, pos + 16)[0]
        if size < 24:
            break
        if guid == _ASF_FILE_PROPERTIES and pos + 88 <= len(data):
            play, _send, preroll = struct.unpack_from("<QQQ", data, pos + 64)
            seconds = max(0.0, play / 1e7 - preroll / 1000.0)
        elif guid == _ASF_STREAM_PROPERTIES and data[pos + 24:pos + 40] == _ASF_VIDEO and pos + 86 <= len(data):
            width, height = struct.unpack_from("<II", data, pos + 78)
        pos += size
    return VideoInfo(duration=seconds, width=width, height=height)


# ── the background duty ───────────────────────────────────────────────────────────
_VIDEO_SQL = ("(is_video=1 OR " + " OR ".join(f"LOWER(path) LIKE '%{ext}'" for ext in sorted(VIDEO_EXTS)) + ")")


def waiting(settings: dict) -> int:
    """Video cards whose length is not known yet."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        return int(con.execute(f"SELECT COUNT(*) FROM images WHERE deleted=0 AND COALESCE(duration,0)=0 AND {_VIDEO_SQL}")
                   .fetchone()[0])


def backfill(settings: dict, *, stop_check=None, budget: float = 15.0, batch: int = 40) -> dict:
    """The length (and the picture size, when the card has none) of video cards from before: as many as ``budget``
    seconds allow, written ``batch`` at a time.  {"done": n, "more": bool}."""
    import time
    from core.database.connection import db
    started = time.monotonic()
    done = 0
    while True:
        with db(settings, readonly=True) as con:
            rows = con.execute(f"SELECT id, path, width, height FROM images WHERE deleted=0 AND COALESCE(duration,0)=0 "
                               f"AND {_VIDEO_SQL} ORDER BY id DESC LIMIT ?", (int(batch),)).fetchall()
        if not rows:
            return {"done": done, "more": False}
        found = []
        for row in rows:
            if stop_check is not None and stop_check():
                break
            info = probe(str(row["path"]))
            duration = round(info.duration, 3) if info is not None and info.duration > 0 else UNKNOWN
            width = info.width if info is not None else 0
            height = info.height if info is not None else 0
            found.append((duration, width, width, height, height, int(row["id"]), str(row["path"])))
        if found:
            with db(settings, write=True) as con:
                con.executemany("UPDATE images SET duration=?, width=CASE WHEN width>0 OR ?<=0 THEN width ELSE ? END, "
                                "height=CASE WHEN height>0 OR ?<=0 THEN height ELSE ? END WHERE id=? AND path=?", found)
            done += len(found)
        if stop_check is not None and stop_check():
            return {"done": done, "more": True}
        if time.monotonic() - started >= budget:
            return {"done": done, "more": True}
