"""«Скачать с сайта» on the tab «Видео»: the video files a title's page links to directly, downloaded into the video
folder as a series, with its description and poster («Описание с сайта», ``core/video_meta_import``).

What is taken — only what the page hands out as a plain file:

* the player's own list (``new Playerjs({file:[{file:"https://…/01.mp4", title:"Файл 1"}, …]})`` on DLE sites such
  as hentasis; ``file:"[720p]…mp4,[480p]…mp4"`` — the best one, the others kept in case the site does not give it);
* ``<video src>``, ``<source src>``, ``og:video``, the page's structured data (``VideoObject.contentUrl``), links to a
  video file;
* an HLS stream (``.m3u8``) when it is open — its parts put together into one file (``core/video_hls``); an encrypted
  one is said in words and left alone;
* a series whose episodes are pages of their own (hentaihaven): the episode pages it links to are read too
  (``core/video_episodes``); the page of one episode finds its sister episodes and the series' page, which describes
  the series.

A link the player keeps encoded (``file:"#2…"``), a DASH stream (``.mpd``) and a player of another site in a frame
(Kodik and the like hide where the video is so that it is not downloaded) are said in words and left alone.

Where they go: the folder of the series whose description came from this page (info.json "source_url"), else a new
folder named after the title.  When the site says who voiced which file («Файл 1 - озвучка от …, файлы 2,3 - …»),
each voice-over gets a sub-folder — a tab over the episode buttons on the series page.  The site's file names are kept
(info.json "files" matches them).

How: one file after another, the way the browser plays them (the page as the referrer, the site's cookies from «Сайты
и ключи»), into ``<name>.part``: a download cut off is taken up again from where it stopped (Range), one stopped is
continued by the next «Скачать».  A file already in the folder is never written over; what a site answers instead of
a file (a robot check, an error page) is never kept as a video.  A file the site gives empty — an empty answer, or the
connection closed before the first byte (old files of hentasis: its own player says «Format error» too) — is asked
once more the way a browser's player asks (``Range: bytes=0-``), then in the player's other quality, if it has one;
else it is said in words that the site does not give this file, without the tries meant for a connection that breaks.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from core import video_library, video_meta

_LOG = logging.getLogger("local_booru.video")

VIDEO_FILES = (".mp4", ".webm", ".mkv", ".m4v", ".mov")
AUDIO_FILES = (".mp3", ".m4a", ".ogg", ".opus", ".flac")
STREAMS = (".m3u8", ".mpd")
# players of other sites shown in a frame: they hide where the video is (said to the person, never worked around)
FRAME_PLAYERS = ("kodik", "aksor", "alloha", "sibnet", "video_ext", "videoembed", "youtube.com/embed", "rutube",
                 "csst.online", "cdnvideohub", "anilib", "libria", "vkvideo", "dzen.ru/embed", "ok.ru/video")
# sites whose players load the video from another site (no file of their own on the page) or hide its address (Jut.su:
# placeholders in place of the files, the real ones put in by the site's scrambled scripts) — never worked around
FRAME_SITES = ("yummyani", "yummy-anime", "yani.tv", "animego", "jut.su", "anilibria", "aniliberty")
# players named on the page (a block «Плеер Kodik», a frame titled so): the frame itself may be made by a script
_NAMED_PLAYERS = (("Kodik", re.compile(r"kodik", re.I)), ("Alloha", re.compile(r"alloha", re.I)),
                  ("Aksor", re.compile(r"aksor", re.I)), ("CDN Video Hub", re.compile(r"cdn\s*video\s*hub", re.I)),
                  ("Collaps", re.compile(r"collaps", re.I)), ("Sibnet", re.compile(r"sibnet", re.I)))
_PICTURES = (".png", ".gif", ".jpg", ".jpeg", ".webp", ".svg")
CHUNK = 256 * 1024             # bytes read at a time: «Остановить» and the progress bar answer within one
TIMEOUT = (15, 60)
TRIES = 4                      # a download cut off is taken up again (from where it stopped) this many times in a row
ATTEMPTS = 20_000             # requests for one file at most (a cut that brought bytes starts the tries anew; a site
                               # that gives a file in pieces is asked piece after piece)
EMPTY_TRIES = 2                # an empty answer: once more the way a browser's player asks, then given up
TITLE_MAX = 80                 # characters of a series folder's name
GROUP_MAX = 48
NAME_MAX = 120
PATH_MAX = 240                 # Windows: longer paths need «long paths» switched on
SPARE = 300 * 1024 * 1024      # bytes a download leaves free on the disk
SIZE_LOOKUPS = 40              # files whose size is asked when the page is read
METER_EVERY = 0.4              # seconds between updates of the progress bar


class DownloadError(Exception):
    """Said to the person as it is."""


class _Empty(DownloadError):
    """The site gave nothing for the file (an empty answer, or the connection closed before the first byte)."""


@dataclass
class Remote:
    """A file the page links to."""
    url: str
    name: str                      # the file's name in the folder (the site's, made safe)
    title: str = ""                # the site's name for it («Файл 2»)
    note: str = ""                 # who voiced it (the site's «Примечание»)
    kind: str = "video"            # "video" or "audio"
    group: str = ""                # the sub-folder it goes to ("" — the series folder)
    size: int = 0                  # bytes, when the site said
    have: str = ""                 # "whole" — in the folder already; "part" — a part is there; "other" — another file
    part: int = 0                  # bytes of the part already there
    quality: str = ""              # the player's name for this address («1080p»), when it has qualities
    alts: list = field(default_factory=list)   # [quality, address] of the player's other qualities, the best first
    stream: str = ""               # "hls" — an HLS stream put together into one file
    blocked: str = ""              # why it cannot be downloaded (an encrypted stream…) — "" when it can
    length: float = 0.0            # seconds (a stream says it)
    page: str = ""                 # the page it was found on (an episode's page): asked with it as the referrer


@dataclass
class Found:
    """What a title's page offers, and where it would go."""
    page: object                   # core.video_meta_import.SavedPage
    title: str
    folder: Path
    files: list = field(default_factory=list)
    streams: int = 0               # streams (.m3u8) on the page — not downloaded
    hidden: int = 0                # links the player keeps encoded — not downloaded
    frames: list = field(default_factory=list)     # hosts of other sites' players in frames
    frame_site: bool = False       # a site whose videos are all other sites' players (YummyAnime)
    new_folder: bool = True
    series_url: str = ""           # the series' page when this one is an episode's
    about: object = None           # the page that describes the series (the series' page, else ``page``)
    episodes: int = 0              # pages of episodes read
    plans: dict = field(default_factory=dict)      # a stream's address -> video_hls.Plan
    sources: list = field(default_factory=list)    # other sites the files come from (``core/video_sources``), each
                                                   # {"site", "dub", "what", "how", "exact", "about", "episodes", …}
    voices: list = field(default_factory=list)     # the page's voice-overs of the title: {"name", "here"} — "here":
                                                   # the one a source gives
    facts: dict = field(default_factory=dict)      # what the page says of the title, for its card on the page
    poster: tuple | None = None                    # (bytes, extension, where from) — shown with the list, then written
    choose: str = ""                               # "dubs" — many voice-overs (``core/video_dubs``): nothing is ticked
                                                   # at first, the page offers «Качество»
    resolve: object = None                         # (remote, *, session, quality, say, stop) -> HLS plan | None: the
                                                   # file's address asked right before it is downloaded (signed
                                                   # addresses that live for hours — ``video_dubs.Resolver``)
    had: object = None                             # (remote) -> bool: the file is in the folder in another shape
                                                   # (a voice-over put into the episode's MKV — ``video_dubs.Merger``)
    after: object = None                           # (*, say, stop) -> dict: done after «Скачать» when asked
                                                   # (``download(merge=True)``: the voice-overs into one file)

    def public(self, root: Path) -> dict:
        _look_at_folder(self.folder, self.files, self.had)  # what is in the folder now (a download may have run)
        try:
            where = str(self.folder.relative_to(root))
        except ValueError:
            where = str(self.folder)
        files = []
        labels = tile_labels(self.files)
        for n, remote in enumerate(self.files):
            item = asdict(remote)
            item.pop("url")                # the page needs the names; the addresses stay here
            item.pop("page")
            item["alts"] = len(item.pop("alts") or [])
            files.append(dict(item, i=n, label=labels[n]))
        # (v5.44.142) «новая» — while it is not there: a list shown again after a download into it says «уже есть»
        new = self.new_folder and not self.folder.is_dir()
        from core import video_dubs          # (v5.44.152) the voice-overs of players not taken: by player, with why
        return {"title": self.title, "folder": where, "new_folder": new, "page_url": self.page.url,
                "streams": self.streams, "hidden": self.hidden, "frames": self.frames, "frame_site": self.frame_site,
                "episodes": self.episodes, "files": files, "sources": [dict(x) for x in self.sources],
                "voices": [dict(v) for v in self.voices], "closed": video_dubs.closed_voices(self.voices),
                "facts": dict(self.facts), "poster": self.poster is not None,
                "choose": self.choose}


