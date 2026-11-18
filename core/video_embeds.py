"""Other sites' players in the frames of a title's page — what each of them gives openly (the owner, 04.11.2026:
hfshare — «этот сайт тоже добавить … сделать универсальный, который сможет с любого даже не из списка качать»).

A page whose video is only in other sites' players (a frame each: hentaimo, mp4upload…) offers its episodes through
them.  The player's own page (the frame's address, the title's page as the referrer) is read, and its video is taken
where that page names it openly:

* the player's settings — ``"sources": [{"src"|"file": …, "type": …}]`` (JSON or a script's object), ``player.src({src:
  …})``, ``file: "….mp4|.m3u8"``, ``<video src>``, ``<source src>``; an address with ``.m3u8`` or the type
  ``application/x-mpegURL`` is an HLS stream (``core/video_hls``), another one a file;
* the same settings packed by ``eval(function(p,a,c,k,e,d){…})`` — packed, not encrypted: unpacked as the browser does;
* the subtitle files the settings name (``"subtitles"|"tracks"``: .ass, .srt, .vtt) — written next to the episode as
  «Серия 01.ru.ass» (the series' page offers them in its menu: ``core/video_tracks``).

A player whose video is encrypted (its settings decoded by its own script with a key — Hydra of hfshare), or whose page
is made by a script (an application: the video asked by its API — FMplayer of hfshare, FireStream), gives nothing: it is
said in words and the next player is tried.  Players known as closed (Kodik, Alloha… — ``video_download.FRAME_PLAYERS``)
are not opened here at all.

The list of the page:

* the DLE «VideoConstructor» (``RalodePlayer.init({...})``: players, each with its episodes — hfshare): every player is
  tried on its first episode while the page is searched (``fill``); the episodes are taken from the first one that gives
  its video, the others stay in reserve for an episode it does not give;
* else the frames of the page (one video, its mirrors).

The addresses of such players live for hours (signed): each episode's is asked right before it is downloaded
(``Found.resolve`` — :class:`Resolver`).
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

_LOG = logging.getLogger("local_booru.video")
MAX_PAGE = 3 * 1024 * 1024       # bytes of a player's page read at most
MAX_SUB = 8 * 1024 * 1024        # bytes of a subtitle file
TIMEOUT = (10, 30)
MAX_PLAYERS = 12                 # players of a page tried at most
SUB_EXTS = (".ass", ".ssa", ".srt", ".vtt")
# frames that are never a video's player (advertising, counters, comments, social widgets)
NOT_PLAYERS = ("googlesyndication", "doubleclick", "googletagmanager", "google.com/recaptcha", "facebook.com",
               "vk.com/widget", "t.me/", "telegram.org", "disqus", "yandex.ru/", "mc.yandex", "twitter.com",
               "instagram.com", "about:blank", "javascript:")


class EmbedError(Exception):
    """A player that gives no video openly — in words for the journal."""


@dataclass
class Sub:
    lang: str                    # «ru», «en», "" when it does not say
    label: str                   # «Русские»
    url: str
    ext: str                     # «.ass»


@dataclass
class Source:
    """What a player's page gives: its video (a file or an HLS stream) and its subtitle files."""
    url: str
    kind: str                    # "hls" or "file"
    page: str                    # the player's page: the referrer of the video
    title: str = ""
    label: str = ""              # the player's name of the quality («Auto», «720p»)
    subs: list = field(default_factory=list)


@dataclass
class Player:
    name: str                    # the page's name of it («GDP3», «Mp4player») or its site
    episodes: list               # [(label «Серия 1», the frame's address)], in their order


# ── the page's players ─────────────────────────────────────────────────────────────
def _absolute(address: str, base: str) -> str:
    address = html_lib.unescape(str(address or "").strip()).replace("\\/", "/")
    if address.startswith("//"):
        return "https:" + address
    return urljoin(base, address) if address else ""


def _frame_of(code: str, base: str) -> str:
    """The frame's address of a player's code («ifr::https://…» or ``<iframe src=…>``)."""
    code = str(code or "")
    if code.startswith("ifr::"):
        return _absolute(code[5:], base)
    found = re.search(r"""<iframe[^>]+src\s*=\s*["']([^"']+)""", code, re.I)
    return _absolute(found.group(1), base) if found else ""


