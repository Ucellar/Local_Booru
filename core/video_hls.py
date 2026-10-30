"""A video a site plays as an HLS stream (``.m3u8``, «Скачать с сайта»): its parts downloaded one after another and
put together into one file — what the browser's player gets while it plays (hentaihaven and the like).

Only an open stream is taken.  A stream with a key (``#EXT-X-KEY`` with a method other than NONE — AES-128,
SAMPLE-AES) or with DRM (``#EXT-X-SESSION-KEY``) is the site's protection: it is said in words and left alone, as
an encrypted player link is (``core/video_download``).

How the file is made:

* the master playlist lists the qualities (``#EXT-X-STREAM-INF``): the largest picture is taken (of equal ones —
  H.264 before others, it plays everywhere), the rest stay in case the site does not give it; a site that names its
  qualities by addresses of their own (AniLiberty: ``hls_1080``, ``hls_720``…) — the same way, in its order
  (``plan_of``);
* its media playlist lists the parts (``#EXTINF``): fragments of MP4 after their header (``#EXT-X-MAP``) make one
  ``.mp4``; MPEG-TS parts make one ``.ts`` (the program plays it; nothing is re-encoded);
* a quality whose sound is a stream of its own (``#EXT-X-MEDIA TYPE=AUDIO`` with an address — the group's default one):
  both are downloaded and joined into one ``.mp4`` (``core/fmp4_mux.py``) when both are MP4 fragments; sound of
  another kind next to a picture is said in words;
* the parts are appended to ``<name>.part`` one by one; how many are in is kept beside it (``<name>.part.hls``), so a
  download stopped or cut off goes on from the next part (the part file is checked against what was noted);
* subtitles of the stream (``TYPE=SUBTITLES``, WebVTT) are put together into ``<name>.<language>.vtt`` next to it.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

_LOG = logging.getLogger("local_booru.video")

MAX_PLAYLIST = 4 * 1024 * 1024          # bytes of a playlist at most
MAX_PARTS = 20_000                      # parts of one stream at most
PART_TRIES = 4                          # a part cut off is asked again this many times
TIMEOUT = (15, 60)
CHUNK = 256 * 1024


class StreamError(Exception):
    """Said to the person as it is."""


@dataclass
class Variant:
    url: str
    bandwidth: int = 0
    width: int = 0
    height: int = 0
    codecs: str = ""
    audio: str = ""                     # the group of its sound (#EXT-X-MEDIA TYPE=AUDIO)
    subtitles: str = ""                 # the group of its subtitles

    @property
    def quality(self) -> str:
        return f"{self.height}p" if self.height else (f"{self.bandwidth // 1000} кбит/с" if self.bandwidth else "")


@dataclass
class Rendition:
    kind: str                           # AUDIO, SUBTITLES, CLOSED-CAPTIONS, VIDEO
    group: str
    name: str = ""
    language: str = ""
    url: str = ""                       # "" — inside the parts of the quality itself
    default: bool = False
    autoselect: bool = False


@dataclass
class Segment:
    url: str
    duration: float = 0.0
    start: int = -1                     # a byte range of the address (#EXT-X-BYTERANGE), -1 — the whole of it
    length: int = 0


@dataclass
class Media:
    url: str
    segments: list = field(default_factory=list)
    init: Segment | None = None         # the header of MP4 fragments (#EXT-X-MAP)
    maps: int = 0                       # how many headers the playlist names (more than one: not one file)
    key: str = ""                       # the method of a key ("" or NONE — none)
    ended: bool = True                  # #EXT-X-ENDLIST (a live stream never ends)
    breaks: int = 0                     # #EXT-X-DISCONTINUITY: joins of other pieces (an inserted advertisement?)

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)

    @property
    def mp4(self) -> bool:
        return self.init is not None


@dataclass
class Plan:
    """What will be downloaded for one stream."""
    media: Media
    variant: Variant | None = None
    others: list = field(default_factory=list)      # other qualities, the best first
    subtitles: list = field(default_factory=list)   # Rendition of the subtitles
    renditions: list = field(default_factory=list)  # all of the master playlist (for another quality)
    blocked: str = ""                                # why it cannot be downloaded ("" — it can)
    audio: "Plan | None" = None                      # the sound, a stream of its own (joined to the picture after)
    audio_name: str = ""                             # its name or language in the playlist

    @property
    def ext(self) -> str:
        return ".mp4" if self.media.mp4 else ".ts"

    @property
    def quality(self) -> str:
        return self.variant.quality if self.variant is not None else ""

    @property
    def size(self) -> int:
        """Bytes it will take, as the playlist says (its bit rate × its length); 0 — not said."""
        if self.variant is not None and self.variant.bandwidth and self.media.duration:
            return int(self.variant.bandwidth * self.media.duration / 8)
        return 0


# ── reading playlists ──────────────────────────────────────────────────────────────
_ATTR = re.compile(r'([A-Z0-9-]+)=("(?:[^"]*)"|[^,]*)')


def _attrs(text: str) -> dict:
    return {m.group(1): m.group(2).strip('"') for m in _ATTR.finditer(text or "")}


def _lines(text: str) -> list[str]:
    if not str(text or "").lstrip("﻿").startswith("#EXTM3U"):
        raise StreamError("сайт прислал не список частей потока (нет #EXTM3U)")
    return [line.strip() for line in str(text).lstrip("﻿").splitlines() if line.strip()]


def is_master(text: str) -> bool:
    return "#EXT-X-STREAM-INF" in str(text or "")


def parse_master(text: str, base: str) -> tuple[list[Variant], list[Rendition]]:
    variants, renditions = [], []
    lines = _lines(text)
    for n, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF:"):
            a = _attrs(line.split(":", 1)[1])
            uri = next((x for x in lines[n + 1:] if not x.startswith("#")), "")
            if not uri:
                continue
            width = height = 0
            size = re.match(r"(\d+)x(\d+)", a.get("RESOLUTION", ""))
            if size:
                width, height = int(size.group(1)), int(size.group(2))
            variants.append(Variant(url=urljoin(base, uri), bandwidth=int(a.get("BANDWIDTH", "0") or 0),
                                    width=width, height=height, codecs=a.get("CODECS", ""),
                                    audio=a.get("AUDIO", ""), subtitles=a.get("SUBTITLES", "")))
        elif line.startswith("#EXT-X-MEDIA:"):
            a = _attrs(line.split(":", 1)[1])
            renditions.append(Rendition(kind=a.get("TYPE", ""), group=a.get("GROUP-ID", ""), name=a.get("NAME", ""),
                                        language=a.get("LANGUAGE", ""),
                                        url=urljoin(base, a["URI"]) if a.get("URI") else "",
                                        default=a.get("DEFAULT", "").upper() == "YES",
                                        autoselect=a.get("AUTOSELECT", "").upper() == "YES"))
    return variants, renditions


def parse_media(text: str, base: str) -> Media:
    media = Media(url=base, ended=False)
    lines = _lines(text)
    duration = 0.0
    byterange: tuple[int, int] | None = None
    next_start = 0
    for line in lines:
        if line.startswith("#EXTINF:"):
            try:
                duration = float(line.split(":", 1)[1].split(",", 1)[0] or 0)
            except ValueError:
                duration = 0.0
        elif line.startswith("#EXT-X-BYTERANGE:"):
            length, _, start = line.split(":", 1)[1].partition("@")
            byterange = (int(start) if start else next_start, int(length))
        elif line.startswith("#EXT-X-KEY:"):
            method = _attrs(line.split(":", 1)[1]).get("METHOD", "NONE").upper()
            if method != "NONE":
                media.key = method
        elif line.startswith("#EXT-X-SESSION-KEY:"):
            media.key = _attrs(line.split(":", 1)[1]).get("METHOD", "SESSION-KEY").upper() or "SESSION-KEY"
        elif line.startswith("#EXT-X-MAP:"):
            a = _attrs(line.split(":", 1)[1])
            media.maps += 1
            if media.init is None and a.get("URI"):
                start, length = -1, 0
                if a.get("BYTERANGE"):
                    raw_length, _, raw_start = a["BYTERANGE"].partition("@")
                    start, length = int(raw_start or 0), int(raw_length)
                media.init = Segment(url=urljoin(base, a["URI"]), start=start, length=length)
        elif line.startswith("#EXT-X-ENDLIST"):
            media.ended = True
        elif line.startswith("#EXT-X-DISCONTINUITY") and not line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE"):
            media.breaks += 1
        elif not line.startswith("#"):
            start, length = byterange if byterange else (-1, 0)
            media.segments.append(Segment(url=urljoin(base, line), duration=duration, start=start, length=length))
            next_start = start + length if byterange else 0
            duration, byterange = 0.0, None
            if len(media.segments) > MAX_PARTS:
                raise StreamError(f"в потоке больше {MAX_PARTS} частей")
    return media


def _rank(variant: Variant) -> tuple:
    h264 = variant.codecs.lower().startswith("avc1") or not variant.codecs
    return (variant.height, variant.width, 1 if h264 else 0, variant.bandwidth)


def choose(variants: list[Variant]) -> list[Variant]:
    """The qualities, the one to take first (the largest picture; of equal ones H.264, then the higher bit rate)."""
    return sorted(variants, key=_rank, reverse=True)


# ── the site ───────────────────────────────────────────────────────────────────────
def _get_text(session, url: str, referer: str) -> str:
    import requests
    try:
        with session.get(url, timeout=TIMEOUT, stream=True, headers={"Referer": referer, "Accept": "*/*"}) as answer:
            if answer.status_code >= 400:
                raise StreamError(f"сайт ответил {answer.status_code} на список частей потока")
            body = bytearray()
            for chunk in answer.iter_content(64 * 1024):
                body += chunk
                if len(body) > MAX_PLAYLIST:
                    raise StreamError("список частей потока слишком большой")
            return bytes(body).decode("utf-8", "replace")
    except requests.RequestException as exc:
        from core.redaction import sanitize_text
        raise StreamError(f"список частей потока не получен: {sanitize_text(str(exc))[:200]}") from exc


def plan(session, url: str, referer: str) -> Plan:
    """What the stream at ``url`` gives (its master playlist, or a media playlist itself)."""
    text = _get_text(session, url, referer)
    if not is_master(text):
        media = parse_media(text, url)
        return _check(Plan(media=media))
    variants, renditions = parse_master(text, url)
    if "#EXT-X-SESSION-KEY" in text:
        return Plan(media=Media(url=url), blocked="поток зашифрован (ключ на весь поток) — это защита сайта, программа "
                                                 "такие не скачивает")
    ordered = choose(variants)
    if not ordered:
        raise StreamError("в потоке нет ни одного качества")
    return for_variant(session, ordered[0], referer, others=ordered[1:], renditions=renditions)


def sound_of(variant: Variant, renditions: list) -> Rendition | None:
    """The sound a player plays with this quality when it is a stream of its own: the group's default (else the one a
    player picks by itself, else the first); None — the sound is inside the picture's parts."""
    group = [r for r in renditions if r.kind == "AUDIO" and variant.audio and r.group == variant.audio]
    if not group:
        return None
    chosen = next((r for r in group if r.default), None) or next((r for r in group if r.autoselect), None) or group[0]
    return chosen if chosen.url else None


