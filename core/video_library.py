"""The video folder of the tab «Видео»: series and single videos, read without Qt (for the web pages).

The rules, as the owner asked for them:

* every folder at the top of the video folder is one series; the video files inside are its episodes;
* a sub-folder of a series is a group of its episodes (a voice-over, a season, extras): the series page shows its
  name as a tab over the episode buttons; files right in the series folder are the first group;
* a video file right in the video folder is a single video (a film), shown like a series of one episode;
* ``info.json`` in a series folder (``<file name>.info.json`` next to a single video) is its description — written
  by «Описание с сайта» (``core/video_meta_import.py``) or by hand; ``poster.jpg`` / ``cover.*`` / ``folder.*``
  (``<file name>-poster.*`` for a single video) is its poster.

The numbers on the episode buttons are found in the file names (``[Group] Title - 03 [1080p].mkv`` → 3, ``Серия 5``
→ 5), else the episodes are numbered in the order of their names.  Each series and episode gets an id made from its
path relative to the video folder, so links and what was watched stay the same between runs.  A scan is kept in
memory and redone at most once a minute (or on demand), as the manga library does.

What was watched (the last episode of a series, where each episode stopped) is kept in ``video_progress.json`` next
to the program settings — not in the video folder.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.video_probe import VIDEO_EXTS

_LOG = logging.getLogger("local_booru.video")

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".bmp")
POSTER_NAMES = ("poster", "cover", "folder")
INFO_NAME = "info.json"
MAX_INFO_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 3                         # sub-folders of a series looked into
_SKIP_DIRS = {"$recycle.bin", "system volume information", "@eadir", "_to_delete", ".lb"}


def video_root(settings: dict) -> str:
    """The video folder: «Настройки → Папки → Папка видео», by default ``video`` in the archive — next to its
    ``settings`` and ``output`` (``Local_Booru_Archive\\video``), not inside them."""
    chosen = str((settings or {}).get("video_root") or "").strip()
    if chosen:
        return chosen
    from core.archive_switch import open_archive
    return str(Path(open_archive()) / "video")


def natural_key(text) -> list:
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(text))]


def _id_for(root: Path, path: Path) -> str:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()
    return hashlib.md5(("video|" + rel.lower()).encode("utf-8")).hexdigest()[:12]


@dataclass
class Episode:
    id: str
    path: str
    name: str                       # the file name
    label: str                      # what its button says: the number found in the names
    group: str = ""                 # the sub-folder it is in ("" — right in the series folder)
    size: int = 0
    mtime: float = 0.0
    series_id: str = ""
    note: str = ""                  # who voiced this file (the site's note about it, info.json "files")
    site_title: str = ""            # its name on the site («Файл 1»)

    @property
    def ext(self) -> str:
        return Path(self.name).suffix.lower()


@dataclass
class Series:
    id: str
    title: str
    path: str
    single: bool = False            # one video file right in the video folder
    episodes: list = field(default_factory=list)      # Episode, in watching order
    groups: list = field(default_factory=list)        # [(group name, [Episode])]
    mtime: float = 0.0
    poster: str = ""                # a picture file, or "" (the first episode's frame is shown)
    info: dict = field(default_factory=dict)
    folder_title: str = ""          # the folder's (or file's) own name

    def episode(self, episode_id: str) -> Episode | None:
        return next((e for e in self.episodes if e.id == episode_id), None)

    def neighbours(self, episode: Episode) -> tuple[Episode | None, Episode | None]:
        """The episodes before and after it — in its group first, then the next group's first."""
        index = next((i for i, e in enumerate(self.episodes) if e.id == episode.id), -1)
        if index < 0:
            return None, None
        before = self.episodes[index - 1] if index > 0 else None
        after = self.episodes[index + 1] if index + 1 < len(self.episodes) else None
        return before, after

    @property
    def rating(self) -> int:
        """The person's own mark, 1…10 (``info.json`` "my_rating"); 0 — not rated."""
        try:
            value = int(self.info.get("my_rating") or 0)
        except (TypeError, ValueError):
            return 0
        return value if 1 <= value <= 10 else 0

    @property
    def alt_titles(self) -> list:
        value = self.info.get("alt_titles") or []
        if isinstance(value, str):                      # written by hand as one line
            value = [value]
        return [str(x) for x in value if str(x).strip()] if isinstance(value, list) else []

    def words(self) -> str:
        """Everything a search on the tab looks in."""
        parts = [self.title, self.folder_title, *self.alt_titles]
        for key in ("genres", "tags", "studio", "voice", "director", "year", "type"):
            value = self.info.get(key)
            parts += value if isinstance(value, list) else [value or ""]
        return " ".join(str(p) for p in parts).lower().replace("ё", "е")


