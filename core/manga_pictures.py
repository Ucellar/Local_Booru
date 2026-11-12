"""(v5.44.81) A title of «Скачать → Из архива» known by its pictures — SauceNAO, the last of the ways of
``core/manga_archive.py``: when neither the folder's files nor its names told what it is (the owner, 5.11.2026: «нужен
не один вариант а несколько если не сработает»).

* The pictures: the folder's cover, else the first pages of its first chapter — :data:`PER_TITLE` at most (each is one
  of the owner's searches of the day); a long strip — its top; sent at most :data:`SIDE` px a side, as JPEG.
* The account as the parser keeps it (``core/parser/reverse/saucenao.py``): the same pause (``service_state``
  «saucenao»: a limit, a failure of the server), the day's quota governor (``core/services/saucenao_quota.py`` — the
  shared throttle ``core/http_rate_limiter.py`` lets the request go or puts it off before it is sent), the answers'
  counters kept the same way.  For the archive — :data:`PER_DAY` searches a day at most (``manga_archive_pictures_
  per_day``), and only while the parser does not run (``core/manga_archive.py`` asks).
* What is taken of the answers: SauceNAO's indexes of manga only (:data:`INDEXES`: Madokami — the names of
  MangaUpdates, MangaDex), :data:`SIMILAR` % alike at least — their ``source`` (the title's name) and their links
  (a chapter of MangaDex: ``core/manga_archive.py`` asks MangaDex for its title).  :attr:`Seen.sure` — the best one
  :data:`SURE` % alike, or two pictures of the folder agreeing: then the program links by itself; else the owner chooses.

The key is never said: not in the journal, not in a file (the address of the request carries it — the page task's
words go through ``core.redaction``; what is kept here is in the program's own words).
"""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from core import manhwa_sources as S

_LOG = logging.getLogger("local_booru.manhwa")

API = "https://saucenao.com/search.php"
HOST = "saucenao.com"
INDEXES = (36, 37, 371)                # Madokami (Manga), MangaDex, MangaDex (its newer index)
PER_TITLE = 2                          # pictures of one title asked at most
PER_DAY = 40                           # searches a day for the archive (``manga_archive_pictures_per_day``)
SIMILAR = 70.0                         # an answer taken at least this alike (%)
SURE = 85.0                            # …this alike — the program links by itself
SIDE = 1200                            # the picture sent at most this big a side
COUNT_FILE = "manga_archive_pictures.json"
_AUTH_WORDS = ("api key", "apikey", "invalid key", "unauthorized", "authentication", "forbidden")
_QUOTA_WORDS = ("limit exceeded", "search limit", "out of searches", "no searches", "quota", "daily limit")
_DAILY_WORDS = ("daily", "24 hour", "long limit", "long_limit")
_COUNT_LOCK = threading.Lock()


@dataclass
class Seen:
    """What SauceNAO said of a folder's pictures: ``names`` — [(a title's name, how alike %)], best first; ``links`` —
    [(a link of MangaDex, how alike %)]; ``asked`` — pictures sent; ``later`` — why it was put off (asked again at
    ``later_at``); ``off`` — why it is not asked at all (no key, switched off)."""
    names: list = field(default_factory=list)
    links: list = field(default_factory=list)
    asked: int = 0
    later: str = ""
    later_at: int = 0
    off: str = ""
    agree: int = 0                      # pictures that named the best name

    @property
    def best(self) -> float:
        return max((sim for _n, sim in [*self.names, *self.links]), default=0.0)

    @property
    def sure(self) -> bool:
        return bool(self.names or self.links) and (self.best >= SURE or self.agree >= 2)


# ── may it be asked ────────────────────────────────────────────────────────────────────────────────────────────────
def key_of(settings: dict) -> str:
    return str((settings or {}).get("saucenao_api_key") or "").strip()


def per_day(settings: dict) -> int:
    try:
        return max(0, min(5000, int((settings or {}).get("manga_archive_pictures_per_day", PER_DAY))))
    except (TypeError, ValueError):
        return PER_DAY


