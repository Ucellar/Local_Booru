"""(v5.44.121) One page of a series — its episodes in the folder and on the site, one list — and one player of an
episode: the owner's mockup D «Видео: одна страница сериала и один плеер» (7.11.2026: «норм мне нравится»).

The same page for a series of the video folder (``/videos/s/<id>``) and for a title of a site the program shows online
(``/videos/watch?url=`` — YummyAnime, Hentasis: «Смотреть»; a title that has its folder here is that folder's series):

* «Откуда серии»: «Папка» — the series' episodes in the folder; its sub-folders «Озвучка …» / «Субтитры …» are variants
  of them (``video_library.Unit``), not episodes of their own; «Онлайн» — «Смотреть онлайн»: the episodes the folder
  has not come from the site (its address — «Описание с сайта», ``info.json`` "source_url"), without downloading them,
  in the same player; «Озвучка» — the series' voice-over (one choice: a track of a file, a sub-folder, a voice-over of
  the site — ``video_merge.dub_key`` makes «Озвучка X», «X» and «X» of a site one);
* the episodes: «в папке» / «с сайта, онлайн» / «досмотрена» / «Вы остановились здесь · 12:34 из 23:40» / «новая»,
  each with its variants (▾) and «Смотреть» / «Продолжить»;
* the player under them: an episode of the folder always plays its file from the disk (in the program's window — its
  own player, ``gallery.native_video_box``; in a browser and on the phone — ``<video>`` with the bar of the page),
  an episode of the site — its stream through the program (``core/video_watch``); the previous and next episode, «Серия 5 из 12», the opening
  and the ending marked on the bar, «Пропустить опенинг», the end of an episode — «Серия 8 закончилась → Следующая:
  серия 9» (after the folder's last one the site's next one).

What was watched — the one memory (``video_library.PROGRESS``): an episode watched online is watched in the folder too.
The page gets everything from :func:`model` (inline, then ``/api/series`` while the site is read) and draws it
(``static/series.js``, ``series.css``); the phone — the same page and player in its frame (``/m/videos/s/<id>``,
``/m/videos/watch``; an episode — ``?ep=<number>&play=1``).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode

from core import video_library as V
from webui.html import esc
from webui.server import Request, Response
from webui import icons

_LOG = logging.getLogger("local_booru.webui")

NEW_FOR = 3 * 86400            # an episode that came this lately and is not watched yet — «новая»
SITE_WAIT = 2.5                # seconds ``/api/series?wait=1`` waits for the site before «… отвечает»
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
          "декабря")
KINDS = (("озвучка", "озвучка"), ("озучка", "озвучка"), ("дубляж", "дубляж"), ("субтитры", "субтитры"),
         ("оригинал", "оригинал"))


def register(router) -> None:
    router.add("GET", r"/api/series", model_json)
    router.add("POST", r"/api/series/choose", choose_json)
    router.add("POST", r"/api/series/progress", progress_json)
    router.add("POST", r"/api/series/play", play_json)


# ── which series ─────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Ref:
    series: object               # video_library.Series of the folder, or None (a title of a site only)
    url: str                     # the title's page on its site ("" — none the program shows online)
    key: str                     # its memory's key (``video_library.series_key``)
    keys: list                   # …and the keys read with it (the folder's own)


def _root(req: Request) -> str:
    return V.video_root(req.settings)


def resolve(req: Request, series_id: str = "", url: str = "") -> Ref | None:
    """The series of a folder (``series_id``) with its site, or of a site (``url``) with its folder — the folder whose
    description came from that title, or the one «Скачать» puts it in (the title found already)."""
    from core import video_watch as W
    root = _root(req)
    here = Path(root).is_dir()
    if series_id:
        series = V.LIBRARY.get(root, series_id) if here else None
        if series is None:
            return None
        source = str(series.info.get("source_url") or "").strip()
        keys = V.memory_keys(series)
        if not (source and W.is_watchable(source)) and not keys[0].startswith("folder:"):
            source = str(V.PROGRESS.of(keys[0]).get("address") or "")    # a folder linked to its title (link)
        return Ref(series, source if source and W.is_watchable(source) else "", keys[0], keys)
    url = str(url or "").strip()[:2000]
    key = W.key_of(url)
    if not key:
        return None
    series = V.LIBRARY.by_key(root, key) if here else None
    if series is None and here:
        title = W.kept(url)
        folder = getattr(getattr(title, "found", None), "folder", None)
        if folder is not None and Path(folder).is_dir():
            series = next((s for s in V.LIBRARY.items(root) if not s.single and Path(s.path) == Path(folder)), None)
            _link(key, series, url)
    keys = [key] + ([f"folder:{series.id}"] if series is not None else [])
    return Ref(series, url, key, keys)


def _link(key: str, series, url: str) -> None:
    """The folder «Скачать» put this title in, its description not from the title's page: one memory from now on —
    its own page is the title's too (``video_library.Progress.link``)."""
    if series is not None and V.series_key(series) != key and not str(series.info.get("source_url") or "").strip():
        V.PROGRESS.link(key, series.id, url)


def _ref_of(req: Request, body: dict | None = None) -> Ref | None:
    body = body if body is not None else {}
    series_id = str(body.get("series") or req.arg("series") or "").strip()
    if series_id and not re.fullmatch(r"[0-9a-f]{12}", series_id):
        return None
    return resolve(req, series_id, str(body.get("url") or req.arg("url") or ""))


def online_on(ref: Ref, item: dict) -> bool:
    """«Смотреть онлайн»: on unless switched off for the series (a series without such an address — never)."""
    return bool(ref.url) and item.get("online") is not False


def site_title(req: Request, ref: Ref, wait: float = 0.0):
    """(the title of the series' site, what is being done, why it did not come) — looked up in its own thread
    (``video_watch.look``); ``wait`` seconds waited for."""
    from core import video_watch as W
    from webui.pages.video_watch import _window
    try:
        return W.look(ref.url, req.settings, Path(_root(req)), wait=wait, window=_window(ref.url))
    except W.WatchError as exc:
        return None, "", str(exc)


def _hidden_title(title) -> str:
    """The mode's word about a title of a site: "" — shown; «sfw» / «nsfw» — hidden."""
    from webui import content_mode
    if title is None:
        return ""
    now = content_mode.mode()
    if title.hentai and not content_mode.shows(True):
        return now
    if not title.hentai and now == "nsfw":
        return now
    return ""


# ── the episodes ─────────────────────────────────────────────────────────────────────────────────────────────────
_TRACKS: dict = {}              # (path, size, time) -> the titles of a file's sound tracks
_TRACKS_LOCK = threading.Lock()


def tracks_of(episode: V.Episode) -> list:
    """The titles of the sound tracks of an episode's file (read once a file: ``video_merge.tracks_in``)."""
    key = (episode.path, episode.size, episode.mtime)
    with _TRACKS_LOCK:
        if key in _TRACKS:
            return list(_TRACKS[key])
    titles: list = []
    try:
        from core import video_merge
        if video_merge.available() and Path(episode.path).is_file():
            titles = [str(t or "").strip() for t in video_merge.tracks_in(Path(episode.path))]
    except Exception as exc:  # noqa: BLE001 - a file PyAV does not read: one variant, the file itself
        _LOG.debug("tracks of %s not read: %s", episode.path, exc)
    with _TRACKS_LOCK:
        _TRACKS[key] = titles
        while len(_TRACKS) > 2000:
            _TRACKS.pop(next(iter(_TRACKS)))
    return list(titles)


def kind_word(name: str) -> str:
    """«озвучка», «субтитры», «дубляж», «оригинал» — by the first word of a voice-over's name ("" — not said)."""
    low = str(name or "").strip().casefold()
    return next((word for start, word in KINDS if low.startswith(start)), "")


def _key(name: str) -> str:
    from core.video_merge import dub_key
    return dub_key(name)


