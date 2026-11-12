"""Where an episode's opening and ending are (v5.43): «Пропустить опенинг» / «Пропустить эндинг» on the series page,
their stretches marked on the player's bar.

The owner asked for it (31.10.2026): the buttons as anime sites have them — the opening's on the left, the ending's on
the right, shown only while it plays; the stretches on the bar in the look's colour; only for series.

**Found by the sound, episode against episode** (:func:`find_series`): an opening and an ending are the same song in
every episode, the rest is not.  Each episode's sound is a row of 32-bit prints (``sound_prints``: 8 kHz, a print
every 64 ms — the signs of the energy's change between 33 bands from 300 Hz to 2 kHz, from one moment to the next, as
music recognisers make them; the same song gives the same prints whatever is laid over it lightly).  Two episodes
are compared (:func:`common`): prints that are exactly alike vote for a shift between them; along each shift with
many votes the prints are compared bit by bit (2-second average under ``MAX_BER``), and a stretch alike for
``MIN_LEN`` to ``MAX_LEN`` seconds is one they have in common.  An episode is compared with up to ``PEERS`` others of
other numbers (never with another voice-over of its own episode — that is all alike); a stretch is believed when
``SUPPORT`` of them have it (a recap of the last episode is in one only): in the first ``OPENING_IN`` of the episode —
its opening, after ``ENDING_FROM`` — its ending.  A series of one episode has nothing to compare with.

**And by the picture** (:func:`picture_common`): a studio may lay something over the song (AniStar's «Фрирен» —
the ending's sound is not alike from episode to episode, its picture is), so the key frames of two episodes are
matched too (``video_merge``'s prints and anchors): a run of one shift for ``MIN_LEN`` seconds is a stretch in common,
its ends made exact frame by frame (``_edges``).  The stretches of both are taken together.

**YummyAnime's own times** (``hint``) — given by its people for a part of the episodes and players, to the second, now
and then wrong (an opening «from 0» in an episode without one) — are taken only when the sound found nothing.

Kept in the series folder: ``.lb/skips.json`` (by the episode's path, size and time: a file changed is looked at
again), the prints of the sound — ``.lb/sound/`` (the library skips the folder ``.lb``).  The background duty
``skips`` looks at a few series a round (``background_round``); the page asks :func:`skips_of`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path

_LOG = logging.getLogger("local_booru.video")

RATE = 8000                  # the sound is looked at in 8 kHz mono
WINDOW = 2048                # samples of one look (0.256 s)
HOP = 512                    # samples between looks: a print every 64 ms
BANDS = 33                   # 300 Hz … 2 kHz, the same width on a log scale: 32 bits a print
LOW, HIGH = 300.0, 2000.0
MAX_BER = 0.33               # bits of 32 two prints of the same music may differ by, on a 2-second average
MIN_LEN = 25.0               # seconds a stretch in common needs (a jingle is shorter, a song is longer)
MAX_LEN = 200.0              # … at most (more: the same episode, another voice-over of it)
VOTES = 25                   # prints exactly alike a shift needs to be looked along
COMMON_VALUE = 60            # a print found this often in an episode tells nothing (silence, a held note)
PEERS = 4                    # other episodes an episode is compared with
SUPPORT = 2                  # of them that must have the stretch (1 when the series has two episodes)
OPENING_IN = 0.45            # the opening begins in this share of the episode
ENDING_FROM = 0.55           # the ending begins after this share
SMOOTH = 32                  # prints in the bit error average (~2 s)
CHUNK = 2048                 # looks put through the FFT at once (~2 min of sound, ~35 MB)
THREADS = 1                  # the decoder's threads: the work goes on in the background, the computer is the
                             # person's.  (v5.44.151; was 4) Measured on the owner's computer, three episodes of 12
                             # minutes: 45.5 s and 1.25 processors with four, 51.9 s and 1.06 with one — the making of
                             # prints, not the decoder, takes the time; one more thread bought nothing
SAME_SHIFT = 2.0             # seconds two stretches of one peer may be apart in shift and still be one song
VERSION = 1
NOTE = "skips.json"
_LOCK = threading.Lock()


def available() -> bool:
    try:
        import av  # noqa: F401
        import numpy  # noqa: F401 - the prints are numpy arrays
    except ImportError:
        return False
    return True


# ── prints of the sound ──────────────────────────────────────────────────────────────
def _bands():
    import numpy as np
    freqs = np.fft.rfftfreq(WINDOW, 1 / RATE)
    edges = np.geomspace(LOW, HIGH, BANDS + 1)
    return [(int(np.searchsorted(freqs, edges[k])), max(int(np.searchsorted(freqs, edges[k + 1])),
                                                         int(np.searchsorted(freqs, edges[k])) + 1))
            for k in range(BANDS)]


def prints_from(samples):
    """32-bit prints of 8 kHz mono samples (numpy float32): one every ``HOP`` samples."""
    import numpy as np
    samples = np.ascontiguousarray(samples, dtype=np.float32)
    count = (len(samples) - WINDOW) // HOP + 1
    if count < 3:
        return np.zeros(0, dtype=np.uint32)
    frames = np.lib.stride_tricks.as_strided(samples, shape=(count, WINDOW),
                                             strides=(samples.strides[0] * HOP, samples.strides[0]))
    window, bands = np.hanning(WINDOW).astype(np.float32), _bands()
    energy = np.empty((count, BANDS), dtype=np.float64)
    for k in range(0, count, CHUNK):                             # a few MB at a time, not the episode at once
        spectrum = np.abs(np.fft.rfft(frames[k:k + CHUNK] * window, axis=1)) ** 2
        energy[k:k + CHUNK] = np.stack([spectrum[:, a:b].sum(1) for a, b in bands], axis=1)
    across = energy[:, :-1] - energy[:, 1:]                      # (count, 32)
    bits = (across[1:] - across[:-1]) > 0                        # (count-1, 32)
    weights = (1 << np.arange(31, -1, -1, dtype=np.uint64)).astype(np.uint64)
    return (bits.astype(np.uint64) @ weights).astype(np.uint32)


def sound_prints(path: Path, *, stop=None, cache: Path | None = None):
    """(the time of the first print on the file's clock, the prints) of a file's first sound track."""
    import av
    import numpy as np
    path = Path(path)
    stat = path.stat()
    key = f"{stat.st_size}-{int(stat.st_mtime)}-{VERSION}"
    if cache is not None and cache.is_file():
        try:
            with np.load(cache) as kept:
                if str(kept["key"]) == key:
                    return float(kept["start"]), kept["prints"]
        except Exception as exc:   # noqa: BLE001 - a cut-short or foreign file (BadZipFile, EOFError …): made anew
            _LOG.info("sound prints of %s not read: %s: %s", path.name, type(exc).__name__, exc)
    pieces, start = [], None
    with av.open(str(path)) as box:
        if not box.streams.audio:
            return 0.0, np.zeros(0, dtype=np.uint32)
        stream = box.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=RATE)
        for n, frame in enumerate(box.decode(stream)):
            if stop is not None and n % 500 == 0 and stop():      # ten seconds of sound: asked several times a second
                raise InterruptedError
            if start is None and frame.time is not None:
                start = float(frame.time)
            for out in resampler.resample(frame):
                pieces.append(out.to_ndarray().reshape(-1))
    samples = np.ascontiguousarray(np.concatenate(pieces)) if pieces else np.zeros(0, dtype=np.float32)
    prints = prints_from(samples.astype(np.float32))
    start = (start or 0.0) + WINDOW / 2 / RATE
    if cache is not None:
        from core.video_merge import save_kept
        save_kept(cache, key=key, start=start, prints=prints)
    return start, prints


# ── what two episodes have in common ─────────────────────────────────────────────────
_POP = None


def _popcount(values):
    import numpy as np
    global _POP
    if _POP is None:
        _POP = np.array([bin(k).count("1") for k in range(1 << 16)], dtype=np.uint8)
    values = values.astype(np.uint32)
    return _POP[values & 0xFFFF] + _POP[values >> 16]


def common(a, b) -> list[tuple[float, float, float]]:
    """Stretches the two rows of prints have in common: (start in a, end in a, shift — b's place minus a's), in
    seconds of print steps (``HOP / RATE``)."""
    import numpy as np
    if len(a) < SMOOTH * 2 or len(b) < SMOOTH * 2:
        return []
    step = HOP / RATE
    values, where = np.unique(b, return_inverse=True)
    counts = np.bincount(where)
    order = np.argsort(where, kind="stable")
    starts = np.concatenate([[0], np.cumsum(counts)])
    found = np.searchsorted(values, a)
    found = np.clip(found, 0, len(values) - 1)
    hit = (values[found] == a)
    votes: dict = {}
    for i in np.nonzero(hit)[0]:
        k = found[i]
        if counts[k] > COMMON_VALUE:
            continue
        for j in order[starts[k]:starts[k + 1]]:
            shift = int(j) - int(i)
            votes[shift] = votes.get(shift, 0) + 1
    if not votes:
        return []
    tops = sorted((v, s) for s, v in votes.items() if v >= VOTES)[::-1][:12]
    out, seen = [], []
    kernel = np.ones(SMOOTH, dtype=np.float32) / SMOOTH
    for _v, shift in tops:
        if any(abs(shift - s) <= 4 for s in seen):
            continue
        seen.append(shift)
        lo, hi = max(0, -shift), min(len(a), len(b) - shift)
        if hi - lo < SMOOTH * 2:
            continue
        errors = _popcount(a[lo:hi] ^ b[lo + shift:hi + shift]).astype(np.float32) / 32
        alike = np.convolve(errors, kernel, mode="same") < MAX_BER
        edges = np.diff(np.concatenate([[0], alike.astype(np.int8), [0]]))
        for begin, end in zip(np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]):
            length = (end - begin) * step
            if MIN_LEN <= length <= MAX_LEN:
                out.append(((lo + begin) * step, (lo + end) * step, shift * step))
    return out


# ── what two episodes' pictures have in common ──────────────────────────────────────────
RUN_GAP = 10.0               # seconds between two key frames of one run at most
RUN_TOL = 0.5                # seconds two anchors' shifts may differ by in one run
EDGE = 20.0                  # seconds before a run's first anchor (after its last) looked at a time for its true end
EDGE_MISS = 0.6              # seconds of frames not alike that end a stretch


def picture_common(a, b) -> list[tuple[float, float, float]]:
    """Stretches the key frames of two episodes (``video_merge.Prints``) have in common: (start in a, end in a,
    shift — b's time minus a's), from the first anchor to the last of a run (``_edges`` makes them exact)."""
    from core import video_merge as G
    anchors = sorted(G._anchors(b, a))
    out, run = [], []
    for u, t in anchors + [(float("inf"), float("inf"))]:
        if run and abs((t - u) - (run[-1][1] - run[-1][0])) <= RUN_TOL and u - run[-1][0] <= RUN_GAP:
            run.append((u, t))
            continue
        if len(run) >= 4 and run[-1][0] - run[0][0] >= MIN_LEN * 0.6:
            shifts = sorted(t - u for u, t in run)
            out.append((run[0][0], run[-1][0], shifts[len(shifts) // 2]))
        run = [(u, t)]
    return out


class Frames:
    """Every frame's print of stretches of files (``video_merge.prints_of``), read in pieces of ``PIECE`` seconds once
    for a look at a series: an episode's opening and ending are compared with four others, and each of them with its
    own four — read once, not eight times."""
    PIECE = 20.0

    def __init__(self, stop=None, folder: Path | None = None):
        self.stop = stop
        self.folder = folder                 # the series folder: the prints of its MKVs kept by their putting together
        self.pieces: dict = {}               # (path, number) → (times, bits) | None (nothing there, does not read)
        self.whole: dict = {}                # path → its every frame's prints kept already (``video_merge``) | None

    def get(self, path: Path, lo: float, hi: float):
        """(times, bits) of the frames of ``path`` from ``lo`` to ``hi`` (its clock) — None when there are none.  An
        episode's MKV whose voice-overs were put together here has every frame's prints kept already: not read."""
        import numpy as np
        from core import video_merge as G
        lo = max(0.0, lo)
        if hi < lo:
            return None
        if str(path) not in self.whole:
            self.whole[str(path)] = G.kept_prints(G._prints_place(Path(self.folder), Path(path)), Path(path)) \
                if self.folder is not None else None
        whole = self.whole[str(path)]
        if whole is not None:
            kept = (whole.times >= lo) & (whole.times <= hi)
            return (whole.times[kept], whole.bits[kept]) if kept.any() else None
        times, bits = [], []
        for k in range(int(lo // self.PIECE), int(hi // self.PIECE) + 1):
            key = (str(path), k)
            if key not in self.pieces:
                try:
                    got = G.prints_of(path, stop=self.stop, start=k * self.PIECE, end=(k + 1) * self.PIECE,
                                      threads=THREADS)
                    self.pieces[key] = (got.times, got.bits)
                except G.MergeError as exc:
                    if str(exc) == "остановлено":       # stopped: the episode is not kept as looked at
                        raise InterruptedError from exc
                    self.pieces[key] = None
            if self.pieces[key] is not None:
                times.append(self.pieces[key][0])
                bits.append(self.pieces[key][1])
        if not times:
            return None
        found, first = np.unique(np.concatenate(times), return_index=True)     # a frame on two pieces' border: once
        kept = (found >= lo) & (found <= hi)
        return (found[kept], np.concatenate(bits)[first][kept]) if kept.any() else None


def _edges(path_a: Path, path_b: Path, start: float, end: float, shift: float, stop=None,
           frames: Frames | None = None) -> tuple[float, float]:
    """A stretch's true ends: the frames of ``path_a`` before ``start`` (after ``end``) still alike to ``path_b``'s
    at ``shift`` — read frame by frame around them (``frames``: the pieces already read)."""
    import numpy as np
    from core import video_merge as G
    frames = frames if frames is not None else Frames(stop)

    def alike_times(lo: float, hi: float):
        pa = frames.get(path_a, lo, hi)
        pb = frames.get(path_b, lo + shift - 1, hi + shift + 1)
        if pa is None or pb is None:
            return None, None
        (ta, ba), (tb, bb) = pa, pb
        da = np.unpackbits(ba, axis=1).astype(np.int16)
        db = np.unpackbits(bb, axis=1).astype(np.int16)
        near = np.clip(np.searchsorted(tb, ta + shift), 0, len(tb) - 1)
        before = np.clip(near - 1, 0, len(tb) - 1)
        near = np.where(np.abs(tb[before] - ta - shift) < np.abs(tb[near] - ta - shift), before, near)
        return ta, np.abs(da - db[near]).sum(1) <= G.MAX_DIST

    # back from the first anchor, a window of EDGE seconds at a time while the frames are still alike at its
    # beginning (the first key frames of a song may be far from its start); forward the same
    for _ in range(int(MAX_LEN // EDGE) + 1):
        times, alike = alike_times(start - EDGE, start + 1)
        if times is None or not len(times):
            break
        k = int(np.searchsorted(times, start))
        last, ended = start, False
        for n in range(min(k, len(times) - 1), -1, -1):
            if alike[n]:
                last = float(times[n])
            elif last - float(times[n]) > EDGE_MISS:
                ended = True
                break
        moved = last < start - 0.01
        start = min(start, last)
        if ended or not moved:
            break
    for _ in range(int(MAX_LEN // EDGE) + 1):
        times, alike = alike_times(end - 1, end + EDGE)
        if times is None or not len(times):
            break
        k = int(np.searchsorted(times, end))
        step = float(times[1] - times[0]) if len(times) > 1 else 0.04
        last, ended = end, False
        for n in range(max(0, k - 1), len(times)):
            if alike[n]:
                last = float(times[n]) + step
            elif float(times[n]) - last > EDGE_MISS:
                ended = True
                break
        moved = last > end + 0.01
        end = max(end, last)
        if ended or not moved:
            break
    return start, end


# ── an episode's opening and ending ──────────────────────────────────────────────────
def _united(stretches: list) -> list[tuple]:
    """Each peer's stretches that overlap at one shift (its sound and its picture saw parts of one song) — one: their
    union; (start, end, peer, where).  Stretches at other shifts that only touch stay apart: a scene of the episode
    before repeated just before the opening («previously») is not the opening.  ``stretches``: (start, end, peer[,
    shift[, where — its start in the peer, a share of the peer's length]])."""
    out, shifts = [], []
    for peer in sorted({s[2] for s in stretches}):
        mine = sorted((s[0], s[1], s[3] if len(s) > 3 else None, s[4] if len(s) > 4 else None)
                      for s in stretches if s[2] == peer)
        for a, b, shift, where in mine:
            same = shift is None or shifts[-1] is None or abs(shift - shifts[-1]) <= SAME_SHIFT if out else False
            if out and out[-1][2] == peer and a < out[-1][1] and same:
                out[-1] = (out[-1][0], max(out[-1][1], b), peer, out[-1][3] if out[-1][3] is not None else where)
            else:
                out.append((a, b, peer, where))
                shifts.append(shift)
    return out


def _cluster(stretches: list) -> list[dict]:
    """The peers' stretches (start, end, peer) that overlap by half — one each: from the earliest start to the latest
    end of them (a peer may see only a part: an ending begins over the episode's own last scene), the medians when
    that would be longer than a song; how many peers have it."""
    groups: list[dict] = []
    for start, end, peer, where in sorted(_united(stretches), key=lambda s: s[:3]):
        for g in groups:
            overlap = min(end, g["end"]) - max(start, g["start"])
            if overlap >= 0.5 * min(end - start, g["end"] - g["start"]):
                g["starts"].append(start)
                g["ends"].append(end)
                g["peers"].add(peer)
                g["wheres"].append(where)
                g["start"], g["end"] = min(g["starts"]), max(g["ends"])
                if g["end"] - g["start"] > MAX_LEN:
                    g["start"] = sorted(g["starts"])[len(g["starts"]) // 2]
                    g["end"] = sorted(g["ends"])[len(g["ends"]) // 2]
                break
        else:
            groups.append({"start": start, "end": end, "starts": [start], "ends": [end], "peers": {peer},
                           "wheres": [where]})
    return groups


def choose(stretches: list, length: float, peers: int) -> dict:
    """{"opening": [start, end] | None, "ending": …} of an episode of ``length`` seconds from the stretches it has in
    common with ``peers`` other episodes ([(start, end, peer[, shift[, where]])] — ``_united``)."""
    need = 1 if peers <= 1 else min(SUPPORT, peers)
    out = {"opening": None, "ending": None}
    groups = _cluster(stretches)
    # one peer is enough when the stretch is in the same part of it too — the opening at its start, the ending at
    # its end (a season's first episode has no opening; a scene repeated from the episode before is at its end)
    first = [g for g in groups if g["start"] < OPENING_IN * length
             and (len(g["peers"]) >= need or any(w is not None and w < OPENING_IN for w in g["wheres"]))]
    last = [g for g in groups if g["start"] >= ENDING_FROM * length
            and (len(g["peers"]) >= need or any(w is not None and w >= ENDING_FROM for w in g["wheres"]))]
    if first:
        g = max(first, key=lambda g: (len(g["peers"]), g["end"] - g["start"]))
        out["opening"] = [round(g["start"], 2), round(g["end"], 2)]
    if last:
        g = max(last, key=lambda g: (len(g["peers"]), g["end"] - g["start"]))
        out["ending"] = [round(g["start"], 2), round(g["end"], 2)]
    return out


def _peers_of(episodes: list, n: int) -> list[int]:
    """Up to ``PEERS`` other episodes to compare episode ``n`` with — the nearest by place, of other numbers."""
    label = episodes[n].label
    others = [k for k in range(len(episodes)) if k != n and episodes[k].label != label]
    others.sort(key=lambda k: (abs(k - n), k))
    return others[:PEERS]


# ── kept in the series folder ────────────────────────────────────────────────────────
def _note_path(folder: Path) -> Path:
    return Path(folder) / ".lb" / NOTE


def read_note(folder: Path) -> dict:
    from core.file_safety import read_json_kept
    data = read_json_kept(_note_path(folder), dict, what="skips") if _note_path(folder).is_file() else None
    if isinstance(data, dict) and data.get("version") == VERSION and isinstance(data.get("episodes"), dict):
        return data
    return {"version": VERSION, "episodes": {}}


def _write_note(folder: Path, data: dict) -> None:
    from core.file_safety import write_json_kept
    place = _note_path(folder)
    place.parent.mkdir(parents=True, exist_ok=True)
    write_json_kept(place, json.dumps(data, ensure_ascii=False, indent=1), dict)


def _stamp(path: Path) -> str:
    try:
        stat = path.stat()
        return f"{stat.st_size}-{int(stat.st_mtime)}"
    except OSError:
        return ""


def _key(folder: Path, path: Path) -> str:
    try:
        return Path(path).relative_to(folder).as_posix()
    except ValueError:
        return Path(path).name


def hint(folder: Path, path: Path, opening=None, ending=None) -> None:
    """YummyAnime's times for an episode file (seconds of its clock: [start, end]) — taken when the sound finds
    nothing."""
    if not opening and not ending:
        return
    with _LOCK:
        data = read_note(folder)
        item = data["episodes"].setdefault(_key(folder, path), {})
        item["hint"] = {"opening": opening, "ending": ending, "stamp": _stamp(path)}
        _write_note(folder, data)


def skips_of(folder: Path, path: Path) -> dict:
    """{"opening": [start, end] | None, "ending": …, "how": "звук" | "YummyAnime" | "", "length": seconds | 0} of an
    episode file (its clock, seconds) — what is known now (nothing for a file changed since); its length when its
    sound was looked at (the player's bar is marked before it plays)."""
    data = read_note(folder)
    item = data["episodes"].get(_key(folder, path)) or {}
    stamp = _stamp(path)
    out = {"opening": None, "ending": None, "how": "", "length": 0.0}
    found = item.get("found") if item.get("stamp") == stamp else None
    if item.get("stamp") == stamp:
        try:
            out["length"] = max(0.0, float(item.get("length") or 0))
        except (TypeError, ValueError):
            pass
    if found and (found.get("opening") or found.get("ending")):
        out.update(opening=found.get("opening"), ending=found.get("ending"), how="звук")
        return out
    given = item.get("hint") or {}
    if given.get("stamp") == stamp and (given.get("opening") or given.get("ending")):
        out.update(opening=given.get("opening"), ending=given.get("ending"), how="YummyAnime")
    return out


def _sound_cache(folder: Path, path: Path) -> Path:
    name = hashlib.sha1(_key(folder, path).encode("utf-8")).hexdigest()[:16]
    return Path(folder) / ".lb" / "sound" / f"{name}.npz"


def waiting(series) -> list:
    """The episodes of a series not looked at since their files changed, or with fewer episodes to compare with than
    it has now (none when it has one episode)."""
    if series.single or len(series.episodes) < 2:
        return []
    data = read_note(Path(series.path))
    episodes = list(series.episodes)
    out = []
    for n, episode in enumerate(episodes):
        item = data["episodes"].get(_key(Path(series.path), Path(episode.path))) or {}
        if item.get("stamp") != _stamp(Path(episode.path)):
            out.append(episode)
        elif int(item.get("peers") or 0) < len(_peers_of(episodes, n)):   # more episodes to compare with now
            out.append(episode)
    return out


def find_series(series, *, stop=None, deadline: float | None = None, first: str = "") -> int:
    """Look at the episodes of a series that wait (``waiting``): their openings and endings by their sound, kept.
    The number looked at — less when ``deadline`` (``time.monotonic()``) came or ``stop()``.  ``first`` — the number
    of the episode the person stopped at: the ones from it on are looked at before the ones watched already."""
    import numpy as np
    folder = Path(series.path)
    episodes = list(series.episodes)
    todo = {id(e) for e in waiting(series)}
    begin = next((k for k, e in enumerate(episodes) if first and e.label == str(first)), 0)
    order = list(range(begin, len(episodes))) + list(range(begin))
    prints: dict = {}

    pictures: dict = {}
    frames = Frames(stop, folder)            # the pieces of frames read for the edges, shared by all the comparisons

    def of(k: int):
        if k not in prints:
            path = Path(episodes[k].path)
            prints[k] = sound_prints(path, stop=stop, cache=_sound_cache(folder, path))
        return prints[k]

    def picture_of(k: int):
        from core import video_merge as G
        if k not in pictures:
            path = Path(episodes[k].path)
            try:
                pictures[k] = G.prints_of(path, stop=stop, keys=True, threads=THREADS,
                                          cache=_sound_cache(folder, path).with_suffix(".keys.npz"))
            except G.MergeError as exc:
                if str(exc) == "остановлено":
                    raise InterruptedError from exc
                pictures[k] = None
        return pictures[k]
    done = 0
    for n in order:
        episode = episodes[n]
        if id(episode) not in todo:
            continue
        if (stop is not None and stop()) or (deadline is not None and time.monotonic() >= deadline):
            break
        try:
            start, mine = of(n)
            origin = _origin(Path(episode.path))
            stretches, peers, compared, ends = [], _peers_of(episodes, n), 0, {}
            for k in peers:
                try:
                    their_start, theirs = of(k)
                    their_origin = _origin(Path(episodes[k].path))
                    their_end = their_start + len(theirs) * HOP / RATE          # its clock
                    their_length = max(1.0, their_end - their_origin)

                    def where(at: float) -> float:   # a moment of the peer (its clock): a share of its length
                        return (at - their_origin) / their_length
                    # on the files' clocks: the shift of a print step plus where each one's sound begins
                    stretches += [(start + a - origin, start + b - origin, k, shift + their_start - start,
                                   where(their_start + a + shift)) for a, b, shift in common(mine, theirs)]
                    pa, pb = picture_of(n), picture_of(k)
                    if pa is not None and pb is not None and len(pa.times) and len(pb.times):
                        for a, b, shift in picture_common(pa, pb):
                            a, b = _edges(Path(episode.path), Path(episodes[k].path), a, b, shift, stop, frames)
                            if MIN_LEN <= b - a <= MAX_LEN:
                                stretches.append((a - origin, b - origin, k, shift, where(a + shift)))
                    ends[k] = their_end
                    compared += 1
                except (InterruptedError, MemoryError):
                    raise
                except Exception as exc:   # noqa: BLE001 - a peer that does not read: the others are enough
                    _LOG.info("skips: %s not compared with %s: %s: %s", episode.path, episodes[k].path,
                              type(exc).__name__, exc)
        except InterruptedError:
            break
        except MemoryError:                # not this file's fault: looked at again later
            _LOG.warning("skips of %s: out of memory", episode.path)
            break
        except Exception as exc:   # noqa: BLE001 - a file that does not read: said, the series goes on
            _LOG.info("skips of %s not found: %s: %s", episode.path, type(exc).__name__, exc)
            stretches, peers, compared, mine, start = [], _peers_of(episodes, n), 0, np.zeros(0), 0.0
        length = start + len(mine) * HOP / RATE - (origin if len(mine) else 0.0)
        found = choose(stretches, length, compared) if len(mine) else {"opening": None, "ending": None}
        ending = found.get("ending")
        if ending and length - ending[1] <= TAIL_GAP:      # the credits after it, as the others end: to the very end
            try:
                mine_end = start + len(mine) * HOP / RATE
                if any(_tail_alike(Path(episode.path), ending[1] + origin, mine_end, Path(episodes[k].path), end,
                                   frames) >= TAIL_ALIKE for k, end in ends.items()):
                    found["ending"] = [ending[0], round(length, 2)]
            except InterruptedError:
                break
        try:
            with _LOCK:
                data = read_note(folder)
                _prune(folder, data)
                item = data["episodes"].setdefault(_key(folder, Path(episode.path)), {})
                item.update(stamp=_stamp(Path(episode.path)), found=found, length=round(max(0.0, length), 2),
                            peers=len(peers))
                _write_note(folder, data)
            _UNWRITABLE.pop(str(folder), None)
        except OSError as exc:             # a folder that cannot be written: left for a while, the others go on
            _LOG.info("skips of %s not kept: %s", folder, exc)
            _UNWRITABLE[str(folder)] = time.monotonic()
            break
        done += 1
    return done


TAIL_GAP = 180.0             # seconds an ending may end before the episode's end and still be taken to its end …
TAIL_ALIKE = 0.6             # … when what is after it is this much alike to another episode's last seconds, the two
                             # put end to end (more credits — Netflix's of its dubs; a preview of the next one is not)


def _tail_alike(path_a: Path, since: float, end_a: float, path_b: Path, end_b: float, frames: Frames) -> float:
    """The share of the frames of an episode from ``since`` to its end ``end_a`` (its clock) alike to another's last
    ones, the two put end to end (``end_b`` — the other's end)."""
    import numpy as np
    from core import video_merge as G
    if end_a - since < 1.0:
        return 1.0                             # it ends with the ending
    pa = frames.get(path_a, since, end_a)
    pb = frames.get(path_b, end_b - (end_a - since) - 1, end_b)
    if pa is None or pb is None:
        return 0.0
    (ta, ba), (tb, bb) = pa, pb
    da = np.unpackbits(ba, axis=1).astype(np.int16)
    db = np.unpackbits(bb, axis=1).astype(np.int16)
    near = np.clip(np.searchsorted(tb, ta + (end_b - end_a)), 0, len(tb) - 1)
    return float((np.abs(da - db[near]).sum(1) <= G.MAX_DIST).mean()) if len(ta) else 0.0


_UNWRITABLE: dict = {}       # series folders whose note could not be written: when (``time.monotonic()``)
UNWRITABLE_WAIT = 3600.0     # seconds before such a folder is tried again


def _prune(folder: Path, data: dict) -> None:
    """Entries of files that are gone (put into an episode's MKV, removed) — out of the note."""
    for key in [k for k in data["episodes"] if not (Path(folder) / k).exists()]:
        del data["episodes"][key]


def stamp_of(path: Path) -> str:
    """What an episode file is known by here (its size and time) — before it is changed (``kept_after_merge``)."""
    return _stamp(Path(path))


def kept_after_merge(folder: Path, path: Path, before: str) -> None:
    """An episode's MKV got another voice-over's track — its picture and its first track are as they were: what was
    found in it (its opening and ending) and what was read of it (its sound's and key frames' prints) stay, under the
    file's new stamp; it is not looked at again."""
    from core import video_merge as G
    now = _stamp(Path(path))
    if not before or not now or before == now:
        return
    with _LOCK:
        data = read_note(folder)
        item = data["episodes"].get(_key(folder, Path(path)))
        if item is not None and item.get("stamp") == before:
            item["stamp"] = now
            hint = item.get("hint")
            if isinstance(hint, dict) and hint.get("stamp") == before:
                hint["stamp"] = now
            _write_note(folder, data)
    cache = _sound_cache(folder, Path(path))
    G.restamp(cache, f"{before}-{VERSION}", f"{now}-{VERSION}")
    G.restamp(cache.with_suffix(".keys.npz"), f"{before}-k{G.PRINTS_VERSION}", f"{now}-k{G.PRINTS_VERSION}")


def carry(folder: Path, sources: list, target: Path) -> None:
    """YummyAnime's times of the file a new MKV took its picture from (``sources``: the first of them that has
    some) — the MKV's now (its picture is that file's, frame for frame; another voice-over's times are of its own
    picture, not taken); the entries of the files gone — out."""
    with _LOCK:
        data = read_note(folder)
        given = None
        for source in sources:
            if not source:
                continue
            item = data["episodes"].get(_key(folder, Path(source))) or {}
            if (item.get("hint") or {}).get("opening") or (item.get("hint") or {}).get("ending"):
                given = item["hint"]
                break
        _prune(folder, data)
        if given is not None:
            item = data["episodes"].setdefault(_key(folder, Path(target)), {})
            if not ((item.get("hint") or {}).get("stamp") == _stamp(Path(target))):
                item["hint"] = {"opening": given.get("opening"), "ending": given.get("ending"),
                                "stamp": _stamp(Path(target))}
        _write_note(folder, data)


def _origin(path: Path) -> float:
    """The file's first moment on its own clock (an MPEG-TS begins at 1.4 s or so): the player counts from it."""
    import av
    try:
        with av.open(str(path)) as box:
            return float(box.start_time / av.time_base) if box.start_time else 0.0
    except (av.FFmpegError, OSError):
        return 0.0


def _in_order(every: list) -> list:
    """(v5.44.151) [(series, the episode it was stopped at — "" when never watched)] — the series watched last
    first, then the ones whose files are the newest: with one thread a long series takes an hour, and the marks are
    wanted where the person watches."""
    from core import video_library as V
    out = []
    for series in every:
        at, last = 0, ""
        try:
            keys = V.memory_keys(series)
            at = int(V.PROGRESS.last_at(keys) or 0)
            last = str((V.PROGRESS.view(keys) or {}).get("last") or "") if at else ""
        except Exception as exc:  # noqa: BLE001 - the memory of what was watched is not read: the folder's order
            _LOG.debug("skips: what was watched of %s not read: %s", series.path, exc)
        if "/" in last:                                  # an episode of a season's sub-folder: by the series' order
            last = ""
        out.append((-at, -float(series.mtime or 0.0), len(out), series, last))
    return [(series, last) for _at, _mtime, _n, series, last in sorted(out, key=lambda row: row[:3])]


def background_round(settings: dict, *, stop=None, seconds: float = 45.0) -> dict:
    """The background duty: the series of the video folder whose episodes wait, looked at for ``seconds`` at most.
    {"done": episodes looked at, "more": bool}."""
    from core import video_library
    if not available():
        return {"done": 0, "more": False}
    root = Path(video_library.video_root(settings))
    if not root.is_dir():
        return {"done": 0, "more": False}
    deadline = time.monotonic() + seconds
    done, more = 0, False
    for series, last in _in_order(video_library.LIBRARY.items(str(root))):
        if stop is not None and stop():
            return {"done": done, "more": True}
        failed_at = _UNWRITABLE.get(str(series.path))
        if failed_at is not None and time.monotonic() - failed_at < UNWRITABLE_WAIT:
            continue
        try:
            if not waiting(series):
                continue
            if time.monotonic() >= deadline:
                more = True
                break
            done += find_series(series, stop=stop, deadline=deadline, first=last)
            more = more or (bool(waiting(series)) and str(series.path) not in _UNWRITABLE)
        except Exception as exc:   # noqa: BLE001 - one series that fails does not hold the others up
            _LOG.warning("skips of %s: %s: %s", series.path, type(exc).__name__, exc)
            _UNWRITABLE[str(series.path)] = time.monotonic()
    return {"done": done, "more": more}
