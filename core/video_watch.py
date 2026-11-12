"""(v5.44.106) «Смотреть» a series of a site in the program — without downloading it.

The owner (5.11.2026): «еще что бы можно было смотреть не скачивая …»; (6.11.2026) «Делай просмотр видео»; the look —
the mockup «Сериал» of the canvas «Local Booru — Скачать и Онлайн» he approved.

A title of YummyAnime (``…/catalog/item/<alias>``) is what «Скачать» finds of it: the other chat's ``core/video_dubs``
(``Api``, ``_found``) — every voice-over, its episodes and the players that show them (Kodik, CVH, AniLiberty).  Only
its answers and its functions of the players are used here; nothing of its downloads changes.  A title is looked up in
a thread of its own and kept a while (:func:`look`, :data:`TITLE_TTL`); the page waits for it a moment and asks again.

An episode watched (:func:`open_stream` → a token):

* its video is asked of its player right before it plays, in the quality chosen («Качество» as «Скачать» has it: the
  best, or not above 1080/720/480/360), the way «Скачать» asks it (``video_dubs.kodik_links``, ``cvh_links``,
  ``Resolver.aniliberty_links``); a player that does not give it — the episode's next player;
* the program hands it to the player under its own addresses: a stream (Kodik, AniLiberty: HLS) — its list of parts
  rewritten (``/watch/s/<token>/index.m3u8``: ``0.ts``, ``1.ts``…; MP4 fragments — ``init.mp4``, ``0.m4s``…), each part
  asked of the site when the player wants it (:func:`piece`); a file (CVH: MP4) — ``/watch/s/<token>/video.mp4``, the
  player's ranges passed on (:func:`file_part`).  The sites' addresses are signed and live for hours: a part refused
  (401/403/404/410) asks the player again (:func:`_ask_again`), not more often than :data:`ASK_AGAIN_AFTER`;
* the player: the window's own (Qt Multimedia — the window's browser has no H.264 and AAC), a browser's ``<video>``
  (hls.js where it does not play HLS itself), the phone's;
* nothing is written to the disk but where it stopped (:data:`PROGRESS` — (v5.44.121) the one memory of the tab
  «Видео», ``core/video_library.Progress``, ``video_progress.json``; ``video_watch.json`` before: the title's last
  episode and its place, the places of the others, the episodes watched, the voice-over, player and quality chosen).

(v5.44.109) A title of Hentasis too (``…/1112-dark-summoner-to-dekiteiru.html``, a mirror ``hentasisN.top``; the owner,
6.11.2026: «я на компе открыл ленту видео и оно опять открывает скачивание») — :data:`PAGE_SITES`.  Its episodes are
what «Скачать» finds on its page (``video_download.look``: the player's list of files; other sites' players in its
frames — ``video_embeds.fill``, their video asked right before an episode plays): a voice-over is a sub-folder of
«Скачать» («Озучка от Dream_Cast», «Субтитры от …» — the site's «Примечание»), its episodes — its files, numbered as
«Скачать» numbers them (``tile_labels``; sounds — not episodes).  A file goes to the player the way CVH's does
(:func:`file_part`), an open stream — the way Kodik's does; there is one «player» (:data:`PAGE_PLAYER` — the site) and
no «Качество» (the page's other qualities of a file — when the best does not come).  The page's own facts, description,
genres and poster (``video_meta.parse_page``), its rating (DLE stars).  Such a site is hentai; a title the program's
gate closes (``gates.title`` of what :func:`_said` reads — its names and its genres) is not opened.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from core import gates as G

_LOG = logging.getLogger("local_booru.video")

SITE_NAME = "YummyAnime"
# (v5.44.109) sites whose pages hand out their episodes as files («Скачать» reads them): the key's word -> the name
PAGE_SITES = {"hentasis": "Hentasis", "haven": "HentaiHaven",   # (v5.44.138) HentaiHaven
              "hentaixs": "Hentai-XS", "hfshare": "HFShare"}      # (v5.44.139)
PAGE_PLAYER = "site"          # the one «player» of such a title: the site's own files
TITLE_TTL = 20 * 60           # seconds a title found is kept (its voice-overs, the players' lists asked with it)
TITLES_KEPT = 6
FAILED_TTL = 60.0             # a title that did not come is said so this long, then asked again
LOOK_WAIT = 2.5               # seconds the page waits for a title before «… отвечает»
STREAM_IDLE = 6 * 3600        # a stream nobody asked for this long is forgotten
STREAMS_KEPT = 40
ASK_AGAIN_AFTER = 20.0        # seconds: a stream's addresses asked of its player again at most this often
PIECE_TRIES = 3
PIECE_MAX = 96 * 1024 * 1024  # bytes of one part of a stream
PLAYLIST_MAX = 4 * 1024 * 1024
TIMEOUT = (15, 40)
CHUNK = 256 * 1024
KEEP_NAME = "video_progress.json"   # (v5.44.121) the one memory of the tab «Видео» (``video_watch.json`` before)
KINDS = (("dub", "Дубляж"), ("voice", "Озвучка"), ("subs", "Субтитры"))
_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,64}")
_RANGE = re.compile(r"bytes=\d{0,15}-\d{0,15}")


class WatchError(Exception):
    """Said to the person as it is."""


class Gone(WatchError):
    """What is asked is not here (a stream forgotten, a part past its end): 404."""


# ── a title ────────────────────────────────────────────────────────────────────────────────────────────────────────
def alias_of(address: str) -> str:
    """The alias of a YummyAnime title's page, else ""."""
    from core import video_dubs as Y
    return Y.alias_of(address) if Y.is_yummy(address) else ""


def key_of(address: str) -> str:
    """«yummy:<alias>» — a title of YummyAnime; (v5.44.109) «hentasis:<number>» — of Hentasis; (v5.44.138)
    «haven:<slug>» — of HentaiHaven; (v5.44.139) «hentaixs:<number>» — of Hentai-XS, «hfshare:<number>» — of HFShare;
    else ""."""
    alias = alias_of(address)
    if alias:
        return f"yummy:{alias}"
    from core.catalog_have import haven_slug, hentasis_number, hfshare_number, xs_number   # as «Лента» tells them
    number = hentasis_number(address)
    if number:
        return f"hentasis:{number}"
    slug = haven_slug(address)
    if slug:
        return f"haven:{slug}"
    number = xs_number(address)
    if number:
        return f"hentaixs:{number}"
    number = hfshare_number(address)
    return f"hfshare:{number}" if number else ""


def site_of(address: str) -> str:
    """The name of the site of a title this page shows («YummyAnime», «Hentasis»), else ""."""
    word = key_of(address).split(":", 1)[0]
    return SITE_NAME if word == "yummy" else PAGE_SITES.get(word, "")


def is_page(key: str) -> bool:
    """(v5.44.109) A title of a site whose page hands out its files (:data:`PAGE_SITES`), not of YummyAnime."""
    return str(key or "").split(":", 1)[0] in PAGE_SITES


