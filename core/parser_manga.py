"""(v5.44.125) «Манга через парсер»: the folders of the booru parser that look like a manga — a manga that a site of
manga confirms goes to «Манга» (a copy, linked to its title there), the rest is parsed as the booru's art and becomes one
сборка of the gallery.

The owner (7.11.2026): «оно должно посмотреть по папке если в папке … что то есть то он должен проверить через snao
манга ли это если да то в мангу и достает с страницы манги информацию и теги а если нет то просто в сборку потому что я
раньше часто даже не мангу помечал 1 2 3 так же манга должна собираться в сборку из манг … оно должно работать
автоматически но не давать ложных результатов манги а и еще если нашло мангу оно проверяет вся ли она скачана если нет
докачивает»; «пусть еще учитывает пул e621»; «просто сделай кароче так что бы в мангу проходила только подтвержденная
манга а не рандом без тегов»; and of e621: «пулы в e621 бывают напичканы рандомными картинками так что я говорил что бы
подтвержденная манга была именно с манга сайтов».  His example — «silver soul»: 18 folders of pages saved from VK, 6
manga (13 folders are the parts of one).

* **Candidates** — ``core/manga_detect.py`` (``find``): the names and the make-up of the folders alone, no picture
  opened: pages by numbers (VK's «01-bLEqzb4tFuw.jpg» too), ComicInfo.xml, archives; «М4.» at the start of a name is
  the owner's order; folders of one name with a number at its end — the parts of one manga; a folder of several manga
  — their сборка.  A candidate is not a manga yet.
* **Confirmed** — only by a site of manga of the program (``manhwa_search.ASKED``), never by e621 (a pool of e621 holds
  any pictures: its name is one more name to search by, nothing more) — each manga of a candidate by itself, the ways
  that cost nothing first (:func:`check_work`, :data:`STEPS`):

  1. «files» — its files: ComicInfo.xml (what the downloaders of manga put beside a chapter; one whose «Web» is e621's
     — not: its «Series» is searched by); a link of a title of a site of manga (``core/manga_clues.py``); a link «Из
     архива» made where it lies (``.lb/source.json``, 5.44.84–5.44.124) — (v5.44.127) a link only when the title's
     pages are the folder's (:func:`_by_link`: an old link may be a wrong guess);
  2. «names» — its names: the folder's (without «М4.» and a part's number), ComicInfo's «Series», the names of the pools
     of e621 its pages are in (their exact MD5 on e621) — searched on the sites of manga (``manhwa_search.search_now``;
     :data:`SEARCHES_PER_HOUR` at most — :class:`Pace`), and the pages of the best titles of that name compared with
     the folder's by pHash (:func:`_by_names`): two pages alike, or one and the very name — confirmed; a name alike
     alone never; a name of numbers alone is not searched;
  3. «pictures» — SauceNAO, only when 1 and 2 said nothing: the indexes of manga (36, 37, 371) and e621's (29 — its
     post's pools' names) in one search, :data:`PROBES` pictures at most: the names it gives — the same check by the
     sites' pages; or sure on two pages (:data:`SURE` %) and a title of that name on a site.

  Nothing — **not a manga**: its pages go to the booru sites as before, and its cards become one сборка of the gallery
  in the folder's order (:func:`folder_cards` — ``core/card_sets.py``: a program's сборка, «одна работа»; each manga of a
  folder of several — its own).  No site of manga answering (no network) — **it waits** («ждёт»): its pages are neither
  parsed nor copied, a run does not wait for it, the background asks again (``later_at``).  SauceNAO out of the day's
  searches or paused while the files and the names confirmed nothing — **«not a manga» for now** (how «later»,
  ``sauce_at``): its pages are parsed as any «not a manga», and the background asks SauceNAO when it may (its step
  «pictures» alone): a manga then — copied as any, and the cards the parser made of its pages go to the trash
  («перенесено в мангу», :func:`trash_cards`); nothing — «not a manga» for good.  The sites' searches of the hour
  spent — a run goes on with that folder when the hour lets it (the others first), the background — later.
* **SauceNAO's searches** — one count with «Из архива» (``manga_pictures``: ``manga_archive_pictures_per_day``, 40 a
  day); the parser's governor of the day (``saucenao_quota``) and the pause (``service_state``) are the same; the key only
  in the request.  The pages of a manga confirmed or waiting are not searched by the parser at all.
* **A confirmed manga** is copied into the manga folder (:func:`copy_work`, ``core/comic_manga``'s staging and checked
  copies, its record «root:<the manga's id>»): its parts — its chapters in order («Глава 0» … «Глава 12»), the pages by
  their numbers; ``info.json`` — its name, the site's name of it, how it was confirmed, its link; the manga of one
  folder — one сборка of «Манга» named by the folder (``lb_set`` — ``core/manga_works.py``).  The parser's files are
  only read: never moved, changed or removed.  Its copy is linked to the title that confirmed it as «Из архива» links
  (``.lb/source.json``: its description and tags, the cover — Remanga's first), the chapters it misses go into the queue
  of «Загрузки» (``manhwa_queue``; never a paid one).
* **The program's rule about minors**: e621 says its pages speak of minors and are not rated safe — never taken
  (``core/filter.page_forbidden``: the whole rule is in ``core/filter.py``; the sites keep it themselves:
  ``manhwa_sources``).
* **When**: in a run of the parser (:class:`RunWatch`: a thread beside the files — the pages of a folder found «not a
  manga» join the run, the journal says «МАНГА:», «НЕ МАНГА:», «ЖДЁТ:», «СБОРКА:» in its tab «Манга») and in the
  background (:func:`background_round`, a part of the duty «comic_manga»: what waited, new folders, the copies).  What
  was found — ``parser_manga_found.json`` in the settings folder (:class:`Store`).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from core import comic_manga as CM, filter as F, manga_detect as MD

_LOG = logging.getLogger("local_booru.manga")

STATE_FILE = "parser_manga_found.json"
MANGA, NOT, WAIT = "manga", "not", "wait"
STEPS = ("files", "names", "pictures")
SURE = 85.0                     # a picture's answer this alike (%) is sure (``manga_pictures.SURE``)
MD5_PAGES = 3                   # pages of each part whose MD5 e621 is asked about (the names of their pools)
MD5_MAX = 60                    # …of one manga at most
PROBES = 2                      # pictures of one manga SauceNAO is asked about at most
NAMES = 3                       # names of one manga searched on the sites at most
TILES = 2                       # titles of one name whose pages are compared at most
LOCAL_PAGES = 2                 # pages of the folder compared with a title's
SITE_PAGES = 3                  # …with so many first pages of its chapter
SEARCHES_PER_HOUR = 60          # searches of the sites of manga by a name an hour at most (the background of «Из архива»:
#                                 4 titles of 3 names every ten minutes) — a folder of thousands never floods them
LATER = 30 * 60                 # a check put off (no network): asked again so much later
ROUND_SECONDS = 40              # a round of the background works so long at most (its duty shares one thread)
SAFE = F.SAFE_RATINGS            # (v5.44.128) the program's rule about minors — in core/filter.py

HOW = {"comicinfo": "ComicInfo.xml", "link": "ссылка в файлах", "site": "тайтл на сайте манги",
       "saucenao": "SauceNAO", "hand": "вы сказали", "nothing": "не подтверждено",
       "forbidden": "правило о несовершеннолетних", "off": "проверить нечем", "down": "сайты манги не ответили",
       "later": "SauceNAO — позже"}
# (a «not a manga» for now: the files and the names confirmed nothing, SauceNAO could not be asked — its pages are
# parsed, SauceNAO is asked by the background when it may; a manga then — copied, its cards of the gallery to the trash)
LATER_BY = "SauceNAO спрошу позже: если подтвердит мангу, перенесу в «Мангу»"

# the words of numbers (``core/download_history.plural``)
FOLDERS = ("папка", "папки", "папок")
PICTURES = ("картинка", "картинки", "картинок")
PAGES = ("страница", "страницы", "страниц")
PARTS = ("часть", "части", "частей")
CHAPTERS = ("глава", "главы", "глав")
MANGAS = ("манга", "манги", "манг")


def plural(n: int, forms: tuple) -> str:
    from core.download_history import plural as said
    return said(int(n), forms)


def line(kind: str, text: str) -> str:
    """A line of the parser's journal of this module: «МАНГА: …», «НЕ МАНГА: …», «ЖДЁТ: …», «СБОРКА: …», «МАНГА В
    ПАПКЕ: …» — the tab «Манга» of the page «Парсер» (``core/parser/jobs.channel_of``)."""
    return f"{kind}: {text}"


def enabled(settings: dict) -> bool:
    """«Мангу из исходной папки — проверять и класть в «Мангу»» (the page «Парсер»): off — the parser parses everything
    as it did before 5.44.84."""
    return bool((settings or {}).get("parser_skip_manga", True))


def fill_on(settings: dict) -> bool:
    """«Докачивать недостающие главы» (the page «Парсер»): the chapters the copy misses into «Загрузки»."""
    return bool((settings or {}).get("parser_manga_fill", True))


# ── what was found ──────────────────────────────────────────────────────────────────────────────────────────────────
class Store:
    """``parser_manga_found.json``: {"items": {candidate key: {"root": the parser's folder it is of, "cand": its
    make-up, "at", "seen", "hand", "works": {work key: {"state", "how", "by", "sim", "title", "url", "later_at",
    "why", "copy", "folder", "fill", "site", "pages", "sauce_at" (SauceNAO later), "handed" (its pages gone into a run),
    "cards_to_trash", "cards_trashed", …}}}}} — the folders of every parser's folder looked at (one chosen for a while
    and back again: nothing is asked twice)."""

    def __init__(self):
        self._lock = threading.RLock()
        self.rev = 0                                   # +1 a change (the page asks again)

    def file(self) -> Path:
        from core.paths import SETTINGS_DIR
        return Path(SETTINGS_DIR) / STATE_FILE

    def read(self) -> dict:
        from core.file_safety import read_json_kept
        data = read_json_kept(self.file(), dict, what="parser manga found")
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("items"), dict):
            data["items"] = {}
        return data

    def _write(self, data: dict) -> None:
        from core.file_safety import write_json_kept
        write_json_kept(self.file(), json.dumps(data, ensure_ascii=False, indent=1), dict)
        self.rev += 1

    def items(self, root: str = "") -> dict:
        """The candidates of the parser's folder ``root`` (all of them — "")."""
        data = self.read()
        if not root:
            return data["items"]
        mine = MD._norm(root)
        return {k: v for k, v in data["items"].items() if isinstance(v, dict) and MD._norm(v.get("root") or "") == mine}

    def change(self, fn):
        """``fn(items)`` under the lock, written after."""
        with self._lock:
            data = self.read()
            out = fn(data["items"])
            self._write(data)
            return out

    def register(self, root: str, cands: list, *, whole: bool = False) -> dict:
        """The candidates of the parser's folder ``root`` seen now (their make-up kept, what was found of them stays);
        ``whole`` — a look at the whole folder: its ones it did not see are gone (moved or removed).  Its items."""
        now = int(time.time())
        refused = MD.not_manga()
        mine = MD._norm(root)

        def put(items: dict) -> dict:
            seen = set()
            for cand in cands:
                seen.add(cand.key)
                item = items.setdefault(cand.key, {"at": now, "works": {}})
                item["root"] = str(root)
                made = cand.public()
                if item.get("cand") != made or "sizes" not in item:      # its size — by the names, once a change
                    item["sizes"] = {w.key: size_words(w) for w in cand.works}
                    item["pages"] = sum(len(MD.pages_of(p)) for w in cand.works for p in w.parts if not p.archive)
                item["cand"] = made
                item["seen"] = now
                if cand.refused and item.get("hand") != MANGA:
                    item["hand"] = NOT
                works = item.setdefault("works", {})
                for work in cand.works:
                    rec = works.setdefault(work.key, {"name": work.name, "state": ""})
                    rec["name"] = work.name
                    alone = bool(work.roots) and all(MD._under(Path(r), refused) for r in work.roots)
                    if alone and rec.get("hand") != MANGA:
                        rec["hand"] = NOT                # «Это не манга» of this one manga (of «Из архива»'s row)
                    elif not alone and rec.get("hand") == NOT:
                        rec.pop("hand", None)
                for key in [k for k in works if k not in {w.key for w in cand.works}]:
                    works.pop(key, None)
            if whole:
                for key in [k for k, v in items.items() if k not in seen and MD._norm(v.get("root") or "") == mine]:
                    items.pop(key, None)
            return items
        with self._lock:
            data = self.read()
            put(data["items"])
            self._write(data)
            return {k: v for k, v in data["items"].items() if MD._norm(v.get("root") or "") == mine}

    def put_work(self, cand_key: str, work_key: str, values: dict) -> None:
        def put(items: dict) -> None:
            item = items.get(cand_key)
            if item is None:
                return
            work = item.setdefault("works", {}).setdefault(work_key, {})
            work.update(values)
        self.change(put)


