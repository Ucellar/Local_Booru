"""Every voice-over of a title from YummyAnime: the episodes of each studio from the players the site shows (v5.41).

The owner asked for it (30.10.2026): «мне нужны разные озвучки … именно возможность начать любую озвучку … не хочется
приложение привязывать чисто к одной озвучке».  Until v5.40 the program left players in frames alone (Kodik and the
like, «защита сайта»); the owner decided otherwise for these two, so they are read the way their own page scripts read
them.  The rest (Alloha, Aksor — DASH) are named, not downloaded.

What is asked:

* YummyAnime's public API (``yummy_api_url`` — ``https://api.yani.tv``, no key; answers ``{"response": …}`` or
  ``{"error": …}``): ``/anime/<alias>`` — the title (description, poster, genres, numbers on Shikimori and MAL),
  ``/anime/<id>/videos`` — every episode of every voice-over in every player: ``number``, ``iframe_url``,
  ``data.player`` («Плеер Kodik»), ``data.dubbing`` («Озвучка AniLibria», «Субтитры Crunchyroll»).  The list of the
  page is made of this alone: no player is asked before «Скачать»;
* **Kodik** (``kodik*`` hosts, up to 720p): its player page (``var domain/d_sign/pd/pd_sign/ref/ref_sign``,
  ``vInfo.type/hash/id``) and its script (the address of its API — base64 in ``atob("…")``, ``/ftor`` in 2026); POST
  there → ``links`` per quality, each an address in ROT-18 + base64 (or open) → an HLS stream of MPEG-TS parts
  (``core/video_hls``) — a ``.ts`` file.  The address is signed in its path: the resume note of a stream is kept by
  ``Media.resume_key`` (the video and the quality), so a download cut off goes on with a fresh address;
* **CVH** (``/iframeCVH.html?dubbing_code=…&anime_id=…&episode=…`` on YummyAnime, up to 1080p): the publisher and the
  aggregator from the frame's script (``data-publisher-id``, ``data-aggregator``), the title's playlist
  (``<cvh>/playlist?pub&aggr&id`` — ``voiceStudio`` + ``episode`` → ``vkId``), the video (``<cvh>/video/<vkId>``) →
  MP4 files per quality (``mpegFullHdUrl``…), downloaded with Range like any file — a ``.mp4`` file.  Its addresses
  carry the asker's IP: the program that asks downloads.

The addresses of video live for hours, so each episode's are asked right before it is downloaded (``Found.resolve``
→ :func:`Resolver.__call__`), in the quality chosen on the page («Качество»: the best, or not above 1080/720/480/360):
the player that gives the most of it first (CVH before Kodik at equal), the other one when it fails.  An episode whose
``.part`` is there already goes on in the player that began it (its file's kind).

The episodes go to the sub-folder of the voice-over as YummyAnime names it («Озвучка AniLibria», «Субтитры») — tabs of
the series; numbered with a fraction (12.5) — «… — спецвыпуски».  The file is «Серия 01.ts» (Kodik) or «Серия
01.mp4» (CVH): an episode under either name is had.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote_plus, urlparse

from core import video_download, video_meta, video_sources

_LOG = logging.getLogger("local_booru.video")

NAME = "YummyAnime"
DEFAULT_API = "https://api.yani.tv"
SITE = "https://site.yummyani.me"
HOSTS = ("yummyani.me", "yummy-anime.ru", "yani.tv")       # its addresses (any sub-domain: site., old., ru.)
CVH_API = "https://plapi.cdnvideohub.com/api/v1/player/sv"
CVH_PUBLISHER, CVH_AGGREGATOR = "745", "mali"              # what the frame's script said on 30.10.2026: when it cannot
                                                           # be read
TIMEOUT = (15, 40)
MAX_ANSWER = 32 * 1024 * 1024     # bytes of one answer (the videos of a long series: thousands of rows)
MAX_PAGE = 4 * 1024 * 1024        # bytes of a player's page or script
QUALITIES = ("best", "1080", "720", "480", "360")          # «Качество» on the page
PLAYERS = {"cvh": ("CVH", 1080), "aniliberty": ("AniLiberty", 1080),     # the players taken: name, the best
           "kodik": ("Kodik", 720)}                                       # they give
ORDER = ("cvh", "aniliberty", "kodik")       # of equal quality: a whole file before a stream
EXT_OF = {"cvh": ".mp4", "aniliberty": ".ts", "kodik": ".ts"}
ANILIBRIA = "Озвучка AniLibria"            # the voice-over AniLiberty (AniLibria's own site) gives — up to 1080p
# (v5.44.152) why the voice-overs of a player the program takes nothing from are not downloaded — a sentence for the
# pages, by a mark in the player's name (the owner, of «Только в плеерах, из которых программа не скачивает: Озвучка
# DEEP (Alloha)»: «схуяли»).  Alloha was looked at on 11.11.2026: its player signs every request and checks that a
# browser asks — a site's guard against programs, which the program does not get around.
CLOSED_WHY = {
    "alloha": "Alloha отдаёт видео только своему плееру в браузере — это его защита от программ, программа её не обходит",
    "aksor": "Aksor отдаёт видео потоком DASH — такие потоки программа не скачивает",
}
CLOSED_OTHER = "Из этого плеера программа не скачивает"
SOUND_EXT = ".aac"                         # a voice-over's sound alone (CVH, ``core/cvh_sound``) — for the merge only
SPECIALS = video_sources.SPECIALS
_ALIAS = re.compile(r"/catalog/item/([A-Za-z0-9][A-Za-z0-9_-]{0,200})(?:[/?#]|$)")
_CVH_SIZES = (("mpeg4kUrl", 2160), ("mpeg2kUrl", 1440), ("mpegQhdUrl", 1440), ("mpegFullHdUrl", 1080),
              ("mpegHighUrl", 720), ("mpegMediumUrl", 480), ("mpegLowUrl", 360), ("mpegLowestUrl", 240),
              ("mpegTinyUrl", 144))


class DubError(video_download.DownloadError):
    """Said to the person as it is."""


# ── settings, addresses ─────────────────────────────────────────────────────────────
def enabled(settings: dict) -> bool:
    return bool((settings or {}).get("video_source_dubs", True))


def api_of(settings: dict) -> str:
    """``https://host`` of YummyAnime's API from the settings; the usual one when it is not an address."""
    given = str((settings or {}).get("yummy_api_url") or "").strip().rstrip("/")
    site = video_sources.site_address(given) if given else DEFAULT_API
    if site == video_sources.DEFAULT_SITE:
        return DEFAULT_API
    path = urlparse(given if "://" in given else "https://" + given).path
    return site + (path if re.fullmatch(r"(/[A-Za-z0-9._~-]+)*", path) else "")


def _host(address: str) -> str:
    try:
        return (urlparse(str(address or "").strip()).hostname or "").lower()
    except ValueError:
        return ""


def is_yummy(address: str) -> bool:
    host = _host(address)
    return any(host == h or host.endswith("." + h) for h in HOSTS)


def alias_of(address: str) -> str:
    """The title's alias of a YummyAnime address (``…/catalog/item/<alias>``), else ""."""
    try:
        path = urlparse(str(address or "").strip()).path or ""
    except ValueError:
        return ""
    found = _ALIAS.search(path)
    return found.group(1) if found else ""


def alias_in(page) -> str:
    """The alias of a YummyAnime page read or saved: its address, its canonical link, its hidden ``anime_alias``."""
    if is_yummy(page.url) and alias_of(page.url):
        return alias_of(page.url)
    soup = video_meta._soup(page.html)
    link = soup.find("link", rel="canonical")
    if link is not None and alias_of(str(link.get("href") or "")):
        return alias_of(str(link.get("href")))
    field_ = soup.find("input", attrs={"name": "anime_alias"})
    value = str(field_.get("value") or "").strip() if field_ is not None else ""
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,200}", value) else ""


def absolute(address: str) -> str:
    address = str(address or "").strip()
    return "https:" + address if address.startswith("//") else address


_KODIK_PATH = re.compile(r"^/(?:serial|season|seria|video|film)/\d+/[0-9a-f]{16,}/\d{3,4}p(?:$|[/?])")


def closed_why(player: str) -> str:
    """Why nothing is downloaded from ``player`` (its name as the site gives it) — a sentence without its full stop."""
    low = str(player or "").lower()
    return next((words for mark, words in CLOSED_WHY.items() if mark in low), CLOSED_OTHER)


def closed_voices(voices: list) -> list:
    """[{"player", "why", "names"}] — the voice-overs that are only in players the program takes nothing from
    (``voices`` — a find's: {"name", "here", "players"}), by player: the pages say why, and that the site shows them."""
    out: dict = {}
    for voice in voices or []:
        if not isinstance(voice, dict) or voice.get("here"):
            continue
        for player in voice.get("players") or []:
            row = out.setdefault(str(player), {"player": str(player), "why": closed_why(player), "names": []})
            name = str(voice.get("name") or "")
            if name and name not in row["names"]:
                row["names"].append(name)
    return [row for row in out.values() if row["names"]]


def player_of(iframe: str) -> str:
    """"kodik", "cvh", or another player's name (not downloaded).  Kodik — by its hosts or by the shape of its
    player's address (``/season/<id>/<hash>/720p`` — its other domains)."""
    address = absolute(iframe)
    host = _host(address)
    try:
        path = urlparse(address).path or ""
    except ValueError:
        path = ""
    if "kodik" in host or host.endswith("aniqit.com") or _KODIK_PATH.match(path):
        return "kodik"
    if "iframecvh" in address.lower():
        return "cvh"
    for mark in ("alloha", "aksor", "sibnet", "cdnvideohub"):
        if mark in host:
            return mark
    return host or "?"


# ── the way to the sites ─────────────────────────────────────────────────────────────
def direct(session):
    """YummyAnime, Kodik and CVH asked straight, not through the system's proxy (a VPN's «системный прокси»: its far
    exit is slow for them or not let in — the owner's computer, 31.10.2026: every request timed out through it, all
    answered straight; CVH's addresses are also bound to the address that asked for them).  When straight does not
    open, the proxy is taken from then on (``_read``).  The session itself, its way set once."""
    if session is not None and not getattr(session, "lb_route", ""):
        session.trust_env = False
        session.lb_route = "direct"
    return session


def _system_proxy(url: str) -> bool:
    """The system (Windows: «Параметры» → «Прокси», a VPN's) or the environment has a proxy for ``url``."""
    import requests
    try:
        return bool(requests.utils.get_environ_proxies(url))
    except (OSError, ValueError):
        return False


# ── YummyAnime's API ─────────────────────────────────────────────────────────────────
def _read(session, method: str, url: str, *, limit: int, what: str, **options) -> tuple[int, str, str]:
    """(status, text, content type) of one answer, at most ``limit`` bytes; :class:`DubError` when there is none.
    A session that goes straight (``direct``) and does not get through — through the system's proxy, once more."""
    import requests
    from core.redaction import sanitize_text
    for attempt in (1, 2):
        try:
            with session.request(method, url, timeout=TIMEOUT, stream=True, **options) as answer:
                body = bytearray()
                for chunk in answer.iter_content(64 * 1024):
                    body += chunk
                    if len(body) > limit:
                        raise DubError(f"{what} прислал слишком большой ответ")
                return answer.status_code, bytes(body).decode("utf-8", "replace"), \
                    str(answer.headers.get("Content-Type") or "").lower()
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == 1 and getattr(session, "lb_route", "") == "direct" and _system_proxy(url):
                _LOG.info("%s: not reached straight (%s) — through the system's proxy from now on", what,
                          sanitize_text(str(exc))[:200])
                session.trust_env = True
                session.lb_route = "system"
                continue
            raise DubError(f"{what} не ответил: {sanitize_text(str(exc))[:200]}") from exc
        except requests.RequestException as exc:
            raise DubError(f"{what} не ответил: {sanitize_text(str(exc))[:200]}") from exc
    raise DubError(f"{what} не ответил")


def _json(text: str, what: str):
    try:
        return json.loads(text)
    except ValueError:
        raise DubError(f"{what} прислал непонятный ответ (не JSON)") from None