_TITLE_NUMBER = re.compile(r"(\d{1,4}(?:\.\d{1,2})?)\s*$")


def tile_labels(files: list) -> list[str]:
    """What each file is called on its tile of the list: the number of its episode as the series' page will number it
    (``video_library.episode_labels`` of the names in its sub-folder; in the series' own folder — the site's number
    of it, «Файл 3» → 3, when every file there has one); a sound — «♪»."""
    labels = [""] * len(files)
    groups: dict = {}
    for n, remote in enumerate(files):
        if remote.kind == "audio":
            labels[n] = "♪"
        else:
            groups.setdefault(remote.group, []).append(n)
    for group, places in groups.items():
        numbers = [_TITLE_NUMBER.search(str(files[n].title or "")) for n in places]
        if not group and all(numbers) and len({m.group(1) for m in numbers}) == len(numbers):
            values = [m.group(1) for m in numbers]
        else:
            values = video_library.episode_labels([Path(files[n].name).stem for n in places])
        for n, value in zip(places, values):
            labels[n] = value
    return labels


def facts_of(meta: dict) -> dict:
    """What the card of the title on the page shows: its year, type, number of episodes, other titles, genres."""
    def text(key: str) -> str:
        value = meta.get(key)
        return str(value[0] if isinstance(value, list) and value else value or "").strip()[:80]
    about = str(meta.get("description") or "").strip()
    about = re.split(r"\n\s*\n", about, 1)[0].strip()             # its first paragraph, for a few lines on the card
    return {"year": text("year"), "type": text("type"), "episodes": text("episodes"),
            "alt": [str(t)[:120] for t in (meta.get("alt_titles") or [])[:2]],
            "genres": [str(g)[:40] for g in (meta.get("genres") or [])[:6]],
            "about": about[:600] + ("…" if len(about) > 600 else "")}


# ── names ─────────────────────────────────────────────────────────────────────────
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}


def safe_name(text, limit: int = NAME_MAX, *, keep_ext: bool = False) -> str:
    """A name Windows takes for a file or folder: no ``<>:"/\\|?*``, no dots or spaces at the ends, not ``CON``…,
    at most ``limit`` characters (a file keeps its extension)."""
    text = re.sub(r"\s+", " ", _BAD.sub(" ", str(text or ""))).strip(" .")
    stem, ext = text, ""
    if keep_ext:
        found = re.match(r"^(.*?)(\.[A-Za-z0-9]{1,5})$", text)
        if found:
            stem, ext = found.group(1).strip(" ."), found.group(2).lower()
    if len(stem) + len(ext) > limit:
        stem = stem[:max(1, limit - len(ext))].rstrip(" .")
        cut = stem.rfind(" ")
        if cut > limit // 2:
            stem = stem[:cut].rstrip(" .")
    if not stem:
        return ""
    if stem.split(".")[0].lower() in _RESERVED:
        stem = "_" + stem
    return stem + ext


def _group_name(note: str) -> str:
    text = safe_name(note, GROUP_MAX)
    return text[:1].upper() + text[1:] if text else ""


def _absolute(address: str, page_url: str) -> str:
    address = str(address or "").strip().replace("\\/", "/")
    if not address or address.startswith(("data:", "blob:", "javascript:")):
        return ""
    if address.startswith("//"):
        address = "https:" + address
    elif not address.startswith(("http://", "https://")):
        if not page_url.startswith(("http://", "https://")):
            return ""
        address = urljoin(page_url, address)
    return address if urlparse(address).hostname else ""


def _ext_of(address: str) -> str:
    return Path(unquote(urlparse(address).path)).suffix.lower()


# ── what the page offers ─────────────────────────────────────────────────────────────
_PLAYER_FILE = re.compile(r"""[{,]\s*["']?file["']?\s*:\s*["']([^"']+)["']""")
_QUALITY = re.compile(r"\[([^\]]*)\]\s*([^,\[\s]+)")


def _qualities(value: str) -> list[tuple[str, str]]:
    """(quality, address) of a player's value, the one to take first: Playerjs ``[720p]a.mp4,[480p]b.mp4`` — the
    best first; ``a.mp4 or b.mp4`` — in their order; else the value itself."""
    pairs = _QUALITY.findall(value)
    if pairs:
        def rank(pair):
            number = re.search(r"\d+", pair[0])
            return int(number.group()) if number else 0
        return [(label.strip(), address) for label, address in sorted(pairs, key=rank, reverse=True)]
    if " or " in value:
        return [("", part.strip()) for part in value.split(" or ") if part.strip()]
    return [("", value)]


def _player_entries(html: str) -> list[tuple[int, str, str]]:
    """(place, address, title) of the files of the page's player."""
    out = [(m.start(), m.group(1), m.group(2)) for m in video_meta._FILE_RE.finditer(html or "")]
    out += [(m.start(), m.group(2), m.group(1)) for m in video_meta._FILE_RE_TITLE_FIRST.finditer(html or "")]
    taken = {address for _at, address, _title in out}
    for m in _PLAYER_FILE.finditer(html or ""):
        if m.group(1) not in taken:
            out.append((m.start(), m.group(1), ""))
    return sorted(out)


def _unique(name: str, files: list) -> str:
    """``name`` (with its extension), or «name 2», «name 3»… when a file of the list has it."""
    stem, ext = (name.rsplit(".", 1)[0], "." + name.rsplit(".", 1)[1]) if "." in name else (name, "")
    taken = {f.name.lower() for f in files}
    n, out = 1, name
    while out.lower() in taken:
        n += 1
        out = f"{stem} {n}{ext}"
    return out