def disk_variants(episode: V.Episode, tracks: bool = True) -> list:
    """The variants a file of an episode gives: its sound tracks (a voice-over each — the MKV «Скачать» puts them
    together in), else the file itself — named by its sub-folder («Озвучка Лунный голос» — «Лунный голос»).
    ``tracks`` False — a browser of the computer: it plays a file's first track only (the program's window and the
    phone switch them), so the file is one variant there, as the page of before offered no choice in it."""
    from core import video_watch as W
    titles = tracks_of(episode)
    word = kind_word(episode.group)
    # «voice» — what the series keeps when it is chosen (its key is the variant's: ``dub_key`` keeps «Субтитры»)
    if len(titles) >= 2 and tracks:
        return [{"kind": "track", "name": t or f"Дорожка {n + 1}", "sub": "дорожка в файле серии", "file": episode.id,
                 "track": n, "key": _key(t) if t else "", "voice": t} for n, t in enumerate(titles)]
    if episode.group:
        name = W.short_of(episode.group) or episode.group
        return [{"kind": "file", "name": name, "sub": f"{word or 'вариант'} · папка «{episode.group}»",
                 "file": episode.id, "track": -1, "key": _key(episode.group), "voice": episode.group}]
    one = titles[0] if titles else ""
    return [{"kind": "file", "name": one or "Файл серии", "sub": "файл серии" if not one else "дорожка в файле серии",
             "file": episode.id, "track": -1, "key": _key(one) if one else "", "voice": one}]


