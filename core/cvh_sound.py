"""A voice-over's sound alone from CVH, for the episode's common file (``video_merge``): no picture is downloaded for it.

The owner, 03.11.2026: «там где есть CVH и можно качать звук с него — можно качать с него, тоже экономия; только с
подгонкой могут быть проблемы, подгонка же по кадрам идёт».  CVH gives MP4 files whose ``moov`` (the table of every
sample: its size, its place, its time) is at the very start, so the sound's samples can be asked by ``Range`` alone:
an episode of «Фрирен» in 480p is 223 MB, its sound 25.6 MB (128 kbit/s; 360p and lower have a poorer sound).  The
sound is spread over the file in 7480 small pieces (one every 0.2 s between the picture's) and CVH's hosts answer one
range a request — so they are asked 8 at a time (the owner's computer, 03.11.2026: 82 s for an episode, none refused).

The fitting onto the picture needs the voice-over's frames: its key frames (one every ~3 s) are taken from CVH's
smallest quality (144p: 2.8 MB) — every key frame is matched to the exact frame of the picture's file, so the shift is
exact; only the edge of a studio's own cut (an intro of its own) is known to a key frame or so.  Their prints are kept
as the sound file's (``video_merge.keep(keys=True)``): ``merge_episode`` takes them without a picture to read.

The sound is written as ADTS («Серия NN.aac»: its frames as they are, with ADTS headers) — ``video_merge`` reads ADTS.
Times: ADTS begins at the sound's first sample; the key frames' times are put on that clock (the MP4's edit lists).
"""
from __future__ import annotations

import json
import logging
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field
from pathlib import Path

from core import download_telemetry

_LOG = logging.getLogger("local_booru.video")
WORKERS = 8                    # ranges asked at once (CVH's hosts answer one range a request)
HEAD = 64 * 1024               # the first bytes read: the top boxes, the size of ``moov``
MOOV_MAX = 40 * 1024 * 1024    # a table larger than this is not a CVH file's
TRIES = 3
EXT = ".aac"
NOTE = ".part.cvhs"            # beside the «.part»: which file it is of and how many pieces are in it


class SoundError(Exception):
    """The sound alone cannot be had (the file is not one of these MP4s, CVH does not answer…) — the whole file then."""


@dataclass
class TrackTable:
    kind: str                                   # "vide" | "soun"
    scale: int                                  # its time scale (units a second)
    sizes: list = field(default_factory=list)   # each sample's bytes
    chunks: list = field(default_factory=list)  # each chunk's place in the file
    per_chunk: list = field(default_factory=list)   # samples of each chunk
    deltas: list = field(default_factory=list)  # each sample's duration (its units)
    shifts: list = field(default_factory=list)  # each sample's composition offset (B-frames), [] — none
    sync: list = field(default_factory=list)    # the key samples (1-based), [] — all of them
    begins: int = 0                             # the edit list's media time (units): what plays at 0
    config: bytes = b""                         # avcC (picture) / AudioSpecificConfig (sound)
    codec: str = ""                             # "avc1" | "mp4a" …

    def places(self) -> list:
        """(offset, size, first sample, count) of each chunk."""
        out, sample = [], 0
        for offset, count in zip(self.chunks, self.per_chunk):
            out.append((offset, sum(self.sizes[sample:sample + count]), sample, count))
            sample += count
        return out

    def times(self) -> list:
        """Each sample's presentation time, seconds (the edit list's start taken off)."""
        out, at = [], 0
        for n, delta in enumerate(self.deltas):
            out.append((at + (self.shifts[n] if n < len(self.shifts) else 0) - self.begins) / self.scale)
            at += delta
        return out


# ── the MP4's table ───────────────────────────────────────────────────────────────────
def _boxes(data: bytes, start: int, end: int):
    at = start
    while at + 8 <= end:
        size, kind = struct.unpack(">I4s", data[at:at + 8])
        head = 8
        if size == 1:
            if at + 16 > end:
                return
            size, head = struct.unpack(">Q", data[at + 8:at + 16])[0], 16
        elif size == 0:
            size = end - at
        if size < head or at + size > end:
            return
        yield kind.decode("latin-1"), at + head, at + size
        at += size