def is_watchable(address: str) -> bool:
    """A title this page can show and play: YummyAnime's, (v5.44.109) Hentasis's."""
    return bool(key_of(address))


def address_of(alias: str) -> str:
    from core import video_dubs as Y
    from urllib.parse import quote
    return f"{Y.SITE}/catalog/item/{quote(str(alias), safe='')}"


def kind_of(name: str) -> str:
    """«dub», «voice» or «subs» — by the first word of the voice-over's name, as the download's page tells them."""
    low = str(name or "").strip().lower()
    return "dub" if low.startswith("дубляж") else "subs" if low.startswith("субтитры") else "voice"


def short_of(name: str) -> str:
    """The studio's name without «Озвучка», «Дубляж», «Субтитры» before it — (v5.44.109) and «от» after it, the way
    Hentasis names its files («Озучка от Dream_Cast» — its own spelling)."""
    text = re.sub(r"^(?:оз?в?учка|дубляж|субтитры)\s*(?:от\s+)?[:\-—]?\s*", "", str(name or ""), flags=re.I).strip()
    return text or str(name or "")


def label_of(number) -> str:
    try:
        return f"{float(number):g}"
    except (TypeError, ValueError):
        return str(number)


@dataclass
class Episode:
    number: object                # 1, 12.5
    label: str                    # «1», «12.5»
    links: dict                   # player -> the address of its frame of this episode
    skips: dict                   # player -> {"opening": [a, b], "ending": [a, b]} — YummyAnime's people's times
    group: str                    # the voice-over's sub-folder, as «Скачать» names it
    title: str                    # «Серия 12», as «Скачать» names the file
    file: int                     # its place in the list «Скачать» found
    have: str = ""                # in the folder: "whole", "part", "other" ("" — not)


@dataclass
class Dub:
    name: str
    kind: str
    episodes: list = field(default_factory=list)

    @property
    def players(self) -> list:
        from core import video_dubs as Y
        found = {p for e in self.episodes for p in e.links}
        return [p for p in Y.ORDER if p in found]

    def episode(self, label: str):
        return next((e for e in self.episodes if e.label == str(label)), None)


@dataclass
class Title:
    key: str
    alias: str
    address: str
    anime: dict
    found: object                 # video_download.Found of «Скачать»
    root: Path
    dubs: list = field(default_factory=list)
    at: float = field(default_factory=time.time)
    site: str = SITE_NAME
    meta: dict = field(default_factory=dict)      # (v5.44.109) a page title: what its page says (video_meta)

    @property
    def page(self) -> bool:
        return is_page(self.key)

    @property
    def name(self) -> str:
        return str(self.anime.get("title") or getattr(self.found, "title", "") or self.meta.get("title")
                   or self.alias or self.site)

    @property
    def hentai(self) -> bool:
        """As the feed tells YummyAnime's titles (``core/catalog`` ``_yummy_items``): the age «Rx» or the genre;
        (v5.44.109) a title of Hentasis — always."""
        if self.page:
            return True
        age = str(_dict(self.anime.get("min_age")).get("title") or "").strip().casefold()
        return age == "rx" or "хентай" in {g.casefold() for g in _names(self.anime.get("genres"))}

    def dub(self, name: str):
        return next((d for d in self.dubs if d.name == str(name)), None)


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _names(items, key: str = "title") -> list:
    return [str(x.get(key)).strip() for x in items or [] if isinstance(x, dict) and str(x.get(key) or "").strip()]


def _dubs_of(found, root: Path) -> list:
    """The voice-overs of a found title, each with its episodes as «Скачать» lists them (and what of them is in the
    folder now: ``Found.public`` looks at it)."""
    found.public(root)
    entries = getattr(found.resolve, "entries", None) or {}
    out: "OrderedDict[str, Dub]" = OrderedDict()
    for n, remote in enumerate(found.files):
        entry = entries.get(id(remote))
        if not entry:
            continue
        name = str(entry.get("dub") or remote.group or "")
        dub = out.get(name)
        if dub is None:
            dub = out[name] = Dub(name=name, kind=kind_of(name))
        number = entry.get("number")
        dub.episodes.append(Episode(number=number, label=label_of(number), links=dict(entry.get("links") or {}),
                                    skips=dict(entry.get("skips") or {}), group=str(remote.group or ""),
                                    title=str(remote.title or ""), file=n, have=str(remote.have or "")))
    for dub in out.values():
        dub.episodes.sort(key=lambda e: float(e.number))
    return list(out.values())


PLAIN = ""                        # (v5.44.109) the voice-over of a page's files the site names no one for


def _page_dubs(found, root: Path) -> list:
    """(v5.44.109) The voice-overs of a page title: its video files by the sub-folder «Скачать» puts them in (the
    site's «Примечание»; none — :data:`PLAIN`), each file an episode numbered as «Скачать» numbers it (sounds and
    what cannot be taken — not); what is in the folder now (``Found.public``)."""
    from core import video_download as D
    found.public(root)
    labels = D.tile_labels(found.files)
    out: "OrderedDict[str, Dub]" = OrderedDict()
    for n, remote in enumerate(found.files):
        if remote.kind != "video" or remote.blocked:
            continue
        name = str(remote.group or PLAIN)
        dub = out.get(name)
        if dub is None:
            dub = out[name] = Dub(name=name, kind=kind_of(name) if name else "voice")
        label, more = str(labels[n] or len(dub.episodes) + 1), 2
        while dub.episode(label) is not None:            # two files of one number: the second «N (2)»
            label, more = f"{labels[n] or len(dub.episodes) + 1} ({more})", more + 1
        try:
            number = float(label)
        except ValueError:
            number = 10 ** 6 + n                          # not a number: after the numbered ones, in the site's order
        dub.episodes.append(Episode(number=number, label=label, links={PAGE_PLAYER: remote.url}, skips={},
                                    group=str(remote.group or ""), title=str(remote.title or ""), file=n,
                                    have=str(remote.have or "")))
    for dub in out.values():
        dub.episodes.sort(key=lambda e: float(e.number))
    return list(out.values())


def _said(title: "Title") -> dict:
    """What a title says of itself, for the program's gate — ``gates.title(**…)`` (a title it closes is not opened: the
    feed does not show it, a link to it is not opened either): its site, its names and genres (a page's — as its page
    tells them, YummyAnime's — as its API does), whether it is hentai."""
    if title.page:
        names = [title.meta.get("title"), *(title.meta.get("alt_titles") or [])]
        return {"site": title.key.split(":", 1)[0], "names": [str(x) for x in names if x],
                "genres": [str(g) for g in title.meta.get("genres") or [] if g], "adult": title.hentai}
    names = [title.anime.get("title"), *(title.anime.get("other_titles") or [])]
    return {"site": "yummy", "names": [str(x) for x in names if x], "genres": _names(title.anime.get("genres")),
            "adult": title.hentai}