# ── episode numbers ──────────────────────────────────────────────────────────────
_NOISE = re.compile(
    r"\[[0-9a-f]{8}\]|\b\d{3,4}[pi]\b|\b[xh]\.?26[45]\b|\bhevc\b|\b(?:8|10)[- ]?bit\b|\baac(?:\d\.\d)?\b|\bflac\b|"
    r"\bdd[p+]?\d\.\d\b|\b\d{2,3}fps\b|\b\d{3,4}x\d{3,4}\b|\b(?:19|20)\d{2}\b|\bmp4\b|\bv\d\b|\b\d\.\d\b", re.I)
_MARKED = re.compile(r"(?:\bep(?:isode)?|\be|серия|серии|эпизод|эп|\bs\d{1,2}e)\.?\s*[-_ #№]?\s*(\d{1,4})\b", re.I)


def _numbers(stem: str) -> tuple[int | None, list[int]]:
    clean = _NOISE.sub(" ", stem.replace("_", " "))
    marked = _MARKED.search(clean)
    runs = [int(x) for x in re.findall(r"\d+", clean)]
    return (int(marked.group(1)) if marked else None), runs


def episode_labels(stems: list[str]) -> list[str]:
    """The number of each episode found in the names, or 1, 2, 3… in the given order."""
    if not stems:
        return []
    infos = [_numbers(s) for s in stems]
    marked = [m for m, _runs in infos]
    if all(m is not None for m in marked) and len(set(marked)) == len(marked):
        return [str(m) for m in marked]
    runs = [r for _m, r in infos]
    if len(stems) > 1 and all(runs):
        for from_end in (1, 2, 3):                 # the episode number is usually the last number of the name
            values = [r[-from_end] if len(r) >= from_end else None for r in runs]
            if None not in values and len(set(values)) == len(values) and max(values) < 2000:
                return [str(v) for v in values]
    if len(stems) == 1 and marked[0] is not None:
        return [str(marked[0])]
    return [str(n) for n in range(1, len(stems) + 1)]


def _ordered(files: list[Path], folder: Path) -> list[tuple[Path, str]]:
    files = sorted(files, key=lambda p: natural_key(p.relative_to(folder).as_posix()))
    labels = episode_labels([p.stem for p in files])
    pairs = list(zip(files, labels))
    if all(label.isdigit() for label in labels):
        pairs.sort(key=lambda pair: (int(pair[1]), natural_key(pair[0].name)))
    return pairs


