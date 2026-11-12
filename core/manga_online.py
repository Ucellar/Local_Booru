"""(v5.44.82) Reading a title of the sites of manga and manhwa without downloading it: «Скачать → Манга / Манхва» (and
«Скачать по ссылке» on the phone) — «Читать» opens a chapter in the program's own reader (the strip of «Манхва»),
the site is not opened; nothing is written to the library.

The owner (5.11.2026): «еще что бы можно было смотреть не скачивая и можно было прямо в приложении нахзодить и качать
мангу видео и хентай и все что у меня сейчас работает через скачивание»; where — «1 но какбы не открывая сайт а
просто прямо в приложении» (the program and the phone).

* The title (:meth:`Online.title`): the card's own search when it is this title (``webui/pages/manhwa_get.py`` — the
  task «Найти» keeps what it found), else ``manhwa_download.look`` once — kept :data:`TITLE_TTL`.  Its chapters are the
  card's (``Found.takes(True)``: the home site's free ones, the paid ones from another site that has them free, the
  ones only another site has) — chapter ``n`` (from 1) is the card's tile ``n``.  A paid chapter no site has free is
  never asked (the owner's rule): its page says so.
* A chapter's pictures (:meth:`Online.chapter`): the site's ``pictures`` (``take.source``, ``take.ref``) — their
  addresses kept :data:`CHAPTER_TTL` (MangaDex's live about a quarter of an hour), its other site's copy (``take.alt``)
  when the first does not give them.
* A picture (:meth:`Online.picture`): fetched by the program with the site's own Referer (the sites refuse others),
  its other servers too (``manhwa_sources.fetch_picture``) — only the pictures of a chapter of a title the program
  found on a site it knows: the address the page asks names the title, the chapter and the page, never a picture's
  own address (no way to make the program fetch anything else).  Kept in memory (:data:`KEPT_BYTES`) and, as the
  friend's reader Yomi 1.7 keeps its pages (``PageCache``), on the disk (:data:`DISK_BYTES` in the system's temporary
  folder, the oldest go): a chapter read again opens without the site.
* What was read (:class:`Progress`): the last chapter of each title (``manga_online.json`` in the settings folder) —
  the card's «Продолжить: Глава 12».
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from core import manhwa_sources as S

_LOG = logging.getLogger("local_booru.manhwa")

TITLE_TTL = 20 * 60                    # a title looked at, kept this long (seconds)
CHAPTER_TTL = 10 * 60                  # the addresses of a chapter's pictures, kept this long
KEPT_BYTES = 80 * 1024 * 1024          # pictures kept in memory at most
DISK_BYTES = 400 * 1024 * 1024         # (as Yomi 1.7's PageCache) …and on the disk
DISK_DIR = "local_booru_online"        # in the system's temporary folder
DISK_ROOT = ""                         # the folder it is in ("" — the system's temporary one; the checks give theirs)
TITLES_KEPT = 24
CHAPTERS_KEPT = 64
PROGRESS_FILE = "manga_online.json"
PROGRESS_KEPT = 300                    # titles whose last chapter is kept
READ_KEPT = 3000                       # (v5.44.119) the chapters read to their end kept of a title
# (v5.44.82) how a chapter is read — as the friend's reader Yomi does it: a strip (manhwa, manhua: the pages one under
# another), the pages one at a time right to left (manga) or left to right (western comics); «auto» — by the title's
# kind, and a strip whenever its pages are tall (their middle height more than :data:`TALL` widths)
MODES = ("strip", "rtl", "ltr")
TALL = 2.2
STRIP_TYPES = {"манхва", "маньхуа", "вебтун", "webtoon", "manhwa", "manhua"}
LTR_TYPES = {"комикс", "западный комикс", "комикс западный", "рукомикс", "руманга", "oel-манга"}


class OnlineError(RuntimeError):
    """Why a chapter cannot be read, in words for the page."""


@dataclass
class Reading:
    """A title being read: its link, the card's chapters (``takes``), when it was looked at."""
    url: str
    branch: str
    found: object                      # manhwa_download.Found
    takes: list
    at: float = field(default_factory=time.time)

    @property
    def title(self):
        return self.found.title

    def label(self, n: int) -> str:
        take = self.takes[n - 1]
        return take.label

    def full(self, n: int) -> str:
        take = self.takes[n - 1]
        return take.label + (f" — {take.name}" if take.name and take.ch.number else "")

    def readable(self, n: int) -> bool:
        return 1 <= n <= len(self.takes) and self.takes[n - 1].source is not None

    def neighbour(self, n: int, step: int) -> int:
        """The next readable chapter before (``step`` -1) or after (+1) ``n``; 0 — none."""
        k = n + step
        while 1 <= k <= len(self.takes):
            if self.takes[k - 1].source is not None:
                return k
            k += step
        return 0


