"""The video folder of the tab «Видео»: series and single videos, read without Qt (for the web pages).

The rules, as the owner asked for them:

* every folder at the top of the video folder is one series; the video files inside are its episodes;
* a sub-folder of a series is a group of its files: (v5.44.121) a voice-over or subtitles («Озвучка …», «Субтитры …»,
  «Дубляж …» — :func:`variant_group`) — variants of the series' own episodes (:class:`Unit`: «16 files» of 8
  episodes are 8 episodes); another (a season, extras) — episodes of its own;
* a video file right in the video folder is a single video (a film), shown like a series of one episode;
* ``info.json`` in a series folder (``<file name>.info.json`` next to a single video) is its description — written
  by «Описание с сайта» (``core/video_meta_import.py``) or by hand; ``poster.jpg`` / ``cover.*`` / ``folder.*``
  (``<file name>-poster.*`` for a single video) is its poster.

The numbers on the episode buttons are found in the file names (``[Group] Title - 03 [1080p].mkv`` → 3, ``Серия 5``
→ 5), else the episodes are numbered in the order of their names.  Each series and episode gets an id made from its
path relative to the video folder, so links and what was watched stay the same between runs.  A scan is kept in
memory and redone at most once a minute (or on demand), as the manga library does.

What was watched (the last episode of a series, where each episode stopped) is kept in ``video_progress.json`` next
to the program settings — not in the video folder; (v5.44.121) one memory (:class:`Progress`) for an episode of the
folder, the same episode watched online (a title of a site — «Смотреть онлайн») and a long video of the library.
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
class Unit:
    """(v5.44.121) One episode of a series as the person counts them: its number and its files — the file right in
    the series folder and the files of the same number in the sub-folders of voice-overs and subtitles («Озвучка …»,
    «Субтитры …» — :func:`variant_group`): variants of one episode, not episodes of their own.  A sub-folder of
    another kind (a season, extras) keeps its episodes: their key is «<sub-folder>/<number>»."""
    key: str
    label: str
    group: str = ""                 # "" — the series' own episodes; else the sub-folder of a season, extras…
    files: list = field(default_factory=list)       # Episode: the series folder's first, then the variants' folders

    @property
    def number(self) -> float:
        try:
            return float(self.label)
        except ValueError:
            return 10.0 ** 6

    @property
    def name(self) -> str:
        """«Серия 5», «Сезон 2 · серия 3»."""
        return f"{self.group} · серия {self.label}" if self.group else f"Серия {self.label}"


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
    _units: list | None = field(default=None, repr=False, compare=False)

    def episode(self, episode_id: str) -> Episode | None:
        return next((e for e in self.episodes if e.id == episode_id), None)

    def units(self) -> list:
        """(v5.44.121) The episodes as the person counts them (:class:`Unit`): «Озвучка …» and «Субтитры …» are
        variants of the series' own episodes — «16 файлов» of 8 episodes are 8 episodes."""
        if self._units is None:
            self._units = make_units(self)
        return self._units

    def unit(self, key: str):
        return next((u for u in self.units() if u.key == str(key)), None)

    def unit_of(self, episode: Episode):
        """The episode (:class:`Unit`) a file is a variant of."""
        return next((u for u in self.units() if any(f.id == episode.id for f in u.files)), None)

    def unit_neighbours(self, unit) -> tuple:
        units = self.units()
        index = next((i for i, u in enumerate(units) if u.key == unit.key), -1)
        if index < 0:
            return None, None
        return (units[index - 1] if index > 0 else None), (units[index + 1] if index + 1 < len(units) else None)

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


# ── (v5.44.121) the episodes as the person counts them ────────────────────────────────────────────────────────────
# a sub-folder named so is a voice-over or subtitles of the series' episodes («Скачать с сайта» makes them: «Озвучка
# AniLibria», «Субтитры Crunchyroll», «Дубляж …»; Hentasis' own spelling «Озучка от …»), not episodes of its own
VARIANT_WORDS = ("озвучка", "озучка", "дубляж", "субтитры", "оригинал")


