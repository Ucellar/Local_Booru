"""The picture and the sound of an HLS stream that a site gives as two tracks (``#EXT-X-MEDIA TYPE=AUDIO`` with an
address of its own, hentaihaven) put into one MP4 — what a player needs to play the episode with its sound.

Both parts are fragmented MP4 (CMAF): a header ``ftyp`` + ``moov`` and fragments ``moof`` + ``mdat``.  The joined file
gets one header with both tracks — the sound's track number changed when it is the picture's — and the fragments of
both in the order of their times.  Nothing is re-encoded: only the boxes around the data are written anew (the track
number in ``tfhd``, the fragments' numbers in ``mfhd``, an absolute ``base_data_offset`` when a part has one).
``styp``, ``sidx``, ``prft`` and ``emsg`` of the parts are left out: they speak of one track of one stream.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

CHUNK = 1 << 20
MAX_HEADER = 32 * 1024 * 1024           # a moov or a moof bigger than this is not a stream's
_DROP = {b"styp", b"sidx", b"ssix", b"prft", b"emsg", b"free", b"skip"}
_LOCKED = (b"encv", b"enca", b"sinf", b"pssh", b"senc", b"tenc")


class MuxError(Exception):
    """Said to the person as it is."""


# ── boxes ──────────────────────────────────────────────────────────────────────────
def _box(kind: bytes, payload: bytes) -> bytes:
    size = 8 + len(payload)
    if size > 0xFFFFFFFF:
        return struct.pack(">I4sQ", 1, kind, size + 8) + payload
    return struct.pack(">I4s", size, kind) + payload


def _children(data: bytes, start: int = 0, end: int | None = None):
    """(type, box start, payload start, box end) of the boxes in ``data[start:end]``."""
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise MuxError("часть потока испорчена (неверный размер блока)")
        yield kind, pos, pos + header, pos + size
        pos += size


def _first(data: bytes, start: int, end: int, kind: bytes):
    return next(((s, p, e) for k, s, p, e in _children(data, start, end) if k == kind), None)


def _top(fh, size: int):
    """(type, start, header size, whole size) of the boxes at the top of a file."""
    pos = 0
    while pos + 8 <= size:
        fh.seek(pos)
        head = fh.read(16)
        box, kind = struct.unpack_from(">I4s", head, 0)
        header = 8
        if box == 1:
            if len(head) < 16:
                raise MuxError("часть потока оборвана")
            box = struct.unpack_from(">Q", head, 8)[0]
            header = 16
        elif box == 0:
            box = size - pos
        if box < header or pos + box > size:
            raise MuxError("часть потока оборвана или испорчена")
        yield kind, pos, header, box
        pos += box


# ── one part: its header and its track ────────────────────────────────────────────────
class _Part:
    def __init__(self, path: Path, what: str):
        self.path = Path(path)
        self.what = what
        self.size = self.path.stat().st_size
        self.ftyp = b""
        self.moov = b""
        with open(self.path, "rb") as fh:
            for kind, start, header, box in _top(fh, self.size):
                if kind in (b"ftyp", b"moov"):
                    if box > MAX_HEADER:
                        raise MuxError(f"{what}: заголовок слишком большой")
                    fh.seek(start)
                    if kind == b"ftyp":
                        self.ftyp = fh.read(box)
                    else:
                        self.moov = fh.read(box)
                if kind == b"moof":
                    break
        if not self.moov:
            raise MuxError(f"{what}: нет заголовка MP4 (moov)")
        if any(mark in self.moov for mark in _LOCKED):
            raise MuxError(f"{what}: дорожка зашифрована — это защита сайта")
        body = (8, len(self.moov))
        traks = [(s, p, e) for k, s, p, e in _children(self.moov, *body) if k == b"trak"]
        if len(traks) != 1:
            raise MuxError(f"{what}: дорожек в части {len(traks)}, а нужна одна")
        if _first(self.moov, *body, b"mvex") is None:
            raise MuxError(f"{what}: часть не из фрагментов MP4")
        self.trak = traks[0]
        tkhd = _first(self.moov, self.trak[1], self.trak[2], b"tkhd")
        if tkhd is None:
            raise MuxError(f"{what}: у дорожки нет tkhd")
        self.id_at = tkhd[1] + (20 if self.moov[tkhd[1]] == 1 else 12)
        self.track_id = struct.unpack_from(">I", self.moov, self.id_at)[0]
        mdia = _first(self.moov, self.trak[1], self.trak[2], b"mdia")
        mdhd = _first(self.moov, mdia[1], mdia[2], b"mdhd") if mdia else None
        if mdhd is None:
            raise MuxError(f"{what}: у дорожки нет mdhd")
        self.timescale = struct.unpack_from(">I", self.moov, mdhd[1] + (20 if self.moov[mdhd[1]] == 1 else 12))[0]
        if not self.timescale:
            raise MuxError(f"{what}: у дорожки нет шкалы времени")
        mvhd = _first(self.moov, *body, b"mvhd")
        self.movie_scale = (struct.unpack_from(">I", self.moov, mvhd[1] + (20 if self.moov[mvhd[1]] == 1 else 12))[0]
                            if mvhd else 0) or 1000

    def trak_with_id(self, track_id: int, movie_scale: int = 0) -> bytes:
        """Its track with another number — and, in another movie's time scale (``movie_scale``), the lengths that
        are counted in the movie's scale (``tkhd`` duration, the edit list's segments) recounted."""
        start = self.trak[0]
        data = bytearray(self.moov[start:self.trak[2]])
        struct.pack_into(">I", data, self.id_at - start, track_id)
        if movie_scale and movie_scale != self.movie_scale:
            def scaled(value: int) -> int:
                return int(round(value * movie_scale / self.movie_scale))
            tkhd = _first(self.moov, self.trak[1], self.trak[2], b"tkhd")
            version = self.moov[tkhd[1]]
            at = tkhd[1] + (28 if version == 1 else 20)                        # its duration
            fmt = ">Q" if version == 1 else ">I"
            struct.pack_into(fmt, data, at - start, scaled(struct.unpack_from(fmt, self.moov, at)[0]))
            edts = _first(self.moov, self.trak[1], self.trak[2], b"edts")
            elst = _first(self.moov, edts[1], edts[2], b"elst") if edts else None
            if elst is not None:
                version = self.moov[elst[1]]
                count = struct.unpack_from(">I", self.moov, elst[1] + 4)[0]
                at, step, fmt = elst[1] + 8, (20 if version == 1 else 12), (">Q" if version == 1 else ">I")
                for _n in range(count):
                    if at + step > elst[2]:
                        break
                    struct.pack_into(fmt, data, at - start, scaled(struct.unpack_from(fmt, self.moov, at)[0]))
                    at += step
        return bytes(data)

    def trex_with_id(self, track_id: int) -> bytes:
        mvex = _first(self.moov, 8, len(self.moov), b"mvex")
        for kind, start, payload, end in _children(self.moov, mvex[1], mvex[2]):
            if kind == b"trex":
                data = bytearray(self.moov[start:end])
                struct.pack_into(">I", data, payload - start + 4, track_id)
                return bytes(data)
        raise MuxError(f"{self.what}: в заголовке нет trex")

    def fragments(self):
        """(time in seconds, start, moof bytes, end of what follows the moof up to its mdat's end) in file order."""
        with open(self.path, "rb") as fh:
            pending = None                       # (start, moof bytes)
            for kind, start, header, box in _top(fh, self.size):
                if kind == b"moof":
                    if box > MAX_HEADER:
                        raise MuxError(f"{self.what}: фрагмент слишком большой")
                    fh.seek(start)
                    pending = (start, fh.read(box))
                elif kind == b"mdat" and pending is not None:
                    yield self._time(pending[1]), pending[0], pending[1], start + box
                    pending = None

    def _time(self, moof: bytes) -> float:
        for kind, _s, payload, end in _children(moof, 8, len(moof)):
            if kind == b"traf":
                tfdt = _first(moof, payload, end, b"tfdt")
                if tfdt is not None:
                    at = tfdt[1]
                    base = (struct.unpack_from(">Q", moof, at + 4)[0] if moof[at] == 1
                            else struct.unpack_from(">I", moof, at + 4)[0])
                    return base / self.timescale
        return 0.0


def _moof_for(moof: bytes, *, sequence: int, track_id: int, shift: int) -> bytes:
    """The fragment's header in the joined file: its number, its track, an absolute data offset moved by ``shift``."""
    data = bytearray(moof)
    for kind, _s, payload, end in _children(moof, 8, len(moof)):
        if kind == b"mfhd":
            struct.pack_into(">I", data, payload + 4, sequence)
        elif kind == b"traf":
            tfhd = _first(moof, payload, end, b"tfhd")
            if tfhd is None:
                raise MuxError("у фрагмента нет tfhd")
            at = tfhd[1]
            struct.pack_into(">I", data, at + 4, track_id)
            if int.from_bytes(moof[at + 1:at + 4], "big") & 0x000001:         # base-data-offset-present
                old = struct.unpack_from(">Q", moof, at + 8)[0]
                struct.pack_into(">Q", data, at + 8, old + shift)
    return bytes(data)


# ── joining ──────────────────────────────────────────────────────────────────────────
def mux(video: Path, audio: Path, out: Path, *, stop=None) -> str:
    """The picture ``video`` and the sound ``audio`` (both fragmented MP4) into ``out``: "done" or "stopped" (``out``
    is then left half-made: the caller writes into a part file).  MuxError — the parts are not what can be joined."""
    stop = stop or (lambda: False)
    picture, sound = _Part(video, "видео"), _Part(audio, "звук")
    picture_id = picture.track_id
    sound_id = sound.track_id if sound.track_id != picture_id else picture_id + 1
    moov = picture.moov
    parts = []
    for kind, start, _payload, end in _children(moov, 8, len(moov)):
        if kind == b"mvhd":
            mvhd = bytearray(moov[start:end])
            struct.pack_into(">I", mvhd, len(mvhd) - 4, max(picture_id, sound_id) + 1)   # next_track_ID
            parts.append(bytes(mvhd))
        elif kind == b"trak":
            parts.append(moov[start:end])
            parts.append(sound.trak_with_id(sound_id, picture.movie_scale))
        elif kind == b"mvex":
            extra = [moov[s:e] for k, s, _p, e in _children(moov, *_first(moov, 8, len(moov), b"mvex")[1:])
                     if k != b"trex"]
            parts.append(_box(b"mvex", b"".join(extra) + picture.trex_with_id(picture_id)
                              + sound.trex_with_id(sound_id)))
        elif kind not in (b"pssh",):
            parts.append(moov[start:end])
    head = (picture.ftyp or _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso6mp41")) + _box(b"moov", b"".join(parts))

    streams = [(picture, picture_id, iter(picture.fragments())), (sound, sound_id, iter(sound.fragments()))]
    nexts = [next(it, None) for _p, _i, it in streams]
    sequence = 0
    with open(out, "wb") as dest, open(picture.path, "rb") as fv, open(sound.path, "rb") as fa:
        sources = (fv, fa)
        dest.write(head)
        while any(n is not None for n in nexts):
            if stop():
                return "stopped"
            # the fragment that starts first; of equal times the picture's
            which = min((i for i, n in enumerate(nexts) if n is not None), key=lambda i: (nexts[i][0], i))
            _time, start, moof, end = nexts[which]
            sequence += 1
            where = dest.tell()
            dest.write(_moof_for(moof, sequence=sequence, track_id=streams[which][1], shift=where - start))
            source = sources[which]
            source.seek(start + len(moof))
            left = end - (start + len(moof))         # what follows the moof up to the end of its mdat, as it was
            while left > 0:
                chunk = source.read(min(CHUNK, left))
                if not chunk:
                    raise MuxError(f"{streams[which][0].what}: часть оборвана")
                dest.write(chunk)
                left -= len(chunk)
            nexts[which] = next(streams[which][2], None)
        dest.flush()
        os.fsync(dest.fileno())
    if sequence == 0:
        raise MuxError("в частях потока нет ни одного фрагмента")
    return "done"