def for_variant(session, variant: Variant, referer: str, *, others: list, renditions: list, _depth: int = 0) -> Plan:
    """The plan of one quality of a master playlist (its media playlist read; its sound's too when it is apart).  A
    quality that is a master playlist itself (a site that names its qualities by addresses of their own — ``plan_of``)
    gives its best one, its others before the qualities after it."""
    text = _get_text(session, variant.url, referer)
    if is_master(text):
        if "#EXT-X-SESSION-KEY" in text:
            return Plan(media=Media(url=variant.url), variant=variant, blocked="поток зашифрован (ключ на весь "
                        "поток) — это защита сайта, программа такие не скачивает")
        inner, inner_renditions = parse_master(text, variant.url)
        ordered = choose(inner)
        if not ordered or _depth >= 2:
            raise StreamError("в потоке нет ни одного качества")
        best = ordered[0]
        if not best.height and variant.height:     # the site's name of the quality when the playlist says none
            best.height, best.width = variant.height, variant.width
        return for_variant(session, best, referer, others=ordered[1:] + list(others), renditions=inner_renditions,
                           _depth=_depth + 1)
    media = parse_media(text, variant.url)
    subtitles = [r for r in renditions if r.kind == "SUBTITLES" and r.url
                 and (not variant.subtitles or r.group == variant.subtitles)]
    found = Plan(media=media, variant=variant, others=list(others), subtitles=subtitles, renditions=list(renditions))
    sound = sound_of(variant, renditions)
    if sound is not None:
        found.audio_name = sound.name or sound.language
        audio = _check(Plan(media=parse_media(_get_text(session, sound.url, referer), sound.url)))
        if audio.blocked:
            found.blocked = "звук: " + audio.blocked
        elif not (media.mp4 and audio.media.mp4):
            found.blocked = ("звук у потока идёт отдельной дорожкой не кусками MP4 — собрать его с видео в один "
                             "файл программа не умеет")
        else:
            found.audio = audio
    return _check(found)