@dataclass
class _Looking:
    done: threading.Event = field(default_factory=threading.Event)
    said: str = ""
    error: str = ""
    at: float = field(default_factory=time.time)


_LOCK = threading.Lock()
_TITLES: "OrderedDict[str, Title]" = OrderedDict()
_LOOKING: dict = {}            # key -> _Looking: going, or failed (said for FAILED_TTL)


NOT_WATCHABLE = ("Смотреть в программе можно тайтлы YummyAnime, Hentasis, HentaiHaven, Hentai-XS и HFShare — нужна "
                 "ссылка на страницу тайтла")


def look(address: str, settings: dict, root: Path, *, wait: float = LOOK_WAIT, again: bool = False, window=None):
    """(the title, what is being done while it is looked up, why it did not come) — the title once it is here; it
    is looked up in a thread of its own, ``wait`` seconds waited for.  ``again`` — asked anew.  ``window(address)`` —
    the program window's browser for a page that checks for robots (a page title; None — without it)."""
    key = key_of(address)
    if not key:
        raise WatchError(NOT_WATCHABLE)
    with _LOCK:
        kept = _TITLES.get(key)
        if kept is not None and not again and time.time() - kept.at < TITLE_TTL:
            _TITLES.move_to_end(key)
            return kept, "", ""
        looking = _LOOKING.get(key)
        if looking is not None and looking.done.is_set() and (again or time.time() - looking.at > FAILED_TTL):
            looking = None
        if looking is None:
            looking = _LOOKING[key] = _Looking()
            threading.Thread(target=_look, args=(key, address, dict(settings or {}), Path(root), looking, window),
                             name="video-watch-look", daemon=True).start()
    looking.done.wait(max(0.0, float(wait)))
    with _LOCK:
        kept = _TITLES.get(key)
    if looking.done.is_set() and not looking.error and kept is not None:
        return kept, "", ""
    return None, looking.said, looking.error if looking.done.is_set() else ""


def kept(address: str):
    """The title found and kept (None — not, or too old: the page asks it again)."""
    key = key_of(address)
    with _LOCK:
        title = _TITLES.get(key)
    return title if title is not None and time.time() - title.at < TITLE_TTL + STREAM_IDLE else None


def _yummy_title(key: str, address: str, settings: dict, root: Path, say) -> Title:
    from core import video_dubs as Y
    if not Y.enabled(settings):
        raise WatchError(f"Озвучки {SITE_NAME} выключены — включите «Все озвучки {SITE_NAME}» в «Настройки → Видео»")
    alias = alias_of(address)
    api = Y.Api(Y.api_of(settings))
    say(f"Спрашиваю {SITE_NAME} о тайтле…")
    anime = api.anime(alias)
    if anime is None:
        raise WatchError(f"У {SITE_NAME} нет тайтла «{alias}» — проверьте ссылку")
    page = address_of(alias)
    found = Y._found(api, anime, Y.title_page(anime, page), root, say=say, stop=lambda: False, settings=settings)
    title = Title(key=key, alias=alias, address=page, anime=anime, found=found, root=root)
    why = G.title(**_said(title))
    if why:
        raise WatchError(why)
    title.dubs = _dubs_of(found, root)
    if not title.dubs:
        raise WatchError(f"У {SITE_NAME} нет серий этого тайтла в плеерах, которые программа понимает")
    return title


def _page_title(key: str, address: str, root: Path, say, window) -> Title:
    """(v5.44.109) A title of a site whose page hands out its files — read the way «Скачать» reads it."""
    from core import video_download as D
    from core import video_meta
    from core import video_meta_import as I
    site = PAGE_SITES.get(key.split(":", 1)[0], "")
    if key.startswith("haven:"):                # (v5.44.138) its genres and tags by their numbers — as its catalogue
        why = haven_closed(key.split(":", 1)[1])                                                         # says them
        if why:
            raise WatchError(why)

    def fetch(link: str):                       # its pages (a series' episodes, its own): through the window's
        return I.page_from_url(link, progress=lambda _text: None, window_fetch=window)    # browser at a robot check
    say(f"Читаю страницу тайтла на {site}…")
    try:
        page = fetch(address)
    except I.MetaImportError as exc:
        raise WatchError(f"Страница тайтла на {site} не открылась: {exc}") from exc
    title = Title(key=key, alias="", address=page.url or address, anime={}, found=None, root=root, site=site,
                  meta=video_meta.parse_page(page.html, page.url or address))
    why = G.title(**_said(title))                # before its files are asked
    if why:
        raise WatchError(why)
    say("Ищу серии на странице…")
    title.found = D.look(page, root, say=say, stop_check=lambda: False, fetch=fetch)
    about = title.found.about
    if about is not None and about is not page:  # an episode's page: the series' page describes the series
        title.meta = video_meta.parse_page(about.html, about.url or address)
        why = G.title(**_said(title))
        if why:
            raise WatchError(why)
    if not title.found.files and not title.found.frame_site:
        _embeds(title.found, say)               # other sites' players in its frames, as «Скачать» takes them
    title.dubs = _page_dubs(title.found, root)
    if not title.dubs:
        why = next((f.blocked for f in title.found.files if f.blocked), "")
        raise WatchError("На странице тайтла нет серий, которые программа может показать" + (f": {why}" if why else
                         " — ни файлов, ни открытых потоков, ни плееров, которые их отдают"))
    return title


def haven_closed(slug: str, net=None) -> str:
    """(v5.44.138) The gate's words about a title of HentaiHaven by what its catalogue says of it (its genres and tags
    by their numbers — ``catalog.haven_said``; the page «Смотреть» opens names its genres only); "" — nothing against
    it, or not known (no such slug, no answer)."""
    from core import catalog as C, manhwa_sources as S
    said = C.haven_said(net if net is not None else S.Net(), slug)
    return G.title("haven", **said) if said else ""


def page_closed(address: str, html: str, url: str = "") -> str:
    """(v5.44.139) The gate's words about the title of a page of a site of hentai video (:data:`PAGE_SITES`) —
    «Скачать с сайта» asks before it looks for the files, as «Смотреть» does (:func:`_page_title`): its site, its names
    and genres as the page tells them, HentaiHaven's — as its catalogue says too; "" — nothing against it (a page of
    another site too)."""
    key = key_of(url or address) or key_of(address)
    if not is_page(key):
        return ""
    from core import video_meta
    site, ident = key.split(":", 1)
    meta = video_meta.parse_page(str(html or ""), url or address)
    names = [meta.get("title"), *(meta.get("alt_titles") or [])]
    why = G.title(site, names=[str(x) for x in names if x], genres=[str(g) for g in meta.get("genres") or [] if g])
    return why or (haven_closed(ident) if site == "haven" else "")