def _find(data: bytes, start: int, end: int, path: list):
    for kind, a, b in _boxes(data, start, end):
        if kind == path[0]:
            return (a, b) if len(path) == 1 else _find(data, a, b, path[1:])
    return None


def _full(data: bytes, box) -> tuple[int, int]:
    """(version, where the content after version and flags begins)."""
    return data[box[0]], box[0] + 4


def _u32s(data: bytes, at: int, count: int) -> list:
    return list(struct.unpack(f">{count}I", data[at:at + 4 * count]))


def _descriptor(data: bytes, at: int) -> tuple[int, int, int]:
    """(tag, size, where its content begins) of an MPEG-4 descriptor (esds)."""
    tag = data[at]
    at += 1
    size = 0
    for _n in range(4):
        byte = data[at]
        at += 1
        size = (size << 7) | (byte & 0x7F)
        if not byte & 0x80:
            break
    return tag, size, at


def _audio_config(data: bytes, esds) -> bytes:
    """The AudioSpecificConfig in an ``esds`` box (ES → DecoderConfig → DecoderSpecificInfo)."""
    at, end = esds[0] + 4, esds[1]
    while at < end:
        tag, size, inner = _descriptor(data, at)
        if tag == 0x03:                                  # ES_Descriptor: id (2), flags (1) [+ what they ask]
            flags = data[inner + 2]
            at = inner + 3 + (2 if flags & 0x80 else 0)
            if flags & 0x40:
                at += 1 + data[at]
            if flags & 0x20:
                at += 2
            continue
        if tag == 0x04:                                  # DecoderConfigDescriptor: 13 bytes, then its info
            at = inner + 13
            continue
        if tag == 0x05:
            return bytes(data[inner:inner + size])
        at = inner + size
    return b""


def _track(data: bytes, a: int, b: int) -> TrackTable | None:
    hdlr = _find(data, a, b, ["mdia", "hdlr"])
    mdhd = _find(data, a, b, ["mdia", "mdhd"])
    stbl = _find(data, a, b, ["mdia", "minf", "stbl"])
    if not (hdlr and mdhd and stbl):
        return None
    kind = data[hdlr[0] + 8:hdlr[0] + 12].decode("latin-1")
    version, at = _full(data, mdhd)
    scale = struct.unpack(">I", data[at + (16 if version else 8):at + (20 if version else 12)])[0]
    table = TrackTable(kind=kind, scale=scale)
    stsz = _find(data, *stbl, ["stsz"])
    fixed, count = struct.unpack(">II", data[stsz[0] + 4:stsz[0] + 12])
    table.sizes = [fixed] * count if fixed else _u32s(data, stsz[0] + 12, count)
    stco = _find(data, *stbl, ["stco"])
    if stco is not None:
        n = struct.unpack(">I", data[stco[0] + 4:stco[0] + 8])[0]
        table.chunks = _u32s(data, stco[0] + 8, n)
    else:
        co64 = _find(data, *stbl, ["co64"])
        n = struct.unpack(">I", data[co64[0] + 4:co64[0] + 8])[0]
        table.chunks = list(struct.unpack(f">{n}Q", data[co64[0] + 8:co64[0] + 8 + 8 * n]))
    stsc = _find(data, *stbl, ["stsc"])
    m = struct.unpack(">I", data[stsc[0] + 4:stsc[0] + 8])[0]
    rows = [struct.unpack(">III", data[stsc[0] + 8 + 12 * i:stsc[0] + 20 + 12 * i]) for i in range(m)]
    for i, (first, per, _desc) in enumerate(rows):
        last = rows[i + 1][0] - 1 if i + 1 < len(rows) else len(table.chunks)
        table.per_chunk += [per] * max(0, last - first + 1)
    stts = _find(data, *stbl, ["stts"])
    m = struct.unpack(">I", data[stts[0] + 4:stts[0] + 8])[0]
    for i in range(m):
        repeat, delta = struct.unpack(">II", data[stts[0] + 8 + 8 * i:stts[0] + 16 + 8 * i])
        table.deltas += [delta] * repeat
    ctts = _find(data, *stbl, ["ctts"])
    if ctts is not None:
        version, at = _full(data, ctts)
        m = struct.unpack(">I", data[at:at + 4])[0]
        for i in range(m):
            repeat, offset = struct.unpack(">I" + ("i" if version else "I"), data[at + 4 + 8 * i:at + 12 + 8 * i])
            table.shifts += [offset] * repeat
    stss = _find(data, *stbl, ["stss"])
    if stss is not None:
        m = struct.unpack(">I", data[stss[0] + 4:stss[0] + 8])[0]
        table.sync = _u32s(data, stss[0] + 8, m)
    elst = _find(data, a, b, ["edts", "elst"])
    if elst is not None:
        version, at = _full(data, elst)
        if struct.unpack(">I", data[at:at + 4])[0] >= 1:
            entry = data[at + 4:at + (24 if version else 16)]
            media = struct.unpack(">q", entry[8:16])[0] if version else struct.unpack(">i", entry[4:8])[0]
            table.begins = max(0, media)
    stsd = _find(data, *stbl, ["stsd"])
    entries = list(_boxes(data, stsd[0] + 8, stsd[1]))
    if entries:
        table.codec, ea, eb = entries[0]
        if kind == "vide":                      # VisualSampleEntry: 78 bytes, then its boxes (avcC)
            avcc = _find(data, ea + 78, eb, ["avcC"])
            table.config = bytes(data[avcc[0]:avcc[1]]) if avcc else b""
        elif kind == "soun":                    # AudioSampleEntry: 28 bytes, then its boxes (esds)
            esds = _find(data, ea + 28, eb, ["esds"])
            table.config = _audio_config(data, esds) if esds else b""
    if len(table.per_chunk) != len(table.chunks) or sum(table.per_chunk) != len(table.sizes) \
            or len(table.deltas) != len(table.sizes):
        return None
    return table