STORE = Store()


def _cand(item: dict) -> "MD.Candidate | None":
    data = item.get("cand") if isinstance(item, dict) else None
    return MD.Candidate.of(data) if isinstance(data, dict) and data.get("key") else None


def state_of(item: dict, work: "MD.Work") -> dict:
    """What was found of one manga of a candidate (the owner's word over the program's)."""
    rec = dict((item.get("works") or {}).get(work.key) or {})
    if item.get("hand") == NOT or rec.get("hand") == NOT:
        rec.update(state=NOT, how="hand", by="вы сказали: «Это не манга»")
    elif item.get("hand") == MANGA and rec.get("state") != MANGA:
        rec.update(state=MANGA, how="hand", by="вы сказали: «Это манга»")
    elif rec.get("state") == MANGA and rec.get("how") == "link" and int(rec.get("link_rule") or 0) < LINK_RULE \
            and not rec.get("copied"):
        rec["state"] = ""                   # (v5.44.127) a link of its files not checked by the pages: checked again
    return rec


# ── what a check asks with ──────────────────────────────────────────────────────────────────────────────────────────
class Asker:
    """e621 (the MD5 of a folder's pages: the names of their pools — names to search by, nothing more), the sites of
    manga (their searches, their titles' pages) and SauceNAO — the run's own e621 in a run, made for the background else
    (:func:`background_asker`)."""

    def __init__(self, settings: dict, *, http, e621=None, stop_check=None):
        self.settings = dict(settings or {})
        self.http = http
        self.e621 = e621
        self.stop_check = stop_check or (lambda: False)
        self._pools: dict = {}
        self._posts: dict = {}
        self._titles: dict = {}             # (a title's link, a chapter) -> its pages' pHashes, its name (a round)
        self._net = None
        self.notes: list = []
        self.down = 0                       # the searches no site of manga answered (no network): a check then waits

    def stopped(self) -> bool:
        try:
            return bool(self.stop_check())
        except Exception:  # noqa: BLE001 - a stop check that breaks: stop
            return True

    # e621
    def e621_ok(self) -> bool:
        return self.e621 is not None and not getattr(self.e621, "disabled_reason", "")

    def md5_posts(self, md5s: list) -> "dict | None":
        """{md5: Post} of the ones e621 has; None — e621 did not answer."""
        from core.parser.http import Cancelled, HttpFailure
        from core.parser.sites import SiteUnavailable
        if not self.e621_ok() or not md5s:
            return {} if self.e621_ok() else None
        out: dict = {}
        try:
            for start in range(0, len(md5s), 50):
                got, _whole = self.e621.search_md5_many(md5s[start:start + 50])
                out.update(got or {})
        except (SiteUnavailable, HttpFailure, Cancelled, OSError, ValueError) as exc:
            self.notes.append(f"e621: {type(exc).__name__}")
            return None
        return out

    def posts(self, ids: list) -> "dict | None":
        """{post id: e621's post} (asked a hundred at a time, kept for the round); None — e621 did not answer."""
        from core.parser.http import Cancelled, HttpFailure
        from core.parser.sites import SiteUnavailable
        wanted = [int(i) for i in dict.fromkeys(ids) if str(i).isdigit() and int(i) not in self._posts]
        if not self.e621_ok():
            return None
        try:
            for start in range(0, len(wanted), 100):
                if self.stopped():
                    return None
                part = wanted[start:start + 100]
                for raw in self.e621.posts_by_ids(part):
                    if isinstance(raw, dict) and str(raw.get("id") or "").isdigit():
                        self._posts[int(raw["id"])] = raw
                for pid in part:
                    self._posts.setdefault(pid, None)
        except (SiteUnavailable, HttpFailure, Cancelled, OSError, ValueError) as exc:
            self.notes.append(f"e621: {type(exc).__name__}")
            return None
        return {int(i): self._posts.get(int(i)) for i in ids if str(i).isdigit()}

    def pools(self, ids: list) -> "dict | None":
        """{pool id: e621's pool} (its name, category, posts in order); None — e621 did not answer."""
        from core.parser.http import Cancelled, HttpFailure
        from core.parser.sites import SiteUnavailable
        wanted = [int(i) for i in dict.fromkeys(ids) if str(i).isdigit() and int(i) not in self._pools]
        if not self.e621_ok():
            return None
        try:
            for start in range(0, len(wanted), 50):
                part = wanted[start:start + 50]
                for raw in self.e621.pools_by_ids(part):
                    if isinstance(raw, dict) and str(raw.get("id") or "").isdigit():
                        self._pools[int(raw["id"])] = raw
                for pid in part:
                    self._pools.setdefault(pid, None)
        except (SiteUnavailable, HttpFailure, Cancelled, OSError, ValueError) as exc:
            self.notes.append(f"e621: {type(exc).__name__}")
            return None
        return {int(i): self._pools.get(int(i)) for i in ids if str(i).isdigit()}

    def pool_names(self, ids: list) -> list:
        """The names of e621's pools ``ids`` (their category any: a name to search the sites of manga by)."""
        got = self.pools(ids) if ids else {}
        return [pool_name(str(raw.get("name") or "")) for raw in (got or {}).values() if raw and raw.get("name")]

    # the sites of manga
    def net(self):
        from core import manhwa_sources as S
        if self._net is None:
            self._net = S.Net(self.stop_check)
        return self._net

    # SauceNAO
    def sauce(self, data: bytes) -> dict:
        from core import manga_pictures as P
        return P.ask_one(self.settings, self.http, data, dbs=(*P.INDEXES, E621_INDEX))


E621_INDEX = 29                 # SauceNAO's index of e621 (its post's pools: names to search by)


def _e621_of(sites: list):
    return next((s for s in sites or [] if getattr(s, "engine", "") == "e621" and s.host == "e621.net"), None)


def run_asker(settings: dict, engine, http, stop_check) -> Asker:
    """The run's own e621 (its pace is the run's)."""
    return Asker(settings, http=http, e621=_e621_of(getattr(engine, "sites", [])), stop_check=stop_check)


_BG: dict = {"key": None, "asker": None}
_BG_LOCK = threading.Lock()


def _settings_key(settings: dict) -> str:
    from core.parser.sites import table_config
    cfg = table_config(settings, "e621.net") or {}
    parts = [cfg.get("enabled"), cfg.get("login"), bool(cfg.get("api_key")),
             bool(str(settings.get("saucenao_api_key") or "").strip()), settings.get("enable_saucenao", True)]
    return hashlib.md5(json.dumps(parts, default=str).encode("utf-8")).hexdigest()


def background_asker(settings: dict, stop_check) -> Asker:
    """The background's asker — made once and kept while the settings it is made of stay."""
    from core.parser.context import ParserContext
    from core.parser.http import HttpClient
    from core.parser.sites import site_for_host, table_config
    key = _settings_key(settings)
    with _BG_LOCK:
        asker = _BG["asker"]
        if asker is None or _BG["key"] != key:
            notes: list = []

            def note(message: str) -> None:
                from core.redaction import sanitize_text
                notes.append(sanitize_text(str(message)))
                del notes[:-20]
            http = HttpClient(settings, log=note, cancel_check=lambda: _BG.get("stop", lambda: False)(),
                              pause_wait=lambda: None)
            ctx = ParserContext(settings, http, note, lambda *_a: None)
            e621 = None
            if (table_config(settings, "e621.net") or {}).get("enabled"):
                e621 = site_for_host(ctx, "e621.net", detect=False)
            asker = Asker(settings, http=http, e621=e621)
            _BG.update(key=key, asker=asker)
        _BG["stop"] = stop_check or (lambda: False)
        asker.settings = dict(settings)
        asker.stop_check = stop_check or (lambda: False)
        asker._pools, asker._posts, asker._titles, asker._net, asker.notes = {}, {}, {}, None, []
        return asker


# ── the pace of the sites of manga ──────────────────────────────────────────────────────────────────────────────────
class Pace(Exception):
    """The searches of the sites of manga of the hour are spent: ``wait`` seconds until the next one."""

    def __init__(self, wait: float):
        super().__init__(f"{float(wait):.0f} s")
        self.wait = float(wait)


_SEARCHES: list = []                    # (monotonic) the searches of the sites of manga of the last hour
_SEARCH_LOCK = threading.Lock()


def search_wait() -> float:
    """Seconds until a search of the sites of manga by a name may go (0 — now)."""
    with _SEARCH_LOCK:
        now = time.monotonic()
        while _SEARCHES and now - _SEARCHES[0] >= 3600:
            _SEARCHES.pop(0)
        if len(_SEARCHES) < max(1, int(SEARCHES_PER_HOUR)):
            return 0.0
        return max(1.0, 3600.0 - (now - _SEARCHES[0]))


def _search_taken() -> None:
    with _SEARCH_LOCK:
        _SEARCHES.append(time.monotonic())


def paced(wait: float) -> "Verdict":
    """The verdict of a manga whose check waits for the sites' searches of the next hour."""
    return Verdict(WAIT, "site", f"сайты манги — не больше {SEARCHES_PER_HOUR} поисков в час, очередь",
                   later_at=int(time.time() + max(60.0, wait)), why="очередь к сайтам манги")


# ── one manga checked ───────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Verdict:
    state: str                          # MANGA | NOT | WAIT ("" — stopped half-way)
    how: str = ""                       # comicinfo | link | site | saucenao | hand | nothing | forbidden | off | down
    #                                     | later («not a manga» for now: SauceNAO asked later — ``sauce_at``)
    by: str = ""                        # in words: how it was told
    sim: float = 0.0
    title: str = ""                     # the title's name on the site (ComicInfo's «Series»)
    url: str = ""                       # the title on a site of manga that confirmed it
    later_at: int = 0
    why: str = ""
    first: str = ""                     # the site's chapter the folder's first part is (its pages compared): the copy's
    #                                     chapters are named by the site's numbers — «Загрузки» fetch only the missing
    sauce_at: int = 0                   # «not a manga» for now: when SauceNAO may be asked again (0 — not to be)

    def record(self) -> dict:
        return {"state": self.state, "how": self.how, "by": self.by, "sim": round(float(self.sim or 0), 1),
                "title": self.title, "url": self.url, "later_at": int(self.later_at or 0), "why": self.why,
                "first": self.first, "sauce_at": int(self.sauce_at or 0), "at": int(time.time())}


def _md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bytes(part: "MD.Part", path: Path) -> bytes:
    """A page's bytes (a page in an archive — its first picture)."""
    if part.archive:
        import zipfile
        try:
            with zipfile.ZipFile(path) as zf:
                names = sorted(n for n in zf.namelist() if Path(n).suffix.lower() in MD.IMAGES)
                return zf.read(names[0]) if names else b""
        except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
            return b""
    try:
        return Path(path).read_bytes()
    except OSError:
        return b""


def pool_name(name: str) -> str:
    from core.comic_manga import pool_name as named
    return named(name)


def _pools_of(post) -> list:
    """The pools a post of e621 is in (a parser's ``Post`` or e621's raw post)."""
    if isinstance(post, dict):
        values = post.get("pools") or []
    else:
        values = str((getattr(post, "meta", {}) or {}).get("pools") or "").split(",")
    return [int(x) for x in values if str(x).strip().isdigit()]


def _tags_of(raw: dict) -> list:
    tags = raw.get("tags") if isinstance(raw, dict) else None
    out = []
    if isinstance(tags, dict):
        for values in tags.values():
            out += [str(v) for v in values or [] if v]
    return out


def forbidden(raws: list) -> bool:
    """The program's rule (``core/filter.page_forbidden``): a page that speaks of minors and is not rated safe — here
    only what e621's posts of the pages say (their tags, their rating)."""
    return any(F.page_forbidden(_tags_of(raw), [str(raw.get("rating") or "").strip().lower()[:1]])
               for raw in raws if isinstance(raw, dict))