def _stream_name(title: str, address: str) -> str:
    """A file's name for a stream: its title, else the part of its address before ``playlist.m3u8`` (``.mp4`` until
    the stream says what it is — ``_plan_streams``)."""
    stem = safe_name(title, NAME_MAX - 4)
    if not stem:
        parts = [p for p in unquote(urlparse(address).path).split("/") if p]
        stem = safe_name(parts[-2] if len(parts) > 1 else "видео", NAME_MAX - 4) or "видео"
    return stem + ".mp4"


def _ld_videos(soup) -> list[tuple[str, str]]:
    """(address, name) of the videos the page's structured data names (``VideoObject.contentUrl``)."""
    out = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except ValueError:
            continue
        for item in video_meta._ld_items(data):
            kind = item.get("@type")
            if "VideoObject" in (kind if isinstance(kind, list) else [kind]) and isinstance(item.get("contentUrl"), str):
                out.append((item["contentUrl"], video_meta._ld_text(item.get("name"))))
    return out


_AD_WORDS = re.compile(r"реклам|advert|sponsor|(?:^|[\s_-])ads?(?:$|[\s_-])|novaads|banner|promo", re.I)


def _advert(el) -> bool:
    """A video that is not the title's: an advertisement (inside a block marked as one — «Реклама», ``ads``,
    ``banner``), a preview of a link (inside ``<a href>``: the series' episodes play their previews on hover), a
    background clip (``loop`` with ``autoplay`` or ``muted``, or hidden from readers — ``aria-hidden``)."""
    video = el if el.name == "video" else el.find_parent("video")
    if video is not None and video.has_attr("loop") and (video.has_attr("autoplay") or video.has_attr("muted")):
        return True
    node = el
    for _level in range(8):
        if node is None or getattr(node, "name", None) in (None, "body", "html", "[document]"):
            return False
        marks = " ".join([str(node.get("aria-label") or ""), " ".join(node.get("class") or []), str(node.get("id") or "")])
        if _AD_WORDS.search(marks) or str(node.get("aria-hidden") or "") == "true":
            return True
        if node.name == "a" and node.get("href"):
            return True
        node = node.parent
    return False


def find_files(page, *, meta: dict | None = None) -> tuple[list, int, int, list]:
    """(files, streams, hidden, frames) of a page (``SavedPage``): the files it links to directly."""
    html, page_url = page.html or "", page.url or ""
    files: list[Remote] = []
    streams = hidden = 0
    seen: set = set()
    meta = meta if meta is not None else video_meta.parse_page(html, page_url)

    def add(address: str, title: str = "") -> None:
        nonlocal streams, hidden
        address = str(address or "").strip()
        if address.startswith("#"):                  # Playerjs keeps the address encoded: not ours to open
            hidden += 1
            return
        options = []                                 # (quality, address) of one file, the one to take first
        hls = []                                     # (quality, address) of HLS streams
        stream = False
        for quality, one in _qualities(address):
            one = _absolute(one, page_url)
            if not one:
                continue
            ext = _ext_of(one)
            if ext == ".m3u8":
                if one not in seen:
                    seen.add(one)
                    hls.append((quality, one))
                continue
            if ext in STREAMS:
                stream = True
                continue
            if ext not in VIDEO_FILES + AUDIO_FILES or one in seen:
                continue
            seen.add(one)
            options.append((quality, one))
        if not options and hls:                      # an HLS stream: its qualities are inside it
            quality, one = hls[0]
            name = _unique(_stream_name(title or str(meta.get("title") or ""), one), files)
            files.append(Remote(url=one, name=name, title=video_meta._clean(title), quality=quality, stream="hls",
                                page=page_url))
            return
        if not options:
            streams += 1 if stream else 0
            return
        quality, one = options[0]
        ext = _ext_of(one)
        name = safe_name(unquote(urlparse(one).path.rsplit("/", 1)[-1]), NAME_MAX, keep_ext=True)
        if not name or any(f.name.lower() == name.lower() for f in files):
            return
        files.append(Remote(url=one, name=name, title=video_meta._clean(title), quality=quality,
                            alts=[[q, a] for q, a in options[1:]], kind="audio" if ext in AUDIO_FILES else "video"))

    for _at, address, title in _player_entries(html):
        add(address, title)
    soup = video_meta._soup(html)
    for address, name in _ld_videos(soup):
        add(address, name)
    for el in soup.find_all(["video", "source", "audio"]):
        if _advert(el):
            continue
        address = el.get("src") or el.get("data-src") or ""
        if el.name == "source" and _ext_of(_absolute(address, page_url)) in _PICTURES:
            hidden += 1                              # a picture in place of the file: the player puts the real one
            continue                                 # in itself (Jut.su) — its address is hidden
        add(address)
    for el in soup.find_all("meta"):
        if str(el.get("property") or el.get("name") or "").lower() in ("og:video", "og:video:url", "og:video:secure_url"):
            add(el.get("content") or "")
    for el in soup.find_all("a", href=True):
        if _ext_of(_absolute(el["href"], page_url)) in VIDEO_FILES + AUDIO_FILES + STREAMS:
            add(el["href"], el.get_text(" ", strip=True)[:80])
    frames = []
    for el in soup.find_all("iframe"):
        src = _absolute(el.get("src") or el.get("data-src") or "", page_url)
        if src and any(mark in src.lower() for mark in FRAME_PLAYERS):
            host = (urlparse(src).hostname or "").removeprefix("www.")
            if host and host not in frames:
                frames.append(host)
    named = " ".join(str(el.get("data-player-title") or "") for el in soup.find_all(attrs={"data-player-title": True}))
    named += " " + " ".join(str(el.get("title") or "") for el in soup.find_all("iframe"))
    for label, mark in _NAMED_PLAYERS:               # JAM club: «Плеер Alloha», «Плеер Kodik» — frames made by script
        if mark.search(named) and not any(mark.search(host) for host in frames):
            frames.append(label)
    _notes_and_groups(files, meta)
    for remote in files:
        remote.page = remote.page or page_url
    return files, streams, hidden, frames


def _notes_and_groups(files: list, meta: dict) -> None:
    """The site's notes («Файл 2 — озвучка от …») go to the files of the same name; when the videos have two voice-overs
    or more, each goes to a sub-folder of its own."""
    by_name = {str(f.get("name") or "").lower(): f for f in meta.get("files") or [] if isinstance(f, dict)}
    for remote in files:
        known = by_name.get(remote.name.lower())
        if known:
            remote.title = remote.title or video_meta._clean(known.get("title"))
            remote.note = video_meta._clean(known.get("note"))
    numbered = [{"title": f.title} for f in files]              # «файл 6 - хента-трек» for a song too
    video_meta._file_notes(numbered, meta.get("notes") or [])
    for remote, item in zip(files, numbered):
        if not remote.note and item.get("note"):
            remote.note = video_meta._clean(item["note"])
    notes = {f.note for f in files if f.kind == "video" and f.note}
    if len(notes) > 1:
        for remote in files:
            if remote.kind == "video" and remote.note:
                remote.group = _group_name(remote.note)


# ── where they go ────────────────────────────────────────────────────────────────────
def _same_page(a, b) -> bool:
    def key(address):
        parsed = urlparse(str(address or "").strip())
        return (parsed.hostname or "").removeprefix("www."), parsed.path.rstrip("/")
    return bool(a) and bool(b) and key(a) == key(b) and key(a)[0] != ""