def next_quality(session, found: Plan, referer: str) -> Plan | None:
    """The plan of the next quality of the stream (None — no other)."""
    if not found.others:
        return None
    return for_variant(session, found.others[0], referer, others=found.others[1:], renditions=found.renditions)


def height_of(label: str) -> int:
    """The picture's height a site's name of a quality says («1080p», «720») — 0 when it says none."""
    found = re.search(r"(\d{3,4})\s*[pP]?\b", str(label or ""))
    return int(found.group(1)) if found else 0


def plan_of(session, qualities: list, referer: str) -> Plan:
    """The plan of a stream a site gives in qualities of its own addresses, the best first ((label, address): AniLiberty
    — ``hls_1080``, ``hls_720``, ``hls_480``): the first that opens; the ones after it are the next qualities when it
    does not give its parts (``next_quality``)."""
    variants = [Variant(url=str(address), height=height_of(label)) for label, address in qualities if address]
    failure = None
    for n, variant in enumerate(variants):
        try:
            return for_variant(session, variant, referer, others=variants[n + 1:], renditions=[])
        except StreamError as exc:
            failure = exc
    raise failure or StreamError("в потоке нет ни одного качества")


def _check(found: Plan) -> Plan:
    media = found.media
    if found.blocked:
        return found
    if media.key:
        found.blocked = f"поток зашифрован ({media.key}) — это защита сайта, программа такие не скачивает"
    elif not media.ended:
        found.blocked = "это прямая трансляция, а не запись — её не скачать целиком"
    elif not media.segments:
        found.blocked = "в потоке нет ни одной части"
    elif media.maps > 1:
        found.blocked = "у частей потока разные заголовки — в один файл они не складываются"
    return found