class Api:
    """YummyAnime's public API."""

    def __init__(self, base: str = DEFAULT_API, session=None):
        self.base = base.rstrip("/")
        self.session = session

    def _get(self, path: str):
        from core.video_meta_import import _session, guard_of
        if self.session is None:
            self.session = _session(self.base)
        direct(self.session)
        status, text, ctype = _read(self.session, "GET", self.base + path, limit=MAX_ANSWER, what=NAME,
                                    headers={"Accept": "application/json"})
        if status == 429:
            raise DubError(f"{NAME} просит подождать (слишком много запросов) — попробуйте через минуту")
        if "json" not in ctype and text.lstrip()[:1] not in ("{", "["):
            guard = guard_of(status, text)
            raise DubError(f"{NAME} ответил страницей вместо данных ("
                           + (f"проверка на роботов: {guard}" if guard else f"ответ {status}") + ")")
        data = _json(text, NAME)
        if isinstance(data, dict) and data.get("error"):
            if status == 404 or data.get("error_code") == 404:
                return None
            raise DubError(f"{NAME}: {str(data.get('error_title') or data.get('error'))[:200]}")
        if status == 404:
            return None
        if status >= 400:
            raise DubError(f"{NAME} ответил {status}")
        return data.get("response") if isinstance(data, dict) and "response" in data else data

    def anime(self, alias: str) -> dict | None:
        data = self._get("/anime/" + str(alias))
        return data if isinstance(data, dict) and data.get("anime_id") is not None else None

    def videos(self, anime_id) -> list[dict]:
        data = self._get(f"/anime/{int(anime_id)}/videos")
        return [v for v in data or [] if isinstance(v, dict)] if isinstance(data, list) else []


def ids_of(anime: dict) -> dict:
    remote = anime.get("remote_ids") if isinstance(anime.get("remote_ids"), dict) else {}
    out = {}
    for key, theirs in (("shikimori", "shikimori_id"), ("mal", "myanimelist_id")):
        value = remote.get(theirs)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            out[key] = value
    return out


def ratings_of(anime: dict, address: str = "") -> list:
    """The title's ratings as YummyAnime shows them ([(name, address, value)]): its own people's first (owner,
    02.11.2026: «оценку тоже бы забирало с ЯмиАниме по самому аниме от пользователей» — «8.11 (2989 голосов)»), then
    Shikimori, World Art, Кинопоиск, MyAnimeList — a database's only when the title is there."""
    rating = anime.get("rating") if isinstance(anime.get("rating"), dict) else {}
    remote = anime.get("remote_ids") if isinstance(anime.get("remote_ids"), dict) else {}
    ids = ids_of(anime)

    def score(value) -> str:
        try:
            return f"{float(value):.2f}".rstrip("0").rstrip(".") if float(value) > 0 else ""
        except (TypeError, ValueError):
            return ""

    def number(key) -> int:
        value = remote.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
    out = []
    votes = rating.get("counters")
    own = score(rating.get("average"))
    if own and isinstance(votes, int) and votes > 0:
        out.append((NAME, address, f"{own} ({votes} {_plural(votes, ('голос', 'голоса', 'голосов'))})"))
    elif own:
        out.append((NAME, address, own))
    if ids.get("shikimori"):
        out.append(("Shikimori", f"https://shikimori.one/animes/{ids['shikimori']}", score(rating.get("shikimori_rating"))))
    if number("worldart_id") and score(rating.get("worldart_rating")):
        kind = str(remote.get("worldart_type") or "animation")
        kind = kind if re.fullmatch(r"[a-z]{3,20}", kind) else "animation"
        out.append(("World Art", f"http://www.world-art.ru/{kind}/{kind}.php?id={number('worldart_id')}",
                    score(rating.get("worldart_rating"))))
    if number("kp_id") and score(rating.get("kp_rating")):
        out.append(("Кинопоиск", f"https://www.kinopoisk.ru/film/{number('kp_id')}/", score(rating.get("kp_rating"))))
    if ids.get("mal"):
        out.append(("MyAnimeList", f"https://myanimelist.net/anime/{ids['mal']}", score(rating.get("myanimelist_rating"))))
    return out


def title_page(anime: dict, address: str):
    """The title's page made of the API's answer, in the markup of YummyAnime's own pages (``video_meta`` reads it)."""
    def names(items, key="title"):
        return [str(x.get(key)) for x in items or [] if isinstance(x, dict) and x.get(key)]
    kind = anime.get("type") if isinstance(anime.get("type"), dict) else {}
    age = anime.get("min_age") if isinstance(anime.get("min_age"), dict) else {}
    episodes = anime.get("episodes") if isinstance(anime.get("episodes"), dict) else {}
    status = anime.get("anime_status") if isinstance(anime.get("anime_status"), dict) else {}
    poster = anime.get("poster") if isinstance(anime.get("poster"), dict) else {}
    refs = ratings_of(anime, address)
    count = episodes.get("count")
    rows = [("Статус:", str(status.get("title") or ""), None), ("Тип:", str(kind.get("name") or ""), None),
            ("Год выпуска:", str(anime.get("year") or ""), None), ("Жанры:", "", names(anime.get("genres"))),
            ("Студия:", "", names(anime.get("studios"))), ("Режиссёр:", "", names(anime.get("creators"))),
            ("Эпизоды:", str(count) if isinstance(count, int) and count > 0 else "", None),
            ("Возрастной рейтинг:", str(age.get("title_long") or age.get("title") or ""), None)]
    others = [str(o) for o in anime.get("other_titles") or [] if o and str(o) != str(anime.get("title") or "")]
    picture = absolute(poster.get("fullsize") or poster.get("big") or poster.get("medium") or "")
    return video_sources.title_page(main=str(anime.get("title") or anime.get("anime_url") or ""), others=others,
                                    refs=refs, rows=rows, picture=picture, text=str(anime.get("description") or ""),
                                    url=address, origin=f"{NAME} (API)")


# ── the voice-overs ─────────────────────────────────────────────────────────────────
@dataclass
class Dub:
    name: str                                         # as YummyAnime names it: «Озвучка AniLibria», «Субтитры»
    episodes: dict = field(default_factory=dict)      # number -> {player: frame's address}
    first: int = 0                                    # its place in YummyAnime's list (the order of the page)
    skips: dict = field(default_factory=dict)         # number -> {player: {"opening": [s, e] | None, "ending": …}} —
                                                      # YummyAnime's people's times (``video_skips.hint``)

    @property
    def players(self) -> list[str]:
        found = {p for links in self.episodes.values() for p in links}
        return [p for p in ORDER if p in found]

    @property
    def best(self) -> int:
        return max((PLAYERS[p][1] for p in self.players), default=0)


def number_of(value):
    """An episode's number: 1, 12.5 — None when it is not one («1-2», «трейлер»)."""
    text = str(value if value is not None else "").strip().replace(",", ".")
    if not re.fullmatch(r"\d{1,4}(?:\.\d{1,2})?", text):
        return None
    number = float(text)
    return int(number) if number.is_integer() else number


def dubs_of(videos: list) -> tuple[list[Dub], list[dict]]:
    """(the voice-overs with episodes in the players taken — the fullest first, then as the site lists them;
    the ones only in other players: [{"name", "players"}]).  One studio's work named two ways in two players («Red Head
    Sound» in Kodik, «RedHeadSound» in CVH) — one voice-over with both players (``video_merge.dub_key``); a dubbing
    and a voice-over of one studio stay two."""
    from core.video_merge import dub_key
    taken: dict[str, Dub] = {}
    elsewhere: dict[str, set] = {}
    named: dict[str, str] = {}               # one voice-over under two names (``dub_key``): the name it goes by
    for video in videos:                     # «Озвучка X» before «Дубляж X»: its track is named «X» (``title_of``)
        data = video.get("data") if isinstance(video.get("data"), dict) else {}
        name = re.sub(r"\s+", " ", str(data.get("dubbing") or "")).strip() or "Без названия"
        key = dub_key(name)
        if key not in named or (name.lower().startswith("озвучка") and not named[key].lower().startswith("озвучка")):
            named[key] = name
    for place, video in enumerate(videos):
        data = video.get("data") if isinstance(video.get("data"), dict) else {}
        name = re.sub(r"\s+", " ", str(data.get("dubbing") or "")).strip() or "Без названия"
        name = named.get(dub_key(name), name)
        number = number_of(video.get("number"))
        iframe = absolute(video.get("iframe_url"))
        if number is None or not iframe:
            continue
        player = player_of(iframe)
        if player not in PLAYERS:
            elsewhere.setdefault(name, set()).add(str(data.get("player") or player).replace("Плеер ", ""))
            continue
        dub = taken.setdefault(name, Dub(name=name, first=place))
        dub.episodes.setdefault(number, {}).setdefault(player, iframe)
        given = _skips_of(video.get("skips"))
        if given:
            dub.skips.setdefault(number, {}).setdefault(player, given)
    dubs = sorted(taken.values(), key=lambda d: (-len(d.episodes), d.first))
    rest = [{"name": n, "players": sorted(p)} for n, p in elsewhere.items() if n not in taken]
    return dubs, rest


def _groups(dubs: list[Dub]) -> dict:
    """{voice-over's name: its sub-folder}: two names that make the same folder («Озвучка: X» and «Озвучка X»; «x» and
    «X» on Windows) get folders of their own — « (2)»."""
    out, taken = {}, set()
    for dub in dubs:
        if dub.name in out:                  # the same voice-over: the same folder
            continue
        base = video_download.safe_name(dub.name, video_download.GROUP_MAX - 4) or "Озвучка"
        group, n = base, 1
        while group.casefold() in taken:
            n += 1
            group = f"{base} ({n})"
        taken.add(group.casefold())
        out[dub.name] = group
    return out


def _skips_of(value) -> dict:
    """YummyAnime's «skips» of a video ({"opening": {"time", "length"}, …}) as {"opening": [start, end], …}."""
    out = {}
    for kind in ("opening", "ending"):
        part = value.get(kind) if isinstance(value, dict) else None
        try:
            start, length = float(part["time"]), float(part["length"])
        except (TypeError, KeyError, ValueError):
            continue
        if start >= 0 and 10 <= length <= 300:
            out[kind] = [start, start + length]
    return out


def _group(dub: Dub, number, groups: dict | None = None) -> str:
    group = (groups or {}).get(dub.name) or video_download.safe_name(dub.name, video_download.GROUP_MAX) or "Озвучка"
    return group + SPECIALS if isinstance(number, float) else group


def _label(number) -> str:
    return f"{number:02d}" if isinstance(number, int) else f"{number:g}"


def _name_in(folder: Path, group: str, stem: str, players: list[str]) -> str:
    """The episode's file name: the one in the folder (whole, then begun), else the one its best player makes."""
    place = folder / group
    for ext in (".mp4", ".ts", SOUND_EXT):
        if (place / f"{stem}{ext}").is_file():
            return stem + ext
    for ext in (".mp4", ".ts", SOUND_EXT):
        if (place / f"{stem}{ext}.part").is_file():
            return stem + ext
    return stem + EXT_OF[players[0]]


def files_of(dubs: list[Dub], folder: Path) -> tuple[list, dict]:
    """The episodes of every voice-over as files to download (``video_download.Remote``) and what is needed to ask
    their video later: {id(remote): {"dub", "number", "links": {player: frame}}}."""
    files, entries = [], {}
    groups = _groups(dubs)
    for dub in dubs:
        for number in sorted(dub.episodes, key=float):
            links = dub.episodes[number]
            players = [p for p in ORDER if p in links]
            group = _group(dub, number, groups)
            stem = f"Серия {_label(number)}"
            remote = video_download.Remote(
                url=links[players[0]], name=_name_in(folder, group, stem, players), title=f"Серия {number:g}",
                group=group, quality=f"до {max(PLAYERS[p][1] for p in players)}p",
                note="Плеер: " + ", ".join(PLAYERS[p][0] for p in players), page=SITE + "/")
            files.append(remote)
            entries[id(remote)] = {"dub": dub.name, "number": number, "links": {p: links[p] for p in players},
                                   "skips": dict(dub.skips.get(number) or {})}
    return files, entries