# ── reading the folder ─────────────────────────────────────────────────────────────
def _read_info(path: Path) -> dict:
    try:
        if not path.is_file() or path.stat().st_size > MAX_INFO_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        _LOG.warning("video description %s unreadable: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _find_poster(candidates: list[Path]) -> str:
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return str(candidate)
        except OSError:
            continue
    return ""


def series_poster_names(folder: Path) -> list[Path]:
    return [folder / f"{name}{ext}" for name in POSTER_NAMES for ext in IMAGE_EXTS]


def single_poster_names(file: Path) -> list[Path]:
    return ([file.with_name(f"{file.stem}-poster{ext}") for ext in IMAGE_EXTS]
            + [file.with_name(f"{file.stem}.poster{ext}") for ext in IMAGE_EXTS]
            + [file.with_name(f"{file.stem}{ext}") for ext in IMAGE_EXTS])


def single_info_path(file: Path) -> Path:
    return file.with_name(file.name + ".info.json")


def _video_files(folder: Path, depth: int = 0) -> list[Path]:
    out = []
    try:
        entries = list(os.scandir(folder))
    except OSError as exc:
        _LOG.warning("video folder %s unreadable: %s", folder, exc)
        return out
    for entry in entries:
        name = entry.name
        if name.startswith("."):
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                if depth < MAX_DEPTH and name.lower() not in _SKIP_DIRS:
                    out += _video_files(Path(entry.path), depth + 1)
            elif entry.is_file() and Path(name).suffix.lower() in VIDEO_EXTS:
                out.append(Path(entry.path))
        except OSError:
            continue
    return out


def _episode(root: Path, path: Path, label: str, group: str, series_id: str) -> Episode:
    try:
        st = path.stat()
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, 0.0
    return Episode(id=_id_for(root, path), path=str(path), name=path.name, label=label, group=group, size=size,
                   mtime=mtime, series_id=series_id)


def _apply_files(series: Series) -> None:
    """The site's notes about each of its files (info.json "files": the name of the file on the site, its title
    there and who voiced it) go to the local files of the same name.  The files right in the series folder, when
    every one of them is one of the site's, go in the site's order, numbered as the site numbers them («Файл 3» → 3):
    voice-overs mixed in one folder.  A sub-folder (one voice-over — «Скачать с сайта» makes them) keeps the
    numbers of its file names."""
    files = series.info.get("files")
    if not isinstance(files, list):
        return
    by_name = {}
    for index, item in enumerate(files):
        if isinstance(item, dict) and str(item.get("name") or "").strip():
            by_name[str(item["name"]).strip().lower()] = (index, item)
    for episode in series.episodes:
        found = by_name.get(episode.name.lower())
        if found:
            episode.note = str(found[1].get("note") or "")
            episode.site_title = str(found[1].get("title") or "")
    regrouped = []
    for group, episodes in series.groups:
        if not group and episodes and all(e.name.lower() in by_name for e in episodes):
            episodes = sorted(episodes, key=lambda e: by_name[e.name.lower()][0])
            for e in episodes:
                index, item = by_name[e.name.lower()]
                number = re.search(r"(\d+)\s*$", str(item.get("title") or ""))
                e.label = number.group(1) if number else str(index + 1)
        regrouped.append((group, episodes))
    series.groups = regrouped
    series.episodes = [e for _group, episodes in regrouped for e in episodes]


def _title(info: dict, fallback: str) -> str:
    title = info.get("title")
    return str(title).strip() if isinstance(title, str) and title.strip() else fallback


def read_series(root: Path, folder: Path) -> Series | None:
    files = _video_files(folder)
    if not files:
        return None
    series_id = _id_for(root, folder)
    info = _read_info(folder / INFO_NAME)
    groups: dict[str, list[Path]] = {}
    for path in files:
        rel = path.relative_to(folder).parts
        groups.setdefault(rel[0] if len(rel) > 1 else "", []).append(path)
    names = sorted(groups, key=lambda g: (g != "", natural_key(g)))
    series = Series(id=series_id, title=_title(info, folder.name), path=str(folder), info=info,
                    folder_title=folder.name, poster=_find_poster(series_poster_names(folder)))
    for group in names:
        base = folder / group if group else folder
        episodes = [_episode(root, path, label, group, series_id) for path, label in _ordered(groups[group], base)]
        series.groups.append((group, episodes))
        series.episodes += episodes
    try:
        series.mtime = max([folder.stat().st_mtime] + [e.mtime for e in series.episodes])
    except OSError:
        series.mtime = max((e.mtime for e in series.episodes), default=0.0)
    _apply_files(series)
    return series


def read_single(root: Path, file: Path) -> Series:
    info = _read_info(single_info_path(file))
    series_id = _id_for(root, file)
    episode = _episode(root, file, "1", "", series_id)
    series = Series(id=series_id, title=_title(info, file.stem), path=str(file), single=True, episodes=[episode],
                    groups=[("", [episode])], mtime=episode.mtime, info=info, folder_title=file.stem,
                    poster=_find_poster(single_poster_names(file)))
    _apply_files(series)
    return series


def scan(root) -> list[Series]:
    root = Path(str(root or ""))
    if not str(root) or not root.is_dir():
        return []
    out = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: natural_key(p.name))
    except OSError as exc:
        _LOG.warning("video folder %s unreadable: %s", root, exc)
        return []
    for entry in entries:
        if entry.name.startswith(".") or entry.name.lower() in _SKIP_DIRS:
            continue
        try:
            if entry.is_dir():
                series = read_series(root, entry)
                if series is not None:
                    out.append(series)
            elif entry.is_file() and entry.suffix.lower() in VIDEO_EXTS:
                out.append(read_single(root, entry))
        except OSError as exc:            # one unreadable entry must not hide the whole folder
            _LOG.warning("video entry %s skipped: %s", entry, exc)
    return out


