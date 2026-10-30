"""Voice-overs and subtitles of an episode: the tracks inside its file and the subtitle files next to it.

* Inside the file — one MKV with several voice-overs and subtitle tracks (a «мультирелиз»): the player lists them
  (Qt Multimedia — ``ui/video_inline.py``, ``ui/video_player.py``; mpv), here their names are made («JAM (русский)»)
  and a remembered choice is matched to them (``choose``).
* Next to it — the episode's subtitle files: ``<its name>….srt|.ass|.ssa|.vtt`` in its folder, and the same in folders
  of subtitles beside it («Subs», «RUS Subs/JAM», «Субтитры»), up to two levels down, in its folder and — when the
  episode is in a sub-folder of its series (``top``) — in the series' folder (``subtitle_files``; never above the
  series: other series there have episodes of the same names).  Read here into cues (``read_cues``): SRT, WebVTT, ASS/SSA — the text without
  its styles and drawings; the encoding guessed (UTF-8 and UTF-16 by their marks, else UTF-8 when it reads, else
  Windows-1251, as Russian subtitles are often written).  A player that cannot read such a file draws the cue of the
  moment itself (``Cues.at``); a browser gets it as WebVTT (``to_vtt``).
* The choice is kept for the series (``video_library.PROGRESS``, "tracks" of the series): the next episode opens with
  the same voice-over and subtitles — the track of the same name, else of the same title, else of the same language.
"""
from __future__ import annotations

import bisect
import html
import os
import re
from dataclasses import dataclass
from pathlib import Path

SUB_EXTS = (".srt", ".ass", ".ssa", ".vtt")
MAX_SUB_FILE = 8 * 1024 * 1024        # bytes of a subtitle file read at most
MAX_CUES = 50_000
MAX_FILES = 40                        # subtitle files offered for one episode at most
MAX_LINES = 3                         # cues shown at once at most (signs of ASS may overlap the dialogue)
LONGEST = 600.0                       # seconds: a cue longer than this is not looked back for
# a folder of subtitles: «Subs», «RUS Subs», «Sub.JAM», «Субтитры», «Сабы» — the word, not a part of another («Subaru»)
_SUB_DIR = re.compile(r"(?<![^\W\d_])(?:subs?|subtitles?|субтитры?|сабы?)(?![^\W\d_])", re.I)
# the names of languages as the program says them (Qt says them in English)
LANGUAGES = {"russian": "русский", "japanese": "японский", "english": "английский", "ukrainian": "украинский",
             "chinese": "китайский", "korean": "корейский", "german": "немецкий", "french": "французский",
             "spanish": "испанский", "italian": "итальянский", "portuguese": "португальский", "polish": "польский",
             "belarusian": "белорусский", "kazakh": "казахский", "arabic": "арабский", "thai": "тайский",
             "vietnamese": "вьетнамский", "indonesian": "индонезийский", "turkish": "турецкий"}
# ISO 639 codes of those languages (mpv and the tags of a file name them so)
LANGUAGE_CODES = {code: name for name, codes in {
    "russian": ("ru", "rus"), "japanese": ("ja", "jpn"), "english": ("en", "eng"), "ukrainian": ("uk", "ukr"),
    "chinese": ("zh", "chi", "zho"), "korean": ("ko", "kor"), "german": ("de", "ger", "deu"),
    "french": ("fr", "fre", "fra"), "spanish": ("es", "spa"), "italian": ("it", "ita"), "portuguese": ("pt", "por"),
    "polish": ("pl", "pol"), "belarusian": ("be", "bel"), "kazakh": ("kk", "kaz"), "arabic": ("ar", "ara"),
    "thai": ("th", "tha"), "vietnamese": ("vi", "vie"), "indonesian": ("id", "ind"), "turkish": ("tr", "tur"),
}.items() for code in codes}


@dataclass
class SubFile:
    path: str
    name: str                         # what the menu says: «JAM», «rus», «Субтитры (.ass)»


# ── names and choices ───────────────────────────────────────────────────────────────
def language_name(english: str) -> str:
    """«русский» for «Russian» (Qt names a language so) or «rus» / «ru» (mpv and the files do); another language
    as it is named; "" for none."""
    text = str(english or "").strip()
    if not text or text.lower() in ("anylanguage", "any language", "c", "und", "mul", "zxx", "mis"):
        return ""
    low = text.lower()
    return LANGUAGES.get(low) or LANGUAGES.get(LANGUAGE_CODES.get(low, ""), text)