def usable(settings: dict) -> str:
    """"" — SauceNAO may be asked for the archive; else why not, in words."""
    settings = settings or {}
    if not settings.get("manga_archive_pictures", True):
        return "выключено в «Настройки → Манга и манхва»"      # (v5.44.116) the switch moved there from «Из архива»
    if not settings.get("enable_saucenao", True):
        return "SauceNAO выключен в настройках парсера"
    if not key_of(settings):
        return "нет ключа SauceNAO (Настройки → Парсер и сайты)"
    return ""


def _count_file() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / COUNT_FILE


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def asked_today() -> int:
    """The archive's searches of SauceNAO today."""
    from core.file_safety import read_json_kept
    data = read_json_kept(_count_file(), dict, what="manga archive pictures")
    return int(data.get("asked") or 0) if isinstance(data, dict) and data.get("day") == _today() else 0


def _count_one() -> None:
    from core.file_safety import write_json_kept
    with _COUNT_LOCK:
        write_json_kept(_count_file(), json.dumps({"day": _today(), "asked": asked_today() + 1}), dict)


def _tomorrow() -> int:
    now = time.localtime()
    return int(time.mktime((now.tm_year, now.tm_mon, now.tm_mday + 1, 0, 5, 0, 0, 0, -1)))


def _pause(settings: dict) -> tuple:
    try:
        from core.services.service_state import get_cooldown
        state = get_cooldown(settings, "saucenao")
        return int(float(state.get("cooldown_until", 0) or 0)), str(state.get("reason") or "")
    except Exception as exc:                          # noqa: BLE001 - the base not readable: not asked now
        _LOG.info("the archive: SauceNAO's pause not read: %s", type(exc).__name__)
        return int(time.time()) + 600, "состояние не прочитано"


def _set_pause(settings: dict, reason: str, seconds: int | None = None) -> int:
    if seconds is None:
        try:
            seconds = max(60, min(86400, int(settings.get("saucenao_cooldown_seconds", 3600) or 3600)))
        except (TypeError, ValueError):
            seconds = 3600
    until = int(time.time()) + max(60, int(seconds))
    try:
        from core.services.service_state import set_cooldown
        set_cooldown(settings, "saucenao", until, reason=reason)
    except Exception as exc:                          # noqa: BLE001
        _LOG.info("the archive: SauceNAO's pause not kept: %s", type(exc).__name__)
    return until


def _record(settings: dict, header: dict, serial: int) -> None:
    """The account's counters of an answer, kept as the parser keeps them (its pace, the day's quota)."""
    from core.http_rate_limiter import learn_pace
    try:
        short = int(header.get("short_limit"))
        if short >= 2:
            learn_pace(HOST, interval=30.0 / (short - 1), rpm=int((short - 1) * 60 / 30.0))
    except (TypeError, ValueError):
        pass
    short_left, long_left = header.get("short_remaining"), header.get("long_remaining")
    if short_left is None and long_left is None:
        return
    try:
        from core.services.service_state import set_quota_snapshot
        set_quota_snapshot(settings, "saucenao", short_remaining=short_left, long_remaining=long_left,
                           probe_interval_seconds=settings.get("saucenao_quota_probe_interval_seconds", 3600),
                           reserve=settings.get("saucenao_quota_reserve", 10), admission_serial=serial)
    except Exception as exc:                          # noqa: BLE001
        _LOG.info("the archive: SauceNAO's quota not kept: %s", type(exc).__name__)
    try:
        if int(long_left) <= 0:
            _set_pause(settings, "суточный лимит исчерпан")
    except (TypeError, ValueError):
        pass


# ── the pictures ───────────────────────────────────────────────────────────────────────────────────────────────────
def _bytes_of(page) -> bytes:
    try:
        if getattr(page, "pdf_page", -1) >= 0:
            from core.manga_library import render_page
            got = render_page(page)
            return got[0] if got else b""
        return page.read_bytes()
    except Exception as exc:                          # noqa: BLE001 - a page that cannot be read: the next one
        _LOG.info("the archive: a page not read for SauceNAO: %s", type(exc).__name__)
        return b""