def _hits_of_sauce(data: dict) -> tuple:
    """(e621's posts [(id, %)], the manga's names [(name, %)], MangaDex's links [(url, %)]) of SauceNAO's answer
    (each at least its own minimum and 70 %)."""
    from core import manga_pictures as P
    header = data.get("header") if isinstance(data.get("header"), dict) else {}
    try:
        floor = max(P.SIMILAR, float(header.get("minimum_similarity") or 0))
    except (TypeError, ValueError):
        floor = P.SIMILAR
    posts = []
    for result in data.get("results") or []:
        if not isinstance(result, dict):
            continue
        head = result.get("header") if isinstance(result.get("header"), dict) else {}
        body = result.get("data") if isinstance(result.get("data"), dict) else {}
        try:
            index, sim = int(head.get("index_id")), float(head.get("similarity") or 0)
        except (TypeError, ValueError):
            continue
        if index != E621_INDEX or sim < floor:
            continue
        number = str(body.get("e621_id") or "")
        if not number.isdigit():
            for url in body.get("ext_urls") or []:
                found = re.search(r"e(?:621|926)\.net/(?:posts|post/show)/(\d+)", str(url))
                if found:
                    number = found.group(1)
                    break
        if number.isdigit():
            posts.append((int(number), sim))
    names, links = P.answers(data)
    return sorted(posts, key=lambda x: -x[1]), names, links


def _phash(data: bytes) -> str:
    """A picture's pHash as the parser counts it (``tagger/hashing.file_phash``: 512 px, RGB); a strip — its top (a
    page of manhwa against a folder's picture of its start)."""
    if not data:
        return ""
    try:
        import imagehash
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            width, height = im.size
            if height > 2 * width:
                im = im.crop((0, 0, width, int(width * 1.5)))
            im.thumbnail((512, 512))
            return str(imagehash.phash(im))
    except Exception as exc:  # noqa: BLE001 - not a picture Pillow reads (or no imagehash): not compared
        _LOG.debug("parser manga: no pHash: %s", type(exc).__name__)
        return ""


def _alike(a: str, b: str, limit: int) -> bool:
    from core.tagger.hashing import phash_distance
    return bool(a and b) and phash_distance(a, b) <= limit


def _limit(settings: dict) -> int:
    """How alike two pages must be: the parser's own «parser_visual_max_distance» (10 of 64 bits)."""
    try:
        return max(0, min(32, int((settings or {}).get("parser_visual_max_distance", 10))))
    except (TypeError, ValueError):
        return 10


def _local(work: "MD.Work", listed: list) -> list:
    """[(part index, page number, path)] of the folder's pages compared and searched: the first :data:`LOCAL_PAGES` of
    its first part with pages."""
    for index, pages in enumerate(listed):
        if pages:
            return [(index, n, p) for n, p in pages[:LOCAL_PAGES]]
    return []


def _chapter_of(work: "MD.Work", index: int) -> str:
    """The number of the site's chapter a part is (its folder's number: «Сильвер Соул 7» → «7»; a manga of one folder
    named with a number at its end — that number); "" — the title's first chapter."""
    part = work.parts[index] if 0 <= index < len(work.parts) else None
    if part is not None and part.label.startswith(MD.PARTS_WORD + " "):
        return part.label.split(" ", 1)[1].strip()
    _base, number = MD.tail_number(work.name)
    return str(number) if number is not None and len(work.parts) == 1 else ""


def names_of(work: "MD.Work", extra=()) -> list:
    """The names a manga is searched by on the sites: its own (its folder's, without «М4.» and a part's number; with a
    number at its end — without it too) and ``extra`` (ComicInfo's «Series», e621's pools, SauceNAO's answers) — never
    a name that names nothing («Новая папка», «123», «1»); :data:`NAMES` at most."""
    from core import manga_clues as C
    own = [work.name]
    base, number = MD.tail_number(work.name)
    if number is not None:
        own.append(base)
    return [n for n in C.good_names([*own, *extra]) if not re.fullmatch(r"[\W\d_]+", n)][:NAMES]