def variant_group(name: str) -> bool:
    low = str(name or "").strip().casefold()
    return bool(low) and low.startswith(VARIANT_WORDS) and "спецвыпуск" not in low


def make_units(series: Series) -> list:
    """The series' episodes (:class:`Unit`): the files of one number in the series folder and in its voice-overs' and
    subtitles' sub-folders are one episode; another sub-folder (a season) — episodes of its own, after them."""
    if series.single:
        return [Unit(key="1", label="1", files=list(series.episodes))]
    own: dict = {}
    order: list = []
    others: list = []
    for group, episodes in series.groups:
        if not group or variant_group(group):
            for episode in episodes:
                unit = own.get(episode.label)
                if unit is None:
                    unit = own[episode.label] = Unit(key=episode.label, label=episode.label)
                    order.append(unit)
                unit.files.append(episode)
        else:
            seen: dict = {}
            for episode in episodes:
                unit = seen.get(episode.label)
                if unit is None:
                    unit = seen[episode.label] = Unit(key=f"{group}/{episode.label}", label=episode.label, group=group)
                    others.append(unit)
                unit.files.append(episode)
    for unit in order:                       # the series folder's file first, then the variants' in the folders' order
        unit.files.sort(key=lambda e: (e.group != "", natural_key(e.group)))
    order.sort(key=lambda u: (u.number, natural_key(u.label)))
    return order + others


def series_key(series: Series) -> str:
    """(v5.44.121) Whose memory a series' episodes are watched in: a series whose description came from a page the
    program shows online (YummyAnime, Hentasis — «Описание с сайта», ``info.json`` "source_url") is that title —
    «yummy:<alias>», as «Смотреть» has it: an episode watched online before it was downloaded is watched after it too;
    another — «folder:<id>»."""
    source = str((series.info or {}).get("source_url") or "").strip()
    if source:
        from core import video_watch
        try:
            key = video_watch.key_of(source)
        except Exception as exc:  # noqa: BLE001 - an odd address (the public copy's stand-in): the folder's own
            _LOG.debug("series key of %s: %s", source, exc)
            key = ""
        if key:
            return key
    linked = PROGRESS.linked().get(series.id)          # (v5.44.121) the folder «Скачать» put a site's title in
    return linked or f"folder:{series.id}"


def memory_keys(series: Series) -> list:
    """The memory's keys of a series: its own (:func:`series_key`) first, then the folder's, read with it (what was
    watched before the series had its site)."""
    key = series_key(series)
    folder = f"folder:{series.id}"
    return [key] if key == folder else [key, folder]


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

    def by_key(self, root, key: str) -> Series | None:
        """(v5.44.121) The series whose memory key (:func:`series_key`) is ``key`` — a title of a site downloaded or
        described here («yummy:<alias>»), or «folder:<id>»."""
        key = str(key or "")
        if key.startswith("folder:"):
            return self.get(root, key[len("folder:"):])
        return next((s for s in self.items(root) if not s.single and series_key(s) == key), None)


LIBRARY = Library()


# ── what was watched: (v5.44.121) one memory for the folder, the sites and the long videos ─────────────────────────
WATCHED_PART = 0.9                    # an episode watched this far counts as watched…
WATCHED_TAIL = 120.0                  # …or this many seconds before its end (its credits), when it is longer than twice that
# «Продолжить просмотр 12:34» (the page's player and the full-size one): offered when the play is at least
# RESUME_GAP seconds before the furthest place reached — and not when that place is in the last RESUME_END seconds
RESUME_GAP = 15.0
RESUME_END = 10.0
KEEP_TITLES = 3000                    # series (and long videos) remembered — the oldest forgotten first
KEEP_PLACES = 400                     # places of episodes of one series
KEEP_WATCHED = 2000
WATCH_FILE = "video_watch.json"       # «Смотреть»'s memory of v5.44.106–5.44.120: read into this one once, left as it is