def tables(session, url: str) -> dict:
    """{"vide": TrackTable, "soun": TrackTable} of a CVH MP4 — its ``moov`` read from its first bytes."""
    import requests
    try:
        head = _range(session, url, 0, HEAD, upto=True)
        moov = next(((a - 8, b - (a - 8)) for kind, a, b in _top(head) if kind == "moov"), None)
        if moov is None:
            raise SoundError("в начале файла нет таблицы (moov)")
        start, size = moov
        if size > MOOV_MAX:
            raise SoundError("таблица файла слишком велика")
        data = head[start:start + size] if start + size <= len(head) else \
            head[start:] + _range(session, url, len(head), start + size - len(head))
    except requests.RequestException as exc:
        raise SoundError(f"CVH не ответил: {exc}") from exc
    out = {}
    for kind, a, b in _boxes(data, 8, len(data)):
        if kind == "trak":
            table = _track(data, a, b)
            if table is not None and table.kind in ("vide", "soun") and table.kind not in out:
                out[table.kind] = table
    if "soun" not in out or "vide" not in out:
        raise SoundError("в файле нет звука или картинки")
    return out


def _top(data: bytes):
    """The top boxes of the first bytes — sizes as said (a box may go on past them)."""
    at = 0
    while at + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[at:at + 8])
        head = 8
        if size == 1 and at + 16 <= len(data):
            size, head = struct.unpack(">Q", data[at + 8:at + 16])[0], 16
        if size < head:
            return
        yield kind.decode("latin-1"), at + head, at + size
        at += size


def _range(session, url: str, start: int, length: int, *, upto: bool = False) -> bytes:
    """``length`` bytes from ``start`` (a 206 answer of exactly them, or SoundError); ``upto`` — at most them (the
    first bytes of a file that may be shorter)."""
    answer = session.get(url, headers={"Range": f"bytes={start}-{start + length - 1}"}, timeout=(10, 30))
    if not (answer.status_code == 206 or (answer.status_code == 200 and start == 0)):
        raise SoundError(f"CVH ответил {answer.status_code}")
    data = answer.content
    download_telemetry.record(net=len(data))
    if answer.status_code == 200:
        data = data[:length]
    if len(data) != length and not (upto and 0 < len(data) < length):
        raise SoundError(f"CVH прислал {len(data)} байт из {length}")
    return data


