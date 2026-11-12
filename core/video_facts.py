"""What the files of a series say of themselves — how long each is, the titles of its sound tracks —, kept in the
series folder (``.lb/facts.json``) by each file's size and time.

(v5.44.151) The page of a series asks this of every file of it («Серии»: an episode's variants are its files and the
tracks inside them): 170 episodes are 170 files opened — at every start of the program, for the answers lived in
memory only (the owner: «некоторые страницы открываются по десять лет»; a stream kept as «Серия 01.ts» took 0.76 s a
file on his computer, his «Чёрный клевер» — two minutes).  Kept here, a file is opened once in its life: the page of
a long series opens at once, whether the disk is awake or not.  A folder that cannot be written (read-only, another
computer's) keeps the answers in memory, as before; a file that could not be read is asked again a little later and
its empty answer is not kept."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

_LOG = logging.getLogger("local_booru.video")

NOTE = "facts.json"
VERSION = 1
AGAIN = 600.0                # seconds before a file that told nothing is asked again
UNWRITABLE_WAIT = 3600.0     # seconds before a folder whose note could not be written is tried again
KEPT_FOLDERS = 400           # series whose facts stay in memory

_LOCK = threading.Lock()
_STATES: dict = {}           # the series' path -> its state (``_state``)


def note_path(folder: Path) -> Path:
    return Path(folder) / ".lb" / NOTE


def _state(key: str) -> dict:
    with _LOCK:
        state = _STATES.pop(key, None)
        if state is None:
            state = {"lock": threading.Lock(), "read": None, "files": {}, "failed": 0.0}
        _STATES[key] = state                             # the one used last is the last to go
        while len(_STATES) > KEPT_FOLDERS:
            _STATES.pop(next(iter(_STATES)))
        return state


def _key(folder: Path, path: str) -> str:
    try:
        return Path(path).relative_to(folder).as_posix()
    except ValueError:
        return Path(path).name


def _ask(path: str) -> dict:
    """{"length", "tracks"} of a file, read from it now ("weak" — it told nothing: asked again later)."""
    from core import video_merge, video_probe
    length, tracks, read = 0.0, [], False
    try:
        info = video_probe.probe(path)
        if info is not None and info.duration > 0:
            length, read = round(float(info.duration), 3), True
    except Exception as exc:  # noqa: BLE001 - a file no reader takes: its length stays unknown
        _LOG.debug("the length of %s not read: %s", path, exc)
    try:
        if video_merge.available() and os.path.isfile(path):
            tracks = [str(t or "").strip() for t in video_merge.tracks_in(Path(path))]
            read = read or bool(tracks)
    except Exception as exc:  # noqa: BLE001 - a file PyAV does not read: one variant, the file itself
        _LOG.debug("the tracks of %s not read: %s", path, exc)
    out = {"length": length, "tracks": tracks}
    if not read:
        out["weak"] = time.monotonic()
    return out


def _load(state: dict, folder: Path) -> None:
    """The note of the folder into the state — when it is another than the one read last."""
    place = note_path(folder)
    try:
        stat = place.stat()
        mark = (stat.st_size, stat.st_mtime_ns)
    except OSError:
        return
    if mark == state["read"]:
        return
    try:
        data = json.loads(place.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:                 # cut short, not ours: made anew as the files are asked
        _LOG.info("video facts of %s not read: %s", folder, exc)
        state["read"] = mark
        return
    files = data.get("files") if isinstance(data, dict) and data.get("version") == VERSION else None
    if isinstance(files, dict):
        weak = {k: v for k, v in state["files"].items() if isinstance(v, dict) and v.get("weak")}
        state["files"] = {k: v for k, v in files.items() if isinstance(v, dict)}
        for key, item in weak.items():
            state["files"].setdefault(key, item)
    state["read"] = mark


def _save(state: dict, folder: Path) -> None:
    if state["failed"] and time.monotonic() - state["failed"] < UNWRITABLE_WAIT:
        return
    from core.file_safety import atomic_write_bytes
    place = note_path(folder)
    kept = {k: {"stamp": v.get("stamp"), "length": v.get("length") or 0.0, "tracks": v.get("tracks") or []}
            for k, v in state["files"].items() if not v.get("weak")}
    try:
        atomic_write_bytes(place, json.dumps({"version": VERSION, "files": kept}, ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8"))
        stat = place.stat()
        state["read"], state["failed"] = (stat.st_size, stat.st_mtime_ns), 0.0
    except OSError as exc:                               # a folder that cannot be written: in memory, tried later
        _LOG.info("video facts of %s not kept: %s", folder, exc)
        state["failed"] = time.monotonic()


def of(series) -> dict:
    """{episode id: {"length": seconds (0.0 — the file does not say), "tracks": [the titles of its sound tracks]}} of
    every file of a series (``core.video_library.Series``) — from the folder's note; a file new or changed since is
    read now, and the note is written again."""
    single = bool(getattr(series, "single", False))
    folder = Path(series.path).parent if single else Path(series.path)
    state = _state(str(series.path))
    out: dict = {}
    with state["lock"]:
        if not single:
            _load(state, folder)
        files, changed, seen = state["files"], False, set()
        now = time.monotonic()
        for episode in series.episodes:
            key = _key(folder, episode.path)
            stamp = f"{int(episode.size)}-{int(episode.mtime)}"
            seen.add(key)
            item = files.get(key)
            stale = not isinstance(item, dict) or item.get("stamp") != stamp
            if stale or (item.get("weak") and now - float(item["weak"]) >= AGAIN):
                item = files[key] = {"stamp": stamp, **_ask(episode.path)}
                changed = changed or not item.get("weak")
            tracks = item.get("tracks")
            try:
                length = max(0.0, float(item.get("length") or 0.0))
            except (TypeError, ValueError):
                length = 0.0
            out[episode.id] = {"length": length, "tracks": [str(t or "") for t in tracks] if isinstance(tracks, list) else []}
        gone = [k for k in files if k not in seen]
        for key in gone:
            del files[key]
        if (changed or gone) and not single:             # one video right in the video folder: nothing kept beside it
            _save(state, folder)
    return out


def forget() -> None:
    """Nothing kept in memory (the checks; the notes of the folders stay)."""
    with _LOCK:
        _STATES.clear()