class Library:
    """The scanned video folder, kept in memory (the manga library's rules: the first request scans and waits;
    later, when the scan is older than ``ttl``, pages get the kept list at once while a thread scans again)."""

    def __init__(self, ttl: float = 60.0):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._root = ""
        self._at = 0.0
        self._rescanning = False
        self._items: list[Series] = []
        self._by_id: dict[str, Series] = {}
        self._episodes: dict[str, Episode] = {}

    def _store(self, key: str, items: list) -> None:
        by_id, episodes = {}, {}
        for series in items:
            by_id[series.id] = series
            for episode in series.episodes:
                episodes[episode.id] = episode
        with self._lock:
            self._root, self._at, self._items, self._by_id, self._episodes = key, time.time(), items, by_id, episodes

    def _rescan(self, key: str) -> None:
        try:
            with self._scan_lock:
                self._store(key, scan(key))
        finally:
            with self._lock:
                self._rescanning = False

    def items(self, root, *, refresh: bool = False) -> list[Series]:
        key = str(root or "")
        with self._lock:
            if key == self._root and self._at > 0 and not refresh:
                if time.time() - self._at >= self.ttl and not self._rescanning:
                    self._rescanning = True
                    threading.Thread(target=self._rescan, args=(key,), name="video-rescan", daemon=True).start()
                return self._items
        with self._scan_lock:
            with self._lock:
                if key == self._root and self._at > 0 and not refresh:
                    return self._items
            self._store(key, scan(key))
        with self._lock:
            return self._items

    def invalidate(self) -> None:
        with self._lock:
            self._at = 0.0

    def get(self, root, series_id: str) -> Series | None:
        self.items(root)
        with self._lock:
            return self._by_id.get(str(series_id))

    def episode(self, root, episode_id: str) -> tuple[Series | None, Episode | None]:
        self.items(root)
        with self._lock:
            episode = self._episodes.get(str(episode_id))
            return (self._by_id.get(episode.series_id), episode) if episode else (None, None)


LIBRARY = Library()


# ── what was watched ─────────────────────────────────────────────────────────────
WATCHED_PART = 0.9                    # an episode watched this far counts as watched
# «Продолжить просмотр 12:34» (the page's player and the full-size one): offered when the play is at least
# RESUME_GAP seconds before the furthest place reached — and not when that place is in the last RESUME_END seconds
RESUME_GAP = 15.0
RESUME_END = 10.0


class Progress:
    """Where each episode stopped and the last episode of each series, in one small JSON file — read and written by
    ``core.file_safety`` (rule 14): a file held open for a moment (an antivirus) is not taken for an empty one, so
    what was watched is never saved over with only the last episode; a damaged one is kept aside and its last good
    copy (``.bak``) read instead."""

    def __init__(self, path: Path | None = None):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict | None = None

    def _file(self) -> Path:
        if self._path is not None:
            return self._path
        from core.paths import SETTINGS_DIR
        return Path(SETTINGS_DIR) / "video_progress.json"

    def _load(self) -> dict | None:
        """The kept data; None while the file cannot be opened (it is read again next time, nothing is saved)."""
        if self._data is None:
            from core.file_safety import read_json_kept
            try:
                data = read_json_kept(self._file(), dict, what="video progress")
            except OSError as exc:
                _LOG.warning("video progress cannot be read now (%s)", exc)
                return None
            data = data if isinstance(data, dict) else {}
            for key in ("episodes", "series"):
                if not isinstance(data.get(key), dict):
                    data[key] = {}
            self._data = data
        return self._data

    def _part(self, key: str) -> dict:
        data = self._load()
        return data[key] if data is not None else {}

    def episode(self, episode_id: str) -> dict:
        with self._lock:
            entry = self._part("episodes").get(str(episode_id))
            return dict(entry) if isinstance(entry, dict) else {}

    def episodes(self, ids) -> dict:
        with self._lock:
            known = self._part("episodes")
            return {i: dict(known[i]) for i in ids if isinstance(known.get(i), dict)}

    def _series(self, series_id: str) -> dict:
        entry = self._part("series").get(str(series_id))
        return entry if isinstance(entry, dict) else {}

    def last(self, series_id: str) -> str:
        with self._lock:
            return str(self._series(series_id).get("last") or "")

    def last_at(self, series_id: str) -> int:
        """When an episode of the series was watched last (0 — never)."""
        with self._lock:
            try:
                return int(self._series(series_id).get("at") or 0)
            except (TypeError, ValueError):
                return 0

    def record(self, series_id: str, episode_id: str, position: float, duration: float, *, ended: bool = False) -> dict:
        position = max(0.0, float(position or 0))
        duration = max(0.0, float(duration or 0))
        with self._lock:
            data = self._load()
            known = (data or {}).get("episodes", {}).get(episode_id)
            entry = dict(known) if isinstance(known, dict) else {}
            done = bool(entry.get("done")) or ended or (duration > 0 and position >= duration * WATCHED_PART)
            entry.update({"pos": round(position, 1), "dur": round(duration, 1), "done": done, "at": int(time.time())})
            if ended:
                entry["pos"] = 0.0
            if data is None:                   # the file is held by someone: this place is not kept, the rest is
                return dict(entry)
            data["episodes"][episode_id] = entry
            kept = data["series"].get(series_id)
            series = dict(kept) if isinstance(kept, dict) else {}       # its voice-over and subtitles stay
            series.update({"last": episode_id, "at": int(time.time())})
            data["series"][series_id] = series
            self._save(data)
            return dict(entry)

    def tracks(self, series_id: str) -> dict:
        """The voice-over and the subtitles chosen for the series (``core/video_tracks``): {"audio": {"name", "title",
        "lang"}, "subtitle": {… "file"} or {"off": True}}."""
        with self._lock:
            entry = self._series(series_id).get("tracks")
            return {k: dict(v) for k, v in entry.items() if isinstance(v, dict)} if isinstance(entry, dict) else {}

    def set_tracks(self, series_id: str, kind: str, value: dict | None) -> None:
        """Remember the voice-over (``kind`` "audio") or the subtitles ("subtitle") chosen for the series."""
        if kind not in ("audio", "subtitle"):
            return
        with self._lock:
            data = self._load()
            if data is None:                   # the file is held by someone: the choice is not kept this time
                return
            kept = data["series"].get(str(series_id))
            series = dict(kept) if isinstance(kept, dict) else {}
            tracks = dict(series.get("tracks") or {}) if isinstance(series.get("tracks"), dict) else {}
            if value:
                tracks[kind] = {k: str(v)[:120] if not isinstance(v, bool) else v for k, v in value.items()
                                if k in ("name", "title", "lang", "file", "off")}
            else:
                tracks.pop(kind, None)
            series["tracks"] = tracks
            data["series"][str(series_id)] = series
            self._save(data)

    def _save(self, data: dict) -> None:
        from core.file_safety import write_json_kept
        try:
            write_json_kept(self._file(), json.dumps(data, ensure_ascii=False, separators=(",", ":")), dict)
        except OSError as exc:
            _LOG.warning("video progress not saved: %s", exc)