# ── the pieces ────────────────────────────────────────────────────────────────────────
def fetch(new_session, url: str, places: list, *, stop=None, progress=None, workers: int = WORKERS) -> list:
    """The bytes of ``places`` [(offset, size, …)] — asked ``workers`` at a time, each tried ``TRIES`` times;
    SoundError when one does not come (or the stop is asked)."""
    import requests
    local = threading.local()
    done = [0]
    lock = threading.Lock()

    def one(place):
        if stop is not None and stop():
            raise SoundError("остановлено")
        if not hasattr(local, "session"):
            local.session = new_session()
        why = ""
        for attempt in range(TRIES):
            try:
                data = _range(local.session, url, place[0], place[1])
                with lock:
                    done[0] += len(data)
                    if progress is not None:
                        progress(done[0])
                return data
            except (SoundError, requests.RequestException) as exc:
                why = str(exc)
                time.sleep(0.5 * (attempt + 1))
        raise SoundError(f"кусок файла CVH не пришёл: {why[:160]}")
    def in_context(context, place):
        return context.run(one, place)

    with ThreadPoolExecutor(max(1, workers)) as pool:
        return list(pool.map(in_context, (copy_context() for _ in places), places))


def adts_frames(frames: list, config: bytes) -> bytes:
    """Raw AAC frames with ADTS headers (the AudioSpecificConfig's profile, rate, channels) — SoundError for a sound
    ADTS cannot say (HE-AAC's explicit rate…)."""
    if len(config) < 2:
        raise SoundError("у звука нет описания")
    value = int.from_bytes(config[:2], "big")
    profile, rate, channels = value >> 11, (value >> 7) & 0x0F, (value >> 3) & 0x0F
    if not 1 <= profile <= 4 or rate > 12 or not 1 <= channels <= 7:
        raise SoundError("звук такого вида в ADTS не записать")
    out = bytearray()
    for frame in frames:
        size = len(frame) + 7
        if size >= 1 << 13:
            raise SoundError("кадр звука слишком велик")
        out += bytes([0xFF, 0xF1, ((profile - 1) << 6) | (rate << 2) | (channels >> 2),
                      ((channels & 3) << 6) | (size >> 11), (size >> 3) & 0xFF, ((size & 7) << 5) | 0x1F, 0xFC])
        out += frame
    return bytes(out)


# ── the key frames' prints ────────────────────────────────────────────────────────────
def key_prints(new_session, url: str, table: TrackTable, *, shift: float, stop=None):
    """``video_merge.Prints`` of the picture's key frames (from the smallest quality), on the sound's clock
    (``shift`` — seconds the sound's ADTS begins after the MP4's time 0)."""
    import av
    import numpy as np
    from core.video_merge import Prints, thumb
    if table.codec not in ("avc1", "avc3") or not table.config:
        raise SoundError(f"картинка {table.codec or '?'} — ключевые кадры не прочитать")
    keys = [k - 1 for k in (table.sync or range(1, len(table.sizes) + 1)) if 0 < k <= len(table.sizes)]
    offset_of = {}                              # each sample's place: its chunk's place and the samples before it
    sample = 0
    for chunk, count in zip(table.chunks, table.per_chunk):
        at = chunk
        for n in range(sample, sample + count):
            offset_of[n] = at
            at += table.sizes[n]
        sample += count
    data = fetch(new_session, url, [(offset_of[k], table.sizes[k]) for k in keys], stop=stop)
    times = table.times()
    codec = av.CodecContext.create("h264", "r")
    codec.extradata = table.config
    out_times, bits, width, height = [], [], 0, 0

    def took(frames):
        nonlocal width, height
        for frame in frames:                    # a frame comes out later than its packet (the decoder's delay of
            if frame.pts is None or not 0 <= frame.pts < len(times):   # B-frames): its sample is its pts
                continue
            grey = thumb(frame)
            bits.append(np.packbits((grey[:, 1:] > grey[:, :-1]).ravel()))
            out_times.append(times[frame.pts] + shift)
            width, height = frame.width, frame.height
    for k, sample_bytes in zip(keys, data):
        packet = av.Packet(sample_bytes)
        packet.pts = packet.dts = k
        try:
            took(codec.decode(packet))
        except av.FFmpegError as exc:
            _LOG.info("key frame %d not decoded: %s", k, exc)
    try:
        took(codec.decode(None))
    except av.FFmpegError as exc:
        _LOG.info("the last key frames not decoded: %s", exc)
    if len(out_times) < 3:
        raise SoundError("ключевые кадры не прочитались")
    order = np.argsort(np.array(out_times))
    step = sum(table.deltas[:100]) / max(1, min(100, len(table.deltas))) / table.scale or 1 / 24
    return Prints(np.array(out_times)[order], np.array(bits)[order], float(step), width, height)