def _title_pages(asker: Asker, url: str, number: str) -> tuple:
    """(the pHashes of the first :data:`SITE_PAGES` pages of the chapter ``number`` of the title at ``url`` — its first
    free chapter when it has no such one —, the title's name); asked once a round.  ``manhwa_sources.SourceError`` —
    the site gave none (a paid chapter, a sign-in, no answer)."""
    from core import manhwa_sources as S
    kept = asker._titles.get((url, number))
    if kept is not None:
        return kept
    source, key = S.source_for(url)
    if source is None:
        raise S.SourceError("не сайт манги программы")
    net = asker.net()
    title = source.title(net, key)
    chapters = [c for c in source.chapters(net, title) if c.free]
    chosen = next((c for c in chapters if number and S._num(c.number) == S._num(number)), None)
    if chosen is None and chapters:
        chosen = min(chapters, key=lambda c: (c.index, S._number_key(c.number)))
    if chosen is None:
        raise S.SourceError("у тайтла нет бесплатных глав")
    hashes = []
    for pic in source.pictures(net, title, chosen)[:SITE_PAGES]:  # (a paid one — ``Paid``, a SourceError: none)
        if asker.stopped():
            break
        data, _ctype = S.fetch_picture(net, pic, what=source.label,
                                       headers={"Referer": source.referer(title),
                                                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
        mark = _phash(data)
        if mark:
            hashes.append(mark)
    got = (hashes, title.title, source.label, S._num(chosen.number))
    asker._titles[(url, number)] = got
    return got


def _by_names(asker: Asker, work: "MD.Work", listed: list, names: list, *, how: str = "site",
              said: str = "", searched: "set | None" = None) -> tuple:
    """2. ``names`` searched on the sites of manga, the pages of the best :data:`TILES` titles of each compared with the
    folder's (pHash): two pages alike, or one and the very name — (the :class:`Verdict`, its link, its name); none — (None,
    {a name's key: (the link of a title of that name found, its name)}).  :class:`Pace` — the hour's searches spent.
    ``searched`` — the keys of the names searched already (nothing found): not searched again (a check gone on after the
    hour's pace), the ones searched now added."""
    from core import manga_archive as A, manhwa_search as MS, manhwa_sources as S
    local = _local_marks(work, listed)
    found: dict = {}
    if not local:
        return None, found
    limit = _limit(asker.settings)
    chapter = _chapter_of(work, local[0][0])
    for name in names:
        if asker.stopped():
            break
        key = S.name_key(name)
        if searched is not None and key in searched:
            continue
        wait = search_wait()
        if wait:
            raise Pace(wait)
        _search_taken()
        down = asker.down
        try:
            tiles, missed = MS.search_now(name, asker.stop_check, net=asker.net())
        except Exception as exc:  # noqa: BLE001 - the sites' trouble: this name says nothing now
            _LOG.info("parser manga: the sites not asked about %r: %s", name, exc)
            asker.down += 1
            continue
        if not tiles and missed and len(missed) >= len(MS.sources()):
            asker.down += 1                         # no site of manga answered: the name said nothing now
            continue
        mine = S.names_of(name)
        named = [t for t in tiles if mine & S.names_of(*t.names)]
        named.sort(key=lambda t: (A._tile_kind(t) not in ("", "manga"), key not in {S.name_key(n) for n in t.names}))
        for tile in named[:TILES]:
            url = A._best_url(tile)
            if not url:
                continue
            exact = key in {S.name_key(n) for n in tile.names}
            for wanted in ([chapter, ""] if chapter else [""]):     # the part's own chapter, then the title's first
                try:
                    hashes, title_name, label, first = _title_pages(asker, url, wanted)
                except S.SourceError as exc:
                    _LOG.info("parser manga: the pages of %s not had: %s", url, exc)
                    if isinstance(exc, S.Silent) or " не ответил" in str(exc):
                        asker.down += 1                 # the site did not answer at all: not «not alike»
                    found.setdefault(key, (url, tile.names[0] if tile.names else name))
                    break
                found.setdefault(key, (url, title_name or name))
                alike = sum(1 for _i, mark in local if any(_alike(mark, h, limit) for h in hashes))
                if alike >= 2 or (alike >= 1 and exact):
                    return Verdict(MANGA, how, f"{said}«{name}» → тайтл «{title_name}»" + (
                        f" на {label}" if label else "") + f": страницы совпали ({alike} из {len(local)})",
                        title=title_name, url=url, first=first), found
                if not wanted or first != S._num(wanted):
                    break                               # (its first chapter was the one compared)
        if searched is not None and asker.down == down and not asker.stopped():
            searched.add(key)                           # (the sites answered: this name is done)
    return None, found


def _e621_link(url: str) -> bool:
    return (urlparse(str(url or "")).hostname or "").lower().removeprefix("www.") in ("e621.net", "e926.net")


def _local_marks(work: "MD.Work", listed: list) -> list:
    """[(part index, pHash)] of the folder's pages compared with a title's (:func:`_local`)."""
    out = []
    for index, _number, path in _local(work, listed):
        mark = _phash(_bytes(work.parts[index], path))
        if mark:
            out.append((index, mark))
    return out


LINK_RULE = 2                   # (v5.44.127) a link of the folder's files confirms only by the title's pages
REMOVED_RULE = 2                # (v5.44.127) «её копию убрали» said by the rule that forgets the old copies


class Removed(CM.ComicError):
    """(v5.44.127) A copy the rule of 5.44.125 made, taken out of the manga folder by the person: never again."""


def _by_link(asker: Asker, work: "MD.Work", listed: list, url: str, *, said: str) -> "Verdict | None":
    """(v5.44.127) A link of a title of a site of manga in the folder's files — «Из архива»'s ``.lb/source.json`` (the
    versions 5.44.84–5.44.124 wrote it into the parser's folder by the old guesses), a description's, ComicInfo's «Web»,
    a shortcut — confirms only when that title's pages are the folder's (one page alike: the link names the title, as
    the very name does in :func:`_by_names`).  A link alone never (the owner: «если оно как то записалось что это манга и
    оно не будет парсить это пиздец»).  None — not confirmed (the names, SauceNAO go on; no site answering — counted in
    ``asker.down``: it waits)."""
    from core import manhwa_sources as S
    local = _local_marks(work, listed)
    if not local:
        return None
    limit = _limit(asker.settings)
    chapter = _chapter_of(work, local[0][0])
    for wanted in ([chapter, ""] if chapter else [""]):
        try:
            hashes, title_name, label, first = _title_pages(asker, url, wanted)
        except S.SourceError as exc:
            _LOG.info("parser manga: the pages of %s not had: %s", url, exc)
            if isinstance(exc, S.Silent) or " не ответил" in str(exc):
                asker.down += 1
            return None
        alike = sum(1 for _i, mark in local if any(_alike(mark, h, limit) for h in hashes))
        if alike >= 1:
            return Verdict(MANGA, "link", f"{said} → тайтл «{title_name}»" + (f" на {label}" if label else "")
                           + f": страницы совпали ({alike} из {len(local)})", title=title_name, url=url, first=first)
        if not wanted or first != S._num(wanted):
            break
    return None


def _by_files(asker: Asker, cand: "MD.Candidate", work: "MD.Work", listed: "list | None" = None) -> tuple:
    """1. Its own files: ComicInfo.xml, a link of a title of a site of manga, «Из архива»'s link — (the verdict or None,
    the names they give to search by: ComicInfo's «Series», a link's title, the names of e621's pools they link).
    (v5.44.127) A link confirms only by its title's pages (:func:`_by_link`); ComicInfo.xml with no link of a site of
    manga — by itself, as before (only the downloaders of manga write it)."""
    from core import manga_clues as C, manhwa_sources as S
    listed = listed if listed is not None else [MD.pages_of(part) for part in work.parts]
    folders = list(dict.fromkeys([*work.roots, *(p.path for p in work.parts if not p.archive)]))
    names: list = []
    tried: set = set()                                  # the links compared already (one title — once)
    for folder in folders[:6]:
        source = Path(folder) / ".lb" / "source.json"
        if source.is_file():
            try:
                data = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if isinstance(data, dict) and data.get("url") and S.source_for(str(data["url"]))[0] is not None:
                url = str(data["url"])
                tried.add(url)
                found = _by_link(asker, work, listed, url, said="привязка «Из архива» к сайту манги")
                if found is not None:
                    return found, names
                names += [str(data["title"])] if data.get("title") else []
    for folder in folders[:6]:
        path = next((p for p in (Path(folder) / "ComicInfo.xml", Path(folder) / "comicinfo.xml") if p.is_file()), None)
        if path is None:
            continue
        try:
            info = C.comic_info(path.read_bytes()) or {}
        except OSError:
            info = {}
        series = (info.get("series") or [""])[0]
        clue = C.clues(Path(folder))
        url = next((u for u, _w in clue.links if S.source_for(u)[0] is not None), "")
        if not url and any(_e621_link(u) for u in info.get("links") or []):
            names += [series] if series else []         # a ComicInfo.xml of a comic of e621: e621 never confirms
            continue
        if url and url not in tried:                    # its title's pages are the folder's — linked to it
            tried.add(url)
            found = _by_link(asker, work, listed, url, said="ComicInfo.xml в папке и ссылка в ней")
            if found is not None:
                found.how = "comicinfo"
                return found, names
        return Verdict(MANGA, "comicinfo", "ComicInfo.xml в папке", title=series), names
    for folder in folders[:6]:
        clue = C.clues(Path(folder))
        for url, where in clue.links:
            if S.source_for(url)[0] is not None and url not in tried:
                tried.add(url)
                found = _by_link(asker, work, listed, url, said=f"ссылка на тайтл сайта манги в {where}")
                if found is not None:
                    return found, names
        if clue.pools and asker.e621_ok():          # a link of a pool of e621: its name — a name to search by
            names += asker.pool_names([pid for pid, _w in clue.pools])
    return None, names


def _e621_names(asker: Asker, work: "MD.Work", listed: list) -> tuple:
    """The exact MD5 of its first pages on e621 — (the names of the pools they are in: names to search the sites of
    manga by, never a confirmation; whether e621 says they speak of minors and are not rated safe)."""
    if not asker.e621_ok():
        return [], False
    md5s = []
    for index, part_pages in enumerate(listed):
        if work.parts[index].archive:
            continue
        for _number, path in part_pages[:MD5_PAGES]:
            if len(md5s) >= MD5_MAX:
                break
            try:
                md5s.append(_md5_file(path))
            except OSError:
                continue
    found = asker.md5_posts(md5s) if md5s else {}
    if not found:
        return [], False
    ids = [int(getattr(p, "post_id", 0) or 0) for p in found.values() if str(getattr(p, "post_id", "")).isdigit()]
    raws = [r for r in (asker.posts(ids) or {}).values() if r]
    pool_ids = sorted({pid for post in found.values() for pid in _pools_of(post)})
    return asker.pool_names(pool_ids), forbidden(raws)


def _dex_names(asker: Asker, links: list) -> list:
    """The names of the title of MangaDex's chapter SauceNAO gave (its other names)."""
    from core import manga_clues as C, manhwa_sources as S
    mangadex = S.by_name("mangadex")
    for url, _sim in links[:2]:
        chapter = C.mangadex_chapter(url)
        if not chapter or mangadex is None or not mangadex.readable:
            continue
        try:
            target = mangadex.title_of_chapter(asker.net(), chapter)
            key = mangadex.key_of(target) if target else ""
            title = mangadex.title(asker.net(), key) if key else None
        except S.SourceError:
            continue
        if title is not None:
            return [title.title, *title.alt_titles]
    return []


def _by_pictures(asker: Asker, work: "MD.Work", listed: list, searched: set, kept: "dict | None" = None) -> Verdict:
    """3. SauceNAO, only when the files and the names said nothing: the indexes of manga and e621's in one search,
    :data:`PROBES` pictures at most (the second only when the first named something not confirmed): the names it gives
    (a manga's; e621's post — its pools') → :func:`_by_names` (not the ones ``searched`` already); sure on two pages
    (:data:`SURE` %) of one name whose title a site has — confirmed too.  ``kept`` — SauceNAO's answers of this check
    ({a page: its answer}): a check gone on after the hour's pace does not ask SauceNAO twice."""
    from core import manga_clues as C, manhwa_sources as S
    kept = kept if kept is not None else {}
    probes = _local(work, listed)[:PROBES]
    if not probes:
        return Verdict(NOT, "nothing", "в папке нет картинок, которые можно спросить")
    asked, later, off = 0, None, ""
    sure: dict = {}                                 # a name's key -> the pictures that named it SURE % and more
    found: dict = {}                                # a name's key -> (a title of that name on a site, its name)
    for index, _number, path in probes:
        if asker.stopped():
            return Verdict("")
        wait = search_wait()
        if wait:
            raise Pace(wait)                        # (no search of SauceNAO whose answer could not be looked up)
        got = kept.get(str(path))
        if got is None:
            got = asker.sauce(_bytes(work.parts[index], path))
            if "data" in got:
                kept[str(path)] = got
        if got.get("off"):
            off = str(got["off"])
            break
        if got.get("later"):
            later = got
            break
        if got.get("stopped"):
            return Verdict("")
        if got.get("bad"):
            continue
        asked += 1
        posts, names, links = _hits_of_sauce(got.get("data") or {})
        extra = []
        if posts:
            raw = (asker.posts([posts[0][0]]) or {}).get(posts[0][0]) or {}
            extra = asker.pool_names(_pools_of(raw))
        extra += _dex_names(asker, links)
        for name, sim in names:
            if sim >= SURE:
                sure[S.name_key(name)] = sure.get(S.name_key(name), 0) + 1
        new = [n for n in C.good_names([*(n for n, _s in names), *extra]) if S.name_key(n) not in searched][:NAMES]
        if new:
            best = max((s for _n, s in names), default=0.0)
            verdict, seen = _by_names(asker, work, listed, new, how="saucenao", searched=searched,
                                      said=f"SauceNAO ({best:.0f} %) — " if best else "SauceNAO (e621) — ")
            if verdict is not None:
                verdict.sim = best
                return verdict
            found.update({k: v for k, v in seen.items() if k not in found})
        if not names and not extra:
            break                                   # nothing at all on the first: the second would be a search spent
    for key, count in sure.items():
        if count >= 2 and key in found:
            url, title = found[key]
            return Verdict(MANGA, "saucenao", f"SauceNAO — две страницы ≥ {SURE:.0f} % → тайтл «{title}» на сайте "
                                              "манги", sim=SURE, title=title, url=url)
    if later is not None:
        return Verdict(WAIT, "saucenao", str(later.get("later") or "SauceNAO недоступен"),
                       later_at=int(later.get("until") or 0) or int(time.time()) + LATER,
                       why=str(later.get("later") or ""))
    if asked:
        return Verdict(NOT, "nothing", "ни тайтла на сайтах манги, ни ответа SauceNAO, который к нему ведёт")
    return Verdict(NOT, "off", "на сайтах манги по имени не нашлось, а SauceNAO спросить нельзя: " + (
        off or "он выключен"))


def check_work(asker: Asker, cand: "MD.Candidate", work: "MD.Work", *, steps: tuple = STEPS,
               memo: "dict | None" = None, later_not: bool = False) -> "Verdict | None":
    """One manga of a candidate checked by the ways of ``steps`` («files», «names», «pictures» — the module's words) —
    its :class:`Verdict`; None — those steps said nothing (the next ones are to ask); a verdict of no state — stopped
    half-way (nothing kept).  ``memo`` — what the steps of one manga hand on (the names to search by, the names
    searched, SauceNAO's answers): the folders of a run or a round go step by step, the ones that cost nothing first
    for all; a check stopped by :class:`Pace` (the sites' searches of the hour spent) goes on from its step with it.
    A «not» while no site of manga answered a search of it (no network) — it waits instead («down»).  SauceNAO not to
    be asked now (the day's searches spent, its pause) while the sites answered — ``later_not`` (the parser's folder):
    «not a manga» for now (how «later», ``sauce_at``: its pages are parsed, SauceNAO is asked by the background
    later); else (a comic of the gallery) it waits."""
    memo = memo if memo is not None else {}
    listed = memo.get("listed")
    if listed is None:
        listed = memo["listed"] = [MD.pages_of(part) for part in work.parts]
    down = asker.down
    if "files" in steps:
        found, extra = _by_files(asker, cand, work, listed)
        if found is not None:
            return found
        memo.setdefault("extra", []).extend(extra)
        if asker.stopped():
            return Verdict("")
    if "names" in steps:
        if "pools" not in memo:                         # (asked once: a check gone on after the pace asks no more)
            memo["pools"], memo["minors"] = _e621_names(asker, work, listed)
        pools, minors = memo["pools"], memo["minors"]
        if minors:
            return Verdict(NOT, "forbidden", "e621: на страницах — несовершеннолетние, не «safe»: в «Мангу» не беру")
        names = names_of(work, [*memo.get("extra", []), *pools])
        try:
            found, _seen = _by_names(asker, work, listed, names, said="имя ", searched=memo.setdefault("searched", set()))
        finally:
            memo["down"] = memo.get("down", 0) + asker.down - down
            down = asker.down
        if found is not None:
            return found
        if asker.stopped():
            return Verdict("")
    if "pictures" in steps:
        try:
            found = _by_pictures(asker, work, listed, memo.setdefault("searched", set()), memo.setdefault("sauce", {}))
        finally:
            memo["down"] = memo.get("down", 0) + asker.down - down
            down = asker.down
        if memo["down"] and (found.state == WAIT and found.how == "saucenao" or
                             found.state == NOT and found.how in ("nothing", "off")):
            return Verdict(WAIT, "down", "сайты манги не ответили", later_at=int(time.time()) + LATER,
                           why="сайты манги не ответили")
        if later_not and found.state == WAIT and found.how == "saucenao":
            return Verdict(NOT, "later", LATER_BY, sauce_at=int(found.later_at or 0) or int(time.time()) + LATER,
                           why=found.why or found.by)
        return found
    memo["down"] = memo.get("down", 0) + asker.down - down
    return None


def confirm_pages(settings: dict, names: list, pages: list, *, stop_check=None) -> Verdict:
    """(``core/comic_manga``) A comic of the gallery confirmed by a site of manga as a folder is: ``names`` (its pool's,
    its сборка's) searched on the sites and its ``pages`` (the cards' files in its order) compared with the title's;
    then SauceNAO.  :class:`Pace` — the sites' searches of the hour spent."""
    asker = background_asker(settings, stop_check)
    work = MD.Work(key="comic", name=str(names[0]) if names else "", path="", parts=[MD.Part(path="", label="")])
    memo = {"listed": [[(n, Path(p)) for n, p in enumerate(pages, 1)]], "extra": list(names[1:])}
    found = check_work(asker, MD.Candidate(key="comic", path="", name=work.name, works=[work]), work,
                       steps=("names", "pictures"), memo=memo)
    return found if found is not None else Verdict(NOT, "nothing", "не подтверждено")


# ── a confirmed manga: its copy, its description ──────────────────────────────────────────────────────────────────────
def _page_names(listed: list) -> list:
    """[(the page's file, its name in the copy)]: «012.jpg» by its number (a second of one number — «012-2.jpg»),
    a picture of no number — its own name (after the numbered ones)."""
    width = max(3, len(str(max((n for n, _p in listed if n is not None), default=1))))
    used, out = set(), []
    for number, path in listed:
        ext = path.suffix.lower()
        if number is None:
            name = path.name
        else:
            name, k = f"{number:0{width}d}{ext}", 2
            while name.lower() in used:
                name, k = f"{number:0{width}d}-{k}{ext}", k + 1
        used.add(name.lower())
        out.append((path, name))
    return out


_DESCRIPTION = {".json", ".xml", ".txt", ".url", ".webloc"}


def _chapter_label(work: "MD.Work", part: "MD.Part", first: str) -> str:
    """The folder of a part in the copy: its own label — or, the site's chapter of the folder's first part known
    (``first``), the site's number of the chapter the part is («Глава 0» … of the folder against the site's «Глава 1» …:
    the numbers moved by the same step); a manga of one folder — that chapter itself."""
    from core import manhwa_sources as S
    if not first:
        return part.label if len(work.parts) > 1 else ""
    try:
        start = float(first)
    except ValueError:
        return part.label if len(work.parts) > 1 else ""
    if len(work.parts) == 1:
        return f"{MD.PARTS_WORD} {S._num(first)}"
    if not part.label.startswith(MD.PARTS_WORD + " "):
        return part.label
    try:
        own = float(part.label.split(" ", 1)[1])
        base = float(work.parts[0].label.split(" ", 1)[1])
    except (ValueError, IndexError):
        return part.label
    return f"{MD.PARTS_WORD} {S._num(str(own - base + start))}"


def _plan(work: "MD.Work", first: str = "") -> list:
    """[(the parser's file, its place in the copy)] of a manga: each part's pages (a manga of one part — right in the
    copy, else in «Глава N» / its chapter's name), its files of a description, its archives, «Из архива»'s link."""
    plan: list = []
    for part in work.parts:
        if part.archive:
            plan.append((Path(part.path), Path(Path(part.path).name)))
            continue
        label = _chapter_label(work, part, first)
        where = Path(label) if label else Path()
        for path, name in _page_names(MD.pages_of(part)):
            plan.append((path, where / name))
        try:
            files = sorted(os.listdir(part.path))
        except OSError:
            files = []
        for name in files:
            source = Path(part.path) / name
            if Path(name).suffix.lower() in _DESCRIPTION and source.is_file() and name.lower() != "info.json":
                plan.append((source, where / name))
    for root in work.roots[:1]:
        lb = Path(root) / ".lb" / "source.json"
        if lb.is_file():
            plan.append((lb, Path(".lb") / "source.json"))
        info = Path(root) / "info.json"
        if info.is_file():
            plan.append((info, Path("info.json")))
    return plan


def copy_name(work: "MD.Work", verdict: dict) -> str:
    """The copy's name: the manga's own (its folder's, without «М4.» and a part's number) — or, the folder's name
    naming nothing («Скачанное 3», «Новая папка (2)»), the one its confirmation gave (the site's title's, ComicInfo's)."""
    from core.manga_clues import junk_name
    title = str(verdict.get("title") or "").strip()
    try:
        junk = junk_name(work.name)
    except Exception:  # noqa: BLE001 - the words of the names not there: its own name
        junk = False
    return title if junk and title else (work.name or title or Path(work.path).name)


def info_of(cand: "MD.Candidate", work: "MD.Work", verdict: dict) -> dict:
    """``info.json`` of the copy: the manga's name, the site's name of it, how it was confirmed, its link, its сборка
    (``lb_set``); the description, the tags and the cover — the site's, by the link (:func:`link_folder`)."""
    title = verdict.get("title") or ""
    data: dict = {"title": copy_name(work, verdict)}
    if title and title != data["title"]:
        data["alt_titles"] = [title]
    if verdict.get("url"):
        data["source_url"] = verdict["url"]
    data["confirmed"] = {"how": verdict.get("how", ""), "by": verdict.get("by", ""), "at": int(time.time())}
    if cand.is_set:
        data["lb_set"] = {"key": cand.key, "name": cand.name,
                          "place": next((n for n, w in enumerate(cand.works, 1) if w.key == work.key), 0)}
    data["from_parser"] = {"key": work.key, "parts": len(work.parts)}
    return data


def copy_work(settings: dict, cand: "MD.Candidate", work: "MD.Work", verdict: dict, *, stop_check=None,
              deadline: float = 0.0) -> "dict | None":
    """A confirmed manga copied into the manga folder (each file read back and checked; the parser's files only read)
    — {"folder", "name", "pages", "bytes", "key", "new"}; None — cut short (the copy goes on next time).
    ``comic_manga.NoRoom``, ``comic_manga.ComicError``."""
    import shutil
    from core import comic_manga as CM, manga_library as M
    stop_check = stop_check or (lambda: False)
    root = CM._root_of(settings)
    key = f"root:{work.key}"
    record = CM._record(key)
    if record and CM.old_gone(key, record):         # (v5.44.127) a copy of the old rules the owner took out: anew
        CM.forget(key)
        record = {}
    kept = Path(str(record["folder"])) if record.get("folder") and record.get("done", True) else None
    if kept is not None and not kept.is_dir():
        if record.get("done", True) and record.get("pages"):
            raise Removed(f"«{work.name}»: её копию убрали из папки манги — не копирую снова")
        kept = None
    plan = _plan(work, str(verdict.get("first") or ""))
    pages = [t for _s, t in plan if t.suffix.lower() in MD.IMAGES | {".cbz", ".zip", ".pdf"}]
    if not pages:
        raise CM.ComicError(f"«{work.name}»: нет страниц, которые читает папка манги")
    target = kept or CM._staging(root, key)
    size = 0
    for source, place in plan:
        try:
            if not (target / place).is_file():
                size += source.stat().st_size
        except OSError:
            continue
    if size and shutil.disk_usage(root).free - size < CM.KEEP_FREE:
        raise CM.NoRoom(f"«{work.name}» ({size / 1024 ** 3:.1f} ГБ): на диске папки манги останется меньше "
                        f"{CM.KEEP_FREE // 1024 ** 3} ГБ — не копирую")
    copied, count = 0, 0
    for source, place in plan:
        if stop_check() or (deadline and time.monotonic() > deadline):
            return None
        where = target / place
        if not CM._kept_copy(source, where):
            try:
                CM._copy_checked(source, where)
                copied += where.stat().st_size
            except OSError as exc:
                _LOG.warning("parser manga %s: %s not copied: %s", work.name, source.name, exc)
                continue
        if place.suffix.lower() in MD.IMAGES:
            count += 1
    if not any((target / t).is_file() for t in pages):
        if kept is None:
            shutil.rmtree(target, ignore_errors=True)
        raise CM.ComicError(f"«{work.name}»: ни один файл не скопировался")
    CM._write_info(target, info_of(cand, work, verdict))
    name = copy_name(work, verdict)
    fresh = {"title": name, "at": int(time.time()), "kind": "parser", "source": work.path,
             "sources": list(work.roots), "pages": count, "chapters": len(work.parts),
             "confirmed": verdict.get("how", ""), "set": cand.key if cand.is_set else "", "site": verdict.get("url", "")}
    if kept is None:
        folder = CM._place(root, key, target, name, {**record, **fresh})
    else:
        folder = kept
        CM._remember(key, fresh)
    M.LIBRARY.invalidate()
    return {"folder": str(folder), "name": folder.name, "pages": count, "bytes": copied, "key": key,
            "new": kept is None or copied > 0}


def _entry_of(key: str, name: str, folder: Path, chapters: int):
    """A copy in the manga folder as a title of «Из архива» (``manga_archive.Entry``): its link, its counts."""
    from core import manga_archive as A, manhwa_download as D
    info = A._info_of(folder)
    linked = D.read_source(folder) if (folder / D.LB_DIR / D.SOURCE_FILE).is_file() else {}
    title = str(info.get("title") or "").strip() or name
    return A.Entry(key=key, kind="manga", lib_id="", path=str(folder), title=title,
                   names=A.names_for(title, folder, info), chapters=max(1, int(chapters or 1)), pages=0,
                   linked=linked if linked.get("site") else {}, folder=True, hentai=A._hentai(info, linked),
                   library="manga", where=folder.name, why="из папки парсера")


def link_folder(settings: dict, key: str, name: str, folder: Path, url: str, *, how: str, by: str,
                chapters: int = 1, stop_check=None, say=None) -> dict:
    """A copy in the manga folder linked to the title of the site that confirmed it (``manga_archive.link`` — its
    description and tags, the cover, Remanga's first), the counts kept for «Из архива»; the chapters it misses — into the
    queue of «Загрузки» (``manhwa_queue``, «Докачивать недостающие главы»; a paid one never).  ``key`` — its key of «Из
    архива» («root:<id>» — a folder of the parser's, «comic:<key>» — a comic of the gallery).
    {"counts", "queued", "error"}."""
    from core import manga_archive as A, manhwa_download as D, manhwa_sources as S
    say = say or (lambda _text: None)
    entry = _entry_of(key, name, folder, chapters)
    out = {"counts": {}, "queued": 0, "error": ""}
    try:
        if not (entry.linked and entry.linked.get("url")):
            done = A.link(settings, entry, url, net=S.Net(stop_check), say=lambda text: say("  " + text))
            out["counts"] = done["counts"]
        else:
            out["counts"] = A.refresh_counts(settings, entry, net=S.Net(stop_check))
    except (A.LinkError, D.DownloadError, S.SourceError) as exc:
        out["error"] = str(exc)
        return out
    A.STORE.put(entry.key, {"state": "linked", "path": str(folder), "counts": out["counts"],
                            "how": "pictures" if how == "saucenao" else "link", "by": by, "tried": [], "later_at": 0,
                            "why": ""})
    if fill_on(settings) and int(out["counts"].get("missing") or 0) > 0:
        entry = _entry_of(key, name, folder, chapters)
        out["queued"] = A.queue_missing(settings, [entry], say=lambda text: say("  " + text))
    return out


def link_copy(settings: dict, work: "MD.Work", folder: Path, url: str, verdict: dict, *, stop_check=None,
              say=None) -> dict:
    """:func:`link_folder` of a copy of a folder of the parser's."""
    return link_folder(settings, f"root:{work.key}", work.name, folder, url, how=str(verdict.get("how") or ""),
                       by=str(verdict.get("by") or ""), chapters=len(work.parts), stop_check=stop_check, say=say)


def _spans(numbers: list) -> str:
    """«14–15», «3, 7–9»."""
    numbers = sorted(set(numbers))
    out, start, prev = [], None, None
    for n in numbers:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}–{prev}" if prev != start else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}–{prev}" if prev != start else str(start))
    return ", ".join(out)