def resume_from(at: float, length: float) -> float:
    """Where a play goes on from: the place kept — but the very start or the end of it: anew."""
    at, length = max(0.0, float(at or 0)), max(0.0, float(length or 0))
    if at < 5 or (length and at > length - min(10.0, length * 0.1)):
        return 0.0
    return at


def watched_at(at: float, length: float, *, ended: bool = False, ending: float = 0.0) -> bool:
    """Played to its end, almost (:data:`WATCHED_PART`, :data:`WATCHED_TAIL`), or past the start of its ending."""
    at, length, ending = max(0.0, float(at or 0)), max(0.0, float(length or 0)), max(0.0, float(ending or 0))
    return bool(ended) or (length > 0 and (at >= length * WATCHED_PART
                                           or (length > 2 * WATCHED_TAIL and at >= length - WATCHED_TAIL))) \
        or (ending > 0 and at >= ending and (not length or ending < length))


def _copy(value):
    return json.loads(json.dumps(value)) if value is not None else None


class Progress:
    """(v5.44.121) The one memory of what was watched — an episode of a series of the video folder, the same episode
    watched online («Смотреть онлайн», a site's title), a long video of the library — ``video_progress.json`` next to
    the program settings: {"titles": {key: item}}.  A key — a series (:func:`series_key`: «yummy:<alias>» for a title
    of a site, the folder's too when its description came from there; «folder:<id>»; «hentasis:<n>»), a long video
    («card:<md5>»).  An item — what «Смотреть» kept before (``video_watch.json``): «last» (the episode watched last),
    «at» and «length» (its place), «done», «watched» [episodes], «places» {episode: [at, length]}, «seen» {episode: when
    watched}, «when», «name»; the choices of the series: «voice» (the voice-over, a variant of every episode), «tracks»
    (the voice-over and the subtitles inside a file), «dub», «player», «quality» (a site's), «online».  An episode is
    its number («5»; «<season>/<number>» — a sub-folder of another kind), the same in the folder and on the site.

    What was kept before is not lost: the notes of v5.18–5.44.120 by the file of an episode («episodes», «series»)
    stay in the file and go to their series' item when it is first looked at (:meth:`adopt`); ``video_watch.json`` is
    read in once (:meth:`_take_watch`) and left where it is.  Read and written by ``core.file_safety`` (rule 14): a
    file held open for a moment (an antivirus) is not taken for an empty one — nothing is saved over it then; a damaged
    one is kept aside and its last good copy (``.bak``) read instead."""

    def __init__(self, path: Path | None = None):
        self._path = path
        self._lock = threading.RLock()
        self._data: dict | None = None
        self._links: dict | None = None            # {folder id: key} (:meth:`link`)

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
            for key in ("titles", "episodes", "series", "moved"):
                if not isinstance(data.get(key), dict):
                    data[key] = {}
            if not isinstance(data.get("imports"), list):
                data["imports"] = []
            self._data = data
            self._take_watch(data)
        return self._data

    def _take_watch(self, data: dict) -> None:
        """«Смотреть»'s own memory of before (``video_watch.json`` beside this one) — read in once: its titles join
        these (an episode watched in either stays watched; a place — the later one), the file is left as it was."""
        if WATCH_FILE in data["imports"]:
            return
        source = self._file().with_name(WATCH_FILE)
        if not source.is_file():
            return
        from core.file_safety import read_json_kept
        try:
            old = read_json_kept(source, dict, what=WATCH_FILE) or {}
        except OSError as exc:                # held for a moment: read in next time
            _LOG.warning("%s not read now: %s", WATCH_FILE, exc)
            return
        titles = old.get("titles") if isinstance(old.get("titles"), dict) else {}
        for key, item in titles.items():
            if isinstance(item, dict):
                data["titles"][str(key)] = _merged([data["titles"].get(str(key)), item]) \
                    if isinstance(data["titles"].get(str(key)), dict) else _copy(item)
        data["imports"].append(WATCH_FILE)
        self._save(data)
        _LOG.info("%s: %d series read into the one memory of what was watched", WATCH_FILE, len(titles))

    def _save(self, data: dict) -> None:
        from core.file_safety import write_json_kept
        self._links = None
        try:
            write_json_kept(self._file(), json.dumps(data, ensure_ascii=False, separators=(",", ":")), dict)
        except OSError as exc:
            _LOG.warning("video progress not saved: %s", exc)

    def forget_cache(self) -> None:
        with self._lock:
            self._data = None
            self._links = None

    def link(self, key: str, folder_id: str, address: str = "") -> None:
        """(v5.44.121) A folder «Скачать» put a title of a site in, its description not from there (no "source_url"):
        the folder's series is that title's — one memory (:func:`series_key`); ``address`` — the title's page."""
        with self._lock:
            data = self._load()
            if data is None:
                return
            titles = data["titles"]
            item = titles.get(str(key))
            if not isinstance(item, dict):
                item = titles[str(key)] = {"when": round(time.time())}
            if item.get("folder") == str(folder_id) and (not address or item.get("address")):
                return
            item["folder"] = str(folder_id)
            if address and not item.get("address"):
                item["address"] = str(address)[:2000]
            self._save(data)

    def linked(self) -> dict:
        """{folder id: key} of the folders linked to a title of a site (:meth:`link`)."""
        with self._lock:
            if self._links is None:
                data = self._load()
                self._links = {str(i["folder"]): str(k) for k, i in ((data or {}).get("titles") or {}).items()
                               if isinstance(i, dict) and i.get("folder")}
            return dict(self._links)

    # ── a series' item ──
    def _item(self, data: dict | None, key: str, name: str = "") -> dict:
        """The item of ``key`` — the latest touched last (the oldest are forgotten first)."""
        if data is None:
            return {}
        titles = data["titles"]
        item = titles.pop(key, None)
        item = item if isinstance(item, dict) else {}
        titles[key] = item
        while len(titles) > KEEP_TITLES:
            titles.pop(next(iter(titles)))
        if name:
            item["name"] = str(name)[:300]
        item["when"] = round(time.time())
        return item

    def of(self, key: str) -> dict:
        with self._lock:
            data = self._load()
            item = (data or {}).get("titles", {}).get(str(key))
            return _copy(item) if isinstance(item, dict) else {}

    def view(self, keys) -> dict:
        """What was watched of a series kept under several keys (its own first: :func:`memory_keys`)."""
        with self._lock:
            data = self._load()
            titles = (data or {}).get("titles", {})
            found = [titles.get(str(k)) for k in keys]
            return _merged(found)

    def items(self) -> dict:
        """{key: item} of every series and long video (the shelf «Продолжить просмотр»)."""
        with self._lock:
            data = self._load()
            return _copy((data or {}).get("titles", {})) or {}

    def last_at(self, key) -> int:
        """When an episode of the series (the keys of :func:`memory_keys`, or one) was watched last (0 — never)."""
        keys = key if isinstance(key, (list, tuple)) else [key]
        with self._lock:
            data = self._load()
            titles = (data or {}).get("titles", {})
            stamps = [played(titles[k]) for k in keys if isinstance(titles.get(k), dict) and titles[k].get("last")]
        return max(stamps or [0])

    def note(self, key: str, **fields) -> None:
        """What the shelf shows of a series of a site (its address, cover, site, how many episodes it has, hentai) —
        kept without making it «watched lately»."""
        wanted = {k: v for k, v in fields.items() if k in ("address", "cover", "site", "count", "hentai")
                  and v not in (None, "")}
        if not wanted:
            return
        with self._lock:
            data = self._load()
            item = (data or {}).get("titles", {}).get(str(key))
            if data is None or not isinstance(item, dict) or all(item.get(k) == v for k, v in wanted.items()):
                return
            item.update(wanted)
            self._save(data)

    def record(self, key: str, label: str, at: float, length: float, *, ended: bool = False, name: str = "",
               dub=None, player=None, quality=None, ending: float = 0.0, extra: dict | None = None) -> dict:
        """The place of an episode (``at`` of ``length`` seconds); watched (:func:`watched_at`) — it leaves the places
        and joins «watched».  ``extra`` — what the shelf shows of a series of a site (its address, cover)."""
        label = str(label)[:120]
        at, length = max(0.0, float(at or 0)), max(0.0, float(length or 0))
        watched = watched_at(at, length, ended=ended, ending=ending)
        with self._lock:
            data = self._load()
            item = self._item(data, str(key), name)
            for field_, value in (("dub", dub), ("player", player), ("quality", quality)):
                if value:
                    item[field_] = str(value)[:200]
            for field_, value in (extra or {}).items():
                if field_ in ("address", "cover", "site", "count", "hentai") and value not in (None, ""):
                    item[field_] = value if field_ in ("count", "hentai") else str(value)[:2000]
            item["played"] = round(time.time())
            places = item.get("places") if isinstance(item.get("places"), dict) else {}
            seen = [str(x) for x in item.get("watched") or []][-KEEP_WATCHED:]
            stamps = item.get("seen") if isinstance(item.get("seen"), dict) else {}
            if watched:
                places.pop(label, None)
                if label not in seen:
                    seen.append(label)
                stamps[label] = round(time.time())
            elif at > 1:
                places.pop(label, None)
                places[label] = [round(at, 1), round(length, 1)]
                while len(places) > KEEP_PLACES:
                    places.pop(next(iter(places)))
            while len(stamps) > KEEP_WATCHED:
                stamps.pop(next(iter(stamps)))
            item.update(places=places, watched=seen, seen=stamps, last=label, at=0.0 if watched else round(at, 1),
                        length=round(length, 1), done=watched)
            if data is not None:
                self._save(data)
            return _copy(item)

    def choose(self, key: str, *, name: str = "", dub=None, player=None, quality=None, voice=None,
               online=None) -> dict:
        """The choices of a series: its voice-over (``voice`` — the name of a variant of its episodes: a track, a
        sub-folder, a site's voice-over; "" — none chosen), a site's voice-over, player and quality, «Смотреть онлайн»."""
        with self._lock:
            data = self._load()
            item = self._item(data, str(key), name)
            for field_, value in (("dub", dub), ("player", player), ("quality", quality), ("voice", voice)):
                if value is not None:
                    item[field_] = str(value)[:200]
            if online is not None:
                item["online"] = bool(online)
            if data is not None:
                self._save(data)
            return _copy(item)

    def tracks(self, key: str) -> dict:
        """The voice-over and the subtitles chosen inside the files of a series (``core/video_tracks``): {"audio":
        {"name", "title", "lang"}, "subtitle": {… "file"} or {"off": True}}."""
        entry = self.of(key).get("tracks")
        return {k: dict(v) for k, v in entry.items() if isinstance(v, dict)} if isinstance(entry, dict) else {}

    def set_tracks(self, key: str, kind: str, value: dict | None) -> None:
        """Remember the voice-over (``kind`` "audio") or the subtitles ("subtitle") chosen for the series; a voice-over
        chosen inside a file is the series' voice-over too."""
        if kind not in ("audio", "subtitle"):
            return
        with self._lock:
            data = self._load()
            if data is None:                   # the file is held by someone: the choice is not kept this time
                return
            item = self._item(data, str(key))
            tracks = dict(item.get("tracks") or {}) if isinstance(item.get("tracks"), dict) else {}
            if value:
                tracks[kind] = {k: str(v)[:120] if not isinstance(v, bool) else v for k, v in value.items()
                                if k in ("name", "title", "lang", "file", "off")}
                if kind == "audio" and (tracks[kind].get("title") or tracks[kind].get("name")):
                    item["voice"] = str(tracks[kind].get("title") or tracks[kind].get("name"))
            else:
                tracks.pop(kind, None)
            item["tracks"] = tracks
            self._save(data)

    # ── the notes of before, by the file of an episode ──
    def adopt(self, series: Series) -> None:
        """(v5.44.121) The notes of v5.18–5.44.120 of a series' files (where each stopped, watched, the last one, its
        voice-over and subtitles) go to the series' item — once; they stay in the file as they were."""
        key = series_key(series)
        with self._lock:
            data = self._load()
            if data is None:
                return
            legacy, moved = data["episodes"], data["moved"]
            files = [(unit, f) for unit in series.units() for f in unit.files]
            waiting = [(unit, f) for unit, f in files if isinstance(legacy.get(f.id), dict) and f.id not in moved]
            old_series = data["series"].get(series.id)
            series_waiting = isinstance(old_series, dict) and f"series:{series.id}" not in moved
            if not waiting and not series_waiting:
                return
            titles = data["titles"]
            item = titles.get(key) if isinstance(titles.get(key), dict) else None
            if item is None:
                item = titles[key] = {"name": series.title[:300]}
            places = item.get("places") if isinstance(item.get("places"), dict) else {}
            seen = [str(x) for x in item.get("watched") or []]
            stamps = item.get("seen") if isinstance(item.get("seen"), dict) else {}
            for unit, f in sorted(waiting, key=lambda pair: float(legacy[pair[1].id].get("at") or 0)):
                old = legacy[f.id]
                when = int(float(old.get("at") or 0) or time.time())
                if old.get("done"):
                    places.pop(unit.key, None)
                    if unit.key not in seen:
                        seen.append(unit.key)
                    stamps.setdefault(unit.key, when)
                elif float(old.get("pos") or 0) > 1 and unit.key not in seen:
                    places[unit.key] = [round(float(old.get("pos") or 0), 1), round(float(old.get("dur") or 0), 1)]
                moved[f.id] = key
            if series_waiting:
                if not item.get("tracks") and isinstance(old_series.get("tracks"), dict):
                    item["tracks"] = _copy(old_series["tracks"])
                    audio = item["tracks"].get("audio") if isinstance(item["tracks"].get("audio"), dict) else {}
                    if audio.get("title") and not item.get("voice"):
                        item["voice"] = str(audio["title"])
                last = series.unit_of(series.episode(str(old_series.get("last") or "")) or Episode("", "", "", ""))
                if last is not None and not item.get("last"):
                    item["last"] = last.key
                    place = places.get(last.key)
                    item.update(at=place[0] if place else 0.0, length=place[1] if place else 0.0,
                                done=last.key in seen)
                try:
                    stamp = int(float(old_series.get("at") or 0))
                except (TypeError, ValueError):
                    stamp = 0
                item["when"] = max(int(item.get("when") or 0), stamp) or round(time.time())
                item["played"] = max(played(item), stamp)
                moved[f"series:{series.id}"] = key
            item.update(places=places, watched=seen[-KEEP_WATCHED:], seen=stamps)
            self._save(data)

    # ── the computer's addresses of before: an episode by its file ──
    def episode_state(self, series: Series, episode: Episode) -> dict:
        """{"pos", "dur", "done"} of a file of an episode (its episode's place) — what the pages of before asked."""
        self.adopt(series)
        unit = series.unit_of(episode)
        if unit is None:
            return {}
        item = self.view(memory_keys(series))
        if unit.key in (item.get("watched") or []):
            return {"pos": 0.0, "dur": 0.0, "done": True}
        place = (item.get("places") or {}).get(unit.key)
        return {"pos": float(place[0]), "dur": float(place[1]), "done": False} if place else {}