# ── the episode's sound ───────────────────────────────────────────────────────────────
def download(new_session, sound_url: str, keys_url: str, target: Path, *, folder: Path, say, stop, label: str,
             meter=None) -> str:
    """The sound of a CVH file (``sound_url``) into ``target`` («Серия NN.aac») and its picture's key frames' prints
    (``keys_url`` — the smallest quality) kept as the file's: "done" | "stopped"; SoundError when it cannot be had.
    A part begun goes on (``<target>.part`` with its note: the pieces in it)."""
    from core import video_merge
    session = new_session()
    started = time.monotonic()
    source_tables = tables(session, sound_url)
    sound = source_tables["soun"]
    if sound.codec != "mp4a" or not sound.config:
        raise SoundError(f"звук {sound.codec or '?'} — не AAC")
    places = sound.places()
    total = sum(p[1] for p in places)
    part = target.with_name(target.name + ".part")
    note = target.with_name(target.name + NOTE)
    source = _source_key(sound_url, sound)
    done = 0
    try:
        kept = json.loads(note.read_text(encoding="utf-8")) if note.is_file() and part.is_file() else {}
    except (OSError, ValueError):
        kept = {}
    if kept.get("source") == source and isinstance(kept.get("pieces"), int) and \
            kept.get("bytes") == part.stat().st_size:
        done = max(0, min(kept["pieces"], len(places)))
    else:
        part.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    had = sum(p[1] for p in places[:done])
    say(f"{label}: звук без картинки из CVH ({total / 1e6:.0f} МБ" + (f", с {had / 1e6:.0f} МБ" if done else "")
        + f", {len(places)} кусков)")
    batch = 400                                 # pieces written and noted together (a stop loses at most these)

    def progress(bytes_now):
        if meter is not None:
            meter(had + bytes_now, total, (bytes_now) / max(0.001, time.monotonic() - started))
    sizes = sound.sizes
    with open(part, "ab") as fh:
        for at in range(done, len(places), batch):
            if stop():
                return "stopped"
            chunk = places[at:at + batch]
            try:
                pieces = fetch(new_session, sound_url, chunk, stop=stop,
                               progress=lambda b, base=sum(p[1] for p in places[done:at]): progress(base + b))
            except SoundError:
                if stop():
                    return "stopped"
                raise
            frames = []
            for place, data in zip(chunk, pieces):
                pos = 0
                for size in sizes[place[2]:place[2] + place[3]]:
                    frames.append(data[pos:pos + size])
                    pos += size
            download_telemetry.record(disk=fh.write(adts_frames(frames, sound.config)))
            fh.flush()
            note.write_text(json.dumps({"source": source, "pieces": at + len(chunk), "bytes": fh.tell()}),
                            encoding="utf-8")
    shift = sound.begins / sound.scale          # the sound's ADTS begins at its first sample: MP4 time −shift
    keys_table = tables(session, keys_url)["vide"] if keys_url != sound_url else source_tables["vide"]
    prints = key_prints(new_session, keys_url, keys_table, shift=shift, stop=stop)
    part.replace(target)
    note.unlink(missing_ok=True)
    video_merge.keep(video_merge._prints_place(folder, target), target, prints, keys=True)
    say(f"{label}: звук {target.stat().st_size / 1e6:.0f} МБ и {len(prints.times)} ключевых кадров для подгонки "
        f"за {_clock(time.monotonic() - started)}")
    return "done"


def _source_key(url: str, sound: TrackTable) -> str:
    """What a begun part is of: its sound's table (CVH's addresses are signed and change — the table does not)."""
    import hashlib
    head = hashlib.sha1(struct.pack(f">{min(len(sound.sizes), 400)}I", *sound.sizes[:400])).hexdigest()[:16]
    return f"{len(sound.sizes)}|{sum(sound.sizes)}|{head}"


def _clock(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"