def _date(stamp) -> str:
    try:
        moment = time.localtime(int(float(stamp)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    return f"{moment.tm_mday} {MONTHS[moment.tm_mon - 1]}"


def ranges(labels: list) -> str:
    """«1–8», «1, 3, 5–7» — episode numbers in a few words."""
    numbers = []
    for label in labels:
        try:
            value = float(label)
        except (TypeError, ValueError):
            return ", ".join(str(x) for x in labels[:6]) + ("…" if len(labels) > 6 else "")
        numbers.append(value)
    if not numbers:
        return ""
    numbers.sort()
    parts, start, prev = [], numbers[0], numbers[0]

    def said(a, b):
        return f"{a:g}" if a == b else f"{a:g}–{b:g}"
    for value in numbers[1:]:
        if value == prev + 1:
            prev = value
            continue
        parts.append(said(start, prev))
        start = prev = value
    parts.append(said(start, prev))
    return ", ".join(parts)


def _length_of(episode: V.Episode) -> float:
    from core import video_probe
    info = video_probe.probe(episode.path)
    return float(info.duration) if info is not None and info.duration > 0 else 0.0


@dataclass
class Built:
    """What the page needs of a series now: the model and what made it."""
    model: dict
    ref: Ref
    title: object = None         # video_watch.Title of its site, when online and come
    hidden: str = ""             # the mode hides the whole series («sfw»/«nsfw»; a title of a site only)


def build(req: Request, ref: Ref, *, wait: float = 0.0) -> Built:
    """The model of the series: its names and facts (the folder's description, else the site's), «Откуда серии», the
    episodes with their variants, where it stopped, «Продолжить: серия 5 с 12:34»."""
    from core import video_watch as W
    series = ref.series
    if series is not None:
        V.PROGRESS.adopt(series)
    item = V.PROGRESS.view(ref.keys)
    online = online_on(ref, item)
    title, said, error = (None, "", "")
    if online:
        title, said, error = site_title(req, ref, wait)
    hidden = _hidden_title(title)
    site_shown = title is not None and not hidden
    if series is None and title is not None and not hidden:
        folder = getattr(title.found, "folder", None)          # «Скачать» put it in a folder by now
        root = _root(req)
        if folder is not None and Path(folder).is_dir() and Path(root).is_dir():
            series = next((s for s in V.LIBRARY.items(root) if not s.single and Path(s.path) == Path(folder)), None)
            _link(ref.key, series, ref.url)
            if series is not None:
                ref.series = series
                ref.keys = [ref.key, f"folder:{series.id}"]
                V.PROGRESS.adopt(series)
                item = V.PROGRESS.view(ref.keys)
    public = W.public(title, item) if site_shown else None
    if site_shown:                        # what the shelf «Продолжить просмотр» shows of it (not «watched lately»)
        V.PROGRESS.note(ref.key, count=len(_labels_of(title)), address=title.address, site=title.site,
                        hentai=bool(title.hentai), cover=None if item.get("cover") else cover_of(title))
    dubs = []
    if site_shown:
        for d in public["dubs"]:
            dubs.append({"name": d["name"], "short": d["short"] if not d.get("plain") else public["site"],
                         "kind": d["kind"], "key": _key(d["name"]) if d["name"] else "", "plain": bool(d.get("plain")),
                         "players": d.get("players") or [], "have": d.get("have") or 0,
                         "eps": [e["n"] for e in d["episodes"]],
                         # «Скачать серию N» / «Скачать остальные N»: the files «Скачать» lists them by
                         "files": {e["n"]: [e.get("group") or "", e.get("title") or ""] for e in d["episodes"]}})
    # the tracks of a file — variants where a player can switch them: the program's window, the phone (prepared)
    from webui import content_mode
    entries = _entries(series, item, dubs, public, tracks=in_app(req) or content_mode.of_phone(req))
    voice = str(item.get("voice") or "")
    voice_key = _key(voice) if voice else ""
    default_dub = _default_dub(item, dubs, entries)
    for entry in entries:
        entry["chosen"] = pick(entry, voice_key, default_dub, dubs)
    single = bool(series is not None and series.single)
    resume = resume_target(entries, item, single)
    model = _model(req, ref, series, item, online, title, public, dubs, entries, voice, resume, said=said,
                   error=error, hidden=hidden)
    model["dub"] = dubs[default_dub]["name"] if dubs and default_dub >= 0 else ""
    model["voice_key"] = voice_key                     # the voice-over chosen, as the variants' keys say it
    return Built(model=model, ref=ref, title=title if site_shown else None, hidden=hidden if series is None else "")


def _entries(series, item: dict, dubs: list, public: dict | None, *, tracks: bool = True) -> list:
    watched = [str(x) for x in item.get("watched") or []]
    places = item.get("places") or {}
    stamps = item.get("seen") or {}
    last = str(item.get("last") or "")
    now = time.time()
    out: list = []
    own: dict = {}
    if series is not None:
        for unit in series.units():
            disk = [v for f in unit.files for v in disk_variants(f, tracks)]
            entry = {"key": unit.key, "label": unit.label, "group": unit.group, "name": unit.name,
                     "title": next((f.site_title for f in unit.files if f.site_title and not
                                    re.fullmatch(r"(?:файл|серия)\s*\d+", f.site_title.strip(), re.I)), ""),
                     "note": next((f.note for f in unit.files if f.note), ""),
                     "disk": disk, "site": [], "length": next((x for x in (_length_of(f) for f in unit.files[:1]) if x), 0.0),
                     "fresh": any(now - f.mtime < NEW_FOR for f in unit.files)}
            out.append(entry)
            if not unit.group:
                own[unit.label] = entry
    if dubs:
        labels: dict = {}
        for n, d in enumerate(dubs):
            for label in d["eps"]:
                labels.setdefault(label, []).append(n)
        extra = []
        for label, which in labels.items():
            entry = own.get(label)
            if entry is None:
                entry = {"key": label, "label": label, "group": "", "name": f"Серия {label}", "title": "", "note": "",
                         "disk": [], "site": [], "length": 0.0, "fresh": False}
                extra.append(entry)
                own[label] = entry
            entry["site"] = which
        if extra:
            mine = [e for e in out if not e["group"]]
            seasons = [e for e in out if e["group"]]
            out = sorted(mine + extra, key=lambda e: (_number(e["label"]), e["label"])) + seasons
    ongoing = bool((public or {}).get("ongoing"))
    site_only = [e for e in out if not e["disk"] and e["site"]]
    for entry in out:
        key = entry["key"]
        done = key in watched
        place = places.get(key) if not done else None
        entry["done"] = done
        entry["seen"] = _date(stamps.get(key)) if done and stamps.get(key) else ""
        entry["place"] = [float(place[0]), float(place[1])] if isinstance(place, list) and len(place) == 2 else None
        if entry["place"] and entry["place"][1] > 0:
            entry["length"] = entry["place"][1]
        entry["here"] = key == last and not done and entry["place"] is not None
        entry["new"] = not done and entry["place"] is None and (
            (entry["disk"] and entry["fresh"]) or (ongoing and site_only and entry is site_only[-1]))
        entry["where"] = "disk" if entry["disk"] else "site"
        entry.pop("fresh", None)
    return out


def _number(label: str) -> float:
    try:
        return float(label)
    except (TypeError, ValueError):
        return 10.0 ** 6


def _default_dub(item: dict, dubs: list, entries: list) -> int:
    """The site's voice-over an episode the folder has not plays in when no voice-over is chosen: the one chosen on
    «Смотреть» before, else the folder's own (the variant most of its episodes have), else the one most episodes of
    which are in the folder, else the first."""
    if not dubs:
        return -1
    names = [d["name"] for d in dubs]
    if item.get("dub") in names:
        return names.index(item["dub"])
    counts: dict = {}
    for entry in entries:
        for v in entry["disk"]:
            if v["key"]:
                counts[v["key"]] = counts.get(v["key"], 0) + 1
    for key, _n in sorted(counts.items(), key=lambda kv: -kv[1]):
        for n, d in enumerate(dubs):
            if d["key"] and d["key"] == key:
                return n
    return max(range(len(dubs)), key=lambda n: (dubs[n]["have"], len(dubs[n]["eps"]), -n))


def pick(entry: dict, voice_key: str, default_dub: int, dubs: list) -> dict:
    """The variant an episode plays in: the series' voice-over — its file (or track) in the folder, else on the site;
    none such — the folder's file (an episode in the folder is played from the disk), else the site's voice-over."""
    if voice_key:
        for v in entry["disk"]:
            if v["key"] == voice_key:
                return dict(v, where="disk")
        for n in entry["site"]:
            if dubs[n]["key"] == voice_key:
                return _site_variant(dubs, n)
    if entry["disk"]:
        return dict(entry["disk"][0], where="disk")
    if entry["site"]:
        return _site_variant(dubs, default_dub if default_dub in entry["site"] else entry["site"][0])
    return {}


def _site_variant(dubs: list, n: int) -> dict:
    d = dubs[n]
    return {"kind": "site", "name": d["short"], "sub": f"{kind_word(d['name']) or 'озвучка'} · онлайн", "dub": d["name"],
            "key": d["key"], "where": "site", "n": n, "voice": d["name"]}


def resume_target(entries: list, item: dict, single: bool = False) -> dict:
    """Where «Продолжить» goes: the last episode at its place; the one after it once it was watched; else the first
    not watched — and the words of the button (``videos.resume_words``)."""
    from webui.pages.videos import resume_words
    if not entries:
        return {}
    keys = [e["key"] for e in entries]
    last = str(item.get("last") or "")
    target, going = None, False
    if last in keys:
        entry = entries[keys.index(last)]
        if not entry["done"]:
            target = entry                    # «с 12:34» — its place says it (none: «Смотреть: серия 5»)
        elif keys.index(last) + 1 < len(entries):
            target, going = entries[keys.index(last) + 1], True
    if target is None:
        target = next((e for e in entries if not e["done"]), entries[0])
    at = V.resume_from(*(target["place"] or (0.0, 0.0)))
    return {"key": target["key"], "at": at, "going": going,
            "words": resume_words(single, target["label"], at, going=going)}


def _model(req, ref, series, item, online, title, public, dubs, entries, voice, resume, *, said, error, hidden) -> dict:
    from core import video_watch as W
    info = series.info if series is not None else {}
    site_name = W.site_of(ref.url) if ref.url else ""
    disk_labels = [e["label"] for e in entries if e["disk"] and not e["group"]]
    site_only = [e["label"] for e in entries if not e["disk"] and e["site"]]
    variants, varied = [], []
    if series is not None:
        for group, eps in series.groups:
            if group and V.variant_group(group):
                variants.append({"name": group, "files": len(eps), "range": ranges([e.label for e in eps])})
                varied += [e.label for e in eps if e.label not in varied]
    voices: dict = {}
    for entry in entries:
        for v in entry["disk"]:
            slot = voices.setdefault(v["key"] or f"=file:{v['name']}", {"name": v["name"], "key": v["key"],
                                                                          "sub": v["sub"], "disk": [], "site": [],
                                                                          "voice": v.get("voice", "")})
            if entry["label"] not in slot["disk"]:
                slot["disk"].append(entry["label"])
        for n in entry["site"]:
            d = dubs[n]
            slot = voices.setdefault(d["key"] or f"=site:{d['name']}",
                                     {"name": d["short"], "key": d["key"], "sub": kind_word(d["name"]) or "озвучка",
                                      "disk": [], "site": [], "voice": d["name"]})
            slot.setdefault("dub", d["name"])
            slot["site"].append(entry["label"])
    total = len(entries)
    rating = ""
    for pair in info.get("ratings") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and str(pair[1]).strip():
            rating = str(pair[1]).strip()
            break
    if public and public.get("ratings"):
        rating = rating or f"{float(public['ratings'][0]['value']):.1f}"
    out = {
        "series": series.id if series is not None else "", "url": ref.url, "key": ref.key,
        "single": bool(series is not None and series.single),
        "title": (series.title if series is not None else "") or (public or {}).get("title") or "",
        "site": {"name": site_name, "address": (public or {}).get("address") or ref.url, "can": bool(ref.url),
                 "online": online, "pending": online and title is None and not error, "said": said, "error": error,
                 "hidden": hidden, "count": len(site_only), "missing": ranges(site_only),
                 "rating": (public or {}).get("ratings", [{}])[0].get("value", 0) if public and public.get("ratings") else 0},
        "folder": {"name": series.folder_title if series is not None else "", "range": ranges(disk_labels),
                   "count": len(disk_labels), "variants": variants, "varied": ranges(varied)},
        "voice": voice, "voices": list(voices.values()),
        "dubs": [dict({k: d[k] for k in ("name", "short", "kind", "plain", "files", "key")},
                      players=[{"key": p.get("key"), "name": p.get("name")} for p in d.get("players") or []
                               if isinstance(p, dict)]) for d in dubs],
        "episodes": entries, "resume": resume,
        "counts": {"total": total, "disk": sum(1 for e in entries if e["disk"]), "site": len(site_only),
                   "done": sum(1 for e in entries if e["done"]), "new": sum(1 for e in entries if e["new"])},
        "rating": rating.replace(".", ","),
        "facts": _facts(info, public), "about": _about(info, public), "about_from": _about_from(info, public),
        "genres": _genres(info, public), "alt": _alt(series, public),
        "poster": (f"/videos/cover/{series.id}?size=l" if series is not None else
                   (f"/api/catalog/cover?c={quote(_poster_of(ref, public), safe='')}" if _poster_of(ref, public)
                    else "")),
        "hentai": bool(public.get("hentai")) if public else False,
        "qualities": (public or {}).get("qualities") or [], "picks": {k: item.get(k) or "" for k in ("dub", "player",
                                                                                                      "quality")},
        "autonext": _autonext(),
        "site_fields": _site_fields(public),
    }
    return out


def _poster_of(ref: Ref, public: dict | None) -> str:
    """(v5.44.123) The poster of a title of a site without a folder (``/api/catalog/cover``'s number): the one the owner
    chose in the menu of covers (``core/title_covers``), else its site's."""
    from core import title_covers
    entry = title_covers.chosen([ref.key]) if ref.key else None
    return (title_covers.catalog_id(entry) if entry is not None else "") or str((public or {}).get("poster") or "")


def _site_fields(public: dict | None) -> list:
    """What the site says of its title (a title shown online, as «Смотреть» showed it): its ratings with the votes, its
    facts («Серий» — and how many are in the folder), the genres, the voice-overs — [[label, words]]."""
    if not public:
        return []
    from webui.pages.post_sets import plural
    rows = []
    for r in public.get("ratings") or []:
        try:
            value = f"{float(r.get('value') or 0):.1f}".replace(".", ",")
            votes = int(r.get("votes") or 0)
        except (TypeError, ValueError):
            continue
        words = f"{votes:,}".replace(",", " ") + " " + plural(votes, ("голос", "голоса", "голосов")).split(" ", 1)[-1]
        rows.append([str(r.get("name") or "Оценка"), value + (f" · {words}" if votes else "")])
    have = max([int(d.get("have") or 0) for d in public.get("dubs") or []] or [0])
    for label, value in public.get("facts") or []:
        rows.append([str(label), f"{value} · в папке {have}" if label == "Серий" and have else str(value)])
    if public.get("genres"):
        rows.append(["Жанры", ", ".join(str(g) for g in public["genres"])])
    named = [d for d in public.get("dubs") or [] if not d.get("plain")]
    if named:
        # a studio's subtitles and its voice-over are two releases: the kind is said where it is not a voice-over
        rows.append(["Озвучка", ", ".join(d["short"] + (" (субтитры)" if d["kind"] == "subs" else " (дубляж)"
                                                        if d["kind"] == "dub" else "") for d in named)])
    return rows


def _autonext() -> str:
    from webui import prefs
    return "off" if str(prefs.load().get("video_autonext") or "on") == "off" else "on"


def _facts(info: dict, public: dict | None) -> str:
    """«ТВ-сериал · 2024 · Выходит · Студия Маяк» — the line over the name."""
    parts = []
    for key in ("type", "year", "status", "studio"):
        value = info.get(key)
        if isinstance(value, list):
            value = ", ".join(str(x) for x in value[:2] if str(x).strip())
        if value and str(value).strip():
            parts.append(str(value).strip()[:40])
    if not parts and public:
        facts = dict(public.get("facts") or [])
        for label in ("Тип", "Год выхода", "Статус", "Студия"):
            if facts.get(label):
                parts.append(str(facts[label])[:40])
    return " · ".join(parts)


def _about(info: dict, public: dict | None) -> list:
    text = str(info.get("description") or "").strip()
    if text:
        return [p.strip() for p in re.split(r"\n\s*\n|\r?\n", text) if p.strip()]
    return list((public or {}).get("about") or [])


def _about_from(info: dict, public: dict | None) -> str:
    try:
        stamp = int(info.get("imported_at") or 0)
    except (TypeError, ValueError):
        stamp = 0
    if info.get("description"):
        site = str(info.get("site") or "").strip()
        when = _date(stamp) if stamp > 0 else ""
        return "Описание" + (f" — с {site}" if site else "") + (f", взято {when}" if when else "")
    return f"Описание — с {public['site']}" if public and public.get("about") else ""


def _genres(info: dict, public: dict | None) -> list:
    genres = info.get("genres")
    if isinstance(genres, list) and genres:
        return [str(g) for g in genres[:12]]
    return list((public or {}).get("genres") or [])[:12]


def _alt(series, public: dict | None) -> list:
    if series is not None and series.alt_titles:
        return series.alt_titles[:4]
    return list((public or {}).get("alt") or [])[:4]


# ── «Продолжить просмотр»: the series watched lately, the folder's and the sites' ─────────────────────────────────────
def _next_label(label: str) -> str:
    try:
        return f"{float(label) + 1:g}"
    except (TypeError, ValueError):
        return ""


def going(every: list, shown: list, *, limit: int = 8, phone: bool = False) -> list:
    """The shelf «Продолжить просмотр» (the computer's and the phone's): the series watched lately — of the folder and
    of the sites (the one memory) — each with the episode to go on with: the one it stopped at, or the next after the
    one watched to the end (in the folder, else on its site); a series the mode hides is not on it."""
    from webui import content_mode
    visible = {s.id for s in shown}
    by_key: dict = {}
    for series in every:
        for key in V.memory_keys(series):
            by_key.setdefault(key, series)
    rows: list = []
    taken: set = set()
    for key, item in reversed(list(V.PROGRESS.items().items())):     # the latest touched first (a tie of seconds)
        if not isinstance(item, dict) or not item.get("last") or key.startswith("card:"):
            continue
        series = by_key.get(key)
        if series is not None:
            if series.id in taken or series.id not in visible:
                continue
            taken.add(series.id)
            V.PROGRESS.adopt(series)
            view = V.PROGRESS.view(V.memory_keys(series))
            rows.append((V.played(view), series, view))
        elif item.get("address") and not key.startswith("folder:") and content_mode.shows(bool(item.get("hentai"))):
            rows.append((V.played(item), None, item))
    rows.sort(key=lambda row: -row[0])
    return [tile for tile in (_going_tile(series, item, phone) for _when, series, item in rows[:limit]) if tile]


def _going_tile(series, item: dict, phone: bool) -> dict:
    from core import video_watch as W
    from webui.pages.videos import resume_words
    watched = {str(x) for x in item.get("watched") or []}
    places = item.get("places") or {}
    last = str(item.get("last") or "")
    units = series.units() if series is not None else []
    keys = [u.key for u in units]
    try:
        count = int(item.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    target, going_on = last, True
    if last in watched:
        if last in keys and keys.index(last) + 1 < len(keys):
            target = keys[keys.index(last) + 1]
        else:
            target = _next_label(last)
            if not target or (count and _number(target) > count and target not in keys):
                return {}                     # watched to the very end: nothing to go on with
    place = places.get(target) if isinstance(places.get(target), list) else None
    at, length = (float(place[0]), float(place[1])) if place else (0.0, 0.0)
    at = V.resume_from(at, length)
    unit = next((u for u in units if u.key == target), None)
    single = bool(series is not None and series.single)
    m = "/m" if phone else ""
    if series is not None:
        href = f"{m}/videos/s/{series.id}?ep={quote(target, safe='')}&play=1" + ("" if phone else "#player")
        picture = f"/videos/frame/{unit.files[0].id}" if unit is not None else f"/videos/cover/{series.id}"
        title = series.title
        where = "disk" if unit is not None else "site"
        site = W.site_of(str(series.info.get("source_url") or "")) or str(item.get("site") or "")
    else:
        address = str(item.get("address") or "")
        href = f"{m}/videos/watch?" + urlencode([("url", address), ("ep", target), ("play", "1")], quote_via=quote)
        picture = f"/api/catalog/cover?c={quote(str(item.get('cover') or ''), safe='')}" if item.get("cover") else ""
        title = str(item.get("name") or W.site_of(address) or "Сериал")
        where, site = "site", str(item.get("site") or W.site_of(address))
    left = ""
    if length and at:
        left = f"осталось {max(1, round((length - at) / 60))} мин"
    elif last in watched and not single:
        left = f"серия {last} досмотрена"
    return {"title": title, "href": href, "picture": picture, "words": resume_words(single, target, at, going=going_on),
            "clock": (f"{human(at) or '0:00'} / {human(length)}" if length else ""),
            "part": round(100 * at / length) if length else 0, "left": left, "where": where,
            "from": "из папки" if where == "disk" else f"с {site}, онлайн" if site else "онлайн", "at": at}


def human(seconds: float) -> str:
    from core.video_probe import human_duration
    return human_duration(seconds)


# ── the APIs ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _hidden_answer(built: Built) -> Response | None:
    if built.hidden:
        word = ("Включён режим «SFW», а этот сериал — хентай" if built.hidden == "sfw" else
                "Включён режим «NSFW» — видно только взрослое, а этот сериал не помечен как хентай")
        return Response.json({"ok": False, "hidden": built.hidden, "error": word}, 409)
    return None


def _folder_hidden(ref: Ref) -> str:
    """The mode hides the folder's series ("" — shown)."""
    from core import content_marks as CM
    from webui import content_mode
    if ref.series is None:
        return ""
    found = CM.series_verdict(ref.series)
    return "" if content_mode.shows(found.hentai) else content_mode.mode()


def model_json(req: Request) -> Response:
    """``/api/series?series=<id>|url=<address>[&wait=1]`` — the model (``wait`` — the site waited for a moment)."""
    ref = _ref_of(req)
    if ref is None:
        return Response.json({"ok": False, "error": "Такого сериала нет"}, 404)
    if _folder_hidden(ref):
        return Response.json({"ok": False, "hidden": _folder_hidden(ref), "error": "Скрыто режимом"}, 409)
    built = build(req, ref, wait=SITE_WAIT if req.arg("wait") == "1" else 0.0)
    refused = _hidden_answer(built)
    return refused or Response.json({"ok": True, "model": built.model})


def choose_json(req: Request) -> Response:
    """{series|url, voice?, online?, autonext?, dub?, player?, quality?}: the series' choices — the new model."""
    from core import video_dubs as Y
    body = req.json()
    ref = _ref_of(req, body)
    if ref is None:
        return Response.json({"ok": False, "error": "Такого сериала нет"}, 404)
    if _folder_hidden(ref):
        return Response.json({"ok": False, "hidden": _folder_hidden(ref), "error": "Скрыто режимом"}, 409)
    quality = body.get("quality")
    if quality is not None and str(quality) not in ("", "best") + tuple(Y.QUALITIES):
        return Response.json({"ok": False, "error": "Непонятное качество"}, 400)
    voice = body.get("voice")
    voice = str(voice)[:200] if voice is not None else None
    online = body.get("online") if isinstance(body.get("online"), bool) else None
    V.PROGRESS.choose(ref.key, name=str(body.get("name") or "")[:300], voice=voice, online=online,
                      dub=str(body["dub"])[:200] if body.get("dub") else None,
                      player=str(body["player"])[:40] if body.get("player") is not None else None,
                      quality=str(quality) if quality is not None else None)
    if voice is not None:                 # the voice-over inside a file: the players of the program take it so
        title = _track_title(build(req, ref).model, voice) if voice else ""
        V.PROGRESS.set_tracks(ref.key, "audio", {"name": "", "title": title[:120], "lang": ""} if voice else None)
        if voice:
            V.PROGRESS.choose(ref.key, voice=voice)
    if isinstance(body.get("autonext"), bool):
        from webui import prefs
        prefs.save({"video_autonext": "on" if body["autonext"] else "off"})
    built = build(req, ref)
    refused = _hidden_answer(built)
    return refused or Response.json({"ok": True, "model": built.model})


def _track_title(model: dict, voice: str) -> str:
    """The title of the sound track a voice-over is inside the files of the series: a track of a file — its own title
    («Озвучка Б»), a sub-folder's or the site's voice-over — its name as the merged file names its track
    («Озвучка AniLibria» → «AniLibria»: ``video_merge.title_of``)."""
    key = _key(voice)
    for entry in model.get("episodes") or []:
        for v in entry.get("disk") or []:
            if v.get("kind") == "track" and (v.get("name") == voice or (key and v.get("key") == key)):
                return str(v["name"])
    from core.video_merge import title_of
    return title_of(voice)


def _number_arg(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if 0 <= number < 10 ** 7 else default


def progress_json(req: Request) -> Response:
    """{series|url, ep, at, length, ended, ending, dub?, player?, quality?}: where an episode stopped — in the one
    memory of the series (an episode of the folder and of the site alike)."""
    body = req.json()
    ref = _ref_of(req, body)
    label = str(body.get("ep") or "").strip()[:120]
    if ref is None or not label:
        return Response.json({"ok": False, "error": "Нет сериала или серии"}, 400)
    extra = {}
    from core import video_watch as W
    title = W.kept(ref.url) if ref.url else None
    if title is not None:                 # what the shelf «Продолжить просмотр» shows of a title of a site
        extra = {"address": title.address, "site": title.site, "count": len(_labels_of(title)),
                 "hentai": bool(title.hentai)}
        if not V.PROGRESS.of(ref.key).get("cover"):
            extra["cover"] = cover_of(title)
    name = ref.series.title if ref.series is not None else (title.name if title is not None else "")
    item = V.PROGRESS.record(ref.key, label, _number_arg(body.get("at")), _number_arg(body.get("length")),
                             ended=body.get("ended") is True, name=name, dub=str(body.get("dub") or "")[:200] or None,
                             ending=_number_arg(body.get("ending")), extra=extra)
    return Response.json({"ok": True, "watched": label in (item.get("watched") or []),
                          "progress": {k: item.get(k) for k in ("last", "at", "length", "done", "watched", "places")}})


def _labels_of(title) -> set:
    return {e.label for d in title.dubs for e in d.episodes}


def cover_of(title) -> str:
    """The cover of a title of a site as ``/api/catalog/cover`` takes it ("" — none): what ``video_watch.public`` gives,
    without reading the folder."""
    from core import catalog
    from core import video_watch as W
    try:
        if title.page:
            picture = W._page_poster(title.meta, title.address)
        else:
            from core import video_dubs as Y
            poster = title.anime.get("poster") if isinstance(title.anime.get("poster"), dict) else {}
            picture = Y.absolute(poster.get("fullsize") or poster.get("big") or poster.get("medium") or "")
        return catalog.cover_id(picture, title.address) if picture else ""
    except Exception as exc:  # noqa: BLE001 - the cover is the shelf's only
        _LOG.debug("cover of %s: %s", getattr(title, "key", ""), exc)
        return ""


def play_json(req: Request) -> Response:
    """{series|url, ep, variant?: {kind, file, track, dub}, phone?}: what plays an episode — its file (the folder's)
    or its stream through the program (the site's), where it starts, its opening and ending."""
    from core import video_watch as W
    body = req.json()
    ref = _ref_of(req, body)
    label = str(body.get("ep") or "").strip()[:120]
    if ref is None or not label:
        return Response.json({"ok": False, "error": "Нет сериала или серии"}, 400)
    if _folder_hidden(ref):
        return Response.json({"ok": False, "hidden": _folder_hidden(ref), "error": "Скрыто режимом"}, 409)
    built = build(req, ref)
    refused = _hidden_answer(built)
    if refused is not None:
        return refused
    entry = next((e for e in built.model["episodes"] if e["key"] == label), None)
    if entry is None:
        return Response.json({"ok": False, "error": f"Серии {label} у сериала нет"}, 404)
    asked = body.get("variant") if isinstance(body.get("variant"), dict) else None
    variant = chosen_variant(built.model, entry, asked)
    if not variant:
        return Response.json({"ok": False, "error": "Этой серии нет ни в папке, ни на сайте (включите «Смотреть "
                                                    "онлайн»)"}, 409)
    if asked:                             # a variant chosen at the episode: the series' voice-over from now on
        if variant["kind"] == "track" or variant.get("key"):          # a track of a file — by its own title
            V.PROGRESS.set_tracks(ref.key, "audio", {"name": "", "title": str(variant["name"])[:120], "lang": ""})
        V.PROGRESS.choose(ref.key, voice=str(variant.get("voice") or ""), dub=variant.get("dub") or None)
    item = V.PROGRESS.view(ref.keys)
    place = entry.get("place") or [0.0, 0.0]
    start = V.resume_from(place[0], place[1]) if not entry["done"] else 0.0
    out = {"ok": True, "ep": entry["key"], "label": entry["label"], "name": entry["name"], "where": variant["where"],
           "variant": variant, "from": start, "length": entry.get("length") or 0.0}
    if variant["where"] == "disk":
        series, episode = ref.series, ref.series.episode(variant["file"]) if ref.series is not None else None
        if episode is None:
            return Response.json({"ok": False, "error": "Файла этой серии нет — перечитайте папку видео"}, 404)
        from webui.pages import videos as VD
        out.update(kind="file", episode=episode.id, src=f"/videos/file/{episode.id}", track=variant.get("track", -1),
                   skips=VD._skips(series, episode) if not series.single else {},
                   subs=[{"key": f"x{n}", "label": f.name, "src": f"/videos/subs/{episode.id}?n={n}"}
                         for n, f in enumerate(_subtitle_files(series, episode))],
                   sub=_chosen_sub(series, episode, item))
        if body.get("phone"):
            from webui.pages import phone_videos as PV
            audio = variant["track"] if variant.get("track", -1) >= 0 else None
            data = PV.data_of(req, series, episode, audio=audio)
            out["phone"] = {k: data[k] for k in ("direct", "src", "audio", "chosen", "subs", "sub", "error", "duration")}
        return Response.json(out)
    if built.title is None:
        return Response.json({"ok": False, "error": "Сайт сериала ещё не ответил — подождите и нажмите серию "
                                                    "снова"}, 409)
    try:
        stream = W.open_stream(built.title, variant["dub"], entry["label"], player=str(body.get("player") or ""),
                               quality=str(body.get("quality") or "").replace("best", ""))
    except W.WatchError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 502)
    V.PROGRESS.choose(ref.key, name=built.model["title"], dub=stream.dub)
    info = W.stream_public(stream, item)
    out.update({k: v for k, v in info.items() if k not in ("from", "kind")})
    out["kind"] = "stream"
    out["stream"] = info["kind"]                 # "hls" — a stream, "mp4" — a file
    if info.get("length"):
        out["length"] = info["length"]
    return Response.json(out)


def chosen_variant(model: dict, entry: dict, asked: dict | None) -> dict:
    """The variant asked at the episode (one of its own), else the one the series' voice-over gives."""
    if asked:
        kind = str(asked.get("kind") or "")
        if kind in ("track", "file"):
            for v in entry["disk"]:
                if v["file"] == str(asked.get("file") or "") and (kind == "file" or v["track"] == asked.get("track")):
                    return dict(v, where="disk")
        elif kind == "site":
            for n in entry["site"]:
                if model["dubs"][n]["name"] == str(asked.get("dub") or ""):
                    d = model["dubs"][n]
                    return {"kind": "site", "name": d["short"], "sub": f"{kind_word(d['name']) or 'озвучка'} · онлайн",
                            "dub": d["name"], "key": _key(d["name"]) if d["name"] else "", "where": "site", "n": n,
                            "voice": d["name"]}
    return dict(entry.get("chosen") or {})


def _subtitle_files(series, episode) -> list:
    from core import video_tracks
    try:
        return video_tracks.subtitle_files(episode.path, top=None if series.single else series.path)
    except OSError:
        return []


def _chosen_sub(series, episode, item: dict) -> str:
    from core import video_tracks
    files = _subtitle_files(series, episode)
    listed = [{"id": f"x{n}", "name": f.name, "title": f.name, "lang": "", "file": True} for n, f in enumerate(files)]
    prefer = item.get("tracks") if isinstance(item.get("tracks"), dict) else {}
    _voice, chosen = video_tracks.preferred(prefer, [], listed)
    return chosen or ""


# ── the page ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def in_app(req: Request) -> bool:
    return "QtWebEngine" in str(req.headers.get("User-Agent") or req.headers.get("user-agent") or "")


def address(ref: Ref, *, ep: str = "", play: bool = False, phone: bool = False) -> str:
    """The page of the series (an episode of it — ``ep``, played at once — ``play``)."""
    pairs = [("ep", ep)] if ep else []
    if play:
        pairs.append(("play", "1"))
    if ref.series is not None:
        base = ("/m" if phone else "") + f"/videos/s/{ref.series.id}"
        return base + ("?" + urlencode(pairs, quote_via=quote) if pairs else "")
    return ("/m" if phone else "") + "/videos/watch?" + urlencode([("url", ref.url)] + pairs, quote_via=quote)


def _json_tag(model: dict) -> str:
    text = json.dumps(model, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/json" data-sv-model>{text}</script>'


def asked_entry(model: dict, ep: str, series) -> tuple:
    """(the episode an address names — its number, or a file of it (``ep`` of before: a file's id), the variant that
    file is) — (None, None) when it names none."""
    ep = str(ep or "").strip()
    if not ep:
        return None, None
    if re.fullmatch(r"[0-9a-f]{12}", ep) and series is not None:
        episode = series.episode(ep)
        unit = series.unit_of(episode) if episode is not None else None
        entry = next((e for e in model["episodes"] if unit is not None and e["key"] == unit.key), None)
        if entry is not None:
            variant = next((v for v in entry["disk"] if v["file"] == ep), None)
            if entry["chosen"].get("file") == ep:
                variant = entry["chosen"]
            return entry, (dict(variant, where="disk") if variant else None)
        return None, None
    entry = next((e for e in model["episodes"] if e["key"] == ep), None)
    return entry, None


SCREEN = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 9'%3E%3Crect width='16' "
          "height='9' fill='%23000'/%3E%3C/svg%3E")         # the window's player before a stream is opened


def _skip_buttons() -> str:
    return ('<button type="button" class="nv-skip nv-skip-opening sv-skip" data-skip="opening" hidden>Пропустить '
            'опенинг</button><button type="button" class="nv-skip nv-skip-ending sv-skip" data-skip="ending" hidden>'
            'Пропустить эндинг</button>')


# (v5.44.122) the icons of the one set (webui/icons.py — the rule of v5.44.26), with the bar's own class for its look
PREV_ICON = icons.PREV.replace('class="icon i-prev"', 'class="icon i-prev sv-i"')
NEXT_ICON = icons.NEXT.replace('class="icon i-next"', 'class="icon i-next sv-i"')


def _bar_html(phone: bool) -> str:
    """The bar of the page's own player (a browser, the phone): play, the previous and next episode and its number, the time, the
    place with the opening and the ending marked, the sound, the whole screen (``series.js``)."""
    from webui import icons
    seek = ('<div class="sv-seek" data-sv-seek><span class="sv-track"></span><span class="sv-done" data-sv-done></span>'
            '<span class="sv-marks" data-sv-marks aria-hidden="true"></span>'
            '<input type="range" class="sv-range" data-sv-range min="0" max="1000" step="1" value="0" '
            'aria-label="Место в серии"></div>')
    buttons = (f'<button type="button" class="sv-b sv-toggle" data-sv-toggle aria-label="Пуск" '
               f'title="Пауза / пуск (пробел)">{icons.PLAY}{icons.PAUSE}</button>'
               f'<button type="button" class="sv-b" data-sv-prev aria-label="Предыдущая серия" '
               f'title="Предыдущая серия">{PREV_ICON}</button>'
               f'<button type="button" class="sv-b sv-next" data-sv-next aria-label="Следующая серия" '
               f'title="Следующая серия">{NEXT_ICON}<span data-sv-next-label></span></button>')
    time_ = '<span class="sv-time" data-sv-time>0:00 / 0:00</span>'
    number = '<span class="sv-num" data-sv-num hidden></span>'
    sound = (f'<button type="button" class="sv-b sv-mute" data-sv-mute aria-label="Выключить звук" title="Звук">'
             f'{icons.SOUND}{icons.MUTED}</button><input type="range" class="sv-vol" data-sv-vol min="0" max="100" '
             f'step="1" value="100" aria-label="Громкость">')
    full = (f'<button type="button" class="sv-b sv-fullb" data-sv-full aria-label="Во весь экран" '
            f'title="Во весь экран (F)">{icons.FULL}{icons.UNFULL}</button>')
    if phone:
        return (f'<div class="sv-bar sv-bar-phone" data-sv-bar>{seek}<div class="sv-row">{buttons}{time_}{number}'
                f'</div></div>')
    return f'<div class="sv-bar" data-sv-bar>{buttons}{number}{time_}{seek}{sound}{full}</div>'


def own_player(*, phone: bool, video_attrs: str = "", src: str = "", poster: str = "", autoplay: bool = False,
               slides: str = "", skips: bool = True) -> str:
    """The page's own player: ``<video>`` without the browser's buttons, with the bar of the page (a browser, the
    phone; in the program's window — a file its browser plays); ``skips`` — «Пропустить опенинг / эндинг» (an
    episode of a series; a film — none)."""
    big = '<button type="button" class="sv-big" data-sv-big aria-label="Смотреть">▶</button>'
    if slides:                        # the frames of the episode before it plays: the big ▶ over them, a click plays
        slides = slides.replace("></div>", ">" + big + "</div>", 1)
    corner = ('<button type="button" class="sv-corner" data-sv-full aria-label="Во весь экран">'
              '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 '
              '15v5h-5"/></svg></button>' if phone else "")
    return (f'<div class="sv-play" data-sv-play><div class="sv-screen vscreen" data-sv-screen>'
            f'<video class="sv-video vvideo" data-sv-video playsinline preload="{"auto" if src else "none"}"'
            + (f' src="{esc(src)}"' if src else "") + (f' poster="{esc(poster)}"' if poster else "")
            + (" autoplay" if autoplay else "") + f'{video_attrs}></video>{slides}'
            + ("" if slides else big) + f'{_skip_buttons() if skips else ""}{corner}'
            '<div class="sv-wait" data-sv-wait hidden><p data-sv-wait-text></p><span class="sv-wait-bar">'
            '<span data-sv-wait-bar></span></span></div>'
            '<p class="sv-problem" data-sv-problem role="alert" hidden></p>'
            f'</div>{_bar_html(phone)}</div>')


def _app_player(req: Request, ref: Ref, model: dict, entry: dict | None, variant: dict, *, play: bool,
                full: bool) -> str:
    """The player the program's window shows for the episode chosen: its file in the program's own player (the
    window's browser — a file it plays: VP9, AV1 with Opus), an episode of the site — its stream in the own player."""
    from core import video_probe
    from webui.pages.gallery import native_video_box, window_player
    title = model["title"] + (f" · Серия {entry['label']}" if entry and not model["single"] else "")
    if not entry or not variant:
        return native_video_box(req, {"stream": "", "autoplay": "0", "loop": "0", "from": "0"}, SCREEN,
                                frame_class="vframe sv-frame", overlay="" if model["single"] else _skip_buttons(),
                                title=title)
    place = entry.get("place") or [0.0, 0.0]
    start = V.resume_from(place[0], place[1]) if not entry["done"] else 0.0
    if variant["where"] == "site":
        attrs = {"stream": "", "autoplay": "1" if play else "0", "loop": "0", "from": f"{start:.1f}",
                 "ep": entry["key"], "dub": variant.get("dub") or ""}
        if entry.get("length"):
            attrs["length"] = f"{float(entry['length']):.1f}"
        if full:
            attrs["full"] = "1"
        return native_video_box(req, attrs, SCREEN, frame_class="vframe sv-frame", overlay=_skip_buttons(),
                                title=title)
    series = ref.series
    episode = series.episode(variant["file"])
    if episode is None:
        return '<p class="sv-problem">Файла этой серии нет — перечитайте папку видео.</p>'
    own = window_player(req, Path(episode.path), prefer=video_probe.OWN) == video_probe.OWN
    other = ""
    if req.arg("player") not in (video_probe.BROWSER, video_probe.OWN):
        switch = video_probe.BROWSER if own else video_probe.OWN
        base = address(ref, ep=episode.id, play=play or own)
        other = base + ("&full=1" if full else "") + f"&player={switch}#player"
    attrs = {"episode": episode.id, "series": series.id, "from": f"{start:.1f}", "resume": f"{start:.1f}",
             "autoplay": "1" if play else "0", "loop": "0", "ep": entry["key"]}
    if entry.get("length"):
        attrs["length"] = f"{float(entry['length']):.1f}"
    if variant.get("track", -1) >= 0:
        attrs["track"] = str(variant["track"])
    if full:
        attrs["full"] = "1"
    if not series.single:
        from webui.pages.videos import _skips
        skips = _skips(series, episode)
        if skips:
            attrs["skips"] = json.dumps(skips, separators=(",", ":"))
    poster = f"/videos/frame/{episode.id}"
    if own:
        if other:
            attrs["other"] = other
        slides = ("" if play else f'<div class="vslides" data-episode="{episode.id}" data-count="{V.SLIDES}" '
                                  f'aria-hidden="true"></div>')
        return native_video_box(req, attrs, poster, frame_class="vframe sv-frame", full=f'data-episode="{episode.id}"',
                                external=(f'<button type="button" data-action="vid-open" data-id="{episode.id}" '
                                          f'title="Открыть файл в программе, которой Windows открывает видео">'
                                          f'Плеер Windows</button>'),
                                overlay=slides + ("" if series.single else _skip_buttons()), title=title)
    if other:
        attrs["other"] = other
        info = video_probe.probe(episode.path)
        if info is not None and info.video:
            attrs["picture"] = "1"           # a picture that never shows is the browser's failure too
    data_attrs = "".join(f' data-{k}="{esc(v)}"' for k, v in attrs.items())
    slides = ("" if play else f'<div class="vslides vcover" data-episode="{episode.id}" data-count="{V.SLIDES}" '
                              f'aria-hidden="true"></div>')
    return own_player(phone=False, video_attrs=data_attrs, src=f"/videos/file/{episode.id}", poster=poster,
                      autoplay=play, slides=slides, skips=not series.single)


def _hero(req: Request, ref: Ref, model: dict, tile: dict, *, phone: bool) -> str:
    """The cover, the facts over the name, the name, its other names, the person's mark, the side links; the
    description and the genres — what the page knows now (a title of a site only: the tile's; the rest comes)."""
    from webui.pages import videos as VD
    series = ref.series
    cover = ""
    if series is not None:
        cover = f'<img src="/videos/cover/{series.id}?size=l&amp;v={esc(VD._stamp(series))}" alt="" decoding="async">'
    elif tile.get("cover"):
        cover = (f'<img src="/api/catalog/cover?c={esc(quote(tile["cover"], safe=""))}" alt="" decoding="async" '
                 f'data-sv-tile>')
    name = model["title"] or tile.get("name") or "Сериал"
    alt = " · ".join(esc(x) for x in model["alt"])
    side = []
    mark = ""                       # (v5.44.122) the phone's «Это хентай» — with what the mode does with it in words
    source = ref.url or (str(series.info.get("source_url") or "") if series is not None else "")
    if source.startswith(("http://", "https://")):
        site = model["site"]["name"] or (str(series.info.get("site") or "") if series is not None else "") or "На сайте"
        side.append(f'<a class="sv-link" href="{esc(source)}" target="_blank" rel="noopener noreferrer" '
                    f'data-sv-site-link>{esc(site)} ↗</a>')
    if series is not None:
        if source.startswith(("http://", "https://")) and not series.single:
            from core.video_download import is_frame_site
            if not is_frame_site(source) or VD._yummy(req, source):
                from webui.pages.get_link import link_of
                side.append(f'<a class="sv-link sv-get" href="{esc(link_of(source))}" title="Скачать серии с этой '
                            f'страницы сайта (те, которых ещё нет)">Скачать с сайта</a>')
        if not phone:
            side.append('<button type="button" class="sv-link" data-action="vid-meta-open" aria-controls="vid-meta">'
                        'Описание с сайта</button>')
        from core import content_marks as CM
        if phone:
            from webui.pages import phone as P
            mark = P.mark_box("series", series.id, CM.series_verdict(series))
        else:
            side.append(VD._mark_button(series, CM.series_verdict(series)).replace('class="vbutton"', 'class="sv-link"'))
    elif ref.url:
        side.append(f'<a class="sv-link sv-get" href="{"/m/get" if phone else "/get"}?url='
                    f'{esc(quote(ref.url, safe=""))}&amp;go=1">Скачать…</a>')
    rating = VD._rating_box(series) if series is not None and not phone else ""
    if model["site"]["pending"]:                       # its site is read meanwhile (series.js: it says what comes)
        rating += (f'<p class="sv-wait-line" data-sv-waiting role="status">Читаю тайтл с '
                   f'{esc(model["site"]["name"])}…</p>')
    if model["about"]:
        about = ('<div class="sv-desc" data-sv-desc><div class="sv-desc-text">'
                 + "".join(f"<p>{esc(p)}</p>" for p in model["about"]) + '</div>'
                 '<button type="button" class="sv-more" data-sv-more hidden>Ещё</button></div>')
    else:
        about = '<div class="sv-desc" data-sv-desc hidden></div>'
    about_from = (f'<p class="sv-desc-from" data-sv-from{"" if model["about_from"] else " hidden"}>'
                  f'{esc(model["about_from"])}</p>')
    chips = ('<ul class="sv-chips" data-sv-chips>' + "".join(
        f'<li><a href="{esc(("/m" if phone else "") + "/videos?" + urlencode({"q" if phone else "tags": g}))}">'
        f'{esc(g)}</a></li>' for g in model["genres"]) + "</ul>")
    order = ""
    if series is not None and not phone and VD._yummy(req, source):
        order = f'<div class="vorder" data-order="{series.id}" hidden></div>'
    return (f'<section class="sv-hero" aria-labelledby="sv-name">'
            f'<div class="sv-cover" data-sv-cover>{cover}</div><div class="sv-about">'
            f'<p class="sv-facts" data-sv-facts>{esc(model["facts"])}</p>'
            f'<h1 class="sv-name" id="sv-name" data-sv-name>{esc(name)}</h1>'
            f'<p class="sv-alt" data-sv-alt{"" if alt else " hidden"}>{alt}</p>'
            f'<div class="sv-numbers" data-sv-numbers></div>{rating}'
            f'<div class="sv-actions" data-sv-actions></div>'
            f'<div class="sv-side">{"".join(side)}<span class="action-status" role="status"></span></div>{mark}'
            f'<section class="sv-src" data-sv-src aria-label="Откуда серии"></section>'
            f'{about}{about_from}{chips}{_fields(series)}{order}</div></section>')


MANY = 10                       # a list longer than this (voice-overs) shows its first ones and «ещё N»


def _fields(series) -> str:
    """Everything the description of the folder's series says («Описание с сайта», ``info.json``): its lines as the
    page of before showed them (the year, the country, the voice-overs, the studio, the ratings…), folded."""
    if series is None:
        return ""
    from core import content_marks as CM
    from core.video_meta import FIELD_ORDER, FIELD_TITLES
    info = series.info
    facts = []
    for key in FIELD_ORDER:
        value = info.get(key)
        if value:
            facts.append(f'<dt>{esc(FIELD_TITLES[key])}</dt><dd>{_value(key, value)}</dd>')
    for pair in info.get("extra") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and str(pair[0]).strip():
            facts.append(f'<dt>{esc(pair[0])}</dt><dd>{esc(pair[1])}</dd>')
    ratings = [f'{esc(pair[0])} <b>{esc(pair[1])}</b>' for pair in (info.get("ratings") or [])
               if isinstance(pair, (list, tuple)) and len(pair) == 2]
    if ratings:
        facts.append(f'<dt>Оценки</dt><dd>{" · ".join(ratings)}</dd>')
    found = CM.series_verdict(series)              # (v5.44.53) for the switch «SFW / Всё / NSFW»
    if found.hentai or found.by_hand:
        said = ("да" if found.hentai else "нет") + (f" — {found.reason}" if found.reason else "")
        facts.append(f'<dt>Хентай</dt><dd class="content-row">{esc(said)}</dd>')
    if not facts:
        return ""
    return (f'<details class="sv-fields"><summary>Всё о сериале</summary><dl class="vfacts">{"".join(facts)}</dl>'
            f'</details>')


def _value(key: str, value) -> str:
    from core.video_meta import LIST_FIELDS
    from webui.html import url
    if isinstance(value, list):
        items = [str(v) for v in value if str(v).strip()]
        if key in ("genres", "voice", "studio", "translation", "director"):
            chips = [f'<a class="vchip" href="{esc(url("/videos", tags=v))}">{esc(v)}</a>' for v in items]
        else:
            chips = [esc(v) for v in items]
        if key == "notes":
            return "<br>".join(chips)
        if len(chips) > MANY:
            return (" ".join(chips[:MANY]) + f'<details class="vmore-list"><summary>ещё {len(chips) - MANY}</summary>'
                    + " ".join(chips[MANY:]) + "</details>")
        return " ".join(chips) if key in LIST_FIELDS else ", ".join(chips)
    return esc(value)


def _strip(ref: Ref, model: dict, current: str) -> str:
    """The episodes as numbers over the player — the tabs of the episode the player shows (the row of numbers of the
    page of before, ``.vepisodes`` / ``.vep``: «Скачать с сайта» leads here and its checks find it); ``series.js``
    draws it again as the page changes."""
    if model["single"] or not model["episodes"]:
        return ""
    tabs = []
    for e in model["episodes"]:
        on = e["key"] == current
        cls = "sv-ep vep" + (" now" if on else " done" if e["done"] else " site" if not e["disk"] else "")
        playable = bool(e["disk"] or e["site"])
        title = e["name"] + (f" — «{e['title']}»" if e["title"] else "") + (", досмотрена" if e["done"] else "") + \
            (", с сайта" if not e["disk"] else "")
        href = f' href="{esc(address(ref, ep=e["key"], play=True) + "#player")}"' if playable else ""
        state = ' aria-selected="true" aria-current="true"' if on else ' aria-selected="false"'
        tabs.append(f'<a class="{cls}{"" if playable else " off"}" role="tab"{state}{href} data-ep="{esc(e["key"])}" '
                    f'title="{esc(title)}">{esc(e["label"])}</a>')
    legend = "✓ досмотрена" + ('<span class="sv-site-word"> • с сайта, онлайн</span>' if model["counts"]["site"] else "")
    return (f'<span class="sv-strip-label">Серии</span><div class="sv-eptabs vepisodes" role="tablist" '
            f'aria-label="Серии">{"".join(tabs)}</div><span class="sv-strip-legend">{legend}</span>')


def _hidden_template(phone: bool, back: str) -> str:
    """The words of a series the mode hides (a title of a site — known once it is read): the page puts them in."""
    from webui import content_mode
    from webui.pages.video_watch import _hidden_words
    now = content_mode.mode()
    if now == "all":
        return ""
    return "<template data-sv-hidden>" + _hidden_words(phone, now, back) + "</template>"


def render(req: Request, ref: Ref, *, phone: bool = False, tile: dict | None = None) -> Response:
    """The page of the series (``?ep=<number>`` — that episode in the player, ``&play=1`` — at once); on the phone
    ``play=1`` — the player's own page."""
    from webui.pages import videos as VD
    tile = tile or {}
    built = build(req, ref)
    model = built.model
    play = req.arg("play") == "1"
    full = play and req.arg("full") == "1"
    entry, variant = asked_entry(model, req.arg("ep"), ref.series)
    asked = variant is not None
    if entry is None and model["resume"]:
        entry = next((e for e in model["episodes"] if e["key"] == model["resume"]["key"]), None)
    variant = variant or (dict(entry["chosen"]) if entry else {})
    app = in_app(req) and not phone
    if not model["rating"] and tile.get("rating"):          # the tile's, until the site answers
        model["rating"] = f"{float(tile['rating']):.1f}".replace(".", ",")
    # «←»: where the page was opened from — «Лента» (a title of a site: /videos/watch), else the series of the folder
    from_feed = req.path.rstrip("/").endswith("/videos/watch")
    back, back_label = ("/videos/feed", "Лента") if from_feed else ("/videos?show=series", "Сериалы")
    if phone:
        back = "/m/videos/feed" if from_feed else "/m/videos"
    attrs = {"series": model["series"], "url": model["url"], "site": model["site"]["name"],
             "phone": "1" if phone else "", "app": "1" if app else "",
             "ep": entry["key"] if entry else "", "file": variant.get("file") or "", "play": "1" if play else "",
             "asked": "1" if asked else "",                  # the address names a file of the episode: that variant
             "back": back, "get": "/m/get" if phone else "/get", "view": "player" if phone and play else "page"}
    data = "".join(f' data-{k}="{esc(v)}"' for k, v in attrs.items())
    hidden = _hidden_template(phone, back)
    if phone and play:
        return _phone_player(ref, model, entry, data, hidden)
    head = ("" if phone else f'<div class="sv-backline"><a class="sv-back" href="{esc(back)}" data-sv-back>'
                             f'← {esc(back_label)}</a></div>')
    player = ""
    if not phone:
        if app:
            stage = _app_player(req, ref, model, entry, variant, play=play, full=full)
        else:
            poster = f"/videos/frame/{variant['file']}" if variant.get("file") else ""
            # the episode chosen, not played yet: its frames one after another over it (videos.js), a click plays it
            slides = (f'<div class="vslides vcover" data-episode="{esc(variant["file"])}" data-count="{V.SLIDES}" '
                      f'data-action="vid-play" aria-hidden="true"></div>'
                      if variant.get("file") and variant.get("where") == "disk" and not play else "")
            stage = own_player(phone=False, poster=poster, slides=slides, skips=not model["single"])
        player = (f'<section class="sv-player" id="player" data-sv-player aria-label="Плеер">'
                  f'<div class="sv-head" data-sv-head></div>'
                  f'<div class="sv-strip" data-sv-strip>{_strip(ref, model, entry["key"] if entry else "")}</div>'
                  f'<div class="sv-stage vstage">{stage}</div><div class="sv-under" data-sv-under></div></section>')
    meta = VD._meta_box(req, ref.series) if ref.series is not None and not phone else ""
    body = (f'<div class="sv{" sv-phone" if phone else ""}" id="series-page"{data}>{_json_tag(model)}{head}'
            f'{_hero(req, ref, model, tile, phone=phone)}'
            f'<section class="sv-list" data-sv-list aria-label="Серии"></section>{player}{meta}</div>{hidden}')
    title = model["title"] or tile.get("name") or "Сериал"
    if phone:
        from webui.pages import phone as P
        return P.page(title, body, active="videos", heading="Видео", back=back, top=P.mode_switch(),
                      scripts=("vendor/hls.light.min", "series", "covers"), kind="m-series-page",   # (v5.44.123)
                      head='<link rel="stylesheet" href="/static/series.css">'
                           '<link rel="stylesheet" href="/static/covers.css">')
    from webui.html import page_frame
    assets = ("videos", "series") if app else ("videos", "hls", "series")      # hls.js before the page's script
    assets += ("covers",)                                  # (v5.44.123) the menu of covers: a click on the poster
    show = "series" if ref.series is not None else "feed"
    return Response.html(page_frame(title, body, active="videos", subnav=False, kind="video-series",
                                    tabs=VD.tabs_of(req, show), top_search=VD._search_form("", "all"),
                                    body_data={"autonext": model["autonext"]}, assets=assets, **VD._look()))


def _phone_player(ref: Ref, model: dict, entry, data: str, hidden: str) -> Response:
    """The phone's page of an episode: the player with the bar of the page (not the browser's buttons), what plays and
    from where, «Озвучка» and «Субтитры», the episodes before and after, «Следующая серия сама», the episodes."""
    from webui.pages import phone as P
    back = address(ref, phone=True)
    label = f"Серия {entry['label']}" if entry and not model["single"] else model["title"]
    body = (f'<div class="sv sv-phone sv-phone-player" id="series-page"{data}>{_json_tag(model)}'
            f'{own_player(phone=True, skips=not model["single"])}'
            f'<section class="sv-about-ep" data-sv-head><h2 class="sv-ep-name">{esc(label)}</h2></section>'
            f'<div class="sv-under" data-sv-under></div>'
            f'<section class="sv-eps" data-sv-strip aria-label="Серии"></section></div>{hidden}')
    return P.page(f"{label} — {model['title']}", body, active="videos", heading=model["title"] or "Видео", back=back,
                  top="", tabs=False, kind="m-video m-series-player", scripts=("vendor/hls.light.min", "series"),
                  head='<link rel="stylesheet" href="/static/series.css">')