# ── downloading ────────────────────────────────────────────────────────────────────
def _state_file(part: Path) -> Path:
    return part.with_name(part.name + ".hls")


def _read_state(part: Path, media: Media) -> int:
    """How many parts are in ``part`` already (0 — start anew: no note, another stream, or the file is not as noted)."""
    try:
        state = json.loads(_state_file(part).read_text(encoding="utf-8"))
        if state.get("playlist") != _key(media.url) or not part.is_file():
            return 0
        if part.stat().st_size != int(state.get("bytes") or -1):
            return 0
        return max(0, min(int(state.get("done") or 0), len(media.segments)))
    except (OSError, ValueError, TypeError):
        return 0


def _key(url: str) -> str:
    """A playlist's address without what changes between visits (a signature in the query)."""
    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}"


def _write_state(part: Path, media: Media, done: int) -> None:
    note = {"playlist": _key(media.url), "done": done, "bytes": part.stat().st_size if part.is_file() else 0}
    tmp = _state_file(part).with_suffix(".tmp")
    tmp.write_text(json.dumps(note), encoding="utf-8")
    tmp.replace(_state_file(part))


def forget(part: Path) -> None:
    _state_file(part).unlink(missing_ok=True)


def _fetch_piece(session, piece: Segment, referer: str, stop) -> bytes | None:
    """The bytes of one part (None — stopped); raises StreamError after its tries."""
    import requests
    headers = {"Referer": referer, "Accept": "*/*", "Accept-Encoding": "identity"}
    if piece.start >= 0:
        headers["Range"] = f"bytes={piece.start}-{piece.start + piece.length - 1}"
    last = ""
    for _attempt in range(PART_TRIES):
        if stop():
            return None
        try:
            with session.get(piece.url, timeout=TIMEOUT, stream=True, headers=headers) as answer:
                if answer.status_code >= 400:
                    last = f"сайт ответил {answer.status_code}"
                    if answer.status_code in (401, 403, 404, 410):
                        break
                    continue
                if "text/html" in str(answer.headers.get("Content-Type") or "").lower():
                    raise StreamError("вместо части видео сайт прислал страницу (проверка на роботов или ошибка)")
                body = bytearray()
                for chunk in answer.iter_content(CHUNK):
                    if stop():
                        return None
                    body += chunk
                expected = int(answer.headers.get("Content-Length") or 0)
                if expected and len(body) < expected:
                    last = "часть пришла не целиком"
                    continue
                if not body:
                    last = "часть пришла пустой"
                    continue
                return bytes(body)
        except requests.RequestException as exc:
            from core.redaction import sanitize_text
            last = sanitize_text(str(exc))[:160]
    raise StreamError(f"часть потока не скачалась: {last}")