def _embeds(found, say) -> bool:
    """The episodes of a page through other sites' players in its frames (``video_embeds.fill``, as «Скачать»)."""
    try:
        from core import video_embeds
    except ImportError:                         # the public copy: the players' code is not in it
        return False
    try:
        return bool(video_embeds.fill(found, say=say, stop=lambda: False))
    except Exception as exc:  # noqa: BLE001 - a player of another site that breaks the reading: the page as it was
        _LOG.warning("watch: other sites' players not read: %s: %s", type(exc).__name__, exc)
        return False


def _look(key: str, address: str, settings: dict, root: Path, looking: _Looking, window=None) -> None:
    from core import video_dubs as Y

    def say(text) -> None:
        looking.said = str(text or "")[:300]
    site = site_of(address) or SITE_NAME
    try:
        if is_page(key):
            title = _page_title(key, address, root, say, window)
        else:
            title = _yummy_title(key, address, settings, root, say)
        with _LOCK:
            _TITLES[key] = title
            _TITLES.move_to_end(key)
            while len(_TITLES) > TITLES_KEPT:
                _TITLES.popitem(last=False)
            if _LOOKING.get(key) is looking:
                _LOOKING.pop(key, None)
    except (Y.DubError, WatchError) as exc:
        looking.error = str(exc)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:   # an answer of a shape not known
        _LOG.warning("watch: %s answered oddly: %r", site, exc)
        looking.error = (f"Страница {site} непонятная — возможно, сайт её изменил" if is_page(key) else
                         f"{site} прислал непонятный ответ — возможно, его API изменился")
    except Exception as exc:  # noqa: BLE001 - the network, the disk: said, the page asks again
        _LOG.warning("watch: the title %s not looked up: %s: %s", key, type(exc).__name__, exc)
        looking.error = f"Тайтл не открылся: {exc}"
    finally:
        looking.at = time.time()
        looking.done.set()