# ── the gallery: a folder found «not a manga» — one сборка ─────────────────────────────────────────────────────────
def not_works(items: dict) -> list:
    """[(candidate, work)] found «not a manga» (or said so by the owner)."""
    out = []
    for item in items.values():
        cand = _cand(item)
        if cand is None:
            continue
        for work in cand.works:
            if state_of(item, work).get("state") == NOT and work.parts:
                out.append((cand, work))
    return out


def folder_cards(con) -> list:
    """(``core/card_sets.py``) [(image id, its сборка's key «dir:<id>», its place)] — the cards the parser made of the
    pages of a folder found «not a manga», in the folder's order (its parts in turn, the pages by their numbers)."""
    try:
        items = STORE.items()
    except Exception as exc:  # noqa: BLE001 - what was found unreadable: no such сборки this time
        _LOG.info("parser manga: not read for the сборки: %s", exc)
        return []
    wanted: dict = {}                                   # the source file -> (key, place)
    for _cand, work in not_works(items):
        place = 0
        for part in work.parts:
            for _number, path in MD.pages_of(part):
                place += 1
                wanted[str(Path(path))] = (f"dir:{work.key}", place)
    if not wanted:
        return []
    out = []
    paths = list(wanted)
    for start in range(0, len(paths), 500):
        part = paths[start:start + 500]
        marks = ",".join("?" * len(part))
        try:
            rows = con.execute(f"SELECT original_path, image_id FROM source_originals WHERE original_path IN ({marks}) "
                               f"AND image_id > 0", part).fetchall()
        except Exception as exc:  # noqa: BLE001 - an old base: no such сборки
            _LOG.debug("parser manga: source_originals not read: %s", exc)
            return []
        for path, image_id in rows:
            key, place = wanted[str(path)]
            out.append((int(image_id), key, place))
    return out


def signature() -> list:
    """What makes the сборки be built again: the folders found «not a manga» changed."""
    try:
        items = STORE.items()
    except Exception:  # noqa: BLE001
        return [0, 0]
    keys = sorted(w.key for _c, w in not_works(items))
    return [len(keys), int(hashlib.md5("|".join(keys).encode("utf-8")).hexdigest()[:8], 16)]


def _set_said(settings: dict, work: "MD.Work") -> tuple:
    """(the cards of a folder found «not a manga», those in Брак, the сборка's id, a card of it) — for the journal and
    the page."""
    from core.database.connection import db
    paths = [str(Path(p)) for part in work.parts for _n, p in MD.pages_of(part)]
    if not paths:
        return 0, 0, 0, 0
    found, brak, set_id, card = 0, 0, 0, 0
    try:
        with db(settings, readonly=True) as con:
            for start in range(0, len(paths), 500):
                part = paths[start:start + 500]
                marks = ",".join("?" * len(part))
                for _path, image_id, bucket, deleted in con.execute(
                        f"SELECT o.original_path, o.image_id, i.bucket, i.deleted FROM source_originals o JOIN images i "
                        f"ON i.id=o.image_id WHERE o.original_path IN ({marks})", part):
                    if int(deleted or 0):
                        continue
                    if str(bucket or "") == "no_match":
                        brak += 1
                    else:
                        found += 1
                        card = card or int(image_id)
                        if not set_id:
                            row = con.execute("SELECT set_id FROM card_set_members WHERE image_id=?",
                                              (int(image_id),)).fetchone()
                            set_id = int(row[0]) if row else 0
                            if set_id:
                                card = int(image_id)
    except Exception as exc:  # noqa: BLE001 - the base busy: said next time
        _LOG.info("parser manga: the сборка of %s not counted: %s", work.name, exc)
    return found, brak, set_id, card


# ── what is done with a verdict ─────────────────────────────────────────────────────────────────────────────────────
def _numbers_words(work: "MD.Work") -> tuple:
    """(the pictures, «номера 1–24») of a manga's pages."""
    numbers, count = [], 0
    for part in work.parts:
        listed = MD.pages_of(part)
        count += len(listed)
        numbers += [n for n, _p in listed if n is not None]
    return count, (f"номера {_spans(numbers)}" if numbers and len(work.parts) == 1 else
                   plural(len(work.parts), PARTS) if len(work.parts) > 1 else "")