def prepared(data: bytes) -> bytes:
    """The picture as it is sent: RGB JPEG at most :data:`SIDE` px a side; a strip (twice as tall as wide at least) —
    its top, one and a half widths tall.  b"" — not a picture."""
    if not data:
        return b""
    try:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            width, height = im.size
            if width < 32 or height < 32:
                return b""
            if height > 2 * width:
                im = im.crop((0, 0, width, int(width * 1.5)))
            im.thumbnail((SIDE, SIDE))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=90)
            return out.getvalue()
    except Exception as exc:                          # noqa: BLE001 - not a picture Pillow reads
        _LOG.info("the archive: a page not made ready for SauceNAO: %s", type(exc).__name__)
        return b""


# ── asking ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def answers(data: dict) -> tuple:
    """The manga of a SauceNAO answer: ([(name, how alike %)], [(a link of MangaDex, how alike %)]) — of
    :data:`INDEXES`, :data:`SIMILAR` % alike at least (or as alike as the answer says is worth it)."""
    header = data.get("header") if isinstance(data.get("header"), dict) else {}
    try:
        floor = max(SIMILAR, float(header.get("minimum_similarity") or 0))
    except (TypeError, ValueError):
        floor = SIMILAR
    names, links = [], []
    for result in data.get("results") or []:
        if not isinstance(result, dict):
            continue
        head = result.get("header") if isinstance(result.get("header"), dict) else {}
        body = result.get("data") if isinstance(result.get("data"), dict) else {}
        try:
            index = int(head.get("index_id"))
            sim = float(head.get("similarity") or 0)
        except (TypeError, ValueError):
            continue
        if index not in INDEXES or sim < floor:
            continue
        for key in ("source", "title", "eng_name"):
            value = body.get(key)
            if isinstance(value, str) and value.strip() and not value.strip().lower().startswith("http"):
                names.append((" ".join(value.split())[:200], sim))
                break
        own = [u for u in body.get("ext_urls") or [] if isinstance(u, str) and "mangadex.org/" in u]
        md = str(body.get("md_id") or "")
        if not own and len(md) >= 32:               # MangaDex's own id of the chapter, when it gives no link
            own = [f"https://mangadex.org/chapter/{md}"]
        links += [(url, sim) for url in own]
    return names, links


def manga_hits(data: dict) -> list:
    """(v5.44.164) The manga of a SauceNAO answer, one a result: [{"name", "part", "link", "sim", "index"}], the best
    first — of :data:`INDEXES`, :data:`SIMILAR` % alike at least (or as alike as the answer says is worth it); ``name``
    — the title's (its ``source``; a result that names none is not one), ``part`` — which chapter of it, in the
    index's own words («- Chapter 12», «Vol.3 Ch.12»), ``link`` — MangaDex's link of the chapter, when it gives one.
    The parser's own search asks every index at once, so its answer about a picture no booru site has says this too
    (``core/parser_loose.py``: pages of manga lying loose among the pictures)."""
    header = data.get("header") if isinstance(data.get("header"), dict) else {}
    try:
        floor = max(SIMILAR, float(header.get("minimum_similarity") or 0))
    except (TypeError, ValueError):
        floor = SIMILAR
    out = []
    for result in data.get("results") or []:
        if not isinstance(result, dict):
            continue
        head = result.get("header") if isinstance(result.get("header"), dict) else {}
        body = result.get("data") if isinstance(result.get("data"), dict) else {}
        try:
            index = int(head.get("index_id"))
            sim = float(head.get("similarity") or 0)
        except (TypeError, ValueError):
            continue
        if index not in INDEXES or sim < floor:
            continue
        name = ""
        for key in ("source", "title", "eng_name"):
            value = body.get(key)
            if isinstance(value, str) and value.strip() and not value.strip().lower().startswith("http"):
                name = " ".join(value.split())[:200]
                break
        if not name:
            continue
        link = next((u for u in body.get("ext_urls") or [] if isinstance(u, str) and "mangadex.org/chapter/" in u), "")
        md = str(body.get("md_id") or "")
        if not link and len(md) >= 32:
            link = f"https://mangadex.org/chapter/{md}"
        part = body.get("part")
        out.append({"name": name, "part": " ".join(str(part).split())[:80] if isinstance(part, (str, int, float)) else "",
                    "link": link, "sim": round(sim, 2), "index": index})
    return sorted(out, key=lambda hit: -hit["sim"])