def track_name(title: str, language: str, number: int) -> str:
    """What the menu calls a track: «JAM (русский)», «русский», «Дорожка 2»."""
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:80]
    language = str(language or "").strip()
    if title and language and language.lower() not in title.lower():
        return f"{title} ({language})"
    return title or language or f"Дорожка {number}"


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def choose(tracks: list, wanted: dict | None) -> int | None:
    """The place of the track a remembered choice names (``tracks`` — {"name", "title", "lang"} each): the same name,
    else the same title, else the same language; None — none of them."""
    if not isinstance(wanted, dict):
        return None
    for key in ("name", "title", "lang"):
        value = _norm(wanted.get(key))
        if not value:
            continue
        for n, track in enumerate(tracks):
            if isinstance(track, dict) and _norm(track.get(key)) == value:
                return n
    return None


def preferred(prefer: dict | None, audio: list, subtitles: list) -> tuple[int | None, str | None]:
    """What a series' remembered choice asks of a play's tracks: (the place of its voice-over — only when there is
    a choice —, the id of its subtitles: "N" a track of the file, "xN" a file next to it); None — nothing to change.
    Subtitles of a file chosen before are looked for among the files first, a track — among the tracks first."""
    prefer = prefer if isinstance(prefer, dict) else {}
    voice = choose(audio, prefer.get("audio")) if len(audio) > 1 else None
    wanted = prefer.get("subtitle")
    if not isinstance(wanted, dict) or wanted.get("off"):
        return voice, None
    files = [t for t in subtitles if isinstance(t, dict) and t.get("file")]
    inside = [t for t in subtitles if isinstance(t, dict) and not t.get("file")]
    order = files + inside if wanted.get("file") else inside + files
    place = choose(order, wanted)
    return voice, (str(order[place].get("id")) if place is not None else None)


# ── subtitle files ──────────────────────────────────────────────────────────────────
def _fits(name: str, stem: str) -> str | None:
    """What follows the episode's name in a subtitle file's name ("" — nothing), or None when it is not the
    episode's (``Show 1`` is not the start of ``Show 10``)."""
    path = Path(name)
    if path.suffix.lower() not in SUB_EXTS:
        return None
    base = name[:-len(path.suffix)]
    if not base.lower().startswith(stem.lower()):
        return None
    rest = base[len(stem):]
    if rest and rest[0].isalnum():
        return None
    return rest.strip(" ._-[]()")


def _sub_dirs(folder: Path, depth: int = 2) -> list[Path]:
    out = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
    except OSError:
        return out
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                path = Path(entry.path)
                if _SUB_DIR.search(entry.name):
                    out.append(path)
                    out += [p for p in _all_dirs(path, depth - 1)]
        except OSError:
            continue
    return out


def _all_dirs(folder: Path, depth: int) -> list[Path]:
    if depth <= 0:
        return []
    out = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
    except OSError:
        return out
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                out.append(Path(entry.path))
                out += _all_dirs(Path(entry.path), depth - 1)
        except OSError:
            continue
    return out


def _team(part: str) -> str:
    """What a folder of subtitles says besides being one: «RUS Subs» → «RUS», «Subs [JAM]» → «JAM», «JAM» → «JAM»."""
    return _SUB_DIR.sub(" ", part).strip(" ._-[]()")


def subtitle_files(video, top=None) -> list[SubFile]:
    """The subtitle files of an episode (see the module's description), named for the menu, each once.  ``top`` — the
    folder of its series: when the episode is in a sub-folder of it, folders of subtitles in the folders between are
    the episode's too; None — the episode's own folder only."""
    video = Path(video)
    stem = video.stem
    places = [(video.parent, video.parent)] + [(d, video.parent) for d in _sub_dirs(video.parent)]
    if top:                                  # up to the series' folder (a season, a voice-over in it), never above
        series, folder = Path(top), video.parent
        for _level in range(3):
            parent = folder.parent
            if folder == series or parent == folder or not (parent == series or series in parent.parents):
                break
            places += [(d, parent) for d in _sub_dirs(parent) if all(d != f for f, _base in places)]
            folder = parent
    found: list[tuple[Path, str]] = []
    for folder, base in places:
        try:
            names = sorted(os.listdir(folder), key=str.lower)
        except OSError:
            continue
        try:
            parts = folder.relative_to(base).parts
        except ValueError:
            parts = ()
        team = [t for t in (_team(p) for p in parts) if t]              # «RUS Subs/JAM» → «JAM»
        for name in names:
            rest = _fits(name, stem)
            if rest is None or len(found) >= MAX_FILES or not (folder / name).is_file():
                continue
            found.append((folder / name, " ".join(x for x in (team[-1] if team else "", rest) if x)))
    out: list[SubFile] = []
    for path, label in found:
        name = label or "Субтитры"
        if name == "Субтитры" or any(s.name.lower() == name.lower() for s in out):
            name = f"{name} ({path.suffix.lower()})"
        while any(s.name.lower() == name.lower() for s in out):
            name += " ·"
        out.append(SubFile(path=str(path), name=name))
    return out