def played(item: dict) -> int:
    """When an episode of an item was watched last (its «when» for an item of before)."""
    try:
        return int(float(item.get("played") or item.get("when") or 0)) if isinstance(item, dict) else 0
    except (TypeError, ValueError):
        return 0


def _merged(items) -> dict:
    """One item of several (a series' keys, :meth:`Progress.view`): watched in any — watched; a place, the last one,
    the choices — the latest item's (by «when») first."""
    found = [i for i in items if isinstance(i, dict)]
    if not found:
        return {}
    if len(found) == 1:
        return _copy(found[0])
    main = _copy(found[0])
    latest = sorted(found, key=lambda i: float(i.get("when") or 0), reverse=True)
    seen: list = []
    stamps: dict = {}
    for item in found:
        for label in item.get("watched") or []:
            if str(label) not in seen:
                seen.append(str(label))
        for label, when in (item.get("seen") or {}).items():
            stamps[str(label)] = max(stamps.get(str(label), 0), when or 0)
    places: dict = {}
    for item in reversed(latest):                 # the latest item's place wins
        for label, place in (item.get("places") or {}).items():
            if str(label) not in seen:
                places[str(label)] = place
    main.update(watched=seen, seen=stamps, places=places)
    newest = max(found, key=played)               # the episode watched last: the item played last
    for field_ in ("last", "at", "length", "done", "played"):
        if newest.get(field_) is not None:
            main[field_] = newest[field_]
    main["when"] = max(float(i.get("when") or 0) for i in found)
    for field_ in ("name", "voice", "tracks", "dub", "player", "quality", "online", "address", "cover", "site", "count",
                   "hentai"):
        if main.get(field_) in (None, "", {}):
            for item in found[1:]:
                if item.get(field_) not in (None, "", {}):
                    main[field_] = _copy(item[field_])
                    break
    return main