def size_words(work: "MD.Work") -> str:
    """«13 частей · 781 стр.», «24 картинки»."""
    pages = sum(len(MD.pages_of(p)) for p in work.parts if not p.archive)
    if len(work.parts) > 1:
        tomes = all(re.match(r"^(?:том|vol|volume|tome)\b", p.label, re.I) for p in work.parts if p.label)
        chapters = all(MD.chapter_like(p.label) for p in work.parts if p.label) and not MD.side_parts(work)
        word = (("том", "тома", "томов") if tomes else CHAPTERS if chapters else PARTS)
        return f"{plural(len(work.parts), word)} · {pages} стр."
    return f"{pages} стр."


def apply(settings: dict, asker: Asker, cand: "MD.Candidate", work: "MD.Work", verdict: Verdict, *, say=None,
          said=None, stop_check=None, deadline: float = 0.0, extra: "dict | None" = None) -> dict:
    """A verdict kept, and done: a manga — copied and linked to its title; not a manga — its pages are the parser's.
    The record kept (with ``extra``: «handed» — its pages gone into a run, «cards_to_trash»); ``said(rec)`` — told
    before the copy is made (the journal: what it is first, then its copy)."""
    say = say or (lambda _text: None)
    said = said or (lambda _rec: None)
    rec = verdict.record()
    rec["link_rule"] = LINK_RULE                        # (v5.44.127) a link of its files was checked by the pages
    rec.update(extra or {})
    STORE.put_work(cand.key, work.key, rec)
    said(rec)
    if verdict.state != MANGA:
        return rec
    return place(settings, asker, cand, work, rec, say=say, stop_check=stop_check, deadline=deadline)


def place(settings: dict, asker: Asker, cand: "MD.Candidate", work: "MD.Work", rec: dict, *, say=None,
          stop_check=None, deadline: float = 0.0) -> dict:
    """A confirmed manga's copy made (or gone on with), linked to the title that confirmed it, the chapters it misses
    queued."""
    from core import comic_manga as CM
    say = say or (lambda _text: None)
    try:
        got = copy_work(settings, cand, work, rec, stop_check=stop_check, deadline=deadline)
    except CM.NoRoom as exc:                            # the disk freed — copied by the next round
        STORE.put_work(cand.key, work.key, {"copy_error": str(exc), "copy_retry_at": int(time.time())})
        say(line("МАНГА", str(exc)))                    # (its words name the manga)
        return {**rec, "copy_error": str(exc), "no_room": str(exc)}
    except CM.ComicError as exc:                        # its copy removed by the person, nothing to copy: never again
        STORE.put_work(cand.key, work.key, {"copy_error": str(exc), "copy_retry_at": 0,
                                            "copy_removed": REMOVED_RULE if isinstance(exc, Removed) else 0})
        say(line("МАНГА", str(exc)))
        return {**rec, "copy_error": str(exc)}
    except OSError as exc:                              # a file that did not read, a folder busy: later
        STORE.put_work(cand.key, work.key, {"copy_error": str(exc), "copy_retry_at": int(time.time()) + LATER})
        say(line("МАНГА", f"«{work.name}»: копия не сделалась ({exc}) — попробую позже"))
        return {**rec, "copy_error": str(exc)}
    if got is None:
        return rec                                      # cut short: the next time goes on
    values = {"copy": got["key"], "folder": got["name"], "copied": True, "copy_error": "", "copy_retry_at": 0,
              "pages": got["pages"]}
    rec["fresh_copy"] = bool(got.get("new"))
    if rec.get("cards_to_trash"):                       # confirmed after its pages were parsed: their cards go
        trashed = trash_cards(settings, work, got["name"])
        STORE.put_work(cand.key, work.key, {"cards_to_trash": False, "cards_trashed": trashed})
        values["cards_trashed"] = trashed
        rec["cards_to_trash"] = False
        say(line("МАНГА", f"«{work.name}» — SauceNAO подтвердил позже → в «Мангу»" + (
            f"; {plural(trashed, PICTURES)} из галереи — в корзину (можно вернуть)" if trashed else "")))
    if rec.get("url"):
        linked = link_copy(settings, work, Path(got["folder"]), rec["url"], rec, stop_check=stop_check, say=say)
        counts = linked.get("counts") or {}
        values["site"] = {"counts": counts, "queued": linked.get("queued", 0), "error": linked.get("error", "")}
        if counts.get("missing") and linked.get("queued"):
            values["fill"] = {"state": "queued", "missing": int(counts["missing"]),
                              "words": f"на сайте ещё {plural(int(counts['missing']), CHAPTERS)}, которых нет в папке"}
            say(line("МАНГА", f"«{work.name}» — на сайте ещё {plural(int(counts['missing']), CHAPTERS)}, которых нет "
                              f"в папке → докачиваю (видно в «Загрузках»)"))
        elif counts:
            values["fill"] = {"state": "done", "missing": int(counts.get("missing") or 0),
                              "words": "все главы на месте" if not counts.get("missing") else "докачка выключена"}
    STORE.put_work(cand.key, work.key, values)
    rec.update(values)
    return rec


def trash_cards(settings: dict, work: "MD.Work", name: str) -> int:
    """The gallery's cards the parser made of a manga's pages (``source_originals``) — to the trash as a comic's are
    («перенесено в мангу»: «Вернуть» brings each back); never a card of a сборка the person made or changed, never a
    file of the parser's folder (the cards' own copies in the library go).  How many went."""
    from core import comic_manga as CM
    from core.database.connection import db
    from core.library_lifecycle import move_to_trash
    paths = [str(Path(p)) for part in work.parts if not part.archive for _n, p in MD.pages_of(part)]
    if not paths:
        return 0
    rows: list = []
    try:
        with db(settings, readonly=True) as con:
            mine = {int(r[0]) for r in con.execute(
                "SELECT m.image_id FROM card_set_members m JOIN card_sets s ON s.set_id=m.set_id "
                "WHERE s.kind='manual' OR s.locked=1")}
            ids: set = set()
            for start in range(0, len(paths), 500):
                part = paths[start:start + 500]
                marks = ",".join("?" * len(part))
                ids |= {int(r[0]) for r in con.execute(
                    f"SELECT image_id FROM source_originals WHERE original_path IN ({marks}) AND image_id > 0", part)}
            wanted = sorted(ids - mine)
            for start in range(0, len(wanted), 800):
                part = wanted[start:start + 800]
                marks = ",".join("?" * len(part))
                rows += [dict(r) for r in con.execute(f"SELECT * FROM images WHERE id IN ({marks}) AND deleted=0",
                                                      part)]
    except Exception as exc:  # noqa: BLE001 - the base busy: the cards stay (the person may trash them)
        _LOG.warning("parser manga: the cards of %s not read: %s", work.name, exc)
        return 0
    root = str((settings or {}).get("root") or "").strip()
    if root:                                            # (the parser's own files are never moved)
        rows = [r for r in rows if not MD._under(Path(str(r.get("path") or "")).parent, {MD._norm(root)})]
    if not rows:
        return 0
    result = move_to_trash(settings, rows, reason=CM.REASON, tag_or_source=str(name)[:200])
    if result.get("error"):
        _LOG.warning("parser manga: the cards of %s not trashed: %s", work.name, result["error"])
        return 0
    return int(result.get("trashed_records") or 0)


# ── the run of the parser ──────────────────────────────────────────────────────────────────────────────────────────
def _route(files: list, cands: list) -> dict:
    """{a file: (candidate, work)} of the files under a manga's folders."""
    roots: dict = {}
    for cand in cands:
        for work in cand.works:
            for root in work.roots:
                roots[MD._norm(root)] = (cand, work)
    out: dict = {}
    for path in files:
        node = MD._norm(Path(path).parent)
        while True:
            hit = roots.get(node)
            if hit is not None:
                out[path] = hit
                break
            parent = os.path.dirname(node)
            if parent == node:
                break
            node = parent
    return out


def detect(root: Path, files: list) -> list:
    """The candidates of a run's files (the names alone)."""
    folders = MD.folders_of(files)
    return MD.find(folders, Path(root), MD.not_manga(), linked=MD.linked_here(folders))


