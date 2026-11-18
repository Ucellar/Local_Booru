"""Recover a lost voice selection from completed episodes of the same series."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.video_library import Series

from core import video_merge
from core.video_sources import SPECIALS

_EPISODE = re.compile(r"Серия (\d+)\.(mkv|mp4|ts|aac)", re.I)


def _linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _picture_donors(folder: Path) -> dict:
    # read_pictures() can restore its backup on disk; inspecting a retry must be read-only.
    note = folder / ".lb" / video_merge.PICTURES
    try:
        if _linked(note.parent) or _linked(note):
            raise ValueError("linked picture notes")
        data = json.loads(note.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or not all(isinstance(item, dict) and isinstance(item.get("group"), str)
                                             and item.get("state") in ("wanted", "refused") for item in data.values()):
        raise ValueError("invalid picture notes")
    return {stem.casefold(): item["group"].casefold() for stem, item in data.items()}


def _episode_files(folder: Path) -> dict[int, list[Path]]:
    donors = _picture_donors(folder)
    groups = [sub for sub in folder.iterdir() if sub.name.casefold().startswith(("озвучка ", "дубляж "))
              and not sub.name.casefold().endswith(SPECIALS.casefold()) and not _linked(sub) and sub.is_dir()]
    episodes = {}
    for place in [folder, *sorted(groups)]:
        with os.scandir(place) as entries:
            for file in entries:
                match = _EPISODE.fullmatch(file.name)
                if not match or not file.is_file(follow_symlinks=False):
                    continue
                number = int(match[1])
                path = Path(file.path)
                if number and (place == folder or donors.get(path.stem.casefold()) != place.name.casefold()):
                    episodes.setdefault(number, []).append(path)
    return episodes


def first_episode_voices(folder: Path) -> list[str]:
    """Named voices of the lowest-numbered completed episode, once there is another distinct playable episode.

    LB's downloader gives a whole file its final name atomically. Inspect container headers only, including at
    most one playable episode besides the first. A separate AAC can establish a voice but not a whole episode.
    """
    folder = Path(folder)
    try:
        if _linked(folder):
            return []
        first = []
        for _, files in sorted(_episode_files(folder).items()):
            voices = {}
            complete = False
            known = True
            for path in files:
                sound_only = path.suffix.lower() == ".aac"
                if first and sound_only:
                    continue
                labels = video_merge.tracks_in(path, require_picture=not sound_only)
                if not labels:
                    known = False
                    continue
                if first:                           # another number, with both picture and sound
                    return first
                if not sound_only:
                    complete = True
                if path.parent != folder:
                    labels = [video_merge.title_of(path.parent.name)]
                for label in labels:
                    key = video_merge.dub_key(label)
                    if not key:
                        known = False
                    voices.setdefault(key, label)
            if complete:
                if not known:
                    return []
                first = list(voices.values())
    except (OSError, ValueError, ImportError):
        return []
    return []


def _public_page(value) -> str:
    from ipaddress import ip_address
    from urllib.parse import urlsplit
    from core import redaction, video_download

    if not isinstance(value, str):
        return ""
    address = value.strip()
    if not video_download.restorable_address(address) or "#" in address or redaction.sanitize_text(address) != address:
        return ""
    host = (urlsplit(address).hostname or "").rstrip(".").lower()
    try:
        return address if ip_address(host).is_global else ""
    except ValueError:
        return address if "." in host and not host.endswith((".localhost", ".local")) else ""


def library_source(root: Path, entry: dict) -> tuple[Series | None, str]:
    """Resolve the history identity from direct series folders and their metadata, without scanning episodes.

    The second value is a usable original page for the chooser even if the folder is gone or ambiguous.
    """
    from core import video_library as library
    from core.video_download_covers import address_key

    retry = entry.get("retry") if isinstance(entry.get("retry"), dict) else {}
    urls = [value for value in (entry.get("url"), retry.get("url"), retry.get("address")) if value]
    keys = {address_key(page) if (page := _public_page(value)) else "" for value in urls}
    if "" in keys or len(keys) > 1:
        return None, ""
    address = _public_page(urls[0]) if urls else ""
    title = str(entry.get("title") or "").strip().casefold()
    series_id = str(entry.get("series") or "")
    recorded_folder = entry.get("folder")
    root = Path(root).absolute()
    if recorded_folder:
        path = root / Path(recorded_folder)
        if path.parent != root or path.name in (".", ".."):
            return None, address
        directories = (path,)
    else:
        directories = root.iterdir()
    match = None
    try:
        for folder in directories:
            if folder.name.startswith(".") or folder.name.casefold() in library._SKIP_DIRS:
                continue
            item_id = library._id_for(root, folder)
            if (series_id and series_id != item_id) or _linked(folder) or not folder.is_dir():
                continue
            info_file = folder / library.INFO_NAME
            if info_file.exists() and _linked(info_file):
                continue
            info = library._read_info(info_file)
            name = library._title(info, folder.name)
            source = _public_page(info.get("source_url"))
            if address:
                if address_key(source) not in keys:
                    continue
            elif title and title not in (name.casefold(), folder.name.casefold()):
                continue
            elif not (title or series_id or recorded_folder):
                continue
            if match is not None:
                return None, address
            match = library.Series(id=item_id, title=name, path=str(folder), folder_title=folder.name, info=info)
    except (OSError, ValueError):
        return None, address
    if match is None:
        return None, address
    source = _public_page(match.info.get("source_url"))
    return (match, source) if source else (None, address)