# ── reading them ───────────────────────────────────────────────────────────────────
def read_text(path) -> str:
    with open(path, "rb") as handle:
        data = handle.read(MAX_SUB_FILE + 1)
    if len(data) > MAX_SUB_FILE:
        raise ValueError("файл субтитров слишком большой")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1251", "replace")


_TIME = r"(?:(\d{1,3}):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})"
_CUE_LINE = re.compile(_TIME + r"\s*-->\s*" + _TIME)
_MARKUP = re.compile(r"</?[a-zA-Z][^>]*>|\{\\[^}]*\}")
_ASS_TIME = re.compile(r"\s*(\d+):(\d{1,2}):(\d{1,2})[.,](\d{1,3})")


def _seconds(hours, minutes, seconds, part) -> float:
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(str(part).ljust(3, "0")[:3]) / 1000.0


def _lines(text: str) -> str:
    return "\n".join(line.strip() for line in str(text).split("\n") if line.strip())


def _srt(text: str) -> list:
    cues = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    n = 0
    while n < len(lines):
        found = _CUE_LINE.search(lines[n])
        n += 1
        if not found:
            continue
        body = []
        while n < len(lines) and lines[n].strip():
            body.append(lines[n])
            n += 1
        start, end = _seconds(*found.group(1, 2, 3, 4)), _seconds(*found.group(5, 6, 7, 8))
        words = _lines(html.unescape(_MARKUP.sub("", "\n".join(body))))
        if words and end > start:
            cues.append((start, end, words))
    return cues


def _ass(text: str) -> list:
    cues = []
    fields: list = []
    events = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("["):
            events = line.lower() == "[events]"
            continue
        if not events:
            continue
        low = line.lower()
        if low.startswith("format:"):
            fields = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
        elif low.startswith("dialogue:") and fields:
            parts = line.split(":", 1)[1].split(",", len(fields) - 1)
            if len(parts) < len(fields):
                continue
            row = dict(zip(fields, parts))
            body = row.get("text", "")
            if re.search(r"\{[^}]*\\p[1-9]", body):           # a drawing, not words
                continue
            start, end = _ASS_TIME.match(row.get("start", "")), _ASS_TIME.match(row.get("end", ""))
            if not start or not end:
                continue
            words = re.sub(r"\{[^}]*\}", "", body).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
            words = _lines(words)
            begin, finish = _seconds(*start.groups()), _seconds(*end.groups())
            if words and finish > begin:
                cues.append((begin, finish, words))
    return cues


def read_cues(path) -> list:
    """(start, end, text) of a subtitle file, in the order of their starts."""
    text = read_text(path)
    ass = Path(path).suffix.lower() in (".ass", ".ssa") or "[Events]" in text[:200_000]
    cues = _ass(text) if ass else _srt(text)
    cues.sort(key=lambda c: (c[0], c[1]))
    return cues[:MAX_CUES]


class Cues:
    """The cues of a file, asked by the moment of the video."""

    def __init__(self, cues: list):
        self.cues = list(cues)
        self.starts = [c[0] for c in self.cues]
        self.longest = min(LONGEST, max((e - s for s, e, _t in self.cues), default=0.0))

    def at(self, seconds: float) -> str:
        found: list[str] = []
        n = bisect.bisect_right(self.starts, seconds) - 1
        while n >= 0 and self.cues[n][0] >= seconds - self.longest:
            start, end, text = self.cues[n]
            if start <= seconds < end and text not in found:
                found.insert(0, text)
            n -= 1
        return "\n".join(found[-MAX_LINES:])


def _stamp(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def to_vtt(cues: list) -> str:
    """The cues as WebVTT, for a browser's ``<track>``."""
    out = ["WEBVTT", ""]
    for start, end, text in cues:
        words = text.replace("-->", "→").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        out += [f"{_stamp(start)} --> {_stamp(end)}", words, ""]
    return "\n".join(out)