def _ralode(page_html: str, base: str) -> list[Player]:
    """The players of a DLE «VideoConstructor» (``RalodePlayer.init({...})``)."""
    at = page_html.find("RalodePlayer.init(")
    if at < 0:
        return []
    try:
        data, _end = json.JSONDecoder().raw_decode(page_html[at + len("RalodePlayer.init("):])
    except ValueError:
        return []
    players = []
    for player in data.values() if isinstance(data, dict) else []:
        if not isinstance(player, dict) or not isinstance(player.get("items"), dict):
            continue
        items = [i for i in player["items"].values() if isinstance(i, dict)]
        items.sort(key=lambda i: (int(str(i.get("lssort") or 0)) if str(i.get("lssort") or "").isdigit() else 0))
        episodes = []
        for item in items:
            frame = _frame_of(item.get("scode_begin") or "", base) or _frame_of(item.get("scode") or "", base)
            if frame:
                label = re.sub(r"\s+", " ", str(item.get("sname") or "")).strip().replace("Cерия", "Серия")
                episodes.append((label or f"Серия {len(episodes) + 1}", frame))
        if episodes:
            players.append(Player(name=str(player.get("name") or "").strip() or _host(episodes[0][1]),
                                  episodes=episodes))
    return players


def _host(address: str) -> str:
    return (urlparse(address).hostname or "").removeprefix("www.")


def _closed(address: str) -> bool:
    from core.video_download import FRAME_PLAYERS
    low = address.lower()
    return any(mark in low for mark in FRAME_PLAYERS) or any(mark in low for mark in NOT_PLAYERS)


def players_of(page_html: str, page_url: str) -> list[Player]:
    """The players of a page: the «VideoConstructor»'s, else each frame of an unknown player as one (the page's one
    video: they are its mirrors).  Players known as closed are left out."""
    players = [p for p in _ralode(page_html, page_url) if not _closed(p.episodes[0][1])]
    if players:
        return players[:MAX_PLAYERS]
    frames = []
    for found in re.finditer(r"""<iframe[^>]+?(?:data-)?src\s*=\s*["']([^"']+)""", page_html or "", re.I):
        frame = _absolute(found.group(1), page_url)
        if frame.startswith(("http://", "https://")) and not _closed(frame) and frame not in frames:
            frames.append(frame)
    return [Player(name=_host(f), episodes=[("Видео", f)]) for f in frames[:MAX_PLAYERS]]


# ── what a player's page gives ───────────────────────────────────────────────────────
_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _unbase(word: str, radix: int) -> int:
    if radix <= 36:
        return int(word, radix)
    value = 0
    for char in word:
        value = value * radix + _DIGITS.index(char)
    return value


def unpack(text: str) -> list[str]:
    """The scripts packed by ``eval(function(p,a,c,k,e,d){…}('…',a,c,'…'.split('|')…))`` in ``text``, unpacked."""
    out = []
    for found in re.finditer(r"}\s*\(\s*'((?:[^'\\]|\\.)*)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'((?:[^'\\]|\\.)*)'\.split\('\|'\)",
                             text or "", re.S):
        payload, radix, count, table = found.group(1), int(found.group(2)), int(found.group(3)), found.group(4).split("|")
        if not 2 <= radix <= 62 or count > 100000:
            continue

        def word(match, table=table, radix=radix):
            try:
                n = _unbase(match.group(0), radix)
            except ValueError:
                return match.group(0)
            return table[n] if n < len(table) and table[n] else match.group(0)
        out.append(re.sub(r"\b\w+\b", word, payload.replace("\\'", "'").replace('\\"', '"')))
    return out


def _is_hls(address: str, kind: str = "") -> bool:
    return ".m3u8" in address.lower() or "mpegurl" in kind.lower() or kind.lower() == "hls"


def _objects_after(text: str, key: str) -> list:
    """The JSON list after ``"key":`` in ``text`` (the player's settings written as JSON), each one."""
    out = []
    for found in re.finditer(r'"%s"\s*:\s*\[' % key, text):
        try:
            value, _end = json.JSONDecoder().raw_decode(text[found.end() - 1:])
        except ValueError:
            continue
        out += [v for v in value if isinstance(v, (dict, str))]
    return out


_MEDIA = r"""["']((?:https?:)?//[^"'\s<>]+?\.(?:m3u8|mp4|webm|mkv)(?:\?[^"'\s<>]*)?)["']"""