def _ask(settings: dict, http, path: Path, *, dbs: tuple = INDEXES) -> dict:
    """One picture asked: {"names", "links", "data"} | {"later": why, "until"} | {"off": why} | {"bad": why} |
    {"stopped"}; (v5.44.125) ``dbs`` — the indexes asked (``core/parser_manga``: the manga's and e621's at once),
    ``data`` — SauceNAO's answer as it came (the key is not in it)."""
    from core.http_rate_limiter import SauceNAOQuotaDeferred, take_saucenao_admission_serial
    from core.parser.http import Cancelled, HttpFailure
    params = {"api_key": key_of(settings), "output_type": 2, "numres": 8, "dedupe": 2, "dbs[]": list(dbs)}
    try:
        response = http.post_file(API, path, params=params)
    except SauceNAOQuotaDeferred as exc:
        return {"later": "бережём суточный лимит SauceNAO", "until": int(exc.retry_after or 0) or _tomorrow()}
    except Cancelled:
        return {"stopped": True}
    except HttpFailure:
        return {"later": "SauceNAO не ответил", "until": int(time.time()) + 1800, "sent": True}
    finally:
        serial = take_saucenao_admission_serial()
    status = response.status_code
    try:
        data = response.json()
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    header = data.get("header") if isinstance(data.get("header"), dict) else {}
    if header:
        _record(settings, header, serial)
    if status == 429:
        text = str(header.get("message") or response.text or "").lower()
        daily = any(w in text for w in _DAILY_WORDS)
        try:
            daily = daily or int(header.get("long_remaining")) <= 0
        except (TypeError, ValueError):
            pass
        if daily:
            until, _why = _pause(settings)
            if until <= time.time():
                until = _set_pause(settings, "суточный лимит (HTTP 429)")
            return {"later": "лимит SauceNAO на сегодня", "until": until, "sent": True}
        return {"later": "лимит SauceNAO на полминуты", "until": int(time.time()) + 120, "sent": True}
    if status >= 500:
        return {"later": f"сбой SauceNAO (HTTP {status})",
                "until": _set_pause(settings, f"HTTP {status}", 300), "sent": True}
    if status in (401, 403):
        return {"off": "SauceNAO не принял ключ", "sent": True}
    if status >= 400:
        return {"bad": f"SauceNAO не взял картинку (HTTP {status})", "sent": True}
    if not header:
        return {"later": "SauceNAO ответил не так, как обычно", "until": int(time.time()) + 3600, "sent": True}
    try:
        api_status = int(header.get("status"))
    except (TypeError, ValueError):
        api_status = 0
    low = str(header.get("message") or data.get("message") or "").lower()
    if api_status > 0:
        return {"later": f"сбой SauceNAO ({api_status})",
                "until": _set_pause(settings, f"сбой сервера {api_status}", 300), "sent": True}
    if api_status < 0:
        if any(w in low for w in _AUTH_WORDS):
            return {"off": "SauceNAO не принял ключ", "sent": True}
        if any(w in low for w in _QUOTA_WORDS):
            return {"later": "лимит SauceNAO", "until": _set_pause(settings, "лимит исчерпан"), "sent": True}
        return {"bad": "SauceNAO не взял картинку", "sent": True}
    names, links = answers(data)
    return {"names": names, "links": links, "sent": True, "data": data}