PROGRESS = Progress()


def record_file(series: Series, episode: Episode, position: float, duration: float, *, ended: bool = False,
                ending: float = 0.0) -> dict:
    """What was watched of a file of an episode (the page's player, the window's own one): its episode's place in the
    series' memory; {"pos", "dur", "done"}."""
    PROGRESS.adopt(series)
    unit = series.unit_of(episode)
    if unit is None:
        return {}
    item = PROGRESS.record(series_key(series), unit.key, position, duration, ended=ended, name=series.title,
                           ending=ending)
    done = unit.key in (item.get("watched") or [])
    return {"pos": 0.0 if done else round(float(position or 0), 1), "dur": round(float(duration or 0), 1), "done": done}


def _series_by_id(settings: dict, series_id: str):
    root = video_root(settings)
    return LIBRARY.get(root, series_id) if Path(root).is_dir() else None


def choose_tracks(settings: dict, series_id: str, kind: str, value: dict | None) -> None:
    """The voice-over or the subtitles chosen in a player of the program for a series of the folder (by its id): kept
    in the series' memory (its next episode opens with them)."""
    series = _series_by_id(settings, series_id)
    PROGRESS.set_tracks(series_key(series) if series is not None else f"folder:{series_id}", kind, value)


def record_ids(settings: dict, series_id: str, episode_id: str, position: float, duration: float, *,
               ended: bool = False) -> dict:
    """:func:`record_file` by the ids of a series and a file of it (the window's own full-size player)."""
    root = video_root(settings)
    series, episode = LIBRARY.episode(root, episode_id) if Path(root).is_dir() else (None, None)
    if series is None or episode is None or series.id != series_id:
        return {}
    return record_file(series, episode, position, duration, ended=ended)


def card_key(item: dict) -> str:
    """(v5.44.121) The memory's key of a long video of the library — by its file's MD5 (card numbers come back)."""
    md5 = str((item or {}).get("hash_md5") or "").strip().lower()
    return f"card:{md5}" if re.fullmatch(r"[0-9a-f]{32}", md5) else ""


# ── pictures ─────────────────────────────────────────────────────────────────────
def episode_frame(episode: Episode, width: int, height: int) -> Path | None:
    """A frame of the episode (kept in the thumbnail cache)."""
    from core.image_safe import _cache_name, _video_thumbnail_path, cached_thumbnail_path
    path = Path(episode.path)
    made = cached_thumbnail_path(path, width, height, flavor="")     # a series' frame: as before (not a card's)
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