def media_of(text: str, base: str) -> tuple[list, list, str]:
    """([(address, "hls"|"file", label)], [Sub], title) a player's page names openly — its settings, its packed
    scripts, its ``<video>``."""
    texts = [text] + unpack(text)
    sources, subs, title = [], [], ""

    def add(address, kind="", label=""):
        address = _absolute(address, base)
        if address.startswith(("http://", "https://")) and all(s[0] != address for s in sources):
            sources.append((address, "hls" if _is_hls(address, kind) else "file", str(label or "")))

    def add_sub(address, lang="", label="", kind=""):
        address = _absolute(address, base)
        if not address.startswith(("http://", "https://")) or any(s.url == address for s in subs):
            return
        kind = "." + str(kind or "").lower().strip(". ")
        ext = kind if kind in SUB_EXTS else next((e for e in SUB_EXTS if e in address.lower()), "")
        subs.append(Sub(lang=str(lang or "").strip().lower()[:8], label=str(label or "").strip(), url=address,
                        ext=ext or ".vtt"))
    for body in texts:
        for item in _objects_after(body, "sources"):
            if isinstance(item, str):
                add(item)
            else:
                add(item.get("src") or item.get("file") or item.get("url") or "", item.get("type") or "",
                    item.get("label") or item.get("res") or "")
        for key in ("subtitles", "tracks", "captions"):
            for item in _objects_after(body, key):
                if isinstance(item, dict) and str(item.get("kind") or "captions").lower() in ("captions", "subtitles"):
                    address = item.get("src") or item.get("file") or item.get("url") or ""
                    if address:
                        add_sub(address, item.get("lang") or item.get("srclang") or "", item.get("label") or "",
                                item.get("type") or item.get("format") or "")
        if not title:
            named = re.search(r'"title"\s*:\s*"([^"]{1,200})"', body)
            title = named.group(1) if named else ""
        for found in re.finditer(r"""(?:\.src\(\s*\{|sources\s*:\s*\[\s*\{|\{)\s*[^{}]{0,200}?\b(?:src|file)\s*:\s*["']([^"']+)["']""",
                                 body):
            address = found.group(1)
            if re.search(r"\.(?:m3u8|mp4|webm|mkv)(?:\?|$)", address.split("#")[0], re.I):
                add(address)
        for found in re.finditer(r"""<(?:video|source)[^>]+src\s*=\s*["']([^"']+)["'][^>]*>""", body, re.I):
            kind = re.search(r"""type\s*=\s*["']([^"']+)""", found.group(0))
            add(found.group(1), kind.group(1) if kind else "")
        if not sources:
            for found in re.finditer(_MEDIA, body):
                add(found.group(1))
    return sources, subs, html_lib.unescape(title)


def _why_none(text: str) -> str:
    """Why a player's page gives no video, in words."""
    low = (text or "").lower()
    if re.search(r"atob\(|cryptojs|aes\.decrypt|decrypt\(", low):
        return "его видео зашифровано (адрес расшифровывает его же скрипт) — такие программа не скачивает"
    if re.search(r'<div id="(?:root|app)"></div>', low) or "type=\"module\"" in low:
        return "страницу плеера собирает скрипт, адреса видео в ней нет"
    return "в его странице нет открытой ссылки на видео"


def open_player(session, frame: str, referer: str) -> Source:
    """What the player at ``frame`` gives (its page asked with ``referer``) — :class:`EmbedError` when nothing."""
    import requests
    from core.redaction import sanitize_text
    try:
        with session.get(frame, timeout=TIMEOUT, stream=True, allow_redirects=True,
                         headers={"Referer": referer, "Accept": "text/html,*/*"}) as answer:
            status = answer.status_code
            body = bytearray()
            for chunk in answer.iter_content(64 * 1024):
                body += chunk
                if len(body) > MAX_PAGE:
                    break
            final = answer.url or frame
            text = bytes(body).decode(answer.encoding or "utf-8", "replace") if body else ""
    except requests.RequestException as exc:
        raise EmbedError(f"плеер не ответил: {sanitize_text(str(exc))[:160]}") from exc
    if status >= 400:
        raise EmbedError(f"плеер ответил {status}")
    sources, subs, title = media_of(text, final)
    if not sources:
        raise EmbedError(_why_none(text))
    best = next((s for s in sources if s[1] == "file"), sources[0])          # a whole file before a stream
    return Source(url=best[0], kind=best[1], page=final, title=title, label=best[2], subs=subs)