def download(session, found: Plan, part: Path, referer: str, *, stop, progress) -> str:
    """Append the stream's parts to ``part`` from where it stopped: "done" or "stopped"; raises StreamError."""
    media = found.media
    done = _read_state(part, media)
    if done == 0:
        part.unlink(missing_ok=True)
        if media.init is not None:
            head = _fetch_piece(session, media.init, referer, stop)
            if head is None:
                return "stopped"
            part.write_bytes(head)
        else:
            part.write_bytes(b"")
        _write_state(part, media, 0)
    total = len(media.segments)
    with open(part, "ab") as out:
        for n in range(done, total):
            data = _fetch_piece(session, media.segments[n], referer, stop)
            if data is None:
                return "stopped"
            out.write(data)
            out.flush()
            _write_state(part, media, n + 1)
            progress(n + 1, total, out.tell())
    return "done"


# ── subtitles ───────────────────────────────────────────────────────────────────────
def subtitles(session, found: Plan, referer: str, stop) -> list[tuple[str, str]]:
    """(language, WebVTT text) of each subtitle track of the stream, its parts put together."""
    out = []
    for track in found.subtitles:
        if stop():
            break
        try:
            text = _get_text(session, track.url, referer)
            pieces = parse_media(text, track.url).segments if text.lstrip("\ufeff").startswith("#EXTM3U") else []
            cues: list[str] = []
            for piece in pieces[:MAX_PARTS]:
                data = _fetch_piece(session, piece, referer, stop)
                if data is None:
                    return out
                for cue in _cues(data.decode("utf-8", "replace")):
                    if cue not in cues[-8:]:             # a cue that spans two parts is in both
                        cues.append(cue)
            if not pieces and "-->" in text:             # the track is one WebVTT file, not a playlist of parts
                cues = _cues(text)
            if cues:
                out.append((track.language or track.name or "und", "WEBVTT\n\n" + "\n\n".join(cues) + "\n"))
        except StreamError as exc:
            _LOG.info("subtitles %s not taken: %s", track.name or track.language, exc)
    return out


def _cues(text: str) -> list[str]:
    """The cues of one WebVTT part (its header and X-TIMESTAMP-MAP left out)."""
    blocks = re.split(r"\n\s*\n", text.replace("\r", "").strip())
    return [b.strip() for b in blocks if "-->" in b]