@dataclass
class Pages:
    """A chapter's pictures: the site that gives them, its Referer, the pictures (``S.Picture``)."""
    source: object
    referer: str
    pictures: list
    at: float = field(default_factory=time.time)


def key_of(url: str, branch: str = "") -> str:
    """One title's key: its site and its key there (two links of one title are one), and the branch asked."""
    source, key = S.source_for(url)
    if source is None:
        raise OnlineError("Такой ссылки программа не знает — это не тайтл сайта манги или манхвы")
    return f"{source.name}:{key}:{branch}"


class Online:
    def __init__(self):
        self._lock = threading.Lock()
        self._titles: "OrderedDict[str, Reading]" = OrderedDict()
        self._chapters: "OrderedDict[tuple, Pages]" = OrderedDict()
        self._pictures: "OrderedDict[tuple, tuple]" = OrderedDict()
        self._bytes = 0
        self._looking: dict = {}       # key → a lock: one look of a title at a time
        self._net: "S.Net | None" = None

    def net(self) -> "S.Net":
        with self._lock:
            if self._net is None:
                self._net = S.Net()
            return self._net

    def forget(self) -> None:
        """Everything kept let go (the checks; a site's address changed)."""
        with self._lock:
            self._titles.clear()
            self._chapters.clear()
            self._pictures.clear()
            self._bytes = 0

    def forget_title(self, found) -> None:
        """(v5.44.146) ``found`` (``manhwa_download.Found``) has more sites now — a download asked the ones that had
        not answered when it was found: what is kept of it here is let go.  A chapter's address is its place in the
        list (``takes``), and with a site more that place may be another chapter's."""
        with self._lock:
            keys = {key for key, reading in self._titles.items() if reading.found is found}
            for key in keys:
                del self._titles[key]
            for ckey in [c for c in self._chapters if c[0] in keys]:
                del self._chapters[ckey]
            for pkey in [x for x in self._pictures if x[0] in keys]:
                self._bytes -= len(self._pictures.pop(pkey)[0])

    # ── the title ───────────────────────────────────────────────────────────────────────────────────────────────
    def _card(self, key: str):
        """The card's own search, when it found this title (``manhwa_get``'s task «Найти»: the same title — by its
        link or by the link the owner gave, which may be another site's — and, when one is asked, its branch);
        (v5.44.117) each page has a search of its own: the newest one that found this title."""
        try:
            from webui.pages.manhwa_get import found_titles
        except ImportError:
            return None
        site_key, branch = key.rsplit(":", 1)
        for found, asked in found_titles():
            mine = []
            for link in (found.title.url, asked):
                try:
                    mine.append(key_of(link).rsplit(":", 1)[0])
                except OnlineError:
                    continue
            if site_key not in mine:
                continue
            if branch and (found.branch.id if found.branch else "") != branch:
                continue
            return found
        return None

    def kept(self, url: str, branch: str = "") -> "Reading | None":
        """(v5.44.117) The title as it is kept now, None — not kept (nothing is asked)."""
        try:
            key = key_of(url, branch)
        except OnlineError:
            return None
        with self._lock:
            kept = self._titles.get(key)
        return kept if kept is not None and time.time() - kept.at < TITLE_TTL else None

    def title(self, url: str, settings: dict, branch: str = "", net: "S.Net | None" = None) -> Reading:
        """The title of ``url`` (kept :data:`TITLE_TTL`); ``net`` — (v5.44.117) how it is asked when it is not kept
        (the reader of a folder asks with :class:`QuietNet`: never the program window's browser)."""
        key = key_of(url, branch)
        with self._lock:
            kept = self._titles.get(key)
            if kept is not None and time.time() - kept.at < TITLE_TTL:
                self._titles.move_to_end(key)
                return kept
            gate = self._looking.setdefault(key, threading.Lock())
        with gate:                                    # one look of a title at a time; the others take its answer
            with self._lock:
                kept = self._titles.get(key)
                if kept is not None and time.time() - kept.at < TITLE_TTL:
                    return kept
            found = self._card(key)
            if found is None:
                from core import manhwa_download as D
                try:
                    found = D.look(url, settings, branch=branch, net=net or self.net(), cover=False, about=False)
                except D.DownloadError as exc:
                    raise OnlineError(str(exc)) from exc
            reading = Reading(url=url, branch=branch, found=found, takes=found.takes(True))
            with self._lock:
                self._titles[key] = reading
                self._titles.move_to_end(key)
                while len(self._titles) > TITLES_KEPT:
                    self._titles.popitem(last=False)
            return reading

    # ── a chapter ───────────────────────────────────────────────────────────────────────────────────────────────
    def chapter(self, url: str, n: int, settings: dict, branch: str = "") -> tuple:
        """(the title, its chapter's pictures — :class:`Pages`) of chapter ``n`` (from 1) of the card's list."""
        reading = self.title(url, settings, branch)
        if not 1 <= n <= len(reading.takes):
            raise OnlineError("Такой главы у тайтла нет — список глав мог обновиться, откройте тайтл заново")
        take = reading.takes[n - 1]
        if take.source is None:
            when = take.ch.free_at
            raise OnlineError("Эта глава платная, и бесплатно её нет ни на одном сайте — программа её не покупает"
                              + (f"; бесплатной станет {_date(when)}" if when else ""))
        ckey = (key_of(url, branch), n)
        with self._lock:
            kept = self._chapters.get(ckey)
            if kept is not None and time.time() - kept.at < CHAPTER_TTL:
                self._chapters.move_to_end(ckey)
                return reading, kept
        net = self.net()
        why = ""
        from core.manhwa_run import options_of
        # (v5.44.146) every site that has the chapter (each take's ``alt``), not two; (v5.44.136) one that gives the
        # program no pictures lately — after the others
        candidates = sorted((c for c in options_of(take) if c.source is not None),
                            key=lambda c: bool(S.withheld(c.source.label)))
        for candidate in candidates:
            try:
                pictures = candidate.source.pictures(net, candidate.title, candidate.ref)
            except S.SourceError as exc:
                why = str(exc) if str(exc).startswith(candidate.source.label) else f"{candidate.source.label}: {exc}"
                _LOG.info("online: %s of %s not given by %s: %s", take.label, reading.title.title,
                          candidate.source.label, exc)
                continue
            if pictures:
                pages = Pages(candidate.source, candidate.source.referer(candidate.title), list(pictures))
                with self._lock:
                    self._chapters[ckey] = pages
                    self._chapters.move_to_end(ckey)
                    while len(self._chapters) > CHAPTERS_KEPT:
                        self._chapters.popitem(last=False)
                return reading, pages
            why = why or f"{candidate.source.label}: у главы нет страниц"
        raise OnlineError("Глава не открылась — " + (why or "сайты её не отдали"))

    def pages_known(self, url: str, n: int, branch: str = "") -> int:
        """(v5.44.119) The pages of chapter ``n`` as kept now (0 — not kept: nothing is asked) — the place read keeps
        «стр. 12 из 38» with it."""
        try:
            ckey = (key_of(url, branch), n)
        except OnlineError:
            return 0
        with self._lock:
            kept = self._chapters.get(ckey)
        return len(kept.pictures) if kept is not None else 0

    # ── a picture ───────────────────────────────────────────────────────────────────────────────────────────────
    def picture(self, url: str, n: int, p: int, settings: dict, branch: str = "") -> tuple:
        """(bytes, type) of page ``p`` (from 1) of chapter ``n`` — fetched once, kept a while."""
        pkey = (key_of(url, branch), n, p)
        with self._lock:
            kept = self._pictures.get(pkey)
            if kept is not None:
                self._pictures.move_to_end(pkey)
                return kept
        reading, pages = self.chapter(url, n, settings, branch)
        if not 1 <= p <= len(pages.pictures):
            raise OnlineError("Такой страницы у главы нет")
        picture = pages.pictures[p - 1]
        on_disk = disk_read(picture.url)
        if on_disk is not None:
            self._keep(pkey, on_disk)
            return on_disk
        try:
            data, ctype = S.fetch_picture(self.net(), picture, what=pages.source.label,
                                          headers={"Referer": pages.referer,
                                                   "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
        except S.SourceError as exc:
            with self._lock:                         # the addresses may have gone stale: asked anew next time
                self._chapters.pop((pkey[0], n), None)
            if isinstance(exc, S.Withheld):          # (v5.44.136) its site gives the program no pictures
                from core.manhwa_run import options_of
                take = reading.takes[n - 1] if 1 <= n <= len(reading.takes) else None
                other = next((c for c in options_of(take) if c.source is not None and c.source is not pages.source),
                             None)
                raise OnlineError(str(exc) + (f". Откройте главу заново — она откроется с {other.source.label}"
                                              if other is not None else "")) from exc
            raise OnlineError(f"{pages.source.label}: страница не пришла — {exc}") from exc
        from core.manhwa_download import _picture_type
        ext = _picture_type(data, ctype)
        if not ext:
            raise OnlineError(f"{pages.source.label} прислал не картинку")
        kind = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
                ".avif": "image/avif"}.get(ext, "application/octet-stream")
        disk_write(picture.url, data, kind)
        self._keep(pkey, (data, kind))
        return data, kind

    def _keep(self, pkey: tuple, value: tuple) -> None:
        with self._lock:
            if pkey in self._pictures:
                return
            self._pictures[pkey] = value
            self._bytes += len(value[0])
            while self._bytes > KEPT_BYTES and self._pictures:
                _k, (old, _t) = self._pictures.popitem(last=False)
                self._bytes -= len(old)


# ── (v5.44.82) the pages on the disk, as Yomi 1.7's PageCache ─────────────────────────────────────────────────────
_DISK_LOCK = threading.Lock()
_DISK = {"written": 0}
KINDS = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif", "avif": "image/avif"}


def disk_dir() -> Path:
    return Path(DISK_ROOT or tempfile.gettempdir()) / DISK_DIR


def _disk_name(link: str) -> str:
    return hashlib.md5(link.encode("utf-8")).hexdigest()


def disk_read(link: str) -> "tuple | None":
    """(bytes, type) of a page kept on the disk; None — not there."""
    folder = disk_dir()
    name = _disk_name(link)
    for ext, kind in KINDS.items():
        path = folder / f"{name}.{ext}"
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if data:
            try:
                os.utime(path)                        # read lately: the last to go
            except OSError:
                pass
            return data, kind
    return None


def disk_write(link: str, data: bytes, kind: str) -> None:
    """A page kept on the disk (a whole file or none: written aside, then put in its place); past :data:`DISK_BYTES`
    the oldest go (every 40 pages written it is counted)."""
    ext = next((e for e, k in KINDS.items() if k == kind), "")
    if not ext or not data:
        return
    folder = disk_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{_disk_name(link)}.{ext}"
        part = path.with_suffix(path.suffix + ".part")
        part.write_bytes(data)
        os.replace(part, path)
    except OSError as exc:
        _LOG.info("online: a page not kept on the disk: %s", type(exc).__name__)
        return
    with _DISK_LOCK:
        _DISK["written"] += 1
        if _DISK["written"] % 40:
            return
    disk_trim()


def disk_trim(limit: int = DISK_BYTES) -> int:
    """The oldest pages on the disk let go until they take :data:`DISK_BYTES` × 0.8; how many went."""
    folder = disk_dir()
    try:
        files = [(f.stat().st_mtime, f.stat().st_size, f) for f in folder.iterdir() if f.is_file()]
    except OSError:
        return 0
    total = sum(size for _m, size, _f in files)
    if total <= limit:
        return 0
    gone = 0
    for _mtime, size, path in sorted(files):
        if total <= limit * 0.8:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        gone += 1
    return gone


ONLINE = Online()


class QuietNet(S.Net):
    """(v5.44.117) The sites asked without the program window's browser — the reader of a folder asks a downloaded
    title's site for the chapters it has (``webui/pages/reader.site``): a site that wants its check for robots is said
    to be closed, no window opens by itself (as ``manhwa_search``'s search by a name)."""

    def _past_check(self, url, closed, options):
        raise closed

    def _through_browser(self, url, what):
        raise S.Closed(S.closed_words(what))


def auto_of(kind: str, manga: bool, sizes: list) -> str:
    """(v5.44.83) The way a chapter is read when the owner did not choose one — for the titles of the sites and of the
    folders alike: a strip for tall pages (``sizes`` — [(width, height)]) and for manhwa and manhua; (v5.44.117; the
    approved mockup of one reader: «манхва — прокруткой, манга — по страницам») the pages left to right for manga and
    comics (``manga`` — a title of the manga's kind) — as the reader of «Манга» has always turned them; right to left
    is the owner's choice, kept for the title."""
    ratios = sorted(h / w for w, h in sizes if w > 0 and h > 0)
    if len(ratios) >= 3 and ratios[len(ratios) // 2] > TALL:
        return "strip"
    kind = str(kind or "").strip().casefold()
    if kind in STRIP_TYPES:
        return "strip"
    if kind in LTR_TYPES or manga or kind in ("манга", "manga"):
        return "ltr"
    return "strip"


def auto_mode(reading: Reading, pictures: list) -> str:
    """The way a chapter of a site's title is read when the owner did not choose one (:func:`auto_of`)."""
    return auto_of(str(reading.title.type or ""), reading.found.kind == "manga",
                   [(p.width, p.height) for p in pictures])


def _date(text: str) -> str:
    from core.manhwa_download import _date as date
    return date(text)


# ── what was read ──────────────────────────────────────────────────────────────────────────────────────────────────
class Progress:
    """``manga_online.json`` in the settings folder: {title key: {url, title, ch, label, page, part, done, at}};
    (v5.44.119) and ``id`` — the chapter's own id (the place found again when the list of chapters changes: other
    translators, another site's chapters), ``pages`` — its pages when known, ``read`` — the ids of the chapters read to
    their end (the title's page marks them «прочитана», as the folder's chapters)."""

    def __init__(self, path: "Path | None" = None):
        self._path = path
        self._lock = threading.Lock()

    def file(self) -> Path:
        if self._path is not None:
            return self._path
        from core.paths import SETTINGS_DIR
        return Path(SETTINGS_DIR) / PROGRESS_FILE

    def read(self) -> dict:
        from core.file_safety import read_json_kept
        data = read_json_kept(self.file(), dict, what="manga online")
        return data if isinstance(data, dict) else {}

    def put(self, url: str, reading: Reading, n: int, page: int = 0, part: float = 0.0, done: bool = False,
            pages: int = 0) -> None:
        from core.file_safety import write_json_kept
        key = key_of(url).rsplit(":", 1)[0]
        take = reading.takes[n - 1] if 1 <= n <= len(reading.takes) else None
        own = str(getattr(take, "id", "") or "")
        with self._lock:
            data = self.read()
            old = data.get(key) if isinstance(data.get(key), dict) else {}
            read = [str(x) for x in old.get("read") or [] if isinstance(x, (str, int))]
            if done and own and own not in read:
                read = (read + [own])[-READ_KEPT:]
            if not pages and old.get("ch") == n and old.get("id", own) == own:
                pages = int(old.get("pages") or 0)          # the same chapter: its pages as known before
            data[key] = {"url": reading.title.url, "title": reading.title.title, "ch": n, "label": reading.label(n),
                         "page": max(0, int(page)), "part": max(0.0, min(1.0, float(part))), "done": bool(done),
                         "at": int(time.time()), "id": own, **({"pages": int(pages)} if pages else {}),
                         **({"read": read} if read else {}),
                         **({"mode": old["mode"]} if old.get("mode") in MODES else {})}
            if len(data) > PROGRESS_KEPT:
                for gone in sorted(data, key=lambda k: data[k].get("at", 0) if isinstance(data[k], dict) else 0)[
                        :len(data) - PROGRESS_KEPT]:
                    data.pop(gone, None)
            write_json_kept(self.file(), json.dumps(data, ensure_ascii=False, indent=1), dict)

    def put_mode(self, url: str, mode: str) -> None:
        """(v5.44.82) The way this title is read, as the owner chose it ("" — «авто»)."""
        from core.file_safety import write_json_kept
        key = key_of(url).rsplit(":", 1)[0]
        with self._lock:
            data = self.read()
            record = data.get(key) if isinstance(data.get(key), dict) else {"at": int(time.time())}
            if mode in MODES:
                record["mode"] = mode
            else:
                record.pop("mode", None)
            data[key] = record
            write_json_kept(self.file(), json.dumps(data, ensure_ascii=False, indent=1), dict)

    def of(self, url: str) -> dict:
        """The last chapter read of the title of ``url`` ({} — none)."""
        try:
            key = key_of(url).rsplit(":", 1)[0]
        except OnlineError:
            return {}
        value = self.read().get(key)
        return value if isinstance(value, dict) else {}

    # (v5.44.148) what is read without downloading and is not a title of a manga site — a gallery of nhentai
    # (``core/gallery_read.py``): the same file, a key of its own («nhentai:<number>»)
    def record(self, key: str) -> dict:
        """The record kept under ``key`` ({} — none)."""
        value = self.read().get(str(key))
        return value if isinstance(value, dict) else {}

    def put_record(self, key: str, fields: dict) -> None:
        """``fields`` over the record kept under ``key`` (its ``mode`` stays), ``at`` — now; the records read longest
        ago go past :data:`PROGRESS_KEPT`, as the titles' do."""
        from core.file_safety import write_json_kept
        with self._lock:
            data = self.read()
            old = data.get(str(key)) if isinstance(data.get(str(key)), dict) else {}
            data[str(key)] = {**fields, "at": int(time.time()),
                              **({"mode": old["mode"]} if old.get("mode") in MODES else {})}
            if len(data) > PROGRESS_KEPT:
                for gone in sorted(data, key=lambda k: data[k].get("at", 0) if isinstance(data[k], dict) else 0)[
                        :len(data) - PROGRESS_KEPT]:
                    data.pop(gone, None)
            write_json_kept(self.file(), json.dumps(data, ensure_ascii=False, indent=1), dict)

    def put_record_mode(self, key: str, mode: str) -> None:
        """The way the thing under ``key`` is read, as the owner chose it ("" — «авто»)."""
        from core.file_safety import write_json_kept
        with self._lock:
            data = self.read()
            record = data.get(str(key)) if isinstance(data.get(str(key)), dict) else {"at": int(time.time())}
            if mode in MODES:
                record["mode"] = mode
            else:
                record.pop("mode", None)
            data[str(key)] = record
            write_json_kept(self.file(), json.dumps(data, ensure_ascii=False, indent=1), dict)


PROGRESS = Progress()