def same_title(ids, other) -> bool:
    """Two descriptions name one title: the same number on Shikimori or on MyAnimeList (``info.json`` "ids")."""
    if not isinstance(ids, dict) or not isinstance(other, dict):
        return False
    return any(isinstance(ids.get(key), int) and ids.get(key) == other.get(key) for key in ("shikimori", "mal"))


def target_folder(root: Path, title: str, page_url: str, also: str = "", ids: dict | None = None) -> tuple[Path, bool]:
    """(folder, is it new): the series this page (or ``also`` — the series' page of an episode's page) described
    before, else the series whose description names the same title (``ids`` — its numbers on Shikimori and
    MyAnimeList: the title's page on another address, another site), else a folder named after the title."""
    everything = [s for s in video_library.LIBRARY.items(str(root), refresh=True) if not s.single] if root.is_dir() else []
    for series in everything:
        known = series.info.get("source_url")
        if _same_page(known, page_url) or _same_page(known, also):
            return Path(series.path), False
    for series in everything:
        if same_title(series.info.get("ids"), ids):
            return Path(series.path), False
    name = safe_name(title, TITLE_MAX) or safe_name((urlparse(page_url).hostname or "").removeprefix("www."), TITLE_MAX)
    folder = root / (name or "Видео с сайта")
    return folder, not folder.is_dir()


def _fit(folder: Path, remote: Remote) -> None:
    """Shorten the sub-folder and the name when the whole path would be longer than Windows takes."""
    def length() -> int:
        return len(str(folder / remote.group / remote.name if remote.group else folder / remote.name))
    if length() > PATH_MAX and remote.group:
        remote.group = safe_name(remote.group, max(12, GROUP_MAX - (length() - PATH_MAX)))
    if length() > PATH_MAX:
        remote.name = safe_name(remote.name, max(24, len(remote.name) - (length() - PATH_MAX)), keep_ext=True)


def target_of(folder: Path, remote: Remote) -> Path:
    return folder / remote.group / remote.name if remote.group else folder / remote.name


def _look_at_folder(folder: Path, files: list, had=None) -> None:
    for remote in files:
        _fit(folder, remote)
        remote.have, remote.part = "", 0
        target = target_of(folder, remote)
        part = target.with_name(target.name + ".part")
        try:
            if target.is_file():                     # a stream's size is only an estimate: its file is the stream's
                same = remote.stream or not remote.size or target.stat().st_size == remote.size
                remote.have = "whole" if same else "other"
            elif part.is_file():
                remote.have, remote.part = "part", part.stat().st_size
            elif had is not None and had(remote):    # in another shape (a voice-over in the episode's MKV)
                remote.have = "whole"
        except OSError:
            continue


# ── reading the page ─────────────────────────────────────────────────────────────────
def is_frame_site(page_url: str, html: str = "") -> bool:
    host = (urlparse(str(page_url or "")).hostname or "").lower()
    return any(mark in host for mark in FRAME_SITES) or 'name="anime_alias"' in (html or "")[:20000]


def look(page, root: Path, *, say=None, stop_check=None, fetch=None) -> Found:
    """What the page offers and where it goes (the sizes asked of the site, what is in the folder already).
    ``fetch(address)`` → a page (``SavedPage``): the pages of the series' episodes and its own page are read with it
    (without it only this page is looked at)."""
    from core import video_episodes
    say = say or (lambda _text: None)
    stop = stop_check or (lambda: False)
    meta = video_meta.parse_page(page.html, page.url)
    files, streams, hidden, frames = find_files(page, meta=meta)
    title = str(meta.get("title") or "").strip()
    series_url = video_episodes.series_url(page.url)
    about = page
    episodes = video_episodes.links(video_meta._soup(page.html), page.url)
    if series_url and fetch is not None:
        about = _read(fetch, series_url, "Страница сериала", say) or page
        if about is not page:
            title = str(video_meta.parse_page(about.html, about.url).get("title") or "").strip() or title
            episodes = episodes or video_episodes.links(video_meta._soup(about.html), about.url)
    read = 0
    if episodes and fetch is not None:
        say(f"Серий на сайте: {len(episodes)}")
        ordered: list[Remote] = []
        for number, link in episodes:
            if stop():
                break
            if _same_page(link, page.url):
                theirs = files
            else:
                child = _read(fetch, link, f"Серия {number}", say)
                if child is None:
                    continue
                read += 1
                theirs, more_streams, more_hidden, more_frames = find_files(child)
                streams, hidden = streams + more_streams, hidden + more_hidden
                frames += [f for f in more_frames if f not in frames]
                say(f"Серия {number}: " + ("поток видео" if any(r.stream for r in theirs) else
                                          f"файлов {len(theirs)}" if theirs else "видео на странице нет"))
            for remote in theirs:
                if any(r.url == remote.url for r in ordered):
                    continue
                if remote.stream:                    # its name: the episode, without the series' title
                    stem = safe_name(video_episodes.episode_name(remote.title, title, number), NAME_MAX - 4)
                    remote.name = _unique((stem or f"Серия {number}") + ".mp4", ordered)
                ordered.append(remote)
        files = ordered + [f for f in files if all(f.url != r.url for r in ordered)]
    folder, new = target_folder(root, title, page.url, also=series_url, ids=meta.get("ids"))
    described = meta if about is page else video_meta.parse_page(about.html, about.url)
    found = Found(page=page, title=title, folder=folder, files=files, streams=streams, hidden=hidden, frames=frames,
                  frame_site=is_frame_site(page.url, page.html), new_folder=new, series_url=series_url, about=about,
                  episodes=read, facts=facts_of(described))
    return complete(found, say, stop)


def complete(found: Found, say, stop) -> Found:
    """The sizes of the files asked, the streams read (quality, length, why one cannot be taken), what of it is in the
    folder already — for a list of files from the page or from another site (``core/video_sources``)."""
    if found.files and found.resolve is None:      # addresses asked later have nothing to ask now
        _sizes([f for f in found.files if not f.stream], found.page.url, say, stop)
        _plan_streams(found, say, stop)
    _look_at_folder(found.folder, found.files, found.had)
    return found


def _read(fetch, address: str, what: str, say):
    """A page by ``fetch`` (None — it did not open: said in the journal)."""
    from core.video_meta_import import MetaImportError
    try:
        return fetch(address)
    except MetaImportError as exc:
        say(f"{what}: страница не открылась — {exc}")
        return None