PROGRESS = Progress()


# ── pictures ─────────────────────────────────────────────────────────────────────
def episode_frame(episode: Episode, width: int, height: int) -> Path | None:
    """A frame of the episode (kept in the thumbnail cache)."""
    from core.image_safe import _cache_name, _video_thumbnail_path, cached_thumbnail_path
    path = Path(episode.path)
    made = cached_thumbnail_path(path, width, height)
    if not made:
        out = _cache_name(path, width, height)
        made = _video_thumbnail_path(path, out, width, height)
    return Path(made) if made and Path(made).is_file() else None


SLIDES = 6                     # frames of an episode shown one after another while it is chosen and not played


def episode_slide(episode: Episode, n: int, width: int, height: int) -> Path | None:
    """The ``n``-th (0…SLIDES-1) of :data:`SLIDES` frames of the episode — at (n+1)/(SLIDES+1) of its length —,
    kept in the thumbnail cache beside its poster frame (None — the video gives no picture there)."""
    from core.image_safe import _TOUCH_EVERY, _cache_name, _remember_thumb_source, _temp_name, swap_in
    if not 0 <= int(n) < SLIDES:
        return None
    path = Path(episode.path)
    base = _cache_name(path, width, height)            # the file's own size and time are in the name
    out = base.with_name(f"{base.stem}-slide{n}of{SLIDES}.jpg")
    try:
        st = out.stat()
        if st.st_size > 0:
            if time.time() - st.st_mtime > _TOUCH_EVERY:
                out.touch()                            # «used lately»: the cache trimming keeps it
            return out
    except OSError:
        pass
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temp_name(out)
    try:
        import cv2
        from PIL import Image

        from core.image_safe import video_frame, video_frame_count
        frames = video_frame_count(path)
        if frames <= SLIDES * 2:                       # too short for frames of its own (or its length unknown)
            return None
        frame = video_frame(path, int(frames * (n + 1) / (SLIDES + 1)))
        if frame is None:
            return None
        picture = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        picture.thumbnail((width, height), Image.Resampling.LANCZOS)
        picture.save(tmp, "JPEG", quality=88, optimize=True)
    except Exception as exc:  # OpenCV missing, or the file unreadable for it: the poster frame stays alone
        _LOG.debug("slide %s of %s not made: %s", n, path, exc)
        tmp.unlink(missing_ok=True)
        return None
    if not swap_in(tmp, out):                          # another request made it a moment earlier: that one is used
        return out if out.is_file() else None
    _remember_thumb_source(out, path)
    return out


def poster_picture(series: Series, width: int, height: int) -> Path | None:
    """The series' poster at this size, or a frame of its first episode."""
    if series.poster:
        from core.image_safe import safe_thumbnail_path
        made = safe_thumbnail_path(series.poster, width, height)
        if made and Path(made).is_file():
            return Path(made)
    return episode_frame(series.episodes[0], width, height) if series.episodes else None