# ── Kodik ──────────────────────────────────────────────────────────────────────────
_KODIK_API: dict = {}                 # player host -> the address of its API (read from its script once)
_KODIK_LOCK = threading.Lock()


def _rot(text: str, shift: int) -> str:
    out = []
    for c in text:
        if "a" <= c <= "z":
            out.append(chr((ord(c) - 97 + shift) % 26 + 97))
        elif "A" <= c <= "Z":
            out.append(chr((ord(c) - 65 + shift) % 26 + 65))
        else:
            out.append(c)
    return "".join(out)


def kodik_decode(src: str) -> str:
    """The address of a quality's stream: open as it is, else ROT-18 + base64 (another shift is tried when the player
    changes it); "" when it is neither."""
    src = str(src or "").strip()
    if src.startswith(("//", "https://", "http://")):
        return absolute(src)
    for shift in [18] + [k for k in range(1, 26) if k != 18]:
        coded = _rot(src, shift)
        coded += "=" * (-len(coded) % 4)
        try:
            address = base64.b64decode(coded, validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if address.startswith(("//", "https://", "http://")) and ".m3u8" in address:
            return absolute(address)
    return ""


def kodik_key(address: str) -> str:
    """What stays of a stream's address between visits: the video and its quality (the signature is in its path)."""
    found = re.search(r"/s/m/([^/]+)/[^/]+/(\d{3,4})\.mp4", str(address or ""))
    return f"kodik/{found.group(1)}/{found.group(2)}" if found else ""


def _var(page: str, name: str) -> str:
    found = re.search(r"var\s+%s\s*=\s*['\"]([^'\"]*)['\"]\s*;" % re.escape(name), page)
    return found.group(1) if found else ""


def _vinfo(page: str, name: str) -> str:
    found = re.search(r"vInfo\.%s\s*=\s*['\"]([^'\"]*)['\"]\s*;" % re.escape(name), page)
    return found.group(1) if found else ""


def _kodik_api_path(session, origin: str, script: str, *, fresh: bool) -> str:
    host = urlparse(origin).netloc
    with _KODIK_LOCK:
        if not fresh and _KODIK_API.get(host):
            return _KODIK_API[host]
    status, text, _ctype = _read(session, "GET", origin + script, limit=MAX_PAGE, what="Kodik (скрипт плеера)")
    found = re.search(r"\$\.ajax\([^)]*?atob\(\s*['\"]([A-Za-z0-9+/=]+)['\"]\s*\)", text) if status < 400 else None
    path = ""
    if found:
        try:
            path = base64.b64decode(found.group(1)).decode("ascii")
        except (ValueError, UnicodeDecodeError):
            path = ""
    if not path.startswith("/"):
        _LOG.info("Kodik: the API's address not in its script (%s) — /ftor", script[:120])
        path = "/ftor"
    with _KODIK_LOCK:
        _KODIK_API[host] = path
    return path


def kodik_links(session, iframe: str) -> list[tuple[str, str]]:
    """[(«720p», stream address)…] of a Kodik player, the best first."""
    iframe = absolute(iframe)
    parsed = urlparse(iframe)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    status, page, _ctype = _read(session, "GET", iframe, limit=MAX_PAGE, what="Kodik",
                                 headers={"Referer": SITE + "/"})
    if "Видео не найдено" in page:
        raise DubError("Kodik: видео не найдено — его убрали из плеера")
    if status in (401, 403):
        raise DubError(f"Kodik не пускает (ответ {status}) — он работает только из России и соседних стран")
    if status >= 400:
        raise DubError(f"Kodik ответил {status}")
    payload = {"d": _var(page, "domain"), "d_sign": _var(page, "d_sign"), "pd": _var(page, "pd"),
               "pd_sign": _var(page, "pd_sign"), "ref": _var(page, "ref"), "ref_sign": _var(page, "ref_sign"),
               "type": _vinfo(page, "type"), "hash": _vinfo(page, "hash"), "id": _vinfo(page, "id"),
               "bad_user": "false", "info": "{}", "cdn_is_working": "true"}
    script = re.search(r"""<script[^>]+src=["'](/assets/js/app\.[^"']+\.js)["']""", page)
    if not (payload["hash"] and payload["id"] and payload["d_sign"] and script):
        raise DubError("Kodik изменил страницу плеера — программе нужно обновление")
    headers = {"Origin": origin, "Referer": iframe, "Accept": "application/json, text/javascript, */*; q=0.01",
               "X-Requested-With": "XMLHttpRequest"}
    data = None
    for fresh in (False, True):                      # the address of its API changes now and then
        path = _kodik_api_path(session, origin, script.group(1), fresh=fresh)
        status, text, ctype = _read(session, "POST", origin + path, limit=MAX_PAGE, what="Kodik", data=payload,
                                    headers=headers)
        if status < 400 and ("json" in ctype or text.lstrip().startswith("{")):
            data = _json(text, "Kodik")
            break
        if status == 500 and "html" in ctype:
            said = re.search(r'<div class="message">(.*?)</div>', text)
            raise DubError("Kodik: ошибка на его стороне" + (f" («{said.group(1)[:120]}»)" if said else "")
                           + " — попробуйте позже")
    if not isinstance(data, dict) or not isinstance(data.get("links"), dict):
        raise DubError("Kodik не отдал ссылки на видео — возможно, он изменил свой API")
    out = []
    for label, items in data["links"].items():
        if not (str(label).isdigit() and isinstance(items, list) and items and isinstance(items[0], dict)):
            continue
        address = kodik_decode(items[0].get("src"))
        if address:
            if label == "720" and "/480.mp4:" in address:          # its «720» named the 480p file once
                address = address.replace("/480.mp4:", "/720.mp4:")
            out.append((f"{label}p", address))
    if not out:
        raise DubError("Kodik прислал ссылки, которые программа не понимает — возможно, он изменил шифр")
    return sorted(out, key=lambda q: -int(q[0][:-1]))


# ── CVH ────────────────────────────────────────────────────────────────────────────
def _cvh_keys(session, iframe: str) -> tuple[str, str]:
    """The publisher and the aggregator from the frame's script (the known ones when it cannot be read)."""
    parsed = urlparse(absolute(iframe))
    origin = f"{parsed.scheme}://{parsed.netloc}"
    try:
        _status, page, _ctype = _read(session, "GET", absolute(iframe), limit=MAX_PAGE, what="CVH")
        found = re.search(r"""<script[^>]*type=["']module["'][^>]*src=["']([^"']+)["']""", page) or \
            re.search(r"""<script[^>]*src=["']([^"']+\.js)["']""", page)
        if found:
            address = found.group(1)
            address = origin + address if address.startswith("/") else absolute(address)
            _status, text, _ctype = _read(session, "GET", address, limit=MAX_PAGE, what="CVH")
            pub = re.search(r'"data-publisher-id"\s*:\s*"?(\d+)', text)
            aggr = re.search(r'"data-aggregator"\s*:\s*"([^"]+)"', text)
            if pub and aggr:
                return pub.group(1), aggr.group(1)
    except DubError as exc:
        _LOG.info("CVH: the frame's script not read (%s)", exc)
    _LOG.info("CVH: the publisher and the aggregator not in the frame's script — the known ones")
    return CVH_PUBLISHER, CVH_AGGREGATOR


def _cvh_get(session, path: str, params: dict | None = None):
    from urllib.parse import urlencode
    status, text, _ctype = _read(session, "GET", CVH_API + path + (f"?{urlencode(params)}" if params else ""),
                                 limit=MAX_ANSWER, what="CVH", headers={"Accept": "application/json, text/plain, */*"})
    if status in (401, 403):
        raise DubError(f"CVH не пускает (ответ {status})")
    if status == 404:
        return None
    if status >= 400:
        raise DubError(f"CVH ответил {status}")
    return _json(text, "CVH")


def cvh_links(session, iframe: str, playlists: dict) -> list[tuple[str, str]]:
    """[(«1080p», MP4 address)…] of an episode in CVH, the best first (``playlists`` — the title's, kept for the
    next episodes)."""
    query = parse_qs(urlparse(absolute(iframe)).query)
    studio = unquote_plus((query.get("dubbing_code") or [""])[0]).strip()
    anime = (query.get("anime_id") or [""])[0]
    episode = number_of((query.get("episode") or [""])[0])
    if not (studio and anime.isdigit() and episode is not None):
        raise DubError("CVH: в ссылке плеера нет озвучки, тайтла или серии")
    if anime not in playlists:
        pub, aggr = _cvh_keys(session, iframe)
        data = _cvh_get(session, "/playlist", {"pub": pub, "aggr": aggr, "id": anime})
        items = data.get("items") if isinstance(data, dict) else None
        playlists[anime] = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    match = [i for i in playlists[anime] if number_of(i.get("episode")) == episode
             and str(i.get("voiceStudio") or "").strip() == studio and i.get("vkId")]
    if not match:
        raise DubError(f"CVH: серии {episode:g} в озвучке {studio} у него нет")
    data = _cvh_get(session, f"/video/{match[0]['vkId']}")
    sources = data.get("sources") if isinstance(data, dict) and isinstance(data.get("sources"), dict) else {}
    out, seen = [], set()
    for key, height in _CVH_SIZES:
        address = str(sources.get(key) or "")
        if address.startswith(("https://", "http://")) and height not in seen:
            seen.add(height)
            out.append((f"{height}p", address))
    if not out:
        raise DubError("CVH не отдал файлы этой серии" + (" (у него есть только DASH)" if sources.get("dashUrl") else ""))
    return out


# ── what a CVH file is: its length and its real size, from its first bytes ──────────────────
HEAD_BYTES = 64 * 1024       # an MP4 «faststart» has its moov — length and the picture's size — at the very start


def mp4_header(data: bytes):
    """(seconds, width, height) from the first bytes of an MP4 whose ``moov`` comes first; None when it is not there
    (``mdat`` first, not an MP4, cut too short)."""
    import struct

    def boxes(start: int, end: int):
        at = start
        while at + 8 <= end:
            size, kind = struct.unpack(">I4s", data[at:at + 8])
            head = 8
            if size == 1:
                if at + 16 > end:
                    return
                size, head = struct.unpack(">Q", data[at + 8:at + 16])[0], 16
            elif size == 0:
                size = end - at
            if size < head:
                return
            yield kind, at + head, min(at + size, end)
            at += size

    seconds, width, height = None, 0, 0
    for kind, body, end in boxes(0, len(data)):
        if kind == b"mdat":
            return None
        if kind != b"moov":
            continue
        for inner, start, stop in boxes(body, end):
            if inner == b"mvhd" and stop - start >= 32:
                if data[start] == 1:
                    scale, length = struct.unpack(">IQ", data[start + 20:start + 32])
                else:
                    scale, length = struct.unpack(">II", data[start + 12:start + 20])
                seconds = length / scale if scale else None
            elif inner == b"trak" and not height:
                for leaf, a, b in boxes(start, stop):
                    if leaf == b"tkhd" and b - a >= 84:              # its last 8 bytes: width, height (16.16)
                        w, h = struct.unpack(">II", data[b - 8:b])
                        if w >> 16 and h >> 16:
                            width, height = w >> 16, h >> 16
        break
    return (float(seconds), width, height) if seconds else None


def cvh_header(session, url: str):
    """(seconds, width, height) of a CVH file by its first ``HEAD_BYTES`` — None when it does not tell."""
    import requests
    try:
        with session.get(url, headers={"Range": f"bytes=0-{HEAD_BYTES - 1}"}, timeout=TIMEOUT, stream=True) as answer:
            if answer.status_code not in (200, 206):
                return None
            data = bytearray()
            for chunk in answer.iter_content(16 * 1024):
                data += chunk
                if len(data) >= HEAD_BYTES:
                    break
    except (requests.RequestException, OSError) as exc:
        _LOG.info("CVH header of a file not read: %s", exc)
        return None
    return mp4_header(bytes(data))


# ── asking the video right before an episode is downloaded ─────────────────────────────
def cap_of(quality: str) -> int:
    """The highest picture «Качество» allows (0 — the best there is)."""
    return int(quality) if str(quality or "") in QUALITIES[1:] else 0


def within(links: list, cap: int) -> list:
    """The qualities not above ``cap``, the best first (the lowest one when all are above)."""
    if not cap:
        return list(links)
    from core.video_hls import height_of
    fit = [q for q in links if height_of(q[0]) <= cap]
    return fit or sorted(links, key=lambda q: height_of(q[0]))[:1]


SOUND_CAP = {"kodik": 360, "cvh": 480, "aniliberty": 480}   # a voice-over wanted for its sound only (its picture
                                         # is another's): the lowest picture that keeps the whole sound — Kodik's
                                         # sound is the same in every quality (~93 kbit/s), CVH's is cut in 360p (64)
                                         # but not in 480p (128), AniLiberty's lowest is 480p (~195 in each)


def order(players: list[str], cap: int, begun: str = "", *, sound: bool = False) -> list[str]:
    """The players to ask, in order: the one that began the file; then the one that gives the most within the
    quality chosen (CVH before Kodik at equal) — for the sound only: CVH (its sound alone, no picture —
    ``core/cvh_sound``: ~26 MB an episode), then Kodik (its sound is whole in 360p: ~80 MB)."""
    def rank(player: str):
        if sound:
            return (player != begun, player != "cvh", player != "kodik", ORDER.index(player))
        best = PLAYERS[player][1]
        return (player != begun, -(min(best, cap) if cap else best), ORDER.index(player))
    return sorted(players, key=rank)


def _route(session, url: str, what: str) -> None:
    """The way to a stream's host settled before the stream is read (``video_hls`` asks with the session as it is):
    its playlist asked once by ``_read`` — straight, through the system's proxy when straight does not open."""
    status, _text, _ctype = _read(session, "GET", url, limit=MAX_PAGE, what=what)
    if status >= 400:
        raise DubError(f"{what} ответил {status}")


def _gives_bytes(session, url: str) -> bool:
    """A stream's part sends its first bytes within the wait (its headers alone tell nothing: on the owner's computer
    AniLiberty's parts answer a HEAD straight and never send their body — 01.11.2026)."""
    import requests
    try:
        with session.get(url, headers={"Range": "bytes=0-65535"}, timeout=(10, 15), stream=True) as answer:
            if answer.status_code >= 400:
                return False
            return bool(next(answer.iter_content(16 * 1024), b""))
    except (requests.RequestException, OSError):
        return False


def _adapter_by_host():
    import requests

    class ByHost(requests.adapters.HTTPAdapter):
        """The session's adapter: the hosts of ``proxied`` domains through their proxy, the others as they were (the
        session goes straight — ``direct``: Kodik and CVH time out through the owner's VPN, AniLiberty's parts do not
        come without it)."""

        def __init__(self):
            super().__init__()
            self.proxied: dict = {}              # a domain -> its proxies

        def send(self, request, **kwargs):
            host = (urlparse(request.url).hostname or "").lower()
            for domain, proxies in self.proxied.items():
                if host == domain or host.endswith("." + domain):
                    kwargs["proxies"] = proxies
                    break
            return super().send(request, **kwargs)
    return ByHost


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host


def stream_way(session, part: str, what: str, say=None) -> str:
    """The way to a stream's parts, once a session for its site: straight when its first part sends bytes straight;
    else, when the system has a proxy (a VPN), the parts of its domain through it — and only they ("system").
    "direct" otherwise (the download says what happens)."""
    import requests
    if getattr(session, "lb_route", "") == "system":
        return "system"                          # the whole session goes through the proxy already
    domain = _domain(part)
    ways = getattr(session, "lb_ways", None)
    if ways is None:
        ways = session.lb_ways = {}
    if domain in ways:
        return ways[domain]
    ways[domain] = "direct"
    if _gives_bytes(session, part):
        return "direct"
    try:
        proxies = requests.utils.get_environ_proxies(part)
    except (OSError, ValueError):
        proxies = {}
    if not proxies:
        return "direct"
    adapter = session.adapters.get("https://")
    if not hasattr(adapter, "proxied"):
        adapter = _adapter_by_host()()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    adapter.proxied[domain] = proxies
    ways[domain] = "system"
    _LOG.info("%s: its parts (%s) do not come straight — through the system's proxy", what, domain)
    if say is not None:
        say(f"{what} напрямую не отдаёт видео — его части идут через прокси системы (VPN), остальное — напрямую")
    return "system"


class SoundPlan:
    """``Found.resolve``'s plan of a voice-over's sound alone (``core/cvh_sound``): ``fetch`` downloads it — when CVH
    does not give it so (another kind of file, its hosts refuse), the whole 480p file is downloaded instead."""

    def __init__(self, sound_url: str, keys_url: str, folder: Path):
        self.sound_url, self.keys_url, self.folder = sound_url, keys_url, folder

    def fetch(self, session, remote, target: Path, *, say, stop, label: str, referer: str = "") -> str:
        from core import cvh_sound, video_download
        import requests

        def new_session():
            fresh = requests.Session()
            fresh.headers.update({"User-Agent": session.headers.get("User-Agent", "Mozilla/5.0")})
            fresh.trust_env = bool(getattr(session, "trust_env", False))     # the way the session goes (``direct``)
            return fresh

        def meter(done, total, speed):
            video_download._set_meter(name=remote.name, done=done, total=total, speed=int(speed))
        try:
            return cvh_sound.download(new_session, self.sound_url, self.keys_url, target, folder=self.folder,
                                      say=say, stop=stop, label=label, meter=meter)
        except cvh_sound.SoundError as exc:
            if stop():
                return "stopped"
            say(f"{label}: звук отдельно не взять ({exc}) — качаю файл целиком")
            _LOG.info("%s: the sound alone not had: %s", remote.name, exc)
        for leftover in (target.with_name(target.name + ".part"), target.with_name(target.name + cvh_sound.NOTE)):
            leftover.unlink(missing_ok=True)
        remote.name = remote.name.rsplit(".", 1)[0] + EXT_OF["cvh"]
        remote.url, remote.quality = self.sound_url, "480p"
        return video_download.download_file(session, remote, self.folder, referer or SITE + "/", say=say, stop=stop)


def _plain(address: str) -> str:
    """A stream's address without its query (AniLiberty's ``?countryIso=…`` changes, the stream does not)."""
    parts = urlparse(address)
    return f"{parts.netloc}{parts.path}"


def _begun(target: Path) -> str:
    """The player a begun ``.part`` of the episode is from: CVH's file is «.mp4» (its sound alone «.aac»); a stream
    («.ts») — Kodik's, or AniLiberty's when its resume note says so (one goes on with its own part; another's stream
    begins anew)."""
    part = target.with_name(target.name + ".part")
    if not part.is_file():
        return ""
    if target.name.endswith((EXT_OF["cvh"], SOUND_EXT)):
        return "cvh"
    try:
        note = json.loads(part.with_name(part.name + ".hls").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "kodik"
    return "aniliberty" if str((note or {}).get("playlist") or "").startswith("aniliberty/") else "kodik"


class Resolver:
    """``Found.resolve``: the episode's video asked of its players right before it is downloaded."""

    SLACK = 1.5                              # seconds two releases' lengths may differ by and be the same cut

    def __init__(self, entries: dict, folder: Path, files: list | None = None):
        self.entries = entries
        self.folder = folder
        self.files = list(files or [])
        self.playlists: dict = {}            # CVH: the title's playlist, asked once for all its episodes
        self.heights: dict = {}              # CVH, a voice-over's group: {its label: the real height} (2K and 4K
                                             # come under each other's names)
        self.survey: dict | None = None      # a voice-over's group: {"seconds", "clean"} — :meth:`look_over`
        self.reference: float | None = None  # the length of the original's cut of the episode looked at
        self.pictures: dict = {}             # an episode (its file's stem): the voice-over it takes its picture from
        self.releases: dict = {}             # AniLiberty: a release's address -> the release (asked once a search)
        self.used: dict = {}                 # id(remote) -> the player its file comes from
        self.donors: list = []               # (v5.44.150) files a download takes for their picture alone: releases
                                             # whose voice-over was not asked for (:meth:`roles`)

    @staticmethod
    def _subs(remote) -> bool:
        return str(remote.group or "").lower().startswith("субтитры")

    def real(self, group: str, links: list, session=None) -> list:
        """CVH's qualities of a voice-over named by their real heights (the best first): the ones above 1080p are
        read from the files' first bytes once for its group — CVH gives 2K as «2160p» and 4K as «1440p»."""
        from core.video_hls import height_of
        known = self.heights.setdefault(group, {})
        for label, url in links:
            if height_of(label) <= 1080:
                known.setdefault(label, height_of(label))
            elif label not in known and session is not None:
                head = cvh_header(session, url)
                known[label] = head[2] if head and head[2] else height_of(label)
        named = [(f"{known.get(label) or height_of(label)}p", url) for label, url in links]
        named.sort(key=lambda q: height_of(q[0]), reverse=True)
        return named

    def look_over(self, session, say=None, stop=None) -> dict:
        """Which voice-overs give the original's picture: each one's episode that most of them have — its length and
        its real sizes, read from the first bytes of its CVH files.  The length most of them share (a subtitles'
        release — the original with words on it — decides between equal ones) is the original's cut; a voice-over
        whose episode is longer or shorter than it is re-cut by its studio (its intro, its plates, its cuts) — it
        gives its sound only.  Once for the title.  (v5.44.150) A release in CVH without that episode (and the only
        one in CVH) — its real sizes all the same, by its own first episode: the page names each release's real
        quality (:meth:`release_facts`), and CVH's «до 1080p» was the player's best, not the release's."""
        from collections import Counter
        if self.survey is not None:
            return self.survey
        direct(session)
        say = say or (lambda _text: None)
        self.survey = {}
        cvh = [r for r in self.files if "cvh" in ((self.entries.get(id(r)) or {}).get("links") or {})]
        if len({r.group for r in cvh}) >= 2:
            stems = Counter(r.name.rsplit(".", 1)[0] for r in cvh)
            anchor = max(stems, key=lambda s: (stems[s], -len(s), s))
            group_of = {r.group: r for r in cvh if r.name.rsplit(".", 1)[0] == anchor}
            say(f"Смотрю, у каких озвучек картинка без перемонтажа и в каком качестве она на самом деле — по "
                f"«{anchor}» ({len(group_of)} {_plural(len(group_of), ('релиз', 'релиза', 'релизов'))} в CVH)…")
            for group, remote in group_of.items():
                if stop is not None and stop():
                    break
                try:
                    links = cvh_links(session, self.entries[id(remote)]["links"]["cvh"], self.playlists)
                except DubError as exc:
                    _LOG.info("look over %s: %s", group, exc)
                    continue
                self.real(group, links, session)                      # its real sizes (above 1080p — read)
                head = cvh_header(session, links[-1][1])             # its length: every quality of it has the same
                if head is not None:
                    self.survey[group] = {"seconds": head[0], "subs": self._subs(remote)}
        for remote in sorted(cvh, key=lambda r: r.name):              # the rest: their real sizes alone
            if remote.group in self.heights or (stop is not None and stop()):
                continue
            try:
                self.real(remote.group, cvh_links(session, self.entries[id(remote)]["links"]["cvh"], self.playlists),
                          session)
            except DubError as exc:
                _LOG.info("look over %s: %s", remote.group, exc)
                self.heights.setdefault(remote.group, {})
        lengths = {g: v["seconds"] for g, v in self.survey.items()}
        best = None
        for group, seconds in lengths.items():
            near = sorted(s for s in lengths.values() if abs(s - seconds) <= self.SLACK)
            key = (len(near), any(self.survey[g]["subs"] for g, s in lengths.items() if abs(s - seconds) <= self.SLACK))
            if best is None or key > best[0]:
                best = (key, near[len(near) // 2])
        if best is not None and (best[0][0] >= 2 or best[0][1]):
            self.reference = best[1]
        for group, item in self.survey.items():
            item["clean"] = self.reference is not None and abs(item["seconds"] - self.reference) <= self.SLACK
        if self.reference is not None:
            clock = video_download._clock
            clean = [g for g, v in self.survey.items() if v["clean"] and not v["subs"]]
            recut = [f"{g} ({clock(v['seconds'])})" for g, v in self.survey.items() if not v["clean"] and not v["subs"]]
            say(f"Картинка как у оригинала ({clock(self.reference)}): " + (", ".join(clean) or "ни у одной озвучки")
                + (f". Перемонтированы студией — у них берётся только звук: {', '.join(recut)}" if recut else ""))
        return self.survey

    def measure(self, session) -> dict:
        """What an episode of the title weighs, for the page's estimate before «Скачать»: one episode of the voice-over
        in CVH with the most episodes — its length and each quality's size by the real height ({"seconds", "sizes":
        {height: bytes}}), read from the files' first bytes (``Content-Range``).  {} when it does not tell.  Once."""
        import requests
        from core.video_hls import height_of
        if getattr(self, "measured", None) is not None:
            return self.measured
        self.measured = {}
        direct(session)
        cvh = [r for r in self.files if "cvh" in ((self.entries.get(id(r)) or {}).get("links") or {})
               and not self._subs(r)]
        if not cvh:
            self.measured = self._measure_streams(session)
            return self.measured
        groups = {}
        for remote in cvh:
            groups.setdefault(remote.group, []).append(remote)
        # (v5.44.150) of the release whose picture is the best known (the original's cut, the largest: what an
        # episode's picture is taken from) — it was the one with the most episodes: its 720p said nothing of the
        # weight of another's 1080p
        def worth(group: str) -> tuple:
            item = (self.survey or {}).get(group)
            clean = 1 if item is None else 2 if item.get("clean") else 0
            return (clean, max((self.heights.get(group) or {}).values(), default=0), len(groups[group]))
        remote = sorted(groups[max(groups, key=worth)], key=lambda r: r.name)[0]
        try:
            links = cvh_links(session, self.entries[id(remote)]["links"]["cvh"], self.playlists)
        except DubError as exc:
            _LOG.info("measure: %s", exc)
            return self.measured
        sizes, seconds = {}, 0.0
        for label, url in links:
            try:
                with session.get(url, headers={"Range": f"bytes=0-{HEAD_BYTES - 1}"}, timeout=TIMEOUT,
                                 stream=True) as answer:
                    total = re.search(r"/(\d+)\s*$", str(answer.headers.get("Content-Range") or ""))
                    data = bytearray()
                    for chunk in answer.iter_content(16 * 1024):
                        data += chunk
                        if len(data) >= HEAD_BYTES:
                            break
            except (requests.RequestException, OSError) as exc:
                _LOG.info("measure %s: %s", label, exc)
                continue
            head = mp4_header(bytes(data))
            height = head[2] if head and head[2] else height_of(label)
            seconds = seconds or (head[0] if head else 0.0)
            if total:
                sizes[height] = int(total.group(1))
        if sizes and seconds:
            self.measured = {"seconds": round(seconds, 1), "sizes": {str(h): b for h, b in sorted(sizes.items())}}
        return self.measured

    def aniliberty_links(self, address: str) -> list[tuple[str, str]]:
        """[(«1080p», its stream)…] of an AniLiberty episode, the best first — its release asked once for the search
        (``address``: the release page with the episode's id after «#»)."""
        page, _sep, key = str(address or "").partition("#")
        parts = urlparse(page)
        site = f"{parts.scheme}://{parts.netloc}"
        alias = video_sources.release_alias(page, site)
        if not alias:
            raise DubError("AniLiberty: в адресе нет релиза")
        if page not in self.releases:
            try:
                self.releases[page] = video_sources.AniLiberty(site).release(alias)
            except video_sources.SourceError as exc:
                raise DubError(f"AniLiberty: {exc}") from exc
        release = self.releases[page]
        if not isinstance(release, dict):
            raise DubError("AniLiberty: релиза больше нет")
        key = unquote_plus(key)
        episodes = [e for e in release.get("episodes") or [] if isinstance(e, dict)]
        episode = next((e for e in episodes if str(e.get("id") or "") == key), None)
        if episode is None and key.startswith("n"):
            episode = next((e for e in episodes if number_of(e.get("ordinal")) == number_of(key[1:])), None)
        if episode is None:
            raise DubError("AniLiberty: этой серии в релизе больше нет")
        links = [(f"{q}p", video_sources._absolute(episode.get(f"hls_{q}"), site)) for q in video_sources.QUALITIES]
        links = [(label, url) for label, url in links if url]
        if not links:
            raise DubError("AniLiberty не даёт видео этой серии")
        return links

    def _measure_streams(self, session) -> dict:
        """:meth:`measure` of a title with no CVH: an AniLiberty episode — each quality's bytes a second from three
        of its parts (their sizes by ``HEAD``) × its length."""
        import requests
        from core import video_hls
        libria = [r for r in self.files if "aniliberty" in ((self.entries.get(id(r)) or {}).get("links") or {})]
        if not libria:
            return {}
        remote = sorted(libria, key=lambda r: r.name)[0]
        try:
            links = self.aniliberty_links(self.entries[id(remote)]["links"]["aniliberty"])
        except DubError as exc:
            _LOG.info("measure: %s", exc)
            return {}
        try:
            _route(session, links[0][1], "AniLiberty")
        except DubError as exc:
            _LOG.info("measure: %s", exc)
            return {}
        sizes, seconds = {}, 0.0
        for label, url in links:
            try:
                plan = video_hls.plan_of(session, [(label, url)], url)
            except video_hls.StreamError as exc:
                _LOG.info("measure %s: %s", label, exc)
                continue
            parts = plan.media.segments
            if parts and not seconds:            # the parts' way (the owner's: through the VPN) — before their sizes
                stream_way(session, parts[0].url, "AniLiberty")      # (straight, 40 s a part to time out: the page
            seconds = seconds or plan.media.duration                 # waited 87 s for its estimate — 02.11.2026)
            picked = [parts[len(parts) * k // 4] for k in (1, 2, 3)] if len(parts) >= 4 else list(parts)
            weight = took = 0.0
            for part in picked:
                try:
                    answer = session.head(part.url, timeout=(5, 10), allow_redirects=True)
                    length = int(answer.headers.get("Content-Length") or 0)
                except (requests.RequestException, OSError, ValueError) as exc:
                    _LOG.info("measure %s: %s", label, exc)
                    continue
                if length > 0 and part.duration > 0:
                    weight, took = weight + length, took + part.duration
            if weight and took and plan.media.duration:
                sizes[video_hls.height_of(label)] = int(weight / took * plan.media.duration)
        if sizes and seconds:
            return {"seconds": round(seconds, 1), "sizes": {str(h): b for h, b in sorted(sizes.items())}}
        return {}

    def release_facts(self) -> dict:
        """(v5.44.150) What is known of each release's picture, for the page (after :meth:`look_over`): {its
        sub-folder: {"height": its real best (CVH's real sizes; a player not looked at — the player's best),
        "known": the height was read from its files, "clean": True — the original's cut, False — re-cut by its
        studio, None — not known, "seconds": the length of its episode looked at}}.  Subtitles' releases too."""
        out = {}
        for remote in self.files:
            entry = self.entries.get(id(remote))
            if entry is None or remote.group in out:
                continue
            item = (self.survey or {}).get(remote.group)
            real = self.heights.get(remote.group) or {}
            heights = [PLAYERS[p][1] for p in entry["links"] if p != "cvh" or not real] + list(real.values())
            out[remote.group] = {"height": max(heights, default=0), "known": bool(real),
                                 "clean": None if item is None else bool(item.get("clean")),
                                 "seconds": round(float(item["seconds"]), 1) if item else 0.0}
        return out

    def picture_for(self, groups: list, quality: str = "") -> dict:
        """(v5.44.150) Which release gives the picture when these voice-overs (their sub-folders) go into one file,
        within «Качество» — as :meth:`roles` decides it for an episode all of them have: {"group", "title",
        "height", "clean" (True / False / None), "lent" — a release not among them (its picture alone is
        downloaded)}; {} — none of them is a voice-over of the title."""
        from core.video_merge import title_of
        cap = cap_of(quality)
        first: dict = {}
        for remote in self.files:
            entry = self.entries.get(id(remote))
            if entry is not None and not self._subs(remote):
                first.setdefault(remote.group, (remote, entry))
        asked = [first[g] for g in dict.fromkeys(groups) if g in first]
        if not asked:
            return {}
        top = max(asked, key=lambda item: self._rank(item[0], item[1], cap))
        lender = self._lender([item for g, item in first.items() if g not in set(groups)], top, cap)
        pick = lender or top
        rank = self._rank(pick[0], pick[1], cap)
        return {"group": pick[0].group, "title": title_of(pick[0].group), "height": rank[1],
                "clean": {2: True, 0: False}.get(rank[0]), "lent": lender is not None}

    def lower_in_folder(self, height: int) -> dict:
        """(v5.44.150) The episodes of the folder whose picture is lower than ``height``: {"episodes": how many,
        "height": the largest picture among them} — the ones put together («Серия NN.mkv») and the ones whose
        voice-overs wait in their sub-folders (their largest file with a picture).  A file's size is read once
        while the file is the same (the page asks at every tick: a season is 170 files)."""
        from core import video_merge
        if not height or not self.folder.is_dir() or not video_merge.available():
            return {"episodes": 0, "height": 0}
        kept = self.__dict__.setdefault("_file_heights", {})

        def height_of_file(path: Path) -> int:
            try:
                stat = path.stat()
            except OSError:
                return 0
            key = (stat.st_size, stat.st_mtime_ns)
            if kept.get(str(path), (None, 0))[0] != key:
                kept[str(path)] = (key, video_merge._height(path))
            return kept[str(path)][1]

        together: dict = {}                      # an episode put together: its file
        waiting: dict = {}                       # one that is not yet: its voice-overs' files with a picture
        try:
            for path in self.folder.iterdir():
                if path.is_file() and path.suffix.lower() == ".mkv":
                    together[path.stem] = [path]
            for sub in self.folder.iterdir():
                if not sub.is_dir() or sub.name.startswith(".") or sub.name.lower().startswith("субтитры"):
                    continue
                for path in sub.iterdir():
                    if path.suffix.lower() in (".mp4", ".ts") and path.stem not in together:
                        waiting.setdefault(path.stem, []).append(path)
        except OSError as exc:
            _LOG.info("the folder's pictures not looked at: %s", exc)
            return {"episodes": 0, "height": 0}
        lower = []
        for files in (*together.values(), *waiting.values()):
            best = max((height_of_file(path) for path in files), default=0)
            if 0 < best < height:
                lower.append(best)
        return {"episodes": len(lower), "height": max(lower, default=0)}

    def _rank(self, remote, entry, cap: int) -> tuple:
        """How good a voice-over's picture is for an episode: the original's cut (2), not known (1), re-cut (0); its
        real height within ``cap``; its length nearest the original's; its best player by ``ORDER`` (CVH — a whole
        file, then AniLiberty, then Kodik)."""
        item = (self.survey or {}).get(remote.group)
        clean = 1 if item is None else 2 if item.get("clean") else 0
        heights = []
        for player in entry["links"]:
            if player == "cvh" and self.heights.get(remote.group):
                heights += list(self.heights[remote.group].values())
            else:
                heights.append(PLAYERS[player][1])
        fit = [h for h in heights if not cap or h <= cap] or [min(heights)]
        near = -abs(item["seconds"] - self.reference) if item and self.reference is not None else 0.0
        return (clean, max(fit), near, -min(ORDER.index(p) for p in entry["links"]))

    def roles(self, remotes: list, quality: str = "", *, session=None, say=None, stop=None,
              repicture=None) -> dict:
        """When the voice-overs of each episode go into one file (``download(merge=True)``): {id(remote): role} —
        "picture" for the one the episode's picture is taken from (the original's cut in the best quality —
        :meth:`look_over`), "sound" for the others (their sound only).  An episode whose file has a picture as good
        already (as large, the original's length) — all "sound".  Subtitles' releases (they stay files of their own)
        — not here.  The episodes are remembered for :meth:`instead`, the pictures in :attr:`pictures` (``Merger``).

        (v5.44.150; the owner: «удалило весь видеоряд а оставило jam где всего 720p», «Чистая картинка в 1080p у
        тайтла есть — у Crunchyroll. добавляй тогда») The picture is the title's best, not the best of what was
        ticked: a release of the title that was not asked for lends it when its cut is the original's and it is
        better than every one asked for (:meth:`_lender`).  Its file of the episode joins the download
        (:attr:`donors`, role "picture") for the picture alone — noted in ``.lb/picture.json``
        (``video_merge.put_picture``), so its sound is not put into the episode's file.  ``repicture`` — the
        episodes of the folder (their stems) whose picture may be taken anew this way though nothing of them is
        downloaded."""
        from core import video_merge
        cap = cap_of(quality)
        if session is not None:
            self.look_over(session, say, stop)
        out, episodes, others = {}, {}, {}
        asked = {id(remote) for remote in remotes}
        noted = video_merge.read_pictures(self.folder)
        refused = {stem: item["group"] for stem, item in noted.items() if item["state"] == "refused"}
        wanted = {stem: item["group"] for stem, item in noted.items() if item["state"] == "wanted"}   # begun before
        for remote in remotes:
            entry = self.entries.get(id(remote))
            if entry is not None and not self._subs(remote):
                episodes.setdefault(remote.name.rsplit(".", 1)[0], []).append((remote, entry))
        # the title's other releases of an episode — what may lend its picture; not one whose file of the episode
        # lies in the folder already (downloaded before as a voice-over of its own: it goes into the episode's file
        # with its sound, as ever)
        there: dict = {}
        for remote in self.files:
            entry = self.entries.get(id(remote))
            if entry is None or id(remote) in asked or self._subs(remote):
                continue
            place = self.folder / remote.group if remote.group else self.folder
            if place not in there:
                try:
                    there[place] = {p.name.rsplit(".", 1)[0] for p in place.iterdir()
                                    if p.suffix.lower() in (".mp4", ".ts", SOUND_EXT)} if place.is_dir() else set()
                except OSError:
                    there[place] = set()
            stem = remote.name.rsplit(".", 1)[0]
            if stem not in there[place] or remote.group == wanted.get(stem):
                others.setdefault(stem, []).append((remote, entry))
        self._episodes, self._cap = episodes, cap
        self.donors = []
        merger = video_merge.Merger(self.files, self.folder)
        for stem in [*episodes, *(s for s in sorted(repicture or ()) if s not in episodes and s in others)]:
            items = episodes.get(stem, [])
            top = max(items, key=lambda item: self._rank(item[0], item[1], cap)) if items else None
            lender = self._lender([o for o in others.get(stem, []) if o[0].group != refused.get(stem)], top, cap)
            pick = lender or top
            if pick is None or not self._better(stem, pick, cap, session):
                for remote, _entry in items:
                    out[id(remote)] = "sound"
                continue
            self.pictures[stem] = pick[0]
            for remote, _entry in items:
                out[id(remote)] = "picture" if remote is pick[0] else "sound"
            if lender is not None:
                out[id(lender[0])] = "picture"
                self.donors.append(lender[0])
                if not merger.had(lender[0]):        # its voice-over is not in the episode's file: the picture alone
                    video_merge.put_picture(self.folder, stem, lender[0].group, "wanted")
        return out

    def _lender(self, others: list, top, cap: int):
        """The release not asked for that lends an episode its picture — (remote, entry), None when none is worth
        it: only one whose cut is known to be the original's (:meth:`look_over`: its length is the length most of
        the releases share), and only when it is better than the best one asked for (``top``): of the original's cut
        where that one is re-cut or not known, or larger within «Качество»."""
        clean = [item for item in others if self._rank(item[0], item[1], cap)[0] == 2]
        if not clean:
            return None
        best = max(clean, key=lambda item: self._rank(item[0], item[1], cap))
        if top is not None and self._rank(best[0], best[1], cap)[:2] <= self._rank(top[0], top[1], cap)[:2]:
            return None
        return best

    def lent_words(self) -> list:
        """What the journal says of the pictures lent in this download: a line a release."""
        from core.video_merge import title_of
        counts: dict = {}
        for remote in self.donors:
            counts[remote.group] = counts.get(remote.group, 0) + 1
        out = []
        for group, count in counts.items():
            height = max((self.heights.get(group) or {}).values(), default=0)
            within = min(height, getattr(self, "_cap", 0)) if height and getattr(self, "_cap", 0) else height
            out.append(f"Картинка {count} {_plural(count, ('серии', 'серий', 'серий'))} — от «{title_of(group)}»"
                       + (f" ({within}p, без перемонтажа)" if within else " (без перемонтажа)")
                       + ": её озвучку вы не отмечали — скачается ради картинки, её звук в файл серии не пойдёт")
        return out

    def _better(self, stem: str, top, cap: int, session) -> bool:
        """``top`` would give the episode a better picture than the one its MKV (or a voice-over's file there) has:
        larger, or of the original's cut where the one there is re-cut (its length is not the original's — the first
        bytes of ``top``'s CVH file tell this episode's)."""
        from core.video_merge import picture_info
        have = picture_info(self.folder, stem)
        if have is None:
            return True
        rank = self._rank(top[0], top[1], cap)
        if rank[0] == 0:                     # a studio's re-cut never takes the place of a picture there
            return False
        if have[0] < rank[1]:
            return True
        if rank[0] < 2 or session is None or "cvh" not in top[1]["links"]:
            return False
        try:
            links = cvh_links(session, top[1]["links"]["cvh"], self.playlists)
        except DubError:
            return False
        head = cvh_header(session, links[-1][1])
        return head is not None and abs(head[0] - have[1]) > self.SLACK

    def _best(self, entry) -> int:
        cap = getattr(self, "_cap", 0)
        return max(min(PLAYERS[p][1], cap) if cap else PLAYERS[p][1] for p in entry["links"])

    def instead(self, remote, roles: dict):
        """The episode's picture did not come (``remote`` failed or is empty on the site): the next best voice-over
        of the episode not downloaded yet gives it instead — its role "picture" now (``roles`` changed); None when
        there is none."""
        stem = remote.name.rsplit(".", 1)[0]
        left = [(r, e) for r, e in getattr(self, "_episodes", {}).get(stem, [])
                if r is not remote and roles.get(id(r)) == "sound" and not video_download.target_of(self.folder, r).is_file()]
        if not left:
            return None
        cap = getattr(self, "_cap", 0)
        chosen = max(left, key=lambda item: self._rank(item[0], item[1], cap))[0]
        roles[id(chosen)] = "picture"
        self.pictures[stem] = chosen
        return chosen

    def __call__(self, remote, *, session, quality: str = "", say=None, stop=None, role: str = ""):
        """Sets the episode's address, kind, qualities and file name; the HLS plan for a stream (None for a file).
        ``role`` "sound" — wanted for its sound only: the lowest picture that keeps the whole sound (``SOUND_CAP``).
        :class:`DubError` when no player gives it."""
        from core import video_hls
        entry = self.entries.get(id(remote))
        if entry is None:                   # not a voice-over's episode (another source's file)
            return None
        direct(session)                     # the players and their files: straight (``direct``)
        say = say or (lambda _text: None)

        def way_said():                     # once: straight did not get through, the system's proxy is taken
            if getattr(session, "lb_route", "") == "system" and not getattr(session, "lb_said", False):
                session.lb_said = True
                say("Напрямую плееры не ответили — дальше через прокси системы (VPN)")
        target = video_download.target_of(self.folder, remote)
        begun = _begun(target)
        sound = role == "sound"
        failures = []
        for player in order(list(entry["links"]), cap_of(quality), begun, sound=sound):
            if failures and stop is not None and stop():
                break
            iframe = entry["links"][player]
            name = PLAYERS[player][0]
            cap = (min(SOUND_CAP[player], cap_of(quality)) if cap_of(quality) else SOUND_CAP[player]) if sound \
                else cap_of(quality)
            try:
                if player == "aniliberty":
                    links = within(self.aniliberty_links(iframe), cap)
                    origin = "{0.scheme}://{0.netloc}/".format(urlparse(iframe))
                    _route(session, links[0][1], "AniLiberty")
                    plan = video_hls.plan_of(session, links, origin)
                    if plan.blocked:
                        raise DubError(plan.blocked)
                    if plan.media.segments:
                        stream_way(session, plan.media.segments[0].url, "AniLiberty", say)
                    plan.media.resume_key = "aniliberty/" + _plain(plan.media.url)
                    self._set(remote, player, links, page=origin, quality=plan.quality or links[0][0])
                    remote.size = plan.size
                    way_said()
                    say(f"{remote.group} / {remote.title}: {name}, {remote.quality}" + (" (ради звука)" if sound else ""))
                    return plan
                if player == "kodik":
                    links = within(kodik_links(session, iframe), cap)
                    plan = video_hls.plan_of(session, links, iframe)
                    if plan.blocked:
                        raise DubError(plan.blocked)
                    plan.media.resume_key = kodik_key(plan.media.url)
                    self._set(remote, player, links, page=iframe, quality=plan.quality or links[0][0])
                    remote.size = plan.size
                    way_said()
                    say(f"{remote.group} / {remote.title}: {name}, {remote.quality}" + (" (ради звука)" if sound else ""))
                    return plan
                if sound and self.sound_alone:          # its sound alone (no picture): ``core/cvh_sound``
                    every = cvh_links(session, iframe, self.playlists)
                    plan = self._sound_plan(remote, every, cap_of(quality))
                    if plan is not None:
                        way_said()
                        say(f"{remote.group} / {remote.title}: {name}, только звук (ради звука)")
                        return plan
                links = self.real(remote.group, cvh_links(session, iframe, self.playlists),
                                  session if not sound and (not cap or cap > 1080) else None)
                links = self._cvh_begun(remote, links, say) or within(links, cap)
                self._set(remote, player, links, page=SITE + "/", quality=links[0][0])
                note = self._note(remote)
                note.parent.mkdir(parents=True, exist_ok=True)
                note.write_text(links[0][0], encoding="utf-8")
                way_said()
                say(f"{remote.group} / {remote.title}: {name}, {remote.quality}" + (" (ради звука)" if sound else ""))
                return None
            except (DubError, video_hls.StreamError) as exc:
                said = str(exc)
                failures.append(said if said.startswith(name) else f"{name}: {said}")
                _LOG.info("%s / %s: %s", remote.group, remote.title, exc)
            except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:   # an answer of a shape
                failures.append(f"{name}: непонятный ответ плеера — возможно, он изменился")  # not known: said, the
                _LOG.warning("%s / %s: %s answered oddly: %r", remote.group, remote.title, name, exc)  # next goes on
            except OSError as exc:                   # the note beside the part: the disk
                raise DubError(f"не записывается в папку — {exc}") from exc
        way_said()
        if not failures:
            raise DubError("остановлено")
        raise DubError("; ".join(failures))

    sound_alone = True                       # a voice-over wanted for its sound: CVH's sound alone (no picture)

    def _sound_plan(self, remote, links: list, cap: int = 0):
        """The plan of a voice-over's sound alone from CVH: the sound of its 480p (128 kbit/s; lower ones are
        poorer) — of the best within «Качество» below 480p — and the key frames of its smallest quality for the
        fitting; None when CVH gives none."""
        from core.video_hls import height_of
        heights = sorted(((height_of(label), url) for label, url in links), key=lambda q: q[0])
        if not heights:
            return None
        within_cap = [q for q in heights if q[0] <= cap] if cap and cap < 480 else []
        sound = within_cap[-1][1] if within_cap else next((url for h, url in heights if h >= 480), heights[-1][1])
        keys = heights[0][1]
        stem = remote.name.rsplit(".", 1)[0]
        remote.name = stem + SOUND_EXT
        remote.url, remote.quality, remote.page, remote.alts = sound, "звук", SITE + "/", []
        remote.stream, remote.size, remote.blocked = "", 0, ""
        self.used[id(remote)] = "cvh"
        return SoundPlan(sound, keys, self.folder)

    def _note(self, remote) -> Path:
        """Beside a CVH file's ``.part``: the quality it was begun in (another quality's bytes never go on it)."""
        place = self.folder / remote.group if remote.group else self.folder
        return place / (remote.name.rsplit(".", 1)[0] + ".mp4.part.cvh")

    def _cvh_begun(self, remote, links: list, say) -> list:
        """The qualities to go on a begun ``.part`` with: its own quality first — or [] (begun anew: the part of
        another quality or of an unknown one is removed)."""
        note = self._note(remote)
        part = note.with_name(note.name[:-len(".cvh")])
        if not part.is_file():
            return []
        try:
            began = note.read_text(encoding="utf-8").strip()
        except OSError:
            began = ""
        same = [q for q in links if q[0] == began]
        if same:                             # the lower ones stay in case the site gives it empty
            from core.video_hls import height_of
            return same + [q for q in links if height_of(q[0]) < height_of(began)]
        say(f"{remote.group} / {remote.title}: начатая часть была в другом качестве ({began or 'неизвестном'}) — "
            "качаю заново")
        part.unlink(missing_ok=True)
        note.unlink(missing_ok=True)
        return []

    def finished(self, remote, state: str) -> None:
        """After the episode (``video_download.download``): a whole file leaves nothing of its other player beside it
        (its begun part, notes); a CVH file stopped or cut off notes the quality its part is in now (the next quality
        is taken when the site gives one empty)."""
        if id(remote) not in self.entries:
            return
        if state in ("failed", "dead") and any(remote is donor for donor in self.donors):
            from core import video_merge         # (v5.44.150) the picture it was to lend did not come: not waited for
            stem = remote.name.rsplit(".", 1)[0]
            if (video_merge.read_pictures(self.folder).get(stem) or {}).get("group") == remote.group:
                video_merge.put_picture(self.folder, stem)
        if state == "done":                      # YummyAnime's times of the episode in the player it came from
            player = self.used.get(id(remote)) or ("kodik" if remote.stream == "hls" else "cvh")
            given = self.entries[id(remote)].get("skips", {}).get(player) or {}
            if given:
                from core import video_skips
                try:
                    video_skips.hint(self.folder, video_download.target_of(self.folder, remote), **given)
                except OSError as exc:
                    _LOG.info("skips of %s not kept: %s", remote.name, exc)
        if state not in ("done", "whole"):
            note = self._note(remote)
            if not remote.stream and remote.quality and note.is_file():
                try:
                    note.write_text(remote.quality, encoding="utf-8")
                except OSError as exc:
                    _LOG.info("%s not written: %s", note, exc)
            return
        target = video_download.target_of(self.folder, remote)
        stem = target.name.rsplit(".", 1)[0]
        for ext in sorted(set(EXT_OF.values()) | {SOUND_EXT}):
            part = target.with_name(f"{stem}{ext}.part")
            for leftover in ((part, part.with_name(part.name + ".hls"), part.with_name(part.name + ".cvh"),
                              part.with_name(part.name + ".cvhs"))
                             if ext != target.suffix else (part.with_name(part.name + ".cvh"),)):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError as exc:
                    _LOG.info("%s not removed: %s", leftover, exc)

    def _set(self, remote, player: str, links: list, *, page: str, quality: str) -> None:
        stem = remote.name.rsplit(".", 1)[0]
        remote.name = stem + EXT_OF[player]
        remote.url, remote.quality, remote.page = links[0][1], quality, page
        remote.alts = [list(q) for q in links[1:]]
        remote.stream = "hls" if player in ("kodik", "aniliberty") else ""
        remote.size, remote.blocked = 0, ""
        self.used[id(remote)] = player


# ── a page → what to download ───────────────────────────────────────────────────────
def find_address(address: str, root: Path, *, settings: dict, say, stop_check=None) -> video_download.Found | None:
    """The voice-overs of a YummyAnime title by the address of its page (the page is not read: the API gives the
    title and its description) — None when the address is not such a page or the voice-overs are switched off."""
    if not address or not enabled(settings) or not is_yummy(address) or not alias_of(address):
        return None
    api = Api(api_of(settings))
    say(f"Страница {NAME} — беру тайтл, описание и все озвучки из API сайта")
    try:
        anime = api.anime(alias_of(address))
        if anime is None:
            raise DubError(f"У {NAME} нет тайтла «{alias_of(address)}» — проверьте ссылку")
        return _found(api, anime, title_page(anime, address), root, say=say, stop=stop_check or (lambda: False),
                      settings=settings)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:     # an answer of a shape not known
        _LOG.warning("%s API answered oddly: %r", NAME, exc)
        raise DubError(f"{NAME} прислал непонятный ответ — возможно, его API изменился") from exc


def find(page, root: Path, *, settings: dict, say, stop_check=None) -> video_download.Found | None:
    """The voice-overs of the title of a YummyAnime page read or saved — None when it is not one (or they are
    switched off)."""
    if not enabled(settings) or not video_download.is_frame_site(page.url, page.html):
        return None
    alias = alias_in(page)
    if not alias:
        return None
    api = Api(api_of(settings))
    say(f"Своего видео на странице нет — беру озвучки тайтла из API {NAME}…")
    try:
        anime = api.anime(alias)
        if anime is None:
            raise DubError(f"У {NAME} нет тайтла «{alias}»")
        return _found(api, anime, page, root, say=say, stop=stop_check or (lambda: False), settings=settings)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:     # an answer of a shape not known
        _LOG.warning("%s API answered oddly: %r", NAME, exc)
        raise DubError(f"{NAME} прислал непонятный ответ — возможно, его API изменился") from exc


def _episode_count(title: dict) -> int:
    episodes = title.get("episodes")
    count = episodes.get("count") if isinstance(episodes, dict) else None
    return count if type(count) is int and count > 0 else 0


def _shared_runs(videos: list, related: list, count: int) -> dict:
    """Map a shared row to the size of its overlong studio/player sequence."""
    theirs = {(v["iframe_url"], number_of(v.get("number"))) for v in related if isinstance(v.get("iframe_url"), str)}
    sequences = {}
    for index, video in enumerate(videos):
        number = number_of(video.get("number"))
        url = video.get("iframe_url")
        if number is None or not isinstance(url, str) or not url or (url, number) not in theirs:
            continue
        data = video["data"] if isinstance(video.get("data"), dict) else {}
        key = (str(data.get("dubbing") or "").strip(), str(data.get("player") or ""))
        sequences.setdefault(key, []).append((index, number))
    runs = {}
    for rows in sequences.values():
        # Number 0 and fractional specials need not be in the announced regular count.
        size = len({n for _, n in rows if isinstance(n, int) and n > 0})
        if size > count:
            runs.update((index, size) for index, _ in rows)
    return runs


def borrowed_of(api: Api, anime: dict, videos: list, *, stop) -> tuple[list, list]:
    """The videos of this title and the related titles whose videos YummyAnime hung on its page.  The site does that:
    the recap of «Shangri-La Frontier» (one episode) carries all 25 episodes of the first season in ten voice-overs —
    downloaded, they would be the season's episodes under the recap's name. Only a matching sequence longer than the
    title's regular run, within one studio/player, identifies a borrowed release. Comparing the published numbers too
    preserves a special listed under its own number on this page."""
    count = _episode_count(anime)
    numbers = {n for v in videos if isinstance(n := number_of(v.get("number")), int) and n > 0}
    if count <= 0 or len(numbers) <= count:
        return videos, []
    own = anime.get("anime_id")
    left, borrowed = list(videos), []
    for other in anime.get("viewing_order") or []:
        if stop() or not left:
            break
        other_id = other.get("anime_id") if isinstance(other, dict) else None
        if not isinstance(other_id, int) or isinstance(other_id, bool) or other_id == own:
            continue
        status = other.get("anime_status") if isinstance(other.get("anime_status"), dict) else {}
        if str(status.get("alias") or "") == "announcement":     # not out: it has no videos to lend
            continue
        try:
            runs = _shared_runs(left, api.videos(other_id), count)
            if not runs or stop():
                continue
            lender = other if _episode_count(other) else api.anime(str(other.get("anime_url") or other_id))
            if not lender or lender.get("anime_id") != other_id:
                continue
            lender_count = _episode_count(lender)
            same = {index for index, size in runs.items() if size <= lender_count}
        except DubError as exc:                  # the related title not read: its videos stay (nothing is known)
            _LOG.info("%s: the videos of the related title %s not read: %s", NAME, other_id, exc)
            continue
        if same:
            borrowed.append({"title": str(other.get("title") or "").strip() or f"№ {other_id}",
                             "alias": str(other.get("anime_url") or ""), "videos": len(same)})
            left = [v for index, v in enumerate(left) if index not in same]
    return left, borrowed


def _other_page(address: str, alias: str) -> str:
    """The page of a related title on the host of the page given (``old.yummyani.me`` stays itself)."""
    parts = urlparse(address or "")
    own = parts.scheme in ("http", "https") and parts.netloc and (is_yummy(address) or parts.path.startswith("/catalog/item/"))
    host = f"{parts.scheme}://{parts.netloc}" if own else SITE
    return f"{host}/catalog/item/{quote(alias, safe='-_')}" if alias else ""


def cvh_lists(session, dubs: list, *, stop) -> dict:
    """CVH's own list of the title's episodes, asked once at the search — the list every episode's file is taken from
    later (``cvh_links``: kept, not asked again).  YummyAnime keeps CVH in its list for titles CVH has none of
    («Shangri-La Frontier»: CVH's list is empty for both seasons) — the page promised «до 1080p» and the download gave
    Kodik's 720p.  -> {CVH's title id: its items}; a title whose list was not read is not in it (nothing known)."""
    frames = {}
    for dub in dubs:
        for links in dub.episodes.values():
            if "cvh" in links:
                aid = (parse_qs(urlparse(absolute(links["cvh"])).query).get("anime_id") or [""])[0]
                if aid.isdigit():
                    frames.setdefault(aid, links["cvh"])
    lists = {}
    for aid, iframe in frames.items():
        if stop():
            break
        try:
            pub, aggr = _cvh_keys(session, iframe)
            data = _cvh_get(session, "/playlist", {"pub": pub, "aggr": aggr, "id": aid})
        except DubError as exc:
            _LOG.info("CVH's list of %s not read at the search: %s", aid, exc)
            continue
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(items, list):          # an answer of another shape tells nothing: its episodes stay as listed
            lists[aid] = [i for i in items if isinstance(i, dict)]
    return lists


def without_missing_cvh(dubs: list, lists: dict) -> tuple[list, int]:
    """The voice-overs with only the CVH episodes CVH's list has (``cvh_lists``); an episode left with no player and a
    voice-over left with no episode go.  -> (the voice-overs, how many CVH episodes CVH has not)."""
    gone = 0
    for dub in dubs:
        for number in list(dub.episodes):
            links = dub.episodes[number]
            frame = links.get("cvh")
            if not frame:
                continue
            query = parse_qs(urlparse(absolute(frame)).query)
            aid = (query.get("anime_id") or [""])[0]
            if aid not in lists:
                continue
            studio = unquote_plus((query.get("dubbing_code") or [""])[0]).strip()
            episode = number_of((query.get("episode") or [""])[0])
            if any(number_of(i.get("episode")) == episode and str(i.get("voiceStudio") or "").strip() == studio
                   and i.get("vkId") for i in lists[aid]):
                continue
            del links["cvh"]
            gone += 1
            if not links:
                del dub.episodes[number]
                dub.skips.pop(number, None)
    kept = [d for d in dubs if d.episodes]
    return sorted(kept, key=lambda d: (-len(d.episodes), d.first)), gone


def _anilibria(name: str) -> bool:
    from core.video_merge import dub_key
    return dub_key(name) == dub_key(ANILIBRIA)


def aniliberty_release(anime: dict, dubs: list, rest: list, settings: dict, *, say, stop) -> tuple[dict | None, str]:
    """AniLibria's release of the title on AniLiberty — the third player (owner: «считай как новый плеер для
    скачивания более качественного видео»): AniLibria's own site gives its voice-over up to 1080p (Kodik — 720p) with
    a fuller sound (~195 kbit/s against ~93).  By the alias YummyAnime knows (``remote_ids.anilibria_alias``); else,
    when the title has AniLibria's voice-over in a player at all, found by its numbers (Shikimori / MyAnimeList — the
    same only).  A release whose numbers are another title's is not it.  (release with its episodes, its site) —
    (None, …) when there is none or AniLiberty is switched off («Настройки → Вкладка «Видео»»)."""
    if not video_sources.enabled(settings or {}):
        return None, ""
    site = video_sources.site_of(settings or {})
    remote = anime.get("remote_ids") if isinstance(anime.get("remote_ids"), dict) else {}
    alias = str(remote.get("anilibria_alias") or "").strip()
    ids = ids_of(anime)
    voiced = any(_anilibria(d.name) for d in dubs) or any(_anilibria(r["name"]) for r in rest)
    if not alias and not (voiced and ids):
        return None, site
    api = video_sources.AniLiberty(site)
    try:
        if alias:
            release = api.release(alias)
        else:
            names = [str(anime.get("title") or "")] + [str(n) for n in anime.get("other_titles") or [] if n]
            year = anime.get("year") if isinstance(anime.get("year"), int) else 0
            found, how, _others = video_sources.find_release(
                api, video_sources.Title(names=[n for n in names if n.strip()], year=year, kind="", ids=ids),
                say=say, stop=stop)
            release = api.release(found.get("alias") or found.get("id")) if found and how == "same" else None
    except video_sources.SourceError as exc:
        _LOG.info("AniLiberty for %s: %s", anime.get("anime_id"), exc)
        say(f"AniLiberty не ответил ({exc}) — AniLibria только из других плееров")
        return None, site
    if not isinstance(release, dict):
        return None, site
    theirs = video_sources.ids_of(release)
    shared = [k for k in ("shikimori", "mal") if k in theirs and k in ids]
    if shared and not any(theirs[k] == ids[k] for k in shared):
        _LOG.info("AniLiberty's %s is another title (%s against %s)", alias, theirs, ids)
        return None, site
    return release, site


def _aniliberty_skips(episode: dict) -> dict:
    """AniLiberty's own times of an episode's opening and ending ({"opening": [start, stop] | None, …})."""
    out = {}
    for kind in ("opening", "ending"):
        part = episode.get(kind) if isinstance(episode.get(kind), dict) else {}
        start, stop = part.get("start"), part.get("stop")
        if isinstance(start, (int, float)) and isinstance(stop, (int, float)) and 0 <= start < stop:
            out[kind] = [float(start), float(stop)]
        else:
            out[kind] = None
    return out if any(out.values()) else {}


def with_aniliberty(dubs: list, rest: list, release: dict, site: str) -> tuple[list, list, int]:
    """AniLibria's voice-over with the episodes of its AniLiberty release as one more player ("aniliberty": the
    release page's address with the episode's id — its streams are asked right before the episode, as the others').
    No AniLibria in Kodik or CVH — the voice-over is made of AniLiberty alone (and is not «only in other players»
    any more).  -> (the voice-overs, the others, how many episodes AniLiberty gives)."""
    alias = str(release.get("alias") or release.get("id") or "")
    page = video_sources.release_url(release, site)
    dub = next((d for d in dubs if _anilibria(d.name)), None)
    given = 0
    for episode in release.get("episodes") or []:
        if not isinstance(episode, dict) or not any(episode.get(f"hls_{q}") for q in video_sources.QUALITIES):
            continue
        number = number_of(episode.get("ordinal"))
        if number is None or not alias:
            continue
        if dub is None:
            dub = Dub(name=ANILIBRIA, first=len(dubs))
            dubs.append(dub)
        key = str(episode.get("id") or "") or f"n{number:g}"
        dub.episodes.setdefault(number, {})["aniliberty"] = f"{page}#{quote(key, safe='-_.')}"
        skips = _aniliberty_skips(episode)
        if skips:
            dub.skips.setdefault(number, {})["aniliberty"] = skips
        given += 1
    rest = [r for r in rest if not (given and _anilibria(r["name"]))]
    return sorted(dubs, key=lambda d: (-len(d.episodes), d.first)), rest, given


def order_of(anime: dict, address: str) -> list:
    """YummyAnime's «Порядок просмотра» of the title — the seasons, films, recaps and extras of its franchise, in the
    order to watch them (``viewing_order`` of the API: no request of its own) — for the page: each one opens with a
    click.  Empty when the title stands alone."""
    own = anime.get("anime_id")
    out = []
    for item in anime.get("viewing_order") or []:
        if not isinstance(item, dict) or not isinstance(item.get("anime_id"), int):
            continue
        kind = item.get("data") if isinstance(item.get("data"), dict) else {}
        shape = item.get("type") if isinstance(item.get("type"), dict) else {}
        status = item.get("anime_status") if isinstance(item.get("anime_status"), dict) else {}
        year = item.get("year") if isinstance(item.get("year"), int) and not isinstance(item.get("year"), bool) else None
        out.append({"title": str(item.get("title") or "").strip() or f"№ {item['anime_id']}",
                    "kind": str(kind.get("text") or "").strip(), "type": str(shape.get("shortname") or "").strip(),
                    "year": year, "soon": str(status.get("alias") or "") == "announcement",
                    "status": str(status.get("alias") or ""), "alias": str(item.get("anime_url") or ""),
                    "here": item["anime_id"] == own,
                    "address": _other_page(address, str(item.get("anime_url") or ""))})
    return out if len(out) > 1 and any(o["here"] for o in out) else []


def _players_words(kodik: int, cvh: int, aniliberty: int = 0) -> str:
    parts = [part for n, part in ((kodik, "Kodik (до 720p)"), (cvh, "CVH (до 1080p, у части — 2K и 4K)"),
                                  (aniliberty, "AniLiberty (озвучка AniLibria до 1080p)")) if n]
    if parts == ["Kodik (до 720p)"]:
        return "плеер Kodik (до 720p — лучше у этого тайтла нет)"
    return ("плеер " if len(parts) == 1 else "плееры ") + (" и ".join(parts) if len(parts) < 3 else
                                                         f"{parts[0]}, {parts[1]} и {parts[2]}")


def _found(api: Api, anime: dict, page, root: Path, *, say, stop, settings: dict | None = None,
           allow_external_only: bool = False) -> video_download.Found:
    videos = api.videos(anime["anime_id"])
    if stop():
        raise DubError("Поиск остановлен")
    videos, borrowed = borrowed_of(api, anime, videos, stop=stop)
    if stop():
        raise DubError("Поиск остановлен")
    for other in borrowed:
        say(f"{NAME} приложил к этой странице видео другого тайтла — «{other['title']}» ({other['videos']}): их не беру")
    if borrowed and not videos:
        names = "; ".join(f"«{o['title']}» — {_other_page(page.url, o['alias'])}" for o in borrowed)
        raise DubError(f"Все видео на этой странице {NAME} — серии другого тайтла: {names}. Вставьте ссылку на него")
    listed = sorted({n for row in videos if row.get("iframe_url") and (n := number_of(row.get("number"))) is not None})
    dubs, rest = dubs_of(videos)
    lists = cvh_lists(api.session, dubs, stop=stop)
    dubs, gone = without_missing_cvh(dubs, lists)
    if gone:
        say(f"В CVH нет {gone} из серий, что {NAME} показывает в нём" if any("cvh" in d.players for d in dubs) else
            f"В CVH нет ни одной серии этого тайтла, хотя {NAME} показывает его плеер")
    release, site = aniliberty_release(anime, dubs, rest, settings or {}, say=say, stop=stop)
    if release is not None:
        dubs, rest, given = with_aniliberty(dubs, rest, release, site)
        if given:
            say(f"AniLibria — ещё и с AniLiberty, её собственного сайта: {given} "
                f"{_plural(given, ('серия', 'серии', 'серий'))} до 1080p")
    if stop():
        raise DubError("Поиск остановлен")
    elsewhere = ", ".join(sorted({p for r in rest for p in r["players"]}))
    if not dubs and not (allow_external_only and listed):
        raise DubError(f"У {NAME} нет серий этого тайтла в плеерах, из которых программа скачивает (Kodik, CVH)"
                       + (f" — только в {elsewhere}" if elsewhere else ""))
    meta = video_meta.parse_page(page.html, page.url)
    ids = meta.get("ids") or ids_of(anime)
    title = str(meta.get("title") or anime.get("title") or "").strip()
    folder, new = video_download.target_folder(root, title, page.url, ids=ids)
    source = {"site": NAME, "title": title, "exact": True, "about": "page", "listed": listed,
              "borrowed": [dict(o, address=_other_page(page.url, o["alias"])) for o in borrowed],
              "order": order_of(anime, page.url)}
    voices = [{"name": r["name"], "here": False, "players": r["players"]} for r in rest]
    if not dubs:
        return video_download.Found(page=page, title=title, folder=folder, new_folder=new, about=page,
                                    sources=[source], voices=voices, facts=video_download.facts_of(meta))
    files, entries = files_of(dubs, folder)
    kodik = sum(1 for d in dubs if "kodik" in d.players)
    cvh = sum(1 for d in dubs if "cvh" in d.players)
    libria = sum(1 for d in dubs if "aniliberty" in d.players)
    most = max(len(d.episodes) for d in dubs)
    status = anime.get("anime_status") if isinstance(anime.get("anime_status"), dict) else {}
    ongoing = str(status.get("alias") or "") == "ongoing" or status.get("value") == 1
    where = "; ".join(part for n, part in ((kodik, f"в Kodik — {kodik}, до 720p"),
                                           (cvh, f"в CVH — {cvh}, до 1080p, у части — 2K и 4K"),
                                           (libria, "AniLibria с AniLiberty — до 1080p")) if n)
    say(f"Озвучек и субтитров: {len(dubs)} ({where}); серий у самой полной: {most}" + (f". Ещё {len(rest)} — только в плеерах {elsewhere}: их программа не скачивает"
                                           if rest else ""))
    source.update({"dub": "Озвучки",
              "what": _what(dubs),
              "how": _players_words(kodik, cvh, libria) + "; ссылки на видео программа берёт перед скачиванием каждой серии, "
                     "в качестве, выбранном в «Качестве»",
              "episodes": most, "total": len(files), "ongoing": ongoing,
              "dubs": [{"name": d.name, "episodes": len(d.episodes), "best": d.best,
                        "players": [PLAYERS[p][0] for p in d.players]} for d in dubs]})
    from core.video_merge import Merger
    merger = Merger(files, folder)
    resolver = Resolver(entries, folder, files)
    resolver.playlists.update(lists)         # asked at the search: not again for the episodes
    merger.pictures = resolver.pictures      # the episode's picture: the voice-over chosen for it (``Resolver.roles``)
    found = video_download.Found(page=page, title=title, folder=folder, files=files, new_folder=new, about=page,
                                 sources=[source], voices=voices, facts=video_download.facts_of(meta),
                                 choose="dubs", resolve=resolver, had=merger.had, after=merger)
    video_download.complete(found, say, stop)
    return found


def _what(dubs: list) -> str:
    """«10 озвучек, 2 дубляжа и 1 вариант субтитров» — what the title has, by kind (as the page's «Дубляж / Озвучка /
    Субтитры»)."""
    subs = sum(1 for d in dubs if d.name.lower().startswith("субтитры"))
    dubbed = sum(1 for d in dubs if d.name.lower().startswith("дубляж"))
    voiced = len(dubs) - subs - dubbed
    parts = [f"{n} {_plural(n, forms)}" for n, forms in (
        (voiced, ("озвучка", "озвучки", "озвучек")), (dubbed, ("дубляж", "дубляжа", "дубляжей")),
        (subs, ("вариант субтитров", "варианта субтитров", "вариантов субтитров"))) if n]
    return " и ".join(parts) if len(parts) < 3 else f"{parts[0]}, {parts[1]} и {parts[2]}"


def _plural(n: int, forms: tuple) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    return forms[1] if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else forms[2]