class RunWatch:
    """The manga of a run of the parser: which files are held back (a manga, a folder waiting, one to check), the
    thread that checks them beside the files (:meth:`start`) — a folder found «not a manga» joins the run
    (:meth:`take`), a manga is copied; after the run — the сборки of the gallery said (:meth:`finish`)."""

    def __init__(self, settings: dict, root: Path, log):
        self.settings = dict(settings)
        self.root = Path(root)
        self.log = log
        self.kept: list = []
        self.todo: list = []                # [(cand, work)] to check
        self.places: list = []              # [(cand, work)] confirmed, their copy not made yet
        self.held: dict = {}                # work key -> [files]
        self.parsed: dict = {}              # work key -> (cand, work): not a manga, its files in this run
        self.fresh: dict = {}               # …found «not a manga» in this run (the journal's «СБОРКА:»)
        self._sets_said: set = set()
        self._out: list = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._deciding = threading.Event()
        self._thread: "threading.Thread | None" = None
        self._runner_stopped = lambda: False
        self._open: dict = {}               # work key -> (cand, work): not decided yet
        self._memos: dict = {}              # work key -> what its steps handed on (``check_work``'s memo)
        self._files_done = threading.Event()        # the run's own files are done: only these checks are left
        self._left_said = False

    # what the run asks
    def take(self) -> list:
        with self._lock:
            out, self._out = self._out, []
        return out

    def busy(self) -> bool:
        """Checks still going: the run's files may still grow."""
        return self._deciding.is_set() and self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        self._stop.set()

    def stopped(self) -> bool:
        return self._stop.is_set() or bool(self._runner_stopped())

    def start(self, engine, http, runner_stopped=None) -> None:
        if not self.todo and not self.places:
            return
        self._runner_stopped = runner_stopped or (lambda: False)
        self._deciding.set()
        self._thread = threading.Thread(target=self._work, args=(engine, http), name="parser-manga", daemon=True)
        self._thread.start()

    def join(self, timeout: float = 30.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _release(self, work: "MD.Work", cand: "MD.Candidate") -> None:
        files = self.held.pop(work.key, [])
        with self._lock:
            self._out += files
        self.parsed[work.key] = (cand, work)
        self.fresh[work.key] = (cand, work)

    def files_done(self) -> None:
        """(the runner) The run's own files are done, only the checks of its manga are left: said once (the watch's
        thread says it — it knows what is left)."""
        self._files_done.set()

    def _step(self, asker: Asker, cand: "MD.Candidate", work: "MD.Work", step: str) -> "Verdict | None":
        """One step of one manga's check (:class:`Pace` — the hour's searches spent: it goes on later from there)."""
        try:
            return check_work(asker, cand, work, steps=(step,), memo=self._memos.setdefault(work.key, {}),
                              later_not=True)
        except Pace:
            raise
        except Exception as exc:  # noqa: BLE001 - one manga's trouble: the others go on, it waits
            _LOG.warning("parser manga: %s not checked: %s: %s", work.name, type(exc).__name__, exc)
            return Verdict(WAIT, "", f"проверка сломалась: {type(exc).__name__}", later_at=int(time.time()) + LATER)

    def _decided(self, asker: Asker, cand: "MD.Candidate", work: "MD.Work", verdict: Verdict) -> None:
        """A manga's verdict kept and done: «not a manga» — its pages join the run (marked «handed»)."""
        extra = {"handed": int(time.time())} if verdict.state == NOT else None
        rec = apply(self.settings, asker, cand, work, verdict, say=self.log, stop_check=self.stopped, extra=extra,
                    said=lambda r, cand=cand, work=work: self._said(cand, work, r))
        if rec.get("state") == NOT:
            self._release(work, cand)
        with self._lock:
            self._open.pop(work.key, None)
        if cand.is_set:
            self._set_line(cand)

    def _nap(self, seconds: float) -> None:
        """Waits for the sites' searches of the next hour — a little at a time (a stop ends it at once)."""
        end = time.monotonic() + max(0.2, min(float(seconds), 30.0))
        while time.monotonic() < end and not self.stopped():
            self._tell_left()
            time.sleep(0.2)

    def _tell_left(self) -> None:
        """«МАНГА В ПАПКЕ: файлы пропарсены, проверяю, манга ли, ещё N папок …» — once, when the run's own files are
        done and checks are left."""
        if self._left_said or not self._files_done.is_set():
            return
        from core import manhwa_sources as S
        with self._lock:
            left = list(self._open.values())
        if not left:
            return
        self._left_said = True
        needed = 0                                      # the searches of the sites they may still take
        for _cand, work in left:
            memo = self._memos.get(work.key) or {}
            searched = memo.get("searched") or set()
            names = [n for n in names_of(work, memo.get("extra") or []) if S.name_key(n) not in searched]
            needed += max(1, len(names))
        with _SEARCH_LOCK:
            now = time.monotonic()
            used = sum(1 for t in _SEARCHES if now - t < 3600)
        hour = max(1, int(SEARCHES_PER_HOUR))
        more = max(0, needed - max(0, hour - used))
        until = time.time() + max(300.0, more * 3600.0 / hour)
        when = time.strftime("%H:%M" if time.localtime(until).tm_yday == time.localtime().tm_yday else "%d.%m %H:%M",
                             time.localtime(until))
        self.log(line("МАНГА В ПАПКЕ", f"файлы пропарсены, проверяю, манга ли, ещё {plural(len(left), FOLDERS)} "
                                       f"(сайты манги — не больше {hour} поисков в час), примерно до {when} — можно "
                                       "остановить: доделает фон"))

    def _work(self, engine, http) -> None:
        asker = run_asker(self.settings, engine, http, self.stopped)
        try:
            for cand, work in self.places:
                if self.stopped():
                    return
                item = STORE.items().get(cand.key) or {}
                place(self.settings, asker, cand, work, state_of(item, work), say=self.log, stop_check=self.stopped)
            told: set = set()
            with self._lock:
                self._open = {work.key: (cand, work) for cand, work in self.todo}
            todo = list(self.todo)
            paced: list = []                            # [(the step it got to, cand, work)]: the hour's searches spent
            for index, step in enumerate(STEPS):        # the ways that cost nothing first for all, then the sites, SauceNAO
                left = []
                for cand, work in todo:
                    if self.stopped():
                        return
                    self._tell_left()
                    if cand.key not in told:
                        told.add(cand.key)
                        for text in notes_of(cand):
                            self.log(line("МАНГА", text))
                    try:
                        verdict = self._step(asker, cand, work, step)
                    except Pace:
                        paced.append((index, cand, work))   # (the ones not held by the pace go first)
                        continue
                    if verdict is None:
                        left.append((cand, work))
                        continue
                    if not verdict.state:
                        return                          # stopped half-way: nothing kept
                    self._decided(asker, cand, work, verdict)
                todo = left
            while paced:                                # the hour's searches spent: they go on when it lets them
                if self.stopped():
                    return
                self._tell_left()
                wait = search_wait()
                if wait > 0:
                    self._nap(wait)
                    continue
                index, cand, work = paced[0]
                verdict = None
                try:
                    for at in range(index, len(STEPS)):
                        paced[0] = (at, cand, work)
                        verdict = self._step(asker, cand, work, STEPS[at])
                        if verdict is not None:
                            break
                except Pace:
                    continue                            # (the next hour's turn: from the step it got to)
                paced.pop(0)
                if verdict is None:
                    continue
                if not verdict.state:
                    return
                self._decided(asker, cand, work, verdict)
        except Exception as exc:  # noqa: BLE001 - the run goes on without its manga: they wait for the background
            _LOG.warning("parser manga: the run's checks broke: %s: %s", type(exc).__name__, exc)
            self.log(line("МАНГА", f"проверка манги прервалась ({type(exc).__name__}) — доделает фон"))
        finally:
            self._deciding.clear()

    def _said(self, cand: "MD.Candidate", work: "MD.Work", rec: dict) -> None:
        state = rec.get("state")
        inside = f" (из «{cand.name}»)" if cand.is_set else ""
        if state == MANGA:
            where = "описание, теги и главы — со страницы тайтла" if rec.get("url") else ""
            self.log(line("МАНГА", f"«{work.name}»{inside} — {size_words(work)}: {rec.get('by')} → в «Мангу»"
                                   + (f"; {where}" if where else "")))
        elif state == NOT:
            count, numbers = _numbers_words(work)
            head = f"«{work.name}»{inside} ({plural(count, PICTURES)}" + (f", {numbers}" if numbers else "") + ")"
            if rec.get("how") == "later":               # SauceNAO not to be asked now: parsed, asked later
                self.log(line("НЕ МАНГА", f"{head} — пока на booru-сайты, потом сборкой; {LATER_BY}"))
            else:
                self.log(line("НЕ МАНГА", f"{head}: {rec.get('by')} → на booru-сайты, потом сборкой"))
        elif state == WAIT:
            when = time.strftime("%d.%m %H:%M", time.localtime(int(rec.get("later_at") or 0))) \
                if rec.get("later_at") else "позже"
            self.log(line("ЖДЁТ", f"«{work.name}»{inside} — {rec.get('by')} — проверю {when}"))

    def _set_line(self, cand: "MD.Candidate") -> None:
        """A сборка's line, once all its manga are decided (once a run)."""
        if cand.key in self._sets_said:
            return
        item = STORE.items().get(cand.key) or {}
        states = [state_of(item, w).get("state") for w in cand.works]
        if any(s not in (MANGA, NOT, WAIT) for s in states):
            return
        self._sets_said.add(cand.key)
        manga = [w for w in cand.works if state_of(item, w).get("state") == MANGA]
        if len(manga) >= 2:
            folders = sum(len(w.parts) for w in cand.works)
            self.log(line("МАНГА", f"«{cand.name}» — {plural(folders, FOLDERS)} → {plural(len(manga), MANGAS)} в "
                                   f"«Манге», одной сборкой «{cand.name}»"))

    def finish(self) -> None:
        """After the run: the folders found «not a manga» in it — their сборки of the gallery built and said."""
        if not self.fresh:
            return
        try:
            from core import card_sets
            card_sets.rebuild(self.settings, wait=30.0)
        except Exception as exc:  # noqa: BLE001 - the duty «card_sets» builds them later
            _LOG.info("parser manga: the сборки not built now: %s", exc)
        for _cand, work in list(self.fresh.values())[:200]:
            found, brak, set_id, _card = _set_said(self.settings, work)
            if found >= 2 and set_id:
                words = plural(found, ("найденная картинка", "найденные картинки", "найденных картинок"))
                self.log(line("СБОРКА", f"«{work.name}» — {words} одной сборкой в галерее"
                                        + (f" ({brak} — в Браке)" if brak else "")))
            elif found + brak:
                self.log(line("СБОРКА", f"«{work.name}» — найдено {found}, в Браке {brak}: сборка соберётся, "
                                        "когда в галерее будет две картинки и больше"))


def notes_of(cand: "MD.Candidate") -> list:
    """What the names of a candidate's folders were taken for (the journal's words, once a run)."""
    out = []
    example = ""
    for work in cand.works:
        for part in work.parts[:1]:
            for _n, path in MD.pages_of(part)[:1]:
                if MD.vk_name(Path(path).stem):
                    example = Path(path).name
        if example:
            break
    if example:
        out.append(f"«{cand.name}» — страницы «{example}» — номер, чёрточка, буквы (так сохраняет ВК): номер — это "
                   "страница")
    prefixes = list(dict.fromkeys(MD.order_prefix(Path(r).name) for w in cand.works for r in w.roots
                                  if MD.order_prefix(Path(r).name)))
    if prefixes:
        out.append(f"«{cand.name}» — " + ", ".join(f"«{p}»" for p in prefixes[:4])
                   + " в начале имён папок — порядок, не название")
    for work in cand.works:
        if MD.side_parts(work):                 # (a title's own chapter folders are no «одно название с номером»)
            first, last = Path(work.parts[0].path).name, Path(work.parts[-1].path).name
            out.append(f"«{cand.name}» — «{first}» … «{last}» — одно название с номером в конце: одна манга "
                       f"«{work.name}», {plural(len(work.parts), PARTS)}")
    return out


def for_run(settings: dict, root: Path, files: list, libraries: tuple = (), *, log=None) -> RunWatch:
    """The run's files sorted out (:class:`RunWatch` ``kept`` — what the parser parses now): the program's own folders
    of manga, manhwa and videos — never; a manga — held back (copied by the watch, if not yet); a folder waiting for
    SauceNAO — held back; not a manga — parsed; a folder not looked at yet — held back and checked beside the run."""
    log = log or (lambda _text: None)
    watch = RunWatch(settings, root, log)
    lib_keys = {MD._norm(x) for x in libraries if x}
    files = [p for p in files if not MD._under(Path(p).parent, lib_keys)] if lib_keys else list(files)
    cands = [c for c in detect(root, files) if not any(MD._under(Path(r), lib_keys) for r in c.roots())]
    items = STORE.register(str(root), cands) if cands else STORE.items(str(root))
    routes = _route(files, cands)
    now = time.time()
    held_pictures, held_folders = 0, set()
    decided = {"manga": 0, "not": 0, "wait": 0}
    for cand in cands:
        item = items.get(cand.key) or {}
        for work in cand.works:
            rec = state_of(item, work)
            state = rec.get("state")
            if state == NOT:
                decided["not"] += 1
                watch.parsed[work.key] = (cand, work)
                continue
            watch.held[work.key] = []
            if state == MANGA:
                decided["manga"] += 1
                if not rec.get("copied") and copy_due(rec, now):
                    watch.places.append((cand, work))
            elif state == WAIT and float(rec.get("later_at") or 0) > now:
                decided["wait"] += 1                    # SauceNAO's limit, the sites' searches of the hour
            else:
                watch.todo.append((cand, work))
    handed: set = set()                                 # (cand key, work key) «not a manga» whose pages go in now
    for path in files:
        hit = routes.get(path)
        if hit is None:
            watch.kept.append(path)
            continue
        cand, work = hit
        if work.key in watch.held:
            watch.held[work.key].append(path)
            held_pictures += 1
            held_folders.add(cand.key)
        else:
            watch.kept.append(path)
            handed.add((cand.key, work.key))
    fresh = [(c, w) for c, w in handed if not (((items.get(c) or {}).get("works") or {}).get(w) or {}).get("handed")]
    if fresh:                                           # (the block: «пропарсится при следующем запуске» no more)
        def put(found: dict) -> None:
            for cand_key, work_key in fresh:
                rec = ((found.get(cand_key) or {}).get("works") or {}).get(work_key)
                if isinstance(rec, dict):
                    rec["handed"] = int(now)
        STORE.change(put)
    if held_folders or decided["not"]:
        text = (f"{plural(len(held_folders), FOLDERS)} похожи на мангу ({plural(held_pictures, PICTURES)}) — на "
                "booru-сайты не идут, пока не узнано, что это") if held_folders else "новых папок, похожих на мангу, нет"
        before = [f"{word} — {decided[key]}" for key, word in (("manga", "манга"), ("not", "не манга (на booru-сайты)"),
                                                               ("wait", "ждут")) if decided[key]]
        log(line("МАНГА В ПАПКЕ", text + (f"; узнано раньше: {', '.join(before)}" if before else "")))
    return watch


# ── the background ─────────────────────────────────────────────────────────────────────────────────────────────────
_ROUND = threading.Lock()


def copy_due(rec: dict, now: float) -> bool:
    """A confirmed manga's copy is to be made: never tried, or tried and its trouble may be over (no room, a file busy
    — :data:`LATER`); its copy removed by the person — never again."""
    if not rec.get("copy_error"):
        return True
    if "убрали из папки манги" in str(rec.get("copy_error")) and int(rec.get("copy_removed") or 0) < REMOVED_RULE:
        return True                         # (v5.44.127) said by 5.44.125–5.44.126 of an old copy: asked once more
    retry = float(rec.get("copy_retry_at") or 0)
    return bool(retry) and retry <= now


def _due(items: dict, now: float) -> tuple:
    """(to check, to place, «not a manga» for now whose SauceNAO may be asked) — [(cand, work)] in the folder's
    order."""
    check, places, later = [], [], []
    for item in items.values():
        cand = _cand(item)
        if cand is None:
            continue
        for work in cand.works:
            rec = state_of(item, work)
            state = rec.get("state")
            if state == MANGA:
                if not rec.get("copied") and copy_due(rec, now):
                    places.append((cand, work))
            elif state == WAIT:
                if float(rec.get("later_at") or 0) <= now:
                    check.append((cand, work))
            elif state == NOT:
                if rec.get("how") == "later" and float(rec.get("sauce_at") or 0) <= now:
                    later.append((cand, work))
            else:
                check.append((cand, work))
    return check, places, later


def background_round(settings: dict, *, stop_check=None, seconds: float = ROUND_SECONDS, say=None) -> dict:
    """(``comic_manga``'s duty, «Проверить сейчас») The manga of the parser's folder that wait for something: new
    folders and those whose wait is over checked (the ways that cost nothing first for all), confirmed ones copied and
    linked; then the «not a manga» for now whose SauceNAO may be asked again (its step «pictures» alone: a manga —
    copied, its cards of the gallery to the trash; nothing — «not a manga» for good) — for ``seconds`` at most; never
    while the parser runs (its own thread does it).  ``say`` — a line of a journal (also the program's log).
    {"checked", "copied", "manga", "more", "busy", "no_room", "wait"}."""
    from core.parser import jobs as parser_jobs
    out = {"checked": 0, "copied": [], "manga": [], "more": False, "busy": False, "no_room": "", "wait": 0.0}
    said = say or (lambda _text: None)

    def tell(text: str) -> None:
        _LOG.info("parser manga: %s", text)
        said(text)

    settings = dict(settings or {})
    root = str(settings.get("root") or "").strip()
    if not enabled(settings) or not root or not Path(root).is_dir():
        return out
    if parser_jobs.is_running():
        out["busy"] = True
        return out
    if not _ROUND.acquire(blocking=False):
        out["busy"] = True
        return out
    try:
        stop_check = stop_check or (lambda: False)
        deadline = time.monotonic() + max(1.0, float(seconds))
        cands = MD.STORE.candidates(root)
        if cands:
            STORE.register(root, [c for c in cands if not _in_libraries(settings, c)], whole=True)
        items = STORE.items(root)
        check, places, later = _due(items, time.time())
        if not (check or places or later):
            return out
        asker = background_asker(settings, stop_check)
        for cand, work in places:
            if stop_check() or time.monotonic() > deadline:
                out["more"] = True
                return out
            item = STORE.items(root).get(cand.key) or {}
            rec = place(settings, asker, cand, work, state_of(item, work), say=tell, stop_check=stop_check,
                        deadline=deadline)
            if rec.get("copied") and rec.get("fresh_copy"):
                out["copied"].append(rec.get("folder") or work.name)
            if rec.get("no_room"):
                out["no_room"] = rec["no_room"]          # the disk of the manga folder: no more copies this round
                return out
        memo: dict = {}
        for step in STEPS:                              # the ways that cost nothing first for all
            left = []
            for cand, work in check:
                if stop_check() or time.monotonic() > deadline:
                    out["more"] = True
                    return out
                try:
                    verdict = check_work(asker, cand, work, steps=(step,), memo=memo.setdefault(work.key, {}),
                                         later_not=True)
                except Pace as pace:
                    verdict = paced(pace.wait)
                    out["wait"] = round(pace.wait, 1)
                if verdict is None:
                    left.append((cand, work))
                    continue
                if not verdict.state:
                    out["more"] = True
                    return out
                rec = apply(settings, asker, cand, work, verdict, say=tell, stop_check=stop_check, deadline=deadline)
                out["checked"] += 1
                if rec.get("state") == MANGA:
                    out["manga"].append(work.name)
                    if rec.get("copied") and rec.get("fresh_copy"):
                        out["copied"].append(rec.get("folder") or work.name)
                if rec.get("no_room"):
                    out["no_room"] = rec["no_room"]
            check = left
        for cand, work in later:                        # «not a manga» for now: SauceNAO, now that it may be asked
            if stop_check() or time.monotonic() > deadline:
                out["more"] = True
                return out
            got = _sauce_again(settings, asker, cand, work, say=tell, stop_check=stop_check, deadline=deadline)
            if got is None:
                out["more"] = True
                return out
            if got.get("wait"):
                out["wait"] = float(got["wait"])
            if got.get("state") in (MANGA, NOT):
                out["checked"] += 1
            if got.get("state") == MANGA:
                out["manga"].append(work.name)
                if got.get("copied") and got.get("fresh_copy"):
                    out["copied"].append(got.get("folder") or work.name)
            if got.get("no_room"):
                out["no_room"] = got["no_room"]
                return out
        return out
    finally:
        _ROUND.release()


def _sauce_again(settings: dict, asker: Asker, cand: "MD.Candidate", work: "MD.Work", *, say, stop_check,
                 deadline: float) -> "dict | None":
    """A «not a manga» for now asked of SauceNAO again (its step «pictures» alone; the parser's files only read): a
    manga — kept, copied, linked, its cards of the gallery to the trash («SauceNAO подтвердил позже»); nothing (or
    SauceNAO switched off) — «not a manga» for good, its mark gone; SauceNAO not to be asked yet, the sites not
    answering, the hour's searches spent — asked again later (it stays «not a manga» for now: its pages are parsed).
    The record; None — stopped half-way."""
    try:
        verdict = check_work(asker, cand, work, steps=("pictures",), memo={}, later_not=True)
    except Pace as pace:
        STORE.put_work(cand.key, work.key, {"sauce_at": int(time.time() + max(60.0, pace.wait))})
        return {"state": "", "wait": round(pace.wait, 1)}
    if not verdict.state:
        return None
    if verdict.state == MANGA:
        return apply(settings, asker, cand, work, verdict, say=say, stop_check=stop_check, deadline=deadline,
                     extra={"cards_to_trash": True})
    if verdict.state == NOT and verdict.how != "later":
        return apply(settings, asker, cand, work, verdict, say=say, stop_check=stop_check, deadline=deadline)
    again = int(verdict.sauce_at or verdict.later_at or 0) or int(time.time()) + LATER
    STORE.put_work(cand.key, work.key, {"sauce_at": again})
    return {"state": ""}


def _in_libraries(settings: dict, cand: "MD.Candidate") -> bool:
    from core.manhwa_download import roots
    libs = {MD._norm(r) for r in roots(settings).values() if r}
    video = str((settings or {}).get("video_root") or "").strip()
    if video:
        libs.add(MD._norm(video))
    return bool(libs) and any(MD._under(Path(r), libs) for r in cand.roots())


# ── the owner's word, the page ─────────────────────────────────────────────────────────────────────────────────────
def say_not(settings: dict, key: str) -> dict:
    """«Это не манга» of a row: its folders go to the parser (and become a сборка of the gallery); a copy made in the
    manga folder stays (the owner removes it — as «Это не манга» of «Из архива»)."""
    item = STORE.items().get(key)
    cand = _cand(item or {})
    if cand is None:
        return {"ok": False, "error": "Такой папки в списке нет — откройте страницу заново"}
    if cand.is_set or len(cand.works) != 1 or Path(cand.path).is_dir():
        MD.set_not_manga(cand.path)
    else:
        for root in cand.roots():
            MD.set_not_manga(root)

    def put(items: dict) -> None:
        if key in items:
            items[key]["hand"] = NOT
    STORE.change(put)
    return {"ok": True}


def say_manga(settings: dict, key: str) -> dict:
    """«Это манга» of a row: the owner's word confirms it — copied into «Манга» (by the background; its pages are
    not parsed any more).  A folder he refused before is the parser's no more."""
    item = STORE.items().get(key)
    cand = _cand(item or {})
    if cand is None:
        return {"ok": False, "error": "Такой папки в списке нет — откройте страницу заново"}
    refused = MD.not_manga()
    for path in [cand.path, *cand.roots()]:
        if MD._norm(path) in refused:
            MD.set_not_manga(path, False)

    def put(items: dict) -> None:
        if key in items:
            items[key]["hand"] = MANGA
            items[key]["cand"]["refused"] = False
    STORE.change(put)
    return {"ok": True}


def cover_of(key: str, index: int = 0):
    """The first page (by its number) of a row's manga — a picture the page shows (by the row's key, never a path)."""
    import zipfile
    from core.manga_library import PageRef
    item = STORE.items().get(str(key))
    cand = _cand(item or {})
    if cand is None or not cand.works:
        return None
    work = cand.works[min(max(0, int(index)), len(cand.works) - 1)]
    for part in work.parts:
        listed = MD.pages_of(part)
        if not listed:
            continue
        _number, path = listed[0]
        if not part.archive:
            return PageRef(str(path))
        try:
            with zipfile.ZipFile(path) as zf:
                names = sorted((n for n in zf.namelist() if Path(n).suffix.lower() in MD.IMAGES), key=MD._natural)
        except (OSError, zipfile.BadZipFile):
            return None
        return PageRef(str(path), names[0]) if names else None
    return None


def _where(root: str, path: str) -> str:
    """«…\\Разобрать\\silver soul» — the folder's names from the parser's folder (never more of the path)."""
    try:
        parts = [Path(root).name, *Path(path).relative_to(Path(root)).parts]
    except ValueError:
        parts = [Path(path).name]
    return "…" + os.sep + os.sep.join(parts)


def rows(settings: dict) -> list:
    """The rows of the block «Манга в исходной папке» (the page «Парсер»): each candidate — its name, where it is
    (names only), its size, what it is now, how, its копия's page, the parts of its сборка."""
    from core import comic_manga as CM, manga_library as M
    root = str((settings or {}).get("root") or "").strip()
    items = STORE.items(root) if root else {}
    moved = CM.moved()
    manga_root = M.manga_root(settings)
    out = []
    for key, item in items.items():
        cand = _cand(item)
        if cand is None:
            continue
        works = []
        sizes = item.get("sizes") or {}
        for work in cand.works:
            rec = state_of(item, work)
            copy = moved.get(str(rec.get("copy") or "")) if rec.get("copy") else None
            page = ""
            if isinstance(copy, dict) and copy.get("folder") and manga_root and Path(str(copy["folder"])).is_dir():
                page = CM.page_of(settings, copy["folder"])
            works.append({"key": work.key, "name": work.name, "state": rec.get("state") or "",
                          "how": rec.get("how", ""), "by": rec.get("by", ""), "sim": rec.get("sim", 0),
                          "later_at": int(rec.get("later_at") or 0), "size": sizes.get(work.key, ""),
                          "sauce_at": int(rec.get("sauce_at") or 0) if rec.get("how") == "later" else 0,
                          "handed": bool(rec.get("handed")), "trashed": int(rec.get("cards_trashed") or 0),
                          "parts": len(work.parts), "first": Path(work.roots[0]).name if work.roots else "",
                          "page": page, "fill": rec.get("fill") or {},
                          "url": rec.get("url", ""), "folder": rec.get("folder", ""),
                          "error": rec.get("copy_error", ""), "site": rec.get("site") or {},
                          "parts_of": [{"label": p.label, "name": Path(p.path).name} for p in work.parts][:40]})
        states = [w["state"] for w in works]
        if all(s == MANGA for s in states):
            state = MANGA
        elif any(s in ("", "check") for s in states):
            state = "check"
        elif any(s == WAIT for s in states):
            state = WAIT
        elif any(s == MANGA for s in states):
            state = "part"
        else:
            state = NOT
        folders = sum(len(w.parts) for w in cand.works) if cand.is_set else 0
        pages = int(item.get("pages") or 0)
        out.append({"key": key, "name": cand.name, "where": _where(root, cand.path), "set": cand.is_set,
                    "state": state, "hand": item.get("hand", ""), "works": works, "pages": pages,
                    "size": (f"{plural(folders, FOLDERS)} · {pages} стр." if cand.is_set else
                             works[0]["size"] if works else ""), "at": int(item.get("at") or 0)})
    order = {"check": 0, MANGA: 1, "part": 1, WAIT: 2, NOT: 3}
    return sorted(out, key=lambda r: (order.get(r["state"], 4), MD._natural(r["name"])))


def counts(settings: dict) -> dict:
    """{"manga", "not", "wait", "check", "later"} of the manga of the parser's folder (what was found alone: no folder
    read); «later» — of «not», the ones whose SauceNAO is asked later."""
    root = str((settings or {}).get("root") or "").strip()
    out = {"manga": 0, "not": 0, "wait": 0, "check": 0, "later": 0}
    for item in (STORE.items(root) if root else {}).values():
        cand = _cand(item)
        if cand is None:
            continue
        for work in cand.works:
            rec = state_of(item, work)
            state = rec.get("state")
            out[state if state in (MANGA, NOT, WAIT) else "check"] += 1
            if state == NOT and rec.get("how") == "later":
                out["later"] += 1
    return out


def summary(settings: dict) -> dict:
    """{"total", "manga", "not", "wait", "check", "later", "waits", "left_today", "per_day"} of the block's first line:
    «later» — rows «not a manga» whose SauceNAO is asked later, «waits» — why the waiting ones wait (their «how»)."""
    from core import manga_pictures as P
    listed = rows(settings)
    count = {"manga": 0, "not": 0, "wait": 0, "check": 0}
    later, waits = 0, set()
    for row in listed:
        state = row["state"]
        count["manga" if state in (MANGA, "part") else state if state in count else "check"] += 1
        if state == NOT and any(w["how"] == "later" for w in row["works"]):
            later += 1
        if state == WAIT:
            waits |= {w["how"] for w in row["works"] if w["state"] == WAIT}
    budget = P.per_day(settings)
    return {"total": len(listed), **count, "later": later, "waits": sorted(waits), "per_day": budget,
            "left_today": max(0, budget - P.asked_today()), "rows": listed}