def ask_one(settings: dict, http, data: bytes, *, dbs: tuple = INDEXES) -> dict:
    """(v5.44.125) One picture (its bytes) asked of SauceNAO for ``core/parser_manga`` — within the searches of the day
    knowing manga may take (:func:`per_day`, one count with «Из архива»): :func:`_ask`'s answer, or {"later": …}
    (the day's searches spent, the pause), {"off": …} (no key, switched off), {"bad": …} (not a picture).  The switch
    of «Из архива» (``manga_archive_pictures``) is not this one: the parser's folder has its own (``parser_skip_manga``)."""
    settings = settings or {}
    if not settings.get("enable_saucenao", True):
        return {"off": "SauceNAO выключен в настройках парсера"}
    if not key_of(settings):
        return {"off": "нет ключа SauceNAO («Настройки → Парсер и сайты»)"}
    until, reason = _pause(settings)
    if until > time.time():
        return {"later": f"пауза SauceNAO ({reason or 'после лимита'})", "until": until}
    budget = per_day(settings)
    if asked_today() >= budget:
        return {"later": f"на сегодня поисков SauceNAO хватит ({budget} из {budget})", "until": _tomorrow()}
    picture = prepared(data)
    if not picture:
        return {"bad": "картинка не прочиталась"}
    handle, name = tempfile.mkstemp(prefix="lb_sauce_", suffix=".jpg")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(picture)
        got = _ask(settings, http, Path(name), dbs=dbs)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass
    if got.get("sent"):
        _count_one()
    return got


def look(settings: dict, pages: list, *, stop_check=None, say=None) -> Seen:
    """SauceNAO asked of the folder's ``pages`` (the cover first; :data:`PER_TITLE` at most) — :class:`Seen`."""
    from core.parser.http import HttpClient
    settings = dict(settings or {})
    stop_check = stop_check or (lambda: False)
    say = say or (lambda _text: None)
    seen = Seen()
    why = usable(settings)
    if why:
        seen.off = why
        return seen
    until, reason = _pause(settings)
    if until > time.time():
        seen.later, seen.later_at = f"пауза SauceNAO ({reason or 'после лимита'})", until
        return seen
    budget = per_day(settings)
    if asked_today() >= budget:
        seen.later, seen.later_at = f"на сегодня поисков по картинкам хватит ({budget})", _tomorrow()
        return seen
    http = HttpClient(settings, cancel_check=stop_check)
    best: dict = {}                                   # name_key → [name, how alike, pictures that named it]
    for page in pages:
        if seen.asked >= PER_TITLE or stop_check():
            break
        if asked_today() >= budget:
            seen.later, seen.later_at = f"на сегодня поисков по картинкам хватит ({budget})", _tomorrow()
            break
        picture = prepared(_bytes_of(page))
        if not picture:
            continue
        handle, name = tempfile.mkstemp(prefix="lb_sauce_", suffix=".jpg")
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(picture)
            got = _ask(settings, http, Path(name))
        finally:
            try:
                os.unlink(name)
            except OSError:
                pass
        if got.get("sent"):
            seen.asked += 1
            _count_one()
        if got.get("stopped"):
            break
        if got.get("off"):
            seen.off = got["off"]
            break
        if got.get("later"):
            seen.later, seen.later_at = got["later"], int(got.get("until") or 0) or int(time.time()) + 3600
            break
        if got.get("bad"):
            say(f"  по картинке: {got['bad']}")
            continue
        named = set()
        for title, sim in got.get("names") or []:
            key = S.name_key(title)
            if not key:
                continue
            kept = best.setdefault(key, [title, sim, 0])
            kept[1] = max(kept[1], sim)
            if key not in named:
                kept[2] += 1
                named.add(key)
        for url, sim in got.get("links") or []:
            if all(u != url for u, _s in seen.links):
                seen.links.append((url, sim))
        if any(sim >= SURE for _t, sim in got.get("names") or []):
            break                                     # sure already: the next picture would be a search spent
    ranked = sorted(best.values(), key=lambda x: (-x[2], -x[1]))
    seen.names = [(title, sim) for title, sim, _n in ranked]
    seen.agree = ranked[0][2] if ranked else 0
    seen.links.sort(key=lambda x: -x[1])
    return seen