# ── a title's page: its episodes through the players ───────────────────────────────
def fill(found, *, session=None, say=None, stop=None) -> bool:
    """The episodes of ``found`` (``video_download.look`` of a page with no file of its own) through the players in its
    frames: every player tried on its first episode, the episodes taken from the first that gives its video.  False —
    the page has no such player or none gives anything (``found`` as it was)."""
    from core import video_download
    say = say or (lambda _text: None)
    stop = stop or (lambda: False)
    page = found.page
    players = players_of(getattr(page, "html", "") or "", page.url)
    if not players:
        return False
    from core.video_meta_import import _session
    session = session or _session(page.url)
    working = []
    say(f"Плееры других сайтов на странице: {len(players)} — смотрю, какой отдаёт видео открыто")
    for player in players:
        if stop():
            return False
        label, frame = player.episodes[0]
        try:
            source = open_player(session, frame, page.url)
        except EmbedError as exc:
            say(f"Плеер {player.name} ({_host(frame)}): {exc}")
            continue
        what = "поток HLS" if source.kind == "hls" else "файл"
        langs = ", ".join(s.lang or s.label or "?" for s in source.subs)
        say(f"Плеер {player.name} ({_host(frame)}): {what}" + (f", субтитры: {langs}" if langs else "")
            + f" — серий: {len(player.episodes)}")
        working.append((player, source))
    if not working:
        return False
    main, first = working[0]
    reserve = [p for p, _s in working[1:]]
    labels = [label for label, _frame in main.episodes]
    numbered = all(re.search(r"\d+\s*$", label) for label in labels) and len(set(labels)) == len(labels)
    files, frames = [], {}
    for n, (label, frame) in enumerate(main.episodes):
        number = re.search(r"(\d+)\s*$", label)
        stem = f"Серия {int(number.group(1)):02d}" if numbered and number else \
            video_download.safe_name(first.title.rsplit(".", 1)[0] if len(main.episodes) == 1 and first.title else
                                     found.title or label, 80) or f"Видео {n + 1}"
        ext = ".ts" if first.kind == "hls" else ".mp4"
        remote = video_download.Remote(url=frame, name=stem + ext, title=label, kind="video",
                                       stream="hls" if first.kind == "hls" else "", page=frame,
                                       note=f"Плеер: {main.name}")
        files.append(remote)
        frames[id(remote)] = [(main.name, frame)] + [(p.name, dict(p.episodes)[label]) for p in reserve
                                                     if label in dict(p.episodes)]
    found.files = files
    found.frames = []
    found.resolve = Resolver(found, frames, referer=page.url)
    video_download._look_at_folder(found.folder, found.files, found.had)
    say(f"Серии берутся из плеера {main.name}" + (f"; если серия там не откроется — из {', '.join(p.name for p in reserve)}"
                                                 if reserve else ""))
    return True


class Resolver:
    """``Found.resolve`` of a page's players: each episode's video asked of its player right before it is downloaded
    (signed addresses that live for hours), the next player when it gives nothing; its subtitle files after it."""

    def __init__(self, found, frames: dict, *, referer: str):
        self.found = found
        self.frames = frames                     # id(remote) -> [(player's name, frame)], the one to use first
        self.referer = referer
        self.subs: dict = {}                     # id(remote) -> [Sub] of the source used
        self.session = None

    def __call__(self, remote, *, session, quality="", say, stop, role=""):
        from core import video_download, video_hls
        self.session = session
        failures = []
        for name, frame in self.frames.get(id(remote)) or [(remote.note, remote.url)]:
            if stop():
                raise video_download.DownloadError("остановлено")
            try:
                source = open_player(session, frame, self.referer)
            except EmbedError as exc:
                failures.append(f"{name}: {exc}")
                continue
            if (source.kind == "hls") != (remote.stream == "hls"):      # another player: a stream for a file
                remote.stream = "hls" if source.kind == "hls" else ""
                remote.name = remote.name.rsplit(".", 1)[0] + (".ts" if source.kind == "hls" else ".mp4")
            remote.url, remote.page = source.url, source.page
            remote.note = f"Плеер: {name}"
            self.subs[id(remote)] = source.subs
            if source.kind == "hls":
                try:
                    return video_hls.plan_of(session, [(source.label or "", source.url)], source.page)
                except video_hls.StreamError as exc:
                    failures.append(f"{name}: {exc}")
                    continue
            return None
        raise video_download.DownloadError("ни один плеер не отдал серию — " + "; ".join(failures))

    def finished(self, remote, state: str) -> None:
        """The subtitle files of an episode downloaded — next to it, «Серия 01.ru.ass» (once; a file there is kept)."""
        from core import video_download
        if state not in ("done", "whole") or self.session is None:
            return
        target = video_download.target_of(self.found.folder, remote)
        for sub in self.subs.pop(id(remote), []):
            lang = re.sub(r"[^a-z]", "", sub.lang) or "sub"
            place = target.with_name(f"{target.stem}.{lang}{sub.ext}")
            if place.exists():
                continue
            try:
                answer = self.session.get(sub.url, timeout=TIMEOUT, headers={"Referer": remote.page or self.referer})
                data = answer.content[:MAX_SUB + 1] if answer.status_code < 400 else b""
            except Exception as exc:  # noqa: BLE001 - a subtitle file that does not come: the episode is there
                _LOG.info("subtitles %s not downloaded: %s", sub.url, exc)
                continue
            if data and len(data) <= MAX_SUB and not data.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
                place.write_bytes(data)