def _plan_streams(found: Found, say, stop) -> None:
    """What each HLS stream gives: its quality, length and size, the extension of its file (``.mp4`` for MP4
    fragments, ``.ts`` for MPEG-TS), why it cannot be downloaded (encrypted…)."""
    from core import video_hls
    from core.video_meta_import import _session
    streams = [f for f in found.files if f.stream == "hls"]
    if not streams:
        return
    session = _session(next((f.url for f in streams if f.url), found.page.url))
    for remote in streams:
        if stop():
            return
        if remote.blocked:                           # the site gives no stream for it (said by the source)
            continue
        referer = remote.page or found.page.url
        try:
            if remote.alts:                          # qualities of addresses of their own: the next if one fails
                plan = video_hls.plan_of(session, [(remote.quality, remote.url)] + [tuple(a) for a in remote.alts],
                                         referer)
            else:
                plan = video_hls.plan(session, remote.url, referer)
        except video_hls.StreamError as exc:
            remote.blocked = str(exc)
            continue
        found.plans[remote.url] = plan
        remote.quality = plan.quality or remote.quality
        remote.size, remote.length, remote.blocked = plan.size, plan.media.duration, plan.blocked
        if plan.ext != ".mp4" and remote.name.lower().endswith(".mp4"):
            remote.name = _unique(remote.name[:-4] + plan.ext, [f for f in found.files if f is not remote])
    usable = [f for f in streams if not f.blocked]
    say(f"Потоков (HLS): {len(streams)}" + (f", скачать можно {len(usable)}" if len(usable) != len(streams) else ""))
    apart = [f for f in usable if found.plans.get(f.url) is not None and found.plans[f.url].audio is not None]
    if apart:
        say(f"Звук у {'потока' if len(apart) == 1 else 'потоков'} идёт отдельной дорожкой — программа скачает его и "
            "соберёт с видео в один файл")
    joined = [f for f in usable if found.plans.get(f.url) is not None and found.plans[f.url].media.breaks]
    if joined:
        say(f"{'Поток' if len(joined) == 1 else f'Потоки {len(joined)} файлов'} склеен{'' if len(joined) == 1 else 'ы'} "
            "из разных кусков (#EXT-X-DISCONTINUITY) — так сайт может вставлять рекламу; программа скачивает поток "
            "как он есть")
    for remote in streams:
        if remote.blocked:
            say(f"{remote.name}: {remote.blocked}")


def _sizes(files: list, referer: str, say, stop) -> None:
    from core.video_meta_import import _session
    import requests
    if not files:
        return
    session = _session(files[0].url)
    for remote in files[:SIZE_LOOKUPS]:
        if stop():
            return
        try:
            with session.head(remote.url, timeout=(10, 20), allow_redirects=True, headers={"Referer": referer}) as answer:
                if answer.status_code < 400 and "text/html" not in str(answer.headers.get("Content-Type") or ""):
                    remote.size = int(answer.headers.get("Content-Length") or 0)
        except (requests.RequestException, ValueError) as exc:
            _LOG.debug("size of %s: %s", remote.url, exc)
    known = sum(1 for f in files if f.size)
    if known:
        say(f"Размеры файлов: {known} из {len(files)} (всего {_mb(sum(f.size for f in files))})")


# ── downloading ─────────────────────────────────────────────────────────────────────
_METER_LOCK = threading.Lock()
_METER: dict = {}
_BUSY: list = []                    # one item while ``download`` runs (work in the background waits for its end)
_PAUSE = threading.Event()          # «Пауза» of the download going (one at a time; the owner, 02.11.2026: «паузы нет на
                                    # скачивании»): its file stops where it is, the download waits; «Продолжить» goes
                                    # on with the same file from the same place (its ``.part``)


def pause() -> None:
    _PAUSE.set()


def resume() -> None:
    _PAUSE.clear()


def paused() -> bool:
    return _PAUSE.is_set()


def _halt_words(have_text: str) -> str:
    """What a file stopped half-way says: on a pause it goes on from here; else from the next «Скачать»."""
    if _PAUSE.is_set():
        return f"пауза на {have_text} — продолжится с этого места по «Продолжить»"
    return f"остановлено на {have_text} — продолжится со следующего «Скачать»"


def busy() -> bool:
    """A download (and its putting together) goes on now: the files of the series folder are being written."""
    return bool(_BUSY)


def meter() -> dict:
    """How the download in progress goes (for the progress bar): file n of count, its name, bytes, speed."""
    with _METER_LOCK:
        return dict(_METER)


def _set_meter(**values) -> None:
    with _METER_LOCK:
        if values.get("clear"):
            _METER.clear()
            return
        _METER.update(values)


def _mb(size: float) -> str:
    size = float(size or 0)
    if size < 1024:
        return f"{int(size)} байт"
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.2f} ГБ".replace(".", ",")
    return f"{size / 1024 ** 2:.0f} МБ" if size >= 1024 ** 2 else f"{size / 1024:.0f} КБ"


def _total_of(content_range: str) -> int:
    found = re.search(r"/\s*(\d+)\s*$", str(content_range or ""))
    return int(found.group(1)) if found else 0


def _free(folder: Path) -> int:
    try:
        return int(shutil.disk_usage(folder).free)
    except OSError:
        return -1