# ── what the page shows of a title ──────────────────────────────────────────────────────────────────────────────────
def _score(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(number, 2) if 0 < number <= 10 else 0.0


def ratings_of(anime: dict, address: str) -> list:
    """[{"name", "value", "votes", "url"}] — YummyAnime's own people's first (the big one on the page)."""
    from core import video_dubs as Y
    rating = _dict(anime.get("rating"))
    out = []
    own = _score(rating.get("average"))
    if own:
        votes = rating.get("counters")
        out.append({"name": SITE_NAME, "value": own, "url": address,
                    "votes": votes if isinstance(votes, int) and not isinstance(votes, bool) and votes > 0 else 0})
    for name, url, value in Y.ratings_of(anime, address):
        if name == Y.NAME:
            continue
        score = _score(value)
        if score:
            out.append({"name": "MAL" if name == "MyAnimeList" else name, "value": score, "url": str(url or ""),
                        "votes": 0})
    return out


def facts_of(title: Title) -> list:
    """[(label, value)] of the page's table: status, type, year, age, studio, director, episodes."""
    anime = title.anime
    rows = []
    status = str(_dict(anime.get("anime_status")).get("title") or "").strip()
    if status:
        rows.append(("Статус", status[:1].upper() + status[1:]))
    kind = str(_dict(anime.get("type")).get("name") or "").strip()
    if kind:
        rows.append(("Тип", kind))
    year = anime.get("year")
    if isinstance(year, int) and not isinstance(year, bool) and year > 0:
        rows.append(("Год выхода", str(year)))
    age = _dict(anime.get("min_age"))
    said = str(age.get("title_long") or age.get("title") or "").strip()
    if said:
        rows.append(("Возраст", said))
    studios = _names(anime.get("studios"))
    if studios:
        rows.append(("Студия", ", ".join(studios[:4])))
    creators = _names(anime.get("creators"))
    if creators:
        rows.append(("Режиссёр", ", ".join(creators[:3])))
    episodes = _dict(anime.get("episodes"))
    count = episodes.get("count")
    aired = episodes.get("aired")
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        text = str(count)
        if isinstance(aired, int) and not isinstance(aired, bool) and 0 < aired < count:
            text = f"{aired} из {count}"
        rows.append(("Серий", text))
    return rows


def public(title: Title, progress: dict | None = None) -> dict:
    """What the page needs of the title: its names, ratings, facts, description, poster, the viewing order, the
    voice-overs with their episodes and players (and what of them is in the folder), where it stopped."""
    if title.page:
        return _page_public(title, progress)
    from core import catalog, video_dubs as Y
    title.dubs = _dubs_of(title.found, title.root)          # the folder now (a download may have run)
    anime = title.anime
    poster = _dict(anime.get("poster"))
    picture = Y.absolute(poster.get("fullsize") or poster.get("big") or poster.get("medium") or "")
    source = (getattr(title.found, "sources", None) or [{}])[0]
    order = []
    for item in source.get("order") or []:
        if not isinstance(item, dict):
            continue
        order.append({"title": item.get("title") or "", "kind": item.get("kind") or "", "type": item.get("type") or "",
                      "year": item.get("year"), "soon": bool(item.get("soon")), "here": bool(item.get("here")),
                      "address": item.get("address") or ""})
    dubs = []
    for dub in title.dubs:
        players = dub.players
        dubs.append({
            "name": dub.name, "short": short_of(dub.name), "kind": dub.kind,
            "players": [{"key": p, "name": Y.PLAYERS[p][0], "best": Y.PLAYERS[p][1],
                         "count": sum(1 for e in dub.episodes if p in e.links)} for p in players],
            "have": sum(1 for e in dub.episodes if e.have in ("whole", "other")),
            "episodes": [{"n": e.label, "have": e.have, "players": [p for p in players if p in e.links],
                          "group": e.group, "title": e.title,          # its file, as «Скачать» lists it
                          "skips": {p: s for p, s in e.skips.items() if s}} for e in dub.episodes]})
    folder = getattr(title.found, "folder", None)
    return {
        "key": title.key, "address": title.address, "site": SITE_NAME, "title": title.name,
        "alt": [str(o) for o in anime.get("other_titles") or [] if o and str(o) != title.name][:4],
        "poster": catalog.cover_id(picture, title.address) if picture else "",
        "ratings": ratings_of(anime, title.address),
        "facts": [[label, value] for label, value in facts_of(title)],
        "genres": _names(anime.get("genres"))[:12],
        "about": [p.strip() for p in re.split(r"\r?\n", str(anime.get("description") or "")) if p.strip()],
        "order": order if len(order) > 1 else [],
        "ongoing": bool(source.get("ongoing")),
        "dubs": dubs,
        "rest": [{"name": v.get("name") or "", "players": list(v.get("players") or [])}
                 for v in getattr(title.found, "voices", None) or [] if isinstance(v, dict)],
        "folder": folder.name if folder is not None and Path(folder).is_dir() else "",
        "hentai": title.hentai,
        "qualities": list(Y.QUALITIES),
        "progress": progress or {},
    }


# (v5.44.109) the lines of a page title's facts, as its page names them (``video_meta``'s keys)
PAGE_FACTS = (("Год выхода", "year"), ("Тип", "type"), ("Статус", "status"), ("Студия", "studio"),
              ("Серий", "episodes"), ("Длительность", "duration"), ("Цензура", "censorship"), ("Возраст", "age"))
_STARS = re.compile(r"""current-rating["'][^>]*?width:\s*(\d{1,3}(?:\.\d+)?)\s*%""", re.I)
_VOTES = re.compile(r"""data-vote-num-id=["']\d+["'][^>]*>\s*([\d\s]{1,12})<""", re.I)


def _text_of(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(x).strip() for x in value[:4] if str(x).strip())
    return str(value or "").strip()


def page_rating(html: str) -> tuple:
    """(v5.44.109) (score of 10, votes) of a DLE page's stars (``.current-rating`` — its width in %; Hentasis), (v5.44.139)
    else of its people's «Нравится» (HFShare: ``catalog.likes_of``), or (0.0, 0)."""
    stars = _STARS.search(str(html or ""))
    if not stars:
        from core.catalog import likes_of
        return likes_of(str(html or ""))
    value = round(float(stars.group(1)) / 10, 1)
    votes = _VOTES.search(str(html or ""))
    count = int(re.sub(r"\D", "", votes.group(1)) or 0) if votes else 0
    return (value, count) if 0 < value <= 10 else (0.0, 0)


def _page_poster(meta: dict, address: str) -> str:
    """The first picture the page names as its poster, made absolute (a site's address — never a saved file's)."""
    for link in meta.get("poster_urls") or []:
        full = urljoin(address, str(link or "").strip())
        if urlparse(full).scheme in ("http", "https"):
            return full
    return ""


def _page_public(title: Title, progress: dict | None) -> dict:
    """(v5.44.109) :func:`public` of a page title: the page's names, facts, genres, description, poster and stars;
    the voice-overs with their episodes (no players, no «Качество»: one file of an episode)."""
    from core import catalog
    title.dubs = _page_dubs(title.found, title.root)        # the folder now (a download may have run)
    meta = title.meta
    about = getattr(title.found, "about", None) or getattr(title.found, "page", None)
    value, votes = page_rating(getattr(about, "html", ""))
    picture = _page_poster(meta, title.address)
    rows = []
    for label, key in PAGE_FACTS:
        text = _text_of(meta.get(key))
        if text:
            rows.append([label, text[:120]])
    dubs = []
    for dub in title.dubs:
        dubs.append({
            "name": dub.name, "short": short_of(dub.name) if dub.name else "Файлы сайта", "kind": dub.kind,
            "plain": dub.name == PLAIN, "players": [],
            "have": sum(1 for e in dub.episodes if e.have in ("whole", "other")),
            "episodes": [{"n": e.label, "have": e.have, "players": [], "group": e.group, "title": e.title, "skips": {}}
                         for e in dub.episodes]})
    folder = getattr(title.found, "folder", None)
    return {
        "key": title.key, "address": title.address, "site": title.site, "title": title.name,
        "alt": [str(o) for o in meta.get("alt_titles") or [] if o and str(o) != title.name][:4],
        "poster": catalog.cover_id(picture, title.address) if picture else "",
        "ratings": [{"name": title.site, "value": value, "url": title.address, "votes": votes}] if value else [],
        "facts": rows,
        "genres": [str(g) for g in meta.get("genres") or []][:12],
        "about": [p.strip() for p in re.split(r"\r?\n", str(meta.get("description") or "")) if p.strip()],
        "order": [], "ongoing": False, "dubs": dubs, "rest": [],
        "folder": folder.name if folder is not None and Path(folder).is_dir() else "",
        "hentai": title.hentai,
        "qualities": [],
        "progress": progress or {},
    }


# ── a stream of an episode ─────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Stream:
    token: str
    key: str                      # the title's
    dub: str
    label: str                    # the episode's
    group: str
    player: str
    frame: str                    # the player's frame of the episode
    cap: int = 0                  # «Качество»: not above this height (0 — the best)
    resolver: object = None       # the title's ``video_dubs.Resolver`` (CVH's lists, AniLiberty's releases)
    skips: dict = field(default_factory=dict)
    kind: str = ""                # "hls" — a stream, "mp4" — a file
    quality: str = ""
    qualities: list = field(default_factory=list)
    lines: list = field(default_factory=list)     # the stream's list as the program gives it
    pieces: list = field(default_factory=list)    # [(address, start, length)] — the parts in their order
    init: tuple | None = None                     # the header of MP4 fragments
    fragments: bool = False                       # the parts are MP4 fragments (else MPEG-TS)
    # (v5.44.138) a stream whose sound is a stream of its own (HentaiHaven): ``lines`` — the program's master list, the
    # picture's list — ``video_lines`` (``video.m3u8``), the sound's — ``audio_lines`` (``audio.m3u8``: ``a<n>.<ext>``,
    # its header ``ainit.mp4``)
    video_lines: list = field(default_factory=list)
    audio_lines: list = field(default_factory=list)
    audio_pieces: list = field(default_factory=list)
    audio_init: tuple | None = None
    audio_ext: str = ""
    url: str = ""                 # the file (CVH)
    referer: str = ""
    session: object = None
    length: float = 0.0
    used: float = field(default_factory=time.time)
    asked: float = field(default_factory=time.time)   # when its addresses were asked of the player
    generation: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    file: int = -1                # (v5.44.109) a page title: its file in the list «Скачать» found
    site: str = ""                # …and the site's name (the «player»)
    asks: int = 0                 # …how many times its address was taken (the next quality of the file after the first)

    @property
    def address(self) -> str:
        """The program's own address of what the player plays."""
        return f"/watch/s/{self.token}/" + ("video.mp4" if self.kind == "mp4" else "index.m3u8")

    @property
    def player_name(self) -> str:
        if self.player == PAGE_PLAYER:
            return self.site or "Сайт"
        from core import video_dubs as Y
        return Y.PLAYERS.get(self.player, (self.player,))[0]


_STREAMS: "OrderedDict[str, Stream]" = OrderedDict()
_STREAMS_LOCK = threading.Lock()


def open_stream(title: Title, dub_name: str, label: str, *, player: str = "", quality: str = "") -> Stream:
    """The episode's video asked of its players — the one chosen first, then the others in «Скачать»'s order (the
    one that gives the most within the quality first) — and kept under a token."""
    from core import video_dubs as Y
    from core import video_hls as H
    dub = title.dub(dub_name)
    if dub is None:
        raise WatchError("У тайтла нет такой озвучки — откройте страницу заново")
    episode = dub.episode(label)
    if episode is None:
        raise WatchError(f"Серии {label} в озвучке «{short_of(dub.name) or title.site}» нет")
    if title.page:
        return _open_page(title, dub, episode)
    if quality and quality not in Y.QUALITIES:
        raise WatchError("Непонятное качество")
    cap = Y.cap_of(quality)
    players = Y.order(list(episode.links), cap)
    if player in episode.links:
        players = [player] + [p for p in players if p != player]
    failures = []
    for name in players:
        stream = Stream(token=secrets.token_urlsafe(18), key=title.key, dub=dub.name, label=episode.label,
                        group=episode.group, player=name, frame=episode.links[name], cap=cap,
                        resolver=title.found.resolve, skips=dict(episode.skips.get(name) or {}))
        word = Y.PLAYERS[name][0]
        try:
            _resolve(stream)
        except (Y.DubError, H.StreamError, WatchError) as exc:
            said = str(exc)
            failures.append(said if said.startswith(word) else f"{word}: {said}")
            _LOG.info("watch %s / %s / %s: %s", title.key, dub.name, episode.label, exc)
            continue
        except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            failures.append(f"{word}: непонятный ответ плеера — возможно, он изменился")
            _LOG.warning("watch %s / %s / %s: %s answered oddly: %r", title.key, dub.name, episode.label, word, exc)
            continue
        _keep(stream)
        return stream
    raise WatchError("Серия не открылась: " + ("; ".join(failures) or "плееров у неё нет"))


def _open_page(title: Title, dub: Dub, episode: Episode) -> Stream:
    """(v5.44.109) An episode of a page title: its file (or open stream) as «Скачать» takes it, under a token."""
    from core import video_download as D
    from core import video_hls as H
    stream = Stream(token=secrets.token_urlsafe(18), key=title.key, dub=dub.name, label=episode.label,
                    group=episode.group, player=PAGE_PLAYER, frame=title.address, resolver=title.found,
                    file=episode.file, site=title.site)
    try:
        _resolve(stream)
    except (H.StreamError, D.DownloadError, WatchError) as exc:
        _LOG.info("watch %s / %s / %s: %s", title.key, dub.name, episode.label, exc)
        raise WatchError(f"Серия не открылась: {exc}") from exc
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        _LOG.warning("watch %s / %s / %s: answered oddly: %r", title.key, dub.name, episode.label, exc)
        raise WatchError(f"Серия не открылась: {title.site} ответил непонятно — возможно, сайт изменился") from exc
    _keep(stream)
    return stream


def _session(frame: str):
    from core import video_dubs as Y
    from core.video_meta_import import _session as make
    return Y.direct(make(frame))


def _proxied(url: str) -> bool:
    import requests
    try:
        return bool(requests.utils.get_environ_proxies(url))
    except (OSError, ValueError):
        return False


def _resolve(stream: Stream) -> None:
    """The episode's video in its player now: a CVH file, or a stream of Kodik or AniLiberty with its parts;
    (v5.44.109) a page title's file or stream."""
    from core import video_dubs as Y
    if stream.player == PAGE_PLAYER:
        _resolve_page(stream)
        return
    stream.session = _session(stream.frame)
    if stream.player == "cvh":
        every = Y.cvh_links(stream.session, stream.frame, stream.resolver.playlists)
        every = stream.resolver.real(stream.group, every,
                                     stream.session if not stream.cap or stream.cap > 1080 else None)
        chosen = Y.within(every, stream.cap)
        stream.kind, stream.url, stream.quality = "mp4", chosen[0][1], chosen[0][0]
        stream.qualities = [q for q, _ in every]
        stream.referer = Y.SITE + "/"
        stream.asked = time.time()
        return
    if stream.player == "aniliberty":
        every = stream.resolver.aniliberty_links(stream.frame)
        referer = "{0.scheme}://{0.netloc}/".format(urlparse(stream.frame))
    elif stream.player == "kodik":
        every = Y.kodik_links(stream.session, stream.frame)
        referer = stream.frame
    else:
        raise WatchError(f"плеер {stream.player} программа не понимает")
    chosen = Y.within(every, stream.cap)
    plan = _plan(stream, chosen, referer)
    if stream.player == "aniliberty" and plan.media.segments:
        Y.stream_way(stream.session, plan.media.segments[0].url, "AniLiberty")
    _take_plan(stream, plan, referer, chosen[0][0], [q for q, _ in every])


def _take_plan(stream: Stream, plan, referer: str, quality: str, qualities: list) -> None:
    """The stream's list (rewritten to the program's addresses) and its parts, of a plan that opens; (v5.44.138) its
    sound, when it is a stream of its own — its list and parts too, under a master list of the program's."""
    media = plan.media
    text = _text(stream.session, media.url, referer)
    lines = rewrite(text, media)
    sound = plan.audio
    if sound is not None:
        ext = _sound_ext(sound.media)
        stream.audio_lines = rewrite(_text(stream.session, sound.media.url, referer), sound.media, prefix="a", ext=ext)
        stream.audio_pieces = [(s.url, s.start, s.length) for s in sound.media.segments]
        stream.audio_init = (sound.media.init.url, sound.media.init.start, sound.media.init.length) \
            if sound.media.init is not None else None
        stream.audio_ext = ext
        stream.video_lines = lines
        stream.lines = master(plan)
    else:
        stream.audio_lines, stream.audio_pieces, stream.audio_init, stream.audio_ext = [], [], None, ""
        stream.video_lines = []
        stream.lines = lines
    stream.pieces = [(s.url, s.start, s.length) for s in media.segments]
    stream.init = (media.init.url, media.init.start, media.init.length) if media.init is not None else None
    stream.fragments = media.init is not None
    stream.kind, stream.referer = "hls", referer
    stream.quality = plan.quality or quality
    stream.qualities = list(qualities)
    stream.length = round(float(media.duration), 2)
    stream.asked = time.time()


def _usable(plan):
    """A stream's plan the program can play, else :class:`WatchError` why not (encrypted…); (v5.44.138) its sound a
    stream of its own — played with it (:func:`master`), unless that one cannot be."""
    if plan.blocked:
        raise WatchError(plan.blocked)
    if plan.audio is not None and plan.audio.blocked:
        raise WatchError(f"звук потока: {plan.audio.blocked}")
    return plan


def _sound_ext(media) -> str:
    """The program's word for the parts of a stream's sound: MP4 fragments («m4s»), plain AAC («aac»), MPEG-TS."""
    if media.init is not None:
        return "m4s"
    first = urlparse(media.segments[0].url).path.lower() if media.segments else ""
    return "aac" if first.endswith(".aac") else "ts"


def master(plan) -> list:
    """(v5.44.138) The program's master list of a stream whose sound is apart: the sound (``audio.m3u8``) — the one
    group, the picture (``video.m3u8``) with the site's bit rate, size and codecs."""
    variant = plan.variant
    attrs = [f"BANDWIDTH={int(getattr(variant, 'bandwidth', 0) or 0) or 1000000}"]
    if variant is not None and variant.width and variant.height:
        attrs.append(f"RESOLUTION={variant.width}x{variant.height}")
    if variant is not None and variant.codecs:
        attrs.append(f'CODECS="{variant.codecs}"')
    attrs.append('AUDIO="lb-sound"')
    name = re.sub(r'["\r\n]', "", str(plan.audio_name or "Звук"))[:60] or "Звук"
    return ["#EXTM3U", "#EXT-X-VERSION:7",
            f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="lb-sound",NAME="{name}",DEFAULT=YES,AUTOSELECT=YES,URI="audio.m3u8"',
            "#EXT-X-STREAM-INF:" + ",".join(attrs), "video.m3u8"]


def _resolve_page(stream: Stream) -> None:
    """(v5.44.109) A page title's episode now, as «Скачать» takes it: the page's address of its file — the player's
    ranges passed on (:func:`file_part`), the file's next quality when it is asked again (refused); an open stream —
    its list and parts; another site's player in the page's frame — its video asked of it now (``Found.resolve``)."""
    from core import video_download as D
    from core.video_meta_import import _session as session_of
    found = stream.resolver
    remote = found.files[stream.file]
    stream.session = session_of(remote.url or found.page.url)       # the site's cookies (``Сайты и ключи``), as «Скачать»
    plan = None
    if found.resolve is not None:                # other sites' players: their signed addresses, asked right now
        plan = found.resolve(remote, session=stream.session, quality="", say=lambda _text: None, stop=lambda: False)
    referer = remote.page or found.page.url
    every = [(str(remote.quality or ""), remote.url)] + [(str(q), str(u)) for q, u in remote.alts or [] if u]
    taken = min(stream.asks, len(every) - 1)
    stream.asks += 1
    if remote.stream == "hls":
        if plan is None and not taken:
            plan = found.plans.get(remote.url)  # read already by «Скачать»'s look
        if plan is None:                        # as «Скачать» reads it: a master list, or qualities of their own
            from core import video_hls as H
            rest = every[taken:]
            plan = H.plan(stream.session, rest[0][1], referer) if len(rest) == 1 else \
                H.plan_of(stream.session, rest, referer)
        _take_plan(stream, _usable(plan), referer, every[taken][0], [q for q, _ in every if q])
        return
    if remote.stream:
        raise WatchError("такой поток программа не показывает")
    stream.kind, stream.url, stream.referer = "mp4", every[taken][1], referer
    stream.quality, stream.qualities = every[taken][0], [q for q, _ in every if q]
    stream.length = float(remote.length or 0)
    stream.asked = time.time()
    if not stream.url:
        raise D.DownloadError("у серии нет адреса файла")


def _plan(stream: Stream, chosen: list, referer: str):
    """The stream's plan (``video_hls.plan_of``: the best quality within «Качество» that opens) — straight, then
    through the system's proxy when straight does not get through (``video_dubs.direct``: the owner's VPN)."""
    from core import video_hls as H
    try:
        plan = H.plan_of(stream.session, chosen, referer)
    except H.StreamError:
        if getattr(stream.session, "lb_route", "") != "direct" or not _proxied(chosen[0][1]):
            raise
        stream.session.trust_env = True
        stream.session.lb_route = "system"
        plan = H.plan_of(stream.session, chosen, referer)
    return _usable(plan)


def _text(session, url: str, referer: str) -> str:
    import requests
    from core.redaction import sanitize_text
    try:
        with session.get(url, timeout=TIMEOUT, stream=True, headers={"Referer": referer, "Accept": "*/*"}) as answer:
            if answer.status_code >= 400:
                raise WatchError(f"сайт ответил {answer.status_code} на список частей потока")
            body = bytearray()
            for chunk in answer.iter_content(64 * 1024):
                body += chunk
                if len(body) > PLAYLIST_MAX:
                    raise WatchError("список частей потока слишком большой")
    except requests.RequestException as exc:
        raise WatchError(f"список частей потока не получен: {sanitize_text(str(exc))[:200]}") from exc
    return bytes(body).decode("utf-8", "replace")


def rewrite(text: str, media, *, prefix: str = "", ext: str = "") -> list:
    """The stream's list with the program's addresses of its parts (``<n>.ts`` or ``<n>.m4s``, the header —
    ``init.mp4``): every tag of the site's list stays (its lengths, joins of pieces, its end) but the parts' byte
    ranges (the program asks them itself) and keys (a stream with one is never opened: ``video_hls``).  (v5.44.138)
    ``prefix`` — of the sound's list (``a<n>.<ext>``, ``ainit.mp4``)."""
    lines = [line.strip() for line in str(text or "").lstrip("\ufeff").splitlines() if line.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise WatchError("сайт прислал не список частей потока")
    ext = ext or ("m4s" if media.init is not None else "ts")
    out, n = [], 0
    for line in lines[1:]:
        if line.startswith(("#EXT-X-BYTERANGE", "#EXT-X-KEY", "#EXT-X-VERSION", "#EXT-X-SESSION-KEY")):
            continue
        if line.startswith("#EXT-X-MAP:"):
            out.append(f'#EXT-X-MAP:URI="{prefix}init.mp4"')
        elif line.startswith("#"):
            out.append(line)
        else:
            out.append(f"{prefix}{n}.{ext}")
            n += 1
    if n != len(media.segments):
        raise WatchError("список частей потока непонятный")
    return ["#EXTM3U", f"#EXT-X-VERSION:{7 if media.init is not None else 3}"] + out


def _keep(stream: Stream) -> None:
    now = time.time()
    with _STREAMS_LOCK:
        for token in [t for t, s in _STREAMS.items() if now - s.used > STREAM_IDLE]:
            _STREAMS.pop(token, None)
        _STREAMS[stream.token] = stream
        while len(_STREAMS) > STREAMS_KEPT:
            _STREAMS.popitem(last=False)


def known(token: str) -> bool:
    """A stream the program gave (the window's own player asks its parts without the page's cookie)."""
    if not _TOKEN.fullmatch(str(token or "")):
        return False
    with _STREAMS_LOCK:
        return str(token) in _STREAMS


def stream_of(token: str) -> Stream:
    with _STREAMS_LOCK:
        stream = _STREAMS.get(str(token or "")) if _TOKEN.fullmatch(str(token or "")) else None
        if stream is not None:
            stream.used = time.time()
            _STREAMS.move_to_end(stream.token)
    if stream is None:
        raise Gone("Этот просмотр уже закончился — нажмите серию ещё раз")
    return stream


def _ask_again(stream: Stream, seen: int) -> bool:
    """The stream's addresses asked of its player again (a part refused: they are signed and live for hours) — True
    when there are new ones to try (asked now, or by another request meanwhile)."""
    with stream.lock:
        if stream.generation != seen:
            return True
        if time.time() - stream.asked < ASK_AGAIN_AFTER:
            return False
        try:
            _resolve(stream)
        except Exception as exc:  # noqa: BLE001 - the part says it did not come
            _LOG.info("watch %s: the addresses not asked again: %s", stream.key, exc)
            stream.asked = time.time()
            return False
        stream.generation += 1
        _LOG.info("watch %s / %s / %s: the addresses asked again (%s)", stream.key, stream.dub, stream.label,
                  stream.player_name)
        return True


def playlist(token: str, which: str = "") -> str:
    """The stream's list as the program gives it; (v5.44.138) ``which`` — "video" or "audio": the picture's or the
    sound's own list of a stream whose sound is apart (else :class:`Gone`)."""
    stream = stream_of(token)
    if stream.kind != "hls":
        raise Gone("Это не поток")
    with stream.lock:
        lines = stream.video_lines if which == "video" else stream.audio_lines if which == "audio" else stream.lines
        if not lines:
            raise Gone("Такого списка у потока нет")
        return "\n".join(lines) + "\n"


SOUND_TYPES = {"m4s": "audio/mp4", "aac": "audio/aac", "ts": "video/mp2t"}


def piece(token: str, n: int, sound: bool = False) -> tuple:
    """(bytes, type) of the stream's part ``n`` (-1 — the header of its fragments), asked of the site now;
    (v5.44.138) ``sound`` — of its sound's own stream."""
    stream = stream_of(token)
    if stream.kind != "hls":
        raise Gone("Это не поток")
    last = ""
    for _attempt in range(PIECE_TRIES):
        with stream.lock:
            seen = stream.generation
            pieces, header = (stream.audio_pieces, stream.audio_init) if sound else (stream.pieces, stream.init)
            if n < 0:
                where = header
            else:
                where = pieces[n] if n < len(pieces) else None
            ext = stream.audio_ext
        if where is None:
            raise Gone("Такой части у потока нет")
        status, body, why = _get(stream, *where)
        if body is not None:
            if sound:
                return body, SOUND_TYPES.get(ext, "audio/mp4")
            kind = "video/mp4" if stream.fragments else "video/mp2t"
            return body, kind
        last = why
        if status in (401, 403, 404, 410):
            if not _ask_again(stream, seen):
                break
    raise WatchError(f"Часть потока не пришла: {last or 'сайт не отдал её'}")


def _get(stream: Stream, url: str, start: int, length: int) -> tuple:
    """(status, bytes or None, why not) of one part."""
    import requests
    from core.redaction import sanitize_text
    headers = {"Referer": stream.referer, "Accept": "*/*", "Accept-Encoding": "identity"}
    if start is not None and start >= 0 and length:
        headers["Range"] = f"bytes={start}-{start + length - 1}"
    try:
        with stream.session.get(url, timeout=TIMEOUT, stream=True, headers=headers) as answer:
            if answer.status_code >= 400:
                return answer.status_code, None, f"сайт ответил {answer.status_code}"
            if "text/html" in str(answer.headers.get("Content-Type") or "").lower():
                return 0, None, "вместо части видео сайт прислал страницу"
            body = bytearray()
            for chunk in answer.iter_content(CHUNK):
                body += chunk
                if len(body) > PIECE_MAX:
                    return 0, None, "часть слишком большая"
            expected = int(answer.headers.get("Content-Length") or 0)
            if expected and len(body) < expected:
                return 0, None, "часть пришла не целиком"
            if not body:
                return 0, None, "часть пришла пустой"
            return answer.status_code, bytes(body), ""
    except (requests.RequestException, ValueError) as exc:
        return 0, None, sanitize_text(str(exc))[:160]


class _Body:
    """The file's bytes as they come from the site (closed with the answer when the player goes)."""

    def __init__(self, answer):
        self.answer = answer
        self.chunks = answer.iter_content(CHUNK)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.chunks)

    def close(self) -> None:
        self.answer.close()


def file_part(token: str, wanted: str = "") -> tuple:
    """(status, headers, bytes as they come) of the episode's file (CVH; a page title's) — the player's range passed
    on."""
    import requests
    from core.redaction import sanitize_text
    stream = stream_of(token)
    if stream.kind != "mp4":
        raise Gone("Это не файл")
    wanted = str(wanted or "").strip()
    wanted = wanted if _RANGE.fullmatch(wanted) and wanted != "bytes=-" else ""
    for attempt in (1, 2):
        with stream.lock:
            seen, url = stream.generation, stream.url
        headers = {"Referer": stream.referer, "Accept": "*/*", "Accept-Encoding": "identity"}
        if wanted:
            headers["Range"] = wanted
        try:
            answer = stream.session.get(url, timeout=TIMEOUT, stream=True, headers=headers)
        except requests.RequestException as exc:
            raise WatchError(f"Файл серии не открылся: {sanitize_text(str(exc))[:200]}") from exc
        if answer.status_code in (401, 403, 404, 410) and attempt == 1 and _ask_again(stream, seen):
            answer.close()
            continue
        if answer.status_code == 416:
            answer.close()
            return 416, {"Content-Range": str(answer.headers.get("Content-Range") or "")}, iter(())
        if answer.status_code >= 400:
            answer.close()
            raise WatchError(f"Файл серии не открылся: сайт ответил {answer.status_code}")
        if "text/html" in str(answer.headers.get("Content-Type") or "").lower():   # a robot check, an error page
            answer.close()
            raise WatchError("Файл серии не открылся: вместо видео сайт прислал страницу")
        out = {"Accept-Ranges": "bytes"}
        for name in ("Content-Length", "Content-Range"):
            if answer.headers.get(name):
                out[name] = str(answer.headers[name])
        return (206 if answer.status_code == 206 else 200), out, _Body(answer)
    raise WatchError("Файл серии не открылся")


def stream_public(stream: Stream, progress: dict | None = None) -> dict:
    """What the page needs of a stream opened: where the player takes it, what it is, where to start."""
    place = ((progress or {}).get("places") or {}).get(stream.label)
    start = float(place[0]) if isinstance(place, list) and place and str(stream.label) not in \
        ((progress or {}).get("watched") or []) else 0.0
    return {"token": stream.token, "src": stream.address, "kind": stream.kind, "player": stream.player,
            "player_name": stream.player_name, "quality": stream.quality, "qualities": stream.qualities,
            "skips": stream.skips, "length": stream.length, "dub": stream.dub, "ep": stream.label,
            "from": max(0.0, start)}


# ── where it stopped ───────────────────────────────────────────────────────────────────────────────────────────────
# (v5.44.121) the one memory of the tab «Видео» (``core/video_library.Progress``, ``video_progress.json``): an episode
# watched here and the same episode of the folder are one; ``video_watch.json`` of v5.44.106–5.44.120 is read into it once
from core.video_library import PROGRESS, Progress  # noqa: E402,F401  (its API is «Смотреть»'s own of before)