def _rename(part: Path, target: Path) -> None:
    """``.part`` → the file, waiting a moment while an antivirus holds the new file (Windows)."""
    for attempt in range(20):
        try:
            if target.exists():
                raise DownloadError(f"В папке уже появился файл {target.name} — скачанное оставлено как {part.name}")
            part.replace(target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def _fetch(session, remote: Remote, part: Path, referer: str, stop, progress, *,
           browser_range: bool = False) -> tuple[str, int, int]:
    """One request for the rest of the file into ``part``: ("done"|"stopped"|"cut", bytes there, bytes in all).
    ``browser_range`` — the whole file asked the way a browser's player asks (``Range: bytes=0-``)."""
    import requests
    have = part.stat().st_size if part.is_file() else 0
    headers = {"Referer": referer, "Accept": "*/*", "Accept-Encoding": "identity"}
    if have:
        headers["Range"] = f"bytes={have}-"
    elif browser_range:
        headers["Range"] = "bytes=0-"
    try:
        with session.get(remote.url, stream=True, timeout=TIMEOUT, headers=headers, allow_redirects=True) as answer:
            status = answer.status_code
            if status == 416:                                   # asked from the end: the part may be the whole file
                total = _total_of(answer.headers.get("Content-Range"))
                if have and total and total == have:
                    return "done", have, total
                if not have:
                    return "done", 0, total                     # «bytes=0-» is past the end: the file is empty
                part.unlink(missing_ok=True)                    # a part that is not of this file: anew
                return "cut", 0, 0
            if status >= 400:
                raise DownloadError(f"сайт ответил {status}" + (" — нет такого файла" if status == 404 else
                                                                 " — не отдаёт файл программе" if status in (401, 403) else ""))
            if "text/html" in str(answer.headers.get("Content-Type") or "").lower():
                raise DownloadError("вместо видео сайт прислал страницу (проверка на роботов или ошибка) — "
                                    "скачайте этот файл браузером и положите в папку")
            if status == 206:                                   # the rest asked, or the whole one as a browser asks
                total = _total_of(answer.headers.get("Content-Range")) or (have + int(answer.headers.get("Content-Length") or 0))
                mode = "ab" if have else "wb"
            else:
                have, mode = 0, "wb"
                total = int(answer.headers.get("Content-Length") or 0)
            free = _free(part.parent)
            if total and free >= 0 and free < (total - have) + SPARE:
                raise DownloadError(f"на диске мало места: нужно {_mb(total - have)}, свободно {_mb(free)}")
            with open(part, mode) as out:
                for chunk in answer.iter_content(CHUNK):
                    if stop():
                        return "stopped", have, total
                    if chunk:
                        out.write(chunk)
                        have += len(chunk)
                        progress(have, total)
    except requests.RequestException as exc:
        from core.redaction import sanitize_text
        _LOG.info("download of %s cut off: %s", remote.name, sanitize_text(str(exc))[:300])
        return "cut", part.stat().st_size if part.is_file() else 0, 0
    if total and have < total:                          # a piece of it as asked (206) — the next one; else cut off
        return ("more" if status == 206 else "cut"), have, total
    return "done", have, total


def download_file(session, remote: Remote, folder: Path, referer: str, *, say, stop, number: str = "",
                  plan=None) -> str:
    """Download one file into its place: "done", "whole" (it was there), "other", "stopped", "failed" or "dead" (the
    site gives it empty).  An HLS stream (``remote.stream``) — its parts by ``_download_stream`` (``plan`` — what
    ``look`` read of it)."""
    target = target_of(folder, remote)
    part = target.with_name(target.name + ".part")
    label = f"{number}{remote.name}"
    referer = remote.page or referer       # the page it was found on (an episode's, another site's player's)
    if plan is not None and callable(getattr(plan, "fetch", None)) and not target.is_file():
        return plan.fetch(session, remote, target, say=say, stop=stop, label=label, referer=remote.page or referer)
    if remote.stream == "hls" and not target.is_file():
        return _download_stream(session, remote, target, remote.page or referer, plan, say=say, stop=stop, label=label)
    if target.is_file():
        size = target.stat().st_size
        if remote.size and size != remote.size and not remote.stream:
            say(f"{label}: в папке уже есть свой файл с этим именем ({_mb(size)}, на сайте {_mb(remote.size)}) — не трогаю")
            return "other"
        say(f"{label}: уже есть")
        return "whole"
    target.parent.mkdir(parents=True, exist_ok=True)
    started, last = time.monotonic(), 0.0
    start_bytes = part.stat().st_size if part.is_file() else 0
    say(f"{label}" + (f" ({_mb(remote.size)})" if remote.size else "") + (f" — докачиваю с {_mb(start_bytes)}" if start_bytes else ""))

    def progress(have: int, total: int) -> None:
        nonlocal last
        now = time.monotonic()
        if now - last >= METER_EVERY:
            last = now
            speed = (have - start_bytes) / max(0.001, now - started)
            _set_meter(name=remote.name, done=have, total=total or remote.size, speed=int(max(0.0, speed)))

    tries = attempts = empty = 0
    while True:
        attempts += 1
        before = part.stat().st_size if part.is_file() else 0
        try:
            state, have, total = _fetch(session, remote, part, referer, stop, progress, browser_range=empty > 0)
            if state == "done" and have <= 0:
                raise _Empty("сайт прислал пустой файл")
            if state == "cut" and have <= 0 and before <= 0:
                raise _Empty("сайт закрывает связь, не прислав ни байта")
        except _Empty as exc:
            part.unlink(missing_ok=True)
            empty += 1
            if empty < EMPTY_TRIES:
                say(f"{label}: {exc} — прошу ещё раз, как просит плеер браузера")
                if stop():
                    return "stopped"
                continue
            if remote.alts:                           # the player's other quality of this file
                quality, address = remote.alts.pop(0)
                say(f"{label}: {exc}" + (f" в {remote.quality}" if remote.quality else "")
                    + f" — беру {quality or 'другую ссылку плеера'}")
                remote.url, remote.quality, empty, tries = address, quality, 0, 0
                continue
            say(f"{label}: не скачан — {exc}. Похоже, у сайта этот файл не работает: его плеер на странице тоже не "
                "покажет это видео; можно поискать его на другом сайте")
            return "dead"
        except DownloadError as exc:
            say(f"{label}: не скачан — {exc}")
            if part.is_file() and part.stat().st_size == 0:
                part.unlink(missing_ok=True)
            return "failed"
        except OSError as exc:                        # the disk: full, gone, not writable
            say(f"{label}: не записался в папку — {exc}")
            return "failed"
        if state == "stopped":
            say(f"{label}: " + _halt_words(_mb(have) + (f" из {_mb(total)}" if total else "")))
            return "stopped"
        if state == "done":
            break
        if have > before:
            tries = 0                                 # bytes came: the connection breaks, the file is there
        if state == "more" and have > before and attempts < ATTEMPTS:
            continue                                  # the site gives the file in pieces: the next one at once
        tries += 1
        if tries >= TRIES or attempts >= ATTEMPTS:
            say(f"{label}: связь обрывается — скачано {_mb(have)}" + (f" из {_mb(total)}" if total else "")
                + "; нажмите «Скачать» ещё раз, файл докачается")
            return "failed"
        say(f"{label}: оборвалось на {_mb(have)} — продолжаю")
        if stop():
            return "stopped"
        time.sleep(min(2.0 * tries, 6.0))
    try:
        _rename(part, target)
    except (DownloadError, OSError) as exc:
        say(f"{label}: скачан, но не переименован — {exc}")
        return "failed"
    seconds = max(0.001, time.monotonic() - started)
    say(f"{label}: готово — {_mb(have)} за {int(seconds // 60)}:{int(seconds % 60):02d}")
    return "done"


def _download_stream(session, remote: Remote, target: Path, referer: str, plan, *, say, stop, label: str) -> str:
    """An HLS stream into ``target``: its parts appended to ``<name>.part`` from where they stopped, the next quality
    when the site does not give one, its sound when it comes apart (``_join_sound``), its subtitles next to it
    (``<name>.<language>.vtt``)."""
    from core import video_hls
    part = target.with_name(target.name + ".part")
    if remote.blocked:
        say(f"{label}: не скачан — {remote.blocked}")
        return "failed"
    try:
        plan = plan or video_hls.plan(session, remote.url, referer)
    except video_hls.StreamError as exc:
        say(f"{label}: не скачан — {exc}")
        return "failed"
    target.parent.mkdir(parents=True, exist_ok=True)
    started, last = time.monotonic(), 0.0
    while True:
        if plan.blocked:
            say(f"{label}: не скачан — {plan.blocked}")
            return "failed"
        parts = len(plan.media.segments)
        say(f"{label}: поток" + (f" {plan.quality}" if plan.quality else "") + f", частей {parts}"
            + (f", {_clock(plan.media.duration)}" if plan.media.duration else ""))

        def progress(done: int, total: int, size: int) -> None:
            nonlocal last
            now = time.monotonic()
            if now - last >= METER_EVERY or done == total:
                last = now
                _set_meter(name=remote.name, done=size, total=int(size * total / max(1, done)),
                           speed=int(size / max(0.001, now - started)))
        try:
            state = video_hls.download(session, plan, part, referer, stop=stop, progress=progress)
            break
        except video_hls.StreamError as exc:
            following = None
            try:
                following = video_hls.next_quality(session, plan, referer)
            except video_hls.StreamError as more:
                _LOG.info("next quality of %s not read: %s", remote.name, more)
            if following is None:
                say(f"{label}: не скачан — {exc}")
                return "failed"
            say(f"{label}: {exc} в {plan.quality or 'этом качестве'} — беру {following.quality or 'другое качество'}")
            video_hls.forget(part)
            part.unlink(missing_ok=True)
            plan = following
        except OSError as exc:                       # the disk: full, gone, not writable
            say(f"{label}: не записался в папку — {exc}")
            return "failed"
    if state == "stopped":
        have = part.stat().st_size if part.is_file() else 0
        say(f"{label}: " + _halt_words(_mb(have)))
        return "stopped"
    final = part
    if plan.audio is not None:
        final, state = _join_sound(session, plan, part, referer, say=say, stop=stop, label=label, name=remote.name)
        if final is None:
            return state
    try:
        _rename(final, target)
    except (DownloadError, OSError) as exc:
        say(f"{label}: скачан, но не переименован — {exc}")
        return "failed"
    video_hls.forget(part)
    if final is not part:                            # the picture and the sound are in the file now
        for kept in (part, part.with_name(part.name + ".audio")):
            video_hls.forget(kept)
            kept.unlink(missing_ok=True)
    for language, text in video_hls.subtitles(session, plan, referer, stop):
        place = target.with_name(f"{target.stem}.{safe_name(language, 16) or 'und'}.vtt")
        if not place.exists():
            try:
                place.write_text(text, encoding="utf-8")
                say(f"{label}: субтитры {language} — {place.name}")
            except OSError as exc:
                say(f"{label}: субтитры {language} не записались — {exc}")
    seconds = max(0.001, time.monotonic() - started)
    say(f"{label}: готово — {_mb(target.stat().st_size)} за {int(seconds // 60)}:{int(seconds % 60):02d}")
    return "done"


def _join_sound(session, plan, part: Path, referer: str, *, say, stop, label: str, name: str):
    """The sound of a stream that comes apart: downloaded next to its picture (``<part>.audio``, from where it stopped)
    and joined with it into ``<part>.joined`` (``core/fmp4_mux``).  (that file, "done") — or (None, "stopped" |
    "failed"), said in words."""
    from core import fmp4_mux, video_hls
    sound = part.with_name(part.name + ".audio")
    joined = part.with_name(part.name + ".joined")
    say(f"{label}: звук идёт отдельной дорожкой" + (f" ({plan.audio_name})" if plan.audio_name else "")
        + " — качаю его и собираю с видео в один файл")
    started, last = time.monotonic(), 0.0

    def progress(done: int, total: int, size: int) -> None:
        nonlocal last
        now = time.monotonic()
        if now - last >= METER_EVERY or done == total:
            last = now
            _set_meter(name=f"{name} — звук", done=size, total=int(size * total / max(1, done)),
                       speed=int(size / max(0.001, now - started)))
    try:
        state = video_hls.download(session, plan.audio, sound, referer, stop=stop, progress=progress)
    except video_hls.StreamError as exc:
        say(f"{label}: не скачан — звук: {exc}")
        return None, "failed"
    except OSError as exc:
        say(f"{label}: звук не записался в папку — {exc}")
        return None, "failed"
    if state == "stopped":
        say(f"{label}: видео скачано, звук остановлен — продолжится со следующего «Скачать»")
        return None, "stopped"
    try:
        state = fmp4_mux.mux(part, sound, joined, stop=stop)
    except fmp4_mux.MuxError as exc:
        joined.unlink(missing_ok=True)
        say(f"{label}: видео и звук скачаны, но в один файл не собрались — {exc}")
        return None, "failed"
    except OSError as exc:
        joined.unlink(missing_ok=True)
        say(f"{label}: видео и звук не собрались в один файл — {exc}")
        return None, "failed"
    if state != "done":
        joined.unlink(missing_ok=True)
        say(f"{label}: остановлено, когда собирались видео и звук — соберутся со следующего «Скачать»")
        return None, "stopped"
    return joined, "done"


def _clock(seconds: float) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else \
        f"{seconds // 60}:{seconds % 60:02d}"


def _describe(found: Found, say, stop) -> None:
    """The description and the poster of the series, before its files: it shows on the tab at once — from the series'
    own page when the page was an episode's (its address is the series' then)."""
    from core import video_meta_import as I
    folder = found.folder
    series = video_library.Series(id="", title=found.title or folder.name, path=str(folder),
                                  poster=video_library._find_poster(video_library.series_poster_names(folder)),
                                  folder_title=folder.name)
    try:
        I.import_into(series, found.about or found.page, progress=say, stop_check=stop, chosen_poster=found.poster)
    except I.MetaImportError as exc:
        say(f"Описание не записано: {exc}")


def download(found: Found, chosen: list, root: Path, *, say, stop_check, quality: str = "", merge: bool = False,
             start_paused: bool = False, repicture: bool = False, planned=None) -> dict:
    """Download the chosen files of a page into the series folder: {"done", "whole", "failed", "folder", "series"}.
    ``quality`` — «Качество» of the page for addresses asked right before a file (``Found.resolve``); ``merge`` — then
    ``Found.after`` (the voice-overs of each episode into one file); ``start_paused`` — begun on «Пауза» (one paused
    when the program closed).  (v5.44.150) With ``merge`` the picture of an episode is the title's best, lent by a
    release that was not asked for when it has a better one (``Resolver.roles``: its file joins the download for
    the picture alone); ``repicture`` — the episodes already in the folder get it too (their picture taken anew;
    nothing need be chosen then); ``planned`` — called with the places of every file the download takes, the lent
    pictures among them (the page of the series shows them on its tiles)."""
    from core.video_meta_import import _session
    places = [n for n in dict.fromkeys(chosen) if isinstance(n, int) and 0 <= n < len(found.files)]
    files = [found.files[n] for n in places]
    repicture = bool(repicture and merge and found.after is not None and found.resolve is not None
                     and hasattr(found.resolve, "roles"))
    if not files and not repicture:
        return {"failed": "Не выбрано ни одного файла"}
    states = {n: "queued" for n in places}         # each file's state for its tile: queued, active, then its end
    try:
        found.folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"failed": f"Папка сериала не создаётся: {exc}"}
    say(f"Папка: {found.folder}")
    _describe(found, say, stop_check)
    video_library.LIBRARY.invalidate()
    session = _session(files[0].url if files else (found.page.url or "https://site.yummyani.me/"))
    counts = {"done": 0, "whole": 0, "other": 0, "failed": 0, "dead": 0, "stopped": 0, "merged": 0}
    roles = {}                                     # into one file: voice-overs wanted for their sound only — lower
    if merge and found.after is not None and found.resolve is not None and hasattr(found.resolve, "roles"):
        from core import video_merge               # (without PyAV nothing is put together: every file as chosen)
        if video_merge.available():
            again = None                           # (v5.44.150) the episodes of the folder whose picture may be
            if repicture:                          # taken anew: the ones put together and the ones waiting for it
                waiting = found.after.waiting() if callable(getattr(found.after, "waiting", None)) else set()
                again = {p.stem for p in found.folder.glob("*.mkv")} | set(waiting)
            if hasattr(found.resolve, "donors"):   # (a resolver of another source lends no pictures)
                roles = found.resolve.roles(files, quality, session=session, say=say, stop=stop_check,
                                            repicture=again)
            else:
                roles = found.resolve.roles(files, quality, session=session, say=say, stop=stop_check)
            for remote in getattr(found.resolve, "donors", None) or []:
                place = next((n for n, f in enumerate(found.files) if f is remote), None)
                if place is not None and place not in places:      # a picture lent: its file joins the download
                    places.append(place)
                    files.append(remote)
                    states[place] = "queued"
            for line in (found.resolve.lent_words() if hasattr(found.resolve, "lent_words") else []):
                say(line)
        sound = sum(1 for role in roles.values() if role == "sound")
        if sound:
            say(f"Озвучки собираются в один файл: картинку серии даёт одна озвучка, остальные ({sound}) качаются "
                "ради звука — у CVH только звук (без картинки), у Kodik в 360p: звук тот же, скачивать в разы меньше. "
                "По сериям: сначала картинка серии, потом звук — и серия сразу собирается")
        if roles:
            # (v5.44.150) episode by episode — its picture, then the others' sound, and it is put together at once
            # (it was every picture of the season first: the first episode to watch came after them all).  The
            # picture first all the same: one that does not come is given by another voice-over of the episode,
            # not downloaded yet (``Resolver.instead``)
            first: dict = {}
            for n, remote in enumerate(files):
                first.setdefault(remote.name.rsplit(".", 1)[0], n)
            pairs = sorted(zip(places, files), key=lambda pair: (first[pair[1].name.rsplit(".", 1)[0]],
                                                                 roles.get(id(pair[1])) == "sound"))
            places, files = [p for p, _f in pairs], [f for _p, f in pairs]
    if planned is not None and len(places) != len([n for n in dict.fromkeys(chosen) if isinstance(n, int)
                                                    and 0 <= n < len(found.files)]):
        planned(list(places))
    if not files:                                  # nothing chosen, and no picture of the folder to take anew
        say("Картинку серий в папке заменить нечем: лучше, чем есть, у тайтла нет")
        return {"done": 0, "whole": 0, "failed_files": 0, "folder": str(found.folder),
                "series": video_library._id_for(root, found.folder), "stopped": False, "states": {}}
    # into one file: each episode as soon as its files have all come (not only at the end — owner, 05.11.2026: a season
    # stopped half-way stayed half in «Озвучка JAM»), and first what an earlier download left in the sub-folders
    merger = found.after if merge and found.after is not None and callable(getattr(found.after, "waiting", None)) else None
    if merger is not None:
        from core import video_merge
        if not video_merge.available():
            merger = None
    pending: dict = {}                             # an episode -> its files of this download not ended yet
    if merger is not None:
        for remote in files:
            if merger.takes(remote):
                pending[merger.stem(remote)] = pending.get(merger.stem(remote), 0) + 1
        left = sorted(merger.waiting() - set(pending), key=video_library.natural_key)
        if left and not stop_check():
            say(f"Сначала собираю серии, оставшиеся несобранными с прошлого раза: {len(left)}")
            counts["merged"] += int((merger(say=say, stop=stop_check, stems=set(left)) or {}).get("episodes") or 0)
            _set_meter(clear=True)
    _BUSY.append(1)
    if start_paused:                               # paused when the program closed: paused again
        _PAUSE.set()
        say("ПАУЗА с прошлого запуска — «Продолжить», чтобы качать дальше")
    else:
        _PAUSE.clear()                             # a download begins going (a pause of one before is not its)

    def halt() -> bool:                            # what stops a file half-way: «Остановить» or «Пауза»
        return stop_check() or _PAUSE.is_set()

    def wait_paused(place) -> bool:
        """While «Пауза»: the file's tile «пауза», the meter says it; True — «Продолжить», False — «Остановить»."""
        states[place] = "paused"
        _set_meter(states=dict(states), paused=True, speed=0)
        while _PAUSE.is_set() and not stop_check():
            time.sleep(0.25)
        _set_meter(paused=False)
        return not stop_check()

    def one(n: int, place: int, remote) -> str:
        """One file: its address asked (``Found.resolve``), then its bytes — its end ("done", "stopped" …)."""
        states[place] = "active"
        _set_meter(index=n, count=len(files), name=remote.name, file=place, states=dict(states), done=remote.part,
                   total=remote.size, speed=0)
        number = f"{n}/{len(files)} " if len(files) > 1 else ""
        plan = found.plans.get(remote.url)
        state = ""
        if found.had is not None and not target_of(found.folder, remote).is_file() and found.had(remote) \
                and roles.get(id(remote)) != "picture":   # its sound is in the file, its picture is wanted
            say(f"{number}{remote.title or remote.name}" + (f" ({remote.group})" if remote.group else "")
                + ": уже есть в общем файле серии")
            state = "whole"
        if not state and found.resolve is not None and not target_of(found.folder, remote).is_file():
            try:
                plan = found.resolve(remote, session=session, quality=quality, say=say, stop=halt,
                                     role=roles.get(id(remote), "")) or plan
            except DownloadError as exc:
                state = "stopped" if halt() else "failed"
                say(f"{number}{remote.title or remote.name}" + (f" ({remote.group})" if remote.group else "")
                    + ((": пауза" if _PAUSE.is_set() else ": остановлено") if state == "stopped"
                       else f": не скачан — {exc}"))
        if not state:
            state = download_file(session, remote, found.folder, found.page.url, say=say, stop=halt,
                                  number=number, plan=plan)
        if found.resolve is not None and hasattr(found.resolve, "finished"):
            found.resolve.finished(remote, state)
        if roles.get(id(remote)) == "picture" and state in ("failed", "dead") and hasattr(found.resolve, "instead"):
            other = found.resolve.instead(remote, roles)
            if other is not None:
                say(f"{remote.title or remote.name}: картинку серии даст {other.group or other.name} — "
                    "она скачается в выбранном качестве")
        return state
    try:
        for n, (place, remote) in enumerate(zip(places, files), 1):
            if _PAUSE.is_set() and not stop_check():   # «Пауза» between two files
                wait_paused(place)
            if stop_check():
                counts["stopped"] += 1
                break
            state = one(n, place, remote)
            while state == "stopped" and _PAUSE.is_set() and not stop_check():
                if not wait_paused(place):             # «Остановить» while paused
                    break
                say(f"{remote.title or remote.name}" + (f" ({remote.group})" if remote.group else "")
                    + ": продолжаю с того же места")
                state = one(n, place, remote)
            states[place] = state
            _set_meter(states=dict(states))
            counts[state] += 1
            if state in ("done", "whole"):
                video_library.LIBRARY.invalidate()
            if state == "stopped":
                break
            stem = merger.stem(remote) if merger is not None and merger.takes(remote) else None
            if stem in pending:                    # the episode's last file: its voice-overs into one file now
                pending[stem] -= 1
                if pending[stem] <= 0:
                    del pending[stem]
                    if not stop_check():
                        counts["merged"] += int((merger(say=say, stop=stop_check, stems={stem}) or {}).get("episodes") or 0)
                        _set_meter(merging=False)
        if merge and found.after is not None and not counts["stopped"] and not stop_check():
            _set_meter(clear=True)                 # what is left (an episode not put together above)
            merged = found.after(say=say, stop=stop_check) or {}
            counts["merged"] += int(merged.get("episodes") or 0)
    finally:
        _BUSY.pop()
        _PAUSE.clear()
        _set_meter(clear=True)
        video_library.LIBRARY.invalidate()
    summary = [f"скачано {counts['done']}"]
    if counts["whole"]:
        summary.append(f"уже были {counts['whole']}")
    if counts["other"]:
        summary.append(f"не тронуты (свои файлы) {counts['other']}")
    if counts["failed"]:
        summary.append(f"не скачаны {counts['failed']}")
    if counts["dead"]:
        summary.append(f"пустые на сайте {counts['dead']}")
    if counts["merged"]:
        summary.append(f"собрано в общие файлы серий {counts['merged']}")
    say("Итог: " + ", ".join(summary))
    series_id = video_library._id_for(root, found.folder)
    result = {"done": counts["done"], "whole": counts["whole"], "failed_files": counts["failed"] + counts["dead"],
              "folder": str(found.folder), "series": series_id, "stopped": bool(counts["stopped"]),
              "states": {str(n): state for n, state in states.items()}}
    if counts["merged"]:
        result["merged"] = counts["merged"]
    if (counts["failed"] or counts["dead"]) and not counts["done"] and not counts["whole"] and not counts["stopped"]:
        result["failed"] = ("Сайт не отдаёт эти файлы: они пустые, и его плеер на странице их тоже не покажет — "
                            "поищите тайтл на другом сайте" if not counts["failed"] else
                            "Ни один файл не скачался — причины в журнале выше")
    return result
